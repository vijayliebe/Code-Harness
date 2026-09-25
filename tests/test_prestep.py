"""Retrieve-as-pre-step plugin seam (DeepSeek steal #4).

Thin hook bus: registered AgentHooks run ``before_model(ctx) -> ctx``
in order. Retrieve is the first built-in. Default is **on** so query/chat
behavior is unchanged; ``--no-retrieve-prestep`` / env ``0`` skips.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace

from harness.config import Config, DEFAULT_CONFIG
from harness.context_builder import ContextReport
from harness.models import Chunk, EntityType, RetrievalResult
from harness.session import Session, SessionTurn


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

    def build_system_prompt(self):
        return "cite path:symbol"


def _cfg(tmp, **updates):
    cfg = Config()
    cfg.repo_path = tmp
    cfg.vector_store["persist_directory"] = os.path.join(tmp, ".code-harness", "chromadb")
    cfg.knowledge_graph["persist_path"] = os.path.join(tmp, ".code-harness", "graph.json")
    for key, value in updates.items():
        if hasattr(cfg, key) and isinstance(getattr(cfg, key), dict) and isinstance(value, dict):
            getattr(cfg, key).update(value)
        else:
            setattr(cfg, key, value)
    return cfg


def _hit():
    return _rr("func:harness/demo.py:run_eval:abcd")


class OrderHook:
    def __init__(self, name, log):
        self.name = name
        self.log = log

    def before_model(self, ctx):
        self.log.append(self.name)
        seen = list(getattr(ctx, "hook_trace", None) or [])
        seen.append(self.name)
        ctx.hook_trace = seen
        return ctx


class TestHookOrder(unittest.TestCase):
    def test_hooks_run_in_registration_order(self):
        from harness.prestep import HookRegistry, PreStepContext

        log = []
        registry = HookRegistry()
        registry.register(OrderHook("memory-brief", log))
        registry.register(OrderHook("retrieve", log))
        registry.register(OrderHook("verify-prep", log))
        ctx = registry.run_before_model(PreStepContext(query="q"))
        self.assertEqual(log, ["memory-brief", "retrieve", "verify-prep"])
        self.assertEqual(ctx.hook_trace, ["memory-brief", "retrieve", "verify-prep"])
        self.assertEqual(registry.names, ["memory-brief", "retrieve", "verify-prep"])

    def test_default_registry_is_retrieve_first_and_documents_second_hook(self):
        from harness.prestep import RetrievePreStep, default_registry

        registry = default_registry()
        self.assertEqual(registry.names, ["retrieve"])
        self.assertIsInstance(registry.hooks[0], RetrievePreStep)
        doc = default_registry.__doc__ or ""
        self.assertIn("MemoryBrief", doc)
        self.assertIn("before_model", doc)


class TestRetrieveOncePerTurn(unittest.TestCase):
    def test_retrieve_runs_once_per_turn(self):
        from harness.prestep import run_query_turn

        retriever = RecordingRetriever([_hit()])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp)
            ctx = run_query_turn(
                "how does eval work?",
                retriever=retriever,
                context_builder=RecordingBuilder(),
                config=cfg,
                top_k=5,
                repo_name="fixture",
                repo_path=tmp,
            )
            self.assertEqual(retriever.calls, ["how does eval work?"])
            self.assertIn("retrieve", ctx.ran)
            self.assertEqual([r.chunk.id for r in ctx.results], [_hit().chunk.id])
            self.assertIsNotNone(ctx.packed)
            self.assertIsNotNone(ctx.outcome)

    def test_second_hook_after_retrieve_sees_packs_without_second_retrieve(self):
        from harness.prestep import HookRegistry, RetrievePreStep, run_query_turn

        seen = {}

        class AfterRetrieve:
            name = "annotate"

            def before_model(self, ctx):
                seen["packed_ids"] = list(getattr(ctx.packed, "packed_chunk_ids", None) or [])
                seen["ran"] = list(ctx.ran)
                ctx.ran.append(self.name)
                return ctx

        retriever = RecordingRetriever([_hit()])
        registry = HookRegistry([RetrievePreStep(), AfterRetrieve()])
        with tempfile.TemporaryDirectory() as tmp:
            run_query_turn(
                "how does eval work?",
                retriever=retriever,
                context_builder=RecordingBuilder(),
                config=_cfg(tmp),
                top_k=5,
                repo_name="fixture",
                repo_path=tmp,
                registry=registry,
            )
        self.assertEqual(retriever.calls, ["how does eval work?"])
        self.assertEqual(seen["packed_ids"], [_hit().chunk.id])
        self.assertEqual(seen["ran"], ["retrieve"])


class TestDisableAndSkip(unittest.TestCase):
    def test_default_on(self):
        from harness.prestep import retrieve_prestep_enabled

        self.assertTrue(DEFAULT_CONFIG["prestep"]["retrieve"])
        self.assertTrue(retrieve_prestep_enabled(args=Namespace(), environ={}))
        self.assertTrue(retrieve_prestep_enabled(config=Config()))

    def test_env_and_flags_can_disable(self):
        from harness.prestep import retrieve_prestep_enabled

        self.assertFalse(
            retrieve_prestep_enabled(
                args=Namespace(retrieve_prestep=False, no_retrieve_prestep=True),
                environ={"CODEHARNESS_RETRIEVE_PRESTEP": "1"},
            )
        )
        self.assertFalse(
            retrieve_prestep_enabled(
                args=Namespace(),
                environ={"CODEHARNESS_RETRIEVE_PRESTEP": "0"},
            )
        )
        self.assertFalse(
            retrieve_prestep_enabled(
                environ={},
                config=Config.from_dict({"prestep": {"retrieve": False}}),
            )
        )
        self.assertFalse(
            retrieve_prestep_enabled(
                args=Namespace(retrieve_prestep=True, no_retrieve_prestep=False),
                environ={"CODEHARNESS_RETRIEVE_PRESTEP": "0"},
            )
        )
        self.assertTrue(
            retrieve_prestep_enabled(
                args=Namespace(retrieve_prestep=True, no_retrieve_prestep=False),
                environ={},
                config=Config.from_dict({"prestep": {"retrieve": False}}),
            )
        )

    def test_disable_skips_retrieve(self):
        from harness.prestep import run_query_turn

        retriever = RecordingRetriever([_hit()])
        with tempfile.TemporaryDirectory() as tmp:
            ctx = run_query_turn(
                "how does eval work?",
                retriever=retriever,
                context_builder=RecordingBuilder(),
                config=_cfg(tmp, prestep={"retrieve": False}),
                top_k=5,
                repo_name="fixture",
                repo_path=tmp,
                environ={"CODEHARNESS_RETRIEVE_PRESTEP": "0"},
            )
        self.assertEqual(retriever.calls, [])
        self.assertIn("retrieve", ctx.skipped)
        self.assertNotIn("retrieve", ctx.ran)
        self.assertIsNone(ctx.outcome)

    def test_skip_when_caller_already_supplies_packs(self):
        from harness.prestep import PreStepContext, default_registry

        retriever = RecordingRetriever([_hit()])
        packed = ContextReport(
            context="# already packed",
            prompt_tokens=8,
            packed_chunk_ids=["func:harness/demo.py:run_eval:abcd"],
            pack_mode="full",
        )
        ctx = PreStepContext(
            query="already packed",
            retriever=retriever,
            context_builder=RecordingBuilder(),
            packed=packed,
            results=[_hit()],
        )
        out = default_registry().run_before_model(ctx)
        self.assertEqual(retriever.calls, [])
        self.assertIn("retrieve", out.skipped)
        self.assertEqual(out.packed.context, "# already packed")


class TestQueryCacheWithHook(unittest.TestCase):
    def test_query_cache_still_hits_through_prestep(self):
        from harness.prestep import run_query_turn
        from harness.query_cache import QueryCache

        retriever = RecordingRetriever([_hit()])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp)
            cache = QueryCache(os.path.join(tmp, "cache.sqlite"))
            kwargs = dict(
                retriever=retriever,
                context_builder=RecordingBuilder(),
                config=cfg,
                top_k=5,
                cache=cache,
                repo_name="fixture",
                repo_path=tmp,
            )
            first = run_query_turn("how does eval work?", **kwargs)
            second = run_query_turn("how does eval work?", **kwargs)
            self.assertEqual(retriever.calls, ["how does eval work?"])
            self.assertEqual(first.packed.packed_chunk_ids, second.packed.packed_chunk_ids)
            self.assertFalse(first.outcome.cached)
            self.assertTrue(second.outcome.cached)
            self.assertEqual(cache.hits, 1)
            self.assertEqual(cache.misses, 1)


class TestEventSessionWithPrestep(unittest.TestCase):
    def test_event_session_path_still_works_when_both_enabled(self):
        from harness.prestep import run_query_turn
        from harness.prefix import snapshot_prefix
        from harness.context_builder import ContextBuilder

        retriever = RecordingRetriever([_hit()])
        with tempfile.TemporaryDirectory() as tmp:
            config = _cfg(
                tmp,
                session={"event_session": True},
                context={"prefix_stable": True},
                prestep={"retrieve": True},
            )
            builder = ContextBuilder(config)
            session = Session(
                repo="demo",
                session_dir=tmp,
                session_id="prestep-1",
                event_session=True,
                config=config,
            )
            freeze = snapshot_prefix(builder)
            session.bind_prefix(freeze)
            builder.bind_prefix_freeze(freeze)
            ctx = run_query_turn(
                "how does eval work?",
                retriever=retriever,
                context_builder=RecordingBuilder(),
                config=config,
                top_k=5,
                repo_name="demo",
                repo_path=tmp,
            )
            session.record_turn(SessionTurn(role="user", text="how does eval work?"))
            session.record_turn(
                SessionTurn(
                    role="assistant",
                    text="ok",
                    chunk_ids=list(ctx.packed.packed_chunk_ids or []),
                    tool_name="retrieve",
                    tool_args="how does eval work?",
                    tool_result=ctx.packed.context,
                )
            )
            self.assertEqual(retriever.calls, ["how does eval work?"])
            self.assertTrue(session.event_session)
            kinds = [event.type for event in session.events]
            self.assertIn("user", kinds)
            self.assertIn("assistant", kinds)
            self.assertIn("tool_use", kinds)
            derived = session.history_for_model()
            self.assertIn("how does eval work?", derived)
            second = builder.build_context_report("another q", [_hit()])
            self.assertEqual(freeze.digest, session.prefix_digest)
            self.assertTrue(second.prefix_bytes)


class TestCliAndConfig(unittest.TestCase):
    def test_cli_help_exposes_retrieve_prestep_flags(self):
        for command in ("query", "chat"):
            run = subprocess.run(
                [sys.executable, "main.py", command, "--help"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("--no-retrieve-prestep", run.stdout)
            self.assertIn("--retrieve-prestep", run.stdout)

    def test_apply_config_stamps_prestep_retrieve(self):
        from harness.prestep import apply_retrieve_prestep_config

        cfg = Config()
        apply_retrieve_prestep_config(
            cfg,
            args=Namespace(no_retrieve_prestep=True, retrieve_prestep=False),
            environ={},
        )
        self.assertFalse(cfg.prestep["retrieve"])
        apply_retrieve_prestep_config(
            cfg,
            args=Namespace(no_retrieve_prestep=False, retrieve_prestep=True),
            environ={},
        )
        self.assertTrue(cfg.prestep["retrieve"])


if __name__ == "__main__":
    unittest.main()
