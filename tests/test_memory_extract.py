"""Tests for heuristic memory auto-extract (no LLM / no network)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness.config import DEFAULT_CONFIG, Config


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


def _fixture_turns():
    return [
        {
            "role": "user",
            "text": "We use Chroma instead of FAISS for the default vector backend.",
        },
        {
            "role": "assistant",
            "text": "Chose Chroma. Default is Chroma until TurboVec recall gates pass. "
            "See `harness/vector_store.py:VectorStore`.",
            "chunk_ids": ["func:harness/vector_store.py:VectorStore:abcd1234"],
        },
        {
            "role": "user",
            "text": "ImportError: no module named faiss. Fixed by staying on Chroma.",
        },
        {
            "role": "assistant",
            "text": (
                "OOM when FAISS indexed the tree.\n"
                "Traceback (most recent call last):\n"
                '  File "harness/vector_store.py", line 12, in add_chunks\n'
                "MemoryError: blew RAM"
            ),
            "chunk_ids": ["func:harness/vector_store.py:VectorStore.add_chunks:ef01"],
        },
        {
            "role": "user",
            "text": "Always use local embeddings. Prefer sentence-transformers. "
            "Don't call the cloud embedder.",
        },
        {
            "role": "assistant",
            "text": "`harness/config.py:Config.from_dict` merges DEFAULT_CONFIG sections.",
            "chunk_ids": ["func:harness/config.py:Config.from_dict:aa11"],
        },
    ]


def _write_session_jsonl(path, turns, repo="demo"):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "event": "session_start",
                    "id": "20260925-extract",
                    "repo": repo,
                    "profile": "default",
                }
            )
            + "\n"
        )
        for turn in turns:
            row = {"event": "turn", **turn}
            row.setdefault("chunk_ids", [])
            fh.write(json.dumps(row) + "\n")


class TestHeuristicKindsAndLinks(unittest.TestCase):
    def test_fixture_dialogue_detects_kinds_and_path_symbol_links(self):
        from harness.memory_extract import extract_candidates

        cands = extract_candidates(_fixture_turns())
        kinds = {c.kind for c in cands}
        self.assertEqual(kinds, {"decision", "error", "preference", "fact"})

        decisions = [c for c in cands if c.kind == "decision"]
        self.assertTrue(decisions)
        self.assertTrue(
            any("chroma" in (c.title + " " + c.body).lower() for c in decisions)
        )
        self.assertTrue(
            any(
                "harness/vector_store.py:VectorStore" in c.links
                for c in decisions
            )
        )

        errors = [c for c in cands if c.kind == "error"]
        self.assertTrue(
            any(
                "oom" in (c.title + " " + c.body).lower()
                or "importerror" in (c.title + " " + c.body).lower()
                for c in errors
            )
        )
        self.assertTrue(
            any(
                "harness/vector_store.py:VectorStore.add_chunks" in c.links
                for c in errors
            )
        )

        prefs = [c for c in cands if c.kind == "preference"]
        self.assertTrue(
            any("prefer" in (c.title + " " + c.body).lower() for c in prefs)
            or any("always" in (c.title + " " + c.body).lower() for c in prefs)
        )

        facts = [c for c in cands if c.kind == "fact"]
        self.assertTrue(
            any("harness/config.py:Config.from_dict" in c.links for c in facts)
        )
        self.assertTrue(
            any("merges" in (c.title + " " + c.body).lower() for c in facts)
        )


class TestDryRunWritesNothing(unittest.TestCase):
    def test_dry_run_prints_candidates_and_writes_nothing(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import run_extract

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            result = run_extract(
                turns=_fixture_turns(),
                store=store,
                dry_run=True,
                invoked=True,
            )
            self.assertTrue(result.dry_run)
            self.assertGreaterEqual(len(result.candidates), 4)
            self.assertEqual(result.written, [])
            self.assertFalse(list(Path(tmp, "knowledge", "memory").rglob("*.md")))
            self.assertEqual(store.list(), [])


class TestSupersedeAndDedupe(unittest.TestCase):
    def test_matching_title_or_link_supersedes_active_entry(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import run_extract

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            old = store.add(
                kind="decision",
                title="We use Chroma instead of FAISS for the default vector backend",
                body="Earlier note: stay on Chroma.",
                links=["harness/vector_store.py:VectorStore"],
                timestamp="2026-01-01T00:00:00Z",
            )
            result = run_extract(
                turns=_fixture_turns(),
                store=store,
                dry_run=False,
                invoked=True,
            )
            self.assertFalse(result.dry_run)
            self.assertTrue(result.written)
            refreshed = store.get(old.id)
            self.assertEqual(refreshed.status, "superseded")
            active = [e for e in store.list() if e.kind == "decision"]
            self.assertTrue(any(e.supersedes == old.id for e in active))
            self.assertTrue(all(e.status == "active" for e in store.list()))

    def test_identical_title_and_body_is_deduped_not_rewritten(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import extract_candidates, run_extract

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            cands = extract_candidates(_fixture_turns())
            first = next(c for c in cands if c.kind == "decision")
            existing = store.add(
                kind=first.kind,
                title=first.title,
                body=first.body,
                links=first.links,
            )
            before = {p: p.read_text(encoding="utf-8") for p in Path(tmp).rglob("*.md")}
            result = run_extract(
                candidates=[first],
                store=store,
                dry_run=False,
                invoked=True,
            )
            self.assertEqual(store.get(existing.id).status, "active")
            self.assertFalse(any(e.supersedes == existing.id for e in store.list(include_inactive=True)))
            after = {p: p.read_text(encoding="utf-8") for p in Path(tmp).rglob("*.md")}
            self.assertEqual(before, after)
            self.assertIn(existing.id, result.skipped)


class TestAutoExtractDefaultOff(unittest.TestCase):
    def test_config_auto_extract_defaults_false(self):
        from harness.memory_extract import auto_extract_enabled, maybe_auto_extract

        self.assertIn("memory", DEFAULT_CONFIG)
        self.assertFalse(DEFAULT_CONFIG["memory"]["auto_extract"])
        self.assertFalse(Config().memory["auto_extract"])
        self.assertFalse(auto_extract_enabled(Config()))
        self.assertFalse(auto_extract_enabled(None))
        merged = Config.from_dict({"memory": {"auto_extract": True}})
        self.assertTrue(auto_extract_enabled(merged))

        from harness.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            result = maybe_auto_extract(
                turns=_fixture_turns(),
                store=store,
                config=Config(),
            )
            self.assertEqual(result.written, [])
            self.assertEqual(store.list(), [])
            self.assertFalse(result.auto)

    def test_auto_true_writes_after_compact_or_exit_hook(self):
        from harness.memory import MemoryStore
        from harness.memory_extract import maybe_auto_extract

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            cfg = Config.from_dict({"memory": {"auto_extract": True}})
            result = maybe_auto_extract(
                turns=_fixture_turns(),
                store=store,
                config=cfg,
            )
            self.assertTrue(result.auto)
            self.assertTrue(result.written)
            self.assertTrue(store.list())


class TestSessionJsonlAndQueryTrace(unittest.TestCase):
    def test_load_session_jsonl_and_latest_session(self):
        from harness.memory_extract import (
            extract_candidates,
            latest_session_path,
            load_session_jsonl,
        )

        with tempfile.TemporaryDirectory() as tmp:
            older = os.path.join(tmp, "20260924-100000.jsonl")
            newer = os.path.join(tmp, "20260925-120000.jsonl")
            _write_session_jsonl(older, [{"role": "user", "text": "Prefer older file."}])
            _write_session_jsonl(newer, _fixture_turns())
            self.assertEqual(latest_session_path(tmp), newer)
            turns = load_session_jsonl(newer)
            self.assertGreaterEqual(len(turns), 6)
            self.assertEqual(turns[0].role, "user")
            kinds = {c.kind for c in extract_candidates(turns)}
            self.assertIn("decision", kinds)

    def test_one_shot_query_trace_is_optional_source(self):
        from harness.memory_extract import extract_candidates, load_session_jsonl

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "query-trace.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "event": "query",
                            "query": "We use Chroma instead of FAISS.",
                            "answer": "Default is Chroma. See harness/vector_store.py:VectorStore.",
                            "chunk_ids": ["func:harness/vector_store.py:VectorStore:abcd"],
                        }
                    )
                    + "\n"
                )
            turns = load_session_jsonl(path)
            kinds = {c.kind for c in extract_candidates(turns)}
            self.assertIn("decision", kinds)


class TestLlmRefineNoopWithoutKey(unittest.TestCase):
    def test_refine_without_key_returns_same_candidates(self):
        from harness.llm import LLMInterface
        from harness.memory_extract import extract_candidates, refine_candidates

        cands = extract_candidates(_fixture_turns())
        cfg = Config.from_dict({"llm": {"provider": "openai", "api_key": None}})
        llm = LLMInterface(cfg)
        llm.api_key = None
        refined = refine_candidates(cands, llm=llm, config=cfg)
        self.assertEqual([(c.kind, c.title, c.body) for c in refined],
                         [(c.kind, c.title, c.body) for c in cands])

        none_refined = refine_candidates(cands, llm=None, config=cfg)
        self.assertEqual(len(none_refined), len(cands))


class TestSlashAndCli(unittest.TestCase):
    def test_memory_extract_slash_is_cheap(self):
        from harness.session import Session, handle_slash, parse_slash

        session = Session(repo=".")
        parsed = parse_slash("/memory extract --dry-run")
        self.assertEqual(parsed.name, "memory")
        self.assertIn("extract", parsed.args)
        result = handle_slash(session, parsed)
        self.assertEqual(result.kind, "memory_extract")
        self.assertTrue(result.memory_extract)
        self.assertTrue(result.dry_run)

        write = handle_slash(session, parse_slash("/memory extract"))
        self.assertEqual(write.kind, "memory_extract")
        self.assertFalse(write.dry_run)

        help_out = handle_slash(session, parse_slash("/help"))
        self.assertIn("/memory extract", help_out.message)

    def test_cli_extract_help_lists_dry_run(self):
        run = subprocess.run(
            [sys.executable, "main.py", "memory", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("extract", run.stdout)

        extract_help = subprocess.run(
            [sys.executable, "main.py", "memory", "extract", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(extract_help.returncode, 0, extract_help.stderr)
        self.assertIn("dry-run", extract_help.stdout)
        self.assertIn("--llm", extract_help.stdout)

    def test_cli_dry_run_from_session_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = os.path.join(tmp, "sess.jsonl")
            _write_session_jsonl(session, _fixture_turns())
            repo = os.path.join(tmp, "repo")
            os.makedirs(repo)
            run = subprocess.run(
                [
                    sys.executable,
                    os.path.join(ROOT, "main.py"),
                    "memory",
                    "extract",
                    repo,
                    "--session",
                    session,
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertRegex(run.stdout.lower(), r"decision|error|preference|fact")
            self.assertFalse(list(Path(repo, "knowledge", "memory").rglob("*.md")))

    def test_cli_extract_writes_when_not_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = os.path.join(tmp, "sess.jsonl")
            _write_session_jsonl(session, _fixture_turns())
            repo = os.path.join(tmp, "repo")
            os.makedirs(repo)
            run = subprocess.run(
                [
                    sys.executable,
                    os.path.join(ROOT, "main.py"),
                    "memory",
                    "extract",
                    repo,
                    "--session",
                    session,
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            written = list(Path(repo, "knowledge", "memory").rglob("*.md"))
            self.assertTrue(written)
            listed = subprocess.run(
                [sys.executable, os.path.join(ROOT, "main.py"), "memory", "list", repo],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertIn("Chroma", listed.stdout)


if __name__ == "__main__":
    unittest.main()
