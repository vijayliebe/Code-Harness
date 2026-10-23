"""Per-result context expansion: drop / chunk / neighbours / whole file.

Heuristic by default (score, identifier overlap, graph adjacency). A
decision-model classify runs only when ``decision.enabled`` is true and
``expansion`` is in ``decision.uses``. On any decision error, fall back
to the heuristic. Off until eval says otherwise.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import RetrievalResult

DROP = "drop"
CHUNK = "chunk"
NEIGHBORS = "neighbors"
FILE = "file"
EXPANSION_KINDS = (DROP, CHUNK, NEIGHBORS, FILE)
USE_EXPANSION = "expansion"


class ExpansionKind(str, Enum):
    DROP = DROP
    CHUNK = CHUNK
    NEIGHBORS = NEIGHBORS
    FILE = FILE


def expansion_section(config: Any = None) -> Dict[str, Any]:
    retrieval = getattr(config, "retrieval", None) if config is not None else None
    if not isinstance(retrieval, dict):
        return {}
    section = retrieval.get("expansion")
    return dict(section) if isinstance(section, dict) else {}


def expansion_enabled(config: Any = None) -> bool:
    return bool(expansion_section(config).get("enabled"))


def _query_tokens(query: str) -> set:
    from .retriever import Retriever

    return set(Retriever._tokenize(query or ""))


def classify_expansion(
    query: str,
    result: RetrievalResult,
    *,
    all_chunks: Optional[Sequence] = None,
    knowledge_graph=None,
    config: Any = None,
    decider=None,
) -> str:
    """Return drop | chunk | neighbors | file."""
    heuristic = classify_expansion_heuristic(
        query,
        result,
        all_chunks=all_chunks,
        knowledge_graph=knowledge_graph,
        config=config,
    )
    if decider is None:
        return heuristic
    decided = _classify_with_decision(
        decider, query, result, heuristic, config=config
    )
    return decided or heuristic


def classify_expansion_heuristic(
    query: str,
    result: RetrievalResult,
    *,
    all_chunks: Optional[Sequence] = None,
    knowledge_graph=None,
    config: Any = None,
) -> str:
    section = expansion_section(config)
    drop_below = float(section.get("drop_below") or 0.02)
    file_score = float(section.get("file_score") or 0.85)
    neighbor_score = float(section.get("neighbor_score") or 0.35)

    chunk = result.chunk
    score = float(result.score or 0.0)
    q = (query or "").lower()
    name = (chunk.entity_name or "").lower()
    path = (chunk.file_path or "").replace("\\", "/")
    base = os.path.basename(path).lower()
    tokens = _query_tokens(query)
    name_tokens = set(Retriever_tokenize(name)) if name else set()
    ident_hit = bool(name and (name in q or bool(tokens & name_tokens)))
    path_hit = bool(base and (base in q or (path and path.lower() in q)))

    if score < drop_below and not ident_hit and not path_hit:
        return DROP

    why = any(word in q for word in ("why", "how does", "overview", "whole file", "entire file"))
    if path_hit or (why and score >= file_score):
        return FILE
    if score >= file_score and ident_hit and _file_has_more_chunks(chunk, all_chunks):
        return FILE

    if score >= neighbor_score and (
        ident_hit
        or _has_adjacent_chunk(chunk, all_chunks)
        or _has_graph_neighbor(chunk, knowledge_graph)
    ):
        return NEIGHBORS
    return CHUNK


def Retriever_tokenize(text: str) -> List[str]:
    from .retriever import Retriever

    return Retriever._tokenize(text or "")


def _file_has_more_chunks(chunk, all_chunks: Optional[Sequence]) -> bool:
    if not all_chunks or not chunk.file_path:
        return False
    path = chunk.file_path
    return sum(1 for other in all_chunks if getattr(other, "file_path", None) == path) > 1


def _has_adjacent_chunk(chunk, all_chunks: Optional[Sequence]) -> bool:
    if not all_chunks or not chunk.file_path:
        return False
    for other in all_chunks:
        if other.id == chunk.id or other.file_path != chunk.file_path:
            continue
        if other.end_line + 1 >= chunk.start_line and other.start_line - 1 <= chunk.end_line:
            return True
        if abs(other.start_line - chunk.end_line) <= 2 or abs(chunk.start_line - other.end_line) <= 2:
            return True
    return False


def _has_graph_neighbor(chunk, knowledge_graph) -> bool:
    if knowledge_graph is None or not getattr(knowledge_graph, "enabled", False):
        return False
    try:
        return bool(knowledge_graph.get_neighbors(chunk.entity_id, max_depth=1))
    except Exception:
        return False


def _classify_with_decision(decider, query, result, heuristic: str, config=None) -> Optional[str]:
    try:
        from .decision import USE_LOOP_GRADE, decision_enabled
    except Exception:
        return None
    if not decision_enabled(config):
        return None
    uses = (getattr(config, "decision", None) or {}).get("uses") or []
    if USE_EXPANSION not in uses and "expansion" not in uses:
        return None
    if not hasattr(decider, "decide"):
        return None
    chunk = result.chunk
    questions = {
        "expansion": {
            "type": "choice",
            "instructions": (
                "How much of this code hit should be packed? "
                "drop: irrelevant. chunk: this span only. "
                "neighbors: this span plus adjacent same-file or graph neighbors. "
                "file: the whole file."
            ),
            "criteria": {
                DROP: "not useful for the query",
                CHUNK: "the matched span is enough",
                NEIGHBORS: "nearby helpers or callers are needed",
                FILE: "the question is about the whole module or file",
            },
        }
    }
    state = {
        "query": query,
        "heuristic": heuristic,
        "path": chunk.file_path,
        "entity": chunk.entity_name,
        "score": float(result.score or 0.0),
        "preview": (chunk.content or "")[:400],
    }
    try:
        answered = decider.decide(state, questions)
    except Exception:
        return None
    if answered is None:
        return None
    choice = answered.choice("expansion") if hasattr(answered, "choice") else None
    if choice in EXPANSION_KINDS:
        return choice
    return None


def apply_expansion(
    query: str,
    results: Sequence[RetrievalResult],
    *,
    all_chunks: Optional[Sequence] = None,
    knowledge_graph=None,
    config: Any = None,
    decider=None,
) -> List[RetrievalResult]:
    """Rewrite the result list according to per-hit expansion decisions."""
    if not expansion_enabled(config) or not results:
        return list(results)
    chunks = list(all_chunks or [])
    by_file: Dict[str, List[Any]] = {}
    by_entity: Dict[str, List[Any]] = {}
    for chunk in chunks:
        by_file.setdefault(chunk.file_path, []).append(chunk)
        by_entity.setdefault(chunk.entity_id, []).append(chunk)
    for path in by_file:
        by_file[path].sort(key=lambda c: (c.start_line, c.end_line))

    seen = set()
    out: List[RetrievalResult] = []
    for result in results:
        kind = classify_expansion(
            query,
            result,
            all_chunks=chunks,
            knowledge_graph=knowledge_graph,
            config=config,
            decider=decider,
        )
        if kind == DROP:
            continue
        extras: Iterable[Any] = ()
        if kind == FILE:
            extras = by_file.get(result.chunk.file_path, ())
        elif kind == NEIGHBORS:
            extras = _neighbor_chunks(
                result.chunk, by_file, by_entity, knowledge_graph
            )
        for extra in extras:
            if extra.id in seen:
                continue
            seen.add(extra.id)
            if extra.id == result.chunk.id:
                out.append(result)
            else:
                out.append(
                    RetrievalResult(
                        chunk=extra,
                        score=float(result.score or 0.0) * 0.85,
                        source="expansion",
                    )
                )
        if result.chunk.id not in seen:
            seen.add(result.chunk.id)
            out.append(result)
    return out


def _neighbor_chunks(chunk, by_file, by_entity, knowledge_graph) -> List[Any]:
    neighbors: List[Any] = [chunk]
    for other in by_file.get(chunk.file_path, ()):
        if other.id == chunk.id:
            continue
        adjacent = (
            other.end_line + 1 >= chunk.start_line
            and other.start_line - 1 <= chunk.end_line
        ) or abs(other.start_line - chunk.end_line) <= 2 or abs(chunk.start_line - other.end_line) <= 2
        if adjacent:
            neighbors.append(other)
    if knowledge_graph is not None and getattr(knowledge_graph, "enabled", False):
        try:
            related = knowledge_graph.get_neighbors(chunk.entity_id, max_depth=1)
        except Exception:
            related = set()
        for entity_id in related:
            neighbors.extend(by_entity.get(entity_id, ()))
    return neighbors
