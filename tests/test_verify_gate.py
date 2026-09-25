"""Default-fail independent verify gate (Claude Code / long-running-agent steal)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from harness.config import Config, DEFAULT_CONFIG
from harness.eval import EvalFixture, load_suite, run_eval
from harness.models import Chunk, EntityType, RetrievalResult


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


class TestEnablementDefaultOff(unittest.TestCase):
    def test_config_and_env_default_off(self):
        from harness.verify import verify_gate_enabled

        self.assertFalse(DEFAULT_CONFIG["session"]["verify"])
        self.assertFalse(verify_gate_enabled())
        self.assertFalse(verify_gate_enabled(config=Config()))
        self.assertTrue(
            verify_gate_enabled(environ={"CODEHARNESS_SESSION_VERIFY": "1"})
        )
        self.assertFalse(
            verify_gate_enabled(
                environ={"CODEHARNESS_SESSION_VERIFY": "0"},
                config=Config.from_dict({"session": {"verify": True}}),
            )
        )
        self.assertTrue(
            verify_gate_enabled(config=Config.from_dict({"session": {"verify": True}}))
        )


class TestDefaultFailContract(unittest.TestCase):
    def test_criteria_start_all_false_even_if_caller_passes_true(self):
        from harness.verify import CompletionGate, Criterion

        gate = CompletionGate(
            enabled=True,
            criteria=[
                Criterion(id="file", description="module exists", kind="file", check="x.py", met=True),
                {"id": "cmd", "description": "tests", "kind": "command", "check": "true", "met": True},
            ],
        )
        self.assertTrue(gate.criteria)
        self.assertTrue(all(not c.met for c in gate.criteria))
        self.assertFalse(gate.is_complete())
        self.assertFalse(gate.verify_ran)

    def test_no_verify_run_is_not_done(self):
        from harness.verify import CompletionGate

        gate = CompletionGate(
            enabled=True,
            criteria=[{"id": "exists", "kind": "file", "check": "harness/verify.py"}],
            cwd=ROOT,
        )
        decision = gate.claim_done("done", role="builder")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "verify_required")
        self.assertFalse(gate.is_complete())

    def test_builder_cannot_set_criteria_true(self):
        from harness.verify import CompletionGate, VerifyRoleError

        gate = CompletionGate(
            enabled=True,
            criteria=[{"id": "exists", "kind": "file", "check": "harness/verify.py"}],
            cwd=ROOT,
        )
        with self.assertRaises(VerifyRoleError):
            gate.set_met("exists", True, role="builder")
        self.assertFalse(gate.criteria[0].met)
        added = gate.add_criterion(
            {"id": "extra", "kind": "file", "check": "README.md", "met": True},
            role="builder",
        )
        self.assertFalse(added.met)
        self.assertFalse(gate.is_complete())

    def test_default_off_unchanged(self):
        from harness.verify import CompletionGate

        gate = CompletionGate(enabled=False)
        self.assertTrue(gate.is_complete())
        decision = gate.claim_done("done", role="builder")
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "verify_off")


class TestIndependentVerifier(unittest.TestCase):
    def test_pass_when_runnable_criteria_met(self):
        from harness.verify import BUILDER_ROLE, VERIFIER_ROLE, CompletionGate

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "shipped.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("ok = True\n")
            gate = CompletionGate(
                enabled=True,
                criteria=[
                    {"id": "file", "kind": "file", "check": "shipped.py"},
                    {"id": "cmd", "kind": "command", "check": f"{sys.executable} -c \"print('ok')\""},
                    {"id": "contains", "kind": "contains", "check": "shipped.py", "expect": "ok = True"},
                    {"id": "coverage", "kind": "coverage", "expect": 0.5},
                ],
                cwd=tmp,
            )
            with self.assertRaises(Exception):
                gate.run_verify(role=BUILDER_ROLE, evidence={"coverage": 0.9})
            result = gate.run_verify(
                role=VERIFIER_ROLE,
                evidence={"coverage": 0.9, "paths": ["shipped.py"]},
            )
            self.assertTrue(result.passed)
            self.assertTrue(gate.is_complete())
            self.assertTrue(all(c.met for c in gate.criteria))
            decision = gate.claim_done("done", role=BUILDER_ROLE)
            self.assertTrue(decision.accepted)
            self.assertEqual(decision.reason, "verified")

    def test_fail_when_file_or_command_missing(self):
        from harness.verify import VERIFIER_ROLE, CompletionGate

        gate = CompletionGate(
            enabled=True,
            criteria=[
                {"id": "file", "kind": "file", "check": "definitely-not-there.py"},
                {"id": "cmd", "kind": "command", "check": f"{sys.executable} -c \"raise SystemExit(2)\""},
            ],
            cwd=ROOT,
        )
        result = gate.run_verify(role=VERIFIER_ROLE)
        self.assertTrue(result.ran)
        self.assertFalse(result.passed)
        self.assertFalse(gate.is_complete())
        self.assertTrue(all(not c.met for c in gate.criteria))

    def test_verifier_has_no_write_tools(self):
        from harness.verify import VERIFIER_SYSTEM, VERIFIER_WRITE_TOOLS, verifier_has_write_tools

        self.assertFalse(verifier_has_write_tools())
        self.assertEqual(VERIFIER_WRITE_TOOLS, ())
        self.assertIn("read-only", VERIFIER_SYSTEM.lower())
        self.assertIn("default", VERIFIER_SYSTEM.lower())
        self.assertRegex(VERIFIER_SYSTEM.lower(), r"fail|false")

    def test_llm_fallback_is_independent_and_default_fail(self):
        from harness.verify import VERIFIER_ROLE, CompletionGate

        calls = []

        def llm(system, context, user):
            calls.append((system, context, user))
            return '{"criteria": [{"id": "llm", "met": false, "evidence": "no proof"}]}'

        gate = CompletionGate(
            enabled=True,
            criteria=[{"id": "llm", "kind": "llm", "description": "behavior matches spec"}],
        )
        result = gate.run_verify(role=VERIFIER_ROLE, llm=llm, evidence={"answer": "I am done"})
        self.assertTrue(result.ran)
        self.assertFalse(result.passed)
        self.assertEqual(len(calls), 1)
        self.assertIn("read-only", calls[0][0].lower())
        self.assertNotIn("I am done", calls[0][0])


class TestForceOverride(unittest.TestCase):
    def test_force_done_accepts_without_green_gate(self):
        from harness.verify import BUILDER_ROLE, CompletionGate

        gate = CompletionGate(
            enabled=True,
            criteria=[{"id": "file", "kind": "file", "check": "missing.py"}],
            cwd=ROOT,
        )
        refused = gate.claim_done("done", role=BUILDER_ROLE)
        self.assertFalse(refused.accepted)
        forced = gate.claim_done("done", role=BUILDER_ROLE, force=True)
        self.assertTrue(forced.accepted)
        self.assertTrue(forced.forced)
        self.assertEqual(forced.reason, "force_done")
        self.assertTrue(gate.is_complete())


class TestDoneClaimHeuristic(unittest.TestCase):
    def test_short_done_claims_and_not_questions(self):
        from harness.verify import looks_like_done_claim

        self.assertTrue(looks_like_done_claim("done"))
        self.assertTrue(looks_like_done_claim("I'm done."))
        self.assertTrue(looks_like_done_claim("the task is complete"))
        self.assertFalse(looks_like_done_claim("how do I know when I'm done?"))
        self.assertFalse(looks_like_done_claim("where is verify_answer"))


class TestSessionSlashAndEndOfTurn(unittest.TestCase):
    def test_verify_and_done_slash_commands(self):
        from harness.session import Session, handle_slash, parse_slash

        session = Session(
            repo=ROOT,
            verify=True,
            verify_criteria=[{"id": "readme", "kind": "file", "check": "README.md"}],
        )
        self.assertTrue(session.verify_enabled)
        help_out = handle_slash(session, parse_slash("/help"))
        self.assertIn("/verify", help_out.message)
        self.assertIn("/done", help_out.message)

        verify = handle_slash(session, parse_slash("/verify"))
        self.assertEqual(verify.kind, "verify")
        self.assertTrue(session.gate.is_complete())
        self.assertIn("readme", verify.message)

        done = handle_slash(session, parse_slash("/done"))
        self.assertEqual(done.kind, "done")
        self.assertTrue(done.done_accepted)

    def test_done_refused_until_force(self):
        from harness.session import Session, handle_slash, parse_slash

        session = Session(
            repo=ROOT,
            verify=True,
            verify_criteria=[{"id": "gone", "kind": "file", "check": "no-such-file.py"}],
        )
        refused = handle_slash(session, parse_slash("/done"))
        self.assertFalse(refused.done_accepted)
        self.assertIn("verify", refused.message.lower())

        forced = handle_slash(session, parse_slash("/done --force"))
        self.assertTrue(forced.done_accepted)
        self.assertTrue(forced.force_done)
        self.assertTrue(session.gate.is_complete())

    def test_end_of_turn_refuses_builder_done_claim(self):
        from harness.session import Session

        session = Session(
            repo=ROOT,
            verify=True,
            verify_criteria=[{"id": "gone", "kind": "file", "check": "no-such-file.py"}],
        )
        banner = session.refuse_done_claim("I'm done.")
        self.assertIsNotNone(banner)
        self.assertIn("verify", banner.lower())
        self.assertIsNone(session.refuse_done_claim("how does packing work?"))

    def test_default_off_session_has_no_gate(self):
        from harness.session import Session, handle_slash, parse_slash

        session = Session(repo=".")
        self.assertFalse(session.verify_enabled)
        self.assertIsNone(session.refuse_done_claim("done"))
        verify = handle_slash(session, parse_slash("/verify"))
        self.assertIn("off", verify.message.lower())


class TestEvalVerifyStage(unittest.TestCase):
    def test_default_eval_skips_verify_stage(self):
        fixture = EvalFixture(
            id="q-plain",
            query="How does context assembly work?",
            must_cite_paths=["harness/context_builder.py"],
            difficulty="easy",
            completion_criteria=[{"id": "mod", "kind": "file", "check": "harness/verify.py"}],
        )
        report = _run_fake_eval([fixture], verify=False)
        self.assertNotIn("verify", report["cases"][0])
        self.assertNotIn("verify_pass_rate", report["metrics"])

    def test_eval_verify_default_fail_then_pass(self):
        fail = EvalFixture(
            id="agent-missing",
            query="Ship the verify module",
            must_cite_paths=["harness/verify.py"],
            difficulty="medium",
            completion_criteria=[{"id": "mod", "kind": "file", "check": "nope.py"}],
        )
        ok = EvalFixture(
            id="agent-ok",
            query="Ship the verify module",
            must_cite_paths=["harness/verify.py"],
            difficulty="medium",
            completion_criteria=[{"id": "mod", "kind": "file", "check": "harness/verify.py"}],
        )
        failed = _run_fake_eval([fail], verify=True, repo_path=ROOT)
        self.assertTrue(failed["cases"][0]["verify"]["ran"])
        self.assertFalse(failed["cases"][0]["verify"]["passed"])
        self.assertEqual(failed["metrics"]["verify_pass_rate"], 0.0)

        passed = _run_fake_eval([ok], verify=True, repo_path=ROOT)
        self.assertTrue(passed["cases"][0]["verify"]["passed"])
        self.assertEqual(passed["metrics"]["verify_pass_rate"], 1.0)

    def test_eval_verify_skips_fixtures_without_criteria(self):
        fixture = EvalFixture(
            id="q-retrieve-only",
            query="How does context assembly work?",
            must_cite_paths=["harness/context_builder.py"],
            difficulty="easy",
        )
        report = _run_fake_eval([fixture], verify=True)
        self.assertTrue(report["cases"][0]["verify"]["skipped"])
        self.assertIsNone(report["metrics"].get("verify_pass_rate"))

    def test_load_suite_reads_completion_criteria(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agent.yaml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    "suite: agent-completion\n"
                    "fixtures:\n"
                    "  - id: agent-verify-module\n"
                    "    query: Implement the independent verify gate\n"
                    "    must_cite_paths: [harness/verify.py]\n"
                    "    difficulty: medium\n"
                    "    completion_criteria:\n"
                    "      - id: module\n"
                    "        kind: file\n"
                    "        check: harness/verify.py\n"
                )
            fixtures, meta = load_suite(path)
            self.assertEqual(meta["suite"], "agent-completion")
            self.assertEqual(fixtures[0].completion_criteria[0]["id"], "module")

        example = load_suite(".docs/research/eval/agent-completion.example.yaml")
        self.assertEqual(example[1]["suite"], "agent-completion-example")
        self.assertTrue(example[0][0].completion_criteria)


class TestCliAffordance(unittest.TestCase):
    def test_session_and_eval_help_expose_verify_and_force_done(self):
        chat = subprocess.run(
            [sys.executable, "main.py", "chat", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(chat.returncode, 0, chat.stderr)
        self.assertIn("--verify", chat.stdout)
        self.assertIn("--force-done", chat.stdout)

        ev = subprocess.run(
            [sys.executable, "main.py", "eval", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(ev.returncode, 0, ev.stderr)
        self.assertIn("--verify", ev.stdout)


def _chunk(cid, path):
    return Chunk(
        id=cid,
        content="x",
        entity_id=cid,
        entity_name="n",
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=2,
    )


def _run_fake_eval(fixtures, verify=False, repo_path="."):
    class _Retriever:
        ce_enabled = False

        def retrieve(self, query, top_k=None, debug=False, **opts):
            hit = RetrievalResult(
                chunk=_chunk("func:harness/verify.py:gate", "harness/verify.py"),
                score=1.0,
                source="test",
            )
            trace = {
                "dense": [hit],
                "sparse": [hit],
                "graph": [hit],
                "fused": [hit],
                "reranked": [hit],
                "latencies_ms": {},
            }
            return ([hit], trace) if debug else [hit]

    class _Builder:
        def build_context_report(self, query, results):
            from harness.eval import ContextReport

            return ContextReport(
                context="# packed\nharness/verify.py",
                prompt_tokens=10,
                packed_chunk_ids=["func:harness/verify.py:gate"],
                packed_paths=["harness/verify.py"],
            )

    return run_eval(
        fixtures=fixtures,
        retriever=_Retriever(),
        context_builder=_Builder(),
        k=10,
        suite_name="verify",
        suite_path="suite.yaml",
        repo_path=repo_path,
        repo_name="workspace",
        verify=verify,
    )


if __name__ == "__main__":
    unittest.main()
