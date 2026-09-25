"""VectorStore protocol, factory, and optional TurboVec adapter tests."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from harness.config import Config, DEFAULT_CONFIG
from harness.models import Chunk, EntityType


def _chunk(cid, content, path="harness/demo.py", repo="demo"):
    return Chunk(
        id=cid,
        content=content,
        entity_id=cid,
        entity_name=cid.split(":")[-1],
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=3,
        repo_name=repo,
    )


def _cfg(**vs_updates):
    cfg = Config()
    cfg.vector_store.update(vs_updates)
    return cfg


class TestBackendNaming(unittest.TestCase):
    def test_default_type_remains_chromadb(self):
        self.assertEqual(DEFAULT_CONFIG["vector_store"]["type"], "chromadb")
        self.assertEqual(
            DEFAULT_CONFIG["vector_store"]["persist_directory"],
            ".code-harness/chromadb",
        )

    def test_aliases_normalize_to_canonical_names(self):
        from harness.vector_store import normalize_backend_name

        self.assertEqual(normalize_backend_name("chroma"), "chromadb")
        self.assertEqual(normalize_backend_name("ChromaDB"), "chromadb")
        self.assertEqual(normalize_backend_name("turbovec"), "turbovec")
        self.assertEqual(normalize_backend_name("turbo-vec"), "turbovec")
        self.assertEqual(normalize_backend_name("stub"), "stub")
        with self.assertRaises(ValueError):
            normalize_backend_name("faiss")


class TestVectorStoreFactory(unittest.TestCase):
    def test_default_facade_selects_chroma_backend(self):
        from harness.vector_store import VectorStore

        store = VectorStore(_cfg())
        self.assertEqual(store.backend_name, "chromadb")
        self.assertFalse(getattr(store, "supports_allowlist", False))

    def test_turbovec_without_dep_or_stub_raises(self):
        from harness.vector_store import BackendUnavailable, VectorStore

        cfg = _cfg(type="turbovec")
        cfg.vector_store["turbovec"] = {"use_stub": False}
        try:
            import turbovec  # noqa: F401
            self.skipTest("native turbovec is installed; missing-dep path not exercised")
        except ImportError:
            pass
        with self.assertRaises(BackendUnavailable) as ctx:
            VectorStore(cfg)
        self.assertIn("turbovec", str(ctx.exception).lower())
        self.assertIn("requirements-turbovec", str(ctx.exception))

    def test_explicit_stub_is_not_named_turbovec(self):
        from harness.vector_store import VectorStore

        cfg = _cfg(type="stub")
        with tempfile.TemporaryDirectory() as tmp:
            cfg.vector_store["persist_directory"] = os.path.join(tmp, "stub")
            store = VectorStore(cfg)
            self.assertEqual(store.backend_name, "stub")
            self.assertNotEqual(store.backend_name, "turbovec")

    def test_turbovec_use_stub_is_labeled_experimental_standin(self):
        from harness.vector_store import VectorStore

        cfg = _cfg(type="turbovec")
        cfg.vector_store["turbovec"] = {"use_stub": True, "persist_directory": ""}
        with tempfile.TemporaryDirectory() as tmp:
            cfg.vector_store["turbovec"]["persist_directory"] = os.path.join(tmp, "tv")
            store = VectorStore(cfg)
            self.assertEqual(store.backend_name, "turbovec-stub")
            self.assertTrue(store.experimental)


class TestMemoryExactStoreProtocol(unittest.TestCase):
    def test_add_search_get_delete_count_round_trip(self):
        from harness.vector_store import VectorStore

        cfg = _cfg(type="stub")
        with tempfile.TemporaryDirectory() as tmp:
            cfg.vector_store["persist_directory"] = os.path.join(tmp, "stub")
            store = VectorStore(cfg)
            chunks = [
                _chunk("func:a.py:alpha", "def alpha(): return 1"),
                _chunk("func:b.py:beta", "def beta(): return 2"),
            ]
            embeddings = [
                [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            ]
            store.add_chunks(chunks, embeddings, repo_name="demo")
            self.assertEqual(store.count(), 2)

            hits = store.search([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=1)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].chunk.id, "func:a.py:alpha")
            self.assertEqual(hits[0].source, "dense")
            self.assertGreater(hits[0].score, 0.9)

            all_chunks = store.get_all(repo_name="demo")
            self.assertEqual({c.id for c in all_chunks}, {c.id for c in chunks})

            store.delete_by_repo("demo")
            self.assertEqual(store.count(), 0)

    def test_allowlist_restricts_dense_hits(self):
        from harness.vector_store import VectorStore

        cfg = _cfg(type="stub")
        with tempfile.TemporaryDirectory() as tmp:
            cfg.vector_store["persist_directory"] = os.path.join(tmp, "stub")
            store = VectorStore(cfg)
            chunks = [
                _chunk("func:a.py:alpha", "alpha"),
                _chunk("func:b.py:beta", "beta"),
            ]
            embeddings = [
                [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                [0.99, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            ]
            store.add_chunks(chunks, embeddings, repo_name="demo")
            hits = store.search(
                [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                top_k=2,
                allowlist_ids=["func:b.py:beta"],
            )
            self.assertEqual([h.chunk.id for h in hits], ["func:b.py:beta"])

    def test_repo_filter_and_persist_reload(self):
        from harness.vector_store import VectorStore

        with tempfile.TemporaryDirectory() as tmp:
            persist = os.path.join(tmp, "stub")
            cfg = _cfg(type="stub", persist_directory=persist)
            store = VectorStore(cfg)
            store.add_chunks(
                [_chunk("func:a.py:alpha", "alpha", repo="one")],
                [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
                repo_name="one",
            )
            store.add_chunks(
                [_chunk("func:b.py:beta", "beta", repo="two")],
                [[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
                repo_name="two",
            )
            self.assertEqual(len(store.get_all(repo_name="one")), 1)
            store.persist()

            again = VectorStore(_cfg(type="stub", persist_directory=persist))
            self.assertEqual(again.count(), 2)
            self.assertEqual(again.get_all(repo_name="two")[0].id, "func:b.py:beta")

    def test_embedding_model_mismatch_refuses_query(self):
        from harness.vector_store import EmbeddingModelMismatch, VectorStore

        with tempfile.TemporaryDirectory() as tmp:
            persist = os.path.join(tmp, "stub")
            cfg = _cfg(type="stub", persist_directory=persist)
            cfg.embedding["model"] = "all-MiniLM-L6-v2"
            store = VectorStore(cfg)
            store.add_chunks(
                [_chunk("func:a.py:alpha", "alpha")],
                [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
                repo_name="demo",
            )
            store.persist()

            cfg.embedding["model"] = "voyage-code-2"
            other = VectorStore(cfg)
            with self.assertRaises(EmbeddingModelMismatch) as ctx:
                other.search([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=1)
            self.assertIn("rebuild", str(ctx.exception).lower())


class TestTurboVecAdapterWithMocks(unittest.TestCase):
    def test_adapter_uses_idmap_add_search_sync(self):
        from harness.turbovec_store import TurboVecStore

        fake_index = MagicMock()
        fake_index.search.return_value = (
            [[0.91]],
            [[1001]],
        )
        store = TurboVecStore(
            _cfg(type="turbovec"),
            index=fake_index,
            sidecar={
                1001: {
                    "chunk_id": "func:a.py:alpha",
                    "content": "def alpha(): pass",
                    "entity_id": "func:a.py:alpha",
                    "entity_name": "alpha",
                    "entity_type": "function",
                    "file_path": "a.py",
                    "start_line": "1",
                    "end_line": "2",
                    "repo_name": "demo",
                    "metadata": {},
                }
            },
            embedding_model="all-MiniLM-L6-v2",
            dim=8,
        )
        hits = store.search([1.0] + [0.0] * 7, top_k=1)
        self.assertEqual(hits[0].chunk.id, "func:a.py:alpha")
        fake_index.search.assert_called()
        kwargs = fake_index.search.call_args
        self.assertIn("k", kwargs.kwargs)
        store.persist()
        fake_index.sync.assert_called()

    def test_adapter_passes_uint64_allowlist(self):
        import numpy as np

        from harness.turbovec_store import TurboVecStore

        fake_index = MagicMock()
        fake_index.search.return_value = ([[0.5]], [[7]])
        store = TurboVecStore(
            _cfg(type="turbovec"),
            index=fake_index,
            sidecar={
                7: {
                    "chunk_id": "func:b.py:beta",
                    "content": "beta",
                    "entity_id": "func:b.py:beta",
                    "entity_name": "beta",
                    "entity_type": "function",
                    "file_path": "b.py",
                    "start_line": "1",
                    "end_line": "1",
                    "repo_name": "demo",
                    "metadata": {},
                }
            },
            embedding_model="all-MiniLM-L6-v2",
            id_map={"func:b.py:beta": 7},
            dim=8,
        )
        store.search([1.0] + [0.0] * 7, top_k=1, allowlist_ids=["func:b.py:beta"])
        allowlist = fake_index.search.call_args.kwargs.get("allowlist")
        self.assertIsNotNone(allowlist)
        self.assertEqual(allowlist.dtype, np.uint64)
        self.assertEqual(list(allowlist), [7])


@unittest.skipUnless(
    importlib.util.find_spec("turbovec") is not None,
    "optional turbovec extra is not installed",
)
class TestNativeTurboVec(unittest.TestCase):
    def test_idmap_round_trip_on_tiny_corpus(self):
        from harness.vector_store import VectorStore

        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(type="turbovec")
            cfg.vector_store["turbovec"] = {
                "bits": 4,
                "persist_directory": os.path.join(tmp, "tv"),
                "use_stub": False,
            }
            cfg.embedding["model"] = "all-MiniLM-L6-v2"
            store = VectorStore(cfg)
            self.assertEqual(store.backend_name, "turbovec")
            dim = 16
            chunks = [
                _chunk("func:a.py:alpha", "alpha"),
                _chunk("func:b.py:beta", "beta"),
            ]
            embeddings = [
                [1.0] + [0.0] * (dim - 1),
                [0.0, 1.0] + [0.0] * (dim - 2),
            ]
            store.add_chunks(chunks, embeddings, repo_name="demo")
            hits = store.search([1.0] + [0.0] * (dim - 1), top_k=1)
            self.assertEqual(hits[0].chunk.id, "func:a.py:alpha")
            store.persist()
            again = VectorStore(cfg)
            self.assertEqual(again.count(), 2)


if __name__ == "__main__":
    unittest.main()
