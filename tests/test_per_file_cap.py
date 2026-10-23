"""R2: per-file chunk cap before the token-budget cut."""

import unittest

from harness.config import Config
from harness.context_builder import ContextBuilder
from harness.models import Chunk, EntityType, RetrievalResult


def _rr(path, name, score, start=1):
    chunk = Chunk(
        id=f"func:{path}:{name}:{start}",
        content=f"def {name}():\n    return {start}\n",
        entity_id=f"func:{path}:{name}",
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=start,
        end_line=start + 2,
    )
    return RetrievalResult(chunk=chunk, score=score, source="test")


class TestPerFileCap(unittest.TestCase):
    def test_default_cap_is_three(self):
        cfg = Config()
        self.assertEqual(int(cfg.context.get("per_file_chunk_cap") or 0), 3)
        builder = ContextBuilder(cfg)
        results = [_rr("a.py", f"f{i}", 1.0 - i * 0.01, start=i * 10) for i in range(5)]
        kept = builder._cap_per_file(results)
        self.assertEqual(len(kept), 3)

    def test_zero_is_unlimited(self):
        cfg = Config()
        cfg.context["per_file_chunk_cap"] = 0
        builder = ContextBuilder(cfg)
        results = [_rr("a.py", f"f{i}", 1.0 - i * 0.01, start=i * 10) for i in range(5)]
        kept = builder._cap_per_file(results)
        self.assertEqual(len(kept), 5)

    def test_cap_keeps_first_n_per_file_in_order(self):
        cfg = Config()
        cfg.context["per_file_chunk_cap"] = 3
        builder = ContextBuilder(cfg)
        results = [
            _rr("a.py", "a1", 0.9, 1),
            _rr("a.py", "a2", 0.8, 20),
            _rr("b.py", "b1", 0.7, 1),
            _rr("a.py", "a3", 0.6, 40),
            _rr("a.py", "a4", 0.5, 60),
            _rr("b.py", "b2", 0.4, 20),
        ]
        kept = builder._cap_per_file(results)
        names = [r.chunk.entity_name for r in kept]
        self.assertEqual(names, ["a1", "a2", "b1", "a3", "b2"])

    def test_cap_runs_before_budget_in_pack(self):
        cfg = Config()
        cfg.context["per_file_chunk_cap"] = 1
        cfg.llm["max_tokens"] = 8000
        builder = ContextBuilder(cfg)
        results = [
            _rr("only.py", "first", 0.9, 1),
            _rr("only.py", "second", 0.8, 40),
        ]
        report = builder.build_context_report("q", results)
        self.assertEqual(len(report.packed_chunk_ids), 1)
        self.assertIn("first", report.context)
        self.assertNotIn("def second", report.context)
