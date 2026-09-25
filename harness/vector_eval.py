"""A/B Recall@k / nDCG@k comparison and experimental-backend gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .vector_store import BackendUnavailable, normalize_backend_name

DEFAULT_RELATIVE_TOLERANCE = 0.05
# Fusion deep-dive gates (absolute point drop vs Chroma).
ABS_RECALL_POINT_GATES = {10: 0.02, 30: 0.01}


@dataclass
class BackendCompareResult:
    k: int
    baseline_name: str
    candidate_name: str
    baseline_metrics: Dict[str, Any] = field(default_factory=dict)
    candidate_metrics: Dict[str, Any] = field(default_factory=dict)
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE
    gate_failures: List[str] = field(default_factory=list)
    passed: bool = True
    forced: bool = False
    skipped: bool = False
    skip_reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "k": self.k,
            "baseline": self.baseline_name,
            "candidate": self.candidate_name,
            "baseline_metrics": self.baseline_metrics,
            "candidate_metrics": self.candidate_metrics,
            "relative_tolerance": self.relative_tolerance,
            "gate_failures": list(self.gate_failures),
            "passed": self.passed,
            "forced": self.forced,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


def _metrics(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not report:
        return {}
    metrics = dict(report.get("metrics") or {})
    dense = ((metrics.get("latencies_ms") or {}).get("dense") or {})
    return {
        "recall_at_k": metrics.get("recall_at_k"),
        "ndcg_at_k": metrics.get("ndcg_at_k"),
        "citation_path_hit_rate": metrics.get("citation_path_hit_rate"),
        "dense_p50": dense.get("p50"),
        "n": metrics.get("n"),
    }


def compare_backend_reports(
    baseline: Optional[Dict[str, Any]],
    candidate: Optional[Dict[str, Any]],
    *,
    baseline_name: str = "chromadb",
    candidate_name: str = "turbovec",
    k: int = 10,
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
    force_experimental: bool = False,
    skip_reason: str = "",
) -> BackendCompareResult:
    if candidate is None:
        return BackendCompareResult(
            k=k,
            baseline_name=baseline_name,
            candidate_name=candidate_name,
            baseline_metrics=_metrics(baseline),
            relative_tolerance=relative_tolerance,
            passed=True,
            forced=force_experimental,
            skipped=True,
            skip_reason=skip_reason or "candidate backend skipped",
        )

    base_m = _metrics(baseline)
    cand_m = _metrics(candidate)
    failures: List[str] = []

    for metric, label in (("recall_at_k", "Recall"), ("ndcg_at_k", "nDCG")):
        base = base_m.get(metric)
        cand = cand_m.get(metric)
        if base is None or cand is None:
            continue
        base_f = float(base)
        cand_f = float(cand)
        if base_f <= 0:
            continue
        rel = (base_f - cand_f) / base_f
        if rel > relative_tolerance + 1e-12:
            failures.append(
                f"{label}@{k} {cand_f:.4f} is {rel:.1%} below chromadb {base_f:.4f} "
                f"(relative tolerance {relative_tolerance:.0%})"
            )

    abs_limit = ABS_RECALL_POINT_GATES.get(int(k))
    if abs_limit is not None:
        base = base_m.get("recall_at_k")
        cand = cand_m.get("recall_at_k")
        if base is not None and cand is not None:
            drop = float(base) - float(cand)
            if drop > abs_limit + 1e-12:
                failures.append(
                    f"Recall@{k} drop {drop:.3f} exceeds {abs_limit:.2f} points vs chromadb"
                )

    passed = (not failures) or force_experimental
    return BackendCompareResult(
        k=k,
        baseline_name=baseline_name,
        candidate_name=candidate_name,
        baseline_metrics=base_m,
        candidate_metrics=cand_m,
        relative_tolerance=relative_tolerance,
        gate_failures=failures,
        passed=passed,
        forced=force_experimental,
        skipped=False,
    )


def gate_exit_code(result: BackendCompareResult, selected: str) -> int:
    try:
        kind = normalize_backend_name(selected)
    except ValueError:
        kind = (selected or "").strip().lower()
    if kind != "turbovec":
        return 0
    if result.forced or result.skipped:
        return 0
    return 0 if result.passed else 2


def probe_backend(name: str, config=None) -> tuple[bool, str]:
    """Return (available, reason). Does not construct a Chroma client."""
    try:
        kind = normalize_backend_name(name)
    except ValueError as exc:
        return False, str(exc)
    extra = {}
    if config is not None:
        extra = (getattr(config, "vector_store", None) or {}).get("turbovec") or {}
    if kind == "turbovec":
        if extra.get("use_stub"):
            return True, "turbovec-stub (exact cosine; not TurboQuant)"
        try:
            import turbovec  # noqa: F401
        except ImportError:
            return False, "turbovec is not installed; pip install -r requirements-turbovec.txt"
        return True, ""
    if kind in ("chromadb", "stub"):
        return True, ""
    return False, f"unknown backend {name}"


def print_backend_comparison(result: BackendCompareResult) -> None:
    k = result.k
    print()
    print(f"Vector backend A/B  k={k}  baseline={result.baseline_name}  candidate={result.candidate_name}")
    header = f"{'backend':<18} {'Recall@'+str(k):>10} {'nDCG@'+str(k):>10} {'dense p50':>10}"
    print(header)
    print("-" * len(header))

    def _row(name: str, metrics: Dict[str, Any]) -> None:
        rec = metrics.get("recall_at_k")
        ndcg = metrics.get("ndcg_at_k")
        p50 = metrics.get("dense_p50")
        rec_s = "n/a" if rec is None else f"{rec:.4f}"
        ndcg_s = "n/a" if ndcg is None else f"{ndcg:.4f}"
        p50_s = "n/a" if p50 is None else f"{p50:.1f}ms"
        print(f"{name:<18} {rec_s:>10} {ndcg_s:>10} {p50_s:>10}")

    if result.baseline_metrics:
        _row(result.baseline_name, result.baseline_metrics)
    else:
        print(f"{result.baseline_name:<18}        n/a        n/a        n/a")
    if result.skipped:
        print(f"{result.candidate_name:<18}     SKIP     SKIP     SKIP")
        print(f"  skip: {result.skip_reason}")
    else:
        _row(result.candidate_name, result.candidate_metrics)
    if result.forced and result.gate_failures:
        print("  gate: FORCED (--force-experimental); would fail:")
        for item in result.gate_failures:
            print(f"    - {item}")
    elif result.gate_failures:
        print("  gate: FAIL")
        for item in result.gate_failures:
            print(f"    - {item}")
    elif result.skipped:
        print("  gate: skipped (no candidate numbers)")
    else:
        print("  gate: PASS")
    print()


def parse_backend_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    names = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        names.append(normalize_backend_name(part))
    return names


def selected_backend_name(config) -> str:
    return normalize_backend_name((getattr(config, "vector_store", None) or {}).get("type", "chromadb"))
