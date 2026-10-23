"""Offline ticket/doc → code mentions. No Onyx source.

Mines ticket keys (ABC-123) and http(s) URLs from git commits, branch
names, and source comments. Edges are persisted under the external
namespace so a later ``index`` can re-apply them onto a rebuilt KG.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

TICKET_RE = re.compile(
    r"\b(?!(?:UTF|ISO|PEP|RFC|CSS|HTML|SHA|MD|UUID|CVE)-)"
    r"([A-Z]{2,10}-\d{1,7})\b"
)
URL_RE = re.compile(r"https?://[^\s)\]>'\"`,]+", re.IGNORECASE)
COMMENT_LINE = re.compile(r"^\s*(?:#|//|--)\s*(.*)$")
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# False-positive prefixes already excluded in TICKET_RE. Keep a second gate
# for keys that still look like encodings / RFCs after a looser scan.
_SKIP_KEYS = frozenset({"UTF-8", "UTF-16", "ISO-8601", "PEP-8"})

_CODE_EXT = (
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt",
)


def extract_ticket_keys(text: str) -> List[str]:
    if not text:
        return []
    seen = set()
    out: List[str] = []
    for match in TICKET_RE.finditer(text):
        key = match.group(1)
        if key in _SKIP_KEYS or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def extract_doc_urls(text: str) -> List[str]:
    if not text:
        return []
    seen = set()
    out: List[str] = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:")
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def ticket_node_id(key: str) -> str:
    return f"ticket:{key}"


def doc_node_id(url_or_id: str) -> str:
    return f"external:{url_or_id}"


def mentions_path(config, repo_path: str = "") -> str:
    from .store import external_dir

    return os.path.join(external_dir(config, repo_path), "mentions.jsonl")


@dataclass
class Mention:
    source_kind: str  # ticket | doc
    source_id: str
    target_path: str
    target_symbol: str = ""
    origin: str = ""  # commit | branch | comment
    origin_ref: str = ""
    snippet: str = ""
    metadata: Dict = field(default_factory=dict)

    def source_node(self) -> str:
        if self.source_kind == "ticket":
            return ticket_node_id(self.source_id)
        return doc_node_id(self.source_id)

    def to_record(self) -> Dict:
        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "target_path": self.target_path,
            "target_symbol": self.target_symbol,
            "origin": self.origin,
            "origin_ref": self.origin_ref,
            "snippet": self.snippet,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_record(cls, raw: Dict) -> "Mention":
        return cls(
            source_kind=str(raw.get("source_kind") or "ticket"),
            source_id=str(raw.get("source_id") or ""),
            target_path=str(raw.get("target_path") or ""),
            target_symbol=str(raw.get("target_symbol") or ""),
            origin=str(raw.get("origin") or ""),
            origin_ref=str(raw.get("origin_ref") or ""),
            snippet=str(raw.get("snippet") or ""),
            metadata=dict(raw.get("metadata") or {}),
        )


def mine_repo(repo_path: str, *, include_comments: bool = True) -> List[Mention]:
    """Collect ticket/doc mentions from git history, branches, and comments."""
    root = os.path.abspath(repo_path or ".")
    mentions: List[Mention] = []
    mentions.extend(_mine_git_commits(root))
    mentions.extend(_mine_git_branches(root))
    if include_comments:
        mentions.extend(_mine_comments(root))
    return _dedupe(mentions)


def persist_mentions(mentions: Sequence[Mention], config, repo_path: str = "") -> str:
    path = mentions_path(config, repo_path or getattr(config, "repo_path", "") or "")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    existing = { _mention_key(m): m for m in load_mentions(config, repo_path) }
    for mention in mentions:
        existing[_mention_key(mention)] = mention
    with open(path, "w") as fh:
        for mention in existing.values():
            fh.write(json.dumps(mention.to_record(), ensure_ascii=False) + "\n")
    return path


def load_mentions(config, repo_path: str = "") -> List[Mention]:
    path = mentions_path(config, repo_path or getattr(config, "repo_path", "") or "")
    if not os.path.isfile(path):
        return []
    out: List[Mention] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Mention.from_record(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return out


def apply_mentions(knowledge_graph, mentions: Sequence[Mention]) -> int:
    """Upsert ticket/doc nodes and ``mentions`` edges onto a KnowledgeGraph."""
    if knowledge_graph is None or not getattr(knowledge_graph, "enabled", True):
        return 0
    graph = knowledge_graph.graph
    added = 0
    path_index = _path_index(graph)
    for mention in mentions:
        if not mention.source_id or not mention.target_path:
            continue
        src = mention.source_node()
        if src not in graph:
            graph.add_node(
                src,
                name=mention.source_id,
                type="documentation",
                file_path="",
                start_line=0,
                end_line=0,
                repo_name=getattr(knowledge_graph, "repo_name", "") or "",
                kind="ticket" if mention.source_kind == "ticket" else "external_doc",
            )
        targets = _resolve_targets(graph, path_index, mention)
        for tgt in targets:
            if graph.has_edge(src, tgt) and (graph.edges[src, tgt].get("relationship") == "mentions"):
                continue
            graph.add_edge(
                src,
                tgt,
                relationship="mentions",
                origin=mention.origin,
                origin_ref=mention.origin_ref,
            )
            added += 1
    return added


def reapply_saved_mentions(knowledge_graph, config, repo_path: str = "") -> int:
    return apply_mentions(knowledge_graph, load_mentions(config, repo_path))


def linked_paths_for_ticket(knowledge_graph, key: str) -> List[str]:
    if knowledge_graph is None or not key:
        return []
    graph = knowledge_graph.graph
    nid = ticket_node_id(key)
    if nid not in graph:
        return []
    paths: List[str] = []
    seen = set()
    for _, tgt, data in graph.out_edges(nid, data=True):
        if (data or {}).get("relationship") != "mentions":
            continue
        path = (graph.nodes[tgt].get("file_path") or "").replace("\\", "/")
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def linked_entity_ids_for_ticket(knowledge_graph, key: str) -> List[str]:
    if knowledge_graph is None or not key:
        return []
    graph = knowledge_graph.graph
    nid = ticket_node_id(key)
    if nid not in graph:
        return []
    ids: List[str] = []
    for _, tgt, data in graph.out_edges(nid, data=True):
        if (data or {}).get("relationship") == "mentions":
            ids.append(tgt)
    return ids


def mine_fixtures(repo_path: str) -> List[Dict]:
    """Turn mined commits into eval fixtures (with-key and no-key variants)."""
    mentions = [m for m in mine_repo(repo_path) if m.origin == "commit" and m.source_kind == "ticket"]
    by_key: Dict[str, Dict] = {}
    for mention in mentions:
        row = by_key.setdefault(
            mention.source_id,
            {"key": mention.source_id, "paths": [], "subjects": []},
        )
        path = mention.target_path.replace("\\", "/")
        if path and path not in row["paths"]:
            row["paths"].append(path)
        subject = (mention.metadata or {}).get("subject") or mention.snippet
        if subject and subject not in row["subjects"]:
            row["subjects"].append(subject)
    fixtures: List[Dict] = []
    for key, row in by_key.items():
        if not row["paths"]:
            continue
        subject = row["subjects"][0] if row["subjects"] else key
        stripped = TICKET_RE.sub("", subject)
        stripped = re.sub(r"\s+", " ", stripped).strip(" -–:|,") or subject
        fixtures.append({
            "id": f"ticket-{key}-with-key",
            "query": f"{key} {stripped}".strip(),
            "must_cite_paths": list(row["paths"]),
            "relevant_chunk_ids": [],
            "difficulty": "easy",
        })
        fixtures.append({
            "id": f"ticket-{key}-no-key",
            "query": stripped,
            "must_cite_paths": list(row["paths"]),
            "relevant_chunk_ids": [],
            "difficulty": "hard",
        })
    return fixtures


def _mention_key(mention: Mention) -> Tuple[str, str, str, str]:
    return (
        mention.source_kind,
        mention.source_id,
        mention.target_path.replace("\\", "/"),
        mention.target_symbol,
    )


def _dedupe(mentions: Sequence[Mention]) -> List[Mention]:
    seen = set()
    out: List[Mention] = []
    for mention in mentions:
        key = _mention_key(mention)
        if key in seen or not mention.source_id:
            continue
        seen.add(key)
        out.append(mention)
    return out


def _mine_git_commits(root: str) -> List[Mention]:
    if not os.path.isdir(os.path.join(root, ".git")) and not _is_git_repo(root):
        return []
    proc = subprocess.run(
        [
            "git", "-C", root, "log", "--all", "--name-only", "--pretty=format:"
            "COMMIT %H%nSUBJECT %s%nBODY %b%nEND",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    mentions: List[Mention] = []
    sha = ""
    subject = ""
    body_parts: List[str] = []
    files: List[str] = []
    phase = "none"
    for raw in (proc.stdout or "").splitlines():
        line = raw.rstrip("\n")
        if line.startswith("COMMIT "):
            mentions.extend(_mentions_from_commit(sha, subject, "\n".join(body_parts), files))
            sha = line[7:].strip()
            subject = ""
            body_parts = []
            files = []
            phase = "subject"
            continue
        if line == "END":
            phase = "files"
            continue
        if line.startswith("SUBJECT "):
            subject = line[8:]
            phase = "body"
            continue
        if line.startswith("BODY "):
            body_parts.append(line[5:])
            phase = "body"
            continue
        if phase == "body":
            body_parts.append(line)
        elif phase == "files" and line.strip():
            files.append(line.strip())
    mentions.extend(_mentions_from_commit(sha, subject, "\n".join(body_parts), files))
    return mentions


def _mentions_from_commit(sha: str, subject: str, body: str, files: Sequence[str]) -> List[Mention]:
    if not sha:
        return []
    blob = f"{subject}\n{body}"
    keys = extract_ticket_keys(blob)
    urls = extract_doc_urls(blob)
    if not keys and not urls:
        return []
    code_files = [f for f in files if f and not f.startswith("COMMIT ")]
    if not code_files:
        code_files = [""]
    out: List[Mention] = []
    for path in code_files:
        for key in keys:
            out.append(
                Mention(
                    source_kind="ticket",
                    source_id=key,
                    target_path=path,
                    origin="commit",
                    origin_ref=sha,
                    snippet=subject,
                    metadata={"subject": subject},
                )
            )
        for url in urls:
            out.append(
                Mention(
                    source_kind="doc",
                    source_id=url,
                    target_path=path,
                    origin="commit",
                    origin_ref=sha,
                    snippet=subject,
                    metadata={"subject": subject},
                )
            )
    return out


def _mine_git_branches(root: str) -> List[Mention]:
    if not _is_git_repo(root):
        return []
    proc = subprocess.run(
        ["git", "-C", root, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    mentions: List[Mention] = []
    for line in (proc.stdout or "").splitlines():
        name = line.strip()
        if not name:
            continue
        for key in extract_ticket_keys(name):
            mentions.append(
                Mention(
                    source_kind="ticket",
                    source_id=key,
                    target_path="",
                    origin="branch",
                    origin_ref=name,
                    snippet=name,
                )
            )
        for url in extract_doc_urls(name):
            mentions.append(
                Mention(
                    source_kind="doc",
                    source_id=url,
                    target_path="",
                    origin="branch",
                    origin_ref=name,
                    snippet=name,
                )
            )
    return mentions


def _mine_comments(root: str) -> List[Mention]:
    mentions: List[Mention] = []
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".code-harness"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in _CODE_EXT:
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            for line_no, snippet in _comment_snippets(text):
                keys = extract_ticket_keys(snippet)
                urls = extract_doc_urls(snippet)
                if not keys and not urls:
                    continue
                symbol = _nearest_symbol(text, line_no)
                for key in keys:
                    mentions.append(
                        Mention(
                            source_kind="ticket",
                            source_id=key,
                            target_path=rel,
                            target_symbol=symbol,
                            origin="comment",
                            origin_ref=f"{rel}:{line_no}",
                            snippet=snippet.strip()[:200],
                        )
                    )
                for url in urls:
                    mentions.append(
                        Mention(
                            source_kind="doc",
                            source_id=url,
                            target_path=rel,
                            target_symbol=symbol,
                            origin="comment",
                            origin_ref=f"{rel}:{line_no}",
                            snippet=snippet.strip()[:200],
                        )
                    )
    return mentions


def _comment_snippets(text: str) -> List[Tuple[int, str]]:
    snippets: List[Tuple[int, str]] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        match = COMMENT_LINE.match(line)
        if match:
            snippets.append((idx, match.group(1)))
    for match in BLOCK_COMMENT.finditer(text):
        start = text[: match.start()].count("\n") + 1
        snippets.append((start, match.group(0)))
    return snippets


_DEF = re.compile(r"^\s*(?:def|class|function|fn|func)\s+([A-Za-z_][A-Za-z0-9_]*)")


def _nearest_symbol(text: str, line_no: int) -> str:
    lines = text.splitlines()
    for idx in range(min(line_no, len(lines)) - 1, -1, -1):
        match = _DEF.match(lines[idx])
        if match:
            return match.group(1)
    return ""


def _is_git_repo(root: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and (proc.stdout or "").strip() == "true"


def _path_index(graph) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = {}
    for nid, data in graph.nodes(data=True):
        path = (data.get("file_path") or "").replace("\\", "/")
        if path:
            index.setdefault(path, []).append(nid)
    return index


def _resolve_targets(graph, path_index: Dict[str, List[str]], mention: Mention) -> List[str]:
    path = (mention.target_path or "").replace("\\", "/")
    if not path:
        return []
    if mention.target_symbol:
        for nid in path_index.get(path, []):
            name = graph.nodes[nid].get("name") or ""
            if name == mention.target_symbol or name.endswith("." + mention.target_symbol):
                return [nid]
    file_id = f"file:{path}"
    if file_id in graph:
        return [file_id]
    if path in path_index:
        return [path_index[path][0]]
    graph.add_node(
        file_id,
        name=os.path.basename(path),
        type="file",
        file_path=path,
        start_line=1,
        end_line=1,
        repo_name="",
        kind="linked_file",
    )
    path_index.setdefault(path, []).append(file_id)
    return [file_id]


def iter_ticket_keys(items: Iterable[str]) -> Set[str]:
    keys: Set[str] = set()
    for item in items:
        keys.update(extract_ticket_keys(item or ""))
    return keys
