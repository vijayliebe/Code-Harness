"""Harness-on vs harness-off ablation over the golden retrieval suite.

Eval-only. The default query path is unchanged: this module clones config,
turns one retrieve/pack stage off (or on, when that stage is already off
by default), and scores the same fixtures. ``no_retrieval`` is a baseline
that returns empty hits so Recall@k / nDCG@k / citation-path are zero.

HyDE and CCR-lite packing are off in the default full harness. Leave-one-out
of those would be a no-op, so the table includes the *on* variants
(``hyde``, ``ccr_lite``) instead — the "where relevant" contrast.
"""

from __future__ import annotations

import copy
import os
import platform
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from .config import DEFAULT_CONFIG, Config

DEFAULT_SUITE = ".docs/research/eval/code-harness.fixture.yaml"
COMMITTED_ABLATION_MD = ".docs/research/eval/RESULTS-ablation.md"
COMMITTED_ABLATION_JSON = ".docs/research/eval/RESULTS-ablation.json"

EvalFn = Callable[..., Any]


@dataclass(frozen=True)
class AblationVariant:
    """One leave-one-out (or optional-on) retrieve/pack setting."""

    id: str
    label: str
    kind: str  # baseline | leave_one_out | optional_on | baseline_off
    notes: str = ""


ABLATION_VARIANTS: List[AblationVariant] = [
    AblationVariant(
        "full",
        "Full harness",
        "baseline",
        "Default retrieve+pack: dense + BM25 + graph expand + MMR; "
        "cross-encoder off; HyDE off; pack_mode=full.",
    ),
    AblationVariant("no_dense", "Dense off", "leave_one_out", "Skip dense search; dense_weight=0."),
    AblationVariant("no_bm25", "BM25 off", "leave_one_out", "Skip BM25 search; sparse_weight=0."),
    AblationVariant(
        "no_graph",
        "Graph expansion off",
        "leave_one_out",
        "Skip neighbor expansion; expand_neighbors=0; graph_weight=0.",
    ),
    AblationVariant(
        "no_rerank",
        "Cross-encoder off",
        "leave_one_out",
        "retrieval.cross_encoder.enabled=false.",
    ),
    AblationVariant("no_mmr", "MMR off", "leave_one_out", "Skip diversity rerank after lexical boost."),
    AblationVariant(
        "hyde",
        "HyDE template on",
        "optional_on",
        "Default HyDE is off; this row enables the local template (no LLM).",
    ),
    AblationVariant(
        "ccr_lite",
        "CCR-lite packing",
        "optional_on",
        "Default pack_mode is full; this row packs signatures + key spans.",
    ),
    AblationVariant(
        "no_retrieval",
        "No-retrieval baseline",
        "baseline_off",
        "Empty hit set. Retrieval recall/nDCG/citation-path are zero by construction.",
    ),
]


def ablation_variant_ids() -> List[str]:
    return [v.id for v in ABLATION_VARIANTS]


def clone_config(config: Optional[Config] = None) -> Config:
    """Deep-copy a Config so ablation patches cannot leak into the source."""
    src = config or Config()
    payload: Dict[str, Any] = {}
    for section in DEFAULT_CONFIG:
        value = getattr(src, section, None)
        payload[section] = copy.deepcopy(value) if isinstance(value, dict) else value
    payload["repo_path"] = getattr(src, "repo_path", ".")
    payload["verbose"] = bool(getattr(src, "verbose", False))
    return Config.from_dict(payload)


def apply_ablation(config: Optional[Config], variant_id: str) -> Config:
    """Return a cloned config with one ablation variant applied."""
    wanted = str(variant_id or "").strip()
    known = {v.id for v in ABLATION_VARIANTS}
    if wanted not in known:
        raise ValueError(f"unknown ablation variant {variant_id!r}; known={sorted(known)}")

    cfg = clone_config(config)
    retrieval = dict(cfg.retrieval)
    hyde = dict(retrieval.get("hyde") or {})
    ce = dict(retrieval.get("cross_encoder") or {})
    context = dict(cfg.context)

    if wanted == "full":
        pass
    elif wanted == "no_dense":
        retrieval["dense_enabled"] = False
        retrieval["dense_weight"] = 0.0
    elif wanted == "no_bm25":
        retrieval["sparse_enabled"] = False
        retrieval["sparse_weight"] = 0.0
    elif wanted == "no_graph":
        retrieval["graph_enabled"] = False
        retrieval["graph_weight"] = 0.0
        retrieval["expand_neighbors"] = 0
    elif wanted == "no_rerank":
        ce["enabled"] = False
    elif wanted == "no_mmr":
        retrieval["mmr"] = False
    elif wanted == "hyde":
        hyde["enabled"] = True
    elif wanted == "ccr_lite":
        context["pack_mode"] = "ccr_lite"
    elif wanted == "no_retrieval":
        retrieval["no_retrieval"] = True

    retrieval["hyde"] = hyde
    retrieval["cross_encoder"] = ce
    cfg.retrieval = retrieval
    cfg.context = context
    return cfg


def llm_key_available(environ: Optional[Dict[str, str]] = None, config=None) -> bool:
    """True when a chat-LLM key is present. Local embedders do not count."""
    env = environ if environ is not None else os.environ
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "LLM_API_KEY",
        "CODEHARNESS_LLM_API_KEY",
    ):
        if str(env.get(key) or "").strip():
            return True
    llm = getattr(config, "llm", None) or {}
    return bool(str(llm.get("api_key") or "").strip())


def row_from_report(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Flatten eval metrics for one ablation column."""
    if not report:
        return {}
    metrics = dict(report.get("metrics") or {})
    latencies = metrics.get("latencies_ms") or {}

    def _stage(name: str) -> Dict[str, Any]:
        vals = latencies.get(name) or {}
        return {"mean": vals.get("mean"), "p50": vals.get("p50")}

    return {
        "recall_at_k": metrics.get("recall_at_k"),
        "ndcg_at_k": metrics.get("ndcg_at_k"),
        "citation_path_hit_rate": metrics.get("citation_path_hit_rate"),
        "prompt_tokens_full_mean": metrics.get("prompt_tokens_full_mean"),
        "prompt_tokens_packed_mean": metrics.get("prompt_tokens_packed_mean"),
        "prompt_token_drop": metrics.get("prompt_token_drop"),
        "failure_counts": dict(metrics.get("failure_counts") or {}),
        "latencies_ms": {
            "dense": _stage("dense"),
            "bm25": _stage("bm25"),
            "graph": _stage("graph"),
            "ce": _stage("ce"),
            "mmr": _stage("mmr"),
        },
        "n": metrics.get("n"),
        "k": metrics.get("k") or report.get("k"),
        "loop_attempts_mean": metrics.get("loop_attempts_mean"),
        "stall_count": metrics.get("stall_count"),
        "stall_rate": metrics.get("stall_rate"),
        "escape_rate": metrics.get("escape_rate"),
    }


def _fmt_score(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}"


def _fmt_ms(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f}"


def _fmt_tokens(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f}"


def _failure_cell(counts: Optional[Dict[str, Any]]) -> str:
    if not counts:
        return "—"
    return ", ".join(f"{name}={counts[name]}" for name in sorted(counts))


def render_ablation_markdown(
    *,
    rows: Dict[str, Dict[str, Any]],
    suite: str,
    suite_path: str,
    k: int,
    commit_sha: str,
    generated: Optional[str] = None,
    machine: Optional[Dict[str, str]] = None,
    answer_level: Optional[Dict[str, Any]] = None,
    notes: str = "",
    variants: Optional[Sequence[AblationVariant]] = None,
) -> str:
    generated = generated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    variants = list(variants or ABLATION_VARIANTS)
    machine = machine or {}
    answer_level = answer_level or {}
    n = None
    for row in rows.values():
        if row.get("n") is not None:
            n = row.get("n")
            break

    lines = [
        "# Harness-on vs harness-off ablation",
        "",
        "Leave-one-out of the default retrieve+pack stack on the committed",
        "golden suite. Inspired by the with-vs-without harness contrast in",
        "[Harness-Aware Distillation for Small Language Model Agents]",
        "(https://arxiv.org/abs/2610.02858). This table is an honesty record,",
        "not a default flip.",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Suite | `{suite}` |",
        f"| Suite path | `{suite_path}` |",
        f"| k | {k} |",
        f"| n | {n if n is not None else 'n/a'} |",
        f"| Generated | {generated} |",
        f"| Commit | `{commit_sha or 'unknown'}` |",
        f"| Machine | {machine.get('summary') or '—'} |",
        f"| Notes | {notes or '—'} |",
        "",
        "## Metrics",
        "",
        "| variant | Recall@{k} | nDCG@{k} | citation-path | dense ms | bm25 ms | graph ms | ce ms | mmr ms | tokens full | tokens packed | failures |".format(
            k=k
        ),
        "|---------|------------|----------|---------------|----------|---------|----------|-------|--------|-------------|---------------|----------|",
    ]
    for variant in variants:
        row = rows.get(variant.id) or {}
        lat = row.get("latencies_ms") or {}
        lines.append(
            "| {vid} | {rec} | {ndcg} | {cite} | {dense} | {bm25} | {graph} | {ce} | {mmr} | {full} | {packed} | {fails} |".format(
                vid=variant.id,
                rec=_fmt_score(row.get("recall_at_k")),
                ndcg=_fmt_score(row.get("ndcg_at_k")),
                cite=_fmt_score(row.get("citation_path_hit_rate")),
                dense=_fmt_ms((lat.get("dense") or {}).get("mean")),
                bm25=_fmt_ms((lat.get("bm25") or {}).get("mean")),
                graph=_fmt_ms((lat.get("graph") or {}).get("mean")),
                ce=_fmt_ms((lat.get("ce") or {}).get("mean")),
                mmr=_fmt_ms((lat.get("mmr") or {}).get("mean")),
                full=_fmt_tokens(row.get("prompt_tokens_full_mean")),
                packed=_fmt_tokens(row.get("prompt_tokens_packed_mean")),
                fails=_failure_cell(row.get("failure_counts")),
            )
        )

    stall_rows = [
        (variant.id, rows.get(variant.id) or {})
        for variant in variants
        if (rows.get(variant.id) or {}).get("stall_count")
        or (rows.get(variant.id) or {}).get("stall_rate")
        or ((rows.get(variant.id) or {}).get("loop_attempts_mean") or 1) > 1
    ]
    if stall_rows:
        lines.extend(
            [
                "",
                "## Stall (corrective loop)",
                "",
                "A stall is a retry whose top-k hit set Jaccard-overlaps the previous "
                "attempt at ≥ 0.8. Escape = a stall followed by a changed, better-graded "
                "attempt. `stop_on_stall` is on when the loop is on.",
                "",
                "| variant | loop attempts | stalls | stall rate | escape rate |",
                "|---------|---------------|--------|------------|-------------|",
            ]
        )
        for vid, row in stall_rows:
            lines.append(
                "| {vid} | {att} | {st} | {sr} | {er} |".format(
                    vid=vid,
                    att=_fmt_tokens(row.get("loop_attempts_mean")),
                    st=row.get("stall_count") if row.get("stall_count") is not None else "n/a",
                    sr=_fmt_score(row.get("stall_rate")),
                    er=_fmt_score(row.get("escape_rate")),
                )
            )

    lines.extend(
        [
            "",
            "## Variant notes",
            "",
        ]
    )
    for variant in variants:
        lines.append(f"- `{variant.id}` ({variant.kind}): {variant.notes}")

    ran = bool(answer_level.get("ran"))
    lines.extend(
        [
            "",
            "## Answer-level citation (full vs no-retrieval)",
            "",
        ]
    )
    if ran:
        lines.append(
            "LLM key was present. Citation overlap on generated answers:"
        )
        lines.append("")
        lines.append("| variant | citation coverage | n |")
        lines.append("|---------|-------------------|---|")
        for name in ("full", "no_retrieval"):
            block = (answer_level.get("variants") or {}).get(name) or {}
            lines.append(
                f"| {name} | {_fmt_score(block.get('citation_coverage'))} | "
                f"{block.get('n', 'n/a')} |"
            )
    else:
        reason = answer_level.get("reason") or (
            "No chat-LLM key in the environment. Answer-level comparison was not run."
        )
        lines.append(reason)

    lines.extend(
        [
            "",
            "## How to run (local)",
            "",
            "```bash",
            "pip install -r requirements.txt",
            "python main.py index .",
            "python main.py eval-ab . --ablate",
            "# or",
            "make eval-ablate",
            "```",
            "",
            "Eval-only. Does not change MiniLM, Chroma, RRF weights, `max_loops=0`,",
            "`decision.enabled=false`, or verify defaults. `make test` stays offline.",
            "",
            "## Caveats",
            "",
            "- Fixture n is small (8 queries). Treat deltas as directional, not a default flip.",
            "- One run, no bootstrap. Stage latency varies with cold embedder / CE load.",
            "- `no_retrieval` recall/nDCG/citation-path are zero by construction.",
            "- `hyde` and `ccr_lite` are optional-on rows: those stages are off in `full`.",
            "- Cross-encoder and MiniLM weights must already be cached; this recipe does not download.",
            "",
        ]
    )
    return "\n".join(lines)


def write_ablation_artifacts(
    *,
    rows: Dict[str, Dict[str, Any]],
    json_path: str,
    markdown_path: str,
    suite: str,
    suite_path: str,
    k: int,
    commit_sha: str,
    generated: Optional[str] = None,
    machine: Optional[Dict[str, str]] = None,
    answer_level: Optional[Dict[str, Any]] = None,
    notes: str = "",
    reports: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    generated = generated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "kind": "harness-ablation",
        "suite": suite,
        "suite_path": suite_path,
        "k": k,
        "generated": generated,
        "commit": commit_sha,
        "machine": machine or {},
        "notes": notes,
        "answer_level": answer_level or {},
        "variants": {
            v.id: {"label": v.label, "kind": v.kind, "notes": v.notes, "metrics": rows.get(v.id) or {}}
            for v in ABLATION_VARIANTS
        },
        "reports": reports or {},
    }
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(markdown_path) or ".", exist_ok=True)
    import json

    with open(json_path, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    md = render_ablation_markdown(
        rows=rows,
        suite=suite,
        suite_path=suite_path,
        k=k,
        commit_sha=commit_sha,
        generated=generated,
        machine=machine,
        answer_level=answer_level,
        notes=notes,
    )
    with open(markdown_path, "w") as fh:
        fh.write(md)
        if not md.endswith("\n"):
            fh.write("\n")
    return {"json": json_path, "markdown": markdown_path}


def git_head_sha(cwd: str = ".") -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )
    except OSError:
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def machine_notes() -> Dict[str, str]:
    py = platform.python_version()
    system = platform.platform()
    summary = f"python {py}; {system}"
    return {"python": py, "platform": system, "summary": summary}


def print_ablation_table(rows: Dict[str, Dict[str, Any]], k: int) -> None:
    print()
    print(f"Ablation  k={k}")
    header = f"{'variant':<14} {'R@k':>7} {'nDCG':>7} {'cite':>7}  failures"
    print(header)
    print("-" * len(header))
    for variant in ABLATION_VARIANTS:
        row = rows.get(variant.id) or {}

        def _fmt(val):
            return f"{val:.3f}" if isinstance(val, float) else "    n/a"

        fails = _failure_cell(row.get("failure_counts"))
        print(
            f"{variant.id:<14} {_fmt(row.get('recall_at_k')):>7} "
            f"{_fmt(row.get('ndcg_at_k')):>7} {_fmt(row.get('citation_path_hit_rate')):>7}  "
            f"{fails}"
        )
    print()


def run_ablation(
    *,
    repo: str = ".",
    suite: str = DEFAULT_SUITE,
    markdown_path: str = COMMITTED_ABLATION_MD,
    json_path: str = COMMITTED_ABLATION_JSON,
    eval_one: Optional[EvalFn] = None,
    config=None,
    k: int = 10,
    answer_level: Optional[Dict[str, Any]] = None,
    commit_sha: Optional[str] = None,
    notes: str = "",
) -> int:
    """Score each variant via ``eval_one(config, variant)`` and persist the table."""
    if eval_one is None:
        raise RuntimeError("eval_one callback is required")

    rows: Dict[str, Dict[str, Any]] = {}
    reports: Dict[str, Any] = {}
    for variant in ABLATION_VARIANTS:
        print(f"[*] Ablation variant: {variant.id} — {variant.label}")
        cfg = apply_ablation(config, variant.id)
        report = eval_one(cfg, variant)
        if not isinstance(report, dict):
            raise RuntimeError(f"ablation variant {variant.id} did not return a report")
        reports[variant.id] = {
            "metrics": report.get("metrics") or {},
            "config": report.get("config") or {},
        }
        rows[variant.id] = row_from_report(report)
        from .eval import print_summary

        print_summary(report)

    if answer_level is None:
        answer_level = {
            "ran": False,
            "reason": (
                "No chat-LLM key in the environment. "
                "Answer-level comparison was not run."
            ),
        }
        if llm_key_available(config=config):
            answer_level = {
                "ran": False,
                "reason": (
                    "An LLM key is present, but this runner did not generate "
                    "answers (eval stays retrieval-only unless a caller supplies "
                    "answer-level scores)."
                ),
            }

    sha = commit_sha if commit_sha is not None else git_head_sha(repo)
    machine = machine_notes()
    write_ablation_artifacts(
        rows=rows,
        json_path=json_path,
        markdown_path=markdown_path,
        suite="code-harness",
        suite_path=suite,
        k=k,
        commit_sha=sha,
        machine=machine,
        answer_level=answer_level,
        notes=notes
        or "Live one-shot retrieve+pack on the committed fixture. Defaults unchanged.",
        reports=reports,
    )
    print_ablation_table(rows, k)
    print(f"[+] Wrote ablation markdown: {markdown_path}")
    print(f"[+] Wrote ablation JSON: {json_path}")
    return 0


class EmptyRetriever:
    """No-retrieval baseline: empty hits, zero stage work."""

    ce_enabled = False

    def retrieve(self, query, top_k=None, debug=False, **kwargs):
        trace = {
            "dense": [],
            "sparse": [],
            "graph": [],
            "fused": [],
            "reranked": [],
            "latencies_ms": {"dense": 0.0, "bm25": 0.0, "graph": 0.0, "ce": 0.0},
            "mode": "none",
        }
        if debug:
            return [], trace
        return []
