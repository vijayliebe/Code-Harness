"""RRF wiki channel: kind=wiki / knowledge/wiki cites, default weight is off."""

import os
import tempfile
import unittest

from harness.config import Config
from harness.knowledge_graph import KnowledgeGraph
from harness.models import Chunk, EntityType, RetrievalResult
from harness.retriever import Retriever, is_wiki_chunk


def _chunk(cid, content, path, name="Page", kind=None, etype=EntityType.DOCUMENTATION):
    meta = {}
    if kind:
        meta["kind"] = kind
    return Chunk(
        id=cid,
        content=content,
        entity_id=cid,
        entity_name=name,
        entity_type=etype,
        file_path=path,
        start_line=1,
        end_line=4,
        metadata=meta,
    )


class FakeEmbedder:
    def __init__(self):
        self.hyde_enabled = False

    def embed_query(self, query, expand=True):
        return [0.1, 0.2, 0.3]


class FakeVectorStore:
    supports_allowlist = False

    def __init__(self, results):
        self._results = results

    def search(self, embedding, top_k=10, repo_name=None, allowlist_ids=None):
        return list(self._results)[:top_k]


def _retriever(chunks, dense, wiki_weight=0.0, kg=None):
    cfg = Config()
    cfg.retrieval["wiki_weight"] = wiki_weight
    cfg.retrieval["cross_encoder"] = {"enabled": False}
    cfg.retrieval["expand_neighbors"] = 0
    if kg is None:
        kg = KnowledgeGraph(cfg, repo_name="fixture")
        kg.enabled = False
    retriever = Retriever(cfg, FakeEmbedder(), FakeVectorStore(dense), kg, repo_name="fixture")
    retriever.index_chunks(chunks, persist=False)
    return retriever


class TestWikiChunkIdentity(unittest.TestCase):
    def test_kind_wiki_and_knowledge_path(self):
        wiki = _chunk("w1", "living wiki", "knowledge/wiki/pkg.md", kind="wiki")
        path_only = _chunk("w2", "architecture index", "knowledge/wiki/architecture.md")
        gloss = _chunk("g1", "tribal note", "knowledge/gloss/seed.md", kind="gloss")
        code = _chunk(
            "c1", "class Seed", "pkg/mod.py", name="Seed",
            kind=None, etype=EntityType.CLASS,
        )
        self.assertTrue(is_wiki_chunk(wiki))
        self.assertTrue(is_wiki_chunk(path_only))
        self.assertFalse(is_wiki_chunk(gloss))
        self.assertFalse(is_wiki_chunk(code))

    def test_wiki_cite_is_knowledge_wiki_page(self):
        from harness.wiki import wiki_cite

        self.assertEqual(wiki_cite("pkg.md"), "knowledge/wiki/pkg.md")
        self.assertEqual(wiki_cite("knowledge/wiki/architecture.md"), "knowledge/wiki/architecture.md")


class TestRrfWikiChannel(unittest.TestCase):
    def test_rrf_merges_wiki_list_without_changing_k60(self):
        code = _chunk("code", "class Seed", "pkg/mod.py", name="Seed", etype=EntityType.CLASS)
        wiki = _chunk("wiki", "Seed package page", "knowledge/wiki/pkg.md", kind="wiki")
        dense = [RetrievalResult(chunk=code, score=0.9, source="dense")]
        sparse = [RetrievalResult(chunk=code, score=0.8, source="sparse")]
        wiki_list = [RetrievalResult(chunk=wiki, score=0.7, source="wiki")]

        fused_off = Retriever._reciprocal_rank_fusion(
            [dense, sparse], weights=[0.3, 0.25],
        )
        fused_on = Retriever._reciprocal_rank_fusion(
            [dense, sparse, wiki_list], weights=[0.3, 0.25, 0.1],
        )
        self.assertEqual([r.chunk.id for r in fused_off], ["code"])
        ids = [r.chunk.id for r in fused_on]
        self.assertIn("code", ids)
        self.assertIn("wiki", ids)
        wiki_score = next(r.score for r in fused_on if r.chunk.id == "wiki")
        # weight * 1/(60+1)
        self.assertAlmostEqual(wiki_score, 0.1 / 61.0, places=6)

    def test_retrieve_includes_wiki_channel_when_weight_set(self):
        code = _chunk(
            "code",
            "def grow(): return 1",
            "pkg/mod.py",
            name="grow",
            etype=EntityType.FUNCTION,
        )
        wiki = _chunk(
            "wiki",
            "Package pkg documents Seed.grow assembly.",
            "knowledge/wiki/pkg.md",
            kind="wiki",
        )
        dense = [RetrievalResult(chunk=code, score=0.95, source="dense")]
        off = _retriever([code, wiki], dense, wiki_weight=0.0)
        on = _retriever([code, wiki], dense, wiki_weight=0.15)

        top_off, trace_off = off.retrieve("how does Seed grow?", debug=True)
        top_on, trace_on = on.retrieve("how does Seed grow?", debug=True)

        self.assertEqual(trace_off.get("wiki") or [], [])
        self.assertTrue(trace_on.get("wiki"))
        self.assertTrue(any(is_wiki_chunk(r.chunk) for r in trace_on["wiki"]))
        wiki_on = next(r for r in trace_on["fused"] if r.chunk.id == "wiki")
        wiki_off = next((r for r in trace_off["fused"] if r.chunk.id == "wiki"), None)
        if wiki_off is None:
            self.assertGreater(wiki_on.score, 0)
        else:
            self.assertGreater(wiki_on.score, wiki_off.score)

        # Default-off ranking still returns the code hit first.
        self.assertEqual(top_off[0].chunk.id, "code")

    def test_default_wiki_weight_is_zero(self):
        cfg = Config()
        self.assertEqual(float(cfg.retrieval.get("wiki_weight", 0.0)), 0.0)

    def test_kg_tags_wiki_documentation(self):
        from harness.models import CodeEntity

        cfg = Config()
        kg = KnowledgeGraph(cfg, repo_name="fixture")
        entity = CodeEntity(
            id="doc:knowledge/wiki/pkg.md:pkg",
            name="pkg",
            type=EntityType.DOCUMENTATION,
            file_path="knowledge/wiki/pkg.md",
            start_line=1,
            end_line=8,
            source_code="# pkg\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            persist = os.path.join(tmp, "graph.json")
            cfg.knowledge_graph["persist_path"] = persist
            kg = KnowledgeGraph(cfg, repo_name="fixture")
            kg.build([entity])
        data = kg.graph.nodes["doc:knowledge/wiki/pkg.md:pkg"]
        self.assertEqual(data.get("kind"), "wiki")


if __name__ == "__main__":
    unittest.main()
