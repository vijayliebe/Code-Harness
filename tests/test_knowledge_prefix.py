"""Packer prefix-load of knowledge/** — opt-in, budgeted, redacted."""

import os
import tempfile
import unittest
from pathlib import Path

from harness.config import DEFAULT_CONFIG, Config
from harness.context_builder import ContextBuilder
from harness.models import Chunk, EntityType, RetrievalResult
from harness.okf import estimate_tokens, load_knowledge_docs


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."

# Unique fake secret used only as a fixture. Concatenated so GitHub push
# protection does not treat this test file as a live-secret leak.
FAKE_OPENAI = "sk-proj-" + "TESTFAKESECRETKEY1234567890ABCD"

WIKI_CHROMA = """\
---
type: WikiPage
title: Vector store architecture
description: Why Chroma stays the default embed index.
generated: true
verified: false
tags:
- wiki
- chroma
okf_version: "0.2"
x_codeharness:
  kind: wiki
---

# Vector store

Chroma is the default backend. See `harness/vector_store.py:VectorStore`.
"""

WIKI_COFFEE = """\
---
type: WikiPage
title: Coffee ritual
description: How the office brews coffee.
generated: true
verified: false
tags:
- wiki
- coffee
okf_version: "0.2"
x_codeharness:
  kind: wiki
---

# Coffee

Never put coffee grounds in the vector index.
"""

MEMORY_CHROMA = """\
---
type: Decision
title: Default vector backend stays Chroma
description: Until TurboVec recall gates pass.
generated: false
verified: human
timestamp: 2026-09-24T00:00:00Z
id: mem/20260924-keep-chroma
status: active
okf_version: "0.2"
x_codeharness:
  kind: memory
  memory_kind: decision
  id: mem/20260924-keep-chroma
  status: active
  links:
  - harness/vector_store.py:VectorStore
---

Keep Chroma until TurboVec recall@k passes.
"""

MEMORY_TOMBSTONE = """\
---
type: Decision
title: Use FAISS forever
description: Superseded choice.
generated: false
verified: human
timestamp: 2026-01-01T00:00:00Z
id: mem/20260101-use-faiss
status: superseded
okf_version: "0.2"
x_codeharness:
  kind: memory
  memory_kind: decision
  id: mem/20260101-use-faiss
  status: superseded
---

This old FAISS decision must not be prefix-loaded.
"""

GLOSS_NOTE = """\
---
entity: class:harness/vector_store.py:VectorStore
---

# VectorStore

Gloss note for the store class.
"""


def _chunk(
    path="harness/demo.py",
    content="def big_fn(config):\n    return 1\n",
    name="big_fn",
    cid="func:harness/demo.py:big_fn:abcd1234",
    kind=None,
):
    meta = {}
    if kind:
        meta["kind"] = kind
    return Chunk(
        id=cid,
        content=content,
        entity_id=":".join(cid.split(":")[:3]) if cid.count(":") >= 2 else cid,
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=2,
        docstring="",
        metadata=meta,
    )


def _rr(chunk=None, score=0.9):
    return RetrievalResult(chunk=chunk or _chunk(), score=score, source="test")


def _write_vault(root: Path, *, secret: str = "", extra_wiki: str = ""):
    (root / "knowledge" / "wiki").mkdir(parents=True, exist_ok=True)
    (root / "knowledge" / "memory" / "decision").mkdir(parents=True, exist_ok=True)
    (root / "knowledge" / "gloss").mkdir(parents=True, exist_ok=True)
    wiki = WIKI_CHROMA
    if secret:
        wiki = wiki + f"\nAPI_KEY={secret}\n"
    if extra_wiki:
        wiki = wiki + extra_wiki
    (root / "knowledge" / "wiki" / "vector-store.md").write_text(wiki, encoding="utf-8")
    (root / "knowledge" / "wiki" / "coffee.md").write_text(WIKI_COFFEE, encoding="utf-8")
    (root / "knowledge" / "memory" / "decision" / "2026-09-24-keep-chroma.md").write_text(
        MEMORY_CHROMA, encoding="utf-8"
    )
    (root / "knowledge" / "memory" / "decision" / "2026-01-01-use-faiss.md").write_text(
        MEMORY_TOMBSTONE, encoding="utf-8"
    )
    (root / "knowledge" / "gloss" / "vector-store.md").write_text(GLOSS_NOTE, encoding="utf-8")


def _builder(tmp, **context):
    cfg = dict(DEFAULT_CONFIG["context"])
    cfg.update(context)
    config = Config.from_dict({"repo_path": tmp, "context": cfg})
    config.repo_path = tmp
    return ContextBuilder(config)


class TestKnowledgePrefixConfig(unittest.TestCase):
    def test_default_is_off_with_documented_budget(self):
        ctx = DEFAULT_CONFIG["context"]
        self.assertFalse(ctx["knowledge_prefix"])
        self.assertEqual(ctx["knowledge_token_budget"], 800)


class TestKnowledgeRanking(unittest.TestCase):
    def test_query_prefers_title_and_path_over_unrelated(self):
        from harness.okf import rank_knowledge_docs

        with tempfile.TemporaryDirectory() as tmp:
            _write_vault(Path(tmp))
            docs = load_knowledge_docs(tmp)
            ranked = rank_knowledge_docs(docs, query="why is Chroma the default vector store?")
            paths = [d.rel_path.replace("\\", "/") for d in ranked]
            chroma = next(i for i, p in enumerate(paths) if p.endswith("wiki/vector-store.md"))
            coffee = next(i for i, p in enumerate(paths) if p.endswith("wiki/coffee.md"))
            self.assertLess(chroma, coffee)


class TestKnowledgePrefixPacker(unittest.TestCase):
    def test_disabled_by_default_excludes_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_vault(Path(tmp))
            builder = _builder(tmp)
            report = builder.build_context_report("why chroma?", [_rr()])
            self.assertNotIn("Knowledge vault", report.context)
            self.assertNotIn("knowledge/wiki/vector-store.md", report.context)
            self.assertNotIn("Coffee ritual", report.context)
            self.assertIn("harness/demo.py", report.context)

    def test_enabled_prefixes_ranked_docs_with_cites(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_vault(Path(tmp))
            Path(tmp, "AGENTS.md").write_text("Be careful.\n", encoding="utf-8")
            builder = _builder(tmp, knowledge_prefix=True)
            report = builder.build_context_report(
                "why is Chroma the default vector store?", [_rr()]
            )
            self.assertIn("## Knowledge vault", report.context)
            self.assertIn("`knowledge/wiki/vector-store.md`", report.context)
            self.assertIn("**Decision**", report.context)
            self.assertIn("`harness/vector_store.py:VectorStore`", report.context)
            self.assertIn("harness/demo.py", report.context)
            docs_at = report.context.index("AGENTS.md")
            vault_at = report.context.index("Knowledge vault")
            hits_at = report.context.index("harness/demo.py")
            self.assertLess(docs_at, vault_at)
            self.assertLess(vault_at, hits_at)
            self.assertNotIn("Use FAISS forever", report.context)
            self.assertNotIn("This old FAISS decision", report.context)

    def test_budget_cap_keeps_code_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            padding = "chroma token padding " * 400
            _write_vault(Path(tmp), extra_wiki="\n" + padding + "\n")
            builder = _builder(tmp, knowledge_prefix=True, knowledge_token_budget=40)
            report = builder.build_context_report("chroma vector store", [_rr()])
            self.assertIn("Knowledge vault", report.context)
            self.assertIn("harness/demo.py", report.context)
            vault_at = report.context.index("Knowledge vault")
            file_at = report.context.index("### File:")
            self.assertLess(vault_at, file_at)
            knowledge_blob = report.context[vault_at:file_at]
            # Header + first page may slightly exceed if one item is truncated;
            # the dedicated budget must still be far smaller than the raw wiki.
            self.assertLessEqual(estimate_tokens(knowledge_blob), 80)
            self.assertLess(len(knowledge_blob), len(padding))

    def test_redaction_still_runs_on_vault_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_vault(Path(tmp), secret=FAKE_OPENAI)
            config = Config.from_dict(
                {
                    "repo_path": tmp,
                    "context": {"knowledge_prefix": True},
                    "redaction": {
                        "enabled": True,
                        "audit": True,
                        "audit_path": os.path.join(tmp, "audit.jsonl"),
                    },
                }
            )
            config.repo_path = tmp
            builder = ContextBuilder(config)
            report = builder.build_context_report("chroma secrets", [_rr()])
            self.assertIn("Knowledge vault", report.context)
            self.assertNotIn(FAKE_OPENAI, report.context)
            self.assertIn("REDACTED", report.context)
            self.assertGreater(report.redaction_count, 0)

    def test_dedupes_wiki_rrf_hit_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_vault(Path(tmp))
            wiki_chunk = _chunk(
                path="knowledge/wiki/vector-store.md",
                content="living wiki already retrieved via RRF",
                name="vector-store",
                cid="doc:knowledge/wiki/vector-store.md:page",
                kind="wiki",
            )
            builder = _builder(tmp, knowledge_prefix=True)
            report = builder.build_context_report("chroma vector store", [_rr(wiki_chunk)])
            self.assertIn("## Knowledge vault", report.context)
            self.assertEqual(report.context.count("knowledge/wiki/vector-store.md"), 1)
            self.assertIn("living wiki already retrieved via RRF", report.context)
            self.assertNotIn("Chroma is the default backend", report.context)
            self.assertIn("**Decision**", report.context)

    def test_memory_brief_does_not_double_stuff(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_vault(Path(tmp))
            builder = _builder(
                tmp,
                knowledge_prefix=True,
                include_memory_brief=True,
            )
            report = builder.build_context_report("why chroma?", [_rr()])
            self.assertIn("Project memory brief", report.context)
            self.assertIn("Knowledge vault", report.context)
            self.assertEqual(report.context.count("Default vector backend stays Chroma"), 1)
            self.assertIn("`knowledge/wiki/vector-store.md`", report.context)


class TestKnowledgePrefixHelpers(unittest.TestCase):
    def test_pack_helper_respects_occupied_paths_and_budget(self):
        from harness.okf import pack_knowledge_prefix

        with tempfile.TemporaryDirectory() as tmp:
            _write_vault(Path(tmp))
            docs = load_knowledge_docs(tmp)
            packed = pack_knowledge_prefix(
                docs,
                query="chroma",
                max_tokens=800,
                occupied_paths=["knowledge/wiki/vector-store.md"],
            )
            self.assertIn("## Knowledge vault", packed)
            self.assertNotIn("knowledge/wiki/vector-store.md", packed)
            self.assertIn("**Decision**", packed)
            tiny = pack_knowledge_prefix(docs, query="chroma", max_tokens=12)
            self.assertTrue(tiny)
            self.assertLessEqual(estimate_tokens(tiny), 20)


if __name__ == "__main__":
    unittest.main()
