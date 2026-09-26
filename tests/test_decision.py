"""Optional decision-model integration (off by default; not HyDE)."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from harness.config import Config, DEFAULT_CONFIG
from harness.models import Chunk, EntityType, RetrievalResult


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


def _chunk(cid, path, name, content="x"):
    return Chunk(
        id=cid,
        content=content,
        entity_id=":".join(cid.split(":")[:3]),
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=4,
    )


def _rr(cid, path, name, score=0.4, source="fused"):
    return RetrievalResult(
        chunk=_chunk(cid, path, name),
        score=score,
        source=source,
    )


class RecordingRetriever:
    def __init__(self, by_query, default=None):
        self.by_query = by_query
        self.default = default or []
        self.ce_enabled = False
        self.calls = []

    def retrieve(self, query, top_k=None, debug=False, entity_id_map=None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, "debug": debug, **kwargs})
        payload = self.by_query.get(query)
        if payload is None:
            results = list(self.default)
            trace = {
                "dense": [],
                "sparse": results,
                "graph": [],
                "fused": results,
                "reranked": [],
                "latencies_ms": {"dense": 0.0, "bm25": 1.0, "graph": 0.0, "ce": 0.0},
            }
        else:
            results = payload["final"]
            trace = {
                "dense": payload.get("dense", results),
                "sparse": payload.get("sparse", results),
                "graph": payload.get("graph", []),
                "fused": payload.get("fused", results),
                "reranked": payload.get("reranked", []),
                "latencies_ms": payload.get(
                    "latencies_ms",
                    {"dense": 1.0, "bm25": 1.0, "graph": 1.0, "ce": 1.0},
                ),
            }
        if debug:
            return results, trace
        return results


class RecordingBuilder:
    def build_context_report(self, query, results):
        from harness.context_builder import ContextReport

        paths = []
        ids = []
        for item in results:
            chunk = getattr(item, "chunk", item)
            ids.append(chunk.id)
            path = chunk.file_path.replace("\\", "/")
            if path not in paths:
                paths.append(path)
        return ContextReport(
            context="# packed\n" + "\n".join(paths),
            prompt_tokens=32 * max(1, len(results)),
            packed_chunk_ids=ids,
            packed_paths=paths,
            mmr_latency_ms=1.0,
        )


class TestDecisionConfigDefaultOff(unittest.TestCase):
    def test_default_config_is_disabled_and_lists_loop_and_verify(self):
        self.assertIn("decision", DEFAULT_CONFIG)
        self.assertFalse(DEFAULT_CONFIG["decision"]["enabled"])
        self.assertIsNone(DEFAULT_CONFIG["decision"]["provider"])
        self.assertIsNone(DEFAULT_CONFIG["decision"]["api_key"])
        self.assertEqual(DEFAULT_CONFIG["decision"]["timeout_ms"], 2000)
        self.assertEqual(DEFAULT_CONFIG["decision"]["min_confidence"], 0.0)
        self.assertEqual(DEFAULT_CONFIG["decision"]["uses"], ["loop_grade", "verify"])

        config = Config()
        self.assertFalse(config.decision["enabled"])
        self.assertEqual(config.decision["uses"], ["loop_grade", "verify"])

    def test_from_dict_merges_decision_section(self):
        config = Config.from_dict({
            "decision": {
                "enabled": True,
                "provider": "mock",
                "min_confidence": 0.6,
                "uses": ["verify"],
            }
        })
        self.assertTrue(config.decision["enabled"])
        self.assertEqual(config.decision["provider"], "mock")
        self.assertEqual(config.decision["min_confidence"], 0.6)
        self.assertEqual(config.decision["uses"], ["verify"])
        self.assertEqual(config.decision["timeout_ms"], 2000)

    def test_env_overrides_enablement_and_key(self):
        from harness.decision import apply_decision_config

        config = Config()
        apply_decision_config(
            config,
            environ={
                "CODEHARNESS_DECISION_ENABLED": "1",
                "CODEHARNESS_DECISION_PROVIDER": "jev",
                "TYPESAFE_API_KEY": "ts-test-key",
                "CODEHARNESS_DECISION_API_BASE": "https://api.typesafe.ai/v1",
            },
        )
        self.assertTrue(config.decision["enabled"])
        self.assertEqual(config.decision["provider"], "jev")
        self.assertEqual(config.decision["api_key"], "ts-test-key")
        self.assertEqual(config.decision["api_base"], "https://api.typesafe.ai/v1")

    def test_save_round_trips_decision(self):
        config = Config.from_dict({
            "decision": {"enabled": True, "provider": "mock", "model": "jev-latest"},
        })
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "code-harness.json")
            config.save(path)
            loaded = Config.from_file(path)
            self.assertTrue(loaded.decision["enabled"])
            self.assertEqual(loaded.decision["provider"], "mock")
            self.assertEqual(loaded.decision["model"], "jev-latest")


class TestDecisionClientDisabledNoNetwork(unittest.TestCase):
    def test_build_client_is_none_when_disabled(self):
        from harness.decision import DecisionClient, build_decision_client

        calls = []

        class Boom:
            def decide(self, state, questions):
                calls.append((state, questions))
                raise AssertionError("provider must not be called when disabled")

        config = Config()
        self.assertIsNone(build_decision_client(config))

        client = DecisionClient(config, provider=Boom())
        self.assertFalse(client.enabled)
        self.assertIsNone(client.decide("state", {"q": {"type": "noul", "instructions": "yes?"}}))
        self.assertEqual(calls, [])

    def test_disabled_loop_path_matches_heuristic_and_never_calls_provider(self):
        from harness.decision import DecisionClient
        from harness.loop import LoopConfig, QueryLoop

        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        hit = _rr("func:main.py:cmd_query:1", "main.py", "cmd_query")
        original = "Where is the query CLI command defined in main.py?"
        retriever = RecordingRetriever({original: {"final": [miss]}}, default=[hit])
        calls = []

        class Boom:
            def decide(self, state, questions):
                calls.append((state, questions))
                raise AssertionError("disabled decision must not run")

        config = Config()
        self.assertFalse(config.decision["enabled"])
        loop = QueryLoop(
            retriever,
            RecordingBuilder(),
            LoopConfig(max_loops=1),
            decider=DecisionClient(config, provider=Boom()),
        )
        outcome = loop.run(original, top_k=10, must_cite_paths=["main.py"])
        self.assertEqual(calls, [])
        self.assertEqual(len(retriever.calls), 2)
        self.assertEqual(outcome.attempts, 2)


class TestDecisionLoopUsesMockChoice(unittest.TestCase):
    def test_mock_proceed_stops_even_when_heuristic_grade_is_low(self):
        from harness.decision import DecisionClient, MockDecisionProvider
        from harness.loop import LoopConfig, QueryLoop

        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        original = "Where is the query CLI command defined in main.py?"
        retriever = RecordingRetriever({original: {"final": [miss]}})
        provider = MockDecisionProvider({
            "action": {
                "type": "choice",
                "choice": "proceed",
                "confidence": 0.95,
                "probabilities": {"proceed": 0.95, "rewrite": 0.05},
            },
            "adequate": {"type": "noul", "noul": 0.88},
        })
        config = Config.from_dict({
            "decision": {"enabled": True, "provider": "mock", "uses": ["loop_grade"]},
        })
        loop = QueryLoop(
            retriever,
            RecordingBuilder(),
            LoopConfig(max_loops=1),
            decider=DecisionClient(config, provider=provider),
        )
        outcome = loop.run(original, top_k=10)
        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(outcome.stop_reason, "decision")
        self.assertEqual(outcome.action, "stop")
        self.assertAlmostEqual(outcome.grade, 0.88)
        self.assertIn("action", provider.calls[0]["questions"])

    def test_mock_rewrite_retries_even_when_heuristic_grade_is_high(self):
        from harness.decision import DecisionClient, MockDecisionProvider
        from harness.loop import LoopConfig, QueryLoop

        hit = _rr("func:main.py:cmd_query:1", "main.py", "cmd_query")
        retriever = RecordingRetriever({"cmd_query": {"final": [hit]}}, default=[hit])
        provider = MockDecisionProvider({
            "action": {
                "type": "choice",
                "choice": "rewrite",
                "confidence": 0.9,
                "probabilities": {"rewrite": 0.9, "proceed": 0.1},
            },
        })
        config = Config.from_dict({
            "decision": {"enabled": True, "provider": "mock", "uses": ["loop_grade"]},
        })
        loop = QueryLoop(
            retriever,
            RecordingBuilder(),
            LoopConfig(max_loops=1),
            decider=DecisionClient(config, provider=provider),
        )
        outcome = loop.run("cmd_query", top_k=10)
        self.assertEqual(len(retriever.calls), 2)
        self.assertGreaterEqual(outcome.grade, 0.35)
        self.assertIn(outcome.traces[0]["action"], ("rewrite", "hyde", "deepen_graph"))


class TestDecisionVerifyUsesMock(unittest.TestCase):
    def test_citation_verify_uses_noul_and_skips_llm(self):
        from harness.decision import DecisionClient, MockDecisionProvider
        from harness.loop import verify_answer

        llm_calls = []

        class BoomLLM:
            def query(self, system, context, user):
                llm_calls.append((system, context, user))
                raise AssertionError("LLM verify must not run when decision answers")

        provider = MockDecisionProvider({"supported": {"type": "noul", "noul": 0.91}})
        config = Config.from_dict({
            "decision": {"enabled": True, "provider": "mock", "uses": ["verify"]},
        })
        decider = DecisionClient(config, provider=provider)
        payload = verify_answer(
            BoomLLM(),
            "Where is cmd_query?",
            "See main.py:cmd_query.",
            "# packed\nmain.py",
            decider=decider,
        )
        self.assertTrue(payload["supported"])
        self.assertEqual(llm_calls, [])
        self.assertEqual(payload.get("source"), "decision")
        self.assertEqual(len(provider.calls), 1)

    def test_session_llm_criterion_uses_decision_noul(self):
        from harness.decision import DecisionClient, MockDecisionProvider
        from harness.verify import VERIFIER_ROLE, CompletionGate

        llm_calls = []

        def boom_llm(system, context, user):
            llm_calls.append((system, context, user))
            raise AssertionError("LLM criterion must not run when decision answers")

        provider = MockDecisionProvider({"llm": {"type": "noul", "noul": 0.12}})
        config = Config.from_dict({
            "decision": {"enabled": True, "provider": "mock", "uses": ["verify"]},
        })
        gate = CompletionGate(
            enabled=True,
            criteria=[{"id": "llm", "kind": "llm", "description": "behavior matches spec"}],
        )
        result = gate.run_verify(
            role=VERIFIER_ROLE,
            llm=boom_llm,
            evidence={"answer": "I am done"},
            decider=DecisionClient(config, provider=provider),
        )
        self.assertTrue(result.ran)
        self.assertFalse(result.passed)
        self.assertEqual(llm_calls, [])
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("decision", (gate.criteria[0].evidence or "").lower())


class TestDecisionFailureFallsBack(unittest.TestCase):
    def test_loop_falls_back_to_heuristics_when_provider_raises(self):
        from harness.decision import DecisionClient
        from harness.loop import LoopConfig, QueryLoop

        miss = _rr("func:other.py:foo:1", "other.py", "foo")
        hit = _rr("func:main.py:cmd_query:1", "main.py", "cmd_query")
        original = "Where is the query CLI command defined in main.py?"
        retriever = RecordingRetriever({original: {"final": [miss]}}, default=[hit])

        class Broken:
            def decide(self, state, questions):
                raise RuntimeError("network down")

        config = Config.from_dict({
            "decision": {"enabled": True, "provider": "mock", "uses": ["loop_grade"]},
        })
        loop = QueryLoop(
            retriever,
            RecordingBuilder(),
            LoopConfig(max_loops=1),
            decider=DecisionClient(config, provider=Broken()),
        )
        outcome = loop.run(original, top_k=10, must_cite_paths=["main.py"])
        self.assertEqual(len(retriever.calls), 2)
        self.assertNotEqual(outcome.stop_reason, "decision")
        self.assertEqual(outcome.attempts, 2)

    def test_verify_falls_back_to_llm_when_decision_fails(self):
        from harness.decision import DecisionClient
        from harness.loop import verify_answer

        llm_calls = []

        class LLM:
            def query(self, system, context, user):
                llm_calls.append((system, context, user))
                return '{"supported": true, "missing_paths": []}'

        class Broken:
            def decide(self, state, questions):
                raise RuntimeError("timeout")

        config = Config.from_dict({
            "decision": {
                "enabled": True,
                "provider": "jev",
                "api_key": "ts-test",
                "uses": ["verify"],
            },
        })
        payload = verify_answer(
            LLM(),
            "q",
            "a",
            "ctx",
            decider=DecisionClient(config, provider=Broken()),
        )
        self.assertTrue(payload["supported"])
        self.assertEqual(len(llm_calls), 1)
        self.assertNotEqual(payload.get("source"), "decision")

    def test_enabled_without_key_does_not_call_http(self):
        from harness.decision import DecisionClient, HttpDecisionProvider

        posts = []

        def boom_post(*args, **kwargs):
            posts.append((args, kwargs))
            raise AssertionError("HTTP must not run without a key")

        config = Config.from_dict({
            "decision": {
                "enabled": True,
                "provider": "jev",
                "api_key": None,
            },
        })
        provider = HttpDecisionProvider(
            api_base="https://api.typesafe.ai/v1",
            api_key=None,
            model="jev-latest",
            timeout_ms=2000,
            post=boom_post,
        )
        result = DecisionClient(config, provider=provider, environ={}).decide(
            "state",
            {"ok": {"type": "noul", "instructions": "yes?"}},
        )
        self.assertIsNone(result)
        self.assertEqual(posts, [])


class TestHttpDecisionProviderShape(unittest.TestCase):
    def test_posts_systemone_payload_and_parses_answers(self):
        from harness.decision import DecisionClient, HttpDecisionProvider

        captured = {}

        def fake_post(url, headers, payload, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            captured["timeout"] = timeout
            return {
                "model": "jev-1.13.0",
                "answers": {
                    "ok": {"type": "noul", "noul": 0.8},
                    "route": {
                        "type": "choice",
                        "choice": "rewrite",
                        "confidence": 0.7,
                        "probabilities": {"rewrite": 0.7, "proceed": 0.3},
                    },
                },
            }

        provider = HttpDecisionProvider(
            api_base="https://api.typesafe.ai/v1",
            api_key="ts-key",
            model="jev-latest",
            timeout_ms=1500,
            post=fake_post,
        )
        config = Config.from_dict({
            "decision": {"enabled": True, "provider": "jev", "api_key": "ts-key"},
        })
        result = DecisionClient(config, provider=provider).decide(
            "Customer was charged twice.",
            {
                "ok": {"type": "noul", "instructions": "Escalate?"},
                "route": {
                    "type": "choice",
                    "instructions": "Next action?",
                    "criteria": {"rewrite": "retry", "proceed": "stop"},
                },
            },
        )
        self.assertIsNotNone(result)
        self.assertEqual(captured["url"], "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer ts-key")
        self.assertEqual(captured["payload"]["model"], "jev-latest")
        self.assertEqual(captured["payload"]["state"], "Customer was charged twice.")
        self.assertEqual(captured["payload"]["questions"]["ok"]["type"], "noul")
        self.assertEqual(captured["timeout"], 1.5)
        self.assertAlmostEqual(result.noul("ok"), 0.8)
        self.assertEqual(result.choice("route"), "rewrite")
        self.assertAlmostEqual(result.confidence("route"), 0.7)

    def test_urlopen_transport_is_used_by_default(self):
        from harness.decision import HttpDecisionProvider

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({
                    "model": "jev-latest",
                    "answers": {"ok": {"type": "noul", "noul": 0.2}},
                }).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            self.assertIn("/systemone", req.full_url)
            self.assertAlmostEqual(timeout, 2.0)
            return FakeResp()

        provider = HttpDecisionProvider(
            api_base="https://api.typesafe.ai/v1",
            api_key="k",
            model="jev-latest",
            timeout_ms=2000,
        )
        with patch("harness.decision.urlopen", fake_urlopen):
            raw = provider.decide("s", {"ok": {"type": "noul", "instructions": "y?"}})
        self.assertEqual(raw["answers"]["ok"]["noul"], 0.2)


class TestDoctorDecisionWarn(unittest.TestCase):
    def test_enabled_without_key_is_warn_not_fail(self):
        from harness.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            chroma = os.path.join(tmp, ".code-harness", "chromadb")
            os.makedirs(chroma, exist_ok=True)
            with open(os.path.join(chroma, "chroma.sqlite3"), "wb") as fh:
                fh.write(b"sqlite")
            graph = os.path.join(tmp, ".code-harness", "graph_fixture.json")
            with open(graph, "w", encoding="utf-8") as fh:
                json.dump(
                    {"directed": True, "multigraph": False, "graph": {}, "nodes": [{"id": "n1"}], "links": []},
                    fh,
                )
            os.makedirs(os.path.join(tmp, ".code-harness", "audit"), exist_ok=True)

            cfg = Config()
            cfg.repo_path = tmp
            cfg.vector_store["persist_directory"] = chroma
            cfg.knowledge_graph["persist_path"] = os.path.join(tmp, ".code-harness", "graph.json")
            cfg.redaction["audit_path"] = os.path.join(tmp, ".code-harness", "audit", "audit.jsonl")
            cfg.embedding["provider"] = "openai"
            cfg.embedding["model"] = "text-embedding-3-small"
            cfg.embedding["api_key"] = "sk-test"
            cfg.llm["provider"] = "ollama"
            cfg.decision["enabled"] = True
            cfg.decision["provider"] = "jev"
            cfg.decision["api_key"] = None

            report = run_doctor(cfg, repo_path=tmp, repo_name="fixture", environ={})
            self.assertTrue(report.ok, report.format())
            decision = next(c for c in report.checks if c.name == "decision")
            self.assertEqual(decision.status, "warn")
            self.assertIn("key", decision.message.lower())

    def test_disabled_decision_does_not_fail_required_checks(self):
        from harness.doctor import run_doctor

        with tempfile.TemporaryDirectory() as tmp:
            chroma = os.path.join(tmp, ".code-harness", "chromadb")
            os.makedirs(chroma, exist_ok=True)
            with open(os.path.join(chroma, "chroma.sqlite3"), "wb") as fh:
                fh.write(b"sqlite")
            graph = os.path.join(tmp, ".code-harness", "graph_fixture.json")
            with open(graph, "w", encoding="utf-8") as fh:
                json.dump(
                    {"directed": True, "multigraph": False, "graph": {}, "nodes": [{"id": "n1"}], "links": []},
                    fh,
                )
            os.makedirs(os.path.join(tmp, ".code-harness", "audit"), exist_ok=True)

            cfg = Config()
            cfg.repo_path = tmp
            cfg.vector_store["persist_directory"] = chroma
            cfg.knowledge_graph["persist_path"] = os.path.join(tmp, ".code-harness", "graph.json")
            cfg.redaction["audit_path"] = os.path.join(tmp, ".code-harness", "audit", "audit.jsonl")
            cfg.embedding["provider"] = "openai"
            cfg.embedding["model"] = "text-embedding-3-small"
            cfg.embedding["api_key"] = "sk-test"
            cfg.llm["provider"] = "ollama"

            report = run_doctor(cfg, repo_path=tmp, repo_name="fixture", environ={})
            self.assertTrue(report.ok, report.format())
            decision = next(c for c in report.checks if c.name == "decision")
            self.assertNotEqual(decision.status, "fail")
            self.assertIn("disabled", decision.message.lower())


class TestHeuristicProvider(unittest.TestCase):
    def test_heuristic_provider_maps_grade_to_noul_and_action(self):
        from harness.decision import HeuristicDecisionProvider

        provider = HeuristicDecisionProvider(grade_threshold=0.35)
        high = provider.decide(
            {"heuristic_grade": 0.8, "query": "cmd_query"},
            {
                "adequate": {"type": "noul", "instructions": "ok?"},
                "action": {"type": "choice", "instructions": "next", "criteria": {"proceed": "", "rewrite": ""}},
            },
        )
        self.assertGreaterEqual(high["answers"]["adequate"]["noul"], 0.35)
        self.assertEqual(high["answers"]["action"]["choice"], "proceed")

        low = provider.decide(
            {"heuristic_grade": 0.1, "query": "unknown widget"},
            {
                "adequate": {"type": "noul", "instructions": "ok?"},
                "action": {"type": "choice", "instructions": "next", "criteria": {"proceed": "", "rewrite": ""}},
            },
        )
        self.assertEqual(low["answers"]["action"]["choice"], "rewrite")


if __name__ == "__main__":
    unittest.main()
