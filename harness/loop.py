"""Bounded corrective retrieval loop (grade / rewrite / HyDE / deepen).

Opt-in: ``retrieval.max_loops=0`` (default) is the existing one-shot hybrid path.
When ``max_loops`` > 0 the loop may take at most two extra retrieve rounds.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .metrics import citation_path_hit_rate, normalize_path

MAX_EXTRA_LOOPS = 2

EXPLAIN_WORDS = frozenset({"explain", "why", "how", "compare", "design", "tradeoff"})
GRAPH_WORDS = frozenset({
    "caller", "callers", "callee", "callees",
    "exposes", "expose", "unused", "dead",
    "inherits", "calls", "called", "unused",
})
PATH_EXTENSIONS = frozenset({"py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "rb", "md"})
STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "in", "on", "at", "to", "for", "of", "and", "or", "if",
    "who", "what", "where", "when", "which", "whom",
    "does", "do", "did", "this", "that", "with", "from",
    "into", "about", "defined", "find", "show",
})
CLI_HINTS = frozenset({"cli", "command", "subcommand", "commands"})

_IDENTIFIER_ONLY = re.compile(r"^[A-Za-z_][A-Za-z0-9_\.]*$")
_TICKED = re.compile(r"`([^`]+)`")
_FILE_PATH = re.compile(
    r"\b[\w./-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|md)\b",
    re.IGNORECASE,
)
_CAMEL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_DOTTED = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b")
_CITED_PATH = re.compile(
    r"(?<![A-Za-z0-9_/])("
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\."
    r"(?:py|js|ts|tsx|jsx|go|rs|java|rb|md)"
    r")(?::[A-Za-z_][A-Za-z0-9_.]*)?"
)
_CAMEL_SPLIT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")

VERIFY_SYSTEM = (
    "You check whether an answer is supported by the packed code chunks. "
    "Use only the query, answer, and chunks. Do not reuse generator reasoning. "
    'Reply with JSON only: {"supported": true|false, "missing_paths": ["path"]}.'
)


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", (text or "").lower())


def _split_ident(text: str) -> List[str]:
    parts: List[str] = []
    for raw in re.findall(r"[A-Za-z0-9_]+", text or ""):
        if "_" in raw:
            parts.extend(p for p in raw.split("_") if p)
        parts.extend(p.lower() for p in _CAMEL_SPLIT.findall(raw) if p)
        parts.append(raw.lower())
    return parts


def detect_query_mode(query: str, no_llm: bool = False) -> str:
    """Return ``bm25`` | ``graph`` | ``hybrid`` from query shape."""
    del no_llm  # --no-llm stays retrieve+pack; it does not force a mode
    stripped = (query or "").strip()
    if not stripped:
        return "hybrid"
    lowered = stripped.lower()
    tokens = tokenize(stripped)
    wordset = set(tokens)
    if wordset & GRAPH_WORDS or "who calls" in lowered or "used by" in lowered:
        return "graph"
    if wordset & EXPLAIN_WORDS:
        return "hybrid"

    ticks = [t.strip() for t in _TICKED.findall(stripped) if t.strip()]
    core = stripped.strip("`").strip()
    if ticks and stripped == f"`{ticks[0]}`":
        core = ticks[0]
    if _IDENTIFIER_ONLY.match(core):
        return "bm25"
    if ticks and all(_IDENTIFIER_ONLY.match(t) for t in ticks) and len(tokens) <= 8:
        return "bm25"
    if len(tokens) <= 8 and _mostly_identifiers(stripped, tokens):
        return "bm25"
    return "hybrid"


def _mostly_identifiers(query: str, tokens: Sequence[str]) -> bool:
    content = [t for t in tokens if t not in STOPWORDS and t not in PATH_EXTENSIONS]
    if not content:
        return False
    if any(_IDENTIFIER_ONLY.match(t) and ("_" in t or t[:1].isupper()) for t in query.replace("`", "").split()):
        return True
    if _CAMEL.search(query) or _SNAKE.search(query) or _DOTTED.search(query):
        return len(content) <= 4
    return False


def _chunk_of(item: Any) -> Any:
    return getattr(item, "chunk", item)


def _infer_ce_max(results: Sequence[Any], ce_max: Optional[float] = None) -> float:
    if ce_max is not None:
        return float(ce_max)
    scores = []
    for item in results or []:
        source = getattr(item, "source", "") or ""
        if source in ("reranked", "ce", "cross_encoder"):
            scores.append(float(getattr(item, "score", 0.0) or 0.0))
    return max(scores) if scores else 0.0


def _normalize_ce(score: float) -> float:
    if score <= 0:
        return 0.0
    if score <= 1.0:
        return float(score)
    return 1.0 / (1.0 + math.exp(-float(score)))


def heuristic_grade(
    query: str,
    results: Sequence[Any],
    ce_max: Optional[float] = None,
) -> float:
    """Lexical grade: name/path overlap, plus a bounded CE term if present."""
    q_tokens = set(tokenize(query))
    q_norm = (query or "").strip().strip("`").lower()
    name_tokens: set = set()
    name_hits = 0
    path_hit = False
    for item in results or []:
        chunk = _chunk_of(item)
        name = getattr(chunk, "entity_name", "") or ""
        path = normalize_path(getattr(chunk, "file_path", "") or "")
        stem = os.path.splitext(os.path.basename(path))[0]
        name_tokens.update(tokenize(name))
        name_tokens.update(tokenize(stem))
        name_tokens.update(_split_ident(name))
        name_tokens.update(_split_ident(stem))
        lowered_name = name.lower()
        if q_norm and (q_norm == lowered_name or q_norm in lowered_name.split(".")):
            name_hits = max(name_hits, 2)
        for tok in q_tokens:
            if len(tok) < 3 or tok in STOPWORDS or tok in PATH_EXTENSIONS:
                continue
            if tok in path.lower():
                path_hit = True
    name_hits = max(name_hits, len(q_tokens & name_tokens))
    ce = _normalize_ce(_infer_ce_max(results, ce_max))
    grade = 0.45 * min(1.0, name_hits / 2.0) + (0.25 if path_hit else 0.0) + 0.30 * ce
    return max(0.0, min(1.0, grade))


def extract_cited_paths(answer: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for match in _CITED_PATH.finditer(answer or ""):
        path = normalize_path(match.group(1))
        if path and path not in seen:
            seen.add(path)
            found.append(path)
    return found


def citation_coverage(
    answer: Optional[str],
    retrieved_paths: Sequence[str],
    must_cite_paths: Optional[Sequence[str]] = None,
) -> float:
    """Answer-path overlap, or packed must-cite fraction when there is no answer."""
    retrieved = [normalize_path(p) for p in retrieved_paths or [] if p]
    must = [normalize_path(p) for p in (must_cite_paths or []) if p]
    if not answer:
        rate = citation_path_hit_rate(retrieved, must)
        return 0.0 if rate is None else float(rate)

    cited = extract_cited_paths(answer)
    cited_set = set(cited)
    retrieved_set = set(retrieved)
    overlap = {p for p in cited_set if any(
        p == r or p.endswith("/" + r) or r.endswith("/" + p) for r in retrieved_set
    )}
    denom_set = set(must) | cited_set
    denom = max(1, len(denom_set))
    return len(overlap) / denom


def should_stop(
    grade: float,
    coverage: float,
    attempt: int,
    max_loops: int,
    grade_threshold: float = 0.35,
    citation_threshold: float = 0.5,
) -> Tuple[bool, str]:
    """Stop on grade, citation coverage, or exhausted extra-retrieve budget."""
    allowed = min(max(int(max_loops), 0), MAX_EXTRA_LOOPS)
    if grade >= grade_threshold:
        return True, "grade"
    if coverage >= citation_threshold:
        return True, "citation"
    extras_used = max(0, int(attempt) - 1)
    if extras_used >= allowed:
        return True, "budget"
    return False, "retry"


def extract_focus_terms(query: str) -> List[str]:
    terms: List[str] = []
    for tick in _TICKED.findall(query or ""):
        if tick.strip():
            terms.append(tick.strip())
    terms.extend(_CAMEL.findall(query or ""))
    terms.extend(_SNAKE.findall(query or ""))
    terms.extend(_DOTTED.findall(query or ""))
    for path in _FILE_PATH.findall(query or ""):
        terms.append(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem:
            terms.append(stem)
    tokens = tokenize(query)
    if set(tokens) & CLI_HINTS:
        for tok in tokens:
            if tok in STOPWORDS or tok in CLI_HINTS or tok in PATH_EXTENSIONS:
                continue
            if len(tok) < 3:
                continue
            terms.append(f"cmd_{tok}")
            terms.append(f"{tok}_command")
    seen = set()
    ordered: List[str] = []
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(term)
    return ordered


def rewrite_query(query: str, attempt: int, mode: str) -> Tuple[str, str]:
    """Rule-based rewrite; HyDE is a retry *action* (embed-time), not new text."""
    focus = extract_focus_terms(query)
    if mode == "graph":
        rewritten = " ".join(focus) if focus else query
        return rewritten, "deepen_graph"
    if focus:
        rewritten = " ".join(focus)
        if rewritten.strip().lower() != (query or "").strip().lower():
            return rewritten, "rewrite"
    if attempt >= 2:
        return query, "hyde"
    return (focus and " ".join(focus) or query), "rewrite"


def loop_config_from_mapping(retrieval: Optional[Dict[str, Any]] = None) -> "LoopConfig":
    return LoopConfig.from_mapping(retrieval or {})


@dataclass
class LoopConfig:
    max_loops: int = 0
    grade_threshold: float = 0.35
    citation_threshold: float = 0.5
    verify: bool = False
    hyde_on_retry: bool = True
    deepen_neighbors: int = 8

    @classmethod
    def from_mapping(cls, retrieval: Optional[Dict[str, Any]] = None) -> "LoopConfig":
        data = retrieval or {}
        hyde = data.get("hyde") or {}
        return cls(
            max_loops=min(max(int(data.get("max_loops") or 0), 0), MAX_EXTRA_LOOPS),
            grade_threshold=float(data.get("grade_threshold", 0.35)),
            citation_threshold=float(data.get("citation_threshold", 0.5)),
            verify=bool(data.get("verify", False)),
            hyde_on_retry=bool(hyde.get("on_retry", True)),
            deepen_neighbors=int(data.get("deepen_neighbors") or 8),
        )


@dataclass
class LoopOutcome:
    results: List[Any]
    packed: Any
    attempts: int
    grade: float
    coverage: float
    action: str
    stop_reason: str
    traces: List[Dict[str, Any]] = field(default_factory=list)
    query_effective: str = ""
    mode: str = "hybrid"
    answer: Optional[str] = None
    verify: Optional[Dict[str, Any]] = None


def call_retrieve(retriever, query: str, top_k: int, debug: bool = True, **opts):
    try:
        return retriever.retrieve(query, top_k=top_k, debug=debug, **opts)
    except TypeError:
        return retriever.retrieve(query, top_k=top_k, debug=debug)


def merge_results(prior: Sequence[Any], incoming: Sequence[Any], top_k: int) -> List[Any]:
    ordered: List[Any] = []
    seen = set()
    for item in list(incoming or []) + list(prior or []):
        chunk = _chunk_of(item)
        cid = getattr(chunk, "id", None)
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ordered.append(item)
        if len(ordered) >= top_k:
            break
    return ordered


def _ids_of(results: Sequence[Any]) -> List[str]:
    ids = []
    for item in results or []:
        chunk = _chunk_of(item)
        cid = getattr(chunk, "id", None)
        if cid:
            ids.append(cid)
    return ids


def _trace_record(
    *,
    query: str,
    mode: str,
    attempt: int,
    results: Sequence[Any],
    packed,
    raw_trace: Dict[str, Any],
    grade: float,
    action: str,
) -> Dict[str, Any]:
    latencies = dict((raw_trace or {}).get("latencies_ms") or {})
    if packed is not None and getattr(packed, "mmr_latency_ms", None) is not None:
        latencies.setdefault("mmr", packed.mmr_latency_ms)
    return {
        "query": query,
        "mode": mode,
        "attempt": attempt,
        "dense_ids": _ids_of((raw_trace or {}).get("dense") or []),
        "bm25_ids": _ids_of((raw_trace or {}).get("sparse") or []),
        "graph_ids": _ids_of((raw_trace or {}).get("graph") or []),
        "fused_ids": _ids_of((raw_trace or {}).get("fused") or []),
        "reranked_ids": _ids_of((raw_trace or {}).get("reranked") or []),
        "packed_ids": list(getattr(packed, "packed_chunk_ids", []) or []),
        "grade": grade,
        "action": action,
        "ms": {
            "dense": latencies.get("dense"),
            "bm25": latencies.get("bm25"),
            "graph": latencies.get("graph"),
            "ce": latencies.get("ce"),
            "mmr": latencies.get("mmr"),
        },
        "latencies_ms": latencies,
        "dense": (raw_trace or {}).get("dense") or [],
        "sparse": (raw_trace or {}).get("sparse") or [],
        "graph": (raw_trace or {}).get("graph") or [],
        "fused": (raw_trace or {}).get("fused") or [],
        "reranked": (raw_trace or {}).get("reranked") or [],
    }


def parse_verify_payload(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        payload = json.loads(raw[start:end + 1] if start >= 0 and end > start else raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"supported": False, "missing_paths": [], "raw": raw}
    supported = bool(payload.get("supported"))
    missing = payload.get("missing_paths") or []
    if not isinstance(missing, list):
        missing = [str(missing)]
    return {"supported": supported, "missing_paths": [str(p) for p in missing]}


def verify_answer(llm, query: str, answer: str, packed_context: str) -> Dict[str, Any]:
    user = f"Query: {query}\nAnswer: {answer}"
    raw = llm.query(VERIFY_SYSTEM, packed_context, user)
    return parse_verify_payload(raw)


class QueryLoop:
    def __init__(self, retriever, context_builder, config: Optional[LoopConfig] = None):
        self.retriever = retriever
        self.context_builder = context_builder
        self.config = config or LoopConfig()

    def run(
        self,
        query: str,
        top_k: int = 10,
        must_cite_paths: Optional[Sequence[str]] = None,
        generate: Optional[Callable[[str, str, str], str]] = None,
        verifier: Optional[Callable[[str, str, str], Dict[str, Any]]] = None,
    ) -> LoopOutcome:
        max_extra = min(max(int(self.config.max_loops), 0), MAX_EXTRA_LOOPS)
        detected = detect_query_mode(query)
        first_mode = "hybrid" if max_extra == 0 else ("bm25" if detected == "bm25" else "hybrid")

        effective = query
        results: List[Any] = []
        traces: List[Dict[str, Any]] = []
        packed = None
        grade = 0.0
        coverage = 0.0
        action = "stop"
        stop_reason = "budget"
        used_hyde = False
        retrieve_mode = first_mode
        next_action = "stop"
        answer = None
        verify_payload = None

        for attempt in range(1, max_extra + 2):
            hyde = False
            expand = None
            retrieve_mode = first_mode if attempt == 1 else retrieve_mode
            if next_action == "deepen_graph":
                retrieve_mode = "hybrid"
                expand = self.config.deepen_neighbors
            elif next_action == "hyde":
                hyde = bool(self.config.hyde_on_retry)
                used_hyde = used_hyde or hyde
            elif next_action == "rewrite" and detected == "bm25":
                retrieve_mode = "bm25"
            elif attempt > 1 and detected == "graph":
                retrieve_mode = "hybrid"
                expand = self.config.deepen_neighbors

            raw = call_retrieve(
                self.retriever,
                effective,
                top_k,
                True,
                mode=retrieve_mode,
                hyde=hyde if hyde else None,
                expand_neighbors=expand,
            )
            if isinstance(raw, tuple):
                new_results, raw_trace = raw
            else:
                new_results, raw_trace = raw, {}
            results = merge_results(results, new_results, top_k)
            packed = self.context_builder.build_context_report(query, results)
            grade = heuristic_grade(
                query,
                results,
                ce_max=_infer_ce_max(new_results, None) or None,
            )
            coverage = citation_coverage(
                None,
                getattr(packed, "packed_paths", []) or [],
                must_cite_paths,
            )
            stop, reason = should_stop(
                grade,
                coverage,
                attempt,
                max_extra,
                self.config.grade_threshold,
                self.config.citation_threshold,
            )
            used_query = effective
            if stop:
                next_action = "stop"
                stop_reason = reason
                action = "stop"
            else:
                effective, next_action = rewrite_query(query, attempt, detected)
                if next_action == "hyde" and (used_hyde or not self.config.hyde_on_retry):
                    next_action = "rewrite"
                action = next_action
            traces.append(_trace_record(
                query=used_query,
                mode=retrieve_mode,
                attempt=attempt,
                results=results,
                packed=packed,
                raw_trace=raw_trace if isinstance(raw_trace, dict) else {},
                grade=grade,
                action=action,
            ))
            if stop:
                break

        if generate is not None and packed is not None:
            system = ""
            if hasattr(self.context_builder, "build_system_prompt"):
                system = self.context_builder.build_system_prompt() or ""
            answer = generate(system, packed.context, query)
            coverage = citation_coverage(
                answer,
                getattr(packed, "packed_paths", []) or [],
                must_cite_paths,
            )
            extras_used = max(0, len(traces) - 1)
            if (
                coverage < self.config.citation_threshold
                and extras_used < max_extra
                and grade < self.config.grade_threshold
            ):
                effective, next_action = rewrite_query(query, len(traces), detected)
                retrieve_mode = "hybrid" if next_action == "deepen_graph" else retrieve_mode
                raw = call_retrieve(
                    self.retriever,
                    effective,
                    top_k,
                    True,
                    mode=retrieve_mode,
                    hyde=(next_action == "hyde" and self.config.hyde_on_retry),
                    expand_neighbors=(
                        self.config.deepen_neighbors if next_action == "deepen_graph" else None
                    ),
                )
                if isinstance(raw, tuple):
                    new_results, raw_trace = raw
                else:
                    new_results, raw_trace = raw, {}
                results = merge_results(results, new_results, top_k)
                packed = self.context_builder.build_context_report(query, results)
                grade = heuristic_grade(query, results)
                traces.append(_trace_record(
                    query=effective,
                    mode=retrieve_mode,
                    attempt=len(traces) + 1,
                    results=results,
                    packed=packed,
                    raw_trace=raw_trace if isinstance(raw_trace, dict) else {},
                    grade=grade,
                    action="stop",
                ))
                answer = generate(system, packed.context, query)
                coverage = citation_coverage(
                    answer,
                    getattr(packed, "packed_paths", []) or [],
                    must_cite_paths,
                )
                stop_reason = "citation" if coverage >= self.config.citation_threshold else "budget"
                action = "stop"

        if self.config.verify and answer and packed is not None:
            packed_text = getattr(packed, "context", "") or ""
            if verifier is not None:
                verify_payload = verifier(query, answer, packed_text)
            else:
                verify_payload = None

        return LoopOutcome(
            results=results,
            packed=packed,
            attempts=len(traces) or 1,
            grade=grade,
            coverage=coverage,
            action=action,
            stop_reason=stop_reason,
            traces=traces,
            query_effective=effective,
            mode=first_mode if max_extra == 0 else detected,
            answer=answer,
            verify=verify_payload,
        )
