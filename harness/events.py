"""Append-only session events + ``derive_messages`` (DeepSeek steal #3).

A session is an append-only log of typed ``SessionEvent``s. Model-facing
history is a *projection* of the current surface, not a second mutable list.

Surface (become model history): ``user``, ``assistant``, ``tool_use``,
``tool_result``, ``system``.

Log-only (stay in the raw log; compact/clear *affect* the surface):
``compact``, ``clear``, ``verify``, ``meta``.

Compaction shadows a balanced range of surface ids and increments
``replace_generation``. Clear events rewrite *projection* of named
``tool_result`` ids to placeholders. Shadowed / original dump bytes stay
on disk so resume is deterministic.

This is a Code-Harness seam, not a Cordis / ``@deepseek-ai/*`` vendor.
Do not copy DeepSeek's 13-type TypeScript envelope wholesale.

Session-event FTS (steal #5) lives in ``session_fts.py``: ``EventLog.append``
optionally indexes into a co-located SQLite FTS5 sidecar.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


_FALSEY = frozenset({"0", "false", "off", "no"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

EVENT_SCHEMA_VERSION = 1

SURFACE_TYPES = frozenset({"user", "assistant", "tool_use", "tool_result", "system"})
LOG_ONLY_TYPES = frozenset({"compact", "clear", "verify", "meta"})
KNOWN_TYPES = SURFACE_TYPES | LOG_ONLY_TYPES

# User-facing type names (steal #3). DeepSeek uses user/message etc.; we keep
# a short local vocabulary so existing turn JSONL can be migrated.
MESSAGE_TYPES = SURFACE_TYPES


def event_session_enabled(
    args=None,
    environ=None,
    config=None,
) -> bool:
    """Interactive default **off**. ``--no-event-session`` / env ``0`` wins."""
    env = environ if environ is not None else os.environ
    raw = str(env.get("CODEHARNESS_EVENT_SESSION") or "").strip().lower()
    if args is not None and getattr(args, "no_event_session", False):
        return False
    if raw in _FALSEY:
        return False
    if args is not None and getattr(args, "event_session", False):
        return True
    if raw in _TRUTHY:
        return True
    if config is not None:
        session = getattr(config, "session", None) or {}
        if "event_session" in session:
            return bool(session.get("event_session"))
    return False


def apply_event_session_config(config, args=None, environ=None):
    """Stamp resolved knobs. Event-session implies prefix-stable unless disabled."""
    session = getattr(config, "session", None)
    if session is None:
        config.session = {}
        session = config.session
    on = event_session_enabled(args, environ=environ, config=config)
    session["event_session"] = on
    context = getattr(config, "context", None)
    if context is None:
        config.context = {}
        context = config.context
    from .prefix import prefix_stable_enabled

    context["prefix_stable"] = prefix_stable_enabled(
        args, environ=environ, config=config, event_session=on
    )
    return config


def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _id_seq(event_id: str) -> int:
    raw = str(event_id or "")
    if raw.startswith("e") and raw[1:].isdigit():
        return int(raw[1:])
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else 0


@dataclass
class SessionEvent:
    type: str
    id: str = ""
    ts: str = ""
    text: str = ""
    kind: str = ""
    chunk_ids: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)
    tool_name: str = ""
    tool_args: str = ""
    tool_use_id: str = ""
    packed_tokens: int = 0
    full_tokens: int = 0
    completion_tokens: int = 0
    loop_attempts: int = 0
    pack_mode: str = ""
    replace_ids: List[str] = field(default_factory=list)
    replace_generation: int = 0
    placeholders: Dict[str, str] = field(default_factory=dict)
    cleared_ids: List[str] = field(default_factory=list)
    tokens_freed: int = 0
    kept: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)
    v: int = EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.type = str(self.type or "").strip().lower()
        if self.type not in KNOWN_TYPES:
            raise ValueError(
                f"unknown session event type {self.type!r}; "
                f"expected one of {sorted(KNOWN_TYPES)}"
            )
        self.ts = self.ts or _now_ts()
        self.chunk_ids = list(self.chunk_ids or [])
        self.paths = list(self.paths or [])
        self.replace_ids = list(self.replace_ids or [])
        self.cleared_ids = list(self.cleared_ids or [])
        self.placeholders = dict(self.placeholders or {})
        self.extra = dict(self.extra or {})

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra", {}) or {}
        # Drop empty optional fields so the JSONL stays readable.
        slim = {k: v for k, v in data.items() if v or k in ("type", "id", "v")}
        slim.update(extra)
        return slim

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SessionEvent":
        data = dict(raw or {})
        known = {f.name for f in cls.__dataclass_fields__.values()}
        extra = {k: v for k, v in data.items() if k not in known and k != "event"}
        typ = data.get("type") or data.get("event") or ""
        if typ in ("session_start", "turn", "profile", "done", "clear_tool_results"):
            # Caller should use migrate_legacy_events for mixed turn files.
            typ = "meta" if typ != "turn" else "meta"
        kwargs = {k: data[k] for k in known if k in data and k != "extra"}
        kwargs["type"] = data.get("type") or typ
        kwargs["extra"] = extra
        return cls(**kwargs)


@dataclass(frozen=True)
class ModelMessage:
    """Frozen model-facing projection node. Mutating this is unrepresentable."""

    role: str
    text: str = ""
    event_id: str = ""
    kind: str = ""
    chunk_ids: Tuple[str, ...] = ()
    tool_name: str = ""
    tool_args: str = ""
    tool_use_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "event_id": self.event_id,
            "kind": self.kind,
            "chunk_ids": list(self.chunk_ids),
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_use_id": self.tool_use_id,
        }


def make_event(type: str, **fields) -> SessionEvent:
    known = {f.name for f in SessionEvent.__dataclass_fields__.values()}
    extra = dict(fields.pop("extra", None) or {})
    unknown = {k: fields.pop(k) for k in list(fields) if k not in known}
    extra.update(unknown)
    return SessionEvent(type=type, extra=extra, **fields)


def _shadowed_ids(events: Sequence[SessionEvent]) -> set:
    shadowed = set()
    for event in events:
        if event.type == "compact":
            for eid in event.replace_ids:
                if eid:
                    shadowed.add(eid)
    return shadowed


def _placeholder_map(events: Sequence[SessionEvent]) -> Dict[str, str]:
    placeholders: Dict[str, str] = {}
    for event in events:
        if event.type != "clear":
            continue
        for eid, stub in (event.placeholders or {}).items():
            if eid:
                placeholders[str(eid)] = stub
        for eid in event.cleared_ids or []:
            placeholders.setdefault(str(eid), event.text or "")
    return placeholders


def derive_messages(events: Sequence[SessionEvent]) -> List[ModelMessage]:
    """Project the current surface. Compact/clear are log records, not a 2nd list."""
    shadowed = _shadowed_ids(events)
    placeholders = _placeholder_map(events)
    latest_system: Optional[SessionEvent] = None
    ordered: List[SessionEvent] = []
    for event in events:
        if event.type == "system":
            if event.id in shadowed:
                continue
            latest_system = event
            continue
        if event.type == "compact":
            if event.id not in shadowed:
                ordered.append(event)
            continue
        if event.type in LOG_ONLY_TYPES:
            continue
        if event.id in shadowed:
            continue
        ordered.append(event)
    if latest_system is not None:
        ordered.insert(0, latest_system)

    messages: List[ModelMessage] = []
    for event in ordered:
        text = event.text or ""
        kind = event.kind or event.type
        if event.type == "compact":
            text = event.text or event.extra.get("summary") or text
            kind = "compact"
            role = "system"
        elif event.type == "tool_result" and event.id in placeholders:
            text = placeholders[event.id]
            kind = "cleared"
            role = event.type
        else:
            role = event.type
        messages.append(
            ModelMessage(
                role=role,
                text=text,
                event_id=event.id,
                kind=kind,
                chunk_ids=tuple(event.chunk_ids or ()),
                tool_name=event.tool_name,
                tool_args=event.tool_args,
                tool_use_id=event.tool_use_id,
            )
        )
    return messages


def render_messages(messages: Sequence[ModelMessage]) -> str:
    lines: List[str] = []
    for msg in messages:
        label = msg.role.replace("_", " ").capitalize()
        ids = f"  pack={','.join(msg.chunk_ids)}" if msg.chunk_ids else ""
        body = msg.text or ""
        if msg.role == "tool_use" and msg.tool_name:
            args = f" {msg.tool_args}" if msg.tool_args else ""
            body = f"{msg.tool_name}{args}".strip() if not body else body
        lines.append(f"{label}: {body}{ids}".rstrip())
    return "\n".join(lines).strip()


def derive_turns(events: Sequence[SessionEvent]):
    """Collapse the surface into ``SessionTurn`` rows for budget / compact UX."""
    from .session import SessionTurn

    messages = derive_messages(events)
    turns: List[Any] = []
    pending_use: Optional[ModelMessage] = None
    pending_result: Optional[ModelMessage] = None
    for msg in messages:
        if msg.role == "user":
            if pending_result is not None:
                turns.append(_turn_from_tool(pending_use, pending_result))
                pending_use = pending_result = None
            turns.append(
                SessionTurn(
                    role="user",
                    text=msg.text,
                    chunk_ids=list(msg.chunk_ids),
                    event_id=msg.event_id,
                )
            )
        elif msg.role == "tool_use":
            pending_use = msg
        elif msg.role == "tool_result":
            pending_result = msg
        elif msg.role == "assistant":
            turn = SessionTurn(
                role="assistant",
                text=msg.text,
                chunk_ids=list(msg.chunk_ids or (pending_result.chunk_ids if pending_result else ())),
                tool_name=(pending_use.tool_name if pending_use else "") or "",
                tool_args=(pending_use.tool_args if pending_use else "") or "",
                tool_result=(pending_result.text if pending_result else "") or "",
                event_id=(pending_result.event_id if pending_result else msg.event_id),
                cleared=bool(pending_result and pending_result.kind == "cleared"),
            )
            turns.append(turn)
            pending_use = pending_result = None
        elif msg.kind == "compact" or (msg.role == "system" and msg.kind == "compact"):
            turns.append(
                SessionTurn(
                    role="compact",
                    text=msg.text,
                    chunk_ids=list(msg.chunk_ids),
                    event_id=msg.event_id,
                )
            )
        elif msg.role == "system":
            continue
    if pending_result is not None:
        turns.append(_turn_from_tool(pending_use, pending_result))
    return turns


def _turn_from_tool(use: Optional[ModelMessage], result: ModelMessage):
    from .session import SessionTurn

    return SessionTurn(
        role="assistant",
        text=result.text,
        chunk_ids=list(result.chunk_ids),
        tool_name=(use.tool_name if use else "") or "retrieve",
        tool_args=(use.tool_args if use else "") or "",
        tool_result=result.text,
        event_id=result.event_id,
        cleared=result.kind == "cleared",
    )


class EventLog:
    """JSONL-backed append-only log. Projection is cached until the next append."""

    def __init__(self, path: Optional[str] = None, fts=None):
        self.path = path
        self.fts = fts
        self.events: List[SessionEvent] = []
        self._seq = 0
        self._generation = 0
        self._derive_cache: Optional[Tuple[ModelMessage, ...]] = None
        self._derive_len = -1

    def next_id(self) -> str:
        self._seq += 1
        return f"e{self._seq:04d}"

    def append(self, event: SessionEvent) -> SessionEvent:
        if not event.id:
            event.id = self.next_id()
        else:
            self._seq = max(self._seq, _id_seq(event.id))
        if not event.ts:
            event.ts = _now_ts()
        if event.type == "compact":
            self._generation += 1
            if not event.replace_generation:
                event.replace_generation = self._generation
        self.events.append(event)
        self._derive_cache = None
        if self.path:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        if self.fts is not None:
            self.fts.index_event(event)
        return event

    def derive_messages(self) -> List[ModelMessage]:
        if self._derive_cache is not None and self._derive_len == len(self.events):
            return list(self._derive_cache)
        msgs = tuple(derive_messages(self.events))
        self._derive_cache = msgs
        self._derive_len = len(self.events)
        return list(msgs)

    @classmethod
    def load(cls, path: str) -> "EventLog":
        log = cls(path=path)
        if not path or not os.path.isfile(path):
            return log
        rows: List[Dict[str, Any]] = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if _looks_legacy(rows):
            events = migrate_legacy_events(rows)
        else:
            events = [_event_from_row(row) for row in rows]
        for event in events:
            if not event.id:
                event.id = log.next_id()
            else:
                log._seq = max(log._seq, _id_seq(event.id))
            if event.type == "compact":
                log._generation = max(log._generation, int(event.replace_generation or 0))
            log.events.append(event)
        if log.fts is not None:
            log.fts.sync(log.events, source_path=path)
        return log


def _looks_legacy(rows: Sequence[Dict[str, Any]]) -> bool:
    if not rows:
        return False
    for row in rows:
        if row.get("type") in KNOWN_TYPES and row.get("v") == EVENT_SCHEMA_VERSION:
            return False
        kind = row.get("event")
        if kind in ("session_start", "turn", "compact", "clear_tool_results"):
            return True
    return False


def _event_from_row(row: Dict[str, Any]) -> SessionEvent:
    data = dict(row)
    if data.get("type") in KNOWN_TYPES:
        return SessionEvent.from_dict(data)
    return migrate_legacy_events([data])[0]


def migrate_legacy_events(rows: Iterable[Dict[str, Any]]) -> List[SessionEvent]:
    """Best-effort reader for mixed ``event: turn`` JSONL (keep old files readable)."""
    events: List[SessionEvent] = []
    seq = 0

    def _next() -> str:
        nonlocal seq
        seq += 1
        return f"e{seq:04d}"

    surface_ids: List[str] = []
    for row in rows:
        kind = row.get("type") or row.get("event") or ""
        if kind == "session_start":
            events.append(
                SessionEvent(
                    type="meta",
                    id=_next(),
                    kind="session_start",
                    extra={
                        "repo": row.get("repo", ""),
                        "profile": row.get("profile", ""),
                        "session_id": row.get("id", ""),
                    },
                )
            )
            continue
        if kind == "turn":
            role = str(row.get("role") or "assistant")
            if role == "user":
                ev = SessionEvent(
                    type="user",
                    id=_next(),
                    text=row.get("text") or "",
                    chunk_ids=list(row.get("chunk_ids") or []),
                )
                events.append(ev)
                surface_ids.append(ev.id)
                continue
            if role == "compact":
                ev = SessionEvent(
                    type="compact",
                    id=_next(),
                    text=row.get("text") or "",
                    chunk_ids=list(row.get("chunk_ids") or []),
                    kind="compact",
                )
                events.append(ev)
                continue
            tool_name = row.get("tool_name") or ""
            tool_result = row.get("tool_result") or ""
            chunk_ids = list(row.get("chunk_ids") or [])
            if tool_name or tool_result or chunk_ids:
                use = SessionEvent(
                    type="tool_use",
                    id=_next(),
                    tool_name=tool_name or "retrieve",
                    tool_args=row.get("tool_args") or "",
                    chunk_ids=chunk_ids,
                    paths=list(row.get("paths") or []),
                )
                events.append(use)
                surface_ids.append(use.id)
                result = SessionEvent(
                    type="tool_result",
                    id=_next(),
                    text=tool_result or "",
                    tool_use_id=use.id,
                    chunk_ids=chunk_ids,
                    paths=list(row.get("paths") or []),
                    tool_name=tool_name or "retrieve",
                )
                events.append(result)
                surface_ids.append(result.id)
            asst = SessionEvent(
                type="assistant",
                id=_next(),
                text=row.get("text") or "",
                chunk_ids=chunk_ids,
                packed_tokens=int(row.get("packed_tokens") or 0),
                full_tokens=int(row.get("full_tokens") or 0),
                completion_tokens=int(row.get("completion_tokens") or 0),
                loop_attempts=int(row.get("loop_attempts") or 0),
                pack_mode=row.get("pack_mode") or "",
            )
            events.append(asst)
            surface_ids.append(asst.id)
            continue
        if kind == "compact":
            keep = max(1, int(row.get("kept") or 1))
            # Heuristic: shadow older surface nodes; keep roughly the last pair.
            keep_surface = min(len(surface_ids), max(2, keep))
            replace_ids = list(surface_ids[:-keep_surface]) if keep_surface else list(surface_ids)
            ev = SessionEvent(
                type="compact",
                id=_next(),
                text=row.get("summary") or row.get("text") or "",
                replace_ids=replace_ids,
                chunk_ids=list(row.get("latest_pack") or []),
                kind="compact",
                extra={"dropped": row.get("dropped"), "kept": row.get("kept")},
            )
            events.append(ev)
            surface_ids = [eid for eid in surface_ids if eid not in set(replace_ids)]
            continue
        if kind in ("clear_tool_results", "clear"):
            events.append(
                SessionEvent(
                    type="clear",
                    id=_next(),
                    tokens_freed=int(row.get("tokens_freed") or 0),
                    kept=int(row.get("kept") or 0),
                    extra={"cleared": row.get("cleared")},
                )
            )
            continue
        if kind == "verify":
            events.append(
                SessionEvent(
                    type="verify",
                    id=_next(),
                    extra={k: v for k, v in row.items() if k not in ("event", "type")},
                )
            )
            continue
        if kind in ("profile", "done"):
            events.append(
                SessionEvent(
                    type="meta",
                    id=_next(),
                    kind=kind,
                    extra={k: v for k, v in row.items() if k not in ("event", "type")},
                )
            )
            continue
        if kind in KNOWN_TYPES:
            ev = SessionEvent.from_dict(row)
            if not ev.id:
                ev.id = _next()
            events.append(ev)
            if ev.type in SURFACE_TYPES:
                surface_ids.append(ev.id)
    return events


def migrate_legacy_session_file(src: str, dest: Optional[str] = None) -> str:
    """Rewrite a mixed turn JSONL as a v1 event log. Returns the dest path."""
    dest = dest or (src if src.endswith(".events.jsonl") else src.replace(".jsonl", ".events.jsonl"))
    if dest == src:
        dest = src + ".events.jsonl"
    rows: List[Dict[str, Any]] = []
    with open(src, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    events = migrate_legacy_events(rows)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
    from .session_fts import SessionEventIndex

    SessionEventIndex.for_log(dest)
    return dest
