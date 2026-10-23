"""Opt-in harness-record validity (path:symbol + claimed KG relations)."""

import os
import tempfile
import unittest

from harness.config import Config, DEFAULT_CONFIG
from harness.eval import EvalFixture, run_eval
from harness.models import Chunk, EntityType, RetrievalResult
from harness.verify import (
    apply_verify_config,
    check_answer_validity,
    extract_cited_refs,
    extract_claimed_relations,
    run_harness_validity,
    validity_enabled,
)


RECORDS = [
    {"id": "e-loop", "name": "QueryLoop", "file_path": "harness/loop.py"},
    {"id": "e-ret", "name": "Retriever", "file_path": "harness/retriever.py"},
    {"id": "e-gate", "name": "CompletionGate", "file_path": "harness/verify.py"},
]
CALLS_EDGE = [("e-loop", "calls", "e-ret")]


class TestValidityDefaultOff(unittest.TestCase):
    def test_config_default_is_false(self):
        self.assertFalse(DEFAULT_CONFIG["verify"]["validity"])
        self.assertFalse(Config().verify.get("validity"))
        self.assertFalse(validity_enabled())
        self.assertFalse(validity_enabled(config=Config()))

    def test_flag_and_env_opt_in(self):
        self.assertTrue(
            validity_enabled(config=Config.from_dict({"verify": {"validity": True}}))
        )
        self.assertTrue(validity_enabled(environ={"CODEHARNESS_VERIFY_VALIDITY": "1"}))
        self.assertFalse(
            validity_enabled(
                environ={"CODEHARNESS_VERIFY_VALIDITY": "0"},
                config=Config.from_dict({"verify": {"validity": True}}),
            )
        )
        args = type("A", (), {"validity": True})()
        self.assertTrue(validity_enabled(args=args))

    def test_apply_verify_config_stamps_flag(self):
        config = Config()
        apply_verify_config(config, args=type("A", (), {"validity": True})())
        self.assertTrue(config.verify["validity"])
        off = Config.from_dict({"verify": {"validity": True}})
        apply_verify_config(off, environ={"CODEHARNESS_VERIFY_VALIDITY": "0"})
        self.assertFalse(off.verify["validity"])


class TestDeterministicChecks(unittest.TestCase):
    def test_existing_citation_passes(self):
        report = check_answer_validity(
            "See `harness/loop.py:QueryLoop` for the retry path.",
            records=RECORDS,
            graph=CALLS_EDGE,
            enabled=True,
        )
        self.assertTrue(report.ran)
        self.assertFalse(report.flags)
        self.assertEqual(report.citation_count, 1)
        self.assertEqual(report.invalid_citation_rate, 0.0)

    def test_missing_symbol_flagged(self):
        report = check_answer_validity(
            "Cite `harness/loop.py:DoesNotExist`.",
            records=RECORDS,
            graph=CALLS_EDGE,
            enabled=True,
        )
        kinds = [f.kind for f in report.flags]
        self.assertIn("missing_symbol", kinds)
        self.assertEqual(report.invalid_citation_rate, 1.0)

    def test_contradicted_kg_edge_flagged(self):
        report = check_answer_validity(
            "QueryLoop inherits Retriever in this repo.",
            records=RECORDS,
            graph=CALLS_EDGE,
            enabled=True,
        )
        kinds = [f.kind for f in report.flags]
        self.assertIn("contradicted_edge", kinds)
        self.assertTrue(any("inherits" in f.detail for f in report.flags))

    def test_known_calls_edge_passes(self):
        report = check_answer_validity(
            "QueryLoop calls Retriever during each attempt.",
            records=RECORDS,
            graph=CALLS_EDGE,
            enabled=True,
        )
        self.assertFalse(report.flags)

    def test_disabled_path_unchanged(self):
        report = check_answer_validity(
            "Cite `harness/loop.py:DoesNotExist`. QueryLoop inherits Retriever.",
            records=RECORDS,
            graph=CALLS_EDGE,
            enabled=False,
        )
        self.assertTrue(report.skipped)
        self.assertFalse(report.ran)
        self.assertFalse(report.flags)
        self.assertIsNone(report.invalid_citation_rate)

        skipped = run_harness_validity(
            "Cite `harness/loop.py:DoesNotExist`.",
            records=RECORDS,
            graph=CALLS_EDGE,
            enabled=False,
            audit=False,
        )
        self.assertEqual(skipped.reason, "validity_off")

    def test_extractors(self):
        refs = extract_cited_refs("see harness/loop.py:QueryLoop and README.md")
        self.assertEqual(refs[0]["path"], "harness/loop.py")
        self.assertEqual(refs[0]["symbol"], "QueryLoop")
        claims = extract_claimed_relations("A is tested by B; Foo calls Bar")
        rels = {c["rel"] for c in claims}
        self.assertIn("tested_by", rels)
        self.assertIn("calls", rels)


class TestSessionValidityGate(unittest.TestCase):
    def test_disabled_session_verify_message_unchanged(self):
        from harness.session import Session, handle_slash, parse_slash

        session = Session(repo=".")
        self.assertFalse(session.validity_enabled)
        verify = handle_slash(session, parse_slash("/verify"))
        self.assertIn("off", verify.message.lower())
        self.assertNotIn("validity:", verify.message)

    def test_verify_slash_reports_flags_when_on(self):
        from harness.session import Session, SessionTurn, handle_slash, parse_slash

        session = Session(
            repo=".",
            config=Config.from_dict({"verify": {"validity": True}}),
        )
        from harness.verify import records_from_mapping

        session._validity_records = records_from_mapping(RECORDS)
        session._validity_graph = CALLS_EDGE
        session.record_turn(
            SessionTurn(role="assistant", text="See `harness/loop.py:MissingSymbol`.")
        )
        out = handle_slash(session, parse_slash("/verify"))
        self.assertIn("validity:", out.message)
        self.assertIn("missing_symbol", out.message)
        self.assertIsNotNone(session.last_validity)
        self.assertTrue(session.last_validity.flags)


class TestEvalValidityMetric(unittest.TestCase):
    def test_default_eval_omits_validity(self):
        report = _run_fake_eval(
            [
                EvalFixture(
                    id="plain",
                    query="How does the loop work?",
                    must_cite_paths=["harness/loop.py"],
                    answer="See `harness/loop.py:DoesNotExist`.",
                )
            ]
        )
        self.assertNotIn("validity", report["cases"][0])
        self.assertNotIn("invalid_citation_rate", report["metrics"])

    def test_eval_reports_invalid_citation_rate(self):
        config = Config.from_dict({"verify": {"validity": True}})
        report = _run_fake_eval(
            [
                EvalFixture(
                    id="bad-cite",
                    query="How does the loop work?",
                    must_cite_paths=["harness/loop.py"],
                    answer="See `harness/loop.py:DoesNotExist`.",
                )
            ],
            config=config,
        )
        case = report["cases"][0]
        self.assertTrue(case["validity"]["ran"])
        self.assertGreater(case["validity"]["flag_count"], 0)
        self.assertEqual(report["metrics"]["invalid_citation_rate"], 1.0)

    def test_cli_exposes_validity_flag(self):
        import subprocess
        import sys

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."
        for command in ("query", "eval", "chat"):
            run = subprocess.run(
                [sys.executable, "main.py", command, "--help"],
                capture_output=True,
                text=True,
                cwd=root,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("--validity", run.stdout)


class TestAuditValidity(unittest.TestCase):
    def test_audit_row_when_enabled(self):
        from harness.audit import tail_audit

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "audit.jsonl")
            config = Config.from_dict({
                "verify": {"validity": True},
                "redaction": {"audit_path": dest},
            })
            config.repo_path = tmp
            report = run_harness_validity(
                "See `harness/loop.py:DoesNotExist`.",
                config=config,
                records=RECORDS,
                graph=CALLS_EDGE,
                query="who is QueryLoop",
                enabled=True,
            )
            self.assertTrue(report.flags)
            rows = tail_audit(5, path=dest)
            self.assertTrue(rows)
            self.assertEqual(rows[-1]["action"], "verify.validity")
            self.assertGreaterEqual(rows[-1]["flag_count"], 1)


def _chunk(cid, path):
    return Chunk(
        id=cid,
        content="x",
        entity_id=cid,
        entity_name="QueryLoop",
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=2,
    )


def _run_fake_eval(fixtures, config=None):
    class _Retriever:
        ce_enabled = False
        knowledge_graph = None
        _all_chunks = [
            _chunk("func:harness/loop.py:QueryLoop", "harness/loop.py"),
        ]

        def retrieve(self, query, top_k=None, debug=False, **opts):
            hit = RetrievalResult(
                chunk=self._all_chunks[0],
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
                context="# packed\nharness/loop.py",
                prompt_tokens=10,
                packed_chunk_ids=["func:harness/loop.py:QueryLoop"],
                packed_paths=["harness/loop.py"],
            )

    return run_eval(
        fixtures=fixtures,
        retriever=_Retriever(),
        context_builder=_Builder(),
        k=10,
        suite_name="validity",
        suite_path="suite.yaml",
        repo_path=".",
        repo_name="workspace",
        config=config,
    )


if __name__ == "__main__":
    unittest.main()
