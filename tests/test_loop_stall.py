"""Stall detection and early-stop in the corrective retrieve loop."""

import os
import subprocess
import sys
import tempfile
import unittest

from harness.config import Config, DEFAULT_CONFIG
from harness.loop import (
    STALL_OVERLAP,
    LoopConfig,
    QueryLoop,
    hitset_jaccard,
    is_retrieval_stall,
    topk_hit_ids,
)
from tests.test_query_loop import RecordingBuilder, RecordingRetriever, _rr


class TestStallHelpers(unittest.TestCase):
    def test_jaccard_and_threshold(self):
        self.assertEqual(STALL_OVERLAP, 0.8)
        self.assertEqual(hitset_jaccard(["a", "b"], ["a", "b"]), 1.0)
        self.assertEqual(hitset_jaccard(["a"], ["b"]), 0.0)
        self.assertAlmostEqual(hitset_jaccard(["a", "b"], ["a", "c"]), 1.0 / 3.0)
        self.assertFalse(is_retrieval_stall(None, ["a"]))
        self.assertTrue(is_retrieval_stall(["a", "b"], ["a", "b"], 0.8))
        self.assertFalse(is_retrieval_stall(["a", "b"], ["c", "d"], 0.8))

    def test_topk_hit_ids(self):
        hits = [
            _rr("func:a.py:a:1", "a.py", "a"),
            _rr("func:b.py:b:1", "b.py", "b"),
        ]
        self.assertEqual(topk_hit_ids(hits, 1), ["func:a.py:a:1"])


class TestStallEarlyStop(unittest.TestCase):
    def test_same_hits_stall_and_stop(self):
        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        query = "Where is the query CLI command defined in main.py?"
        retriever = RecordingRetriever({}, default=[miss])
        loop = QueryLoop(
            retriever,
            RecordingBuilder(),
            LoopConfig(max_loops=2, stop_on_stall=True),
        )
        outcome = loop.run(query, top_k=5)
        self.assertEqual(len(retriever.calls), 2)
        self.assertEqual(outcome.stop_reason, "stall")
        self.assertEqual(outcome.stall_count, 1)
        self.assertEqual(outcome.stall_rate, 1.0)
        self.assertEqual(outcome.escape_rate, 0.0)
        self.assertTrue(outcome.traces[-1].get("stalled"))

    def test_changed_hits_are_not_a_stall(self):
        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        hit = _rr("func:main.py:cmd_query:1", "main.py", "cmd_query")
        original = "Where is the query CLI command defined in main.py?"
        retriever = RecordingRetriever(
            {original: {"final": [miss]}},
            default=[hit],
        )
        loop = QueryLoop(
            retriever,
            RecordingBuilder(),
            LoopConfig(max_loops=1, stop_on_stall=True),
        )
        outcome = loop.run(original, top_k=10, must_cite_paths=["main.py"])
        self.assertEqual(len(retriever.calls), 2)
        self.assertNotEqual(outcome.stop_reason, "stall")
        self.assertEqual(outcome.stall_count, 0)

    def test_escape_when_stop_on_stall_is_off(self):
        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        hit = _rr("func:main.py:cmd_query:1", "main.py", "cmd_query", score=8.0, source="reranked")
        original = "Where is the query CLI command defined in main.py?"
        calls = {"n": 0}

        class _SeqRetriever(RecordingRetriever):
            def retrieve(self, query, top_k=None, debug=False, entity_id_map=None, **kwargs):
                calls["n"] += 1
                payload = [miss] if calls["n"] <= 2 else [hit]
                self.calls.append({"query": query, "n": calls["n"]})
                trace = {
                    "dense": payload,
                    "sparse": payload,
                    "graph": [],
                    "fused": payload,
                    "reranked": payload,
                    "latencies_ms": {},
                }
                return (payload, trace) if debug else payload

        retriever = _SeqRetriever({})
        loop = QueryLoop(
            retriever,
            RecordingBuilder(),
            LoopConfig(max_loops=2, stop_on_stall=False, grade_threshold=0.99),
        )
        outcome = loop.run(original, top_k=5)
        self.assertGreaterEqual(outcome.stall_count, 1)
        self.assertGreaterEqual(outcome.escape_count, 1)
        self.assertGreater(outcome.escape_rate, 0.0)
        self.assertNotEqual(outcome.stop_reason, "stall")

    def test_one_shot_path_unchanged(self):
        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        retriever = RecordingRetriever(
            {
                "Where is the query CLI command defined in main.py?": {
                    "final": [miss],
                }
            }
        )
        loop = QueryLoop(retriever, RecordingBuilder(), LoopConfig(max_loops=0))
        outcome = loop.run("Where is the query CLI command defined in main.py?", top_k=10)
        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.stop_reason, "budget")
        self.assertEqual(outcome.stall_count, 0)
        self.assertEqual(outcome.stall_rate, 0.0)
        self.assertEqual(outcome.escape_rate, 0.0)

    def test_audit_row_on_stall(self):
        from harness.audit import tail_audit

        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "audit.jsonl")
            config = Config.from_dict({"redaction": {"audit_path": dest}})
            config.repo_path = tmp
            retriever = RecordingRetriever({}, default=[miss])
            retriever.config = config
            loop = QueryLoop(
                retriever,
                RecordingBuilder(),
                LoopConfig(max_loops=1, stop_on_stall=True),
            )
            outcome = loop.run("Where is the query CLI command defined in main.py?", top_k=5)
            self.assertEqual(outcome.stop_reason, "stall")
            self.assertEqual(outcome.stall_count, 1)
            rows = tail_audit(5, path=dest)
            self.assertTrue(rows)
            self.assertEqual(rows[-1]["action"], "loop.stall")
            self.assertTrue(rows[-1]["stopped"])


class TestConfigAndCost(unittest.TestCase):
    def test_defaults(self):
        self.assertTrue(DEFAULT_CONFIG["retrieval"]["stop_on_stall"])
        self.assertEqual(DEFAULT_CONFIG["retrieval"]["stall_overlap"], 0.8)
        self.assertEqual(DEFAULT_CONFIG["retrieval"]["max_loops"], 0)
        cfg = LoopConfig.from_mapping(Config().retrieval)
        self.assertTrue(cfg.stop_on_stall)
        self.assertEqual(cfg.stall_overlap, 0.8)
        self.assertEqual(cfg.max_loops, 0)

    def test_cost_lists_stalls(self):
        from harness.session import Session, SessionTurn

        session = Session(repo=".")
        session.record_turn(
            SessionTurn(role="assistant", text="ok", loop_attempts=2, stall_count=1)
        )
        report = session.format_cost()
        self.assertIn("stalls:", report)
        self.assertIn("1", report)

    def test_cli_exposes_no_stop_on_stall(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."
        run = subprocess.run(
            [sys.executable, "main.py", "query", "--help"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--no-stop-on-stall", run.stdout)

    def test_eval_summary_includes_stall_metrics(self):
        from harness.eval import EvalFixture, run_eval

        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        fixture = EvalFixture(
            id="q-stall",
            query="Where is the query CLI command defined in main.py?",
            relevant_chunk_ids=["func:main.py:cmd_query"],
            must_cite_paths=["main.py"],
            difficulty="hard",
        )
        report = run_eval(
            fixtures=[fixture],
            retriever=RecordingRetriever({}, default=[miss]),
            context_builder=RecordingBuilder(),
            k=5,
            suite_name="stall",
            suite_path="stall.yaml",
            repo_path=".",
            repo_name="workspace",
            loop_config=LoopConfig(max_loops=2, stop_on_stall=True),
        )
        self.assertEqual(report["cases"][0]["loop"]["stop_reason"], "stall")
        self.assertGreaterEqual(report["metrics"]["stall_count"], 1)
        self.assertIsNotNone(report["metrics"]["stall_rate"])


if __name__ == "__main__":
    unittest.main()
