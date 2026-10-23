"""R3: original + identifier + HyDE template, weighted RRF, no LLM."""

import unittest

from harness.config import Config
from harness.knowledge_graph import KnowledgeGraph
from harness.models import Chunk, EntityType, RetrievalResult
from harness.retriever import Retriever


def _chunk(cid, name, content, path="harness/demo.py"):
    return Chunk(
        id=cid,
        content=content,
        entity_id=cid,
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=4,
    )


class FakeEmbedder:
    def __init__(self):
        self.hyde_enabled = False
        self.queries = []

    def embed_query(self, text, expand=True):
        self.queries.append((text, expand))
        return [0.1, 0.2, 0.3]

    def _generate_hypothetical(self, query):
        return (
            f"The following code implements {query}. "
            f"It contains classes, functions, and methods related to {query}."
        )


class FakeStore:
    supports_allowlist = False

    def __init__(self, by_query_text=None, default=None):
        self.by_query_text = by_query_text or {}
        self.default = default or []
        self.calls = []

    def search(self, embedding, top_k=10, repo_name=None, allowlist_ids=None):
        self.calls.append({"top_k": top_k, "allowlist": allowlist_ids})
        return list(self.default)[:top_k]


def _retriever(chunks, dense, mq_enabled=True):
    cfg = Config()
    cfg.retrieval["cross_encoder"] = {"enabled": False}
    cfg.retrieval["expand_neighbors"] = 0
    cfg.retrieval["multi_query"]["enabled"] = mq_enabled
    kg = KnowledgeGraph(cfg, repo_name="t")
    kg.enabled = False
    retriever = Retriever(cfg, FakeEmbedder(), FakeStore(default=dense), kg, repo_name="t")
    retriever.index_chunks(chunks, persist=False)
    return retriever


class TestMultiQuery(unittest.TestCase):
    def test_variants_are_original_identifier_hyde(self):
        chunk = _chunk("c1", "ContextBuilder", "class ContextBuilder:\n    pass\n")
        retriever = _retriever([chunk], [], mq_enabled=True)
        variants = retriever._query_variants(
            "How does ContextBuilder assemble MMR context?"
        )
        names = [row[0] for row in variants]
        self.assertEqual(names[0], "original")
        self.assertIn("identifier", names)
        self.assertIn("hyde", names)
        ident = next(text for name, text, _w in variants if name == "identifier")
        self.assertIn("ContextBuilder", ident)
        hyde = next(text for name, text, _w in variants if name == "hyde")
        self.assertIn("The following code implements", hyde)

    def test_skip_expansion_keeps_only_original(self):
        chunk = _chunk("c1", "ContextBuilder", "class ContextBuilder:\n    pass\n")
        retriever = _retriever([chunk], [], mq_enabled=True)
        variants = retriever._query_variants("How does ContextBuilder work?", skip_expansion=True)
        self.assertEqual([row[0] for row in variants], ["original"])

    def test_disabled_is_original_only(self):
        chunk = _chunk("c1", "run_eval", "def run_eval():\n    pass\n")
        retriever = _retriever([chunk], [], mq_enabled=False)
        variants = retriever._query_variants("How does run_eval score a suite?")
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0][0], "original")

    def test_dedupe_sums_weights(self):
        merged = Retriever._dedupe_query_variants(
            [("original", "Foo", 1.0), ("identifier", "foo", 1.0), ("hyde", "Bar", 0.7)]
        )
        self.assertEqual(len(merged), 2)
        by_name = {name: weight for name, _text, weight in merged}
        self.assertEqual(by_name["original"], 2.0)
        self.assertEqual(by_name["hyde"], 0.7)

    def test_retrieve_runs_each_variant(self):
        chunk = _chunk("c1", "ContextBuilder", "class ContextBuilder:\n    pass\n")
        dense = [RetrievalResult(chunk=chunk, score=0.8, source="dense")]
        retriever = _retriever([chunk], dense, mq_enabled=True)
        retriever.retrieve("How does ContextBuilder assemble MMR context?", top_k=5)
        self.assertGreaterEqual(len(retriever.embedder.queries), 2)
        self.assertGreaterEqual(len(retriever.vector_store.calls), 2)
