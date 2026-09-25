"""Fixture-suite TurboVec vs Chroma A/B runner (no native extras required)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from harness.config import DEFAULT_CONFIG
from harness.vector_eval import compare_backend_reports


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


def _report(recall, ndcg, k=10, dense_p50=5.0, citation=1.0, n=8, name="code-harness"):
    return {
        "suite": name,
        "k": k,
        "metrics": {
            "k": k,
            "n": n,
            "recall_at_k": recall,
            "ndcg_at_k": ndcg,
            "citation_path_hit_rate": citation,
            "latencies_ms": {"dense": {"mean": dense_p50, "p50": dense_p50}},
        },
        "cases": [],
    }


class TestCompareArtifacts(unittest.TestCase):
    def test_markdown_and_json_include_recall_ndcg_latency_and_gate(self):
        from harness.vector_eval import render_compare_markdown, write_compare_artifacts

        result = compare_backend_reports(
            _report(1.0, 0.9, dense_p50=4.2),
            _report(0.99, 0.89, dense_p50=3.1),
            baseline_name="chromadb",
            candidate_name="turbovec",
            k=10,
        )
        md = render_compare_markdown(
            result,
            suite="code-harness",
            suite_path=".docs/research/eval/code-harness.fixture.yaml",
            generated="2026-09-25T00:00:00Z",
        )
        self.assertIn("Recall@10", md)
        self.assertIn("nDCG@10", md)
        self.assertIn("dense p50", md)
        self.assertIn("chromadb", md)
        self.assertIn("turbovec", md)
        self.assertIn("1.0000", md)
        self.assertIn("4.2ms", md)
        self.assertIn("PASS", md)
        self.assertIn("eval-ab", md)

        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "RESULTS.json")
            md_path = os.path.join(tmp, "RESULTS.md")
            written = write_compare_artifacts(
                result,
                json_path=json_path,
                markdown_path=md_path,
                suite="code-harness",
                suite_path=".docs/research/eval/code-harness.fixture.yaml",
                generated="2026-09-25T00:00:00Z",
                reports={"chromadb": _report(1.0, 0.9), "turbovec": _report(0.99, 0.89)},
            )
            self.assertEqual(written["json"], json_path)
            self.assertEqual(written["markdown"], md_path)
            with open(json_path) as fh:
                payload = json.load(fh)
            self.assertEqual(payload["kind"], "vector-backend-ab")
            self.assertEqual(payload["suite"], "code-harness")
            self.assertTrue(payload["compare"]["passed"])
            self.assertEqual(payload["compare"]["baseline_metrics"]["recall_at_k"], 1.0)
            self.assertEqual(payload["compare"]["candidate_metrics"]["dense_p50"], 3.1)
            with open(md_path) as fh:
                self.assertIn("PASS", fh.read())

    def test_skipped_candidate_markdown_is_honest(self):
        from harness.vector_eval import render_compare_markdown

        result = compare_backend_reports(
            _report(0.875, 0.8),
            None,
            skip_reason="turbovec is not installed; pip install -r requirements-turbovec.txt",
        )
        md = render_compare_markdown(result, suite="code-harness")
        self.assertIn("SKIP", md)
        self.assertIn("not installed", md)
        self.assertIn("requirements-turbovec", md)


class TestFixtureABDecision(unittest.TestCase):
    def test_optional_path_skips_missing_turbovec(self):
        from harness.eval_ab import decide_fixture_ab

        decision = decide_fixture_ab(
            selected="chromadb",
            compare_names=["chromadb", "turbovec"],
            probes={
                "chromadb": (True, ""),
                "turbovec": (False, "turbovec is not installed; pip install -r requirements-turbovec.txt"),
            },
            optional=True,
        )
        self.assertEqual(decision.exit_code, 0)
        self.assertTrue(decision.optional_skip)
        self.assertEqual(decision.backends_to_run, ["chromadb"])
        self.assertIn("turbovec", decision.skipped)
        self.assertIn("not installed", decision.skipped["turbovec"])
        self.assertFalse(decision.failed)

    def test_selected_turbovec_missing_fails(self):
        from harness.eval_ab import decide_fixture_ab

        decision = decide_fixture_ab(
            selected="turbovec",
            compare_names=["chromadb", "turbovec"],
            probes={
                "chromadb": (True, ""),
                "turbovec": (False, "turbovec is not installed; pip install -r requirements-turbovec.txt"),
            },
            optional=True,
        )
        self.assertEqual(decision.exit_code, 1)
        self.assertTrue(decision.failed)
        self.assertIn("turbovec", decision.fail_message.lower())
        self.assertIn("not installed", decision.fail_message.lower())

    def test_both_available_indexes_both(self):
        from harness.eval_ab import decide_fixture_ab

        decision = decide_fixture_ab(
            selected="chromadb",
            compare_names=["chromadb", "turbovec"],
            probes={"chromadb": (True, ""), "turbovec": (True, "")},
            optional=True,
        )
        self.assertEqual(decision.exit_code, 0)
        self.assertEqual(decision.backends_to_run, ["chromadb", "turbovec"])
        self.assertEqual(decision.skipped, {})
        self.assertFalse(decision.optional_skip)

    def test_optional_path_placeholder_when_chroma_also_missing(self):
        from harness.eval_ab import decide_fixture_ab

        decision = decide_fixture_ab(
            selected="chromadb",
            compare_names=["chromadb", "turbovec"],
            probes={
                "chromadb": (False, "chromadb is not installed; pip install chromadb"),
                "turbovec": (False, "turbovec is not installed"),
            },
            optional=True,
        )
        self.assertEqual(decision.exit_code, 0)
        self.assertTrue(decision.optional_skip)
        self.assertEqual(decision.backends_to_run, [])
        self.assertIn("chromadb", decision.skipped)


class TestFixtureABRunner(unittest.TestCase):
    def test_runner_writes_placeholder_and_skips_index_when_optional(self):
        from harness.eval_ab import run_fixture_ab

        indexed = []

        def probe(name, config=None):
            return False, f"{name} is not installed"

        def index_backend(name):
            indexed.append(name)
            raise AssertionError("optional skip must not index")

        def eval_compare(names):
            raise AssertionError("optional skip must not eval")

        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "RESULTS.md")
            json_path = os.path.join(tmp, "RESULTS.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_fixture_ab(
                    repo=".",
                    suite=".docs/research/eval/code-harness.fixture.yaml",
                    selected="chromadb",
                    compare_names=["chromadb", "turbovec"],
                    markdown_path=md_path,
                    json_path=json_path,
                    optional=True,
                    probe=probe,
                    index_backend=index_backend,
                    eval_compare=eval_compare,
                )
            self.assertEqual(code, 0)
            self.assertEqual(indexed, [])
            text = buf.getvalue().lower()
            self.assertIn("skip", text)
            self.assertTrue(os.path.isfile(md_path))
            with open(md_path) as fh:
                md = fh.read()
            self.assertIn("not installed", md)
            self.assertIn("eval-ab", md)
            with open(json_path) as fh:
                payload = json.load(fh)
            self.assertTrue(payload["compare"]["skipped"])

    def test_runner_fails_when_selected_turbovec_missing(self):
        from harness.eval_ab import run_fixture_ab

        def probe(name, config=None):
            if name == "turbovec":
                return False, "turbovec is not installed; pip install -r requirements-turbovec.txt"
            return True, ""

        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(buf):
                code = run_fixture_ab(
                    repo=".",
                    selected="turbovec",
                    compare_names=["chromadb", "turbovec"],
                    markdown_path=os.path.join(tmp, "RESULTS.md"),
                    json_path=os.path.join(tmp, "RESULTS.json"),
                    optional=True,
                    probe=probe,
                    index_backend=lambda name: None,
                    eval_compare=lambda names: 0,
                )
        self.assertEqual(code, 1)
        self.assertIn("not installed", buf.getvalue().lower())

    def test_runner_indexes_available_backends_and_persists_compare(self):
        from harness.eval_ab import run_fixture_ab

        indexed = []

        def probe(name, config=None):
            return True, ""

        def index_backend(name):
            indexed.append(name)

        def eval_compare(names):
            self.assertEqual(names, ["chromadb", "turbovec"])
            result = compare_backend_reports(
                _report(1.0, 1.0, dense_p50=5.0),
                _report(0.99, 0.99, dense_p50=4.0),
            )
            from harness.vector_eval import write_compare_artifacts

            write_compare_artifacts(
                result,
                json_path=eval_compare.json_path,
                markdown_path=eval_compare.md_path,
                suite="code-harness",
            )
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            eval_compare.md_path = os.path.join(tmp, "RESULTS.md")
            eval_compare.json_path = os.path.join(tmp, "RESULTS.json")
            code = run_fixture_ab(
                repo=".",
                selected="chromadb",
                compare_names=["chromadb", "turbovec"],
                markdown_path=eval_compare.md_path,
                json_path=eval_compare.json_path,
                optional=True,
                probe=probe,
                index_backend=index_backend,
                eval_compare=eval_compare,
            )
            self.assertEqual(code, 0)
            self.assertEqual(indexed, ["chromadb", "turbovec"])
            with open(eval_compare.md_path) as fh:
                md = fh.read()
            self.assertIn("PASS", md)
            self.assertIn("0.9900", md)

    def test_runner_swallows_embed_failure_on_optional_path(self):
        from harness.eval_ab import run_fixture_ab

        def probe(name, config=None):
            return True, ""

        def index_backend(name):
            raise RuntimeError("sentence-transformers not installed")

        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "RESULTS.md")
            json_path = os.path.join(tmp, "RESULTS.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_fixture_ab(
                    selected="chromadb",
                    markdown_path=md_path,
                    json_path=json_path,
                    optional=True,
                    probe=probe,
                    index_backend=index_backend,
                    eval_compare=lambda names: 0,
                )
            self.assertEqual(code, 0)
            with open(md_path) as fh:
                self.assertIn("sentence-transformers", fh.read())
            self.assertIn("could not run", buf.getvalue().lower())

    def test_runner_propagates_gate_exit_code(self):
        from harness.eval_ab import run_fixture_ab

        def probe(name, config=None):
            return True, ""

        def eval_compare(names):
            raise SystemExit(2)

        with tempfile.TemporaryDirectory() as tmp:
            code = run_fixture_ab(
                selected="turbovec",
                markdown_path=os.path.join(tmp, "RESULTS.md"),
                json_path=os.path.join(tmp, "RESULTS.json"),
                skip_index=True,
                probe=probe,
                index_backend=lambda name: None,
                eval_compare=eval_compare,
            )
        self.assertEqual(code, 2)


class TestEvalABCli(unittest.TestCase):
    def test_eval_ab_help_and_eval_compare_flags(self):
        ev = subprocess.run(
            [sys.executable, "main.py", "eval", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(ev.returncode, 0, ev.stderr)
        self.assertIn("--compare-backends", ev.stdout)
        self.assertIn("--compare-output", ev.stdout)
        self.assertIn("--compare-markdown", ev.stdout)

        ab = subprocess.run(
            [sys.executable, "main.py", "eval-ab", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(ab.returncode, 0, ab.stderr)
        self.assertIn("fixture", ab.stdout.lower())
        self.assertIn("--compare-backends", ab.stdout)
        self.assertIn("--skip-index", ab.stdout)

    def test_eval_ab_optional_path_exits_zero_without_extras(self):
        try:
            import chromadb  # noqa: F401
            import turbovec  # noqa: F401
            self.skipTest("both extras installed; optional skip path not exercised")
        except ImportError:
            pass
        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "RESULTS.md")
            json_path = os.path.join(tmp, "RESULTS.json")
            run = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "eval-ab",
                    ".",
                    "--skip-index",
                    "--markdown",
                    md_path,
                    "--json",
                    json_path,
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
                env={**os.environ, "CODEHARNESS_VECTOR_BACKEND": ""},
            )
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            self.assertTrue(
                "skip" in run.stdout.lower() or "could not run" in run.stdout.lower(),
                run.stdout,
            )
            self.assertTrue(os.path.isfile(md_path))
            with open(md_path) as fh:
                self.assertIn("How to run", fh.read())

    def test_eval_selected_turbovec_missing_exits_nonzero(self):
        try:
            import turbovec  # noqa: F401
            self.skipTest("native turbovec installed; selected-missing path not exercised")
        except ImportError:
            pass
        run = subprocess.run(
            [
                sys.executable,
                "main.py",
                "eval",
                ".",
                "--suite",
                ".docs/research/eval/code-harness.fixture.yaml",
                "--vector-backend",
                "turbovec",
                "--compare-backends",
                "chromadb,turbovec",
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(run.returncode, 1, run.stderr + run.stdout)
        self.assertIn("unavailable", (run.stdout + run.stderr).lower())


class TestDoctorExperimentalWarn(unittest.TestCase):
    def test_native_turbovec_active_warns_even_when_probe_ok(self):
        from harness.config import Config
        from harness.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.repo_path = tmp
            cfg.vector_store["type"] = "turbovec"
            cfg.vector_store["turbovec"] = {
                "use_stub": False,
                "persist_directory": os.path.join(tmp, ".code-harness", "turbovec"),
            }
            cfg.knowledge_graph["persist_path"] = os.path.join(tmp, ".code-harness", "graph.json")
            cfg.redaction["audit_path"] = os.path.join(tmp, ".code-harness", "audit", "audit.jsonl")
            cfg.embedding["provider"] = "local"
            cfg.embedding["model"] = "all-MiniLM-L6-v2"
            cfg.llm["provider"] = "ollama"
            with patch("harness.vector_eval.probe_backend", return_value=(True, "")):
                report = run_doctor(cfg, repo_path=tmp, repo_name="fixture", environ={})
            backend = {c.name: c for c in report.checks}["vector_backend"]
            self.assertEqual(backend.status, "warn")
            self.assertIn("experimental", backend.message.lower())
            self.assertIn("eval-ab", (backend.hint or "").lower() + backend.message.lower())


class TestProbeChroma(unittest.TestCase):
    def test_probe_chromadb_is_honest_when_missing(self):
        from harness.vector_eval import probe_backend

        try:
            import chromadb  # noqa: F401
            self.skipTest("chromadb installed; missing-probe path not exercised")
        except ImportError:
            pass
        cfg = type("C", (), {"vector_store": dict(DEFAULT_CONFIG["vector_store"])})()
        available, reason = probe_backend("chromadb", cfg)
        self.assertFalse(available)
        self.assertIn("chromadb", reason.lower())


if __name__ == "__main__":
    unittest.main()
