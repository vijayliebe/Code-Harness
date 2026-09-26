"""Local retrieval eval harness: golden suites, metrics, JSON reports."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .context_builder import ContextReport
from .metrics import (
    citation_path_hit_rate,
    cited_paths_hit,
    classify_failures,
    gold_ids_hit,
    match_gold_id,
    mean,
    median,
    ndcg_at_k,
    normalize_path,
    paths_in_order,
    recall_at_k,
)

__all__ = [
    "ContextReport",
    "EvalFixture",
    "load_suite",
    "run_eval",
    "write_report",
    "print_summary",
    "default_report_path",
]


@dataclass
class EvalFixture:
    id: str
    query: str
    relevant_chunk_ids: List[str] = field(default_factory=list)
    must_cite_paths: List[str] = field(default_factory=list)
    difficulty: str = "medium"
    completion_criteria: List[Dict[str, Any]] = field(default_factory=list)


def load_suite(path: str) -> Tuple[List[EvalFixture], Dict[str, Any]]:
    """Load a YAML or JSON golden suite.

    Accepted shapes:
      - a list of fixture objects
      - a mapping `{suite, k, fixtures: [...]}`
    Each fixture: `{id, query, relevant_chunk_ids[], must_cite_paths[],
    difficulty, optional completion_criteria[]}`.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Eval suite not found: {path}")

    with open(path) as fh:
        if path.endswith((".yaml", ".yml")):
            import yaml
            raw = yaml.safe_load(fh)
        else:
            raw = json.load(fh)

    suite_name = os.path.splitext(os.path.basename(path))[0]
    default_k = None
    items: List[Any]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        suite_name = raw.get("suite") or suite_name
        default_k = raw.get("k")
        items = raw.get("fixtures") or raw.get("cases") or []
    else:
        raise ValueError(f"Suite must be a list or mapping, got {type(raw).__name__}")

    fixtures = [_parse_fixture(item, idx) for idx, item in enumerate(items)]
    if not fixtures:
        raise ValueError(f"Suite {path} contains no fixtures")
    meta = {"suite": suite_name, "k": default_k, "path": os.path.abspath(path)}
    return fixtures, meta


def _parse_fixture(item: Any, idx: int) -> EvalFixture:
    if not isinstance(item, dict):
        raise ValueError(f"Fixture {idx} must be an object")
    fixture_id = item.get("id")
    query = item.get("query")
    if not fixture_id or not query:
        raise ValueError(f"Fixture {idx} requires 'id' and 'query'")
    difficulty = item.get("difficulty") or "medium"
    if difficulty not in ("easy", "medium", "hard"):
        raise ValueError(f"Fixture {fixture_id}: difficulty must be easy|medium|hard")
    raw_criteria = item.get("completion_criteria") or item.get("verify") or []
    criteria: List[Dict[str, Any]] = []
    if isinstance(raw_criteria, list):
        criteria = [dict(row) for row in raw_criteria if isinstance(row, dict)]
    return EvalFixture(
        id=str(fixture_id),
        query=str(query),
        relevant_chunk_ids=[str(x) for x in (item.get("relevant_chunk_ids") or [])],
        must_cite_paths=[normalize_path(str(x)) for x in (item.get("must_cite_paths") or [])],
        difficulty=difficulty,
        completion_criteria=criteria,
    )


def default_report_path(suite_name: str, now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in suite_name).strip("-") or "suite"
    return os.path.join(".code-harness", "eval", f"{safe}-{stamp}.json")


def write_report(report: Dict[str, Any], dest: Optional[str] = None) -> str:
    path = dest or default_report_path(report.get("suite") or "suite")
    if path.endswith(os.sep) or (os.path.isdir(path) if os.path.exists(path) else False):
        path = os.path.join(path, os.path.basename(default_report_path(report.get("suite") or "suite")))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    return path


def _result_ids(results: Sequence) -> List[str]:
    ids = []
    for item in results or []:
        chunk = getattr(item, "chunk", item)
        cid = getattr(chunk, "id", None)
        if cid:
            ids.append(cid)
    return ids


def _result_paths(results: Sequence) -> List[str]:
    chunks = []
    for item in results or []:
        chunks.append(getattr(item, "chunk", item))
    return paths_in_order(chunks)


def _chunks_of(results: Sequence):
    return [getattr(item, "chunk", item) for item in (results or [])]


def _ranked_for_metrics(results: Sequence, gold_ids: Sequence[str], must_paths: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Return (retrieved_labels, relevant_labels) for recall/nDCG."""
    if gold_ids:
        retrieved = []
        seen_gold: set = set()
        for chunk in _chunks_of(results):
            matched = None
            for gold in gold_ids:
                if gold in seen_gold:
                    continue
                if match_gold_id(chunk.id, getattr(chunk, "entity_id", None), gold):
                    matched = gold
                    break
            if matched:
                retrieved.append(matched)
                seen_gold.add(matched)
            else:
                retrieved.append(chunk.id)
        return retrieved, list(gold_ids)
    return paths_in_order(_chunks_of(results)), [normalize_path(p) for p in must_paths]


def _union_stage(traces: Sequence[Dict[str, Any]], key: str) -> List[Any]:
    seen = set()
    merged: List[Any] = []
    for trace in traces or []:
        for item in trace.get(key) or []:
            chunk = getattr(item, "chunk", item)
            cid = getattr(chunk, "id", id(item))
            if cid in seen:
                continue
            seen.add(cid)
            merged.append(item)
    return merged


def run_eval(
    fixtures: Sequence[EvalFixture],
    retriever,
    context_builder,
    k: int,
    suite_name: str,
    suite_path: str,
    repo_path: str,
    repo_name: str,
    config_snapshot: Optional[Dict[str, Any]] = None,
    loop_config=None,
    cache=None,
    config=None,
    verify=None,
) -> Dict[str, Any]:
    from .loop import LoopConfig, QueryLoop
    from .query_cache import run_with_query_cache

    cases: List[Dict[str, Any]] = []
    ce_enabled = bool(getattr(retriever, "ce_enabled", False))
    from .decision import build_decision_client

    loop = QueryLoop(
        retriever,
        context_builder,
        loop_config or LoopConfig(max_loops=0),
        decider=build_decision_client(config),
    )
    eval_config = config
    if verify is None:
        verify = bool((getattr(eval_config, "session", None) or {}).get("verify"))
    verify = bool(verify)

    for fixture in fixtures:
        if cache is not None and eval_config is not None:
            outcome = run_with_query_cache(
                loop,
                fixture.query,
                top_k=k,
                cache=cache,
                config=eval_config,
                repo_name=repo_name,
                repo_path=repo_path,
                must_cite_paths=fixture.must_cite_paths,
            )
        else:
            outcome = loop.run(
                fixture.query,
                top_k=k,
                must_cite_paths=fixture.must_cite_paths,
            )
        results = outcome.results
        packed = outcome.packed
        traces = outcome.traces or []
        last = traces[-1] if traces else {}
        latencies = dict(last.get("latencies_ms") or {})
        if packed is not None and packed.mmr_latency_ms is not None:
            latencies["mmr"] = packed.mmr_latency_ms
        for extra in traces[:-1]:
            extra_ms = extra.get("latencies_ms") or {}
            for key, value in extra_ms.items():
                if value is None:
                    continue
                latencies[key] = latencies.get(key, 0.0) + float(value)

        dense = _union_stage(traces, "dense")
        sparse = _union_stage(traces, "sparse")
        graph = _union_stage(traces, "graph")
        fused = _union_stage(traces, "fused")
        reranked = _union_stage(traces, "reranked")
        if packed is None:
            packed = context_builder.build_context_report(fixture.query, results)

        ranked, relevant = _ranked_for_metrics(
            results, fixture.relevant_chunk_ids, fixture.must_cite_paths
        )
        recall = recall_at_k(ranked, relevant, k)
        ndcg = ndcg_at_k(ranked, relevant, k)
        cite = citation_path_hit_rate(packed.packed_paths, fixture.must_cite_paths)
        failures = classify_failures(
            gold_ids=fixture.relevant_chunk_ids,
            must_cite_paths=fixture.must_cite_paths,
            dense_ids=_result_ids(dense),
            bm25_ids=_result_ids(sparse),
            graph_ids=_result_ids(graph),
            fused_ids=_result_ids(fused),
            final_ids=_result_ids(results),
            packed_ids=packed.packed_chunk_ids,
            packed_paths=packed.packed_paths,
            rerank_enabled=ce_enabled,
            dense_paths=_result_paths(dense),
            bm25_paths=_result_paths(sparse),
            graph_paths=_result_paths(graph),
            fused_paths=_result_paths(fused),
            final_paths=_result_paths(results),
        )

        gold_hit = gold_ids_hit(_chunks_of(results), fixture.relevant_chunk_ids)
        case = {
            "id": fixture.id,
            "query": fixture.query,
            "difficulty": fixture.difficulty,
            "recall_at_k": recall,
            "ndcg_at_k": ndcg,
            "citation_path_hit_rate": cite,
            "citation_path_full_hit": (
                cite == 1.0 if cite is not None else None
            ),
            "cited_paths": packed.packed_paths,
            "must_cite_hit": sorted(cited_paths_hit(packed.packed_paths, fixture.must_cite_paths)),
            "relevant_hit": sorted(gold_hit),
            "retrieved_chunk_ids": _result_ids(results),
            "packed_chunk_ids": list(packed.packed_chunk_ids),
            "prompt_tokens": packed.prompt_tokens,
            "prompt_tokens_full": getattr(packed, "prompt_tokens_full", 0) or packed.prompt_tokens,
            "prompt_tokens_packed": getattr(packed, "prompt_tokens_packed", 0) or packed.prompt_tokens,
            "pack_mode": getattr(packed, "pack_mode", None) or "full",
            "prefix_hash": getattr(packed, "prefix_hash", "") or "",
            "latencies_ms": latencies,
            "failures": failures,
            "loop": {
                "attempts": outcome.attempts,
                "grade": outcome.grade,
                "coverage": outcome.coverage,
                "stop_reason": outcome.stop_reason,
                "action": outcome.action,
                "mode": outcome.mode,
                "queries": [t.get("query") for t in traces],
            },
            "cached": bool(getattr(outcome, "cached", False)),
            "trace": {
                "query": fixture.query,
                "mode": last.get("mode") or outcome.mode or "hybrid",
                "attempt": outcome.attempts,
                "dense_ids": _result_ids(dense),
                "bm25_ids": _result_ids(sparse),
                "graph_ids": _result_ids(graph),
                "fused_ids": _result_ids(fused),
                "reranked_ids": _result_ids(reranked),
                "packed_ids": list(packed.packed_chunk_ids),
                "grade": outcome.grade,
                "action": last.get("action") or outcome.action or "stop",
                "ms": {
                    "dense": latencies.get("dense"),
                    "bm25": latencies.get("bm25"),
                    "graph": latencies.get("graph"),
                    "ce": latencies.get("ce"),
                    "mmr": latencies.get("mmr"),
                },
            },
        }
        if verify:
            from .verify import run_fixture_verify

            evidence = {
                "coverage": outcome.coverage,
                "grade": outcome.grade,
                "paths": list(packed.packed_paths or []),
                "chunk_ids": list(packed.packed_chunk_ids or []),
            }
            verify_result = run_fixture_verify(
                fixture.completion_criteria,
                enabled=True,
                cwd=repo_path,
                evidence=evidence,
            )
            case["verify"] = verify_result.to_dict()
        cases.append(case)

    metrics = _aggregate(cases, k)
    if cache is not None:
        metrics["query_cache"] = {
            "enabled": True,
            "hits": int(getattr(cache, "hits", 0) or 0),
            "misses": int(getattr(cache, "misses", 0) or 0),
        }
    return {
        "suite": suite_name,
        "suite_path": os.path.abspath(suite_path) if suite_path else "",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": os.path.abspath(repo_path),
        "repo_name": repo_name,
        "k": k,
        "config": config_snapshot or {},
        "metrics": metrics,
        "cases": cases,
    }


def _aggregate(cases: Sequence[Dict[str, Any]], k: int) -> Dict[str, Any]:
    recalls = [c["recall_at_k"] for c in cases if c["recall_at_k"] is not None]
    ndcgs = [c["ndcg_at_k"] for c in cases if c["ndcg_at_k"] is not None]
    cites = [c["citation_path_hit_rate"] for c in cases if c["citation_path_hit_rate"] is not None]
    tokens = [c["prompt_tokens"] for c in cases]
    tokens_full = [c.get("prompt_tokens_full") or c["prompt_tokens"] for c in cases]
    tokens_packed = [c.get("prompt_tokens_packed") or c["prompt_tokens"] for c in cases]
    full_hits = [1.0 if c["citation_path_full_hit"] else 0.0 for c in cases if c["citation_path_full_hit"] is not None]

    drop = None
    if tokens_full and sum(tokens_full) > 0:
        drop = round(1.0 - (mean(tokens_packed) / mean(tokens_full)), 4)

    latency_keys = set()
    for case in cases:
        latency_keys.update(case.get("latencies_ms") or {})
    latencies: Dict[str, Dict[str, float]] = {}
    for key in sorted(latency_keys):
        vals = [float(case["latencies_ms"][key]) for case in cases if key in (case.get("latencies_ms") or {})]
        latencies[key] = {"mean": round(mean(vals), 3), "p50": round(median(vals), 3)}

    failure_counts: Dict[str, int] = {}
    for case in cases:
        for label in case.get("failures") or []:
            failure_counts[label] = failure_counts.get(label, 0) + 1

    attempts = [float((c.get("loop") or {}).get("attempts") or 1) for c in cases]

    def _subset_metrics(subset: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        sub_recalls = [c["recall_at_k"] for c in subset if c["recall_at_k"] is not None]
        sub_ndcgs = [c["ndcg_at_k"] for c in subset if c["ndcg_at_k"] is not None]
        sub_cites = [c["citation_path_hit_rate"] for c in subset if c["citation_path_hit_rate"] is not None]
        sub_tokens = [c.get("prompt_tokens") for c in subset if c.get("prompt_tokens") is not None]
        sub_attempts = [float((c.get("loop") or {}).get("attempts") or 1) for c in subset]
        return {
            "n": len(subset),
            "recall_at_k": None if not sub_recalls else round(mean(sub_recalls), 4),
            "ndcg_at_k": None if not sub_ndcgs else round(mean(sub_ndcgs), 4),
            "citation_path_hit_rate": None if not sub_cites else round(mean(sub_cites), 4),
            "prompt_tokens_mean": None if not sub_tokens else round(mean(sub_tokens), 1),
            "loop_attempts_mean": None if not sub_attempts else round(mean(sub_attempts), 3),
        }

    by_difficulty: Dict[str, Any] = {}
    for difficulty in ("easy", "medium", "hard"):
        subset = [c for c in cases if c.get("difficulty") == difficulty]
        if subset:
            by_difficulty[difficulty] = _subset_metrics(subset)

    metrics = {
        "k": k,
        "n": len(cases),
        "recall_at_k": None if not recalls else round(mean(recalls), 4),
        "ndcg_at_k": None if not ndcgs else round(mean(ndcgs), 4),
        "citation_path_hit_rate": None if not cites else round(mean(cites), 4),
        "citation_path_full_hit_rate": None if not full_hits else round(mean(full_hits), 4),
        "prompt_tokens_mean": None if not tokens else round(mean(tokens), 1),
        "prompt_tokens_full_mean": None if not tokens_full else round(mean(tokens_full), 1),
        "prompt_tokens_packed_mean": None if not tokens_packed else round(mean(tokens_packed), 1),
        "prompt_token_drop": drop,
        "latencies_ms": latencies,
        "failure_counts": failure_counts,
        "loop_attempts_mean": None if not attempts else round(mean(attempts), 3),
        "by_difficulty": by_difficulty,
    }
    verify_rows = [
        c.get("verify")
        for c in cases
        if isinstance(c.get("verify"), dict) and not c["verify"].get("skipped")
    ]
    if verify_rows:
        metrics["verify_n"] = len(verify_rows)
        metrics["verify_pass_rate"] = round(
            mean([1.0 if row.get("passed") else 0.0 for row in verify_rows]),
            4,
        )
    return metrics


def print_summary(report: Dict[str, Any]) -> None:
    metrics = report.get("metrics") or {}
    k = report.get("k")
    print()
    print(f"Suite: {report.get('suite')}   k={k}   cases={metrics.get('n', 0)}")
    print(
        f"  Recall@{k}: {metrics.get('recall_at_k')}   "
        f"nDCG@{k}: {metrics.get('ndcg_at_k')}   "
        f"cite: {metrics.get('citation_path_hit_rate')}   "
        f"tokens: {metrics.get('prompt_tokens_mean')}   "
        f"full: {metrics.get('prompt_tokens_full_mean')}   "
        f"packed: {metrics.get('prompt_tokens_packed_mean')}   "
        f"drop: {metrics.get('prompt_token_drop')}"
    )
    latencies = metrics.get("latencies_ms") or {}
    if latencies:
        parts = [f"{name}={vals.get('mean')}ms" for name, vals in latencies.items()]
        print("  latency mean: " + "  ".join(parts))
    failures = metrics.get("failure_counts") or {}
    if failures:
        print("  failures: " + ", ".join(f"{k}={v}" for k, v in sorted(failures.items())))
    by_diff = metrics.get("by_difficulty") or {}
    if by_diff:
        parts = []
        for name in ("easy", "medium", "hard"):
            row = by_diff.get(name)
            if not row:
                continue
            parts.append(
                f"{name}: R={row.get('recall_at_k')} cite={row.get('citation_path_hit_rate')} "
                f"tok={row.get('prompt_tokens_mean')} loops={row.get('loop_attempts_mean')}"
            )
        if parts:
            print("  by difficulty: " + "  |  ".join(parts))
    if metrics.get("loop_attempts_mean") is not None:
        print(f"  loop attempts mean: {metrics.get('loop_attempts_mean')}")
    if metrics.get("verify_pass_rate") is not None:
        print(
            f"  verify pass rate: {metrics.get('verify_pass_rate')} "
            f"(n={metrics.get('verify_n', 0)})"
        )
    qc = metrics.get("query_cache") or {}
    if qc.get("enabled"):
        print(f"  query cache: hits={qc.get('hits', 0)} misses={qc.get('misses', 0)}")
    print()
    header = f"{'id':<28} {'R@k':>6} {'nDCG':>6} {'cite':>6} {'tok':>6}  failures"
    print(header)
    print("-" * len(header))
    for case in report.get("cases") or []:
        def _fmt(val):
            return f"{val:.3f}" if isinstance(val, float) else "  n/a"

        fails = ",".join(case.get("failures") or []) or "—"
        print(
            f"{case['id']:<28} {_fmt(case.get('recall_at_k')):>6} "
            f"{_fmt(case.get('ndcg_at_k')):>6} {_fmt(case.get('citation_path_hit_rate')):>6} "
            f"{str(case.get('prompt_tokens')):>6}  {fails}"
        )
    print()
