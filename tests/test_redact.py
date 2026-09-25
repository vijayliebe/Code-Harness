"""Secret redaction + append-only audit JSONL (Fusion PR #4)."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness.config import Config, DEFAULT_CONFIG
from harness.context_builder import ContextBuilder
from harness.models import Chunk, EntityType, RetrievalResult


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."

# Unique fake secrets used only as fixtures. Concatenated so GitHub push
# protection does not treat this test file as a live-secret leak.
FAKE_OPENAI = "sk-proj-" + "TESTFAKESECRETKEY1234567890ABCD"
FAKE_ANTHROPIC = "sk-ant-" + "api03-FakeAnthropicKeyValue1234567890ABCD"
FAKE_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_GITHUB = "ghp_" + "abcdefghijklmnopqrstuvwxyz1234567890"
FAKE_SLACK = "xoxb-" + "123456789012-123456789012-abcdefghijklmnopqrstuvwx"
FAKE_STRIPE = "sk_live_" + "51FakeStripeKeyTest123456"
FAKE_ENV_VALUE = "supersecretvalue12345"
FAKE_DB_PASSWORD = "s3cretPassw0rd"
FAKE_BEARER = "ya29." + "a0FakeBearerTokenValue1234567890ABCD"
FAKE_JWT = (
    "eyJ" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
    "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFBP7Uc9X6s"
)
FAKE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAvFakePrivateKeyMaterialNotReal0001\n"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789pluspad==\n"
    "-----END RSA PRIVATE KEY-----"
)
FAKE_CONN = f"postgres://app:{FAKE_DB_PASSWORD}@localhost:5432/db"

ALL_FAKES = (
    FAKE_OPENAI,
    FAKE_ANTHROPIC,
    FAKE_AWS,
    FAKE_GITHUB,
    FAKE_SLACK,
    FAKE_STRIPE,
    FAKE_ENV_VALUE,
    FAKE_DB_PASSWORD,
    FAKE_BEARER,
    FAKE_JWT,
    FAKE_PEM,
    FAKE_CONN,
)


def _assert_no_plaintext(test, text, extras=()):
    blob = text or ""
    for secret in ALL_FAKES + tuple(extras):
        test.assertNotIn(secret, blob, f"plaintext secret leaked: {secret[:12]}...")


def _chunk(content, cid="func:harness/demo.py:run_eval:abcd1234"):
    return Chunk(
        id=cid,
        content=content,
        entity_id="func:harness/demo.py:run_eval",
        entity_name="run_eval",
        entity_type=EntityType.FUNCTION,
        file_path="harness/demo.py",
        start_line=1,
        end_line=max(2, content.count("\n") + 1),
    )


def _rr(content):
    return RetrievalResult(chunk=_chunk(content), score=0.9, source="test")


class TestRedactPatterns(unittest.TestCase):
    def test_known_secret_patterns_are_stripped(self):
        from harness.redact import redact_text

        samples = {
            "api_key": f"openai_key = '{FAKE_OPENAI}'",
            "anthropic": f"client = Anthropic(api_key='{FAKE_ANTHROPIC}')",
            "aws": f"aws_access_key_id = {FAKE_AWS}",
            "github": f"Authorization: token {FAKE_GITHUB}",
            "slack": f"SLACK_BOT_TOKEN={FAKE_SLACK}",
            "stripe": f"stripe.api_key = '{FAKE_STRIPE}'",
            "env": f"API_KEY={FAKE_ENV_VALUE}",
            "bearer": f"Authorization: Bearer {FAKE_BEARER}",
            "jwt": f"token={FAKE_JWT}",
            "pem": FAKE_PEM,
            "conn": f"DATABASE_URL={FAKE_CONN}",
        }
        for kind, text in samples.items():
            result = redact_text(text)
            self.assertGreater(result.count, 0, kind)
            _assert_no_plaintext(self, result.text)
            self.assertIn("REDACTED", result.text)

    def test_redacted_output_never_contains_fixture_plaintext(self):
        from harness.redact import redact_text

        blob = "\n".join(
            [
                f"OPENAI_API_KEY={FAKE_OPENAI}",
                f"Authorization: Bearer {FAKE_BEARER}",
                FAKE_PEM,
                f"DATABASE_URL={FAKE_CONN}",
                f"export GH_TOKEN={FAKE_GITHUB}",
            ]
        )
        result = redact_text(blob)
        _assert_no_plaintext(self, result.text)
        self.assertGreaterEqual(result.count, 4)
        for hit in result.hits:
            self.assertTrue(hit.fingerprint)
            self.assertNotIn(FAKE_OPENAI, hit.fingerprint)
            self.assertEqual(len(hit.fingerprint), 12)

    def test_fingerprint_is_sha256_prefix_not_raw_secret(self):
        from harness.redact import fingerprint, redact_text

        fp = fingerprint(FAKE_OPENAI)
        self.assertEqual(fp, hashlib.sha256(FAKE_OPENAI.encode("utf-8")).hexdigest()[:12])
        result = redact_text(f"API_KEY={FAKE_OPENAI}")
        self.assertTrue(any(h.fingerprint == fp for h in result.hits))
        _assert_no_plaintext(self, json.dumps([h.__dict__ for h in result.hits]))

    def test_normal_repo_code_is_not_shredded(self):
        from harness.redact import redact_text

        # Fixtures from this repo: env *names*, Bearer templates, short hashes.
        code = """
def _resolve_api_key(self):
    key_map = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
    return os.environ.get(env_var) if env_var else None

headers["Authorization"] = f"Bearer {self.api_key}"
digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
token = tokenize(text)
def password_ok(password):
    return bool(password)
"""
        result = redact_text(code)
        self.assertEqual(result.count, 0, result.text)
        self.assertIn("OPENAI_API_KEY", result.text)
        self.assertIn('f"Bearer {self.api_key}"', result.text)
        self.assertIn("hexdigest()[:12]", result.text)

    def test_placeholders_and_short_values_are_kept(self):
        from harness.redact import redact_text

        text = "\n".join(
            [
                'export OPENAI_API_KEY="sk-..."',
                "API_KEY=changeme",
                "API_KEY=YOUR_API_KEY",
                "password=secret",
                "TOKEN=xxx",
            ]
        )
        result = redact_text(text)
        self.assertEqual(result.count, 0, result.text)

    def test_disable_flag_leaves_text_unchanged(self):
        from harness.redact import redact_text

        raw = f"API_KEY={FAKE_ENV_VALUE}"
        result = redact_text(raw, enabled=False)
        self.assertEqual(result.text, raw)
        self.assertEqual(result.count, 0)

    def test_idempotent_on_already_redacted_text(self):
        from harness.redact import redact_text

        once = redact_text(f"API_KEY={FAKE_OPENAI}")
        twice = redact_text(once.text)
        self.assertEqual(once.text, twice.text)
        self.assertEqual(twice.count, 0)

    def test_env_disable_is_the_only_test_escape_hatch(self):
        from harness.redact import redaction_enabled

        self.assertTrue(redaction_enabled(Config(), environ={}))
        self.assertFalse(redaction_enabled(Config(), environ={"CODEHARNESS_REDACT": "0"}))
        self.assertFalse(redaction_enabled(Config(), environ={"CODEHARNESS_REDACT": "false"}))
        self.assertFalse(redaction_enabled(Config(), environ={"CODEHARNESS_REDACT": "off"}))
        disabled = Config.from_dict({"redaction": {"enabled": False}})
        self.assertFalse(redaction_enabled(disabled, environ={}))
        self.assertTrue(DEFAULT_CONFIG["redaction"]["enabled"])


class TestAuditJsonl(unittest.TestCase):
    def test_append_records_counts_and_fingerprints_never_raw(self):
        from harness.audit import append_audit, tail_audit
        from harness.redact import redact_and_audit, redact_text

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            result = redact_text(f"Authorization: Bearer {FAKE_BEARER}\nAPI_KEY={FAKE_ENV_VALUE}")
            append_audit(
                action="redact.context",
                path=path,
                redaction=result,
                query_id="qid-test",
                session_id="sid-test",
                chunk_ids=["chunk-1"],
                tokens=42,
                model="gpt-4o",
            )
            rows = tail_audit(20, path=path)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["action"], "redact.context")
            self.assertGreaterEqual(row["redaction_count"], 2)
            self.assertIn("ts", row)
            self.assertEqual(row["query_id"], "qid-test")
            self.assertEqual(row["session_id"], "sid-test")
            self.assertEqual(row["chunk_ids"], ["chunk-1"])
            self.assertEqual(row["tokens"], 42)
            self.assertEqual(row["model"], "gpt-4o")
            self.assertTrue(row["fingerprints"])
            dumped = json.dumps(row)
            _assert_no_plaintext(self, dumped)
            self.assertNotIn(FAKE_BEARER, dumped)
            self.assertNotIn(FAKE_ENV_VALUE, dumped)

            again = redact_and_audit(
                f"OPENAI_API_KEY={FAKE_OPENAI}",
                action="redact.llm",
                audit_path=path,
                query="how does packing work?",
            )
            _assert_no_plaintext(self, again.text)
            rows = tail_audit(5, path=path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[-1]["action"], "redact.llm")
            _assert_no_plaintext(self, json.dumps(rows))

    def test_append_only_grows_the_file(self):
        from harness.audit import append_audit, tail_audit
        from harness.redact import redact_text

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            hit = redact_text(f"API_KEY={FAKE_ENV_VALUE}")
            append_audit(action="redact.a", path=path, redaction=hit)
            append_audit(action="redact.b", path=path, redaction=hit)
            lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual([r["action"] for r in tail_audit(1, path=path)], ["redact.b"])


class TestContextAndLlmHooks(unittest.TestCase):
    def test_packed_context_redacts_secrets_by_default(self):
        from harness.redact import fingerprint

        secret_chunk = (
            "def run_eval():\n"
            f"    openai_key = '{FAKE_OPENAI}'\n"
            f"    headers = {{'Authorization': 'Bearer {FAKE_BEARER}'}}\n"
            "    return {}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.from_dict(
                {
                    "repo_path": tmp,
                    "redaction": {
                        "enabled": True,
                        "audit": True,
                        "audit_path": os.path.join(tmp, "audit.jsonl"),
                    },
                }
            )
            config.repo_path = tmp
            builder = ContextBuilder(config)
            report = builder.build_context_report("How does eval work?", [_rr(secret_chunk)])
            _assert_no_plaintext(self, report.context)
            self.assertIn("REDACTED", report.context)
            self.assertIn("def run_eval():", report.context)
            self.assertGreater(report.redaction_count, 0)
            self.assertEqual(report.packed_chunk_ids, [_chunk(secret_chunk).id])
            audit = Path(tmp, "audit.jsonl").read_text(encoding="utf-8")
            _assert_no_plaintext(self, audit)
            self.assertIn(fingerprint(FAKE_OPENAI), audit)

    def test_expand_and_retrieve_helpers_redact_response_bodies(self):
        from harness.redact import redact_text

        raw = f"TOKEN={FAKE_GITHUB}\n" + "value_40 = 1\n"
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.from_dict(
                {
                    "context": {"pack_mode": "ccr_lite"},
                    "ccr": {"spill_dir": tmp, "first_lines": 1, "last_lines": 0, "omit_threshold": 1},
                    "redaction": {"audit_path": os.path.join(tmp, "audit.jsonl")},
                }
            )
            builder = ContextBuilder(config)
            chunk = _chunk(raw, cid="func:harness/demo.py:run_eval:deadbeef")
            report = builder.build_context_report("explain", [RetrievalResult(chunk=chunk, score=0.8, source="t")])
            expanded = builder.expand_into_context(report.context, [chunk.id])
            _assert_no_plaintext(self, expanded)
            self.assertIn("value_40", expanded)
            outbound = redact_text(raw)
            _assert_no_plaintext(self, outbound.text)
            self.assertGreater(outbound.count, 0)

    def test_llm_prepare_outbound_redacts_messages(self):
        from harness.llm import LLMInterface

        with tempfile.TemporaryDirectory() as tmp:
            config = Config.from_dict(
                {
                    "redaction": {"audit_path": os.path.join(tmp, "audit.jsonl")},
                    "llm": {"provider": "ollama", "model": "demo"},
                }
            )
            llm = LLMInterface(config)
            messages = llm.prepare_outbound(
                "You are an expert.",
                f"context has {FAKE_OPENAI}",
                f"what is API_KEY={FAKE_ENV_VALUE}",
            )
            blob = json.dumps(messages)
            _assert_no_plaintext(self, blob)
            self.assertTrue(any("REDACTED" in m["content"] for m in messages))

    def test_no_redact_flag_disables_packer(self):
        raw = f"API_KEY={FAKE_ENV_VALUE}"
        config = Config.from_dict({"redaction": {"enabled": False, "audit": False}})
        builder = ContextBuilder(config)
        report = builder.build_context_report("q", [_rr(raw)])
        self.assertIn(FAKE_ENV_VALUE, report.context)
        self.assertEqual(report.redaction_count, 0)


class TestSessionMemoryWikiHooks(unittest.TestCase):
    def test_session_jsonl_and_prints_redact_secrets(self):
        from harness.session import Session, SessionTurn

        with tempfile.TemporaryDirectory() as tmp:
            session = Session(
                repo="demo",
                session_dir=tmp,
                session_id="20260925-redact",
                redact=True,
                audit_path=os.path.join(tmp, "audit.jsonl"),
            )
            session.record_turn(SessionTurn(role="user", text=f"use {FAKE_OPENAI}"))
            session.record_turn(
                SessionTurn(
                    role="assistant",
                    text=f"Authorization: Bearer {FAKE_BEARER}",
                    chunk_ids=["id-1"],
                )
            )
            path = session.jsonl_path()
            dumped = Path(path).read_text(encoding="utf-8")
            _assert_no_plaintext(self, dumped)
            self.assertIn("REDACTED", dumped)
            self.assertIn("use ", session.turns[0].text)
            _assert_no_plaintext(self, session.turns[0].text)
            _assert_no_plaintext(self, session.history_for_prompt())

    def test_memory_brief_and_export_redact_when_flagged(self):
        from harness.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.add(
                kind="fact",
                title="CI token",
                body=f"API_KEY={FAKE_ENV_VALUE}",
            )
            plain = store.brief(query="token")
            self.assertIn(FAKE_ENV_VALUE, plain.text)
            redacted = store.brief(query="token", redact=True)
            _assert_no_plaintext(self, redacted.text)
            self.assertIn("REDACTED", redacted.text)

            dest = os.path.join(tmp, "bundle")
            store.export_okf(dest, redact=True)
            exported = ""
            for path in Path(dest).rglob("*.md"):
                exported += path.read_text(encoding="utf-8")
            _assert_no_plaintext(self, exported)

    def test_wiki_generate_strips_env_like_echoes(self):
        from harness.knowledge_graph import KnowledgeGraph
        from harness.models import CodeEntity
        from harness.wiki import generate_wiki

        with tempfile.TemporaryDirectory() as tmp:
            src = (
                "class Seed:\n"
                f'    """API_KEY={FAKE_ENV_VALUE}"""\n'
                "    def grow(self):\n"
                "        return 1\n"
            )
            Path(tmp, "pkg").mkdir()
            Path(tmp, "pkg", "mod.py").write_text(src, encoding="utf-8")
            cfg = Config.from_dict(
                {"redaction": {"audit_path": os.path.join(tmp, "audit.jsonl")}}
            )
            cfg.repo_path = tmp
            kg = KnowledgeGraph(cfg, repo_name="fixture")
            kg.build(
                [
                    CodeEntity(
                        id="class:pkg/mod.py:Seed",
                        name="Seed",
                        type=EntityType.CLASS,
                        file_path="pkg/mod.py",
                        start_line=1,
                        end_line=4,
                        source_code=src,
                        docstring=f"API_KEY={FAKE_ENV_VALUE}",
                        metadata={"methods": ["grow"]},
                    )
                ]
            )
            out = Path(tmp) / "knowledge" / "wiki"
            generate_wiki(kg, repo_path=tmp, out_dir=out, repo_name="fixture", config=cfg)
            written = "".join(p.read_text(encoding="utf-8") for p in out.glob("*.md"))
            _assert_no_plaintext(self, written)
            self.assertIn("REDACTED", written)


class TestAuditCli(unittest.TestCase):
    def test_audit_show_and_tail_help(self):
        for args in (["audit", "--help"], ["audit", "show", "--help"], ["audit", "tail", "--help"]):
            run = subprocess.run(
                [sys.executable, "main.py", *args],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertRegex(run.stdout, r"last|tail|audit")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            Path(path).write_text(
                json.dumps({"ts": "t", "action": "redact.context", "redaction_count": 1}) + "\n",
                encoding="utf-8",
            )
            run = subprocess.run(
                [sys.executable, "main.py", "audit", "show", "--last", "5", "--path", path],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("redact.context", run.stdout)

        top = subprocess.run(
            [sys.executable, "main.py", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertIn("audit", top.stdout)


if __name__ == "__main__":
    unittest.main()
