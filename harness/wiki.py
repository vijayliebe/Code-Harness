"""Deterministic living wiki from the knowledge graph (no LLM polish).

Writes OKF ``WikiPage`` markdown under ``knowledge/wiki/`` (or ``--out``).
Pages are package-level plus an architecture index and an endpoints page
when ``exposes`` edges exist. Every heading cites ``path:symbol``.
Mermaid is exported from KG edges via ``to_mermaid`` / ``mermaid_from_graph``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .config import Config
from .knowledge_graph import KnowledgeGraph, _load_node_link
from .kg_enrich import load_gloss_entities, mermaid_from_graph
from .okf import (
    WikiPage,
    cite_from_entity_id,
    default_wiki_dir,
    dump_okf_markdown,
    parse_okf_markdown,
)

DOCSTRING_RE = re.compile(r'(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', re.DOTALL)
SKIP_PAGE_PREFIXES = (".code-harness/", "knowledge/wiki/")


class MissingGraphError(FileNotFoundError):
    """Raised when wiki generate cannot find a persisted knowledge graph."""


@dataclass
class WikiGenerateResult:
    wiki_dir: str
    pages: List[str] = field(default_factory=list)
    page_count: int = 0
    mermaid_pages: List[str] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)
    graph_hash: str = ""


def graph_content_hash(graph) -> str:
    nodes = sorted(str(n) for n in graph.nodes())
    edges = sorted(
        (str(src), str(tgt), str((data or {}).get("relationship") or ""))
        for src, tgt, data in graph.edges(data=True)
    )
    payload = json.dumps({"n": nodes, "e": edges}, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def resolve_graph_path(repo_path: str, repo_name: str, config: Optional[Config] = None,
                       graph_path: Optional[str] = None) -> str:
    if graph_path:
        return graph_path
    cfg = config or Config()
    kg = KnowledgeGraph(cfg, repo_name=repo_name or "")
    candidates = [
        kg.persist_path,
        os.path.join(repo_path or ".", ".code-harness", f"graph_{repo_name}.json") if repo_name else "",
        os.path.join(repo_path or ".", ".code-harness", "graph.json"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    return kg.persist_path


def load_kg_or_raise(
    repo_path: str,
    config: Optional[Config] = None,
    repo_name: str = "",
    graph_path: Optional[str] = None,
) -> Tuple[KnowledgeGraph, str]:
    cfg = config or Config()
    cfg.repo_path = repo_path
    name = repo_name or os.path.basename(os.path.abspath(repo_path or "."))
    kg = KnowledgeGraph(cfg, repo_name=name)
    path = resolve_graph_path(repo_path, name, cfg, graph_path)
    if not os.path.isfile(path):
        raise MissingGraphError(
            f"Knowledge graph not found: {path}\n"
            f"    Run: python main.py index {repo_path or '.'}"
        )
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        kg.graph = _load_node_link(data)
        kg.persist_path = path
    except Exception as exc:
        raise MissingGraphError(
            f"Knowledge graph could not be loaded: {path} ({exc})\n"
            f"    Run: python main.py index {repo_path or '.'}"
        ) from exc
    if kg.graph.number_of_nodes() == 0:
        raise MissingGraphError(
            f"Knowledge graph is empty: {path}\n"
            f"    Run: python main.py index {repo_path or '.'}"
        )
    return kg, path


def generate_from_repo(
    repo_path: str,
    config: Optional[Config] = None,
    repo_name: str = "",
    out_dir: Optional[str] = None,
    graph_path: Optional[str] = None,
    module: Optional[str] = None,
) -> WikiGenerateResult:
    kg, _ = load_kg_or_raise(repo_path, config=config, repo_name=repo_name, graph_path=graph_path)
    name = repo_name or os.path.basename(os.path.abspath(repo_path or "."))
    dest = Path(out_dir) if out_dir else Path(default_wiki_dir(repo_path))
    return generate_wiki(
        kg, repo_path=repo_path, out_dir=dest, repo_name=name, module=module, config=config,
    )


def generate_wiki(
    kg: KnowledgeGraph,
    repo_path: str,
    out_dir: Path,
    repo_name: str = "",
    module: Optional[str] = None,
    config: Optional[Config] = None,
) -> WikiGenerateResult:
    graph = kg.graph
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ghash = graph_content_hash(graph)
    groups = _group_nodes(graph)
    gloss_by_target = _gloss_notes(graph, repo_path)

    wanted = None
    if module:
        wanted = _package_key(module) or module.replace("\\", "/").split("/")[0]

    pages: List[WikiPage] = []
    mermaid_pages: List[str] = []
    all_cites: List[str] = []

    pkg_pages = []
    for pkg in sorted(groups):
        if wanted and pkg != wanted:
            continue
        page = _render_package_page(
            pkg, groups[pkg], graph, repo_path, repo_name, ghash, gloss_by_target, out_dir,
        )
        pages.append(page)
        pkg_pages.append(page)
        if "```mermaid" in page.body:
            mermaid_pages.append(_page_filename(page))
        all_cites.extend((page.x_codeharness or {}).get("citations") or [])

    endpoints = _collect_endpoints(graph)
    if endpoints and not wanted:
        ep_page = _render_endpoints_page(endpoints, graph, repo_path, repo_name, ghash, out_dir)
        pages.append(ep_page)
        all_cites.extend((ep_page.x_codeharness or {}).get("citations") or [])

    arch = _render_architecture_page(
        graph, pkg_pages, endpoints if not wanted else [], repo_path, repo_name, ghash, out_dir,
    )
    pages.insert(0, arch)
    if "```mermaid" in arch.body:
        mermaid_pages.insert(0, "architecture.md")
    all_cites.extend((arch.x_codeharness or {}).get("citations") or [])

    written = []
    for page in pages:
        filename = _page_filename(page)
        text = dump_okf_markdown(page)
        wiki_on = True
        if config is not None:
            wiki_on = (getattr(config, "redaction", None) or {}).get("wiki", True)
        if wiki_on:
            from .redact import redact_and_audit, redaction_enabled

            if redaction_enabled(config):
                text = redact_and_audit(
                    text, action="redact.wiki", config=config,
                ).text
        dest = out_dir / filename
        dest.write_text(text, encoding="utf-8")
        written.append(filename)

    cites = sorted(dict.fromkeys(c for c in all_cites if c))
    return WikiGenerateResult(
        wiki_dir=str(out_dir),
        pages=written,
        page_count=len(written),
        mermaid_pages=mermaid_pages,
        citations=cites,
        graph_hash=ghash,
    )


def list_pages(wiki_dir: str | Path) -> List[Dict[str, str]]:
    root = Path(wiki_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Wiki directory not found: {root}")
    items = []
    for path in sorted(root.glob("*.md")):
        parsed = parse_okf_markdown(path.read_text(encoding="utf-8", errors="replace"))
        items.append({
            "path": path.name,
            "title": parsed.title or path.stem,
            "type": parsed.type or "",
        })
    return items


def show_page(wiki_dir: str | Path, name: str) -> str:
    root = Path(wiki_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Wiki directory not found: {root}")
    needle = (name or "").strip()
    candidates = [
        root / needle,
        root / f"{needle}.md",
        root / Path(needle).name,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand.read_text(encoding="utf-8", errors="replace")
    stem = Path(needle).stem
    for path in sorted(root.glob("*.md")):
        if path.stem == stem:
            return path.read_text(encoding="utf-8", errors="replace")
    raise FileNotFoundError(f"Wiki page not found: {name} (under {root})")


def _page_filename(page: WikiPage) -> str:
    slug = ((page.x_codeharness or {}).get("page") or page.title or "page")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(slug)).strip("-").lower()
    return f"{slug or 'page'}.md"


def _package_key(file_path: str) -> Optional[str]:
    path = (file_path or "").replace("\\", "/").lstrip("./")
    if not path:
        return None
    if path.startswith(SKIP_PAGE_PREFIXES):
        return None
    if "/gloss/" in f"/{path}":
        return None
    parts = path.split("/")
    if len(parts) == 1:
        return Path(parts[0]).stem or "root"
    return parts[0]


def _group_nodes(graph) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    for node_id, data in graph.nodes(data=True):
        ntype = str(data.get("type") or "")
        if ntype == "documentation" or data.get("kind") == "gloss":
            continue
        pkg = _package_key(data.get("file_path") or "")
        if not pkg:
            continue
        groups.setdefault(pkg, []).append(node_id)
    for pkg in groups:
        groups[pkg] = sorted(groups[pkg], key=_node_sort_key(graph))
    return groups


def _node_sort_key(graph):
    rank = {
        "file": 0, "module": 1, "class": 2, "endpoint": 3,
        "function": 4, "method": 5, "variable": 6,
    }

    def key(node_id: str):
        data = graph.nodes.get(node_id, {})
        return (rank.get(str(data.get("type") or ""), 9), str(data.get("name") or node_id))

    return key


def _cite_node(graph, node_id: str) -> str:
    data = graph.nodes.get(node_id, {})
    return cite_from_entity_id(
        str(node_id),
        file_path=str(data.get("file_path") or ""),
        name=str(data.get("name") or ""),
    )


def _rel_link(out_dir: Path, target: str, repo_path: str = "") -> str:
    """Href from a wiki page to a repo-relative path (stable, not CWD-based)."""
    target_norm = (target or "").replace("\\", "/").lstrip("./")
    if not target_norm:
        return ""
    root = Path(repo_path or ".")
    try:
        rel_out = os.path.relpath(Path(out_dir), root)
    except ValueError:
        rel_out = "knowledge/wiki"
    if rel_out in (".", ""):
        return target_norm
    up = "/".join([".."] * len(Path(rel_out).parts))
    return f"{up}/{target_norm}"


def _md_cite(cite: str, href: str) -> str:
    if href:
        return f"[`{cite}`]({href})"
    return f"`{cite}`"


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip().strip('"').strip("'")
        if stripped.startswith("#"):
            continue
        if stripped:
            return stripped[:220]
    return ""


def _peek_docstring(repo_path: str, file_path: str, start_line: int) -> str:
    full = Path(repo_path or ".") / (file_path or "")
    if not full.is_file():
        return ""
    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    idx = max(0, int(start_line or 1) - 1)
    window = "\n".join(lines[idx:idx + 24])
    match = DOCSTRING_RE.search(window)
    if match:
        return _first_line(match.group(1))
    return ""


def _summary_for(graph, node_id: str, repo_path: str) -> str:
    data = graph.nodes.get(node_id, {})
    peeked = _peek_docstring(repo_path, str(data.get("file_path") or ""), int(data.get("start_line") or 1))
    if peeked:
        return peeked
    name = str(data.get("name") or node_id)
    ntype = str(data.get("type") or "symbol")
    return f"{ntype} `{name}`"


def _neighbors(graph, node_id: str, rel: str) -> List[Tuple[str, str, Dict]]:
    found = []
    if node_id not in graph:
        return found
    for _, tgt, data in graph.out_edges(node_id, data=True):
        if (data or {}).get("relationship") == rel:
            found.append(("out", tgt, data or {}))
    for src, _, data in graph.in_edges(node_id, data=True):
        if (data or {}).get("relationship") == rel:
            found.append(("in", src, data or {}))
    return found


def _collect_endpoints(graph) -> List[str]:
    ids = []
    for node_id, data in graph.nodes(data=True):
        if str(data.get("type") or "") == "endpoint":
            ids.append(node_id)
            continue
        kind = str(data.get("kind") or "")
        if kind in {"cli", "http", "mcp"} and str(data.get("type") or "") != "documentation":
            ids.append(node_id)
    return sorted(set(ids), key=lambda n: _cite_node(graph, n))


def _gloss_notes(graph, repo_path: str) -> Dict[str, List[Dict[str, str]]]:
    notes: Dict[str, List[Dict[str, str]]] = {}

    def add(target: str, title: str, path: str, snippet: str):
        notes.setdefault(target, []).append({
            "title": title,
            "path": path,
            "snippet": snippet,
        })

    for node_id, data in graph.nodes(data=True):
        if data.get("kind") != "gloss" and str(data.get("type") or "") != "documentation":
            continue
        title = str(data.get("name") or Path(str(data.get("file_path") or "gloss")).stem)
        path = str(data.get("file_path") or "")
        snippet = _first_line(_peek_body(repo_path, path))
        for direction, other, _meta in _neighbors(graph, node_id, "gloss"):
            target = other if direction == "out" else other
            add(target, title, path, snippet)

    try:
        extras = load_gloss_entities(repo_path) if repo_path else []
    except Exception:
        extras = []
    for entity in extras:
        title = entity.name or Path(entity.file_path).stem
        snippet = _first_line(_body_without_frontmatter(entity.source_code or ""))
        for target in (entity.metadata or {}).get("linked_entities") or []:
            add(str(target), title, entity.file_path, snippet)
    return notes


def _peek_body(repo_path: str, file_path: str) -> str:
    full = Path(repo_path or ".") / (file_path or "")
    if not full.is_file():
        return ""
    try:
        return _body_without_frontmatter(full.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


def _body_without_frontmatter(text: str) -> str:
    match = re.match(r"\A---\s*\n.*?\n---\s*\n?", text or "", re.DOTALL)
    return text[match.end():] if match else (text or "")


def _human_arch_link(repo_path: str, out_dir: Path) -> Optional[Tuple[str, str]]:
    for cand in (".docs/architecture.md", "ARCHITECTURE.md"):
        if (Path(repo_path or ".") / cand).is_file():
            return cand, _rel_link(out_dir, cand, repo_path)
    return None


def _mermaid_block(graph, focus: Optional[str], max_nodes: int = 24) -> str:
    if graph.number_of_nodes() == 0:
        return ""
    if focus and focus not in graph:
        focus = None
    text = mermaid_from_graph(graph, focus=focus, max_nodes=max_nodes, depth=2)
    if "empty graph" in text:
        return ""
    return f"```mermaid\n{text.rstrip()}\n```\n"


def _focus_for_package(graph, node_ids: Sequence[str]) -> Optional[str]:
    classes = [n for n in node_ids if str(graph.nodes[n].get("type") or "") == "class"]
    if classes:
        return max(classes, key=lambda n: graph.degree(n) if n in graph else 0)
    files = [n for n in node_ids if str(graph.nodes[n].get("type") or "") == "file"]
    if files:
        return files[0]
    return node_ids[0] if node_ids else None


def _render_package_page(
    pkg: str,
    node_ids: Sequence[str],
    graph,
    repo_path: str,
    repo_name: str,
    ghash: str,
    gloss_by_target: Dict[str, List[Dict[str, str]]],
    out_dir: Path,
) -> WikiPage:
    classes = [n for n in node_ids if str(graph.nodes[n].get("type") or "") == "class"]
    funcs = [
        n for n in node_ids
        if str(graph.nodes[n].get("type") or "") in {"function", "method"}
    ]
    files = [n for n in node_ids if str(graph.nodes[n].get("type") or "") == "file"]
    endpoints = [n for n in node_ids if str(graph.nodes[n].get("type") or "") == "endpoint"]

    citations = []
    for nid in list(files) + list(classes) + list(funcs) + list(endpoints):
        citations.append(_cite_node(graph, nid))

    lines = [f"# {pkg}", ""]
    lines.append("Generated from the knowledge graph (deterministic template; no LLM polish).")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    bits = []
    if classes:
        bits.append(f"{len(classes)} class" + ("es" if len(classes) != 1 else ""))
    if funcs:
        bits.append(f"{len(funcs)} function" + ("s" if len(funcs) != 1 else ""))
    if endpoints:
        bits.append(f"{len(endpoints)} endpoint" + ("s" if len(endpoints) != 1 else ""))
    lines.append(f"Package `{pkg}` — {', '.join(bits) or 'symbols from the KG'}.")
    if classes:
        top = classes[0]
        stub = _summary_for(graph, top, repo_path)
        cite = _cite_node(graph, top)
        href = _rel_link(out_dir, graph.nodes[top].get("file_path") or "", repo_path)
        lines.append(f"Key class { _md_cite(cite, href) } — {stub}")
        citations.append(cite)
    lines.append("")

    lines.append("## Symbols")
    lines.append("")
    symbol_ids = [n for n in node_ids if str(graph.nodes[n].get("type") or "") in {"class", "function", "method", "file"}]
    for nid in symbol_ids:
        data = graph.nodes[nid]
        cite = _cite_node(graph, nid)
        href = _rel_link(out_dir, data.get("file_path") or "", repo_path)
        stub = _summary_for(graph, nid, repo_path)
        lines.append(f"- {_md_cite(cite, href)} — {stub}")
        citations.append(cite)
    if not symbol_ids:
        lines.append("- (none)")
    lines.append("")

    test_lines = []
    for nid in node_ids:
        for direction, other, _meta in _neighbors(graph, nid, "tested_by"):
            # extractor stores test --tested_by--> symbol
            test_id, symbol_id = (nid, other) if direction == "out" else (other, nid)
            if str(graph.nodes.get(test_id, {}).get("type") or "") in {"file"}:
                continue
            test_cite = _cite_node(graph, test_id)
            sym_cite = _cite_node(graph, symbol_id)
            href = _rel_link(out_dir, graph.nodes.get(test_id, {}).get("file_path") or "", repo_path)
            test_lines.append(f"- {_md_cite(test_cite, href)} tests `{sym_cite}`")
            citations.extend([test_cite, sym_cite])
    if test_lines:
        lines.append("## Tests")
        lines.append("")
        lines.extend(sorted(dict.fromkeys(test_lines)))
        lines.append("")

    ep_lines = []
    for nid in node_ids:
        for direction, other, meta in _neighbors(graph, nid, "exposes"):
            endpoint_id = other if direction == "out" else nid
            source_id = nid if direction == "out" else other
            ep_cite = _cite_node(graph, endpoint_id)
            src_cite = _cite_node(graph, source_id)
            href = _rel_link(out_dir, graph.nodes.get(endpoint_id, {}).get("file_path") or "", repo_path)
            kind = (meta or {}).get("kind") or graph.nodes.get(endpoint_id, {}).get("kind") or "endpoint"
            ep_lines.append(f"- {_md_cite(ep_cite, href)} ({kind}) via `{src_cite}`")
            citations.extend([ep_cite, src_cite])
    for nid in endpoints:
        ep_cite = _cite_node(graph, nid)
        href = _rel_link(out_dir, graph.nodes[nid].get("file_path") or "", repo_path)
        ep_lines.append(f"- {_md_cite(ep_cite, href)}")
        citations.append(ep_cite)
    if ep_lines:
        lines.append("## Endpoints")
        lines.append("")
        lines.extend(sorted(dict.fromkeys(ep_lines)))
        lines.append("")

    gloss_lines = []
    for nid in node_ids:
        for note in gloss_by_target.get(nid, []):
            href = _rel_link(out_dir, note["path"], repo_path)
            snippet = note["snippet"] or note["title"]
            gloss_lines.append(f"- [{note['title']}]({href}) — {snippet}")
            if note["path"]:
                citations.append(note["path"])
    if gloss_lines:
        lines.append("## Gloss")
        lines.append("")
        lines.extend(sorted(dict.fromkeys(gloss_lines)))
        lines.append("")

    focus = _focus_for_package(graph, node_ids)
    mermaid = _mermaid_block(graph, focus)
    if mermaid:
        lines.append("## Graph")
        lines.append("")
        lines.append(mermaid)
        if not mermaid.endswith("\n"):
            lines.append("")

    unique_cites = sorted(dict.fromkeys(c for c in citations if c))
    lines.append("## Citations")
    lines.append("")
    for cite in unique_cites:
        lines.append(f"- `{cite}`")
    lines.append("")

    description = _first_line(next((_summary_for(graph, n, repo_path) for n in classes), "") or f"Package {pkg}")
    return WikiPage(
        title=pkg,
        description=description,
        generated=True,
        verified=False,
        tags=["wiki", "package", pkg],
        body="\n".join(lines),
        x_codeharness={
            "kind": "wiki",
            "page": pkg,
            "repo": repo_name,
            "citations": unique_cites,
            "graph_hash": ghash,
            "focus": focus or "",
        },
    )


def _render_endpoints_page(
    endpoint_ids: Sequence[str],
    graph,
    repo_path: str,
    repo_name: str,
    ghash: str,
    out_dir: Path,
) -> WikiPage:
    citations = []
    lines = ["# Endpoints", "", "Routes and CLI commands discovered via `exposes` edges.", ""]
    lines.append("## Symbols")
    lines.append("")
    for nid in endpoint_ids:
        data = graph.nodes[nid]
        cite = _cite_node(graph, nid)
        href = _rel_link(out_dir, data.get("file_path") or "", repo_path)
        kind = data.get("kind") or "endpoint"
        sources = []
        for direction, other, _meta in _neighbors(graph, nid, "exposes"):
            src = other if direction == "in" else nid
            if direction == "in":
                sources.append(_cite_node(graph, src))
        via = f" via `{sources[0]}`" if sources else ""
        lines.append(f"- {_md_cite(cite, href)} ({kind}){via}")
        citations.append(cite)
        citations.extend(sources)
    lines.append("")
    unique = sorted(dict.fromkeys(citations))
    lines.append("## Citations")
    lines.append("")
    for cite in unique:
        lines.append(f"- `{cite}`")
    lines.append("")
    return WikiPage(
        title="endpoints",
        description="Endpoints exposed by the repository (CLI / HTTP / MCP).",
        generated=True,
        verified=False,
        tags=["wiki", "endpoints"],
        body="\n".join(lines),
        x_codeharness={
            "kind": "wiki",
            "page": "endpoints",
            "repo": repo_name,
            "citations": unique,
            "graph_hash": ghash,
        },
    )


def _render_architecture_page(
    graph,
    package_pages: Sequence[WikiPage],
    endpoint_ids: Sequence[str],
    repo_path: str,
    repo_name: str,
    ghash: str,
    out_dir: Path,
) -> WikiPage:
    citations = []
    title = f"{repo_name} architecture" if repo_name else "architecture"
    lines = [f"# {title}", ""]
    lines.append(
        "Living wiki generated from the knowledge graph. "
        "This file does **not** replace human docs."
    )
    human = _human_arch_link(repo_path, out_dir)
    if human:
        path, href = human
        lines.append(f"Human architecture notes: [`{path}`]({href}).")
    lines.append("")
    lines.append("## Packages")
    lines.append("")
    if package_pages:
        for page in package_pages:
            fname = _page_filename(page)
            lines.append(f"- [{page.title}]({fname}) — {page.description or 'package page'}")
    else:
        lines.append("- (none)")
    lines.append("")
    if endpoint_ids:
        lines.append("## Endpoints")
        lines.append("")
        lines.append("See [endpoints](endpoints.md) for the `exposes` index.")
        for nid in endpoint_ids[:12]:
            cite = _cite_node(graph, nid)
            citations.append(cite)
            href = _rel_link(out_dir, graph.nodes[nid].get("file_path") or "", repo_path)
            lines.append(f"- {_md_cite(cite, href)}")
        lines.append("")

    # Prefer a class focus so the diagram has a subgraph; fall back to degree trim.
    classes = [
        n for n, d in graph.nodes(data=True)
        if str(d.get("type") or "") == "class"
    ]
    focus = max(classes, key=lambda n: graph.degree(n), default=None)
    mermaid = _mermaid_block(graph, focus, max_nodes=30)
    if mermaid:
        lines.append("## Graph")
        lines.append("")
        lines.append(mermaid)

    # A few high-signal cites so the index page is itself citeable.
    for nid in list(classes)[:8]:
        citations.append(_cite_node(graph, nid))
    unique = sorted(dict.fromkeys(c for c in citations if c))
    if unique:
        lines.append("## Citations")
        lines.append("")
        for cite in unique:
            lines.append(f"- `{cite}`")
        lines.append("")

    return WikiPage(
        title="architecture" if not repo_name else title,
        description=f"Generated architecture index for {repo_name or 'this repository'}.",
        generated=True,
        verified=False,
        tags=["wiki", "architecture"],
        body="\n".join(lines),
        x_codeharness={
            "kind": "wiki",
            "page": "architecture",
            "repo": repo_name,
            "citations": unique,
            "graph_hash": ghash,
            "focus": focus or "",
        },
    )
