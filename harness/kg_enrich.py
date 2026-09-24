"""Additive KG extractors: exposes, tested_by, gloss. Fail-soft by design."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .models import CodeEntity, EntityType, RelationshipType

COMMON_NAMES = frozenset({
    "get", "set", "main", "test", "setup", "teardown", "helper", "util",
    "utils", "data", "config", "init", "run", "load", "save", "parse",
    "build", "index", "query", "info", "app", "cli", "name", "value",
    "type", "id", "path", "self", "cls", "args", "kwargs",
})

ADD_PARSER_RE = re.compile(r"""add_parser\(\s*['"]([^'"]+)['"]""")
CLICK_COMMAND_RE = re.compile(
    r"""@(?:[\w.]+\.)?(?:command|group)\(\s*(?:['"]([^'"]+)['"])?"""
)
HTTP_DECORATOR_RE = re.compile(
    r"""@[\w.]+\.(get|post|put|patch|delete|options|head|route|websocket)\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
EXPRESS_RE = re.compile(
    r"""(?:app|router)\.(get|post|put|patch|delete)\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)
MCP_TOOL_RE = re.compile(
    r"""@(?:[\w.]+\.)?tool\(\s*(?:name\s*=\s*)?['"]([^'"]+)['"]"""
    r"""|@mcp\.tool\(\s*\)"""
)
FROM_IMPORT_RE = re.compile(
    r"""from\s+([\w.]+)\s+import\s+([A-Za-z_][\w,\s]*)"""
)
IMPORT_RE = re.compile(r"""^\s*import\s+([A-Za-z_][\w.]*)""", re.MULTILINE)
IDENT_RE = re.compile(r"""\b([A-Z][A-Za-z0-9_]+|[a-z_][a-z0-9_]{3,})\b""")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
ENTITY_LINE_RE = re.compile(
    r"""^(?:entity|entities)\s*:\s*(.+)$""", re.MULTILINE | re.IGNORECASE
)
TEST_FILE_RE = re.compile(
    r"""(?:^|/)(?:tests?/.*|test_[^/]+|[^/]+_test)\.(?:py|go|js|ts)$"""
)

EnrichRel = Tuple[str, str, str, Dict]


def _endpoint_id(file_path: str, name: str) -> str:
    return f"endpoint:{file_path}:{name}"


def make_endpoint_entity(
    file_path: str,
    name: str,
    kind: str,
    confidence: float,
    source: str = "",
    start_line: int = 1,
) -> CodeEntity:
    return CodeEntity(
        id=_endpoint_id(file_path, name),
        name=name,
        type=EntityType.ENDPOINT,
        file_path=file_path,
        start_line=start_line,
        end_line=start_line,
        source_code=source or f"{kind} endpoint {name}",
        metadata={"kind": kind, "confidence": confidence},
    )


def extract_imported_symbols(source: str) -> Set[str]:
    names: Set[str] = set()
    for match in FROM_IMPORT_RE.finditer(source or ""):
        for part in match.group(2).split(","):
            token = part.strip().split(" as ")[-1].strip()
            if token and token != "*":
                names.add(token)
    for match in IMPORT_RE.finditer(source or ""):
        names.add(match.group(1).split(".")[-1])
    return names


def extract_exposes(entities: Sequence[CodeEntity]) -> Tuple[List[CodeEntity], List[EnrichRel]]:
    extra: Dict[str, CodeEntity] = {}
    rels: List[EnrichRel] = []
    by_file: Dict[str, List[CodeEntity]] = {}
    for entity in entities:
        by_file.setdefault(entity.file_path, []).append(entity)

    seen_edges: Set[Tuple[str, str]] = set()

    def add_endpoint(source_id: str, file_path: str, name: str, kind: str,
                     confidence: float, snippet: str = "", line: int = 1):
        name = (name or "").strip()
        if not name:
            return
        endpoint = extra.get(_endpoint_id(file_path, name)) or make_endpoint_entity(
            file_path, name, kind, confidence, snippet, line
        )
        extra[endpoint.id] = endpoint
        key = (source_id, endpoint.id)
        if key in seen_edges or source_id == endpoint.id:
            return
        seen_edges.add(key)
        rels.append((
            source_id,
            endpoint.id,
            RelationshipType.EXPOSES.value,
            {"kind": kind, "confidence": confidence, "endpoint": name},
        ))

    for entity in entities:
        source = entity.source_code or ""
        if not source:
            continue
        if entity.type not in (
            EntityType.FILE, EntityType.FUNCTION, EntityType.METHOD, EntityType.CLASS
        ):
            continue
        if _is_test_path(entity.file_path):
            continue

        for match in ADD_PARSER_RE.finditer(source):
            cmd = match.group(1)
            add_endpoint(entity.id, entity.file_path, cmd, "cli", 0.5, match.group(0))
            handler = next(
                (
                    other.id for other in by_file.get(entity.file_path, [])
                    if other.name in (f"cmd_{cmd}", cmd, f"{cmd}_command")
                    or other.id.endswith(f":cmd_{cmd}")
                    or other.id.endswith(f":{cmd}")
                ),
                None,
            )
            if handler:
                add_endpoint(handler, entity.file_path, cmd, "cli", 0.6, match.group(0))

        for match in CLICK_COMMAND_RE.finditer(source):
            cmd = match.group(1) or entity.name
            if entity.type in (EntityType.FUNCTION, EntityType.METHOD):
                add_endpoint(entity.id, entity.file_path, cmd, "cli", 0.8, match.group(0))
            add_endpoint(
                f"file:{entity.file_path}", entity.file_path, cmd, "cli", 0.7, match.group(0)
            )

        for match in HTTP_DECORATOR_RE.finditer(source):
            route = match.group(2)
            name = route or match.group(1)
            add_endpoint(entity.id, entity.file_path, name, "http", 0.8, match.group(0))
            if entity.type != EntityType.FILE:
                add_endpoint(
                    f"file:{entity.file_path}", entity.file_path, name, "http", 0.7, match.group(0)
                )

        for match in EXPRESS_RE.finditer(source):
            route = match.group(2)
            add_endpoint(entity.id, entity.file_path, route, "http", 0.7, match.group(0))

        for match in MCP_TOOL_RE.finditer(source):
            tool = match.group(1) or entity.name
            add_endpoint(entity.id, entity.file_path, tool, "mcp", 0.8, match.group(0))

    return list(extra.values()), rels


def _is_test_path(path: str) -> bool:
    norm = path.replace("\\", "/")
    return bool(TEST_FILE_RE.search(norm))


def extract_tested_by(entities: Sequence[CodeEntity]) -> List[EnrichRel]:
    name_to_ids: Dict[str, List[str]] = {}
    for entity in entities:
        if entity.type in (EntityType.FILE, EntityType.DOCUMENTATION, EntityType.ENDPOINT):
            continue
        if _is_test_path(entity.file_path):
            continue
        name_to_ids.setdefault(entity.name, []).append(entity.id)
        simple = entity.name.split(".")[-1]
        if simple != entity.name:
            name_to_ids.setdefault(simple, []).append(entity.id)

    rels: List[EnrichRel] = []
    seen: Set[Tuple[str, str]] = set()
    for entity in entities:
        if not _is_test_path(entity.file_path):
            continue
        if entity.type not in (EntityType.FILE, EntityType.FUNCTION, EntityType.METHOD):
            continue
        source = entity.source_code or ""
        imported = extract_imported_symbols(source)
        mentioned = set(IDENT_RE.findall(source))
        candidates = imported | (mentioned & set(name_to_ids))
        for name in candidates:
            if name.lower() in COMMON_NAMES or name in COMMON_NAMES:
                continue
            targets = list(dict.fromkeys(name_to_ids.get(name, [])))
            if len(targets) != 1:
                continue
            target = targets[0]
            if target == entity.id:
                continue
            key = (entity.id, target)
            if key in seen:
                continue
            seen.add(key)
            confidence = 0.8 if name in imported else 0.55
            rels.append((
                entity.id,
                target,
                RelationshipType.TESTED_BY.value,
                {"confidence": confidence, "via": name},
            ))
    return rels


def parse_frontmatter_entities(text: str) -> List[str]:
    match = FRONTMATTER_RE.search(text or "")
    if not match:
        return []
    block = match.group(1)
    found: List[str] = []
    for line in ENTITY_LINE_RE.finditer(block):
        raw = line.group(1).strip()
        raw = raw.strip("[]")
        for part in re.split(r"[,\s]+", raw):
            token = part.strip().strip("'\"")
            if token and ":" in token:
                found.append(token)
    return found


def _gloss_title(path: str, text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return re.sub(r"^#+\s*", "", stripped).strip() or Path(path).stem
    return Path(path).stem


def load_gloss_entities(repo_path: str, gloss_dirs: Optional[Sequence[str]] = None) -> List[CodeEntity]:
    if not repo_path or not os.path.isdir(repo_path):
        return []
    dirs = list(gloss_dirs or [".code-harness/gloss", "knowledge/gloss"])
    entities: List[CodeEntity] = []
    seen: Set[str] = set()
    root = Path(repo_path)
    for rel_dir in dirs:
        base = root / rel_dir
        if not base.is_dir():
            continue
        for md in sorted(base.rglob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel_path = str(md.relative_to(root)).replace("\\", "/")
            title = _gloss_title(rel_path, text)
            eid = f"doc:{rel_path}:gloss"
            if eid in seen:
                continue
            seen.add(eid)
            targets = parse_frontmatter_entities(text)
            entities.append(CodeEntity(
                id=eid,
                name=title,
                type=EntityType.DOCUMENTATION,
                file_path=rel_path,
                start_line=1,
                end_line=len(text.splitlines()) or 1,
                source_code=text,
                docstring=title,
                metadata={
                    "kind": "gloss",
                    "linked_entities": targets,
                },
            ))
    return entities


def extract_gloss(entities: Sequence[CodeEntity], known_ids: Optional[Set[str]] = None) -> List[EnrichRel]:
    known = known_ids or {e.id for e in entities}
    file_sources = {
        e.file_path: e.source_code or ""
        for e in entities
        if e.type == EntityType.FILE
    }
    rels: List[EnrichRel] = []
    seen: Set[Tuple[str, str]] = set()
    for entity in entities:
        meta = entity.metadata or {}
        path = entity.file_path.replace("\\", "/")
        is_gloss = meta.get("kind") == "gloss" or (
            entity.type == EntityType.DOCUMENTATION and "/gloss/" in f"/{path}"
        )
        if not is_gloss:
            continue
        targets = list(meta.get("linked_entities") or [])
        if not targets:
            targets = parse_frontmatter_entities(entity.source_code or "")
        if not targets:
            targets = parse_frontmatter_entities(file_sources.get(entity.file_path, ""))
        for target in targets:
            if target not in known:
                continue
            key = (entity.id, target)
            if key in seen:
                continue
            seen.add(key)
            rels.append((
                entity.id,
                target,
                RelationshipType.GLOSS.value,
                {"confidence": 0.9},
            ))
    return rels


def collect_enrichment_entities(
    entities: Sequence[CodeEntity],
    repo_path: str = "",
    config: Optional[object] = None,
) -> List[CodeEntity]:
    kg_cfg = getattr(config, "knowledge_graph", None) or {}
    extra: List[CodeEntity] = []
    seen = {e.id for e in entities}
    if kg_cfg.get("enrich", True) and kg_cfg.get("extract_gloss", True):
        for gloss in load_gloss_entities(repo_path, kg_cfg.get("gloss_dirs")):
            if gloss.id not in seen:
                extra.append(gloss)
                seen.add(gloss.id)
    if kg_cfg.get("enrich", True) and kg_cfg.get("extract_exposes", True):
        endpoints, _ = extract_exposes(list(entities) + extra)
        for endpoint in endpoints:
            if endpoint.id not in seen:
                extra.append(endpoint)
                seen.add(endpoint.id)
    return extra


def extract_enrichment_relationships(
    entities: Sequence[CodeEntity],
    config: Optional[object] = None,
) -> List[EnrichRel]:
    kg_cfg = getattr(config, "knowledge_graph", None) or {}
    if not kg_cfg.get("enrich", True):
        return []
    rels: List[EnrichRel] = []
    if kg_cfg.get("extract_exposes", True):
        try:
            _, expose_rels = extract_exposes(entities)
            rels.extend(expose_rels)
        except Exception:
            pass
    if kg_cfg.get("extract_tested_by", True):
        try:
            rels.extend(extract_tested_by(entities))
        except Exception:
            pass
    if kg_cfg.get("extract_gloss", True):
        try:
            rels.extend(extract_gloss(entities))
        except Exception:
            pass
    return rels


_MERMAID_PRIOR = {
    "gloss": 0.9, "exposes": 0.85, "calls": 0.8, "tested_by": 0.7,
    "inherits": 0.65, "contains": 0.5, "imports": 0.4, "references": 0.15,
}


def mermaid_from_graph(graph, focus: Optional[str] = None, max_nodes: int = 40,
                       depth: int = 2) -> str:
    if graph.number_of_nodes() == 0:
        return "graph TD\n  empty[\"empty graph\"]\n"

    def incident(node_id: str):
        out = []
        for _, tgt, data in graph.out_edges(node_id, data=True):
            out.append((tgt, data.get("relationship") or "", _MERMAID_PRIOR.get(data.get("relationship"), 0.2)))
        for src, _, data in graph.in_edges(node_id, data=True):
            out.append((src, data.get("relationship") or "", _MERMAID_PRIOR.get(data.get("relationship"), 0.2)))
        out.sort(key=lambda item: item[2], reverse=True)
        return out

    chosen: List[str] = []
    if focus and focus in graph:
        chosen = [focus]
        per_rel = {}
        rel_cap = 3
        skip_refs = True
        for nbr, rel, prior in incident(focus):
            if rel == "references" and skip_refs:
                continue
            if per_rel.get(rel, 0) >= rel_cap:
                continue
            if nbr in chosen:
                continue
            chosen.append(nbr)
            per_rel[rel] = per_rel.get(rel, 0) + 1
            if len(chosen) >= max_nodes:
                break
        if depth > 1 and len(chosen) < max_nodes:
            for node in list(chosen[1:]):
                npath = (graph.nodes[node].get("file_path") or "").replace("\\", "/")
                if "/test" in f"/{npath}" or npath.startswith("tests/"):
                    continue
                for nbr, rel, prior in incident(node):
                    if rel == "references":
                        continue
                    if nbr in chosen:
                        continue
                    chosen.append(nbr)
                    if len(chosen) >= max_nodes:
                        break
                if len(chosen) >= max_nodes:
                    break
    else:
        chosen = list(graph.nodes())

    if len(chosen) > max_nodes:
        if focus and focus in chosen:
            chosen = [focus] + [n for n in chosen if n != focus][: max_nodes - 1]
        else:
            degrees = dict(graph.degree(chosen))
            chosen = sorted(chosen, key=lambda n: degrees.get(n, 0), reverse=True)[:max_nodes]

    chosen_set = set(chosen)

    def mid(node_id: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", node_id)

    def label(node_id: str) -> str:
        data = graph.nodes.get(node_id, {})
        name = data.get("name") or node_id.split(":")[-1]
        return name.replace('"', "'")

    lines = ["graph TD"]
    for node_id in chosen:
        lines.append(f'  {mid(node_id)}["{label(node_id)}"]')
    for src, tgt, data in graph.edges(data=True):
        if src not in chosen_set or tgt not in chosen_set:
            continue
        rel = data.get("relationship") or ""
        if rel:
            lines.append(f"  {mid(src)} -->|{rel}| {mid(tgt)}")
        else:
            lines.append(f"  {mid(src)} --> {mid(tgt)}")
    return "\n".join(lines) + "\n"
