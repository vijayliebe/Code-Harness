"""Prefix-stable packing — freeze system / tools / knowledge for KV cache.

Invariants (do not reshuffle a live request series):

1. **System sections** concatenate in ``PREFIX_SECTION_ORDER``.
2. **Tool schemas** are JSON with ``sort_keys=True``, tools sorted by name.
3. **Project docs** follow ``context.prefix_files`` (ARCHITECTURE, AGENTS, CLAUDE).
4. **Knowledge prefix** uses frozen **path** order, never query-rank reshuffle.
5. **Memory brief** is query-independent (empty query / first snapshot).
6. **Volatile retrieval hits come last.** Hits may change; prefix bytes must not.
7. Occupied-path dedupe against hits is **off** while frozen (it would bust cache
   when the pack changes). Hits may still drop copies already in the prefix blob.
8. Same ``PrefixRoute`` + same vault snapshot → identical prefix bytes + hash.
9. Profile / wiki / prefix_files / tool-schema changes start a **new route**.
10. Compaction that needs a summary should replay this prefix byte-for-byte.

Counted once with Headroom CacheAligner / CCR-lite layout. Not a Cordis import.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


_FALSEY = frozenset({"0", "false", "off", "no"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

PREFIX_SECTION_ORDER: Tuple[str, ...] = (
    "system",
    "tools",
    "project_docs",
    "memory_brief",
    "knowledge_prefix",
)

PREFIX_INVARIANTS = PREFIX_SECTION_ORDER + (
    "volatile_hits_last",
    "path_stable_knowledge",
    "sorted_tool_schemas",
    "frozen_per_route",
)

DEFAULT_TOOL_SCHEMAS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "retrieve",
        "description": "Hybrid retrieve over the local code index (BM25 + dense + graph).",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "retrieve_chunk",
        "description": "Load a cached original chunk by id (CCR retrieve-back).",
        "parameters": {
            "type": "object",
            "properties": {"chunk_id": {"type": "string"}},
            "required": ["chunk_id"],
        },
    },
    {
        "name": "graph_neighbors",
        "description": "Read-only knowledge-graph neighbor expand.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "depth": {"type": "integer"},
            },
            "required": ["entity_id"],
        },
    },
)


def prefix_stable_enabled(
    args=None,
    environ=None,
    config=None,
    event_session: Optional[bool] = None,
) -> bool:
    """Default off. Event-session turns this on unless ``--no-prefix-stable``."""
    env = environ if environ is not None else os.environ
    raw = str(env.get("CODEHARNESS_PREFIX_STABLE") or "").strip().lower()
    if args is not None and getattr(args, "no_prefix_stable", False):
        return False
    if raw in _FALSEY:
        return False
    if args is not None and getattr(args, "prefix_stable", False):
        return True
    if raw in _TRUTHY:
        return True
    if event_session:
        return True
    if config is not None:
        context = getattr(config, "context", None) or {}
        if "prefix_stable" in context:
            return bool(context.get("prefix_stable"))
        session = getattr(config, "session", None) or {}
        if session.get("event_session"):
            return True
    return False


def freeze_tool_schemas(tools: Optional[Sequence[Dict[str, Any]]] = None) -> str:
    """Byte-stable tool schema JSON (sorted names, sorted keys)."""
    items = [dict(t) for t in (tools if tools is not None else DEFAULT_TOOL_SCHEMAS)]
    items.sort(key=lambda t: str(t.get("name") or ""))
    return json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prefix_digest(blob: str) -> str:
    return hashlib.sha256((blob or "").encode("utf-8")).hexdigest()[:12]


def _project_docs_blob(project_docs: Optional[Dict[str, str]], names: Sequence[str]) -> str:
    if not project_docs:
        return ""
    parts: List[str] = ["## Project-level Context Files\n"]
    order = list(names) if names else sorted(project_docs)
    seen = set()
    for name in order:
        if name in project_docs and name not in seen:
            seen.add(name)
            parts.append(f"**{name}** (project root)\n```\n{project_docs[name]}\n```\n")
    for name in sorted(project_docs):
        if name not in seen:
            parts.append(f"**{name}** (project root)\n```\n{project_docs[name]}\n```\n")
    return "".join(parts)


def assemble_prefix(
    *,
    system: str = "",
    tools_blob: str = "",
    project_docs: Optional[Dict[str, str]] = None,
    prefix_files: Optional[Sequence[str]] = None,
    memory_brief: str = "",
    knowledge_prefix: str = "",
) -> str:
    """Concatenate frozen sections. Volatile hits are *not* included."""
    names = list(prefix_files or ("ARCHITECTURE.md", "AGENTS.md", "CLAUDE.md"))
    sections = {
        "system": (system or "").rstrip() + ("\n" if system else ""),
        "tools": (f"## Tools\n{tools_blob}\n" if tools_blob else ""),
        "project_docs": _project_docs_blob(project_docs, names),
        "memory_brief": (
            memory_brief
            if not memory_brief or memory_brief.endswith("\n")
            else memory_brief + "\n"
        ),
        "knowledge_prefix": (
            knowledge_prefix
            if not knowledge_prefix or knowledge_prefix.endswith("\n")
            else knowledge_prefix + "\n"
        ),
    }
    return "".join(sections[name] for name in PREFIX_SECTION_ORDER)


@dataclass(frozen=True)
class PrefixRoute:
    pack_mode: str = "full"
    wiki_mode: bool = False
    knowledge_prefix: bool = False
    include_memory_brief: bool = False
    prefix_files: Tuple[str, ...] = ()
    profile: str = "default"
    tools: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pack_mode": self.pack_mode,
            "wiki_mode": self.wiki_mode,
            "knowledge_prefix": self.knowledge_prefix,
            "include_memory_brief": self.include_memory_brief,
            "prefix_files": list(self.prefix_files),
            "profile": self.profile,
            "tools": list(self.tools),
        }


@dataclass
class PrefixFreeze:
    route: PrefixRoute
    system: str
    tools_blob: str
    project_docs: Dict[str, str] = field(default_factory=dict)
    memory_brief: str = ""
    knowledge: str = ""
    blob: str = ""
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.blob:
            self.blob = assemble_prefix(
                system=self.system,
                tools_blob=self.tools_blob,
                project_docs=self.project_docs,
                prefix_files=self.route.prefix_files,
                memory_brief=self.memory_brief,
                knowledge_prefix=self.knowledge,
            )
        if not self.digest:
            self.digest = prefix_digest(self.blob)


def route_from_config(config, *, profile: str = "default") -> PrefixRoute:
    context = getattr(config, "context", None) or {}
    chat = getattr(config, "chat", None) or {}
    tools = tuple(t["name"] for t in DEFAULT_TOOL_SCHEMAS)
    files = tuple(context.get("prefix_files") or ("ARCHITECTURE.md", "AGENTS.md", "CLAUDE.md"))
    return PrefixRoute(
        pack_mode=str(context.get("pack_mode") or "full"),
        wiki_mode=bool(chat.get("wiki_mode")),
        knowledge_prefix=bool(context.get("knowledge_prefix")),
        include_memory_brief=bool(context.get("include_memory_brief")),
        prefix_files=files,
        profile=profile or "default",
        tools=tools,
    )


def pack_stable_knowledge_prefix(
    docs,
    max_tokens: int,
    skip_memory: bool = False,
) -> str:
    """Path-ordered knowledge prefix (no query rank)."""
    from .okf import (
        KNOWLEDGE_PREFIX_HEADER,
        KNOWLEDGE_PREFIX_ITEM_CAP,
        estimate_tokens,
        format_knowledge_entry,
        is_memory_page,
        VAULT_KIND_MEMORY,
    )

    cap = max(0, int(max_tokens or 0))
    if cap <= 0 or not docs:
        return ""
    ordered = sorted(list(docs or []), key=lambda d: (d.rel_path or "").replace("\\", "/"))
    assembled = KNOWLEDGE_PREFIX_HEADER
    used = 0
    for doc in ordered:
        if skip_memory and (doc.kind == VAULT_KIND_MEMORY or is_memory_page(doc.page)):
            continue
        try:
            from .okf import _memory_inactive

            if _memory_inactive(doc):
                continue
        except Exception:
            pass
        candidate = assembled + format_knowledge_entry(doc)
        if estimate_tokens(candidate) > cap:
            if used == 0:
                max_chars = max(0, cap * 4)
                clipped = candidate[:max_chars].rstrip()
                return (clipped + "\n") if clipped and not clipped.endswith("\n") else clipped
            break
        assembled = candidate
        used += 1
        if used >= KNOWLEDGE_PREFIX_ITEM_CAP:
            break
    return assembled if used else ""


def snapshot_prefix(builder, *, tools: Optional[Sequence[Dict[str, Any]]] = None) -> PrefixFreeze:
    """Freeze the current builder's prefix (empty-query, path-stable knowledge)."""
    config = builder.config
    context = getattr(config, "context", None) or {}
    if tools is None:
        tools = list(DEFAULT_TOOL_SCHEMAS)
        session = getattr(config, "session", None) or {}
        if session.get("event_session"):
            from .session_fts import SEARCH_SESSION_TOOL

            tools = list(tools) + [SEARCH_SESSION_TOOL]
    tools_blob = freeze_tool_schemas(tools)
    project_docs = builder._load_project_context()
    prev = getattr(builder, "_prefix_freeze", None)
    builder._prefix_freeze = None
    try:
        memory_brief = builder._load_memory_brief("")
        knowledge = builder._load_knowledge_prefix("", occupied_paths=None)
        system = builder.build_system_prompt()
    finally:
        builder._prefix_freeze = prev
    route = route_from_config(config, profile=str(context.get("profile") or "default"))
    freeze = PrefixFreeze(
        route=route,
        system=system,
        tools_blob=tools_blob,
        project_docs=project_docs,
        memory_brief=memory_brief,
        knowledge=knowledge,
    )
    return freeze
