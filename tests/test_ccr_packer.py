"""Unit/smoke tests for CCR-lite pack modes and retrieve-back cache."""

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

from harness.ccr import CCRCache, pack_chunk, retrieve_chunk, sanitize_chunk_id
from harness.config import Config
from harness.context_builder import ContextBuilder
from harness.eval import EvalFixture, run_eval
from harness.models import Chunk, EntityType, RetrievalResult


def _chunk(
    cid="func:harness/demo.py:big_fn:abcd1234",
    path="harness/demo.py",
    name="big_fn",
    entity_type=EntityType.FUNCTION,
    content=None,
    docstring="",
    start=10,
    end=80,
    metadata=None,
):
    if content is None:
        body = [
            "    value_%d = compute_item(config, state, cache, index=%d)" % (i, i)
            for i in range(80)
        ]
        content = "def big_fn(config):\n    \"\"\"Pack large functions.\"\"\"\n" + "\n".join(body)
    return Chunk(
        id=cid,
        content=content,
        entity_id=":".join(cid.split(":")[:3]) if cid.count(":") >= 2 else cid,
        entity_name=name,
        entity_type=entity_type,
        file_path=path,
        start_line=start,
        end_line=end,
        docstring=docstring,
        metadata=metadata or {"params": ["config"]},
    )


def _rr(chunk, score=0.9):
    return RetrievalResult(chunk=chunk, score=score, source="test")


class TestPackChunk(unittest.TestCase):
    def test_large_chunk_keeps_signature_and_omit_marker(self):
        packed = pack_chunk(_chunk())
        self.assertTrue(packed.omitted)
        self.assertGreater(packed.omitted_line_count, 0)
        self.assertIn("func:harness/demo.py:big_fn:abcd1234", packed.preview)
        self.assertIn("retrieve_chunk", packed.preview)
        self.assertIn("big_fn(config)", packed.preview)
        self.assertIn("def big_fn", packed.preview)
        self.assertNotIn("value_40", packed.preview)

    def test_tiny_chunk_is_not_omitted(self):
        small = _chunk(
            content="def tiny():\n    return 1\n",
            name="tiny",
            metadata={"params": []},
        )
        packed = pack_chunk(small)
        self.assertFalse(packed.omitted)
        self.assertEqual(packed.omitted_line_count, 0)
        self.assertIn("return 1", packed.preview)
        self.assertNotIn("lines omitted", packed.preview)

    def test_preview_includes_docstring_or_first_sentence(self):
        packed = pack_chunk(_chunk(docstring="Smart code chunking (entity-type aware)."))
        self.assertIn("Smart code chunking", packed.preview)

    def test_short_wide_chunk_does_not_report_negative_omit(self):
        content = "long_line_a = '" + ("x" * 400) + "'\nlong_line_b = '" + ("y" * 400) + "'\n"
        packed = pack_chunk(_chunk(content=content, name="wide"))
        self.assertGreaterEqual(packed.omitted_line_count, 0)
        self.assertFalse(packed.omitted)
        self.assertNotIn("(-1 lines", packed.preview)
        self.assertIn("long_line_a", packed.preview)


class TestCCRCache(unittest.TestCase):
    def test_memory_retrieve_back_returns_original(self):
        cache = CCRCache()
        chunk = _chunk()
        cache.put(chunk.id, chunk.content)
        self.assertEqual(cache.get(chunk.id), chunk.content)
        self.assertEqual(retrieve_chunk(chunk.id, cache=cache), chunk.content)

    def test_spill_file_roundtrip(self):
        chunk = _chunk()
        with tempfile.TemporaryDirectory() as tmp:
            cache = CCRCache(spill_dir=tmp)
            cache.put(chunk.id, chunk.content)
            path = os.path.join(tmp, sanitize_chunk_id(chunk.id) + ".txt")
            self.assertTrue(os.path.isfile(path))
            cold = CCRCache(spill_dir=tmp)
            self.assertEqual(cold.get(chunk.id), chunk.content)
            self.assertEqual(retrieve_chunk(chunk.id, spill_dir=tmp), chunk.content)

    def test_missing_id_returns_none(self):
        self.assertIsNone(CCRCache().get("no-such-id"))
        self.assertIsNone(retrieve_chunk("no-such-id"))


class TestContextBuilderPackModes(unittest.TestCase):
    def test_full_default_matches_legacy_assembly(self):
        builder = ContextBuilder(Config())
        self.assertEqual(builder.pack_mode, "full")
        result = _rr(_chunk(content="def run_eval():\n    return {}\n", name="run_eval"))
        context = builder.build_context("How does eval work?", [result])
        self.assertIn("# Query: How does eval work?", context)
        self.assertIn("## Relevant Code Context", context)
        self.assertIn("### File: harness/demo.py", context)
        self.assertIn("def run_eval():", context)
        self.assertIn("return {}", context)
        report = builder.build_context_report("How does eval work?", [_rr(result.chunk)])
        self.assertEqual(report.pack_mode, "full")
        self.assertEqual(report.prompt_tokens, report.prompt_tokens_full)
        self.assertGreater(report.prompt_tokens_full, 0)
        self.assertGreater(report.prompt_tokens_packed, 0)

    def test_ccr_lite_is_shorter_and_prefix_then_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            arch = os.path.join(tmp, "ARCHITECTURE.md")
            with open(arch, "w") as fh:
                fh.write("# Architecture\nStable prefix docs.\n")
            config = Config.from_dict(
                {
                    "repo_path": tmp,
                    "context": {"pack_mode": "ccr_lite"},
                    "ccr": {"spill_dir": os.path.join(tmp, "ccr")},
                }
            )
            config.repo_path = tmp
            builder = ContextBuilder(config)
            long_chunk = _chunk()
            report = builder.build_context_report("how does the chunker work?", [_rr(long_chunk)])
            self.assertEqual(report.pack_mode, "ccr_lite")
            self.assertIn("ARCHITECTURE.md", report.context)
            self.assertIn("retrieve_chunk", report.context)
            self.assertIn(long_chunk.id, report.context)
            self.assertLess(report.prompt_tokens_packed, report.prompt_tokens_full)
            drop = 1.0 - (report.prompt_tokens_packed / report.prompt_tokens_full)
            self.assertGreaterEqual(drop, 0.30)
            arch_pos = report.context.find("ARCHITECTURE.md")
            hit_pos = report.context.find(long_chunk.id)
            self.assertGreaterEqual(arch_pos, 0)
            self.assertGreater(hit_pos, arch_pos)
            self.assertEqual(builder.cache.get(long_chunk.id), long_chunk.content)
            spilled = os.path.join(tmp, "ccr", sanitize_chunk_id(long_chunk.id) + ".txt")
            self.assertTrue(os.path.isfile(spilled), spilled)

    def test_expand_chunk_materializes_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.from_dict(
                {
                    "context": {"pack_mode": "ccr_lite"},
                    "ccr": {"spill_dir": tmp},
                }
            )
            builder = ContextBuilder(config)
            chunk = _chunk()
            report = builder.build_context_report("explain", [_rr(chunk)])
            self.assertNotIn("value_40", report.context)
            expanded = builder.expand_into_context(report.context, [chunk.id])
            self.assertIn("value_40", expanded)
            self.assertIn(chunk.content.split("\n")[5], expanded)

    def test_prefix_hash_is_stable_for_same_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "AGENTS.md"), "w") as fh:
                fh.write("Be careful.\n")
            config = Config.from_dict(
                {
                    "repo_path": tmp,
                    "context": {"pack_mode": "ccr_lite"},
                    "ccr": {"spill_dir": os.path.join(tmp, "ccr")},
                }
            )
            config.repo_path = tmp
            builder = ContextBuilder(config)
            a = builder.build_context_report("q1", [_rr(_chunk())])
            b = builder.build_context_report("q2", [_rr(_chunk(cid="other:id"))])
            self.assertTrue(a.prefix_hash)
            self.assertEqual(a.prefix_hash, b.prefix_hash)
            expected = hashlib.sha256(b"Be careful.\n").hexdigest()[:12]
            self.assertEqual(a.prefix_hash, expected)


class _FakeRetriever:
    def __init__(self, results):
        self.results = results
        self.ce_enabled = False

    def retrieve(self, query, top_k=None, debug=False, entity_id_map=None):
        trace = {
            "dense": self.results,
            "sparse": self.results,
            "graph": [],
            "fused": self.results,
            "reranked": [],
            "latencies_ms": {"dense": 1.0, "bm25": 1.0, "graph": 0.0},
        }
        if debug:
            return self.results, trace
        return self.results


class TestEvalTokenColumns(unittest.TestCase):
    def test_eval_reports_full_and_packed_tokens(self):
        chunk = _chunk()
        fixture = EvalFixture(
            id="q-pack",
            query="How does packing work?",
            relevant_chunk_ids=[chunk.entity_id],
            must_cite_paths=["harness/demo.py"],
            difficulty="easy",
        )
        builder = ContextBuilder(Config.from_dict({"context": {"pack_mode": "full"}}))
        report = run_eval(
            fixtures=[fixture],
            retriever=_FakeRetriever([_rr(chunk)]),
            context_builder=builder,
            k=10,
            suite_name="pack",
            suite_path="pack.yaml",
            repo_path=".",
            repo_name="workspace",
        )
        case = report["cases"][0]
        self.assertIn("prompt_tokens_full", case)
        self.assertIn("prompt_tokens_packed", case)
        self.assertGreater(case["prompt_tokens_full"], case["prompt_tokens_packed"])
        self.assertEqual(report["metrics"]["recall_at_k"], 1.0)
        self.assertIn("prompt_tokens_full_mean", report["metrics"])
        self.assertIn("prompt_tokens_packed_mean", report["metrics"])
        self.assertGreaterEqual(report["metrics"]["prompt_token_drop"], 0.30)


class TestCliAffordance(unittest.TestCase):
    def test_query_help_exposes_pack_and_expand(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."
        help_run = subprocess.run(
            [sys.executable, "main.py", "query", "--help"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        self.assertIn("--pack-mode", help_run.stdout)
        self.assertIn("--expand-chunk", help_run.stdout)

        retrieve_help = subprocess.run(
            [sys.executable, "main.py", "retrieve-chunk", "--help"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        self.assertEqual(retrieve_help.returncode, 0, retrieve_help.stderr)
        self.assertIn("chunk", retrieve_help.stdout.lower())


if __name__ == "__main__":
    unittest.main()
