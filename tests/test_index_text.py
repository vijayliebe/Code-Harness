"""R1: indexed text is path + symbol + signature + content; display stays plain."""

import unittest

from harness.ccr import chunk_signature
from harness.config import Config
from harness.index_text import (
    cap_prefix,
    chunk_index_prefix,
    chunk_index_text,
    enrich_index_text_enabled,
)
from harness.models import Chunk, EntityType


def _chunk(**kwargs):
    defaults = dict(
        id="func:harness/demo.py:run_eval:abcd",
        content="def run_eval(suite):\n    return 1\n",
        entity_id="func:harness/demo.py:run_eval",
        entity_name="run_eval",
        entity_type=EntityType.FUNCTION,
        file_path="harness/demo.py",
        start_line=1,
        end_line=2,
        metadata={"params": ["suite"]},
    )
    defaults.update(kwargs)
    return Chunk(**defaults)


class TestIndexText(unittest.TestCase):
    def test_off_by_default_returns_plain_content(self):
        cfg = Config()
        self.assertFalse(enrich_index_text_enabled(cfg))
        chunk = _chunk()
        self.assertEqual(chunk_index_text(chunk, cfg), chunk.content)

    def test_prefix_is_path_name_signature(self):
        chunk = _chunk()
        prefix = chunk_index_prefix(chunk)
        self.assertIn("harness/demo.py", prefix)
        self.assertIn("run_eval", prefix)
        self.assertIn(chunk_signature(chunk), prefix)
        self.assertEqual(chunk_signature(chunk), "run_eval(suite)")

    def test_enabled_prefixes_content_and_leaves_chunk_content(self):
        cfg = Config()
        cfg.indexing["enrich_index_text"] = True
        chunk = _chunk()
        indexed = chunk_index_text(chunk, cfg)
        self.assertTrue(indexed.startswith("harness/demo.py"))
        self.assertIn(chunk.content, indexed)
        self.assertTrue(indexed.endswith(chunk.content) or chunk.content in indexed)
        self.assertEqual(chunk.content, "def run_eval(suite):\n    return 1\n")

    def test_prefix_capped_at_ratio(self):
        long_prefix = "x" * 500
        capped = cap_prefix(long_prefix, "body" * 10, ratio=0.25)
        self.assertLessEqual(len(capped), 192)
        self.assertGreaterEqual(len(capped), 96)

    def test_bm25_uses_index_text_when_enabled(self):
        from harness.knowledge_graph import KnowledgeGraph
        from harness.retriever import Retriever

        class FakeEmbedder:
            hyde_enabled = False

        class FakeStore:
            supports_allowlist = False

        cfg = Config()
        cfg.indexing["enrich_index_text"] = True
        cfg.retrieval["cross_encoder"] = {"enabled": False}
        kg = KnowledgeGraph(cfg, repo_name="t")
        kg.enabled = False
        retriever = Retriever(cfg, FakeEmbedder(), FakeStore(), kg, repo_name="t")
        named = _chunk(content="return the packed tokens")
        other = _chunk(
            id="func:harness/other.py:helper:abcd",
            content="unrelated helper body",
            entity_id="func:harness/other.py:helper",
            entity_name="helper",
            file_path="harness/other.py",
            metadata={"params": []},
        )
        retriever.index_chunks([named, other], persist=False)
        tokenized = retriever._tokenize(chunk_index_text(named, cfg))
        self.assertIn("run_eval", tokenized)
        self.assertIn("harness", tokenized)
        self.assertNotIn("run_eval", retriever._tokenize(named.content))
        # rank_bm25 IDF is 0 on tiny corpora; assert the stored tokens instead.
        self.assertEqual(
            retriever._bm25_index.doc_len[0],
            len(tokenized),
        )
        self.assertEqual(retriever._bm25_chunks[0].content, named.content)
