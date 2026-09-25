"""Tests for heuristic memory auto-extract from session JSONL."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from harness.config import Config, DEFAULT_CONFIG


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "memory_extract_session.jsonl")


def _write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestHeuristicExtractFixture(unittest.TestCase):
    def test_fixture_dialogue_extracts_four_kinds_with_path_symbol_links(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import extract_session

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            report = extract_session(
                repo=tmp,
                session_path=FIXTURE,
                store=store,
            )
            kinds = {entry.kind for entry in store.list()}
            self.assertEqual(kinds, {"decision", "error", "preference", "fact"})
            self.assertGreaterEqual(len(report.written), 4)

            decision = next(e for e in store.list() if e.kind == "decision")
            error = next(e for e in store.list() if e.kind == "error")
            preference = next(e for e in store.list() if e.kind == "preference")
            fact = next(e for e in store.list() if e.kind == "fact")

            self.assertIn("Chroma", decision.title + " " + decision.body)
            self.assertIn("harness/vector_store.py:VectorStore", decision.links)
            self.assertIn("harness/vector_store.py:VectorStore.add_chunks", error.links)
            self.assertTrue(
                any("OOM" in (error.title + error.body) or "oom" in (error.title + error.body).lower()
                    for _ in (error,))
            )
            self.assertIn("local", (preference.title + " " + preference.body).lower())
            self.assertIn("OKF", fact.title + " " + fact.body)
            self.assertTrue(decision.generated)
            self.assertEqual(decision.verified, "heuristic")


class TestDryRunWritesNothing(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import extract_session

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            report = extract_session(
                repo=tmp,
                session_path=FIXTURE,
                store=store,
                dry_run=True,
            )
            self.assertTrue(report.dry_run)
            self.assertGreaterEqual(len(report.candidates), 4)
            self.assertEqual(report.written, [])
            self.assertFalse((Path(tmp) / "knowledge" / "memory").exists())
            self.assertEqual(store.list(), [])


class TestExtractSupersedeAndDedupe(unittest.TestCase):
    def test_extract_supersedes_conflicting_decision_on_same_link(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import extract_session

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            old = store.add(
                kind="decision",
                title="Use FAISS",
                body="Local FAISS is the default vector backend.",
                links=["harness/vector_store.py:VectorStore"],
                timestamp="2026-01-01T00:00:00Z",
            )
            report = extract_session(
                repo=tmp,
                session_path=FIXTURE,
                store=store,
            )
            refreshed = store.get(old.id)
            self.assertEqual(refreshed.status, "superseded")
            active = store.list(kind="decision")
            self.assertEqual(len(active), 1)
            self.assertIn("Chroma", active[0].title + " " + active[0].body)
            self.assertEqual(active[0].supersedes, old.id)
            self.assertIn(old.id, report.superseded)

    def test_second_extract_dedupes_identical_candidates(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import extract_session

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            first = extract_session(repo=tmp, session_path=FIXTURE, store=store)
            before = {e.id for e in store.list(include_inactive=True)}
            second = extract_session(repo=tmp, session_path=FIXTURE, store=store)
            after = {e.id for e in store.list(include_inactive=True)}
            self.assertEqual(before, after)
            self.assertGreaterEqual(len(second.skipped), 1)
            self.assertEqual(len(second.written), 0)
            self.assertGreaterEqual(len(first.written), 4)


class TestAutoExtractDefaultOff(unittest.TestCase):
    def test_config_default_is_false(self):
        self.assertFalse(DEFAULT_CONFIG["memory"]["auto_extract"])
        self.assertFalse(Config().memory.get("auto_extract"))
        merged = Config.from_dict({})
        self.assertFalse(merged.memory.get("auto_extract"))

    def test_maybe_auto_extract_is_noop_when_default_off(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import maybe_auto_extract
        from harness.session import Session, SessionTurn

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = os.path.join(tmp, "sessions")
            session = Session(repo=tmp, session_dir=session_dir, session_id="auto-off")
            session.record_turn(SessionTurn(
                role="user",
                text="We decided to keep Chroma as the default in harness/vector_store.py:VectorStore.",
            ))
            session.record_turn(SessionTurn(
                role="assistant",
                text="Default stays Chroma.",
                chunk_ids=["func:harness/vector_store.py:VectorStore:abcd"],
            ))
            store = MemoryStore(tmp)
            config = Config()
            config.repo_path = tmp
            report = maybe_auto_extract(config=config, session=session, store=store)
            self.assertIsNone(report)
            self.assertEqual(store.list(), [])
            self.assertFalse((Path(tmp) / "knowledge" / "memory").exists())

    def test_maybe_auto_extract_runs_after_compact_when_enabled(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import maybe_auto_extract
        from harness.session import Session, SessionTurn, handle_slash, parse_slash

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = os.path.join(tmp, "sessions")
            session = Session(repo=tmp, session_dir=session_dir, session_id="auto-on")
            session.record_turn(SessionTurn(
                role="user",
                text="We decided to keep Chroma as the default vector backend in `harness/vector_store.py:VectorStore`.",
            ))
            session.record_turn(SessionTurn(
                role="assistant",
                text="Default stays Chroma.",
                chunk_ids=["func:harness/vector_store.py:VectorStore:abcd1234"],
            ))
            compact = handle_slash(session, parse_slash("/compact"))
            self.assertEqual(compact.kind, "compact")
            store = MemoryStore(tmp)
            config = Config.from_dict({"memory": {"auto_extract": True}, "repo_path": tmp})
            config.repo_path = tmp
            report = maybe_auto_extract(config=config, session=session, store=store)
            self.assertIsNotNone(report)
            self.assertGreaterEqual(len(store.list()), 1)
            self.assertEqual(store.list()[0].kind, "decision")


class TestLlmRefineNoop(unittest.TestCase):
    def test_llm_flag_is_clean_noop_without_client_or_key(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import extract_session

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            config = Config.from_dict({
                "llm": {"provider": "openai", "api_key": None, "api_base": None},
                "repo_path": tmp,
            })
            env = os.environ.copy()
            for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
                env.pop(key, None)
            old = {k: os.environ.get(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")}
            try:
                for key in old:
                    os.environ.pop(key, None)
                report = extract_session(
                    repo=tmp,
                    session_path=FIXTURE,
                    store=store,
                    config=config,
                    use_llm=True,
                )
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertFalse(report.used_llm)
            self.assertGreaterEqual(len(report.written), 4)
            kinds = {e.kind for e in store.list()}
            self.assertEqual(kinds, {"decision", "error", "preference", "fact"})


class TestLatestSessionAndCli(unittest.TestCase):
    def test_extract_uses_last_session_jsonl_when_unspecified(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import extract_session

        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp) / ".code-harness" / "sessions"
            sessions.mkdir(parents=True)
            older = sessions / "20260101-old.jsonl"
            newer = sessions / "20260925-new.jsonl"
            _write_jsonl(older, [
                {"event": "session_start", "id": "old", "repo": "demo", "profile": "default"},
                {"event": "turn", "role": "user", "text": "Fact: this older session should be ignored."},
            ])
            time.sleep(0.02)
            newer.write_text(Path(FIXTURE).read_text(encoding="utf-8"), encoding="utf-8")
            os.utime(newer, None)
            store = MemoryStore(tmp)
            report = extract_session(repo=tmp, store=store)
            self.assertEqual(os.path.abspath(report.session_path), os.path.abspath(str(newer)))
            kinds = {e.kind for e in store.list()}
            self.assertIn("decision", kinds)
            self.assertNotIn("this older session should be ignored", " ".join(e.body for e in store.list()))

    def test_cli_extract_help_and_dry_run(self):
        help_run = subprocess.run(
            [sys.executable, "main.py", "memory", "extract", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        self.assertIn("--dry-run", help_run.stdout)
        self.assertIn("--session", help_run.stdout)
        self.assertIn("--llm", help_run.stdout)

        top = subprocess.run(
            [sys.executable, "main.py", "memory", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertIn("extract", top.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            dry = subprocess.run(
                [
                    sys.executable, os.path.join(ROOT, "main.py"),
                    "memory", "extract", tmp,
                    "--session", FIXTURE,
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            self.assertTrue(
                (Path(tmp) / "knowledge" / "memory").exists() is False
            )
            listed = subprocess.run(
                [sys.executable, os.path.join(ROOT, "main.py"), "memory", "list", tmp],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
            self.assertNotIn("Chroma", listed.stdout)

            wrote = subprocess.run(
                [
                    sys.executable, os.path.join(ROOT, "main.py"),
                    "memory", "extract", tmp,
                    "--session", FIXTURE,
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(wrote.returncode, 0, wrote.stdout + wrote.stderr)
            listed2 = subprocess.run(
                [sys.executable, os.path.join(ROOT, "main.py"), "memory", "list", tmp],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertIn("Chroma", listed2.stdout)


if __name__ == "__main__":
    unittest.main()
