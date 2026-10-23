"""R5: heuristic drop / chunk / neighbours / file. LLM only if decision.enabled."""

import unittest

from harness.config import Config
from harness.expansion import (
    CHUNK,
    DROP,
    FILE,
    NEIGHBORS,
    apply_expansion,
    classify_expansion_heuristic,
    expansion_enabled,
)
from harness.knowledge_graph import KnowledgeGraph
from harness.models import Chunk, EntityType, RetrievalResult


def _chunk(cid, path, name, start, end, content=None):
    return Chunk(
        id=cid,
        content=content or f"def {name}():\n    pass\n",
        entity_id=cid,
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=start,
        end_line=end,
    )


def _rr(chunk, score):
    return RetrievalResult(chunk=chunk, score=score, source="fused")


class TestExpansion(unittest.TestCase):
    def test_default_follows_config(self):
        self.assertTrue(expansion_enabled(Config()))
        cfg = Config()
        cfg.retrieval["expansion"]["enabled"] = False
        self.assertFalse(expansion_enabled(cfg))

    def test_low_score_without_identifier_drops(self):
        cfg = Config()
        cfg.retrieval["expansion"]["enabled"] = True
        chunk = _chunk("c1", "harness/other.py", "unrelated", 1, 4)
        kind = classify_expansion_heuristic("how does ContextBuilder work?", _rr(chunk, 0.001), config=cfg)
        self.assertEqual(kind, DROP)

    def test_path_in_query_selects_file(self):
        cfg = Config()
        chunk = _chunk("c1", "harness/context_builder.py", "build_context", 10, 40)
        kind = classify_expansion_heuristic(
            "what is in context_builder.py?",
            _rr(chunk, 0.4),
            config=cfg,
        )
        self.assertEqual(kind, FILE)

    def test_identifier_and_adjacent_selects_neighbors(self):
        cfg = Config()
        main = _chunk("c1", "harness/demo.py", "ContextBuilder", 10, 20)
        neighbor = _chunk("c2", "harness/demo.py", "helper", 21, 30)
        kind = classify_expansion_heuristic(
            "How does ContextBuilder assemble context?",
            _rr(main, 0.5),
            all_chunks=[main, neighbor],
            config=cfg,
        )
        self.assertEqual(kind, NEIGHBORS)

    def test_apply_neighbors_adds_adjacent_chunk(self):
        cfg = Config()
        cfg.retrieval["expansion"]["enabled"] = True
        main = _chunk("c1", "harness/demo.py", "ContextBuilder", 10, 20)
        neighbor = _chunk("c2", "harness/demo.py", "helper", 21, 30)
        other = _chunk("c3", "harness/other.py", "skip_me", 1, 4)
        out = apply_expansion(
            "How does ContextBuilder assemble context?",
            [_rr(main, 0.5)],
            all_chunks=[main, neighbor, other],
            config=cfg,
        )
        ids = [r.chunk.id for r in out]
        self.assertIn("c1", ids)
        self.assertIn("c2", ids)
        self.assertNotIn("c3", ids)

    def test_apply_noop_when_disabled(self):
        cfg = Config()
        cfg.retrieval["expansion"]["enabled"] = False
        main = _chunk("c1", "harness/demo.py", "ContextBuilder", 10, 20)
        neighbor = _chunk("c2", "harness/demo.py", "helper", 21, 30)
        out = apply_expansion(
            "How does ContextBuilder assemble context?",
            [_rr(main, 0.5)],
            all_chunks=[main, neighbor],
            config=cfg,
        )
        self.assertEqual([r.chunk.id for r in out], ["c1"])

    def test_decision_not_called_when_decision_disabled(self):
        cfg = Config()
        cfg.retrieval["expansion"]["enabled"] = True
        cfg.decision["enabled"] = False
        called = []

        class Decider:
            def decide(self, state, questions):
                called.append(True)
                return None

        main = _chunk("c1", "harness/other.py", "unrelated", 1, 4)
        apply_expansion(
            "how does ContextBuilder work?",
            [_rr(main, 0.001)],
            all_chunks=[main],
            config=cfg,
            decider=Decider(),
        )
        self.assertEqual(called, [])
