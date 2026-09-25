"""Tests for OKF WikiPage emit, path:symbol cites, and wiki generate."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness.config import Config
from harness.knowledge_graph import KnowledgeGraph
from harness.models import CodeEntity, EntityType
from harness.okf import (
    OKF_VERSION,
    WIKI_PAGE_TYPE,
    WikiPage,
    cite_from_entity_id,
    cite_path_symbol,
    dump_okf_markdown,
    parse_okf_markdown,
)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."


def _entity(eid, name, etype, path, source="", metadata=None, start=1, end=10, docstring=None):
    return CodeEntity(
        id=eid,
        name=name,
        type=etype,
        file_path=path,
        start_line=start,
        end_line=end,
        source_code=source,
        docstring=docstring,
        metadata=metadata or {},
    )


def _file(path, source):
    return _entity(f"file:{path}", os.path.basename(path), EntityType.FILE, path, source)


def _func(path, name, source="", etype=EntityType.FUNCTION, docstring=None):
    prefix = "func" if etype == EntityType.FUNCTION else "method"
    return _entity(
        f"{prefix}:{path}:{name}", name, etype, path, source, docstring=docstring,
    )


def _class(path, name, source="", methods=None, docstring=None):
    return _entity(
        f"class:{path}:{name}",
        name,
        EntityType.CLASS,
        path,
        source,
        metadata={"methods": methods or []},
        docstring=docstring,
    )


def _kg(entities, repo_name="fixture", persist_path=None, repo_path=""):
    cfg = Config()
    cfg.knowledge_graph["enrich"] = True
    if persist_path:
        cfg.knowledge_graph["persist_path"] = persist_path
    if repo_path:
        cfg.repo_path = repo_path
    kg = KnowledgeGraph(cfg, repo_name=repo_name)
    kg.build(entities)
    return kg


class TestCitePathSymbol(unittest.TestCase):
    def test_class_and_method_golden_format(self):
        self.assertEqual(
            cite_path_symbol("harness/context_builder.py", "ContextBuilder.build_context"),
            "harness/context_builder.py:ContextBuilder.build_context",
        )
        self.assertEqual(
            cite_from_entity_id("class:harness/chunker.py:CodeChunker"),
            "harness/chunker.py:CodeChunker",
        )
        self.assertEqual(
            cite_from_entity_id("func:harness/chunker.py:CodeChunker.chunk_entities"),
            "harness/chunker.py:CodeChunker.chunk_entities",
        )
        self.assertEqual(
            cite_from_entity_id("endpoint:main.py:index"),
            "main.py:index",
        )

    def test_file_entity_is_path_only(self):
        self.assertEqual(cite_from_entity_id("file:harness/chunker.py"), "harness/chunker.py")
        self.assertEqual(cite_path_symbol("harness/chunker.py", ""), "harness/chunker.py")

    def test_windows_separators_normalized(self):
        self.assertEqual(
            cite_from_entity_id(r"class:harness\chunker.py:CodeChunker"),
            "harness/chunker.py:CodeChunker",
        )


class TestOkfWikiPage(unittest.TestCase):
    def test_required_type_and_version(self):
        page = WikiPage(title="harness", body="# harness\n")
        text = dump_okf_markdown(page)
        parsed = parse_okf_markdown(text)
        self.assertEqual(parsed.type, WIKI_PAGE_TYPE)
        self.assertEqual(parsed.okf_version, OKF_VERSION)
        self.assertTrue(parsed.generated)
        mapping = parsed.to_okf_dict()
        self.assertEqual(mapping["type"], "WikiPage")
        self.assertEqual(mapping["okf_version"], "0.2")
        self.assertIn("title", mapping)
        self.assertIn("x_codeharness", mapping)

    def test_unknown_key_round_trip(self):
        raw = (
            "---\n"
            "type: WikiPage\n"
            "title: Sample\n"
            "okf_version: \"0.2\"\n"
            "x_other: keep-me\n"
            "x_memanto:\n"
            "  type: note\n"
            "---\n"
            "\n"
            "# Sample\n"
            "body stays\n"
        )
        parsed = parse_okf_markdown(raw)
        again = parse_okf_markdown(dump_okf_markdown(parsed))
        self.assertEqual(again.extras.get("x_other"), "keep-me")
        self.assertEqual(again.extras.get("x_memanto"), {"type": "note"})
        self.assertIn("body stays", again.body)


class TestWikiGenerate(unittest.TestCase):
    def _tiny_repo(self, tmp):
        src = Path(tmp) / "pkg" / "mod.py"
        src.parent.mkdir(parents=True)
        src.write_text(
            'class Seed:\n'
            '    """Assemble packed context."""\n'
            "    def grow(self):\n"
            '        """Expand a neighbor."""\n'
            "        return 1\n",
            encoding="utf-8",
        )
        test_src = (
            "from pkg.mod import Seed\n"
            "def test_grow():\n"
            "    Seed().grow()\n"
        )
        tpath = Path(tmp) / "tests" / "test_mod.py"
        tpath.parent.mkdir(parents=True)
        tpath.write_text(test_src, encoding="utf-8")
        api = Path(tmp) / "api.py"
        api.write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            '@app.get("/health")\n'
            "def health():\n"
            "    return {\"ok\": True}\n",
            encoding="utf-8",
        )
        gloss_dir = Path(tmp) / "knowledge" / "gloss"
        gloss_dir.mkdir(parents=True)
        (gloss_dir / "seed.md").write_text(
            "---\n"
            "entity: class:pkg/mod.py:Seed\n"
            "---\n"
            "# Seed notes\n"
            "Tribal note about Seed.\n",
            encoding="utf-8",
        )
        seed_src = src.read_text(encoding="utf-8")
        entities = [
            _file("pkg/mod.py", seed_src),
            _class("pkg/mod.py", "Seed", seed_src, methods=["grow"], docstring="Assemble packed context."),
            _func("pkg/mod.py", "Seed.grow", "def grow(self):\n    return 1\n", etype=EntityType.METHOD,
                  docstring="Expand a neighbor."),
            _file("tests/test_mod.py", test_src),
            _func("tests/test_mod.py", "test_grow", test_src),
            _file("api.py", api.read_text(encoding="utf-8")),
            _func("api.py", "health", api.read_text(encoding="utf-8")),
        ]
        return _kg(entities, repo_path=tmp)

    def test_missing_graph_raises_clear_error(self):
        from harness.wiki import MissingGraphError, generate_from_repo

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.repo_path = tmp
            cfg.knowledge_graph["persist_path"] = os.path.join(tmp, "graph.json")
            with self.assertRaises(MissingGraphError) as ctx:
                generate_from_repo(tmp, config=cfg, repo_name="empty")
            msg = str(ctx.exception).lower()
            self.assertIn("graph", msg)
            self.assertIn("index", msg)

    def test_writes_pages_with_cites_mermaid_endpoints_tests_gloss(self):
        from harness.wiki import generate_wiki, list_pages, show_page

        with tempfile.TemporaryDirectory() as tmp:
            kg = self._tiny_repo(tmp)
            out = Path(tmp) / "knowledge" / "wiki"
            result = generate_wiki(kg, repo_path=tmp, out_dir=out, repo_name="fixture")
            self.assertGreaterEqual(result.page_count, 2)
            names = {p.name for p in out.glob("*.md")}
            self.assertIn("architecture.md", names)
            self.assertTrue({"pkg.md", "api.md"} & names or "pkg.md" in names)

            arch = (out / "architecture.md").read_text(encoding="utf-8")
            self.assertIn("type: WikiPage", arch)
            self.assertIn('okf_version: "0.2"', arch)
            self.assertIn("```mermaid", arch)
            self.assertIn("graph TD", arch)

            pkg = (out / "pkg.md").read_text(encoding="utf-8")
            self.assertIn("`pkg/mod.py:Seed`", pkg)
            self.assertIn("](../../pkg/mod.py)", pkg)
            self.assertNotIn("/workspace/", pkg)
            self.assertIn("Assemble packed context", pkg)
            self.assertIn("```mermaid", pkg)
            self.assertRegex(pkg, r"test_mod\.py:test_grow|tests/test_mod\.py:test_grow")
            self.assertIn("Seed notes", pkg)

            all_text = "\n".join(p.read_text(encoding="utf-8") for p in out.glob("*.md"))
            self.assertTrue(
                "`api.py:health`" in all_text or "`api.py:/health`" in all_text,
                all_text,
            )

            listed = list_pages(out)
            titles = {item["title"] for item in listed}
            self.assertTrue(any("architecture" in t.lower() or t == "architecture" for t in titles) or listed)
            shown = show_page(out, "architecture")
            self.assertIn("WikiPage", shown)

    def test_hash_stable_regen(self):
        from harness.wiki import generate_wiki

        with tempfile.TemporaryDirectory() as tmp:
            kg = self._tiny_repo(tmp)
            out = Path(tmp) / "knowledge" / "wiki"
            generate_wiki(kg, repo_path=tmp, out_dir=out, repo_name="fixture")
            first = {p.name: p.read_bytes() for p in sorted(out.glob("*.md"))}
            generate_wiki(kg, repo_path=tmp, out_dir=out, repo_name="fixture")
            second = {p.name: p.read_bytes() for p in sorted(out.glob("*.md"))}
            self.assertEqual(first, second)

    def test_does_not_write_into_docs(self):
        from harness.wiki import generate_wiki

        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp) / ".docs"
            docs.mkdir()
            marker = docs / "architecture.md"
            marker.write_text("HUMAN DOC\n", encoding="utf-8")
            kg = self._tiny_repo(tmp)
            out = Path(tmp) / "knowledge" / "wiki"
            generate_wiki(kg, repo_path=tmp, out_dir=out, repo_name="fixture")
            self.assertEqual(marker.read_text(encoding="utf-8"), "HUMAN DOC\n")
            self.assertTrue((out / "architecture.md").is_file())


class TestWikiCli(unittest.TestCase):
    def test_wiki_help_lists_subcommands(self):
        run = subprocess.run(
            [sys.executable, "main.py", "wiki", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("generate", run.stdout)
        self.assertIn("list", run.stdout)
        self.assertIn("show", run.stdout)

    def test_generate_missing_graph_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = subprocess.run(
                [sys.executable, "main.py", "wiki", "generate", tmp],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertNotEqual(run.returncode, 0)
            combined = (run.stdout + run.stderr).lower()
            self.assertIn("graph", combined)
            self.assertIn("index", combined)


if __name__ == "__main__":
    unittest.main()
