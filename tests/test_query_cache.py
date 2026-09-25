"""Interactive / eval query-hash cache: hit, miss, invalidate, CLI flags."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from harness.config import Config
from harness.context_builder import ContextReport
from harness.eval import EvalFixture, print_summary, run_eval
from harness.loop import LoopConfig, QueryLoop
from harness.models import Chunk, EntityType, RetrievalResult


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


def _chunk(cid, path="harness/demo.py", name="run_eval"):
    return Chunk(
        id=cid,
        content=f"def {name}():\n    return 1\n",
        entity_id=":".join(cid.split(":")[:3]),
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=3,
    )


def _rr(cid, path="harness/demo.py", name="run_eval", score=0.9):
    return RetrievalResult(chunk=_chunk(cid, path, name), score=score, source="test")


class RecordingRetriever:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.ce_enabled = False

    def retrieve(self, query, top_k=None, debug=False, **kwargs):
        self.calls.append(query)
        if debug:
            return list(self.results), {
                "dense": list(self.results),
                "sparse": list(self.results),
                "graph": [],
                "fused": list(self.results),
                "reranked": [],
                "latencies_ms": {"dense": 1.0, "bm25": 1.0, "graph": 0.0, "ce": 0.0},
            }
        return list(self.results)


class RecordingBuilder:
    def build_context_report(self, query, results):
        ids = [getattr(item, "chunk", item).id for item in results]
        paths = []
        for item in results:
            path = getattr(item, "chunk", item).file_path
            if path not in paths:
                paths.append(path)
        return ContextReport(
            context=f"# packed {query}\n" + "\n".join(paths),
            prompt_tokens=16 * max(1, len(results)),
            packed_chunk_ids=ids,
            packed_paths=paths,
            mmr_latency_ms=1.0,
            pack_mode="full",
        )


def _cfg(tmp, **retrieval):
    cfg = Config()
    cfg.repo_path = tmp
    cfg.vector_store["persist_directory"] = os.path.join(tmp, ".code-harness", "chromadb")
    cfg.knowledge_graph["persist_path"] = os.path.join(tmp, ".code-harness", "graph.json")
    cfg.retrieval.update(retrieval)
    return cfg


class TestQueryCacheCore(unittest.TestCase):
    def test_identical_query_hits_and_skips_retrieve(self):
        from harness.query_cache import QueryCache, run_with_query_cache

        hit = _rr("func:harness/demo.py:run_eval:abcd")
        retriever = RecordingRetriever([hit])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp)
            cache = QueryCache(os.path.join(tmp, "cache.sqlite"))
            loop = QueryLoop(retriever, RecordingBuilder(), LoopConfig(max_loops=0))
            first = run_with_query_cache(
                loop,
                "how does eval work?",
                top_k=5,
                cache=cache,
                config=cfg,
                repo_name="fixture",
                repo_path=tmp,
            )
            second = run_with_query_cache(
                loop,
                "how does eval work?",
                top_k=5,
                cache=cache,
                config=cfg,
                repo_name="fixture",
                repo_path=tmp,
            )
            self.assertEqual(retriever.calls, ["how does eval work?"])
            self.assertEqual([r.chunk.id for r in first.results], [hit.chunk.id])
            self.assertEqual([r.chunk.id for r in second.results], [hit.chunk.id])
            self.assertEqual(first.packed.packed_chunk_ids, second.packed.packed_chunk_ids)
            self.assertEqual(cache.hits, 1)
            self.assertEqual(cache.misses, 1)

    def test_whitespace_normalized_query_still_hits(self):
        from harness.query_cache import QueryCache, run_with_query_cache

        retriever = RecordingRetriever([_rr("func:harness/demo.py:run_eval:abcd")])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp)
            cache = QueryCache()
            loop = QueryLoop(retriever, RecordingBuilder(), LoopConfig(max_loops=0))
            run_with_query_cache(
                loop, "  same   question  ", top_k=5, cache=cache,
                config=cfg, repo_name="fixture", repo_path=tmp,
            )
            run_with_query_cache(
                loop, "same question", top_k=5, cache=cache,
                config=cfg, repo_name="fixture", repo_path=tmp,
            )
            self.assertEqual(len(retriever.calls), 1)
            self.assertEqual(cache.hits, 1)

    def test_different_query_or_knobs_miss(self):
        from harness.query_cache import QueryCache, run_with_query_cache

        retriever = RecordingRetriever([_rr("func:harness/demo.py:run_eval:abcd")])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp)
            cache = QueryCache()
            loop = QueryLoop(retriever, RecordingBuilder(), LoopConfig(max_loops=0))
            kwargs = dict(cache=cache, config=cfg, repo_name="fixture", repo_path=tmp)
            run_with_query_cache(loop, "alpha", top_k=5, **kwargs)
            run_with_query_cache(loop, "beta", top_k=5, **kwargs)
            run_with_query_cache(loop, "alpha", top_k=8, **kwargs)
            other = _cfg(tmp)
            other.retrieval["wiki_weight"] = 0.2
            run_with_query_cache(loop, "alpha", top_k=5, cache=cache, config=other,
                                 repo_name="fixture", repo_path=tmp)
            self.assertEqual(len(retriever.calls), 4)
            self.assertEqual(cache.hits, 0)
            self.assertEqual(cache.misses, 4)

    def test_invalidate_after_index_fingerprint_change(self):
        from harness.query_cache import QueryCache, run_with_query_cache

        retriever = RecordingRetriever([_rr("func:harness/demo.py:run_eval:abcd")])
        with tempfile.TemporaryDirectory() as tmp:
            persist = Path(tmp) / ".code-harness" / "chromadb"
            persist.mkdir(parents=True)
            sqlite = persist / "chroma.sqlite3"
            sqlite.write_bytes(b"v1")
            cfg = _cfg(tmp)
            cache = QueryCache(os.path.join(tmp, "cache.sqlite"))
            loop = QueryLoop(retriever, RecordingBuilder(), LoopConfig(max_loops=0))
            kwargs = dict(cache=cache, config=cfg, repo_name="fixture", repo_path=tmp)
            run_with_query_cache(loop, "same", top_k=5, **kwargs)
            run_with_query_cache(loop, "same", top_k=5, **kwargs)
            self.assertEqual(len(retriever.calls), 1)
            sqlite.write_bytes(b"v2-reindexed")
            os.utime(sqlite, (os.path.getmtime(sqlite) + 10, os.path.getmtime(sqlite) + 10))
            run_with_query_cache(loop, "same", top_k=5, **kwargs)
            self.assertEqual(len(retriever.calls), 2)
            self.assertEqual(cache.misses, 2)
            self.assertEqual(cache.hits, 1)

    def test_memory_write_invalidates_live_cache(self):
        from harness.memory import MemoryStore
        from harness.query_cache import QueryCache, run_with_query_cache

        retriever = RecordingRetriever([_rr("func:harness/demo.py:run_eval:abcd")])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp)
            cfg.retrieval["memory_weight"] = 0.15
            cache = QueryCache()
            loop = QueryLoop(retriever, RecordingBuilder(), LoopConfig(max_loops=0))
            kwargs = dict(cache=cache, config=cfg, repo_name="fixture", repo_path=tmp)
            run_with_query_cache(loop, "decision?", top_k=5, **kwargs)
            run_with_query_cache(loop, "decision?", top_k=5, **kwargs)
            self.assertEqual(len(retriever.calls), 1)
            store = MemoryStore(tmp)
            store.add("decision", "Keep cache keys honest", "Vault writes must bust retrieve packs.")
            run_with_query_cache(loop, "decision?", top_k=5, **kwargs)
            self.assertEqual(len(retriever.calls), 2)


class TestEvalAndSessionCache(unittest.TestCase):
    def test_eval_warm_repeat_hits_and_keeps_recall(self):
        from harness.query_cache import QueryCache

        gold = "func:harness/demo.py:run_eval"
        hit = _rr(f"{gold}:deadbeef", "harness/demo.py", "run_eval")
        fixture = EvalFixture(
            id="q-eval",
            query="How does eval work?",
            relevant_chunk_ids=[gold],
            must_cite_paths=["harness/demo.py"],
            difficulty="easy",
        )
        retriever = RecordingRetriever([hit])
        builder = RecordingBuilder()
        with tempfile.TemporaryDirectory() as tmp:
            cache = QueryCache()
            kwargs = dict(
                fixtures=[fixture],
                retriever=retriever,
                context_builder=builder,
                k=5,
                suite_name="cache",
                suite_path="suite.yaml",
                repo_path=tmp,
                repo_name="fixture",
                cache=cache,
                config=_cfg(tmp),
            )
            first = run_eval(**kwargs)
            second = run_eval(**kwargs)
            self.assertEqual(len(retriever.calls), 1)
            self.assertEqual(first["metrics"]["recall_at_k"], 1.0)
            self.assertEqual(second["metrics"]["recall_at_k"], 1.0)
            self.assertEqual(first["metrics"]["recall_at_k"], second["metrics"]["recall_at_k"])
            self.assertEqual(second["metrics"]["query_cache"]["hits"], 1)
            self.assertEqual(second["metrics"]["query_cache"]["misses"], 1)
            self.assertTrue(second["cases"][0].get("cached"))

    def test_eval_summary_prints_cache_counts(self):
        from io import StringIO
        from contextlib import redirect_stdout

        report = {
            "suite": "cache",
            "k": 5,
            "metrics": {
                "n": 1,
                "recall_at_k": 1.0,
                "ndcg_at_k": 1.0,
                "citation_path_hit_rate": 1.0,
                "prompt_tokens_mean": 16,
                "prompt_tokens_full_mean": 16,
                "prompt_tokens_packed_mean": 16,
                "prompt_token_drop": 0.0,
                "query_cache": {"enabled": True, "hits": 2, "misses": 3},
            },
            "cases": [
                {
                    "id": "q-eval",
                    "recall_at_k": 1.0,
                    "ndcg_at_k": 1.0,
                    "citation_path_hit_rate": 1.0,
                    "prompt_tokens": 16,
                    "failures": [],
                    "cached": True,
                }
            ],
        }
        buf = StringIO()
        with redirect_stdout(buf):
            print_summary(report)
        compact = buf.getvalue().lower().replace(" ", "")
        self.assertIn("querycache", compact)
        self.assertIn("hits=2", compact)
        self.assertIn("misses=3", compact)

    def test_session_cost_includes_cache_stats(self):
        from harness.query_cache import QueryCache
        from harness.session import Session, SessionTurn

        cache = QueryCache()
        cache.hits = 2
        cache.misses = 1
        session = Session(repo=".", profile="default")
        session.query_cache = cache
        session.record_turn(SessionTurn(role="assistant", text="ok", packed_tokens=10, full_tokens=20))
        report = session.format_cost()
        self.assertRegex(report.lower(), r"query cache")
        self.assertIn("2", report)
        self.assertIn("1", report)


class TestQueryCacheFlags(unittest.TestCase):
    def test_env_and_flags_default_off(self):
        from harness.query_cache import query_cache_enabled

        self.assertFalse(query_cache_enabled(args=Namespace(), environ={}))
        self.assertFalse(
            query_cache_enabled(
                args=Namespace(query_cache=False, no_query_cache=False),
                environ={"CODEHARNESS_QUERY_CACHE": "0"},
            )
        )
        self.assertFalse(
            query_cache_enabled(
                args=Namespace(query_cache=False, no_query_cache=True),
                environ={"CODEHARNESS_QUERY_CACHE": "1"},
            )
        )
        self.assertTrue(
            query_cache_enabled(
                args=Namespace(query_cache=True, no_query_cache=False),
                environ={},
            )
        )
        self.assertTrue(
            query_cache_enabled(
                args=Namespace(query_cache=False, no_query_cache=False),
                environ={"CODEHARNESS_QUERY_CACHE": "1"},
            )
        )

    def test_cli_help_exposes_query_cache_flags(self):
        for command in ("query", "chat", "eval"):
            run = subprocess.run(
                [sys.executable, "main.py", command, "--help"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("--query-cache", run.stdout)
            self.assertIn("--no-query-cache", run.stdout)


if __name__ == "__main__":
    unittest.main()
