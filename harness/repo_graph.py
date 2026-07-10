import json
import os
from collections import defaultdict
from itertools import combinations
from typing import Dict, List, Optional, Set

import networkx as nx

from .models import CodeEntity, EntityType, RelationshipType
from .config import Config
from .utils import extract_imports_from_entities, extract_exports


DEFAULT_SKIP_MODULES: Set[str] = {
    "os", "sys", "re", "json", "math", "typing", "collections",
    "dataclasses", "enum", "pathlib", "abc", "io", "functools",
    "itertools", "datetime", "copy", "hashlib", "uuid",
}


class RepoGraph:
    def __init__(self, config: Config):
        self.config = config
        self.persist_path = config.repo_graph.get(
            "persist_path", ".code-harness/repo_graph.json"
        )
        self.enabled = config.repo_graph.get("enabled", True)
        self.skip_modules = config.repo_graph.get(
            "skip_modules", sorted(DEFAULT_SKIP_MODULES)
        )
        self.graph = nx.DiGraph()

    def update_repo(self, repo_name: str, repo_path: str,
                    entities: List[CodeEntity]):
        if not self.enabled:
            return

        imports = extract_imports_from_entities(
            entities, skip_builtins=set(self.skip_modules)
        )
        exports = extract_exports(entities)

        self.graph.add_node(repo_name, **{
            "repo_path": repo_path,
            "imports": sorted(imports),
            "exports": sorted(exports),
            "entity_count": len(entities),
        })

    def build_cross_repo_edges(self):
        if not self.enabled:
            return

        repos = list(self.graph.nodes(data=True))
        if len(repos) < 2:
            return

        self.graph.remove_edges_from(list(self.graph.edges()))

        module_importers: Dict[str, Set[str]] = defaultdict(set)
        entity_exporters: Dict[str, Set[str]] = defaultdict(set)
        repo_imports: Dict[str, Set[str]] = {}

        for repo, data in repos:
            imports = set(data.get("imports", []))
            exports = set(data.get("exports", []))
            repo_imports[repo] = imports
            for imp in imports:
                module_importers[imp].add(repo)
            for exp in exports:
                entity_exporters[exp].add(repo)

        for module, importers in module_importers.items():
            if len(importers) >= 2:
                for a, b in combinations(importers, 2):
                    self._add_edge_rel(a, b, "shared_import", {"module": module})

        for entity, exporters in entity_exporters.items():
            if len(exporters) >= 2:
                for a, b in combinations(exporters, 2):
                    self._add_edge_rel(a, b, "shared_entity", {"entity": entity})

        for repo, imports in repo_imports.items():
            for imp in imports:
                exporters = entity_exporters.get(imp, set()) - {repo}
                for exporter in exporters:
                    self._add_edge_rel(
                        repo, exporter, "depends_on", {"module": imp}
                    )

    def _add_edge_rel(self, source: str, target: str,
                      rel_type: str, details: dict):
        existing = dict(self.graph.get_edge_data(source, target) or {})
        rels = existing.get("relationships", [])
        new_rel = {"type": rel_type, **details}
        if new_rel not in rels:
            rels = [*rels, new_rel]
        self.graph.add_edge(source, target, relationships=rels)

    def get_related_repos(self, repo_name: str) -> List[Dict]:
        if not self.enabled or repo_name not in self.graph:
            return []

        related = []
        for _, target, data in self.graph.edges(repo_name, data=True):
            rels = data.get("relationships", [])
            types = list({r["type"] for r in rels})
            related.append({
                "repo": target,
                "direction": "outgoing",
                "relationships": rels,
                "relationship_types": types,
            })
        for source, _, data in self.graph.in_edges(repo_name, data=True):
            rels = data.get("relationships", [])
            types = list({r["type"] for r in rels})
            related.append({
                "repo": source,
                "direction": "incoming",
                "relationships": rels,
                "relationship_types": types,
            })
        return related

    def get_all_repos(self) -> List[Dict]:
        repos = []
        for node, data in self.graph.nodes(data=True):
            repos.append({"name": node, **data})
        return repos

    def remove_repo(self, repo_name: str):
        if repo_name in self.graph:
            self.graph.remove_node(repo_name)

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

    def summary(self) -> str:
        repos = self.get_all_repos()
        if not repos:
            return "No repositories indexed."

        lines = [f"Repositories indexed: {len(repos)}"]
        for r in repos:
            imp_count = len(r.get("imports", []))
            exp_count = len(r.get("exports", []))
            lines.append(
                f"  - {r['name']}: {r.get('entity_count', 0)} entities, "
                f"{imp_count} imports, {exp_count} exports"
            )

        edge_count = self.graph.number_of_edges()
        if edge_count > 0:
            lines.append(f"\nCross-repo relationships: {edge_count}")
            for u, v, data in self.graph.edges(data=True):
                rels = data.get("relationships", [])
                types = list({r["type"] for r in rels})
                details = []
                for r in rels[:3]:
                    if r.get("module"):
                        details.append(f"{r['type']}: {r['module']}")
                    elif r.get("entity"):
                        details.append(f"{r['type']}: {r['entity']}")
                detail_str = f" ({'; '.join(details)})" if details else ""
                lines.append(
                    f"  {u} --[{', '.join(types)}]--> {v}{detail_str}"
                )

        return "\n".join(lines)
