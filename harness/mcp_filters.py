"""MCP retrieve argument validation and the shared error envelope.

Unknown filter names and illegal enum values are rejected with close-match
suggestions so the scope is never silently wider. The list of alternatives
is capped so the error stays short.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_NAMES_IN_ERROR = 10

# Retrieve-tool arguments that act as filters / knobs. ``query`` is required
# content, not a filter, but listed so unknown-key matching can suggest it.
RETRIEVE_FILTERS: Dict[str, Dict[str, Any]] = {
    "query": {"type": "string", "required": True, "filter": False},
    "top_k": {"type": "integer", "filter": True},
    "pack_mode": {
        "type": "string",
        "enum": ["full", "ccr_lite"],
        "filter": True,
    },
    "include_memory_brief": {"type": "boolean", "filter": True},
    "include_memory_search": {"type": "boolean", "filter": True},
    "include_knowledge_prefix": {"type": "boolean", "filter": True},
    "expand_chunks": {"type": "array", "filter": True},
    "skip_query_expansion": {
        "type": "boolean",
        "aliases": ["skip_expansion"],
        "filter": True,
        "description": "Skip identifier / HyDE multi-query and embedder expand.",
    },
}

_ALIAS_TO_CANONICAL = {
    "skip_expansion": "skip_query_expansion",
}


def error_envelope(message: str, extras: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Onyx-style tool error: same shape on every failure."""
    payload: Dict[str, Any] = {"error": message, "results": []}
    if extras:
        payload.update(extras)
    return payload


def list_filters() -> Dict[str, Any]:
    """Discoverable filter catalog for agents."""
    items = []
    for name, spec in RETRIEVE_FILTERS.items():
        if not spec.get("filter"):
            continue
        item = {
            "name": name,
            "type": spec.get("type"),
        }
        if spec.get("enum"):
            item["enum"] = list(spec["enum"])
        if spec.get("aliases"):
            item["aliases"] = list(spec["aliases"])
        if spec.get("description"):
            item["description"] = spec["description"]
        items.append(item)
    return {
        "filters": items,
        "names": [item["name"] for item in items],
    }


def canonical_filter_name(name: str) -> str:
    key = str(name or "").strip()
    return _ALIAS_TO_CANONICAL.get(key, key)


def known_filter_names() -> List[str]:
    names = list(RETRIEVE_FILTERS)
    for spec in RETRIEVE_FILTERS.values():
        names.extend(spec.get("aliases") or [])
    return names


def close_matches(value: str, choices: Sequence[str], n: int = 3) -> List[str]:
    return difflib.get_close_matches(value, list(choices), n=n, cutoff=0.4)


def _format_suggestions(matches: Sequence[str]) -> str:
    shown = list(matches)[:MAX_NAMES_IN_ERROR]
    if not shown:
        return ""
    return " Did you mean: " + ", ".join(shown) + "?"


def validate_retrieve_args(args: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Return ``(error_envelope, normalized_args)``. error is None when valid."""
    raw = dict(args or {})
    unknown = []
    for key in raw:
        canon = canonical_filter_name(key)
        if canon not in RETRIEVE_FILTERS:
            unknown.append(key)
    if unknown:
        suggestions: List[str] = []
        for key in unknown:
            suggestions.extend(close_matches(key, known_filter_names()))
        # unique, stable
        seen = set()
        uniq = []
        for item in suggestions:
            if item in seen:
                continue
            seen.add(item)
            uniq.append(item)
        names = ", ".join(unknown)
        valid = ", ".join(name for name, spec in RETRIEVE_FILTERS.items() if spec.get("filter"))
        msg = f"unknown filter(s): {names}. Valid filters: {valid}."
        msg += _format_suggestions(uniq)
        return error_envelope(msg, {"unknown": unknown, "suggestions": uniq}), raw

    normalized: Dict[str, Any] = {}
    for key, value in raw.items():
        canon = canonical_filter_name(key)
        spec = RETRIEVE_FILTERS[canon]
        allowed = spec.get("enum")
        if allowed is not None and value is not None and value not in allowed:
            matches = close_matches(str(value), [str(x) for x in allowed])
            msg = (
                f"invalid {canon}={value!r}. "
                f"Allowed: {', '.join(str(x) for x in allowed)}."
            )
            msg += _format_suggestions(matches)
            return error_envelope(msg, {"field": canon, "suggestions": matches}), raw
        normalized[canon] = value
    return None, normalized
