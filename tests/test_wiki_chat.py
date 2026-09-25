"""Chat-over-wiki mode: retrieve/pack/cite wiki first; default chat unchanged."""

import os
import subprocess
import sys
import tempfile
import unittest

from harness.config import DEFAULT_CONFIG, Config
from harness.context_builder import ContextBuilder
from harness.knowledge_graph import KnowledgeGraph
from harness.models import Chunk, EntityType, RetrievalResult
from harness.retriever import Retriever, is_wiki_chunk


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


def _chunk(cid, content, path, name="Page", kind=None, etype=EntityType.DOCUMENTATION):
    meta = {}
    if kind:
        meta["kind"] = kind
    return Chunk(
        id=cid,
        content=content,
        entity_id=cid,
        entity_name=name,
        entity_type=etype,
        file_path=path,
        start_line=1,
        end_line=8,
        metadata=meta,
    )


class FakeEmbedder:
    def __init__(self):
        self.hyde_enabled = False

    def embed_query(self, query, expand=True):
        return [0.1, 0.2, 0.3]


class FakeVectorStore:
    supports_allowlist = False

    def __init__(self, results):
        self._results = results

    def search(self, embedding, top_k=10, repo_name=None, allowlist_ids=None):
        return list(self._results)[:top_k]


def _retriever(chunks, dense, *, wiki_weight=0.0, wiki_mode=False):
    from harness.wiki_chat import apply_wiki_mode

    cfg = Config()
    cfg.retrieval["wiki_weight"] = wiki_weight
    cfg.retrieval["cross_encoder"] = {"enabled": False}
    cfg.retrieval["expand_neighbors"] = 0
    if wiki_mode:
        apply_wiki_mode(cfg, True)
    kg = KnowledgeGraph(cfg, repo_name="fixture")
    kg.enabled = False
    retriever = Retriever(cfg, FakeEmbedder(), FakeVectorStore(dense), kg, repo_name="fixture")
    retriever.index_chunks(chunks, persist=False)
    return retriever


CODE = _chunk(
    "code",
    "def grow(): return 1",
    "pkg/mod.py",
    name="grow",
    etype=EntityType.FUNCTION,
)
WIKI = _chunk(
    "wiki",
    "Package pkg documents Seed.grow assembly. See `pkg/mod.py:grow`.",
    "knowledge/wiki/pkg.md",
    kind="wiki",
)


class TestWikiModeConfig(unittest.TestCase):
    def test_default_wiki_mode_is_off(self):
        from harness.wiki_chat import wiki_mode_enabled

        self.assertFalse(DEFAULT_CONFIG["chat"]["wiki_mode"])
        cfg = Config()
        self.assertFalse(cfg.chat.get("wiki_mode"))
        self.assertFalse(wiki_mode_enabled(cfg))
        self.assertEqual(float(cfg.retrieval.get("wiki_weight", 0.0)), 0.0)

    def test_apply_wiki_mode_sets_weight_and_restores(self):
        from harness.wiki_chat import WIKI_MODE_WEIGHT, apply_wiki_mode, wiki_mode_enabled

        cfg = Config()
        apply_wiki_mode(cfg, True)
        self.assertTrue(wiki_mode_enabled(cfg))
        self.assertGreater(float(cfg.retrieval["wiki_weight"]), 0.0)
        self.assertAlmostEqual(float(cfg.retrieval["wiki_weight"]), WIKI_MODE_WEIGHT)
        apply_wiki_mode(cfg, False)
        self.assertFalse(wiki_mode_enabled(cfg))
        self.assertEqual(float(cfg.retrieval["wiki_weight"]), 0.0)

    def test_apply_preserves_explicit_wiki_weight(self):
        from harness.wiki_chat import apply_wiki_mode

        cfg = Config()
        cfg.retrieval["wiki_weight"] = 0.4
        apply_wiki_mode(cfg, True)
        self.assertAlmostEqual(float(cfg.retrieval["wiki_weight"]), 0.4)
        apply_wiki_mode(cfg, False)
        self.assertAlmostEqual(float(cfg.retrieval["wiki_weight"]), 0.4)


class TestWikiModeRetrieve(unittest.TestCase):
    def test_default_retrieve_skips_wiki_channel(self):
        dense = [RetrievalResult(chunk=CODE, score=0.95, source="dense")]
        off = _retriever([CODE, WIKI], dense, wiki_weight=0.0, wiki_mode=False)
        top, trace = off.retrieve("how does Seed grow?", debug=True)
        self.assertEqual(trace.get("wiki") or [], [])
        self.assertEqual(top[0].chunk.id, "code")
        self.assertFalse(any(is_wiki_chunk(r.chunk) and r.source == "wiki" for r in top))

    def test_wiki_mode_retrieves_wiki_docs(self):
        dense = [RetrievalResult(chunk=CODE, score=0.95, source="dense")]
        on = _retriever([CODE, WIKI], dense, wiki_mode=True)
        top, trace = on.retrieve("how does Seed grow?", debug=True)
        self.assertTrue(trace.get("wiki"))
        self.assertTrue(any(is_wiki_chunk(r.chunk) for r in trace["wiki"]))
        self.assertTrue(any(is_wiki_chunk(r.chunk) for r in top))
        self.assertEqual(top[0].chunk.id, "wiki")

    def test_wiki_mode_falls_back_to_code_when_wiki_sparse(self):
        lonely = _chunk(
            "unrelated",
            "office coffee ritual",
            "knowledge/wiki/coffee.md",
            kind="wiki",
        )
        dense = [RetrievalResult(chunk=CODE, score=0.95, source="dense")]
        on = _retriever([CODE, lonely], dense, wiki_mode=True)
        top, trace = on.retrieve("how does Seed grow?", debug=True)
        self.assertFalse(any(is_wiki_chunk(r.chunk) for r in (trace.get("wiki") or [])))
        self.assertEqual(top[0].chunk.id, "code")


class TestWikiModePackAndCite(unittest.TestCase):
    def test_default_system_prompt_unchanged(self):
        from harness.session import CITATION_INSTRUCTION
        from harness.wiki_chat import WIKI_ANSWER_LINE

        prompt = ContextBuilder(Config()).build_system_prompt()
        self.assertIn(CITATION_INSTRUCTION, prompt)
        self.assertIn("`path:symbol`", prompt)
        self.assertNotIn(WIKI_ANSWER_LINE, prompt)
        self.assertNotIn("Answer from the knowledge wiki first", prompt)

    def test_wiki_mode_prompt_cites_wiki_and_path_symbol(self):
        from harness.wiki_chat import WIKI_ANSWER_LINE, apply_wiki_mode

        cfg = Config()
        apply_wiki_mode(cfg, True)
        prompt = ContextBuilder(cfg).build_system_prompt()
        self.assertIn(WIKI_ANSWER_LINE, prompt)
        self.assertIn("`knowledge/wiki/<page>`", prompt)
        self.assertIn("`path:symbol`", prompt)
        self.assertIn("Answer from the knowledge wiki first", prompt)

    def test_wiki_mode_packs_wiki_pages_before_code(self):
        from harness.wiki_chat import apply_wiki_mode

        early = _chunk(
            "early",
            "def grow(): return 1",
            "aaa/mod.py",
            name="grow",
            etype=EntityType.FUNCTION,
        )
        cfg = Config()
        apply_wiki_mode(cfg, True)
        builder = ContextBuilder(cfg)
        wiki_hit = RetrievalResult(chunk=WIKI, score=0.4, source="wiki")
        code_hit = RetrievalResult(chunk=early, score=0.9, source="dense")
        report = builder.build_context_report("how does Seed grow?", [code_hit, wiki_hit])
        self.assertIn("knowledge/wiki/pkg.md", report.context)
        self.assertIn("`pkg/mod.py:grow`", report.context)
        wiki_at = report.context.index("knowledge/wiki/pkg.md")
        code_at = report.context.index("aaa/mod.py")
        self.assertLess(wiki_at, code_at)
        self.assertIn("knowledge/wiki/pkg.md", report.packed_paths)

    def test_default_pack_does_not_reorder_for_wiki(self):
        early = _chunk(
            "early",
            "def grow(): return 1",
            "aaa/mod.py",
            name="grow",
            etype=EntityType.FUNCTION,
        )
        builder = ContextBuilder(Config())
        wiki_hit = RetrievalResult(chunk=WIKI, score=0.4, source="wiki")
        code_hit = RetrievalResult(chunk=early, score=0.9, source="dense")
        report = builder.build_context_report("how does Seed grow?", [code_hit, wiki_hit])
        self.assertIn("aaa/mod.py", report.context)
        # Default file grouping stays alphabetical: aaa/ before knowledge/
        code_at = report.context.index("aaa/mod.py")
        wiki_at = report.context.index("knowledge/wiki/pkg.md")
        self.assertLess(code_at, wiki_at)


class TestWikiCcrExpand(unittest.TestCase):
    def test_extracts_path_symbol_and_expands_linked_source(self):
        from harness.wiki_chat import linked_expand_ids, seed_ccr_cache

        code_full = _chunk(
            "func:pkg/mod.py:grow",
            "def grow():\n    return Seed().sprout()\n",
            "pkg/mod.py",
            name="grow",
            etype=EntityType.FUNCTION,
        )
        ids = linked_expand_ids(
            [WIKI.content],
            [code_full, WIKI],
            packed_ids=["wiki"],
        )
        self.assertEqual(ids, ["func:pkg/mod.py:grow"])

        builder = ContextBuilder(Config())
        seed_ccr_cache(builder, [code_full])
        packed = "### wiki  knowledge/wiki/pkg.md\nPackage pkg. See `pkg/mod.py:grow`.\n"
        expanded = builder.expand_into_context(packed, ids)
        self.assertIn("retrieve_chunk func:pkg/mod.py:grow", expanded)
        self.assertIn("return Seed().sprout()", expanded)


class TestWikiSlashAndCli(unittest.TestCase):
    def test_slash_toggles_and_keeps_page_show(self):
        from harness.session import Session, handle_slash, parse_slash

        session = Session(repo=".")
        self.assertFalse(session.wiki_mode)

        toggle = handle_slash(session, parse_slash("/wiki"))
        self.assertEqual(toggle.kind, "wiki")
        self.assertTrue(toggle.wiki_mode)
        self.assertTrue(session.wiki_mode)
        self.assertIsNone(toggle.wiki_page)
        self.assertIn("wiki", toggle.message.lower())

        off = handle_slash(session, parse_slash("/wiki off"))
        self.assertFalse(off.wiki_mode)
        self.assertFalse(session.wiki_mode)

        on = handle_slash(session, parse_slash("/wiki on"))
        self.assertTrue(on.wiki_mode)
        self.assertTrue(session.wiki_mode)

        page = handle_slash(session, parse_slash("/wiki architecture"))
        self.assertEqual(page.kind, "wiki")
        self.assertEqual(page.wiki_page, "architecture")
        self.assertIsNone(page.wiki_mode)

        help_out = handle_slash(session, parse_slash("/help"))
        self.assertIn("/wiki", help_out.message)
        self.assertIn("on|off", help_out.message)

    def test_cli_exposes_wiki_flag_on_chat_and_query(self):
        for command in ("chat", "query"):
            run = subprocess.run(
                [sys.executable, "main.py", command, "--help"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("--wiki", run.stdout)

    def test_cli_help_does_not_require_network(self):
        run = subprocess.run(
            [sys.executable, "main.py", "chat", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(run.returncode, 0)
        self.assertNotIn("http", (run.stdout + run.stderr).lower())


class TestWikiModeKnowledgePrefix(unittest.TestCase):
    def test_wiki_mode_can_prefix_vault_when_index_sparse(self):
        from harness.wiki_chat import apply_wiki_mode
        from pathlib import Path

        wiki_page = """\
---
type: WikiPage
title: Seed grow
okf_version: "0.2"
---

# Seed

See `pkg/mod.py:grow`.
"""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "knowledge" / "wiki"
            vault.mkdir(parents=True)
            (vault / "pkg.md").write_text(wiki_page, encoding="utf-8")
            cfg = Config()
            cfg.repo_path = tmp
            apply_wiki_mode(cfg, True)
            builder = ContextBuilder(cfg)
            report = builder.build_context_report(
                "how does Seed grow?",
                [RetrievalResult(chunk=CODE, score=0.9, source="dense")],
            )
            self.assertIn("`knowledge/wiki/pkg.md`", report.context)
            self.assertIn("`pkg/mod.py:grow`", report.context)


if __name__ == "__main__":
    unittest.main()
