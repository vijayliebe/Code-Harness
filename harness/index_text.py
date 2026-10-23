"""Index-time chunk enrichment for dense + BM25 text.

The displayed / packed body stays ``chunk.content``. When enabled, embeddings
and the BM25 token stream use a short prefix of file path + symbol name +
signature, then the plain chunk. Prefix is capped so MiniLM's 256-token
window still sees the body.

Changing ``indexing.enrich_index_text`` requires a re-index (Chroma vectors
and the BM25 pickle). Query-time rebuilds from stored chunks re-apply the
same function, so a BM25-only rebuild is enough if the vectors already
match the flag.
"""

from __future__ import annotations

from typing import Any, Optional

from .ccr import chunk_signature
from .models import Chunk

# Onyx caps metadata at 25% of the chunk. MiniLM is 256 tokens (~1024 chars).
DEFAULT_PREFIX_RATIO = 0.25
MAX_PREFIX_CHARS = 192


def enrich_index_text_enabled(config: Any = None) -> bool:
    indexing = getattr(config, "indexing", None) if config is not None else None
    if not isinstance(indexing, dict):
        return False
    return bool(indexing.get("enrich_index_text"))


def prefix_ratio(config: Any = None) -> float:
    indexing = getattr(config, "indexing", None) if config is not None else None
    if not isinstance(indexing, dict):
        return DEFAULT_PREFIX_RATIO
    try:
        value = float(indexing.get("enrich_prefix_ratio") or DEFAULT_PREFIX_RATIO)
    except (TypeError, ValueError):
        return DEFAULT_PREFIX_RATIO
    return min(0.5, max(0.05, value))


def chunk_index_prefix(chunk: Chunk) -> str:
    """Path + symbol + signature, de-duplicated when they repeat."""
    path = (chunk.file_path or "").replace("\\", "/").strip()
    name = (chunk.entity_name or "").strip()
    signature = (chunk_signature(chunk) or "").strip()
    parts = []
    seen = set()
    for part in (path, name, signature):
        key = part.lower()
        if not part or key in seen:
            continue
        seen.add(key)
        parts.append(part)
    return " ".join(parts)


def cap_prefix(prefix: str, content: str, ratio: float = DEFAULT_PREFIX_RATIO) -> str:
    if not prefix:
        return ""
    # Floor so short chunks still get path+name; ceiling keeps MiniLM's window.
    from_ratio = int(len(content or "") * float(ratio))
    budget = min(MAX_PREFIX_CHARS, max(96, from_ratio))
    if len(prefix) <= budget:
        return prefix
    return prefix[:budget].rstrip()


def chunk_index_text(chunk: Chunk, config: Any = None, enabled: Optional[bool] = None) -> str:
    """Text to embed / BM25-index. Never used as the packed display body."""
    content = chunk.content or ""
    on = enrich_index_text_enabled(config) if enabled is None else bool(enabled)
    if not on:
        return content
    prefix = cap_prefix(chunk_index_prefix(chunk), content, prefix_ratio(config))
    if not prefix:
        return content
    return f"{prefix}\n{content}"
