"""A/B Recall@k / nDCG@k comparison and experimental-backend gate."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .vector_store import normalize_backend_name

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
    if kind == "chromadb":
        try:
            import chromadb  # noqa: F401
        except ImportError:
            return False, "chromadb is not installed; pip install chromadb"
        return True, ""
    if kind == "stub":
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


def default_compare_json_path(suite_name: str, now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in suite_name).strip("-") or "suite"
    return os.path.join(".code-harness", "eval", f"{safe}-ab-{stamp}.json")


def _fmt_metric(value: Any, *, kind: str = "score") -> str:
    if value is None:
        return "n/a"
    if kind == "ms":
        return f"{float(value):.1f}ms"
    if kind == "int":
        return str(int(value))
    return f"{float(value):.4f}"


def gate_status_label(result: BackendCompareResult) -> str:
    if result.skipped:
        return "SKIP"
    if result.forced and result.gate_failures:
        return "FORCED"
    if result.gate_failures:
        return "FAIL"
    return "PASS"


def render_compare_markdown(
    result: BackendCompareResult,
    *,
    suite: str = "code-harness",
    suite_path: str = ".docs/research/eval/code-harness.fixture.yaml",
    generated: Optional[str] = None,
    notes: str = "",
    skips: Optional[Dict[str, str]] = None,
    kind: str = "vector-backend-ab",
) -> str:
    """Persistable fixture A/B table (Recall@k / nDCG@k / latency)."""
    generated = generated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    k = result.k
    status = gate_status_label(result)
    skips = skips or {}
    skip_reason = result.skip_reason or skips.get(result.candidate_name, "")
    note_lines = []
    if notes:
        note_lines.append(notes)
    if skip_reason:
        note_lines.append(skip_reason)
    notes_cell = "<br>".join(note_lines) if note_lines else "—"

    def row(name: str, metrics: Dict[str, Any], skipped: bool = False) -> str:
        if skipped:
            return f"| {name} | SKIP | SKIP | SKIP | SKIP | — |"
        return (
            f"| {name} "
            f"| {_fmt_metric(metrics.get('recall_at_k'))} "
            f"| {_fmt_metric(metrics.get('ndcg_at_k'))} "
            f"| {_fmt_metric(metrics.get('citation_path_hit_rate'))} "
            f"| {_fmt_metric(metrics.get('dense_p50'), kind='ms')} "
            f"| {_fmt_metric(metrics.get('n'), kind='int')} |"
        )

    gate_lines = [
        f"- relative tolerance: {result.relative_tolerance:.0%}",
        "- Recall@10 absolute: −2 pts; Recall@30 absolute: −1 pt",
        f"- status: **{status}**",
    ]
    if result.gate_failures:
        for item in result.gate_failures:
            gate_lines.append(f"- {item}")
    if skip_reason:
        gate_lines.append(f"- skip: {skip_reason}")

    axis = "embedder" if kind == "embedder-ab" else "backend"
    if kind == "embedder-ab":
        title = "MiniLM vs Jina-code fixture A/B"
        intro = (
            "Committed retrieval A/B on the project fixture suite. "
            "jina-embeddings-v2-base-code stays an optional local candidate; "
            "this table is the honesty record, not a default flip."
        )
        how_to = """```bash
# Core retrieve stack (Chroma + default MiniLM)
pip install -r requirements.txt

# Routine recipe: index isolated Chroma dirs, compare, write this file
python main.py eval-ab . --compare-embedders all-MiniLM-L6-v2,jina-embeddings-v2-base-code
# or
make eval-ab-embed

# Same recipe, one embedder at a time
python main.py index .
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml
python main.py index . --embed-model jina-embeddings-v2-base-code
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml \\
  --embed-model jina-embeddings-v2-base-code
```

Make target: `make eval-ab-embed`. Script: `python scripts/eval_minilm_vs_jina.py`.

`all-MiniLM-L6-v2` is 384-d, small, and fast (production default).
`jina-embeddings-v2-base-code` is 768-d, code-specialized, slower to
download/load (~161M params), and uses a separate Chroma persist dir
because dimensions cannot share a collection. CI and default unittest
skip the live Jina column when the weights are absent (exit 0).
Selecting `--embed-model jina-embeddings-v2-base-code` while the model
cannot be loaded fails clearly (exit 1).

## Policy

Default embedder remains `all-MiniLM-L6-v2`. Default dense store remains
Chroma. Do not flip either until this table plus a larger fixture set
justify it. `decision.enabled` stays false.
"""
    else:
        title = "TurboVec vs Chroma fixture A/B"
        intro = (
            "Committed retrieval A/B on the project fixture suite. TurboVec stays\n"
            "experimental; this table is the honesty record, not a default flip."
        )
        how_to = """```bash
# Core retrieve stack (Chroma + local embedder)
pip install -r requirements.txt

# Optional TurboVec extra (Rust wheel). Unittest stays green without it.
pip install -r requirements-turbovec.txt

# Routine recipe: index both persist dirs, compare, write this file
python main.py eval-ab .

# Equivalent explicit commands
python main.py index .
python main.py index . --vector-backend turbovec
python main.py eval . --suite .docs/research/eval/code-harness.fixture.yaml \\
  --compare-backends chromadb,turbovec \\
  --compare-markdown .docs/research/eval/RESULTS.md \\
  --compare-output .docs/research/eval/RESULTS.json
```

Make target: `make eval-ab`. Script: `python scripts/eval_chroma_vs_turbovec.py`.

Run this on a machine that can install `turbovec` plus the local embedder
(for example the developer workstation that owns this checkout). CI and
default unittest skip the live TurboVec column when the extra is absent
(exit 0). Selecting `--vector-backend turbovec` while the wheel is missing
fails clearly (exit 1). A failed recall gate still exits 2 unless
`--force-experimental`.

## Policy

Default dense store remains Chroma. Dual-write, TQ+ calibrate, and flipping
the default are later work. Do not treat a skipped or placeholder row as
TurboQuant recall.
"""

    return f"""# {title}

{intro}

| Field | Value |
|-------|-------|
| Suite | `{suite}` |
| Suite path | `{suite_path}` |
| k | {k} |
| Generated | {generated} |
| Gate | {status} |
| Notes | {notes_cell} |

## Metrics

| {axis} | Recall@{k} | nDCG@{k} | citation-path | dense p50 | n |
|---------|------------|----------|---------------|-----------|---|
{row(result.baseline_name, result.baseline_metrics)}
{row(result.candidate_name, result.candidate_metrics, skipped=result.skipped)}

## Gate

{chr(10).join(gate_lines)}

## How to run (local)

{how_to}"""


def compare_artifact_dict(
    result: BackendCompareResult,
    *,
    suite: str = "code-harness",
    suite_path: str = ".docs/research/eval/code-harness.fixture.yaml",
    generated: Optional[str] = None,
    reports: Optional[Dict[str, Any]] = None,
    skips: Optional[Dict[str, str]] = None,
    notes: str = "",
    kind: str = "vector-backend-ab",
) -> Dict[str, Any]:
    generated = generated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slim_reports = {}
    for name, report in (reports or {}).items():
        slim_reports[name] = {
            "suite": (report or {}).get("suite"),
            "k": (report or {}).get("k"),
            "metrics": (report or {}).get("metrics") or {},
        }
    return {
        "kind": kind,
        "suite": suite,
        "suite_path": suite_path,
        "generated": generated,
        "notes": notes,
        "compare": result.as_dict(),
        "reports": slim_reports,
        "skips": dict(skips or {}),
    }


def write_compare_artifacts(
    result: BackendCompareResult,
    *,
    json_path: Optional[str] = None,
    markdown_path: Optional[str] = None,
    suite: str = "code-harness",
    suite_path: str = ".docs/research/eval/code-harness.fixture.yaml",
    generated: Optional[str] = None,
    reports: Optional[Dict[str, Any]] = None,
    skips: Optional[Dict[str, str]] = None,
    notes: str = "",
    kind: str = "vector-backend-ab",
) -> Dict[str, str]:
    """Write JSON and/or markdown A/B artifacts. Missing dests are skipped."""
    generated = generated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    written: Dict[str, str] = {}
    if json_path:
        parent = os.path.dirname(json_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(json_path, "w") as fh:
            json.dump(
                compare_artifact_dict(
                    result,
                    suite=suite,
                    suite_path=suite_path,
                    generated=generated,
                    reports=reports,
                    skips=skips,
                    notes=notes,
                    kind=kind,
                ),
                fh,
                indent=2,
            )
            fh.write("\n")
        written["json"] = json_path
    if markdown_path:
        parent = os.path.dirname(markdown_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(markdown_path, "w") as fh:
            fh.write(
                render_compare_markdown(
                    result,
                    suite=suite,
                    suite_path=suite_path,
                    generated=generated,
                    notes=notes,
                    skips=skips,
                    kind=kind,
                )
            )
        written["markdown"] = markdown_path
    return written
