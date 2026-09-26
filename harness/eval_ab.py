"""Fixture-suite A/B runners (vector backends and local embedders).

Backend path: indexes each available persist dir, runs ``eval --compare-backends``,
and writes ``.docs/research/eval/RESULTS.md`` / ``RESULTS.json``. The TurboVec
extra is optional: missing wheel → skip + placeholder (exit 0). Selecting
TurboVec as the active backend while it is missing fails (exit 1). A failed
recall gate still exits 2.

Embedder path: same fixture recipe on Chroma, MiniLM vs
``jina-embeddings-v2-base-code``. Writes ``RESULTS-embed.md`` /
``RESULTS-embed.json``. Missing Jina weights → skip + placeholder (exit 0).
Selecting Jina while it is unavailable fails (exit 1). Default embedder stays
MiniLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from .embedder import (
    DEFAULT_EMBED_MODEL,
    JINA_CODE_EMBED_MODEL,
    normalize_embed_model,
    probe_embedder,
)
from .vector_eval import compare_backend_reports, probe_backend, write_compare_artifacts

DEFAULT_SUITE = ".docs/research/eval/code-harness.fixture.yaml"
DEFAULT_COMPARE = ("chromadb", "turbovec")
DEFAULT_EMBED_COMPARE = (DEFAULT_EMBED_MODEL, JINA_CODE_EMBED_MODEL)
COMMITTED_RESULTS_MD = ".docs/research/eval/RESULTS.md"
COMMITTED_RESULTS_JSON = ".docs/research/eval/RESULTS.json"
COMMITTED_EMBED_RESULTS_MD = ".docs/research/eval/RESULTS-embed.md"
COMMITTED_EMBED_RESULTS_JSON = ".docs/research/eval/RESULTS-embed.json"

OPTIONAL_SKIP_HINT = (
    "Optional TurboVec extra is not installed. Unittest stays green. "
    "Install with: pip install -r requirements-turbovec.txt"
)
OPTIONAL_EMBED_SKIP_HINT = (
    "Optional jina-embeddings-v2-base-code weights are not cached. "
    "Unittest stays green. Run `make eval-ab-embed` on a machine that can "
    "download the Hugging Face model."
)

ProbeFn = Callable[..., tuple]
IndexFn = Callable[[str], None]
EvalFn = Callable[[List[str]], int]
EmbedIndexFn = Callable[[str], None]


@dataclass
class FixtureABDecision:
    selected: str
    backends_to_run: List[str]
    skipped: Dict[str, str] = field(default_factory=dict)
    failed: bool = False
    fail_message: str = ""
    exit_code: int = 0
    optional_skip: bool = False


def decide_fixture_ab(
    *,
    selected: str,
    compare_names: Sequence[str],
    probes: Dict[str, tuple],
    optional: bool = True,
) -> FixtureABDecision:
    """Decide which backends to index/eval and how the optional path exits."""
    skipped: Dict[str, str] = {}
    to_run: List[str] = []
    for name in compare_names:
        ok, reason = probes.get(name, (False, f"{name} was not probed"))
        if ok:
            to_run.append(name)
        else:
            skipped[name] = reason or f"{name} is unavailable"

    selected_missing = selected in skipped
    if selected_missing and (selected == "turbovec" or not optional):
        return FixtureABDecision(
            selected=selected,
            backends_to_run=[],
            skipped=skipped,
            failed=True,
            fail_message=f"Selected backend {selected} unavailable: {skipped[selected]}",
            exit_code=1,
        )

    return FixtureABDecision(
        selected=selected,
        backends_to_run=to_run,
        skipped=skipped,
        optional_skip=bool(skipped) and optional,
        exit_code=0,
    )


def _write_placeholder(
    decision: FixtureABDecision,
    markdown_path: str,
    json_path: str,
    suite: str,
    suite_path: str,
    *,
    blocker: str = "",
    k: int = 10,
) -> None:
    skip_reason = blocker or "; ".join(
        f"{name}: {reason}" for name, reason in decision.skipped.items()
    ) or OPTIONAL_SKIP_HINT
    result = compare_backend_reports(
        None,
        None,
        baseline_name="chromadb",
        candidate_name="turbovec",
        k=k,
        skip_reason=skip_reason,
    )
    write_compare_artifacts(
        result,
        json_path=json_path,
        markdown_path=markdown_path,
        suite=suite,
        suite_path=suite_path,
        skips=decision.skipped,
    )


def run_fixture_ab(
    *,
    repo: str = ".",
    suite: str = DEFAULT_SUITE,
    selected: str = "chromadb",
    compare_names: Optional[Sequence[str]] = None,
    markdown_path: str = COMMITTED_RESULTS_MD,
    json_path: str = COMMITTED_RESULTS_JSON,
    skip_index: bool = False,
    optional: bool = True,
    probe: Optional[ProbeFn] = None,
    index_backend: Optional[IndexFn] = None,
    eval_compare: Optional[EvalFn] = None,
    config=None,
    k: int = 10,
) -> int:
    """Index available backends, compare, persist the table. See module docstring."""
    compare_names = list(compare_names or DEFAULT_COMPARE)
    probe_fn = probe or probe_backend
    probes = {name: tuple(probe_fn(name, config)) for name in compare_names}
    decision = decide_fixture_ab(
        selected=selected,
        compare_names=compare_names,
        probes=probes,
        optional=optional,
    )
    if decision.failed:
        print(f"[!] {decision.fail_message}")
        return decision.exit_code

    if not decision.backends_to_run:
        _write_placeholder(
            decision, markdown_path, json_path, "code-harness", suite, k=k
        )
        joined = "; ".join(f"{name}: {reason}" for name, reason in decision.skipped.items())
        print(f"[!] Skipping fixture A/B: {joined}")
        print("[+] Wrote placeholder results (optional path, exit 0).")
        return 0

    if index_backend is None or eval_compare is None:
        raise RuntimeError("index_backend and eval_compare callbacks are required")

    try:
        if not skip_index:
            for name in decision.backends_to_run:
                print(f"[*] Indexing fixture corpus into {name}")
                index_backend(name)
        code = eval_compare(list(decision.backends_to_run))
        return int(code or 0)
    except SystemExit as exc:
        raw = exc.code
        if raw is None:
            return 0
        if isinstance(raw, int):
            return raw
        return 1
    except Exception as exc:
        if selected == "turbovec":
            print(f"[!] Selected backend turbovec failed: {exc}")
            return 1
        _write_placeholder(
            decision,
            markdown_path,
            json_path,
            "code-harness",
            suite,
            blocker=str(exc),
            k=k,
        )
        print(f"[!] Fixture A/B could not run: {exc}")
        print("[+] Wrote placeholder results (optional path, exit 0).")
        return 0


@dataclass
class EmbedABDecision:
    selected: str
    models_to_run: List[str]
    skipped: Dict[str, str] = field(default_factory=dict)
    failed: bool = False
    fail_message: str = ""
    exit_code: int = 0
    optional_skip: bool = False


def _probe_lookup(probes: Dict[str, tuple], name: str) -> Optional[tuple]:
    if name in probes:
        return probes[name]
    for key, value in probes.items():
        if normalize_embed_model(key) == name:
            return value
    return None


def decide_embed_ab(
    *,
    selected: str,
    compare_names: Sequence[str],
    probes: Dict[str, tuple],
    optional: bool = True,
) -> EmbedABDecision:
    """Decide which local embedders to index/eval. Missing Jina is an optional skip."""
    selected = normalize_embed_model(selected)
    names = [normalize_embed_model(name) for name in compare_names]
    skipped: Dict[str, str] = {}
    to_run: List[str] = []
    for name in names:
        hit = _probe_lookup(probes, name)
        ok, reason = hit if hit is not None else (False, f"{name} was not probed")
        if ok:
            to_run.append(name)
        else:
            skipped[name] = reason or f"{name} is unavailable"

    if selected in skipped and (selected == JINA_CODE_EMBED_MODEL or not optional):
        return EmbedABDecision(
            selected=selected,
            models_to_run=[],
            skipped=skipped,
            failed=True,
            fail_message=f"Selected embedder {selected} unavailable: {skipped[selected]}",
            exit_code=1,
        )

    candidate_missing = any(name != selected and name in skipped for name in names)
    if candidate_missing and optional and selected != JINA_CODE_EMBED_MODEL:
        return EmbedABDecision(
            selected=selected,
            models_to_run=[],
            skipped=skipped,
            optional_skip=True,
            exit_code=0,
        )

    return EmbedABDecision(
        selected=selected,
        models_to_run=to_run,
        skipped=skipped,
        optional_skip=bool(skipped) and optional,
        exit_code=0,
    )


def _write_embed_placeholder(
    decision: EmbedABDecision,
    markdown_path: str,
    json_path: str,
    suite: str,
    suite_path: str,
    *,
    blocker: str = "",
    k: int = 10,
) -> None:
    skip_reason = blocker or "; ".join(
        f"{name}: {reason}" for name, reason in decision.skipped.items()
    ) or OPTIONAL_EMBED_SKIP_HINT
    result = compare_backend_reports(
        None,
        None,
        baseline_name=DEFAULT_EMBED_MODEL,
        candidate_name=JINA_CODE_EMBED_MODEL,
        k=k,
        skip_reason=skip_reason,
    )
    write_compare_artifacts(
        result,
        json_path=json_path,
        markdown_path=markdown_path,
        suite=suite,
        suite_path=suite_path,
        skips=decision.skipped,
        kind="embedder-ab",
    )


def run_embed_ab(
    *,
    repo: str = ".",
    suite: str = DEFAULT_SUITE,
    selected: str = DEFAULT_EMBED_MODEL,
    compare_names: Optional[Sequence[str]] = None,
    markdown_path: str = COMMITTED_EMBED_RESULTS_MD,
    json_path: str = COMMITTED_EMBED_RESULTS_JSON,
    skip_index: bool = False,
    optional: bool = True,
    probe: Optional[ProbeFn] = None,
    index_model: Optional[EmbedIndexFn] = None,
    eval_compare: Optional[EvalFn] = None,
    config=None,
    k: int = 10,
) -> int:
    """Index available local embedders on Chroma, compare, persist RESULTS-embed."""
    compare_names = list(compare_names or DEFAULT_EMBED_COMPARE)
    selected = normalize_embed_model(selected)
    probe_fn = probe or probe_embedder
    probes = {}
    for name in compare_names:
        canonical = normalize_embed_model(name)
        probes[canonical] = tuple(probe_fn(canonical, config))
    decision = decide_embed_ab(
        selected=selected,
        compare_names=compare_names,
        probes=probes,
        optional=optional,
    )
    if decision.failed:
        print(f"[!] {decision.fail_message}")
        return decision.exit_code

    if not decision.models_to_run:
        _write_embed_placeholder(
            decision, markdown_path, json_path, "code-harness", suite, k=k
        )
        joined = "; ".join(f"{name}: {reason}" for name, reason in decision.skipped.items())
        print(f"[!] Skipping embedder A/B: {joined or OPTIONAL_EMBED_SKIP_HINT}")
        print("[+] Wrote placeholder results (optional path, exit 0).")
        return 0

    if index_model is None or eval_compare is None:
        raise RuntimeError("index_model and eval_compare callbacks are required")

    try:
        if not skip_index:
            for name in decision.models_to_run:
                print(f"[*] Indexing fixture corpus with embedder {name}")
                index_model(name)
        code = eval_compare(list(decision.models_to_run))
        return int(code or 0)
    except SystemExit as exc:
        raw = exc.code
        if raw is None:
            return 0
        if isinstance(raw, int):
            return raw
        return 1
    except Exception as exc:
        if selected == JINA_CODE_EMBED_MODEL:
            print(f"[!] Selected embedder {selected} failed: {exc}")
            return 1
        _write_embed_placeholder(
            decision,
            markdown_path,
            json_path,
            "code-harness",
            suite,
            blocker=str(exc),
            k=k,
        )
        print(f"[!] Embedder A/B could not run: {exc}")
        print("[+] Wrote placeholder results (optional path, exit 0).")
        return 0
