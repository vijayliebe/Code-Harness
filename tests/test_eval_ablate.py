"""Offline tests for harness-on vs harness-off ablation (no network, no LLM)."""

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

from harness.config import Config, DEFAULT_CONFIG
from harness.eval import EvalFixture, run_eval
from harness.eval_ablate import (
    ABLATION_VARIANTS,
    EmptyRetriever,
    apply_ablation,
    llm_key_available,
    render_ablation_markdown,
    row_from_report,
    run_ablation,
)
from harness.models import Chunk, EntityType, RetrievalResult


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


def _chunk(cid, path, name="n"):
    return Chunk(
        id=cid,
        content="x",
        entity_id=cid,
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=2,
    )


def _report(recall=1.0, ndcg=0.9, cite=0.5, n=8, k=10, failures=None):
    return {
        "suite": "code-harness",
        "k": k,
        "metrics": {
            "k": k,
            "n": n,
            "recall_at_k": recall,
            "ndcg_at_k": ndcg,
            "citation_path_hit_rate": cite,
            "prompt_tokens_full_mean": 400.0,
            "prompt_tokens_packed_mean": 200.0,
            "prompt_token_drop": 0.5,
            "latencies_ms": {
                "dense": {"mean": 10.0, "p50": 10.0},
                "bm25": {"mean": 2.0, "p50": 2.0},
                "graph": {"mean": 1.0, "p50": 1.0},
                "ce": {"mean": 5.0, "p50": 5.0},
                "mmr": {"mean": 0.5, "p50": 0.5},
            },
            "failure_counts": failures or {},
        },
        "cases": [],
        "config": {},
    }


class TestAblationVariants(unittest.TestCase):
    def test_default_config_unchanged_by_clone(self):
        cfg = Config()
        self.assertEqual(cfg.retrieval.get("dense_weight"), DEFAULT_CONFIG["retrieval"]["dense_weight"])
        self.assertEqual(cfg.retrieval.get("sparse_weight"), DEFAULT_CONFIG["retrieval"]["sparse_weight"])
        self.assertEqual(cfg.retrieval.get("graph_weight"), DEFAULT_CONFIG["retrieval"]["graph_weight"])
        self.assertTrue((cfg.retrieval.get("cross_encoder") or {}).get("enabled", True))
        self.assertFalse((cfg.retrieval.get("hyde") or {}).get("enabled", False))
        self.assertEqual(cfg.context.get("pack_mode"), "full")
        self.assertEqual(cfg.retrieval.get("max_loops", 0), 0)
        self.assertFalse(DEFAULT_CONFIG["decision"]["enabled"])
        self.assertFalse(DEFAULT_CONFIG["session"]["verify"])

    def test_full_is_a_clone_not_a_mutation(self):
        src = Config()
        src.retrieval["dense_weight"] = 0.3
        out = apply_ablation(src, "full")
        out.retrieval["dense_weight"] = 0.0
        self.assertEqual(src.retrieval["dense_weight"], 0.3)
        self.assertFalse(out.retrieval.get("no_retrieval"))

    def test_leave_one_out_patches(self):
        no_dense = apply_ablation(Config(), "no_dense")
        self.assertFalse(no_dense.retrieval.get("dense_enabled"))
        self.assertEqual(no_dense.retrieval.get("dense_weight"), 0.0)
        self.assertEqual(no_dense.retrieval.get("sparse_weight"), DEFAULT_CONFIG["retrieval"]["sparse_weight"])

        no_bm25 = apply_ablation(Config(), "no_bm25")
        self.assertFalse(no_bm25.retrieval.get("sparse_enabled"))
        self.assertEqual(no_bm25.retrieval.get("sparse_weight"), 0.0)

        no_graph = apply_ablation(Config(), "no_graph")
        self.assertFalse(no_graph.retrieval.get("graph_enabled"))
        self.assertEqual(no_graph.retrieval.get("expand_neighbors"), 0)
        self.assertEqual(no_graph.retrieval.get("graph_weight"), 0.0)

        no_rerank = apply_ablation(Config(), "no_rerank")
        self.assertFalse((no_rerank.retrieval.get("cross_encoder") or {}).get("enabled"))
        self.assertTrue((Config().retrieval.get("cross_encoder") or {}).get("enabled", True))

        no_mmr = apply_ablation(Config(), "no_mmr")
        self.assertFalse(no_mmr.retrieval.get("mmr"))
        self.assertEqual(Config().retrieval.get("mmr", True), True)

    def test_optional_on_and_no_retrieval(self):
        hyde = apply_ablation(Config(), "hyde")
        self.assertTrue((hyde.retrieval.get("hyde") or {}).get("enabled"))
        self.assertFalse((Config().retrieval.get("hyde") or {}).get("enabled", False))

        ccr = apply_ablation(Config(), "ccr_lite")
        self.assertEqual(ccr.context.get("pack_mode"), "ccr_lite")
        self.assertEqual(Config().context.get("pack_mode"), "full")

        empty = apply_ablation(Config(), "no_retrieval")
        self.assertTrue(empty.retrieval.get("no_retrieval"))
        self.assertFalse(Config().retrieval.get("no_retrieval", False))

    def test_unknown_variant_raises(self):
        with self.assertRaises(ValueError):
            apply_ablation(Config(), "not-a-variant")

    def test_variant_list_covers_requested_axes(self):
        ids = [v.id for v in ABLATION_VARIANTS]
        for needed in (
            "full",
            "no_dense",
            "no_bm25",
            "no_graph",
            "no_rerank",
            "no_mmr",
            "hyde",
            "ccr_lite",
            "no_retrieval",
        ):
            self.assertIn(needed, ids)


class TestEmptyRetrieverEval(unittest.TestCase):
    def test_no_retrieval_recall_and_citation_are_zero(self):
        fixture = EvalFixture(
            id="q-context-builder",
            query="How does ContextBuilder assemble MMR context?",
            relevant_chunk_ids=["class:harness/context_builder.py:ContextBuilder"],
            must_cite_paths=["harness/context_builder.py"],
            difficulty="easy",
        )

        class _Builder:
            def build_context_report(self, query, results):
                from harness.context_builder import ContextReport

                return ContextReport(
                    context="No relevant code found.",
                    prompt_tokens=6,
                    packed_chunk_ids=[],
                    packed_paths=[],
                    prompt_tokens_full=6,
                    prompt_tokens_packed=6,
                )

        report = run_eval(
            fixtures=[fixture],
            retriever=EmptyRetriever(),
            context_builder=_Builder(),
            k=10,
            suite_name="ablate",
            suite_path="suite.yaml",
            repo_path=".",
            repo_name="workspace",
        )
        self.assertEqual(report["metrics"]["recall_at_k"], 0.0)
        self.assertEqual(report["metrics"]["ndcg_at_k"], 0.0)
        self.assertEqual(report["metrics"]["citation_path_hit_rate"], 0.0)


class TestAblationRunner(unittest.TestCase):
    def test_runner_writes_table_from_mocked_eval(self):
        seen = []

        def eval_one(cfg, variant):
            seen.append(variant.id)
            if variant.id == "no_retrieval":
                return _report(recall=0.0, ndcg=0.0, cite=0.0, failures={"dense_miss": 8})
            if variant.id == "no_dense":
                return _report(recall=0.5, ndcg=0.4, cite=0.4, failures={"dense_miss": 4})
            return _report(recall=0.75, ndcg=0.7, cite=0.6)

        with tempfile.TemporaryDirectory() as tmp:
            md_path = os.path.join(tmp, "RESULTS-ablation.md")
            json_path = os.path.join(tmp, "RESULTS-ablation.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_ablation(
                    repo=tmp,
                    suite=".docs/research/eval/code-harness.fixture.yaml",
                    markdown_path=md_path,
                    json_path=json_path,
                    eval_one=eval_one,
                    config=Config(),
                    k=10,
                    commit_sha="deadbeef",
                    answer_level={
                        "ran": False,
                        "reason": "No chat-LLM key in the environment. Answer-level comparison was not run.",
                    },
                )
            self.assertEqual(code, 0)
            self.assertEqual(seen, [v.id for v in ABLATION_VARIANTS])
            with open(md_path) as fh:
                md = fh.read()
            self.assertIn("Recall@10", md)
            self.assertIn("nDCG@10", md)
            self.assertIn("citation-path", md)
            self.assertIn("tokens full", md)
            self.assertIn("tokens packed", md)
            self.assertIn("no_retrieval", md)
            self.assertIn("0.0000", md)
            self.assertIn("deadbeef", md)
            self.assertIn("Answer-level comparison was not run", md)
            self.assertIn("eval-ablate", md)
            with open(json_path) as fh:
                payload = json.load(fh)
            self.assertEqual(payload["kind"], "harness-ablation")
            self.assertEqual(payload["variants"]["no_retrieval"]["metrics"]["recall_at_k"], 0.0)

    def test_markdown_lists_stage_latency_and_failures(self):
        rows = {
            "full": row_from_report(_report(failures={"packer_drop": 1})),
            "no_retrieval": row_from_report(_report(0.0, 0.0, 0.0, failures={"dense_miss": 8})),
        }
        md = render_ablation_markdown(
            rows=rows,
            suite="code-harness",
            suite_path=".docs/research/eval/code-harness.fixture.yaml",
            k=10,
            commit_sha="abc123",
            generated="2026-10-17T00:00:00Z",
            machine={"summary": "python 3.12; test"},
            answer_level={"ran": False, "reason": "No chat-LLM key in the environment."},
        )
        self.assertIn("dense ms", md)
        self.assertIn("packer_drop=1", md)
        self.assertIn("abc123", md)
        self.assertIn("No chat-LLM key", md)


class TestLlmKeyProbe(unittest.TestCase):
    def test_no_key_is_false(self):
        self.assertFalse(llm_key_available(environ={}, config=Config()))

    def test_openai_key_is_true(self):
        self.assertTrue(llm_key_available(environ={"OPENAI_API_KEY": "sk-test"}))


class TestCliAndMake(unittest.TestCase):
    def test_eval_and_eval_ab_help_list_ablate(self):
        for command in ("eval", "eval-ab"):
            run = subprocess.run(
                [sys.executable, "main.py", command, "--help"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("--ablate", run.stdout)

    def test_makefile_has_eval_ablate(self):
        with open(os.path.join(ROOT, "Makefile")) as fh:
            body = fh.read()
        self.assertIn("eval-ablate", body)
        self.assertIn("--ablate", body)


class TestRetrieverSkipFlags(unittest.TestCase):
    def test_dense_enabled_false_skips_vector_search(self):
        from harness.retriever import Retriever

        class _VS:
            def search(self, *args, **kwargs):
                raise AssertionError("dense search must not run")

        class _Emb:
            hyde_enabled = False

            def embed_query(self, query, expand=True):
                raise AssertionError("embed must not run when dense is off")

        class _KG:
            enabled = True

            def expand_beam(self, *args, **kwargs):
                return set()

            def get_related_chunks(self, *args, **kwargs):
                return set()

        cfg = apply_ablation(Config(), "no_dense")
        retriever = Retriever(cfg, _Emb(), _VS(), _KG(), repo_name="t")
        retriever._bm25_index = None
        results, trace = retriever.retrieve("ContextBuilder", top_k=5, debug=True)
        self.assertEqual(trace["dense"], [])
        self.assertEqual(trace["latencies_ms"].get("dense"), 0.0)


class TestMmrFlag(unittest.TestCase):
    def test_mmr_off_skips_diversity_and_reports_zero_ms(self):
        from harness.context_builder import ContextBuilder

        cfg = apply_ablation(Config(), "no_mmr")
        builder = ContextBuilder(cfg)
        hits = [
            RetrievalResult(
                chunk=_chunk(f"id-{i}", f"f{i}.py", f"n{i}"),
                score=1.0 - i * 0.01,
                source="test",
            )
            for i in range(4)
        ]
        for hit in hits:
            hit.chunk.content = "def n():\n    return 1\n"
        with patch.object(ContextBuilder, "_diversity_rerank", side_effect=AssertionError("mmr")):
            report = builder.build_context_report("query", hits)
        self.assertEqual(report.mmr_latency_ms, 0.0)


if __name__ == "__main__":
    unittest.main()
