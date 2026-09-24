"""Tests for golden-suite loading and offline eval scoring."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from harness.context_builder import ContextBuilder
from harness.config import Config
from harness.eval import load_suite, run_eval, write_report
from harness.models import Chunk, EntityType, RetrievalResult


class TestLoadSuite(unittest.TestCase):
    def test_loads_yaml_list_and_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            list_path = os.path.join(tmp, "list.yaml")
            with open(list_path, "w") as f:
                f.write(
                    "- id: q1\n"
                    "  query: How does context assembly work?\n"
                    "  relevant_chunk_ids: [class:harness/context_builder.py:ContextBuilder]\n"
                    "  must_cite_paths: [harness/context_builder.py]\n"
                    "  difficulty: easy\n"
                )
            fixtures, meta = load_suite(list_path)
            self.assertEqual(len(fixtures), 1)
            self.assertEqual(fixtures[0].id, "q1")
            self.assertEqual(fixtures[0].difficulty, "easy")
            self.assertEqual(meta["suite"], "list")

            map_path = os.path.join(tmp, "mapped.yaml")
            with open(map_path, "w") as f:
                f.write(
                    "suite: code-harness\n"
                    "k: 8\n"
                    "fixtures:\n"
                    "  - id: q2\n"
                    "    query: Who calls Retriever index_chunks?\n"
                    "    must_cite_paths: [harness/retriever.py]\n"
                    "    difficulty: hard\n"
                )
            fixtures, meta = load_suite(map_path)
            self.assertEqual(meta["suite"], "code-harness")
            self.assertEqual(meta["k"], 8)
            self.assertEqual(fixtures[0].relevant_chunk_ids, [])

    def test_loads_json_and_rejects_bad_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "suite.json")
            with open(path, "w") as f:
                json.dump(
                    {
                        "suite": "json-suite",
                        "fixtures": [
                            {
                                "id": "ok",
                                "query": "q",
                                "relevant_chunk_ids": [],
                                "must_cite_paths": ["main.py"],
                                "difficulty": "easy",
                            },
                            {"id": "missing-query"},
                        ],
                    },
                    f,
                )
            with self.assertRaises(ValueError):
                load_suite(path)

            with open(path, "w") as f:
                json.dump(
                    [
                        {
                            "id": "ok",
                            "query": "q",
                            "must_cite_paths": ["main.py"],
                            "difficulty": "medium",
                        }
                    ],
                    f,
                )
            fixtures, meta = load_suite(path)
            self.assertEqual(meta["suite"], "suite")
            self.assertEqual(fixtures[0].must_cite_paths, ["main.py"])

    def test_committed_fixture_loads(self):
        fixtures, meta = load_suite(".docs/research/eval/code-harness.fixture.yaml")
        self.assertEqual(meta["suite"], "code-harness")
        self.assertGreaterEqual(len(fixtures), 1)
        self.assertTrue(all(fx.query and fx.must_cite_paths for fx in fixtures))


class _FakeRetriever:
    def __init__(self, by_query):
        self.by_query = by_query
        self.ce_enabled = True

    def retrieve(self, query, top_k=None, debug=False, entity_id_map=None):
        payload = self.by_query[query]
        results = payload["final"]
        trace = {
            "dense": payload["dense"],
            "sparse": payload["sparse"],
            "graph": payload["graph"],
            "reranked": payload.get("reranked", []),
            "fused": payload.get("fused", payload["dense"]),
            "latencies_ms": {
                "dense": 1.0,
                "bm25": 2.0,
                "graph": 3.0,
                "ce": 4.0,
            },
        }
        if debug:
            return results, trace
        return results


class _FakeContextBuilder:
    def __init__(self, packed_by_query):
        self.packed_by_query = packed_by_query

    def build_context_report(self, query, results):
        from harness.eval import ContextReport

        packed = self.packed_by_query[query]
        return ContextReport(
            context="# packed\n" + "\n".join(packed["paths"]),
            prompt_tokens=packed["tokens"],
            packed_chunk_ids=packed["ids"],
            packed_paths=packed["paths"],
            mmr_latency_ms=5.0,
        )


def _chunk(cid, path, entity_id=None):
    return Chunk(
        id=cid,
        content="x",
        entity_id=entity_id or cid.split(":")[0] if ":" in cid else cid,
        entity_name="n",
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=2,
    )


def _rr(cid, path, entity_id=None):
    return RetrievalResult(chunk=_chunk(cid, path, entity_id), score=1.0, source="test")


class TestRunEval(unittest.TestCase):
    def test_scores_fixture_and_persists_report(self):
        gold = "func:harness/context_builder.py:build_context"
        path = "harness/context_builder.py"
        hit = _rr(f"{gold}:deadbeef", path, gold)

        from harness.eval import EvalFixture

        fixture = EvalFixture(
            id="q-context-builder",
            query="How does context assembly work?",
            relevant_chunk_ids=[gold],
            must_cite_paths=[path],
            difficulty="easy",
        )
        retriever = _FakeRetriever(
            {
                fixture.query: {
                    "dense": [hit],
                    "sparse": [hit],
                    "graph": [hit],
                    "fused": [hit],
                    "reranked": [hit],
                    "final": [hit],
                }
            }
        )
        builder = _FakeContextBuilder(
            {
                fixture.query: {
                    "ids": [hit.chunk.id],
                    "paths": [path],
                    "tokens": 128,
                }
            }
        )
        report = run_eval(
            fixtures=[fixture],
            retriever=retriever,
            context_builder=builder,
            k=10,
            suite_name="code-harness",
            suite_path="suite.yaml",
            repo_path=".",
            repo_name="workspace",
            config_snapshot={"retrieval": {"top_k": 10}},
        )
        self.assertEqual(report["metrics"]["recall_at_k"], 1.0)
        self.assertEqual(report["metrics"]["ndcg_at_k"], 1.0)
        self.assertEqual(report["metrics"]["citation_path_hit_rate"], 1.0)
        self.assertEqual(report["metrics"]["prompt_tokens_mean"], 128)
        self.assertEqual(report["cases"][0]["failures"], [])
        self.assertEqual(report["config"]["retrieval"]["top_k"], 10)
        self.assertIn("dense", report["metrics"]["latencies_ms"])
        self.assertIn("mmr", report["metrics"]["latencies_ms"])

        with tempfile.TemporaryDirectory() as tmp:
            out = write_report(report, os.path.join(tmp, "run.json"))
            with open(out) as f:
                loaded = json.load(f)
            self.assertEqual(loaded["suite"], "code-harness")
            self.assertEqual(loaded["cases"][0]["id"], "q-context-builder")


class TestContextBuilderCompat(unittest.TestCase):
    def test_build_context_still_assembles_and_reports_tokens(self):
        builder = ContextBuilder(Config())
        result = RetrievalResult(
            chunk=_chunk("func:harness/eval.py:run_eval:abcd1234", "harness/eval.py", "func:harness/eval.py:run_eval"),
            score=0.9,
            source="test",
        )
        result.chunk.content = "def run_eval():\n    return {}\n"
        context = builder.build_context("How does eval work?", [result])
        self.assertIn("Relevant Code Context", context)
        self.assertIn("harness/eval.py", context)
        fresh = RetrievalResult(
            chunk=result.chunk,
            score=0.9,
            source="test",
        )
        report = builder.build_context_report("How does eval work?", [fresh])
        self.assertIn("Relevant Code Context", report.context)
        self.assertGreater(report.prompt_tokens, 0)
        self.assertEqual(report.packed_chunk_ids, [result.chunk.id])
        self.assertEqual(report.packed_paths, ["harness/eval.py"])


class TestEvalCliSmoke(unittest.TestCase):
    def test_eval_help_and_dry_run(self):
        help_run = subprocess.run(
            [sys.executable, "main.py", "eval", "--help"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or ".",
        )
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        self.assertIn("--suite", help_run.stdout)

        query_help = subprocess.run(
            [sys.executable, "main.py", "query", "--help"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or ".",
        )
        self.assertEqual(query_help.returncode, 0, query_help.stderr)
        self.assertIn("--no-llm", query_help.stdout)

        dry = subprocess.run(
            [
                sys.executable,
                "main.py",
                "eval",
                ".",
                "--suite",
                ".docs/research/eval/code-harness.fixture.yaml",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or ".",
        )
        self.assertEqual(dry.returncode, 0, dry.stderr + dry.stdout)
        self.assertIn("q-context-builder", dry.stdout)
        self.assertIn("Dry-run OK", dry.stdout)


if __name__ == "__main__":
    unittest.main()
