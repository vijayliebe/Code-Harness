"""Recall@k A/B gate for experimental vector backends."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from harness.config import DEFAULT_CONFIG


def _report(recall, ndcg, k=10, dense_p50=5.0, name="suite"):
    return {
        "suite": name,
        "k": k,
        "metrics": {
            "k": k,
            "n": 2,
            "recall_at_k": recall,
            "ndcg_at_k": ndcg,
            "latencies_ms": {"dense": {"mean": dense_p50, "p50": dense_p50}},
        },
        "cases": [],
    }


class TestCompareReports(unittest.TestCase):
    def test_side_by_side_and_relative_tolerance_fail(self):
        from harness.vector_eval import compare_backend_reports, print_backend_comparison

        baseline = _report(1.0, 1.0)
        candidate = _report(0.90, 0.90)
        result = compare_backend_reports(
            baseline,
            candidate,
            baseline_name="chromadb",
            candidate_name="turbovec",
            k=10,
            relative_tolerance=0.05,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("recall" in f.lower() for f in result.gate_failures))
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_backend_comparison(result)
        text = buf.getvalue()
        self.assertIn("chromadb", text)
        self.assertIn("turbovec", text)
        self.assertIn("Recall@10", text)
        self.assertIn("nDCG@10", text)

    def test_within_tolerance_passes(self):
        from harness.vector_eval import compare_backend_reports

        result = compare_backend_reports(
            _report(1.0, 1.0),
            _report(0.985, 0.985),
            baseline_name="chromadb",
            candidate_name="turbovec",
            k=10,
            relative_tolerance=0.05,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.gate_failures, [])

    def test_absolute_point_gate_at_k10(self):
        from harness.vector_eval import compare_backend_reports

        # 0.97 is within 5% relative of 1.0, but 3 points below Recall@10 (gate is -2 pts).
        result = compare_backend_reports(
            _report(1.0, 1.0, k=10),
            _report(0.97, 0.99, k=10),
            baseline_name="chromadb",
            candidate_name="turbovec",
            k=10,
            relative_tolerance=0.05,
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("2" in f or "point" in f.lower() for f in result.gate_failures))

    def test_force_experimental_passes_even_when_below(self):
        from harness.vector_eval import compare_backend_reports

        result = compare_backend_reports(
            _report(1.0, 1.0),
            _report(0.5, 0.5),
            baseline_name="chromadb",
            candidate_name="turbovec",
            k=10,
            force_experimental=True,
        )
        self.assertTrue(result.passed)
        self.assertTrue(result.forced)
        self.assertTrue(result.gate_failures)

    def test_skipped_candidate_does_not_fail_gate(self):
        from harness.vector_eval import compare_backend_reports

        result = compare_backend_reports(
            _report(1.0, 1.0),
            None,
            baseline_name="chromadb",
            candidate_name="turbovec",
            k=10,
            skip_reason="turbovec is not installed",
        )
        self.assertTrue(result.passed)
        self.assertTrue(result.skipped)
        self.assertIn("not installed", result.skip_reason)

    def test_selected_turbovec_below_baseline_exits_nonzero_unless_forced(self):
        from harness.vector_eval import compare_backend_reports, gate_exit_code

        failed = compare_backend_reports(
            _report(1.0, 1.0),
            _report(0.8, 0.8),
            baseline_name="chromadb",
            candidate_name="turbovec",
            k=10,
        )
        self.assertEqual(gate_exit_code(failed, selected="turbovec"), 2)
        self.assertEqual(gate_exit_code(failed, selected="chromadb"), 0)
        forced = compare_backend_reports(
            _report(1.0, 1.0),
            _report(0.8, 0.8),
            baseline_name="chromadb",
            candidate_name="turbovec",
            k=10,
            force_experimental=True,
        )
        self.assertEqual(gate_exit_code(forced, selected="turbovec"), 0)


class TestEvalCliFlags(unittest.TestCase):
    def test_eval_help_exposes_backend_and_gate_flags(self):
        help_run = subprocess.run(
            [sys.executable, "main.py", "eval", "--help"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or ".",
        )
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        out = help_run.stdout
        self.assertIn("--vector-backend", out)
        self.assertIn("--compare-backends", out)
        self.assertIn("--force-experimental", out)

    def test_index_help_exposes_vector_backend(self):
        help_run = subprocess.run(
            [sys.executable, "main.py", "index", "--help"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or ".",
        )
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        self.assertIn("--vector-backend", help_run.stdout)

    def test_compare_backends_skips_missing_turbovec(self):
        try:
            import turbovec  # noqa: F401
            self.skipTest("native turbovec installed; skip-missing path not exercised")
        except ImportError:
            pass
        from harness.vector_eval import probe_backend

        cfg = type("C", (), {"vector_store": dict(DEFAULT_CONFIG["vector_store"])})()
        available, reason = probe_backend("turbovec", cfg)
        self.assertFalse(available)
        self.assertIn("install", reason.lower())


class TestDoctorTurboVec(unittest.TestCase):
    def test_experimental_backend_warns_and_checks_own_persist(self):
        from harness.config import Config
        from harness.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.repo_path = tmp
            cfg.vector_store["type"] = "turbovec"
            cfg.vector_store["turbovec"] = {
                "use_stub": True,
                "persist_directory": os.path.join(tmp, ".code-harness", "turbovec"),
            }
            cfg.knowledge_graph["persist_path"] = os.path.join(tmp, ".code-harness", "graph.json")
            cfg.redaction["audit_path"] = os.path.join(tmp, ".code-harness", "audit", "audit.jsonl")
            cfg.embedding["provider"] = "local"
            cfg.embedding["model"] = "all-MiniLM-L6-v2"
            cfg.llm["provider"] = "ollama"
            report = run_doctor(cfg, repo_path=tmp, repo_name="fixture", environ={})
            names = {c.name: c for c in report.checks}
            self.assertIn(names["index"].status, {"fail", "warn"})
            backend = names.get("vector_backend")
            self.assertIsNotNone(backend)
            self.assertEqual(backend.status, "warn")
            self.assertIn("experimental", backend.message.lower())


if __name__ == "__main__":
    unittest.main()
