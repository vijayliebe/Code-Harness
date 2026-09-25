"""SQLite FTS5 over append-only session events (DeepSeek steal #5).

Sibling of BM25-over-memory (``memory search``). This indexes *session
events*, not the OKF vault. Local-first; no cloud deps.

Indexed types: ``user``, ``assistant``, ``tool_use``, ``tool_result``,
``system``, ``compact``. Skipped: ``meta``, ``clear``, ``verify``.

Search is available whenever a typed event log exists. Interactive
``/search`` and the CLI require ``--event-session`` (or a migrated event
file). Legacy ``event: turn`` JSONL is not indexed.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence


INDEXABLE_TYPES = frozenset(
    {"user", "assistant", "tool_use", "tool_result", "system", "compact"}
)
SKIP_TYPES = frozenset({"meta", "clear", "verify"})

_TOKEN = re.compile(r"[A-Za-z0-9_]+")
SNIPPET_WIDTH = 160


class SessionSearchError(ValueError):
    """Raised when search is asked to run without a typed event log."""


@dataclass(frozen=True)
class SessionHit:
    event_id: str
    type: str
    ts: str
    snippet: str
    score: float
    session_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "ts": self.ts,
            "snippet": self.snippet,
            "score": self.score,
            "session_id": self.session_id,
        }


def default_fts_path(jsonl_path: Optional[str]) -> Optional[str]:
    """Co-locate the sidecar next to the event JSONL."""
    if not jsonl_path:
        return None
    if jsonl_path.endswith(".jsonl"):
        return jsonl_path[: -len(".jsonl")] + ".fts.sqlite"
    return jsonl_path + ".fts.sqlite"


def indexable_body(event) -> str:
    """Plain text that should enter FTS. Empty means skip (meta noise)."""
    typ = str(getattr(event, "type", "") or "").strip().lower()
    if typ not in INDEXABLE_TYPES:
        return ""
    extra = getattr(event, "extra", None) or {}
    parts: List[str] = []
    if typ == "tool_use":
        parts.extend(
            [
                getattr(event, "tool_name", "") or "",
                getattr(event, "tool_args", "") or "",
                getattr(event, "text", "") or "",
            ]
        )
    elif typ == "tool_result":
        parts.extend(
            [
                getattr(event, "tool_name", "") or "",
                getattr(event, "text", "") or "",
            ]
        )
    else:
        parts.append(getattr(event, "text", "") or "")
        if typ == "compact":
            parts.append(str(extra.get("summary") or ""))
    return " ".join(part for part in parts if part).strip()


def _tokens(query: str) -> List[str]:
    return _TOKEN.findall(query or "")


def _fts_match(query: str) -> str:
    tokens = _tokens(query)
    if not tokens:
        return ""
    return " AND ".join('"' + tok.replace('"', "") + '"' for tok in tokens)


def _snippet(body: str, query: str, width: int = SNIPPET_WIDTH) -> str:
    text = " ".join((body or "").split())
    if not text:
        return ""
    lower = text.lower()
    pos = -1
    for tok in _tokens(query):
        pos = lower.find(tok.lower())
        if pos >= 0:
            break
    if pos < 0:
        pos = 0
    start = max(0, pos - 32)
    end = min(len(text), start + max(24, int(width or SNIPPET_WIDTH)))
    snippet = text[start:end].strip()
    if start:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def is_typed_event_log(path: str) -> bool:
    """True when the file already uses steal-#3 typed events (``v: 1``)."""
    if not path or not os.path.isfile(path):
        return False
    from .events import EVENT_SCHEMA_VERSION, KNOWN_TYPES

    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") in KNOWN_TYPES and row.get("v") == EVENT_SCHEMA_VERSION:
                    return True
                kind = row.get("event")
                if kind in ("session_start", "turn", "clear_tool_results"):
                    return False
    except OSError:
        return False
    return False


class SessionEventIndex:
    """SQLite FTS5 sidecar. ``path=None`` keeps the index in-memory."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        if path:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(path)
        else:
            self._conn = sqlite3.connect(":memory:")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5("
            " event_id UNINDEXED,"
            " type UNINDEXED,"
            " ts UNINDEXED,"
            " session_id UNINDEXED,"
            " body,"
            " tokenize='porter unicode61'"
            ")"
        )
        self._conn.commit()

    def _meta(self, key: str) -> str:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else ""

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (key, value),
        )

    def _fingerprint(self, source_path: Optional[str], events: Sequence[Any]) -> str:
        if source_path and os.path.isfile(source_path):
            stat = os.stat(source_path)
            return f"{stat.st_mtime_ns}:{stat.st_size}:{len(events)}"
        ids = ",".join(getattr(event, "id", "") or "" for event in events)
        return f"mem:{len(events)}:{ids}"

    def _indexed_ids(self) -> set:
        return {
            row[0]
            for row in self._conn.execute("SELECT event_id FROM events_fts")
            if row and row[0]
        }

    def _ensure_id(self, event, seq: int) -> str:
        event_id = getattr(event, "id", "") or ""
        if event_id:
            return event_id
        event_id = f"e{seq:04d}"
        try:
            event.id = event_id
        except Exception:
            pass
        return event_id

    def index_event(self, event, session_id: str = "") -> bool:
        body = indexable_body(event)
        event_id = getattr(event, "id", "") or ""
        if not body:
            return False
        if not event_id:
            event_id = self._ensure_id(event, 1)
            if not event_id:
                return False
        self._conn.execute("DELETE FROM events_fts WHERE event_id = ?", (event_id,))
        self._conn.execute(
            "INSERT INTO events_fts(event_id, type, ts, session_id, body) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                event_id,
                getattr(event, "type", "") or "",
                getattr(event, "ts", "") or "",
                session_id or "",
                body,
            ),
        )
        self._conn.commit()
        return True

    def invalidate(self, session_id: Optional[str] = None) -> None:
        if session_id:
            self._conn.execute(
                "DELETE FROM events_fts WHERE session_id = ?", (session_id,)
            )
        else:
            self._conn.execute("DELETE FROM events_fts")
        self._conn.execute("DELETE FROM meta")
        self._conn.commit()

    def rebuild(
        self,
        events: Sequence[Any],
        source_path: Optional[str] = None,
        session_id: str = "",
    ) -> int:
        self._conn.execute("DELETE FROM events_fts")
        added = 0
        for i, event in enumerate(events, 1):
            self._ensure_id(event, i)
            if self.index_event(event, session_id=session_id):
                added += 1
        self._set_meta("fingerprint", self._fingerprint(source_path, events))
        self._conn.commit()
        return added

    def sync(
        self,
        events: Sequence[Any],
        source_path: Optional[str] = None,
        session_id: str = "",
    ) -> int:
        rows = list(events or [])
        fingerprint = self._fingerprint(source_path, rows)
        if self._meta("fingerprint") == fingerprint:
            return 0
        for i, event in enumerate(rows, 1):
            self._ensure_id(event, i)
        incoming = [event for event in rows if indexable_body(event) and event.id]
        incoming_ids = {event.id for event in incoming}
        known = self._indexed_ids()
        if known and known <= incoming_ids:
            added = 0
            for event in incoming:
                if event.id not in known and self.index_event(event, session_id=session_id):
                    added += 1
            self._set_meta("fingerprint", fingerprint)
            self._conn.commit()
            return added
        if known == incoming_ids:
            self._set_meta("fingerprint", fingerprint)
            self._conn.commit()
            return 0
        return self.rebuild(rows, source_path=source_path, session_id=session_id)

    def search(self, query: str, top_k: int = 10) -> List[SessionHit]:
        match = _fts_match(query)
        if not match:
            return []
        cap = max(1, int(top_k or 10))
        try:
            rows = self._conn.execute(
                "SELECT event_id, type, ts, session_id, body, bm25(events_fts) AS rank "
                "FROM events_fts WHERE events_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, cap),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        hits: List[SessionHit] = []
        for event_id, typ, ts, session_id, body, rank in rows:
            score = -float(rank if rank is not None else 0.0)
            hits.append(
                SessionHit(
                    event_id=event_id,
                    type=typ,
                    ts=ts or "",
                    snippet=_snippet(body or "", query),
                    score=score,
                    session_id=session_id or "",
                )
            )
        return hits

    @classmethod
    def for_log(cls, path: str, session_id: str = "") -> "SessionEventIndex":
        from .events import EventLog

        index = cls(default_fts_path(path))
        log = EventLog.load(path)
        sid = session_id or os.path.splitext(os.path.basename(path))[0]
        index.sync(log.events, source_path=path, session_id=sid)
        return index


def search_session(
    query: str,
    *,
    events: Optional[Iterable[Any]] = None,
    path: Optional[str] = None,
    repo: Optional[str] = None,
    top_k: int = 10,
) -> Dict[str, Any]:
    """Ranked hits as JSON-ready dicts (CLI / MCP / agent tool)."""
    if events is not None:
        index = SessionEventIndex()
        index.sync(list(events))
        hits = index.search(query, top_k=top_k)
        return {"hits": [hit.to_dict() for hit in hits]}
    if not path:
        from .memory_extract import resolve_session_path

        path = resolve_session_path(repo or ".")
    if not path or not os.path.isfile(path):
        raise SessionSearchError(
            "no event log found. Start chat with --event-session "
            "(or pass --session <events.jsonl>)."
        )
    if not is_typed_event_log(path):
        raise SessionSearchError(
            "session search requires --event-session (typed event log). "
            "Legacy transcripts are not indexed."
        )
    hits = SessionEventIndex.for_log(path).search(query, top_k=top_k)
    return {"hits": [hit.to_dict() for hit in hits]}


def format_hits(hits: Sequence[Any]) -> str:
    lines: List[str] = []
    for raw in hits or []:
        hit = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
        score = hit.get("score")
        try:
            score_s = f"{float(score):.3f}"
        except (TypeError, ValueError):
            score_s = str(score or "")
        lines.append(
            f"{hit.get('event_id') or '?'}  {hit.get('type') or '?'}  "
            f"{hit.get('ts') or ''}  {score_s}\n  {hit.get('snippet') or ''}"
        )
    return "\n".join(lines)


SEARCH_SESSION_TOOL = {
    "name": "search_session",
    "description": (
        "Full-text search over the append-only session event log. "
        "Returns ranked hits with event ids, short snippets, and timestamps. "
        "Re-expand a hit via retrieve / retrieve_chunk when you need the body."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "session": {"type": "string"},
            "top_k": {"type": "integer"},
        },
        "required": ["query"],
    },
}
