import json
import os
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set

import networkx as nx
from rich.progress import track

from .models import CodeEntity, EntityType, Relationship, RelationshipType
from .config import Config
from .utils import extract_imports, extract_exports


class KnowledgeGraph:
    def __init__(self, config: Config, repo_name: str = ""):
        self.config = config
        self.repo_name = repo_name
        self.enabled = config.knowledge_graph.get("enabled", True)
        self.graph = nx.DiGraph()

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

        relationships = []
        rels: List[tuple] = []

        # Build indexes once — O(n), then lookups are O(1)
        name_index: Dict[str, str] = {}
        file_index: Dict[str, List[CodeEntity]] = defaultdict(list)
        method_by_class: Dict[str, Dict[str, CodeEntity]] = defaultdict(dict)

        for entity in track(entities, description="  Indexing entities"):
            self.graph.add_node(entity.id, **{
                "name": entity.name,
                "type": entity.type.value,
                "file_path": entity.file_path,
                "start_line": entity.start_line,
                "end_line": entity.end_line,
                "repo_name": self.repo_name,
            })
            name_index[entity.name] = entity.id
            file_index[entity.file_path].append(entity)
            if entity.type == EntityType.METHOD:
                dot = entity.name.rfind(".")
                if dot > 0:
                    cls = entity.name[:dot]
                    method_by_class[cls][entity.name] = entity

        for entity in track(entities, description="  Building edges"):
            if entity.type == EntityType.FILE:
                siblings = file_index.get(entity.file_path, [])
                for other in siblings:
                    if other.id != entity.id:
                        rels.append((entity.id, other.id, "contains"))

            elif entity.type == EntityType.CLASS:
                cls_name = entity.name
                for base_name in entity.metadata.get("bases", []):
                    target = name_index.get(base_name)
                    if target:
                        rels.append((entity.id, target, "inherits"))
                for method_name in entity.metadata.get("methods", []):
                    methods = method_by_class.get(cls_name, {})
                    target = methods.get(f"{cls_name}.{method_name}")
                    if not target:
                        target = name_index.get(method_name)
                    if target:
                        rels.append((entity.id, target, "contains"))

            elif entity.type in (EntityType.FUNCTION, EntityType.METHOD):
                for imp in self._detect_imports(entity):
                    target = name_index.get(imp)
                    if target:
                        rels.append((entity.id, target, "calls"))
                for ref in self._detect_references(entity):
                    target = name_index.get(ref)
                    if target:
                        rels.append((entity.id, target, "references"))

        # Batch-add edges to NetworkX — single pass instead of per-edge overhead
        for src, tgt, rel_type in track(rels, description="  Adding edges"):
            self.graph.add_edge(src, tgt, relationship=rel_type)
            relationships.append(Relationship(
                source_id=src, target_id=tgt,
                relationship_type=RelationshipType(rel_type),
            ))

        return relationships

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
        data = nx.node_link_data(self.graph)
        with open(self.persist_path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self):
        if not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path) as f:
                data = json.load(f)
            self.graph = nx.node_link_graph(data)
        except Exception:
            self.graph = nx.DiGraph()
