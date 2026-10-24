"""Unit tests for retrieval eval metrics (no index / network required)."""

import math
import unittest

from harness.metrics import (
    FAILURE_LABELS,
    citation_path_hit_rate,
    classify_failures,
    gold_ids_hit,
    gold_ids_hit_from_ids,
    is_source_backed,
    is_synthetic_chunk,
    match_gold_chunk,
    match_gold_id,
    ndcg_at_k,
    normalize_path,
    paths_in_order,
    recall_at_k,
)


class TestRecallAtK(unittest.TestCase):
    def test_perfect_recall(self):
        self.assertEqual(recall_at_k(["a", "b", "c"], ["a", "b"], k=2), 1.0)

    def test_partial_recall(self):
        self.assertEqual(recall_at_k(["a", "x", "y"], ["a", "b"], k=3), 0.5)

    def test_zero_when_none_retrieved(self):
        self.assertEqual(recall_at_k(["x", "y"], ["a"], k=2), 0.0)

    def test_unlabeled_returns_none(self):
        self.assertIsNone(recall_at_k(["a"], [], k=5))

    def test_respects_k(self):
        self.assertEqual(recall_at_k(["x", "a"], ["a"], k=1), 0.0)
        self.assertEqual(recall_at_k(["x", "a"], ["a"], k=2), 1.0)


class TestNdcgAtK(unittest.TestCase):
    def test_perfect_ranking(self):
        self.assertAlmostEqual(ndcg_at_k(["a", "b"], ["a", "b"], k=2), 1.0)

    def test_unlabeled_returns_none(self):
        self.assertIsNone(ndcg_at_k(["a"], [], k=5))

    def test_swapped_order_is_less_than_perfect(self):
        swapped = ndcg_at_k(["x", "a"], ["a"], k=2)
        perfect = ndcg_at_k(["a", "x"], ["a"], k=2)
        self.assertGreater(perfect, swapped)
        self.assertGreater(swapped, 0.0)

    def test_ideal_dcg_formula(self):
        # one relevant item at rank 2: 1/log2(3)
        expected = (1.0 / math.log2(3)) / (1.0 / math.log2(2))
        self.assertAlmostEqual(ndcg_at_k(["x", "a"], ["a"], k=2), expected)

    def test_duplicate_hits_do_not_exceed_one(self):
        score = ndcg_at_k(["a", "a", "a"], ["a", "b"], k=3)
        self.assertIsNotNone(score)
        self.assertLessEqual(score, 1.0)


class TestCitationPathHit(unittest.TestCase):
    def test_all_paths_present(self):
        self.assertEqual(
            citation_path_hit_rate(
                ["harness/retriever.py", "harness/context_builder.py"],
                ["harness/retriever.py"],
            ),
            1.0,
        )

    def test_partial_and_none(self):
        self.assertEqual(
            citation_path_hit_rate(
                ["harness/retriever.py"],
                ["harness/retriever.py", "main.py"],
            ),
            0.5,
        )
        self.assertEqual(citation_path_hit_rate(["other.py"], ["main.py"]), 0.0)

    def test_normalizes_separators_and_dot_slash(self):
        self.assertEqual(
            citation_path_hit_rate(["./harness\\retriever.py"], ["harness/retriever.py"]),
            1.0,
        )

    def test_empty_must_cite_is_none(self):
        self.assertIsNone(citation_path_hit_rate(["a.py"], []))


class TestNormalizeAndMatch(unittest.TestCase):
    def test_normalize_path(self):
        self.assertEqual(normalize_path("./harness\\foo.py"), "harness/foo.py")

    def test_match_gold_id_exact_and_prefix(self):
        gold = "class:harness/context_builder.py:ContextBuilder"
        self.assertTrue(match_gold_id(gold, gold, gold))
        self.assertTrue(
            match_gold_id(
                f"{gold}:ab12cd34",
                gold,
                gold,
            )
        )
        self.assertFalse(
            match_gold_id(
                "class:harness/retriever.py:Retriever:ffff",
                "class:harness/retriever.py:Retriever",
                gold,
            )
        )

    def test_match_func_gold_to_treesitter_method(self):
        self.assertTrue(
            match_gold_id(
                "method:harness/retriever.py:Retriever.index_chunks:74759c2d",
                "method:harness/retriever.py:Retriever.index_chunks",
                "func:harness/retriever.py:index_chunks",
            )
        )
        self.assertFalse(
            match_gold_id(
                "method:harness/retriever.py:Retriever.retrieve:aaaa",
                "method:harness/retriever.py:Retriever.retrieve",
                "func:harness/retriever.py:index_chunks",
            )
        )

    def test_gold_ids_hit_and_paths_in_order(self):
        class _C:
            def __init__(self, cid, eid, path):
                self.id = cid
                self.entity_id = eid
                self.file_path = path
                self.start_line = 1
                self.end_line = 4
                self.metadata = {}

        chunks = [
            _C("class:a.py:Foo:1", "class:a.py:Foo", "a.py"),
            _C("func:b.py:bar:2", "func:b.py:bar", "b.py"),
            _C("func:a.py:baz:3", "func:a.py:baz", "./a.py"),
        ]
        self.assertEqual(
            gold_ids_hit(chunks, ["class:a.py:Foo", "missing"]),
            {"class:a.py:Foo"},
        )
        self.assertEqual(paths_in_order(chunks), ["a.py", "b.py"])


class TestSourceBackedGold(unittest.TestCase):
    """Catalog stubs sharing a gold entity id are not a hit by themselves."""

    def _src(self, eid="func:main.py:cmd_query"):
        class _C:
            def __init__(self):
                self.id = f"{eid}:aaaa"
                self.entity_id = eid
                self.file_path = "main.py"
                self.start_line = 130
                self.end_line = 164
                self.content = "def cmd_query(args):\n    return 1\n"
                self.metadata = {}

        return _C()

    def _stub(self, eid="func:main.py:cmd_query"):
        class _C:
            def __init__(self):
                self.id = f"{eid}:cli-doc"
                self.entity_id = eid
                self.file_path = "main.py"
                self.start_line = 130
                self.end_line = 164
                self.content = "The query CLI command is defined as cmd_query in main.py."
                self.metadata = {"kind": "cli_doc", "generated": True}

        return _C()

    def test_stub_alone_is_not_source_backed(self):
        stub = self._stub()
        src = self._src()
        self.assertTrue(is_synthetic_chunk(stub))
        self.assertFalse(is_source_backed(stub))
        self.assertFalse(is_synthetic_chunk(src))
        self.assertTrue(is_source_backed(src))

    def test_stub_alone_does_not_count_as_gold_hit(self):
        gold = "func:main.py:cmd_query"
        stub = self._stub(gold)
        self.assertTrue(match_gold_id(stub.id, stub.entity_id, gold))
        self.assertFalse(match_gold_chunk(stub, gold))
        self.assertEqual(gold_ids_hit([stub], [gold]), set())
        self.assertEqual(gold_ids_hit_from_ids([stub.id], [gold]), set())

    def test_stub_plus_real_source_in_top_k_counts(self):
        gold = "func:main.py:cmd_query"
        self.assertEqual(
            gold_ids_hit([self._stub(gold), self._src(gold)], [gold]),
            {gold},
        )

    def test_real_source_alone_still_counts(self):
        gold = "func:main.py:cmd_query"
        src = self._src(gold)
        self.assertTrue(match_gold_chunk(src, gold))
        self.assertEqual(gold_ids_hit([src], [gold]), {gold})


class TestFailureTaxonomy(unittest.TestCase):
    def test_labels_are_the_contracted_set(self):
        self.assertEqual(
            FAILURE_LABELS,
            (
                "dense_miss",
                "bm25_miss",
                "graph_miss",
                "rerank_drop",
                "packer_drop",
            ),
        )

    def test_stage_misses_and_rerank_and_packer(self):
        labels = classify_failures(
            gold_ids=["rel-1"],
            must_cite_paths=["harness/retriever.py"],
            dense_ids=["other"],
            bm25_ids=["other"],
            graph_ids=["other"],
            fused_ids=["rel-1"],
            final_ids=["other-final"],
            packed_ids=["other-final"],
            packed_paths=["harness/eval.py"],
            rerank_enabled=True,
        )
        self.assertIn("dense_miss", labels)
        self.assertIn("bm25_miss", labels)
        self.assertIn("graph_miss", labels)
        self.assertIn("rerank_drop", labels)
        self.assertNotIn("packer_drop", labels)

        packed_drop = classify_failures(
            gold_ids=["rel-1"],
            must_cite_paths=["harness/retriever.py"],
            dense_ids=["rel-1"],
            bm25_ids=["rel-1"],
            graph_ids=["rel-1"],
            fused_ids=["rel-1"],
            final_ids=["rel-1"],
            packed_ids=["other-final"],
            packed_paths=["harness/eval.py"],
            rerank_enabled=True,
        )
        self.assertIn("packer_drop", packed_drop)

    def test_no_labels_on_clean_hit(self):
        labels = classify_failures(
            gold_ids=["rel-1"],
            must_cite_paths=["harness/retriever.py"],
            dense_ids=["rel-1"],
            bm25_ids=["rel-1"],
            graph_ids=["rel-1"],
            fused_ids=["rel-1"],
            final_ids=["rel-1"],
            packed_ids=["rel-1"],
            packed_paths=["harness/retriever.py"],
            rerank_enabled=True,
        )
        self.assertEqual(labels, [])

    def test_packer_drop_stub_when_no_pack_trace(self):
        labels = classify_failures(
            gold_ids=["rel-1"],
            must_cite_paths=[],
            dense_ids=["rel-1"],
            bm25_ids=["rel-1"],
            graph_ids=["rel-1"],
            fused_ids=["rel-1"],
            final_ids=["rel-1"],
            packed_ids=None,
            packed_paths=None,
            rerank_enabled=True,
        )
        self.assertNotIn("packer_drop", labels)


if __name__ == "__main__":
    unittest.main()
