"""Tests for full-vault OKF export/import (wiki + memory + gloss)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or "."

WIKI_PAGE = """\
---
type: WikiPage
title: Architecture
description: Fixture wiki page
generated: true
verified: false
tags:
- wiki
- architecture
okf_version: "0.2"
x_other: keep-me
x_memanto:
  type: note
x_codeharness:
  kind: wiki
  repo: fixture
---

# Architecture

See `harness/okf.py:export_vault`.
"""

MEMORY_PAGE = """\
---
type: Decision
title: Keep Chroma
description: Until TurboVec gates pass.
generated: false
verified: human
timestamp: 2026-09-24T00:00:00Z
id: mem/20260924-keep-chroma
status: active
okf_version: "0.2"
x_other: mem-keep
x_memanto:
  type: decision
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

GLOSS_PAGE = """\
---
entity: class:harness/okf.py:WikiPage
x_other: gloss-keep
---

# WikiPage

Gloss note attached to the OKF record type.
"""

LOCAL_GLOSS = """\
---
entity: class:harness/memory.py:MemoryStore
---

# MemoryStore

Local overlay gloss under `.code-harness/gloss`.
"""


def _write_fixture_vault(root: Path) -> None:
    wiki = root / "knowledge" / "wiki"
    memory = root / "knowledge" / "memory" / "decision"
    gloss = root / "knowledge" / "gloss"
    local_gloss = root / ".code-harness" / "gloss"
    wiki.mkdir(parents=True)
    memory.mkdir(parents=True)
    gloss.mkdir(parents=True)
    local_gloss.mkdir(parents=True)
    (wiki / "architecture.md").write_text(WIKI_PAGE, encoding="utf-8")
    (memory / "2026-09-24-keep-chroma.md").write_text(MEMORY_PAGE, encoding="utf-8")
    (gloss / "wiki-page.md").write_text(GLOSS_PAGE, encoding="utf-8")
    (local_gloss / "memory-store.md").write_text(LOCAL_GLOSS, encoding="utf-8")


class TestEmptyVaultFailSoft(unittest.TestCase):
    def test_empty_vault_export_writes_manifest_and_does_not_raise(self):
        from harness.okf import export_vault, read_vault_manifest

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "empty"
            bundle = Path(tmp) / "bundle"
            repo.mkdir()
            manifest = export_vault(str(repo), str(bundle))
            self.assertEqual(manifest.counts["total"], 0)
            self.assertEqual(manifest.counts["wiki"], 0)
            self.assertEqual(manifest.counts["memory"], 0)
            self.assertEqual(manifest.counts["gloss"], 0)
            self.assertTrue(manifest.generated)
            self.assertEqual(manifest.okf_version, "0.2")
            self.assertTrue((bundle / "okf-manifest.yaml").is_file())
            loaded = read_vault_manifest(str(bundle))
            self.assertEqual(loaded.counts["total"], 0)

    def test_empty_bundle_import_fails_soft(self):
        from harness.okf import export_vault, import_vault

        with tempfile.TemporaryDirectory() as tmp:
            empty_repo = Path(tmp) / "empty"
            dest = Path(tmp) / "dest"
            bundle = Path(tmp) / "bundle"
            empty_repo.mkdir()
            dest.mkdir()
            export_vault(str(empty_repo), str(bundle))
            imported = import_vault(str(bundle), str(dest))
            self.assertEqual(imported.counts["total"], 0)
            self.assertFalse((dest / "knowledge").exists())

    def test_missing_bundle_import_is_a_clear_error(self):
        from harness.okf import VaultError, import_vault

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(VaultError):
                import_vault(os.path.join(tmp, "missing"), tmp)


class TestUnknownKeySurvival(unittest.TestCase):
    def test_unknown_frontmatter_keys_survive_export_import(self):
        from harness.okf import export_vault, import_vault, parse_okf_markdown

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            dst = Path(tmp) / "dst"
            bundle = Path(tmp) / "bundle"
            src.mkdir()
            dst.mkdir()
            _write_fixture_vault(src)

            exported = export_vault(str(src), str(bundle))
            self.assertGreaterEqual(exported.counts["total"], 3)
            imported = import_vault(str(bundle), str(dst))
            self.assertEqual(imported.counts["total"], exported.counts["total"])

            wiki = parse_okf_markdown(
                (dst / "knowledge" / "wiki" / "architecture.md").read_text(encoding="utf-8")
            )
            self.assertEqual(wiki.type, "WikiPage")
            self.assertEqual(wiki.extras.get("x_other"), "keep-me")
            self.assertEqual(wiki.extras.get("x_memanto"), {"type": "note"})

            memory = parse_okf_markdown(
                (dst / "knowledge" / "memory" / "decision" / "2026-09-24-keep-chroma.md").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(memory.type, "Decision")
            self.assertEqual(memory.extras.get("x_other"), "mem-keep")
            self.assertEqual(memory.extras.get("x_memanto"), {"type": "decision"})
            self.assertEqual(memory.id, "mem/20260924-keep-chroma")

            gloss = parse_okf_markdown(
                (dst / "knowledge" / "gloss" / "wiki-page.md").read_text(encoding="utf-8")
            )
            self.assertEqual(gloss.extras.get("entity"), "class:harness/okf.py:WikiPage")
            self.assertEqual(gloss.extras.get("x_other"), "gloss-keep")


class TestExportImportIdempotence(unittest.TestCase):
    def test_fixture_vault_round_trips_paths_and_bodies(self):
        from harness.okf import export_vault, import_vault, load_knowledge_docs

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            dst = Path(tmp) / "dst"
            bundle = Path(tmp) / "bundle"
            again = Path(tmp) / "bundle2"
            src.mkdir()
            dst.mkdir()
            _write_fixture_vault(src)

            first = export_vault(str(src), str(bundle))
            self.assertEqual(first.counts["wiki"], 1)
            self.assertEqual(first.counts["memory"], 1)
            self.assertGreaterEqual(first.counts["gloss"], 2)
            self.assertIn("okf-manifest.yaml", os.listdir(bundle))
            self.assertTrue((bundle / "knowledge" / "wiki" / "architecture.md").is_file())
            self.assertTrue((bundle / ".code-harness" / "gloss" / "memory-store.md").is_file())

            import_vault(str(bundle), str(dst))
            expected = {
                "knowledge/wiki/architecture.md",
                "knowledge/memory/decision/2026-09-24-keep-chroma.md",
                "knowledge/gloss/wiki-page.md",
                ".code-harness/gloss/memory-store.md",
            }
            for rel in expected:
                left = (src / rel).read_text(encoding="utf-8")
                right = (dst / rel).read_text(encoding="utf-8")
                self.assertEqual(right, left, rel)

            second = export_vault(str(dst), str(again))
            self.assertEqual(second.counts, first.counts)
            for rel in expected:
                self.assertEqual(
                    (bundle / rel).read_text(encoding="utf-8"),
                    (again / rel).read_text(encoding="utf-8"),
                    rel,
                )

            docs = load_knowledge_docs(str(src))
            kinds = {doc.kind for doc in docs}
            paths = {doc.rel_path.replace("\\", "/") for doc in docs}
            self.assertEqual(kinds, {"wiki", "memory", "gloss"})
            self.assertTrue(paths >= expected)
            wiki_doc = next(d for d in docs if d.kind == "wiki")
            self.assertEqual(wiki_doc.page.type, "WikiPage")
            self.assertIn("export_vault", wiki_doc.text)


class TestKnowledgeCli(unittest.TestCase):
    def test_knowledge_help_lists_export_import(self):
        run = subprocess.run(
            [sys.executable, "main.py", "knowledge", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("export", run.stdout)
        self.assertIn("import", run.stdout)

    def test_cli_export_import_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            dst = Path(tmp) / "dst"
            bundle = Path(tmp) / "okf-out"
            src.mkdir()
            dst.mkdir()
            _write_fixture_vault(src)

            exported = subprocess.run(
                [
                    sys.executable,
                    os.path.join(ROOT, "main.py"),
                    "knowledge",
                    "export",
                    str(src),
                    "--out",
                    str(bundle),
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(exported.returncode, 0, exported.stdout + exported.stderr)
            self.assertTrue((bundle / "okf-manifest.yaml").is_file())

            imported = subprocess.run(
                [
                    sys.executable,
                    os.path.join(ROOT, "main.py"),
                    "knowledge",
                    "import",
                    str(bundle),
                    "--repo",
                    str(dst),
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
            self.assertIn("Imported", imported.stdout)
            self.assertEqual(
                (dst / "knowledge" / "wiki" / "architecture.md").read_text(encoding="utf-8"),
                WIKI_PAGE,
            )

    def test_cli_empty_vault_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "empty"
            bundle = Path(tmp) / "bundle"
            repo.mkdir()
            run = subprocess.run(
                [
                    sys.executable,
                    os.path.join(ROOT, "main.py"),
                    "knowledge",
                    "export",
                    str(repo),
                    "--out",
                    str(bundle),
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertTrue((bundle / "okf-manifest.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
