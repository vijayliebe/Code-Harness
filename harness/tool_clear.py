"""Claude Code steal: clear aged retrieve/tool dumps, keep ids for re-expand.

Conversation ``/compact`` still owns non-re-fetchable user/assistant text.
This module only replaces bulky, re-fetchable retrieve/tool payloads with
placeholders that keep ``chunk_id`` / path / tool+args so CCR can reload.

Default is **off** (``--clear-tool-results`` / ``CODEHARNESS_CLEAR_TOOL_RESULTS=1``
/ ``session.clear_tool_results``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from .config import Config


_FALSEY = frozenset({"0", "false", "off", "no"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

DEFAULT_CLEAR_KEEP = 1
DEFAULT_TOKEN_TRIGGER = 0
PLACEHOLDER_MARK = "[cleared tool_result]"
RETRIEVE_TOOL = "retrieve"


def estimate_tokens(text: str) -> int:
    return len(text or "") // 4


def tool_result_clearing_enabled(
    args=None,
    environ=None,
    config: Optional[Config] = None,
) -> bool:
    """Interactive default off. ``--no-clear-tool-results`` / env ``0`` wins."""
    env = environ if environ is not None else os.environ
    raw = str(env.get("CODEHARNESS_CLEAR_TOOL_RESULTS") or "").strip().lower()
    if args is not None and getattr(args, "no_clear_tool_results", False):
        return False
    if raw in _FALSEY:
        return False
    if args is not None and getattr(args, "clear_tool_results", False):
        return True
    if raw in _TRUTHY:
        return True
    if config is not None:
        session = getattr(config, "session", None) or {}
        if "clear_tool_results" in session:
            return bool(session.get("clear_tool_results"))
    return False


def resolve_clear_keep(args=None, config: Optional[Config] = None) -> int:
    if args is not None:
        raw = getattr(args, "clear_tool_keep", None)
        if raw is not None:
            return max(1, int(raw))
    if config is not None:
        session = getattr(config, "session", None) or {}
        if session.get("clear_tool_keep") is not None:
            return max(1, int(session.get("clear_tool_keep") or DEFAULT_CLEAR_KEEP))
        if session.get("keep_recent") is not None:
            return max(1, int(session.get("keep_recent") or DEFAULT_CLEAR_KEEP))
    return DEFAULT_CLEAR_KEEP


def resolve_token_trigger(args=None, config: Optional[Config] = None) -> int:
    if args is not None:
        raw = getattr(args, "clear_tool_token_trigger", None)
        if raw is not None:
            return max(0, int(raw))
    if config is not None:
        session = getattr(config, "session", None) or {}
        if session.get("clear_tool_token_trigger") is not None:
            return max(0, int(session.get("clear_tool_token_trigger") or 0))
    return DEFAULT_TOKEN_TRIGGER


def apply_clear_tool_config(config: Config, args=None, environ=None) -> Config:
    """Stamp resolved knobs onto ``config.session`` (default remains off)."""
    session = getattr(config, "session", None)
    if session is None:
        config.session = {}
        session = config.session
    session["clear_tool_results"] = tool_result_clearing_enabled(
        args, environ=environ, config=config
    )
    session["clear_tool_keep"] = resolve_clear_keep(args, config=config)
    session["clear_tool_token_trigger"] = resolve_token_trigger(args, config=config)
    return config


def is_placeholder(text: str) -> bool:
    return PLACEHOLDER_MARK in (text or "")


def is_refetchable_dump(turn) -> bool:
    """True when a turn holds (or held) a re-fetchable retrieve/tool payload."""
    if getattr(turn, "role", "") == "user":
        return False
    if getattr(turn, "cleared", False):
        return True
    if getattr(turn, "tool_result", None):
        return True
    tool = str(getattr(turn, "tool_name", "") or "").strip().lower()
    if tool and tool != "assistant":
        return bool(getattr(turn, "chunk_ids", None) or getattr(turn, "text", None))
    if getattr(turn, "chunk_ids", None) and _text_looks_like_dump(turn):
        return True
    return False


def _text_looks_like_dump(turn) -> bool:
    text = getattr(turn, "text", "") or ""
    if is_placeholder(text):
        return True
    if len(text) < 80:
        return False
    markers = (
        "# Query:",
        "## Relevant Code Context",
        "### File:",
        "retrieve_chunk ",
        "Packed: signatures",
        "### retrieve_chunk",
    )
    return any(marker in text for marker in markers)


def dump_payload(turn) -> str:
    """The bulky re-fetchable body, if any."""
    result = getattr(turn, "tool_result", None) or ""
    if result:
        return result
    if _text_looks_like_dump(turn) or str(getattr(turn, "tool_name", "") or "").strip():
        return getattr(turn, "text", "") or ""
    return ""


def payload_tokens(turn, estimate_fn: Optional[Callable[[str], int]] = None) -> int:
    est = estimate_fn or estimate_tokens
    return int(est(dump_payload(turn)))


def _args_summary(turn, limit: int = 120) -> str:
    raw = str(getattr(turn, "tool_args", "") or "").strip()
    if not raw and getattr(turn, "role", "") != "user":
        raw = ""
    blob = " ".join(raw.split())
    if len(blob) > limit:
        return blob[: limit - 3].rstrip() + "..."
    return blob


def placeholder_for(turn) -> str:
    """Short stub with enough to re-fetch via CCR / retrieve_chunk."""
    tool = str(getattr(turn, "tool_name", "") or "").strip() or RETRIEVE_TOOL
    args = _args_summary(turn)
    ids = [str(x) for x in (getattr(turn, "chunk_ids", None) or []) if x]
    paths = [str(x) for x in (getattr(turn, "paths", None) or []) if x]
    if not paths:
        for cid in ids:
            path = _path_from_chunk_id(cid)
            if path and path not in paths:
                paths.append(path)
    parts = [PLACEHOLDER_MARK, f"tool={tool}"]
    if args:
        parts.append(f"args={args}")
    if ids:
        parts.append("chunk_ids=" + ",".join(ids))
    if paths:
        parts.append("paths=" + ",".join(paths))
    parts.append("re-fetch: retrieve_chunk / /expand")
    return " ".join(parts)


def _path_from_chunk_id(chunk_id: str) -> str:
    raw = str(chunk_id or "")
    if raw.startswith("func:") or raw.startswith("class:") or raw.startswith("file:"):
        bits = raw.split(":")
        if len(bits) >= 3:
            return bits[1]
    if "/" in raw and not raw.startswith("func:"):
        return raw.split(":")[0]
    return ""


def apply_placeholder(turn, placeholder: Optional[str] = None) -> int:
    """Replace dump payload(s). Returns estimated tokens freed (len//4)."""
    stub = placeholder if placeholder is not None else placeholder_for(turn)
    after = estimate_tokens(stub)
    seen: List[str] = []
    result = getattr(turn, "tool_result", None) or ""
    text = getattr(turn, "text", "") or ""
    if result and not is_placeholder(result):
        seen.append(result)
        turn.tool_result = stub
    if text and not is_placeholder(text) and (
        _text_looks_like_dump(turn) or (result and text == result)
    ):
        if text not in seen:
            seen.append(text)
        turn.text = stub
    if not getattr(turn, "tool_result", None):
        turn.tool_result = stub
    turn.cleared = True
    before = sum(estimate_tokens(blob) for blob in seen)
    return max(0, before - after) if seen else 0


@dataclass
class ClearToolResult:
    cleared: int = 0
    kept: int = 0
    tokens_freed: int = 0
    placeholders: List[str] = field(default_factory=list)
    fired: bool = False


def _latest_user_index(turns: Sequence) -> Optional[int]:
    for i in range(len(turns) - 1, -1, -1):
        if getattr(turns[i], "role", "") == "user":
            return i
    return None


def _latest_pack_index(turns: Sequence) -> Optional[int]:
    for i in range(len(turns) - 1, -1, -1):
        if getattr(turns[i], "chunk_ids", None):
            return i
    return None


def _dump_indexes(turns: Sequence) -> List[int]:
    return [i for i, turn in enumerate(turns) if is_refetchable_dump(turn)]


def should_clear(
    turns: Sequence,
    *,
    keep_n: int = DEFAULT_CLEAR_KEEP,
    token_trigger: int = DEFAULT_TOKEN_TRIGGER,
    estimate_fn: Optional[Callable[[str], int]] = None,
) -> bool:
    dumps = _dump_indexes(turns)
    if not dumps:
        return False
    keep = max(1, int(keep_n or DEFAULT_CLEAR_KEEP))
    if len(dumps) > keep:
        return True
    trigger = int(token_trigger or 0)
    if trigger > 0:
        total = sum(payload_tokens(turns[i], estimate_fn) for i in dumps)
        if total > trigger:
            return True
    return False


def select_indexes_to_clear(
    turns: Sequence,
    *,
    keep_n: int = DEFAULT_CLEAR_KEEP,
    token_trigger: int = DEFAULT_TOKEN_TRIGGER,
    estimate_fn: Optional[Callable[[str], int]] = None,
) -> List[int]:
    """Aged refetchable dumps only. Never the latest user or current top pack."""
    dumps = _dump_indexes(turns)
    if not dumps:
        return []
    keep = max(1, int(keep_n or DEFAULT_CLEAR_KEEP))
    last_user = _latest_user_index(turns)
    last_pack = _latest_pack_index(turns)
    protected = set()
    if last_user is not None:
        protected.add(last_user)
    if last_pack is not None:
        protected.add(last_pack)
    keep_set = set(dumps[-keep:])
    protected |= keep_set

    chosen: List[int] = []
    for idx in dumps:
        if idx in protected:
            continue
        if is_placeholder(dump_payload(turns[idx])):
            continue
        chosen.append(idx)

    trigger = int(token_trigger or 0)
    if trigger > 0:
        remaining = [i for i in dumps if i not in chosen]
        total = sum(payload_tokens(turns[i], estimate_fn) for i in remaining)
        extras = [i for i in remaining if i != last_pack and i != last_user]
        extras.sort()
        for idx in extras:
            if total <= trigger:
                break
            if idx in keep_set and idx == last_pack:
                continue
            if is_placeholder(dump_payload(turns[idx])):
                continue
            # Token trigger may clear keep-N dumps except the current top pack.
            if idx == last_pack:
                continue
            chosen.append(idx)
            total -= payload_tokens(turns[idx], estimate_fn)

    chosen = sorted(set(chosen))
    return chosen


def clear_tool_results(
    turns: Sequence,
    *,
    keep_n: int = DEFAULT_CLEAR_KEEP,
    token_trigger: int = DEFAULT_TOKEN_TRIGGER,
    estimate_fn: Optional[Callable[[str], int]] = None,
    enabled: bool = True,
) -> ClearToolResult:
    """Replace aged dump payloads in-place. No-op when ``enabled`` is false."""
    result = ClearToolResult()
    if not enabled:
        result.kept = len(_dump_indexes(turns))
        return result
    if not should_clear(
        turns, keep_n=keep_n, token_trigger=token_trigger, estimate_fn=estimate_fn
    ):
        result.kept = len(_dump_indexes(turns))
        return result
    indexes = select_indexes_to_clear(
        turns,
        keep_n=keep_n,
        token_trigger=token_trigger,
        estimate_fn=estimate_fn,
    )
    result.fired = bool(indexes)
    for idx in indexes:
        turn = turns[idx]
        stub = placeholder_for(turn)
        result.tokens_freed += apply_placeholder(turn, stub)
        result.placeholders.append(stub)
        result.cleared += 1
    result.kept = len(_dump_indexes(turns)) - result.cleared
    return result
