"""BM25-over-memory RRF channel: active OKF files, default weight off."""

import os
import tempfile
import unittest

from harness.config import Config
from harness.context_builder import ContextBuilder
from harness.knowledge_graph import KnowledgeGraph
from harness.models import Chunk, EntityType, RetrievalResult
from harness.retriever import Retriever


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."

CHROMA_ID = "mem/20260924-default-vector-backend-stays-chroma"
FAISS_ID = "mem/20260101-use-faiss"
CHROMA_PATH = "knowledge/memory/decision/2026-09-24-chroma-default.md"


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


def _retriever(chunks, dense, memory_weight=0.0, repo_path=".", include_memory_search=False):
    cfg = Config()
    cfg.retrieval["memory_weight"] = memory_weight
    cfg.retrieval["cross_encoder"] = {"enabled": False}
    cfg.retrieval["expand_neighbors"] = 0
    cfg.context["include_memory_search"] = include_memory_search
    cfg.repo_path = repo_path
    kg = KnowledgeGraph(cfg, repo_name="fixture")
    kg.enabled = False
    retriever = Retriever(cfg, FakeEmbedder(), FakeVectorStore(dense), kg, repo_name="fixture")
    retriever.index_chunks(chunks, persist=False)
    return retriever


class TestMemoryBm25Scoring(unittest.TestCase):
    def test_search_finds_fixture_chroma_decision(self):
        from harness.memory import MemoryStore

        hits = MemoryStore(ROOT).search("why is Chroma the default vector backend?")
        ids = [hit.entry.id for hit in hits]
        self.assertIn(CHROMA_ID, ids)
        top = hits[0]
        self.assertEqual(top.entry.id, CHROMA_ID)
        self.assertGreater(top.score, 0)
        self.assertEqual(top.cite, CHROMA_PATH)

    def test_search_excludes_superseded_faiss(self):
        from harness.memory import MemoryStore

        hits = MemoryStore(ROOT).search("FAISS chroma vector backend")
        ids = [hit.entry.id for hit in hits]
        self.assertNotIn(FAISS_ID, ids)
        self.assertTrue(all(hit.entry.status == "active" for hit in hits))
        self.assertIn(CHROMA_ID, ids)


class TestMemoryWeightDefaultOff(unittest.TestCase):
    def test_default_memory_weight_is_zero(self):
        cfg = Config()
        self.assertEqual(float(cfg.retrieval.get("memory_weight", 0.0)), 0.0)
        self.assertFalse(cfg.context.get("include_memory_search"))

    def test_retrieve_skips_memory_channel_when_weight_zero(self):
        code = _chunk(
            "code",
            "def grow(): return 1",
            "pkg/mod.py",
            name="grow",
            etype=EntityType.FUNCTION,
        )
        dense = [RetrievalResult(chunk=code, score=0.95, source="dense")]
        retriever = _retriever([code], dense, memory_weight=0.0, repo_path=ROOT)
        top, trace = retriever.retrieve("why is Chroma the default?", debug=True)
        self.assertEqual(trace.get("memory") or [], [])
        self.assertEqual(top[0].chunk.id, "code")
        self.assertFalse(any(r.chunk.id == CHROMA_ID for r in trace.get("fused") or []))


class TestMemoryRrfMerge(unittest.TestCase):
    def test_rrf_merges_memory_list_without_changing_k60(self):
        code = _chunk("code", "class Seed", "pkg/mod.py", name="Seed", etype=EntityType.CLASS)
        memory = _chunk(
            CHROMA_ID,
            "Keep Chroma as the default vector backend",
            CHROMA_PATH,
            name="Default vector backend stays Chroma",
            kind="memory",
        )
        dense = [RetrievalResult(chunk=code, score=0.9, source="dense")]
        sparse = [RetrievalResult(chunk=code, score=0.8, source="sparse")]
        memory_list = [RetrievalResult(chunk=memory, score=0.7, source="memory")]

        fused_off = Retriever._reciprocal_rank_fusion(
            [dense, sparse], weights=[0.3, 0.25],
        )
        fused_on = Retriever._reciprocal_rank_fusion(
            [dense, sparse, memory_list], weights=[0.3, 0.25, 0.15],
        )
        self.assertEqual([r.chunk.id for r in fused_off], ["code"])
        ids = [r.chunk.id for r in fused_on]
        self.assertIn("code", ids)
        self.assertIn(CHROMA_ID, ids)
        memory_score = next(r.score for r in fused_on if r.chunk.id == CHROMA_ID)
        self.assertAlmostEqual(memory_score, 0.15 / 61.0, places=6)

    def test_retrieve_includes_memory_channel_when_weight_set(self):
        from harness.retriever import is_memory_chunk

        code = _chunk(
            "code",
            "def grow(): return 1",
            "pkg/mod.py",
            name="grow",
            etype=EntityType.FUNCTION,
        )
        dense = [RetrievalResult(chunk=code, score=0.95, source="dense")]
        off = _retriever([code], dense, memory_weight=0.0, repo_path=ROOT)
        on = _retriever([code], dense, memory_weight=0.15, repo_path=ROOT)

        top_off, trace_off = off.retrieve("why is Chroma the default vector backend?", debug=True)
        top_on, trace_on = on.retrieve("why is Chroma the default vector backend?", debug=True)

        self.assertEqual(trace_off.get("memory") or [], [])
        self.assertTrue(trace_on.get("memory"))
        self.assertTrue(any(is_memory_chunk(r.chunk) for r in trace_on["memory"]))
        self.assertTrue(any(r.chunk.id == CHROMA_ID for r in trace_on["memory"]))
        self.assertFalse(any(r.chunk.id == FAISS_ID for r in trace_on["memory"]))
        memory_on = next(r for r in trace_on["fused"] if r.chunk.id == CHROMA_ID)
        memory_off = next((r for r in trace_off["fused"] if r.chunk.id == CHROMA_ID), None)
        if memory_off is None:
            self.assertGreater(memory_on.score, 0)
        else:
            self.assertGreater(memory_on.score, memory_off.score)
        self.assertEqual(top_off[0].chunk.id, "code")
        self.assertEqual(memory_on.chunk.file_path, CHROMA_PATH)

    def test_include_memory_search_enables_channel(self):
        from harness.memory import MEMORY_SEARCH_WEIGHT, apply_memory_search

        cfg = Config()
        self.assertEqual(float(cfg.retrieval.get("memory_weight", 0.0)), 0.0)
        apply_memory_search(cfg, True)
        self.assertTrue(cfg.context.get("include_memory_search"))
        self.assertAlmostEqual(float(cfg.retrieval["memory_weight"]), MEMORY_SEARCH_WEIGHT)
        cfg.retrieval["memory_weight"] = 0.4
        apply_memory_search(cfg, True)
        self.assertAlmostEqual(float(cfg.retrieval["memory_weight"]), 0.4)

        code = _chunk(
            "code",
            "def grow(): return 1",
            "pkg/mod.py",
            name="grow",
            etype=EntityType.FUNCTION,
        )
        dense = [RetrievalResult(chunk=code, score=0.95, source="dense")]
        retriever = _retriever(
            [code], dense, memory_weight=0.0, repo_path=ROOT, include_memory_search=True,
        )
        _top, trace = retriever.retrieve("why is Chroma the default?", debug=True)
        self.assertTrue(any(r.chunk.id == CHROMA_ID for r in (trace.get("memory") or [])))


class TestMemoryCiteAndIdentity(unittest.TestCase):
    def test_memory_cite_is_knowledge_memory_path(self):
        from harness.memory import MemoryStore
        from harness.okf import is_memory_path, memory_cite
        from harness.retriever import is_memory_chunk

        self.assertEqual(
            memory_cite("decision/2026-09-24-chroma-default.md"),
            CHROMA_PATH,
        )
        self.assertEqual(memory_cite(CHROMA_PATH), CHROMA_PATH)
        self.assertTrue(is_memory_path(CHROMA_PATH))
        self.assertTrue(is_memory_path(".code-harness/memory/decision/x.md"))
        self.assertFalse(is_memory_path("knowledge/wiki/pkg.md"))

        entry = MemoryStore(ROOT).get(CHROMA_ID)
        self.assertEqual(memory_cite(entry), CHROMA_PATH)
        chunk = _chunk(CHROMA_ID, "chroma", CHROMA_PATH, kind="memory")
        self.assertTrue(is_memory_chunk(chunk))
        self.assertFalse(is_memory_chunk(_chunk("c1", "class Seed", "pkg/mod.py")))


class TestMemoryPackerDedupe(unittest.TestCase):
    def test_memory_brief_drops_matching_rrf_hits(self):
        from harness.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            entry = store.add(
                kind="decision",
                title="Default vector backend stays Chroma",
                body="Keep Chroma until TurboVec recall@k passes.",
                links=["harness/vector_store.py:VectorStore"],
            )
            cite = f"knowledge/memory/{entry.rel_path}"
            memory_hit = RetrievalResult(
                chunk=_chunk(entry.id, entry.body, cite, name=entry.title, kind="memory"),
                score=0.8,
                source="memory",
            )
            code = RetrievalResult(
                chunk=_chunk(
                    "code",
                    "def grow(): return 1",
                    "pkg/mod.py",
                    name="grow",
                    etype=EntityType.FUNCTION,
                ),
                score=0.9,
                source="dense",
            )
            cfg = Config.from_dict(
                {"repo_path": tmp, "context": {"include_memory_brief": True}}
            )
            cfg.repo_path = tmp
            report = ContextBuilder(cfg).build_context_report(
                "why chroma?", [code, memory_hit],
            )
            self.assertIn("Project memory brief", report.context)
            self.assertEqual(report.context.count("Default vector backend stays Chroma"), 1)
            self.assertNotIn(entry.id, report.packed_chunk_ids)

    def test_knowledge_prefix_does_not_double_memory_rrf_hit(self):
        from harness.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            entry = store.add(
                kind="decision",
                title="Default vector backend stays Chroma",
                body="Keep Chroma until TurboVec recall@k passes the eval suite.",
                links=["harness/vector_store.py:VectorStore"],
            )
            cite = f"knowledge/memory/{entry.rel_path}"
            memory_hit = RetrievalResult(
                chunk=_chunk(entry.id, entry.body, cite, name=entry.title, kind="memory"),
                score=0.8,
                source="memory",
            )
            cfg = Config.from_dict(
                {"repo_path": tmp, "context": {"knowledge_prefix": True}}
            )
            cfg.repo_path = tmp
            report = ContextBuilder(cfg).build_context_report("why chroma?", [memory_hit])
            self.assertEqual(report.context.count(cite), 1)
            self.assertEqual(
                report.context.count("Keep Chroma until TurboVec recall@k passes the eval suite."),
                1,
            )


class TestMemorySearchCli(unittest.TestCase):
    def test_cli_search_prints_fixture_decision(self):
        import subprocess
        import sys

        run = subprocess.run(
            [sys.executable, "main.py", "memory", "search", ROOT, "-q", "chroma default"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(CHROMA_ID, run.stdout)
        self.assertIn(CHROMA_PATH, run.stdout)
        self.assertNotIn(FAISS_ID, run.stdout)

    def test_cli_search_redacts_outbound(self):
        import subprocess
        import sys

        secret = "sk-proj-" + "TESTFAKESECRETKEY1234567890ABCD"
        with tempfile.TemporaryDirectory() as tmp:
            from harness.memory import MemoryStore

            MemoryStore(tmp).add(
                kind="fact",
                title="CI token reminder",
                body=f"Do not paste {secret} into prompts.",
            )
            run = subprocess.run(
                [
                    sys.executable, os.path.join(ROOT, "main.py"),
                    "memory", "search", tmp, "-q", "CI token", "--redact",
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertNotIn(secret, run.stdout)
            self.assertIn("REDACTED", run.stdout)


if __name__ == "__main__":
    unittest.main()
