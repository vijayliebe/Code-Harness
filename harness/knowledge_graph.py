import json
import os
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set

import networkx as nx
from rich.progress import track

from .models import CodeEntity, EntityType, Relationship, RelationshipType
from .config import Config
from .utils import extract_imports
from .kg_enrich import (
    collect_enrichment_entities,
    extract_enrichment_relationships,
    extract_exposes as _extract_exposes_fn,
    extract_gloss as _extract_gloss_fn,
    extract_tested_by as _extract_tested_by_fn,
    load_gloss_entities,
    mermaid_from_graph,
)

EDGE_PRIOR = {
    "gloss": 0.9,
    "exposes": 0.85,
    "calls": 0.8,
    "tested_by": 0.8,
    "inherits": 0.65,
    "contains": 0.4,
    "references": 0.25,
    "imports": 0.5,
    "shared_import": 0.3,
    "shared_entity": 0.3,
    "depends_on": 0.35,
}


class KnowledgeGraph:
    def __init__(self, config: Config, repo_name: str = ""):
        self.config = config
        self.repo_name = repo_name
        self.enabled = config.knowledge_graph.get("enabled", True)
        self.graph = nx.DiGraph()
        self.extra_entities: List[CodeEntity] = []

        # Compute per-repo persist path
        base_path = config.knowledge_graph.get("persist_path", ".code-harness/graph.json")
        if repo_name:
            directory = os.path.dirname(base_path)
            self.persist_path = os.path.join(directory, f"graph_{repo_name}.json")
        else:
            self.persist_path = base_path

    def build(self, entities: List[CodeEntity]) -> List[Relationship]:
        if not self.enabled:
            return []

        working = list(entities)
        try:
            extras = collect_enrichment_entities(
                working,
                getattr(self.config, "repo_path", "") or "",
                self.config,
            )
            working = [
                e for e in working
                if not (
                    e.type == EntityType.DOCUMENTATION
                    and "/gloss/" in f"/{(e.file_path or '').replace(chr(92), '/')}"
                    and (e.metadata or {}).get("kind") != "gloss"
                )
            ]
            seen = {e.id for e in working}
            for extra in extras:
                if extra.id not in seen:
                    working.append(extra)
                    seen.add(extra.id)
                    self.extra_entities.append(extra)
        except Exception:
            pass

        relationships = []
        rels: List[tuple] = []

        # Build indexes once — O(n), then lookups are O(1)
        name_index: Dict[str, str] = {}
        file_index: Dict[str, List[CodeEntity]] = defaultdict(list)
        method_by_class: Dict[str, Dict[str, CodeEntity]] = defaultdict(dict)

        for entity in track(working, description="  Indexing entities"):
            node_attrs = {
                "name": entity.name,
                "type": entity.type.value if hasattr(entity.type, "value") else str(entity.type),
                "file_path": entity.file_path,
                "start_line": entity.start_line,
                "end_line": entity.end_line,
                "repo_name": self.repo_name,
            }
            kind = (entity.metadata or {}).get("kind")
            if not kind and entity.type == EntityType.DOCUMENTATION:
                path = (entity.file_path or "").replace("\\", "/")
                if "/gloss/" in f"/{path}":
                    kind = "gloss"
                    entity.metadata["kind"] = "gloss"
                elif path.startswith("knowledge/wiki/") or "/knowledge/wiki/" in f"/{path}":
                    kind = "wiki"
                    entity.metadata["kind"] = "wiki"
            if kind:
                node_attrs["kind"] = kind
            self.graph.add_node(entity.id, **node_attrs)
            name_index[entity.name] = entity.id
            file_index[entity.file_path].append(entity)
            if entity.type == EntityType.METHOD:
                dot = entity.name.rfind(".")
                if dot > 0:
                    cls = entity.name[:dot]
                    method_by_class[cls][entity.name] = entity

        for entity in track(working, description="  Building edges"):
            if entity.type == EntityType.FILE:
                siblings = file_index.get(entity.file_path, [])
                for other in siblings:
                    if other.id != entity.id:
                        rels.append((entity.id, other.id, "contains", {}))

            elif entity.type == EntityType.CLASS:
                cls_name = entity.name
                for base_name in entity.metadata.get("bases", []):
                    target = name_index.get(base_name)
                    if target:
                        rels.append((entity.id, target, "inherits", {}))
                for method_name in entity.metadata.get("methods", []):
                    methods = method_by_class.get(cls_name, {})
                    target = methods.get(f"{cls_name}.{method_name}")
                    if not target:
                        target_id = name_index.get(method_name)
                        target = next((e for e in working if e.id == target_id), None) if target_id else None
                    if target:
                        rels.append((entity.id, target.id if hasattr(target, "id") else target, "contains", {}))

            elif entity.type in (EntityType.FUNCTION, EntityType.METHOD):
                for imp in self._detect_imports(entity):
                    target = name_index.get(imp)
                    if target:
                        rels.append((entity.id, target, "calls", {}))
                for ref in self._detect_references(entity):
                    target = name_index.get(ref)
                    if target:
                        rels.append((entity.id, target, "references", {}))

        for src, tgt, rel_type, meta in self._safe_enrichment_rels(working):
            rels.append((src, tgt, rel_type, meta))

        # Batch-add edges to NetworkX — single pass instead of per-edge overhead
        for item in track(rels, description="  Adding edges"):
            if len(item) == 4:
                src, tgt, rel_type, meta = item
            else:
                src, tgt, rel_type = item
                meta = {}
            if src not in self.graph or tgt not in self.graph:
                continue
            edge_attrs = {"relationship": rel_type}
            if meta:
                edge_attrs.update({k: v for k, v in meta.items() if k != "relationship"})
            self.graph.add_edge(src, tgt, **edge_attrs)
            try:
                relationships.append(Relationship(
                    source_id=src, target_id=tgt,
                    relationship_type=RelationshipType(rel_type),
                    metadata=dict(meta or {}),
                ))
            except ValueError:
                continue

        return relationships

    def _safe_enrichment_rels(self, entities: List[CodeEntity]) -> List[tuple]:
        if not self.config.knowledge_graph.get("enrich", True):
            return []
        rels: List[tuple] = []
        extractors = (
            ("extract_exposes", self._extract_exposes),
            ("extract_tested_by", self._extract_tested_by),
            ("extract_gloss", self._extract_gloss),
        )
        for flag, fn in extractors:
            if not self.config.knowledge_graph.get(flag, True):
                continue
            try:
                rels.extend(fn(entities) or [])
            except Exception:
                continue
        return rels

    def _extract_exposes(self, entities: List[CodeEntity]) -> List[tuple]:
        extra, rels = _extract_exposes_fn(entities)
        existing = set(self.graph.nodes()) | {e.id for e in entities}
        for entity in extra:
            if entity.id in existing:
                continue
            self.graph.add_node(entity.id, **{
                "name": entity.name,
                "type": entity.type.value,
                "file_path": entity.file_path,
                "start_line": entity.start_line,
                "end_line": entity.end_line,
                "repo_name": self.repo_name,
                "kind": (entity.metadata or {}).get("kind", "endpoint"),
            })
            self.extra_entities.append(entity)
            existing.add(entity.id)
        return rels

    def _extract_tested_by(self, entities: List[CodeEntity]) -> List[tuple]:
        return _extract_tested_by_fn(entities)

    def _extract_gloss(self, entities: List[CodeEntity]) -> List[tuple]:
        known = set(self.graph.nodes()) | {e.id for e in entities}
        try:
            gloss = load_gloss_entities(
                getattr(self.config, "repo_path", "") or "",
                self.config.knowledge_graph.get("gloss_dirs"),
            )
        except Exception:
            gloss = []
        have = {e.id for e in entities}
        merged = list(entities)
        for item in gloss:
            if item.id not in have:
                merged.append(item)
                have.add(item.id)
            if item.id not in self.graph:
                self.graph.add_node(item.id, **{
                    "name": item.name,
                    "type": item.type.value,
                    "file_path": item.file_path,
                    "start_line": item.start_line,
                    "end_line": item.end_line,
                    "repo_name": self.repo_name,
                    "kind": "gloss",
                })
                self.extra_entities.append(item)
                known.add(item.id)
        return _extract_gloss_fn(merged, known)

    def _detect_imports(self, entity: CodeEntity) -> List[str]:
        return list(extract_imports(entity.source_code))

    def _detect_references(self, entity: CodeEntity) -> List[str]:
        source = entity.source_code
        words = re.findall(r'([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)', source, re.IGNORECASE)
        return list(set(
            w for w in words
            if w not in ("if", "for", "while", "return", "import", "from", "def",
                         "class", "self", "None", "True", "False", "and", "or", "not",
                         "in", "is", "as", "with", "try", "except", "finally", "raise",
                         "yield", "lambda", "pass", "break", "continue", "elif", "else",
                         "assert", "del", "global", "nonlocal")
        ))

    def get_neighbors(self, entity_id: str, max_depth: int = 2) -> Set[str]:
        if not self.enabled:
            return set()

        neighbors = set()
        for depth in range(1, max_depth + 1):
            for node in nx.single_source_shortest_path_length(
                self.graph, entity_id, cutoff=depth
            ):
                if node != entity_id:
                    neighbors.add(node)
        return neighbors

    def get_related_chunks(self, entity_ids: List[str], max_depth: int = 2) -> Set[str]:
        if not self.enabled:
            return set()

        related = set()
        for eid in entity_ids:
            try:
                related.update(self.get_neighbors(eid, max_depth))
            except (nx.NetworkXError, nx.NodeNotFound):
                continue
        return related

    def expand_beam(
        self,
        seed_scores: Dict[str, float],
        width: int = 6,
        depth: int = 2,
        max_added: int = 6,
    ) -> Set[str]:
        """Score-ordered neighbor expansion (not naive BFS)."""
        if not self.enabled or not seed_scores:
            return set()

        width = max(1, int(width))
        depth = max(1, int(depth))
        max_added = max(1, int(max_added))

        beam = []
        for nid, score in seed_scores.items():
            if nid in self.graph:
                beam.append((float(score), nid))
        if not beam:
            return set()

        added: Set[str] = set()
        visited = {nid for _, nid in beam}
        seed_is_file = any(
            self.graph.nodes[nid].get("type") == "file"
            for _, nid in beam
        )
        seed_is_test = any(
            "/test" in (self.graph.nodes[nid].get("file_path") or "").replace("\\", "/")
            or (self.graph.nodes[nid].get("file_path") or "").startswith("tests/")
            for _, nid in beam
        )

        for hop in range(depth):
            candidates = []
            for seed_score, nid in beam:
                for nbr, rel, incoming in self._incident(nid):
                    if nbr in visited:
                        continue
                    nbr_type = self.graph.nodes[nbr].get("type")
                    if nbr_type == "file" and not seed_is_file:
                        continue
                    prior = EDGE_PRIOR.get(rel, 0.2)
                    if rel == "tested_by" and incoming and not seed_is_test:
                        prior = min(prior, 0.35)
                    if rel == "contains" and not incoming:
                        prior = max(prior, 0.55)
                    recency = 0.85 ** hop
                    candidates.append((prior * float(seed_score) * recency, nbr, rel))
            candidates.sort(key=lambda item: item[0], reverse=True)
            next_beam = []
            per_rel = defaultdict(int)
            rel_cap = max(2, width // 3)
            # At most rel_cap per relationship so tests don't drown contains/calls.
            for score, nbr, rel in candidates:
                if nbr in visited or per_rel[rel] >= rel_cap:
                    continue
                if len(added) >= max_added or len(next_beam) >= width:
                    break
                visited.add(nbr)
                added.add(nbr)
                per_rel[rel] += 1
                next_beam.append((score, nbr))
            beam = next_beam
            if not beam or len(added) >= max_added:
                break
        return added

    def _incident(self, node_id: str) -> List[tuple]:
        edges = []
        if node_id not in self.graph:
            return edges
        for _, tgt, data in self.graph.out_edges(node_id, data=True):
            edges.append((tgt, data.get("relationship") or "", False))
        for src, _, data in self.graph.in_edges(node_id, data=True):
            edges.append((src, data.get("relationship") or "", True))
        return edges

    def to_mermaid(self, focus: Optional[str] = None, max_nodes: int = 40, depth: int = 2) -> str:
        return mermaid_from_graph(self.graph, focus=focus, max_nodes=max_nodes, depth=depth)

    def find_path(self, source_id: str, target_id: str) -> List[str]:
        try:
            return nx.shortest_path(self.graph, source_id, target_id)
        except (nx.NetworkXError, nx.NetworkXNoPath):
            return []

    def get_entity_summary(self, entity_id: str) -> Optional[Dict]:
        if entity_id not in self.graph:
            return None
        data = self.graph.nodes[entity_id]
        neighbors = list(self.graph.neighbors(entity_id))
        return {
            "id": entity_id,
            **data,
            "neighbors": neighbors[:10],
            "neighbor_count": len(neighbors),
        }

    def save(self):
        if not self.enabled:
            return
        os.makedirs(os.path.dirname(self.persist_path) or ".", exist_ok=True)
        try:
            data = nx.node_link_data(self.graph, edges="links")
        except TypeError:
            data = nx.node_link_data(self.graph)
        with open(self.persist_path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self):
        if not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path) as f:
                data = json.load(f)
            self.graph = _load_node_link(data)
        except Exception:
            self.graph = nx.DiGraph()


def _load_node_link(data: dict):
    if "links" in data and "edges" not in data:
        return nx.node_link_graph(data, edges="links")
    try:
        return nx.node_link_graph(data)
    except Exception:
        return nx.node_link_graph(data, edges="links")
