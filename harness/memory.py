"""Typed project memory beside the code index (Memanto steal, OKF files).

Four kinds: decision / error / preference / fact. Files are OKF markdown
(SPEC v0.2) written under ``knowledge/memory/`` (wiki-vault style) and
optionally ``.code-harness/memory/``. Not written into Chroma.

Reconcile is supersession + tombstone, not silent overwrite. ``memory brief``
packs active entries with a hard cap of 800 tokens (``len // 4``).
LLM auto-extract from chats is out of scope — :func:`observe_stub` only.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .okf import (
    MEMORY_KINDS,
    OKF_VERSION,
    WikiPage,
    cite_path_symbol,
    default_local_memory_dir,
    default_memory_dir,
    dump_okf_markdown,
    estimate_tokens,
    is_memory_page,
    normalize_memory_kind,
    okf_type_for_kind,
    parse_okf_markdown,
    walk_okf_markdown,
)

BRIEF_TOKEN_CAP = 800
BRIEF_ITEM_CAP = 5
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_FORGOTTEN = "forgotten"
TYPE_PRIOR = {
    "decision": 1.0,
    "error": 0.9,
    "preference": 0.6,
    "fact": 0.5,
}

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class MemoryError(ValueError):
    """Invalid memory write (missing type/title/body, unknown kind, bad pointer)."""


@dataclass
class MemoryEntry:
    id: str
    kind: str
    title: str
    body: str
    timestamp: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    supersedes: Optional[str] = None
    status: str = STATUS_ACTIVE
    extras: Dict[str, Any] = field(default_factory=dict)
    x_codeharness: Dict[str, Any] = field(default_factory=dict)
    rel_path: str = ""
    generated: bool = False
    verified: Any = "human"
    okf_version: str = OKF_VERSION

    def to_page(self) -> WikiPage:
        bag = dict(self.x_codeharness or {})
        bag.update({
            "kind": "memory",
            "memory_kind": self.kind,
            "id": self.id,
            "status": self.status,
            "links": list(self.links),
        })
        if self.supersedes:
            bag["supersedes"] = self.supersedes
        extras = dict(self.extras)
        extras.pop("x_codeharness", None)
        return WikiPage(
            title=self.title,
            type=okf_type_for_kind(self.kind),
            description=self.description or _first_line(self.body),
            generated=bool(self.generated),
            verified=self.verified,
            timestamp=self.timestamp,
            tags=list(self.tags),
            okf_version=self.okf_version or OKF_VERSION,
            extras=extras,
            body=self.body,
            x_codeharness=bag,
            id=self.id,
            status=self.status,
            supersedes=self.supersedes,
        )


@dataclass
class MemoryBrief:
    text: str
    token_count: int
    entry_ids: List[str] = field(default_factory=list)
    skipped_ids: List[str] = field(default_factory=list)


class MemoryStore:
    def __init__(self, repo_path: str, memory_dir: Optional[str] = None):
        self.repo_path = os.path.abspath(repo_path or ".")
        self.memory_dir = os.path.abspath(
            memory_dir or default_memory_dir(self.repo_path)
        )
        self.local_dir = os.path.abspath(default_local_memory_dir(self.repo_path))

    @classmethod
    def for_repo(cls, repo_path: str, memory_dir: Optional[str] = None) -> "MemoryStore":
        return cls(repo_path, memory_dir=memory_dir)

    def add(
        self,
        kind: str,
        title: str,
        body: str,
        links: Optional[Sequence[str]] = None,
        tags: Optional[Sequence[str]] = None,
        supersedes: Optional[str] = None,
        timestamp: Optional[str] = None,
        entry_id: Optional[str] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> MemoryEntry:
        normalized = normalize_memory_kind(kind)
        if not normalized:
            raise MemoryError(
                f"type must be one of {', '.join(MEMORY_KINDS)} (got {kind!r})"
            )
        title = (title or "").strip()
        body = (body or "").strip()
        if not title:
            raise MemoryError("title is required")
        if not body:
            raise MemoryError("body is required")
        ts = _normalize_timestamp(timestamp)
        entry_id = (entry_id or "").strip() or make_memory_id(title, ts)
        links = [_normalize_link(link) for link in (links or []) if str(link).strip()]
        old = None
        if supersedes:
            old = self.get(supersedes)
            if old is None:
                raise MemoryError(f"unknown supersedes id: {supersedes}")
            supersedes = old.id
        entry = MemoryEntry(
            id=entry_id,
            kind=normalized,
            title=title,
            body=body,
            timestamp=ts,
            description=_first_line(body),
            tags=[str(t) for t in (tags or []) if str(t).strip()],
            links=links,
            supersedes=supersedes,
            status=STATUS_ACTIVE,
            extras=dict(extras or {}),
            rel_path=_rel_path_for(normalized, title, ts, entry_id),
        )
        self._write(entry)
        if old is not None:
            old.status = STATUS_SUPERSEDED
            self._write(old)
        return entry

    def get(self, key: str) -> Optional[MemoryEntry]:
        needle = (key or "").strip()
        if not needle:
            return None
        for entry in self._load_all():
            if _same_key(entry, needle):
                return entry
        return None

    def list(
        self,
        kind: Optional[str] = None,
        as_of: Optional[str] = None,
        include_inactive: bool = False,
    ) -> List[MemoryEntry]:
        wanted = normalize_memory_kind(kind) if kind else None
        cutoff = _parse_ts(as_of) if as_of else None
        entries = self._load_all()
        dated: List[MemoryEntry] = []
        for entry in entries:
            if wanted and entry.kind != wanted:
                continue
            if cutoff is not None:
                ts = _parse_ts(entry.timestamp)
                if ts is not None and ts > cutoff:
                    continue
            dated.append(entry)
        superseded_as_of = set()
        if cutoff is not None and not include_inactive:
            for entry in dated:
                if entry.supersedes and entry.status != STATUS_FORGOTTEN:
                    superseded_as_of.add(entry.supersedes)
        out = []
        for entry in dated:
            if include_inactive:
                out.append(entry)
                continue
            if entry.status == STATUS_FORGOTTEN:
                continue
            if cutoff is not None:
                if entry.id in superseded_as_of or entry.status == STATUS_SUPERSEDED:
                    if entry.id in superseded_as_of:
                        continue
                    # Keep an older row that is only marked superseded by a later file.
                    later = any(
                        other.supersedes == entry.id and _parse_ts(other.timestamp)
                        and _parse_ts(other.timestamp) <= cutoff
                        for other in dated
                    )
                    if later:
                        continue
                out.append(entry)
                continue
            if entry.status != STATUS_ACTIVE:
                continue
            out.append(entry)
        out.sort(key=lambda e: (_parse_ts(e.timestamp) or datetime.min, e.id), reverse=True)
        return out

    def brief(
        self,
        query: str = "",
        max_tokens: int = BRIEF_TOKEN_CAP,
        max_items: int = BRIEF_ITEM_CAP,
        redact: bool = False,
    ) -> MemoryBrief:
        cap = max(0, int(max_tokens or BRIEF_TOKEN_CAP))
        item_cap = max(0, int(max_items or BRIEF_ITEM_CAP))
        active = self.list(include_inactive=False)
        skipped = [e.id for e in self._load_all() if e.status != STATUS_ACTIVE]
        ranked = sorted(
            active,
            key=lambda e: (
                -_score(e, query),
                -(_parse_ts(e.timestamp) or datetime.min).timestamp(),
                e.id,
            ),
        )
        header = "## Project memory brief\n\n"
        assembled = header
        used: List[str] = []
        for entry in ranked:
            if len(used) >= item_cap:
                skipped.append(entry.id)
                continue
            block = _format_brief_item(entry)
            candidate = assembled + block
            tokens = estimate_tokens(candidate)
            if tokens > cap:
                if not used:
                    truncated = _truncate_to_tokens(header + block, cap)
                    if redact and truncated:
                        from .redact import redact_and_audit

                        truncated = redact_and_audit(
                            truncated, action="redact.memory",
                        ).text
                    return MemoryBrief(
                        text=truncated,
                        token_count=estimate_tokens(truncated),
                        entry_ids=[entry.id],
                        skipped_ids=skipped + [e.id for e in ranked[1:]],
                    )
                skipped.append(entry.id)
                continue
            assembled = candidate
            used.append(entry.id)
        text = assembled if used else ""
        if redact and text:
            from .redact import redact_and_audit

            text = redact_and_audit(text, action="redact.memory").text
        return MemoryBrief(
            text=text,
            token_count=estimate_tokens(text),
            entry_ids=used,
            skipped_ids=skipped,
        )

    def export_okf(self, dest: str, redact: bool = False) -> int:
        dest_root = Path(dest)
        dest_root.mkdir(parents=True, exist_ok=True)
        count = 0
        for entry in self._load_all():
            rel = entry.rel_path or _rel_path_for(entry.kind, entry.title, entry.timestamp, entry.id)
            target = dest_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            blob = dump_okf_markdown(entry.to_page())
            if redact:
                from .redact import redact_and_audit

                blob = redact_and_audit(blob, action="redact.memory").text
            target.write_text(blob, encoding="utf-8")
            count += 1
        return count

    def import_okf(self, src: str) -> int:
        if not src or not os.path.isdir(src):
            raise MemoryError(f"OKF bundle not found: {src}")
        count = 0
        for path, page in walk_okf_markdown(src):
            if not is_memory_page(page) and not page.title:
                continue
            if not is_memory_page(page):
                continue
            try:
                entry = self.entry_from_page(page, _guess_rel(src, path, page))
            except MemoryError:
                continue
            self._write(entry)
            count += 1
        return count

    def entry_from_page(self, page: WikiPage, rel_path: str = "") -> MemoryEntry:
        bag = dict(page.x_codeharness or {})
        kind = (
            normalize_memory_kind(page.type)
            or normalize_memory_kind(bag.get("memory_kind"))
            or normalize_memory_kind(bag.get("type"))
        )
        if not kind:
            raise MemoryError(f"not a typed memory concept: {page.type!r}")
        title = (page.title or "").strip()
        body = (page.body or "").strip()
        if not title:
            raise MemoryError("title is required")
        if not body:
            raise MemoryError("body is required")
        ts = _normalize_timestamp(page.timestamp)
        entry_id = (
            (page.id or bag.get("id") or make_memory_id(title, ts))
        )
        links = bag.get("links") or page.extras.get("links") or []
        if isinstance(links, str):
            links = [links]
        extras = dict(page.extras or {})
        return MemoryEntry(
            id=str(entry_id),
            kind=kind,
            title=title,
            body=page.body if page.body.endswith("\n") else (page.body or body),
            timestamp=ts,
            description=page.description or _first_line(body),
            tags=list(page.tags or []),
            links=[_normalize_link(x) for x in links if str(x).strip()],
            supersedes=page.supersedes or bag.get("supersedes") or None,
            status=str(page.status or bag.get("status") or STATUS_ACTIVE),
            extras=extras,
            x_codeharness=bag,
            rel_path=rel_path or _rel_path_for(kind, title, ts, str(entry_id)),
            generated=bool(page.generated),
            verified=page.verified if page.verified is not None else "human",
            okf_version=page.okf_version or OKF_VERSION,
        )

    def _write(self, entry: MemoryEntry) -> Path:
        os.makedirs(self.memory_dir, exist_ok=True)
        rel = entry.rel_path or _rel_path_for(entry.kind, entry.title, entry.timestamp, entry.id)
        entry.rel_path = rel
        dest = Path(self.memory_dir) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(dump_okf_markdown(entry.to_page()), encoding="utf-8")
        return dest

    def _roots(self) -> List[str]:
        roots = [self.memory_dir]
        if os.path.isdir(self.local_dir) and os.path.abspath(self.local_dir) != os.path.abspath(self.memory_dir):
            roots.append(self.local_dir)
        return roots

    def _load_all(self) -> List[MemoryEntry]:
        by_id: Dict[str, MemoryEntry] = {}
        for root in self._roots():
            if not os.path.isdir(root):
                continue
            for path, page in walk_okf_markdown(root):
                if not is_memory_page(page):
                    continue
                try:
                    entry = self.entry_from_page(page, _guess_rel(root, path, page))
                except MemoryError:
                    continue
                by_id[entry.id] = entry
        return list(by_id.values())


def observe_stub(*_args, **_kwargs) -> None:
    """Placeholder — LLM memory synthesis is fusion PR #3+ (out of scope)."""
    return None


def make_memory_id(title: str, timestamp: str) -> str:
    return f"mem/{_date_compact(timestamp)}-{_slug(title)}"


def _rel_path_for(kind: str, title: str, timestamp: str, entry_id: str) -> str:
    slug = _slug(title)
    date = _date_iso(timestamp)
    compact = date.replace("-", "")
    if entry_id and "/" in entry_id:
        rest = entry_id.split("/", 1)[1]
        if rest.startswith(compact + "-"):
            slug = rest[len(compact) + 1:] or slug
        elif rest.startswith(date + "-"):
            slug = rest[len(date) + 1:] or slug
        elif rest and not rest[0].isdigit():
            slug = rest
    slug = slug[:-3] if slug.endswith(".md") else slug
    return f"{kind}/{date}-{slug}.md"


def _slug(title: str, limit: int = 48) -> str:
    slug = _SLUG_RE.sub("-", (title or "").strip().lower()).strip("-")
    return (slug or "memory")[:limit].strip("-") or "memory"


def _date_iso(timestamp: str) -> str:
    ts = _parse_ts(timestamp)
    if ts:
        return ts.date().isoformat()
    return datetime.now(timezone.utc).date().isoformat()


def _date_compact(timestamp: str) -> str:
    return _date_iso(timestamp).replace("-", "")


def _normalize_timestamp(value: Optional[str]) -> str:
    ts = _parse_ts(value)
    if ts is None:
        ts = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text[:10])
        except ValueError:
            return None


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:220]
    return ""


def _normalize_link(link: str) -> str:
    raw = (link or "").replace("\\", "/").strip()
    if not raw:
        return ""
    if ":" in raw:
        path, symbol = raw.split(":", 1)
        return cite_path_symbol(path, symbol)
    return cite_path_symbol(raw, "")


def _same_key(entry: MemoryEntry, needle: str) -> bool:
    n = needle.replace("\\", "/").strip()
    if entry.id == n:
        return True
    if entry.rel_path.replace("\\", "/") == n.lstrip("./"):
        return True
    if Path(entry.rel_path).stem == Path(n).stem:
        return True
    if entry.id.endswith("/" + n) or n.endswith(entry.id):
        return True
    return False


def _guess_rel(root: str, path: str, page: WikiPage) -> str:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = os.path.basename(path)
    rel = rel.replace("\\", "/")
    if rel.startswith("../"):
        kind = normalize_memory_kind(page.type) or "fact"
        return f"{kind}/{os.path.basename(path)}"
    return rel


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _score(entry: MemoryEntry, query: str) -> float:
    prior = TYPE_PRIOR.get(entry.kind, 0.4)
    recency = _recency(entry.timestamp)
    if not (query or "").strip():
        return 0.7 * prior + 0.3 * recency
    q = set(_tokenize(query))
    if not q:
        return 0.7 * prior + 0.3 * recency
    tokens = set(_tokenize(f"{entry.title} {entry.body} {' '.join(entry.links)}"))
    overlap = len(q & tokens) / max(len(q), 1)
    return 0.5 * overlap + 0.3 * recency + 0.2 * prior


def _recency(timestamp: str) -> float:
    ts = _parse_ts(timestamp)
    if ts is None:
        return 0.5
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    days = max(0.0, (now - ts.astimezone(timezone.utc)).total_seconds() / 86400.0)
    return max(0.0, min(1.0, 1.0 - (days / 365.0)))


def _format_brief_item(entry: MemoryEntry) -> str:
    date = _date_iso(entry.timestamp)
    cites = " ".join(f"`{link}`" for link in entry.links)
    summary = entry.description or _first_line(entry.body)
    line = f"- **[{entry.kind}]** {entry.title} ({date})\n"
    extra = f"  {summary}"
    if cites:
        extra = f"{extra} {cites}"
    return line + extra.rstrip() + "\n"


def _truncate_to_tokens(text: str, cap: int) -> str:
    if estimate_tokens(text) <= cap:
        return text
    max_chars = max(0, cap * 4)
    clipped = (text or "")[:max_chars].rstrip()
    if not clipped.endswith("\n"):
        clipped += "\n"
    return clipped


# Re-export estimator so tests/CLI can share the ContextBuilder formula.
__all__ = [
    "BRIEF_ITEM_CAP",
    "BRIEF_TOKEN_CAP",
    "MEMORY_KINDS",
    "MemoryBrief",
    "MemoryEntry",
    "MemoryError",
    "MemoryStore",
    "estimate_tokens",
    "observe_stub",
]
