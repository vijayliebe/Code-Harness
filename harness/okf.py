"""OKF (Open Knowledge Format, Google SPEC v0.2) — local subset.

Open Knowledge Format is a Google Cloud specification
(https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md),
not a Memanto-owned format. Memanto is one implementer.

This module is a small, dependency-light producer/consumer for the
markdown + YAML frontmatter subset. Identity is the file path. Unknown
keys (including foreign ``x_*`` extensions) are preserved on round-trip.
Attested Computation is out of scope.

WikiPage field mapping (Code-Harness emit → SPEC v0.2)
-----------------------------------------------------
| Field            | SPEC role                         | We emit                          |
|------------------|-----------------------------------|----------------------------------|
| type             | required concept type             | ``WikiPage``                     |
| title            | human name                        | package / page title             |
| description      | short summary                     | stub from symbols / gloss        |
| generated        | provenance                        | ``true``                         |
| verified         | trust                             | ``false`` (template, no LLM)     |
| timestamp        | lifecycle (optional)              | omitted (keeps regen hash-stable)|
| tags             | optional labels                   | ``wiki``, page kind              |
| okf_version      | pin                               | ``"0.2"``                        |
| x_codeharness    | vendor bag (like ``x_memanto``)   | repo, kind, citations, graph_hash|
| body             | markdown after frontmatter        | sections + Mermaid + ``path:symbol`` cites |

Typed memory field mapping (same parser; no second schema)
----------------------------------------------------------
| Field            | SPEC role                         | We emit                          |
|------------------|-----------------------------------|----------------------------------|
| type             | required concept type             | ``Decision`` / ``Error`` / ``Preference`` / ``Fact`` |
| title            | human name                        | required                          |
| description      | short summary                     | first line of body when omitted   |
| generated        | provenance                        | ``false`` (human-written)         |
| verified         | trust                             | ``human``                         |
| timestamp        | lifecycle                         | ISO-8601                          |
| id               | extension (path is identity)      | ``mem/YYYYMMDD-slug``             |
| status           | lifecycle extension               | ``active`` / ``superseded`` / ``forgotten`` |
| supersedes       | reconcile pointer                 | prior ``id`` or relative path     |
| tags             | optional labels                   | caller-supplied                   |
| okf_version      | pin                               | ``"0.2"``                         |
| x_codeharness    | vendor bag                        | kind=memory, memory_kind, links, id, status, supersedes |
| body             | markdown after frontmatter        | prose + optional ``path:symbol`` cites |

CLI kinds are lowercase (``decision`` / ``error`` / ``preference`` / ``fact``);
the OKF ``type`` field is PascalCase to match ``WikiPage``. Unknown keys
(including foreign ``x_memanto`` / ``x_other``) stay in ``extras`` and survive
export → import. Path identity remains the file path; ``id`` is an extension
so ``supersedes`` can point at a stable name.

Full-vault interchange (same parser; no second schema)
------------------------------------------------------
``knowledge export`` copies every ``knowledge/**/*.md`` page plus local
overlays (``.code-harness/gloss``, optional ``.code-harness/memory`` /
``.code-harness/wiki``) into a bundle with ``okf-manifest.yaml``
(``okf_version``, ``generated``, per-kind counts). ``knowledge import``
writes those paths back. WikiPage / Decision / Error / Preference / Fact
mappings are reused for classification; gloss notes keep their ``entity:``
frontmatter. Unknown keys survive because files are copied, not rewritten.
``load_knowledge_docs`` is the prefix-load helper for retrieve/packer
(not wired into the packer yet).

Citations in the body use Code Wiki style ``path:symbol``
(e.g. ``harness/chunker.py:CodeChunker``), never chunk UUIDs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

OKF_VERSION = "0.2"
WIKI_PAGE_TYPE = "WikiPage"
MEMORY_KINDS = ("decision", "error", "preference", "fact")
MEMORY_OKF_TYPES = {kind: kind.capitalize() for kind in MEMORY_KINDS}

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

_KNOWN_FIELDS = (
    "type",
    "title",
    "description",
    "generated",
    "verified",
    "timestamp",
    "tags",
    "okf_version",
    "x_codeharness",
    "id",
    "status",
    "supersedes",
)

_SYMBOL_KINDS = frozenset({
    "class",
    "func",
    "function",
    "method",
    "endpoint",
    "var",
    "variable",
    "module",
})


class _Quoted(str):
    """Force double-quoted YAML scalars (okf_version must stay a string)."""


class _Dumper(yaml.SafeDumper):
    pass


def _quoted_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


_Dumper.add_representer(_Quoted, _quoted_representer)


def cite_path_symbol(file_path: str, symbol: str = "") -> str:
    """Return a Code Wiki citation: ``path:symbol``, or ``path`` when no symbol."""
    path = (file_path or "").replace("\\", "/").lstrip("./")
    symbol = (symbol or "").strip()
    if symbol.startswith(path + ":"):
        return symbol.replace("\\", "/")
    if not path:
        return symbol
    if not symbol:
        return path
    return f"{path}:{symbol}"


def cite_from_entity_id(entity_id: str, file_path: str = "", name: str = "") -> str:
    """Derive ``path:symbol`` from a KG entity id such as ``class:path:Name``."""
    eid = (entity_id or "").replace("\\", "/")
    path = (file_path or "").replace("\\", "/")
    symbol = (name or "").strip()
    parts = eid.split(":", 2)
    if len(parts) == 3 and parts[0] in _SYMBOL_KINDS:
        return cite_path_symbol(parts[1], parts[2])
    if len(parts) >= 2 and parts[0] == "file":
        return cite_path_symbol(":".join(parts[1:]), "")
    if len(parts) == 3 and parts[0] == "doc":
        return cite_path_symbol(parts[1], "")
    if path and symbol:
        return cite_path_symbol(path, symbol)
    return path or eid


def _as_version(value: Any) -> str:
    if value is None:
        return OKF_VERSION
    if isinstance(value, float) and value == 0.2:
        return "0.2"
    return str(value)


@dataclass
class WikiPage:
    """OKF-compatible concept (markdown+frontmatter or dict).

    Named WikiPage for the wiki MVP; the same record is the memory
    interchange type (``type: Decision`` / ``Error`` / …).
    """

    title: str
    type: str = WIKI_PAGE_TYPE
    description: str = ""
    generated: bool = True
    verified: Any = False
    timestamp: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    okf_version: str = OKF_VERSION
    extras: Dict[str, Any] = field(default_factory=dict)
    body: str = ""
    x_codeharness: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None
    status: Optional[str] = None
    supersedes: Optional[str] = None

    def to_frontmatter(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "type": self.type or WIKI_PAGE_TYPE,
            "title": self.title,
        }
        if self.description:
            data["description"] = self.description
        data["generated"] = bool(self.generated)
        data["verified"] = self.verified
        if self.timestamp:
            data["timestamp"] = self.timestamp
        if self.id:
            data["id"] = self.id
        if self.status:
            data["status"] = self.status
        if self.supersedes:
            data["supersedes"] = self.supersedes
        if self.tags:
            data["tags"] = list(self.tags)
        data["okf_version"] = _Quoted(_as_version(self.okf_version))
        extras = dict(self.extras)
        xch = extras.pop("x_codeharness", None)
        bag = dict(self.x_codeharness or xch or {})
        if bag:
            data["x_codeharness"] = bag
        for key in sorted(extras):
            data[key] = extras[key]
        return data

    def to_okf_dict(self) -> Dict[str, Any]:
        """JSON-shaped mapping of SPEC fields + body + extension bag."""
        data = self.to_frontmatter()
        data["okf_version"] = _as_version(data.get("okf_version"))
        data["body"] = self.body
        data.setdefault("x_codeharness", dict(self.x_codeharness))
        return data


def dump_okf_markdown(page: WikiPage) -> str:
    dumped = yaml.dump(
        page.to_frontmatter(),
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    body = page.body or ""
    if body.startswith("\n"):
        body = body.lstrip("\n")
    if body and not body.endswith("\n"):
        body += "\n"
    return f"---\n{dumped}---\n\n{body}"


def parse_okf_markdown(text: str) -> WikiPage:
    raw = text or ""
    match = FRONTMATTER_RE.search(raw)
    meta: Dict[str, Any] = {}
    body = raw
    if match:
        loaded = yaml.safe_load(match.group(1)) or {}
        if isinstance(loaded, dict):
            meta = loaded
        body = raw[match.end():]
    extras = {k: v for k, v in meta.items() if k not in _KNOWN_FIELDS}
    xch = meta.get("x_codeharness") if isinstance(meta.get("x_codeharness"), dict) else {}
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return WikiPage(
        title=str(meta.get("title") or ""),
        type=str(meta.get("type") or ""),
        description=str(meta.get("description") or ""),
        generated=bool(meta.get("generated", False)),
        verified=meta.get("verified", False),
        timestamp=str(meta["timestamp"]) if meta.get("timestamp") else None,
        tags=list(tags),
        okf_version=_as_version(meta.get("okf_version")),
        extras=extras,
        body=body,
        x_codeharness=dict(xch),
        id=str(meta["id"]) if meta.get("id") else None,
        status=str(meta["status"]) if meta.get("status") else None,
        supersedes=str(meta["supersedes"]) if meta.get("supersedes") else None,
    )


def default_wiki_dir(repo_path: str) -> str:
    return os.path.join(repo_path or ".", "knowledge", "wiki")


def is_wiki_path(file_path: str) -> bool:
    """True for vault pages under ``knowledge/wiki/`` (any repo-relative form)."""
    path = (file_path or "").replace("\\", "/").lstrip("./")
    if path.startswith("knowledge/wiki/"):
        return True
    return "/knowledge/wiki/" in f"/{path}"


def wiki_cite(page: str) -> str:
    """Citation form for a generated page: ``knowledge/wiki/<page>``."""
    name = (page or "").replace("\\", "/").lstrip("./")
    if name.startswith("knowledge/wiki/"):
        return name
    return f"knowledge/wiki/{os.path.basename(name) or 'page.md'}"


def default_memory_dir(repo_path: str) -> str:
    return os.path.join(repo_path or ".", "knowledge", "memory")


def default_local_memory_dir(repo_path: str) -> str:
    return os.path.join(repo_path or ".", ".code-harness", "memory")


def normalize_memory_kind(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if text in MEMORY_KINDS:
        return text
    return None


def okf_type_for_kind(kind: str) -> str:
    normalized = normalize_memory_kind(kind) or (kind or "").strip()
    return MEMORY_OKF_TYPES.get(normalized, normalized.capitalize() or "Fact")


def is_memory_page(page: WikiPage) -> bool:
    if normalize_memory_kind(page.type):
        return True
    bag = page.x_codeharness or {}
    if str(bag.get("kind") or "").lower() == "memory":
        return True
    if normalize_memory_kind(bag.get("memory_kind")):
        return True
    return False


def walk_okf_markdown(root: str) -> List[tuple]:
    """Yield ``(abs_path, WikiPage)`` for every ``*.md`` under *root*. Fail-soft."""
    results: List[tuple] = []
    if not root or not os.path.isdir(root):
        return results
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in {".git", "__pycache__", ".code-harness"}
        )
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    results.append((path, parse_okf_markdown(fh.read())))
            except OSError:
                continue
    return results


def estimate_tokens(text: str) -> int:
    """Same estimator as ``ContextBuilder`` (``len // 4``)."""
    return len(text or "") // 4


MANIFEST_NAME = "okf-manifest.yaml"
VAULT_KIND_WIKI = "wiki"
VAULT_KIND_MEMORY = "memory"
VAULT_KIND_GLOSS = "gloss"
VAULT_KIND_OTHER = "other"
VAULT_KINDS = (VAULT_KIND_WIKI, VAULT_KIND_MEMORY, VAULT_KIND_GLOSS, VAULT_KIND_OTHER)
_MANIFEST_NAMES = frozenset({MANIFEST_NAME, "manifest.yaml", "manifest.yml", "manifest.json"})
_SKIP_WALK_DIRS = frozenset({".git", "__pycache__"})


class VaultError(ValueError):
    """Invalid vault bundle (missing path, not a directory)."""


@dataclass
class KnowledgeDoc:
    """One vault markdown file for export, import, or prefix-load."""

    rel_path: str
    text: str
    page: WikiPage
    kind: str


@dataclass
class VaultManifest:
    okf_version: str = OKF_VERSION
    generated: bool = True
    generated_at: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "okf_version": _Quoted(_as_version(self.okf_version)),
            "generated": bool(self.generated),
            "generated_at": self.generated_at,
            "counts": dict(self.counts),
            "files": list(self.files),
        }


def default_gloss_dir(repo_path: str) -> str:
    return os.path.join(repo_path or ".", "knowledge", "gloss")


def default_local_gloss_dir(repo_path: str) -> str:
    return os.path.join(repo_path or ".", ".code-harness", "gloss")


def default_okf_bundle_dir(repo_path: str) -> str:
    return os.path.join(repo_path or ".", ".code-harness", "okf-bundle")


def _norm_relpath(rel_path: str) -> str:
    """Normalize a repo-relative path without stripping hidden-dir dots."""
    rel = (rel_path or "").replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lstrip("/")


def classify_vault_kind(rel_path: str, page: Optional[WikiPage] = None) -> str:
    """Classify a vault file by path first, then OKF type."""
    norm = _norm_relpath(rel_path)
    if "/wiki/" in f"/{norm}" or norm.startswith("wiki/") or norm == "wiki":
        return VAULT_KIND_WIKI
    if "/memory/" in f"/{norm}" or norm.startswith("memory/") or norm == "memory":
        return VAULT_KIND_MEMORY
    if "/gloss/" in f"/{norm}" or norm.startswith("gloss/") or norm == "gloss":
        return VAULT_KIND_GLOSS
    if page is not None:
        if is_memory_page(page):
            return VAULT_KIND_MEMORY
        if (page.type or "") == WIKI_PAGE_TYPE:
            return VAULT_KIND_WIKI
    return VAULT_KIND_OTHER


def _vault_source_roots(repo_path: str) -> List[Tuple[str, str]]:
    """Return ``(abs_root, repo_rel_prefix)`` pairs that exist on disk."""
    root = os.path.abspath(repo_path or ".")
    candidates = [
        (os.path.join(root, "knowledge"), "knowledge"),
        (os.path.join(root, ".code-harness", "gloss"), ".code-harness/gloss"),
        (os.path.join(root, ".code-harness", "memory"), ".code-harness/memory"),
        (os.path.join(root, ".code-harness", "wiki"), ".code-harness/wiki"),
    ]
    return [(abs_root, prefix) for abs_root, prefix in candidates if os.path.isdir(abs_root)]


def _is_manifest_name(name: str) -> bool:
    return os.path.basename(name or "").lower() in _MANIFEST_NAMES


def iter_vault_markdown(repo_path: str) -> List[Tuple[str, str, str]]:
    """Yield ``(rel_path, abs_path, text)`` for vault markdown. Fail-soft."""
    seen: set = set()
    results: List[Tuple[str, str, str]] = []
    for abs_root, prefix in _vault_source_roots(repo_path):
        for dirpath, dirnames, filenames in os.walk(abs_root):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_WALK_DIRS)
            for name in sorted(filenames):
                if not name.endswith(".md") or _is_manifest_name(name):
                    continue
                abs_path = os.path.abspath(os.path.join(dirpath, name))
                if abs_path in seen:
                    continue
                seen.add(abs_path)
                try:
                    rel_inside = os.path.relpath(abs_path, abs_root).replace("\\", "/")
                except ValueError:
                    rel_inside = name
                rel_path = prefix if rel_inside in {".", ""} else f"{prefix}/{rel_inside}"
                try:
                    with open(abs_path, encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    continue
                results.append((rel_path.replace("\\", "/"), abs_path, text))
    results.sort(key=lambda item: item[0])
    return results


def load_knowledge_docs(repo_path: str) -> List[KnowledgeDoc]:
    """Prefix-load ``knowledge/**/*.md`` plus local gloss/memory/wiki overlays.

    Fail-soft. Intended for retrieve/packer later; not injected by default.
    """
    docs: List[KnowledgeDoc] = []
    for rel_path, _abs_path, text in iter_vault_markdown(repo_path):
        page = parse_okf_markdown(text)
        docs.append(
            KnowledgeDoc(
                rel_path=rel_path,
                text=text,
                page=page,
                kind=classify_vault_kind(rel_path, page),
            )
        )
    return docs


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_counts() -> Dict[str, int]:
    counts = {kind: 0 for kind in VAULT_KINDS}
    counts["total"] = 0
    return counts


def _manifest_from_docs(
    docs: List[KnowledgeDoc], generated_at: Optional[str] = None
) -> VaultManifest:
    counts = _empty_counts()
    files: List[str] = []
    for doc in docs:
        kind = doc.kind if doc.kind in counts else VAULT_KIND_OTHER
        counts[kind] = counts.get(kind, 0) + 1
        counts["total"] += 1
        files.append(doc.rel_path.replace("\\", "/"))
    return VaultManifest(
        okf_version=OKF_VERSION,
        generated=True,
        generated_at=generated_at or _now_utc(),
        counts=counts,
        files=files,
    )


def dump_vault_manifest(manifest: VaultManifest) -> str:
    return yaml.dump(
        manifest.to_dict(),
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def parse_vault_manifest(text: str) -> VaultManifest:
    loaded = yaml.safe_load(text or "") or {}
    if not isinstance(loaded, dict):
        loaded = {}
    raw_counts = loaded.get("counts") or {}
    counts = _empty_counts()
    if isinstance(raw_counts, dict):
        for key, value in raw_counts.items():
            try:
                counts[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
    files = loaded.get("files") or []
    if isinstance(files, str):
        files = [files]
    return VaultManifest(
        okf_version=_as_version(loaded.get("okf_version")),
        generated=bool(loaded.get("generated", True)),
        generated_at=str(loaded.get("generated_at") or ""),
        counts=counts,
        files=[str(item).replace("\\", "/") for item in files],
    )


def _manifest_path(bundle: str) -> str:
    root = bundle or ""
    for name in (MANIFEST_NAME, "manifest.yaml", "manifest.yml", "manifest.json"):
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(root, MANIFEST_NAME)


def read_vault_manifest(bundle: str) -> VaultManifest:
    path = _manifest_path(bundle)
    if not os.path.isfile(path):
        return VaultManifest(counts=_empty_counts())
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return parse_vault_manifest(fh.read())
    except OSError:
        return VaultManifest(counts=_empty_counts())


def export_vault(repo_path: str, dest: str, redact: bool = False) -> VaultManifest:
    """Copy the knowledge vault into *dest* plus an OKF manifest. Fail-soft."""
    docs = load_knowledge_docs(repo_path)
    dest_root = Path(dest)
    dest_root.mkdir(parents=True, exist_ok=True)
    written: List[KnowledgeDoc] = []
    for doc in docs:
        rel = _norm_relpath(doc.rel_path)
        if not rel:
            continue
        target = dest_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = doc.text
        if redact and blob:
            from .redact import redact_and_audit

            blob = redact_and_audit(blob, action="redact.knowledge").text
        target.write_text(blob, encoding="utf-8")
        written.append(
            KnowledgeDoc(rel_path=rel, text=blob, page=doc.page, kind=doc.kind)
        )
    manifest = _manifest_from_docs(written)
    (dest_root / MANIFEST_NAME).write_text(dump_vault_manifest(manifest), encoding="utf-8")
    return manifest


def _import_dest_rel(rel: str) -> str:
    rel = _norm_relpath(rel)
    if not rel or _is_manifest_name(rel):
        return ""
    if rel.startswith("knowledge/") or rel.startswith(".code-harness/"):
        return rel
    first = rel.split("/", 1)[0]
    if first in (VAULT_KIND_WIKI, VAULT_KIND_MEMORY, VAULT_KIND_GLOSS):
        return f"knowledge/{rel}"
    if first in MEMORY_KINDS:
        return f"knowledge/memory/{rel}"
    return rel


def import_vault(bundle: str, dest_repo: str) -> VaultManifest:
    """Copy an OKF vault bundle onto *dest_repo*. Fail-soft for empty bundles."""
    if not bundle or not os.path.isdir(bundle):
        raise VaultError(f"OKF bundle not found: {bundle}")
    dest = os.path.abspath(dest_repo or ".")
    os.makedirs(dest, exist_ok=True)
    written: List[KnowledgeDoc] = []
    for dirpath, dirnames, filenames in os.walk(bundle):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_WALK_DIRS)
        for name in sorted(filenames):
            if not name.endswith(".md") or _is_manifest_name(name):
                continue
            abs_path = os.path.join(dirpath, name)
            try:
                rel = os.path.relpath(abs_path, bundle).replace("\\", "/")
            except ValueError:
                rel = name
            dest_rel = _import_dest_rel(rel)
            if not dest_rel:
                continue
            try:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            target = Path(dest) / dest_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            page = parse_okf_markdown(text)
            written.append(
                KnowledgeDoc(
                    rel_path=dest_rel,
                    text=text,
                    page=page,
                    kind=classify_vault_kind(dest_rel, page),
                )
            )
    return _manifest_from_docs(written)
