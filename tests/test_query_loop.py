"""Tests for the bounded corrective retrieval loop (grader, stops, easy path)."""

import os
import subprocess
import sys
import unittest

from harness.config import Config, DEFAULT_CONFIG
from harness.eval import EvalFixture, run_eval
from harness.models import Chunk, EntityType, RetrievalResult


def _chunk(cid, path, name, entity_id=None, content="x"):
    return Chunk(
        id=cid,
        content=content,
        entity_id=entity_id or ":".join(cid.split(":")[:3]),
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=4,
    )


def _rr(cid, path, name, score=0.4, source="fused"):
    return RetrievalResult(
        chunk=_chunk(cid, path, name),
        score=score,
        source=source,
    )


class RecordingRetriever:
    """Records retrieve() kwargs so tests can assert mode / call count."""

    def __init__(self, by_query, default=None):
        self.by_query = by_query
        self.default = default or []
        self.ce_enabled = False
        self.calls = []

    def retrieve(self, query, top_k=None, debug=False, entity_id_map=None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, "debug": debug, **kwargs})
        payload = self.by_query.get(query)
        if payload is None:
            results = list(self.default)
            trace = {
                "dense": [],
                "sparse": results,
                "graph": [],
                "fused": results,
                "reranked": [],
                "latencies_ms": {"dense": 0.0, "bm25": 1.0, "graph": 0.0, "ce": 0.0},
            }
        else:
            results = payload["final"]
            trace = {
                "dense": payload.get("dense", results),
                "sparse": payload.get("sparse", results),
                "graph": payload.get("graph", []),
                "fused": payload.get("fused", results),
                "reranked": payload.get("reranked", []),
                "latencies_ms": payload.get(
                    "latencies_ms",
                    {"dense": 1.0, "bm25": 1.0, "graph": 1.0, "ce": 1.0},
                ),
            }
        if debug:
            return results, trace
        return results


class RecordingBuilder:
    def build_context_report(self, query, results):
        from harness.context_builder import ContextReport

        paths = []
        ids = []
        for item in results:
            chunk = getattr(item, "chunk", item)
            ids.append(chunk.id)
            path = chunk.file_path.replace("\\", "/")
            if path not in paths:
                paths.append(path)
        return ContextReport(
            context="# packed\n" + "\n".join(paths),
            prompt_tokens=32 * max(1, len(results)),
            packed_chunk_ids=ids,
            packed_paths=paths,
            mmr_latency_ms=1.0,
        )


class TestDetectQueryMode(unittest.TestCase):
    def test_identifier_like_queries_are_bm25(self):
        from harness.loop import detect_query_mode

        self.assertEqual(detect_query_mode("cmd_query"), "bm25")
        self.assertEqual(detect_query_mode("Retriever.index_chunks"), "bm25")
        self.assertEqual(detect_query_mode("`ContextBuilder`"), "bm25")
        self.assertEqual(detect_query_mode("CodeParser"), "bm25")

    def test_explain_queries_are_hybrid(self):
        from harness.loop import detect_query_mode

        self.assertEqual(
            detect_query_mode("How does ContextBuilder assemble MMR context?"),
            "hybrid",
        )
        self.assertEqual(
            detect_query_mode("Explain the tradeoff in reciprocal rank fusion"),
            "hybrid",
        )

    def test_who_calls_is_graph_shaped(self):
        from harness.loop import detect_query_mode

        self.assertEqual(detect_query_mode("Who calls Retriever index_chunks?"), "graph")
        self.assertEqual(detect_query_mode("what exposes the query CLI"), "graph")


class TestHeuristicGrade(unittest.TestCase):
    def test_high_when_name_and_path_overlap(self):
        from harness.loop import heuristic_grade

        hits = [_rr("func:main.py:cmd_query:1", "main.py", "cmd_query", score=0.2)]
        grade = heuristic_grade("cmd_query", hits)
        self.assertGreaterEqual(grade, 0.35)
        self.assertGreater(grade, heuristic_grade("unrelated_symbol", hits))

    def test_low_when_no_name_or_path_overlap(self):
        from harness.loop import heuristic_grade

        hits = [_rr("func:other.py:foo:1", "other.py", "foo", score=0.9)]
        grade = heuristic_grade("Where is the query CLI command defined in main.py?", hits)
        self.assertLess(grade, 0.35)

    def test_ce_is_not_the_only_signal(self):
        from harness.loop import heuristic_grade

        hits = [_rr("func:other.py:foo:1", "other.py", "foo", score=9.0, source="reranked")]
        grade = heuristic_grade("cmd_query", hits, ce_max=9.0)
        self.assertLess(grade, 0.70)
        lexical = heuristic_grade("foo", hits, ce_max=0.0)
        self.assertGreater(lexical, 0.0)


class TestCitationCoverage(unittest.TestCase):
    def test_extracts_paths_from_answer_and_scores_overlap(self):
        from harness.loop import citation_coverage, extract_cited_paths

        answer = "See `main.py:cmd_query` and harness/retriever.py for callers."
        cited = extract_cited_paths(answer)
        self.assertIn("main.py", cited)
        self.assertIn("harness/retriever.py", cited)
        coverage = citation_coverage(
            answer,
            retrieved_paths=["main.py", "harness/eval.py"],
            must_cite_paths=["main.py"],
        )
        self.assertGreater(coverage, 0.0)
        self.assertLessEqual(coverage, 1.0)

    def test_no_llm_uses_packed_must_cite_fraction(self):
        from harness.loop import citation_coverage

        coverage = citation_coverage(
            answer=None,
            retrieved_paths=["main.py", "harness/retriever.py"],
            must_cite_paths=["main.py", "missing.py"],
        )
        self.assertAlmostEqual(coverage, 0.5)


class TestStopConditions(unittest.TestCase):
    def test_stop_when_grade_meets_threshold(self):
        from harness.loop import should_stop

        stop, reason = should_stop(
            grade=0.40,
            coverage=0.0,
            attempt=1,
            max_loops=1,
            grade_threshold=0.35,
            citation_threshold=0.5,
        )
        self.assertTrue(stop)
        self.assertEqual(reason, "grade")

    def test_stop_when_citation_coverage_meets_threshold(self):
        from harness.loop import should_stop

        stop, reason = should_stop(
            grade=0.10,
            coverage=0.80,
            attempt=1,
            max_loops=1,
            grade_threshold=0.35,
            citation_threshold=0.5,
        )
        self.assertTrue(stop)
        self.assertEqual(reason, "citation")

    def test_stop_when_budget_exhausted(self):
        from harness.loop import should_stop

        stop, reason = should_stop(
            grade=0.10,
            coverage=0.0,
            attempt=2,
            max_loops=1,
            grade_threshold=0.35,
            citation_threshold=0.5,
        )
        self.assertTrue(stop)
        self.assertEqual(reason, "budget")

    def test_continue_when_below_thresholds_and_budget_remains(self):
        from harness.loop import should_stop

        stop, reason = should_stop(
            grade=0.10,
            coverage=0.0,
            attempt=1,
            max_loops=1,
            grade_threshold=0.35,
            citation_threshold=0.5,
        )
        self.assertFalse(stop)
        self.assertEqual(reason, "retry")


class TestQueryLoopBounds(unittest.TestCase):
    def test_max_loops_zero_is_one_shot_hybrid(self):
        from harness.loop import LoopConfig, QueryLoop

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
        self.assertEqual(retriever.calls[0].get("mode", "hybrid"), "hybrid")
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(outcome.stop_reason, "budget")
        self.assertEqual(outcome.action, "stop")

    def test_easy_path_short_circuits_second_retrieve(self):
        from harness.loop import LoopConfig, QueryLoop

        hit = _rr("func:main.py:cmd_query:1", "main.py", "cmd_query")
        retriever = RecordingRetriever({"cmd_query": {"final": [hit]}})
        loop = QueryLoop(retriever, RecordingBuilder(), LoopConfig(max_loops=1))
        outcome = loop.run("cmd_query", top_k=10)
        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(retriever.calls[0].get("mode"), "bm25")
        self.assertGreaterEqual(outcome.grade, 0.35)
        self.assertEqual(outcome.stop_reason, "grade")

    def test_low_grade_retries_up_to_max_loops(self):
        from harness.loop import LoopConfig, QueryLoop

        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        hit = _rr("func:main.py:cmd_query:1", "main.py", "cmd_query")
        original = "Where is the query CLI command defined in main.py?"
        retriever = RecordingRetriever(
            {original: {"final": [miss]}},
            default=[hit],
        )
        loop = QueryLoop(retriever, RecordingBuilder(), LoopConfig(max_loops=1))
        outcome = loop.run(original, top_k=10, must_cite_paths=["main.py"])
        self.assertEqual(len(retriever.calls), 2)
        self.assertNotEqual(retriever.calls[1]["query"], original)
        self.assertEqual(outcome.attempts, 2)
        self.assertIn(outcome.action, ("rewrite", "hyde", "deepen_graph", "stop"))

    def test_max_loops_caps_at_two_extra(self):
        from harness.loop import LoopConfig, QueryLoop

        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        retriever = RecordingRetriever({}, default=[miss])
        loop = QueryLoop(retriever, RecordingBuilder(), LoopConfig(max_loops=9))
        outcome = loop.run("totally unknown widget", top_k=5)
        self.assertLessEqual(len(retriever.calls), 3)
        self.assertLessEqual(outcome.attempts, 3)


class TestEvalLoopWiring(unittest.TestCase):
    def test_eval_reports_loop_and_difficulty_split(self):
        from harness.loop import LoopConfig

        hit = _rr("func:main.py:cmd_query:1", "main.py", "cmd_query")
        fixture = EvalFixture(
            id="q-cli-query",
            query="cmd_query",
            relevant_chunk_ids=["func:main.py:cmd_query"],
            must_cite_paths=["main.py"],
            difficulty="easy",
        )
        report = run_eval(
            fixtures=[fixture],
            retriever=RecordingRetriever({fixture.query: {"final": [hit]}}),
            context_builder=RecordingBuilder(),
            k=10,
            suite_name="loop",
            suite_path="loop.yaml",
            repo_path=".",
            repo_name="workspace",
            loop_config=LoopConfig(max_loops=1),
        )
        self.assertIn("by_difficulty", report["metrics"])
        self.assertIn("easy", report["metrics"]["by_difficulty"])
        self.assertIn("loop", report["cases"][0])
        self.assertGreaterEqual(report["cases"][0]["loop"]["attempts"], 1)
        self.assertIsNotNone(report["cases"][0]["trace"]["grade"])

    def test_eval_default_stays_one_shot(self):
        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        fixture = EvalFixture(
            id="q-hard",
            query="Where is the query CLI command defined in main.py?",
            relevant_chunk_ids=["func:main.py:cmd_query"],
            must_cite_paths=["main.py"],
            difficulty="hard",
        )
        retriever = RecordingRetriever({fixture.query: {"final": [miss]}})
        report = run_eval(
            fixtures=[fixture],
            retriever=retriever,
            context_builder=RecordingBuilder(),
            k=10,
            suite_name="oneshot",
            suite_path="oneshot.yaml",
            repo_path=".",
            repo_name="workspace",
        )
        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(report["cases"][0]["trace"]["action"], "stop")


class TestConfigAndCli(unittest.TestCase):
    def test_default_max_loops_is_zero(self):
        self.assertEqual(DEFAULT_CONFIG["retrieval"].get("max_loops", 0), 0)
        config = Config()
        self.assertEqual(config.retrieval.get("max_loops", 0), 0)

    def test_query_and_eval_help_expose_loop_flags(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."
        for command in ("query", "eval"):
            run = subprocess.run(
                [sys.executable, "main.py", command, "--help"],
                capture_output=True,
                text=True,
                cwd=root,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("--loop", run.stdout)
            self.assertIn("--max-loops", run.stdout)
        query = subprocess.run(
            [sys.executable, "main.py", "query", "--help"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        self.assertIn("--verify", query.stdout)


if __name__ == "__main__":
    unittest.main()
