"""Tests for typed project memory, brief packing, and OKF round-trip."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness.config import Config
from harness.context_builder import ContextBuilder
from harness.models import Chunk, EntityType, RetrievalResult
from harness.okf import (
    OKF_VERSION,
    dump_okf_markdown,
    parse_okf_markdown,
    walk_okf_markdown,
)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


def _chunk():
    return Chunk(
        id="func:harness/demo.py:big_fn:abcd1234",
        content="def big_fn(config):\n    return 1\n",
        entity_id="func:harness/demo.py:big_fn",
        entity_name="big_fn",
        entity_type=EntityType.FUNCTION,
        file_path="harness/demo.py",
        start_line=1,
        end_line=2,
        docstring="",
        metadata={},
    )


def _rr(chunk=None, score=0.9):
    return RetrievalResult(chunk=chunk or _chunk(), score=score, source="test")


class TestMemoryCrud(unittest.TestCase):
    def test_add_list_requires_type_and_title_and_body(self):
        from harness.memory import MemoryError, MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            with self.assertRaises(MemoryError):
                store.add(kind="decision", title="", body="keep chroma")
            with self.assertRaises(MemoryError):
                store.add(kind="decision", title="chroma", body="   ")
            with self.assertRaises(MemoryError):
                store.add(kind="note", title="x", body="y")

            entry = store.add(
                kind="decision",
                title="Default vector backend stays Chroma",
                body="Keep Chroma until TurboVec recall@k passes the eval suite.",
                links=["harness/vector_store.py:VectorStore"],
            )
            self.assertTrue(entry.id)
            self.assertEqual(entry.kind, "decision")
            self.assertEqual(entry.links, ["harness/vector_store.py:VectorStore"])
            listed = store.list()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].title, entry.title)
            self.assertTrue((Path(tmp) / "knowledge" / "memory").exists())

    def test_four_kinds_and_path_symbol_links(self):
        from harness.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            decision = store.add(
                kind="Decision",
                title="CCR default remains full",
                body="Do not flip pack_mode until eval says otherwise.",
                links=["harness/context_builder.py:ContextBuilder"],
            )
            error = store.add(
                kind="error",
                title="FAISS OOM on large repos",
                body="Local FAISS blew RAM while indexing this tree.",
                links=["harness/vector_store.py:VectorStore.add_chunks"],
            )
            store.add(kind="preference", title="Local embed first", body="Prefer sentence-transformers.")
            store.add(kind="fact", title="OKF is Google SPEC", body="Memanto implements; it does not own OKF.")
            kinds = {e.kind for e in store.list()}
            self.assertEqual(kinds, {"decision", "error", "preference", "fact"})
            self.assertIn("harness/context_builder.py:ContextBuilder", decision.links)
            self.assertIn("harness/vector_store.py:VectorStore.add_chunks", error.links)
            self.assertEqual(len(store.list(kind="error")), 1)


class TestMemorySupersede(unittest.TestCase):
    def test_supersede_tombs_old_and_brief_skips_it(self):
        from harness.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            old = store.add(
                kind="decision",
                title="Use FAISS",
                body="Local FAISS is the default vector backend.",
                timestamp="2026-01-01T00:00:00Z",
            )
            new = store.add(
                kind="decision",
                title="Default vector backend stays Chroma",
                body="Keep Chroma until TurboVec recall@k passes.",
                links=["harness/vector_store.py:VectorStore"],
                supersedes=old.id,
                timestamp="2026-09-24T00:00:00Z",
            )
            self.assertEqual(new.supersedes, old.id)
            refreshed = store.get(old.id)
            self.assertEqual(refreshed.status, "superseded")
            self.assertEqual(store.get(new.id).status, "active")

            active = store.list()
            self.assertEqual([e.id for e in active], [new.id])
            all_rows = store.list(include_inactive=True)
            self.assertEqual({e.id for e in all_rows}, {old.id, new.id})

            as_of = store.list(as_of="2026-06-01T00:00:00Z", include_inactive=True)
            self.assertEqual([e.id for e in as_of], [old.id])

            brief = store.brief()
            self.assertIn("Chroma", brief.text)
            self.assertNotIn("Use FAISS", brief.text)
            self.assertNotIn(old.id, brief.entry_ids)
            self.assertIn(old.id, brief.skipped_ids)


class TestMemoryBriefCap(unittest.TestCase):
    def test_brief_hard_caps_at_800_tokens(self):
        from harness.memory import BRIEF_TOKEN_CAP, MemoryStore, estimate_tokens

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            for i in range(20):
                store.add(
                    kind="fact",
                    title=f"Fact {i:02d} about packing",
                    body=("Token filler sentence about context assembly. " * 40),
                    timestamp=f"2026-09-{(i % 28) + 1:02d}T00:00:00Z",
                )
            brief = store.brief()
            self.assertLessEqual(brief.token_count, BRIEF_TOKEN_CAP)
            self.assertLessEqual(estimate_tokens(brief.text), BRIEF_TOKEN_CAP)
            self.assertGreater(brief.token_count, 0)
            self.assertLessEqual(len(brief.entry_ids), 5)
            again = store.brief()
            self.assertEqual(brief.text, again.text)

    def test_brief_query_prefers_matching_decision(self):
        from harness.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.add(
                kind="fact",
                title="Wiki pages live under knowledge/wiki",
                body="Generated WikiPage files.",
                timestamp="2026-09-20T00:00:00Z",
            )
            store.add(
                kind="decision",
                title="Default vector backend stays Chroma",
                body="Keep Chroma until TurboVec recall@k passes the eval suite.",
                links=["harness/vector_store.py:VectorStore"],
                timestamp="2026-09-24T00:00:00Z",
            )
            brief = store.brief(query="why chroma vector store?")
            self.assertIn("Chroma", brief.text)
            self.assertIn("harness/vector_store.py:VectorStore", brief.text)


class TestMemoryOkfRoundTrip(unittest.TestCase):
    def test_export_import_preserves_id_supersedes_and_unknown_keys(self):
        from harness.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            src_repo = Path(tmp) / "src"
            dst_repo = Path(tmp) / "dst"
            bundle = Path(tmp) / "bundle"
            src_repo.mkdir()
            dst_repo.mkdir()
            store = MemoryStore(str(src_repo))
            old = store.add(
                kind="decision",
                title="Use FAISS",
                body="Tried FAISS first.",
                timestamp="2026-01-01T00:00:00Z",
            )
            new = store.add(
                kind="decision",
                title="Chroma default",
                body="Keep Chroma until TurboVec recall gates pass.",
                links=["harness/vector_store.py:VectorStore"],
                supersedes=old.id,
                timestamp="2026-09-24T00:00:00Z",
            )
            # Inject into the new entry's file. glob() order is not stable
            # (CI failed when [-1] was the superseded FAISS note).
            target = Path(src_repo) / "knowledge" / "memory" / new.rel_path
            self.assertTrue(target.is_file(), target)
            raw = target.read_text(encoding="utf-8")
            # Inject a foreign extension the way a Memanto bundle would.
            injected = raw.replace(
                "okf_version: \"0.2\"\n",
                "okf_version: \"0.2\"\nx_other: keep-me\nx_memanto:\n  type: decision\n",
            )
            target.write_text(injected, encoding="utf-8")

            count = store.export_okf(str(bundle))
            self.assertGreaterEqual(count, 2)
            walked = walk_okf_markdown(str(bundle))
            self.assertTrue(walked)
            for _path, page in walked:
                self.assertTrue(page.type)
                self.assertEqual(page.okf_version, OKF_VERSION)

            dest = MemoryStore(str(dst_repo))
            imported = dest.import_okf(str(bundle))
            self.assertGreaterEqual(imported, 2)
            copy = dest.get(new.id)
            self.assertIsNotNone(copy)
            self.assertEqual(copy.supersedes, old.id)
            self.assertEqual(copy.links, ["harness/vector_store.py:VectorStore"])
            self.assertEqual(copy.extras.get("x_other"), "keep-me")
            self.assertEqual(copy.extras.get("x_memanto"), {"type": "decision"})

            page = parse_okf_markdown(dump_okf_markdown(copy.to_page()))
            again = dest.entry_from_page(page, rel_path="decision/chroma.md")
            self.assertEqual(again.extras.get("x_other"), "keep-me")
            self.assertEqual(again.id, new.id)

    def test_wiki_page_round_trip_still_works(self):
        raw = (
            "---\n"
            "type: WikiPage\n"
            "title: Sample\n"
            "okf_version: \"0.2\"\n"
            "x_other: keep-me\n"
            "---\n"
            "\n"
            "body stays\n"
        )
        parsed = parse_okf_markdown(raw)
        again = parse_okf_markdown(dump_okf_markdown(parsed))
        self.assertEqual(again.type, "WikiPage")
        self.assertEqual(again.extras.get("x_other"), "keep-me")


class TestMemoryContextHook(unittest.TestCase):
    def test_query_path_omits_brief_by_default(self):
        from harness.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(tmp)
            store.add(
                kind="decision",
                title="Default vector backend stays Chroma",
                body="Keep Chroma until TurboVec recall@k passes.",
            )
            config = Config()
            config.repo_path = tmp
            builder = ContextBuilder(config)
            report = builder.build_context_report("why chroma?", [_rr()])
            self.assertNotIn("Project memory brief", report.context)
            self.assertNotIn("Default vector backend stays Chroma", report.context)

    def test_opt_in_flag_injects_brief_between_docs_and_hits(self):
        from harness.memory import MemoryStore

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "AGENTS.md").write_text("Be careful.\n", encoding="utf-8")
            store = MemoryStore(tmp)
            store.add(
                kind="decision",
                title="Default vector backend stays Chroma",
                body="Keep Chroma until TurboVec recall@k passes.",
                links=["harness/vector_store.py:VectorStore"],
            )
            config = Config.from_dict(
                {"repo_path": tmp, "context": {"include_memory_brief": True}}
            )
            config.repo_path = tmp
            builder = ContextBuilder(config)
            report = builder.build_context_report("why chroma?", [_rr()])
            self.assertIn("Project memory brief", report.context)
            self.assertIn("Chroma", report.context)
            docs_at = report.context.index("AGENTS.md")
            brief_at = report.context.index("Project memory brief")
            hits_at = report.context.index("harness/demo.py")
            self.assertLess(docs_at, brief_at)
            self.assertLess(brief_at, hits_at)


class TestRepoSampleVault(unittest.TestCase):
    def test_committed_samples_link_path_symbol_and_skip_tombstone(self):
        from harness.memory import MemoryStore

        store = MemoryStore(ROOT)
        chroma = store.get("mem/20260924-default-vector-backend-stays-chroma")
        faiss = store.get("mem/20260101-use-faiss")
        oom = store.get("mem/20260924-faiss-oom-on-large-repos")
        self.assertIsNotNone(chroma)
        self.assertIsNotNone(faiss)
        self.assertIsNotNone(oom)
        self.assertEqual(chroma.status, "active")
        self.assertEqual(faiss.status, "superseded")
        self.assertEqual(chroma.supersedes, faiss.id)
        self.assertIn("harness/vector_store.py:VectorStore", chroma.links)
        self.assertIn("harness/vector_store.py:VectorStore.add_chunks", oom.links)
        brief = store.brief(query="why chroma vector store?")
        self.assertIn("Chroma", brief.text)
        self.assertNotIn("Use FAISS", brief.text)
        self.assertLessEqual(brief.token_count, 800)


class TestMemoryCli(unittest.TestCase):
    def test_memory_help_lists_subcommands(self):
        run = subprocess.run(
            [sys.executable, "main.py", "memory", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        for name in ("add", "list", "brief", "search", "export", "import", "extract"):
            self.assertIn(name, run.stdout)

    def test_cli_add_list_brief_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            add = subprocess.run(
                [
                    sys.executable, os.path.join(ROOT, "main.py"), "memory", "add", tmp,
                    "--type", "decision",
                    "--title", "Default vector backend stays Chroma",
                    "--body", "Keep Chroma until TurboVec recall@k passes.",
                    "--link", "harness/vector_store.py:VectorStore",
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(add.returncode, 0, add.stdout + add.stderr)
            listed = subprocess.run(
                [sys.executable, os.path.join(ROOT, "main.py"), "memory", "list", tmp],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
            self.assertIn("Chroma", listed.stdout)
            brief = subprocess.run(
                [sys.executable, os.path.join(ROOT, "main.py"), "memory", "brief", tmp,
                 "-q", "why chroma?"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(brief.returncode, 0, brief.stdout + brief.stderr)
            self.assertIn("Chroma", brief.stdout)
            self.assertIn("harness/vector_store.py:VectorStore", brief.stdout)

            bundle = os.path.join(tmp, "okf-out")
            exported = subprocess.run(
                [sys.executable, os.path.join(ROOT, "main.py"), "memory", "export", tmp, bundle],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(exported.returncode, 0, exported.stdout + exported.stderr)
            dest = os.path.join(tmp, "other")
            os.makedirs(dest)
            imported = subprocess.run(
                [sys.executable, os.path.join(ROOT, "main.py"), "memory", "import", dest, bundle],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
            listed2 = subprocess.run(
                [sys.executable, os.path.join(ROOT, "main.py"), "memory", "list", dest],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertIn("Chroma", listed2.stdout)


if __name__ == "__main__":
    unittest.main()
