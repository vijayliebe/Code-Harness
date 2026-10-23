"""Validate the v2 golden suite: schema, on-disk paths, original-8 subset."""

import os
import unittest

from harness.eval import load_suite


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V2_PATH = os.path.join(REPO_ROOT, ".docs/research/eval/code-harness-v2.fixture.yaml")
V1_PATH = os.path.join(REPO_ROOT, ".docs/research/eval/code-harness.fixture.yaml")
ALLOWED_DIFFICULTY = frozenset({"easy", "medium", "hard"})
ALLOWED_CATEGORY = frozenset(
    {"symbol", "nl", "cross-file", "cli", "docs", "hard", "ticket"}
)
ENTITY_PREFIX = frozenset({"class", "func", "method", "file", "doc", "struct"})


class TestGoldenV2Suite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures, cls.meta = load_suite(V2_PATH)
        cls.v1_fixtures, _ = load_suite(V1_PATH)

    def test_suite_meta_and_size(self):
        self.assertEqual(self.meta["suite"], "code-harness-v2")
        self.assertEqual(self.meta.get("k"), 10)
        self.assertGreaterEqual(len(self.fixtures), 40)
        self.assertLessEqual(len(self.fixtures), 50)

    def test_schema_and_unique_ids(self):
        ids = []
        for fx in self.fixtures:
            ids.append(fx.id)
            self.assertTrue(fx.id, "fixture id required")
            self.assertTrue(fx.query and fx.query.strip(), f"{fx.id}: empty query")
            self.assertTrue(fx.must_cite_paths, f"{fx.id}: must_cite_paths required")
            self.assertIn(fx.difficulty, ALLOWED_DIFFICULTY, fx.id)
            category = getattr(fx, "category", "") or ""
            if category:
                self.assertIn(category, ALLOWED_CATEGORY, fx.id)
            for gold in fx.relevant_chunk_ids:
                kind = gold.split(":", 1)[0]
                self.assertIn(kind, ENTITY_PREFIX, f"{fx.id}: bad gold id {gold}")
        self.assertEqual(len(ids), len(set(ids)), "duplicate fixture ids")

    def test_must_cite_paths_exist(self):
        missing = []
        for fx in self.fixtures:
            for rel in fx.must_cite_paths:
                abs_path = os.path.join(REPO_ROOT, rel)
                if not os.path.isfile(abs_path):
                    missing.append(f"{fx.id}: {rel}")
        self.assertEqual(missing, [], "labeled paths missing from the repo")

    def test_original_eight_are_subset(self):
        v2_by_id = {fx.id: fx for fx in self.fixtures}
        self.assertEqual(len(self.v1_fixtures), 8)
        for old in self.v1_fixtures:
            self.assertIn(old.id, v2_by_id, f"v1 id {old.id} missing from v2")
            new = v2_by_id[old.id]
            self.assertEqual(old.query, new.query, old.id)
            self.assertEqual(old.relevant_chunk_ids, new.relevant_chunk_ids, old.id)
            self.assertEqual(old.must_cite_paths, new.must_cite_paths, old.id)
            self.assertEqual(old.difficulty, new.difficulty, old.id)

    def test_mix_covers_required_categories(self):
        by_cat = {}
        for fx in self.fixtures:
            cat = getattr(fx, "category", "") or "unset"
            by_cat.setdefault(cat, 0)
            by_cat[cat] += 1
        for needed in ("symbol", "nl", "cross-file", "ticket"):
            self.assertGreaterEqual(by_cat.get(needed, 0), 1, f"missing category {needed}")
        self.assertGreaterEqual(by_cat.get("ticket", 0), 6)
        self.assertLessEqual(by_cat.get("ticket", 0), 8)


if __name__ == "__main__":
    unittest.main()
