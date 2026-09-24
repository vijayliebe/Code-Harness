"""Pure retrieval metrics for the local eval harness (no network, no extra deps)."""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Set

FAILURE_LABELS = (
    "dense_miss",
    "bm25_miss",
    "graph_miss",
    "rerank_drop",
    "packer_drop",
)


def normalize_path(path: str) -> str:
    text = (path or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def _path_eq(a: str, b: str) -> bool:
    left, right = normalize_path(a), normalize_path(b)
    if not left or not right:
        return False
    return left == right or left.endswith("/" + right) or right.endswith("/" + left)


def _entity_key(eid: Optional[str]) -> Optional[tuple]:
    """(path, unqualified_name) from `kind:path:Name` or `kind:path:Class.name[:extra]`."""
    if not eid:
        return None
    parts = eid.split(":")
    if len(parts) < 3:
        return None
    path = normalize_path(parts[1])
    short = parts[2].split(".")[-1]
    if not path or not short:
        return None
    return path, short


def match_gold_id(chunk_id: Optional[str], entity_id: Optional[str], gold: str) -> bool:
    """Match a retrieved chunk to a gold id.

    Chunk ids are `{entity_id}:{uuid}` and change on re-index. Gold labels may be
    the full chunk id, the stable entity id, or `func:path:name` matching a
    treesitter `method:path:Class.name`.
    """
    if not gold:
        return False
    if chunk_id == gold or entity_id == gold:
        return True
    if chunk_id and chunk_id.startswith(gold + ":"):
        return True
    if entity_id and entity_id.startswith(gold + ":"):
        return True
    gold_key = _entity_key(gold)
    if gold_key:
        for candidate in (entity_id, chunk_id):
            cand_key = _entity_key(candidate)
            if cand_key and cand_key == gold_key:
                return True
    return False


def gold_ids_hit(chunks: Iterable, gold_ids: Sequence[str]) -> Set[str]:
    hit: Set[str] = set()
    for gold in gold_ids:
        for chunk in chunks:
            if match_gold_id(getattr(chunk, "id", None), getattr(chunk, "entity_id", None), gold):
                hit.add(gold)
                break
    return hit


def gold_ids_hit_from_ids(candidate_ids: Sequence[str], gold_ids: Sequence[str]) -> Set[str]:
    hit: Set[str] = set()
    for gold in gold_ids:
        for cid in candidate_ids:
            if match_gold_id(cid, None, gold):
                hit.add(gold)
                break
    return hit


def paths_in_order(chunks: Iterable) -> List[str]:
    ordered: List[str] = []
    seen: Set[str] = set()
    for chunk in chunks:
        path = normalize_path(getattr(chunk, "file_path", "") or "")
        if path and path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def recall_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> Optional[float]:
    if not relevant:
        return None
    hit = set(retrieved[:k]) & set(relevant)
    return len(hit) / len(set(relevant))


def ndcg_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> Optional[float]:
    if not relevant:
        return None
    relevant_set = set(relevant)
    seen: Set[str] = set()
    gains = []
    for item in retrieved[:k]:
        if item in relevant_set and item not in seen:
            gains.append(1.0)
            seen.add(item)
        else:
            gains.append(0.0)
    ideal = [1.0] * min(len(relevant_set), k)
    denom = _dcg(ideal)
    if denom == 0.0:
        return 0.0
    return min(1.0, _dcg(gains) / denom)


def _dcg(gains: Sequence[float]) -> float:
    return sum(gain / math.log2(idx + 2) for idx, gain in enumerate(gains))


def citation_path_hit_rate(cited_paths: Sequence[str], must_cite_paths: Sequence[str]) -> Optional[float]:
    if not must_cite_paths:
        return None
    hits = 0
    for required in must_cite_paths:
        if any(_path_eq(cited, required) for cited in cited_paths):
            hits += 1
    return hits / len(must_cite_paths)


def cited_paths_hit(cited_paths: Sequence[str], must_cite_paths: Sequence[str]) -> Set[str]:
    hit: Set[str] = set()
    for required in must_cite_paths:
        if any(_path_eq(cited, required) for cited in cited_paths):
            hit.add(normalize_path(required))
    return hit


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _stage_miss(
    gold_ids: Sequence[str],
    must_cite_paths: Sequence[str],
    candidate_ids: Sequence[str],
    candidate_paths: Optional[Sequence[str]],
) -> bool:
    if gold_ids:
        return len(gold_ids_hit_from_ids(candidate_ids, gold_ids)) == 0
    if must_cite_paths:
        rate = citation_path_hit_rate(candidate_paths or [], must_cite_paths)
        return rate is None or rate == 0.0
    return False


def classify_failures(
    gold_ids: Sequence[str],
    must_cite_paths: Sequence[str],
    dense_ids: Sequence[str],
    bm25_ids: Sequence[str],
    graph_ids: Sequence[str],
    fused_ids: Sequence[str],
    final_ids: Sequence[str],
    packed_ids: Optional[Sequence[str]],
    packed_paths: Optional[Sequence[str]],
    rerank_enabled: bool,
    dense_paths: Optional[Sequence[str]] = None,
    bm25_paths: Optional[Sequence[str]] = None,
    graph_paths: Optional[Sequence[str]] = None,
    fused_paths: Optional[Sequence[str]] = None,
    final_paths: Optional[Sequence[str]] = None,
) -> List[str]:
    """Return zero or more labels from FAILURE_LABELS for a single fixture."""
    labels: List[str] = []
    if _stage_miss(gold_ids, must_cite_paths, dense_ids, dense_paths):
        labels.append("dense_miss")
    if _stage_miss(gold_ids, must_cite_paths, bm25_ids, bm25_paths):
        labels.append("bm25_miss")
    if _stage_miss(gold_ids, must_cite_paths, graph_ids, graph_paths):
        labels.append("graph_miss")

    if rerank_enabled:
        if gold_ids:
            dropped = gold_ids_hit_from_ids(fused_ids, gold_ids) - gold_ids_hit_from_ids(final_ids, gold_ids)
            if dropped:
                labels.append("rerank_drop")
        elif must_cite_paths:
            fused_hit = cited_paths_hit(fused_paths or [], must_cite_paths)
            final_hit = cited_paths_hit(final_paths or [], must_cite_paths)
            if fused_hit - final_hit:
                labels.append("rerank_drop")

    if packed_ids is None and packed_paths is None:
        return labels

    if gold_ids:
        dropped_pack = gold_ids_hit_from_ids(final_ids, gold_ids) - gold_ids_hit_from_ids(packed_ids or [], gold_ids)
        if dropped_pack:
            labels.append("packer_drop")
    elif must_cite_paths:
        final_hit = cited_paths_hit(final_paths or [], must_cite_paths)
        packed_hit = cited_paths_hit(packed_paths or [], must_cite_paths)
        if final_hit - packed_hit:
            labels.append("packer_drop")

    return labels
