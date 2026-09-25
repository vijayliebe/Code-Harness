"""Chat-over-wiki: opt-in wiki-first retrieve/pack/expand via CCR.

Default query/chat is unchanged. Enable with ``chat.wiki_mode``,
``--wiki``, ``CODEHARNESS_WIKI_MODE=1``, or session ``/wiki`` / ``/wiki on``.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

from .okf import is_wiki_path
from .retriever import is_wiki_chunk


WIKI_MODE_WEIGHT = 0.15
WIKI_SPARSE_MIN = 1
WIKI_ANSWER_LINE = (
    "Answer from the knowledge wiki first. "
    "Cite wiki pages as `knowledge/wiki/<page>` and keep source cites as `path:symbol`."
)

PATH_SYMBOL_RE = re.compile(
    r"`([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+):([A-Za-z_][A-Za-z0-9_.]*)`"
)

_RESTORE_WEIGHT = "_wiki_weight_restore"
_OWNED_PREFIX = "_wiki_owned_knowledge_prefix"


def wiki_mode_enabled(config) -> bool:
    chat = getattr(config, "chat", None) or {}
    return bool(chat.get("wiki_mode"))


def apply_wiki_mode(config, enabled: bool):
    """Turn wiki chat on or off without permanently retuning RRF."""
    chat = getattr(config, "chat", None)
    if chat is None:
        config.chat = {}
        chat = config.chat
    retrieval = getattr(config, "retrieval", None)
    if retrieval is None:
        config.retrieval = {}
        retrieval = config.retrieval
    context = getattr(config, "context", None)
    if context is None:
        config.context = {}
        context = config.context

    enabled = bool(enabled)
    chat["wiki_mode"] = enabled
    if enabled:
        if _RESTORE_WEIGHT not in chat:
            chat[_RESTORE_WEIGHT] = float(retrieval.get("wiki_weight", 0.0) or 0.0)
        if float(retrieval.get("wiki_weight", 0.0) or 0.0) <= 0:
            retrieval["wiki_weight"] = WIKI_MODE_WEIGHT
        if not context.get("knowledge_prefix"):
            context["knowledge_prefix"] = True
            chat[_OWNED_PREFIX] = True
    else:
        if _RESTORE_WEIGHT in chat:
            retrieval["wiki_weight"] = chat.pop(_RESTORE_WEIGHT)
        if chat.pop(_OWNED_PREFIX, False):
            context["knowledge_prefix"] = False
    return config


def sync_retriever(retriever, config) -> None:
    if retriever is None:
        return
    retriever.config = config
    retriever.wiki_weight = float((getattr(config, "retrieval", None) or {}).get("wiki_weight", 0.0) or 0.0)


def prefer_wiki_results(results, *, sparse_min: int = WIKI_SPARSE_MIN):
    """Put wiki hits first when the channel is not empty; otherwise keep hybrid order."""
    if not results:
        return results
    wiki = [r for r in results if is_wiki_chunk(getattr(r, "chunk", None))]
    if len(wiki) < int(sparse_min or 0):
        return results
    other = [r for r in results if not is_wiki_chunk(getattr(r, "chunk", None))]
    return list(wiki) + list(other)


def pack_file_sort_key(path: str, wiki_first: bool = False):
    norm = (path or "").replace("\\", "/")
    if wiki_first and is_wiki_path(norm):
        return (0, norm)
    return (1 if wiki_first else 0, norm)


def extract_path_symbols(text: str) -> List[str]:
    return [f"{path}:{symbol}" for path, symbol in PATH_SYMBOL_RE.findall(text or "")]


def linked_expand_ids(
    wiki_texts: Sequence[str],
    chunks: Sequence,
    packed_ids: Optional[Iterable[str]] = None,
) -> List[str]:
    """CCR retrieve-back ids for ``path:symbol`` cites in wiki pages."""
    wanted = set()
    for text in wiki_texts or []:
        wanted.update(extract_path_symbols(text))
    packed = set(packed_ids or [])
    ids: List[str] = []
    seen = set()
    for chunk in chunks or []:
        cid = getattr(chunk, "id", None)
        if not cid or cid in packed or cid in seen:
            continue
        path = (getattr(chunk, "file_path", None) or "").replace("\\", "/")
        cite = f"{path}:{getattr(chunk, 'entity_name', '')}"
        if cite in wanted:
            ids.append(cid)
            seen.add(cid)
    return ids


def seed_ccr_cache(builder, chunks: Sequence) -> None:
    cache = getattr(builder, "cache", None)
    if cache is None:
        return
    for chunk in chunks or []:
        cid = getattr(chunk, "id", None)
        content = getattr(chunk, "content", None)
        if cid and content is not None:
            cache.put(cid, content)


def wiki_expand_ids(report, chunks: Sequence, limit: int = 3) -> List[str]:
    packed_ids = list(getattr(report, "packed_chunk_ids", None) or [])
    texts = []
    context = getattr(report, "context", "") or ""
    if context:
        texts.append(context)
    return linked_expand_ids(texts, chunks, packed_ids)[: max(0, int(limit))]
