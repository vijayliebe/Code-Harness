"""Config-gated cross-encoder blend / gate / input (default stays replace)."""

import unittest
from unittest.mock import patch

from harness.config import Config
from harness.knowledge_graph import KnowledgeGraph
from harness.models import Chunk, EntityType, RetrievalResult
from harness.retriever import Retriever, _cross_encoder_cache


class FakeEmbedder:
    def embed_query(self, query, expand=True):
        return [0.1, 0.2, 0.3]


class FakeVectorStore:
    supports_allowlist = False

    def search(self, embedding, top_k=10, repo_name=None, allowlist_ids=None):
        return []


class FakeCE:
    def __init__(self, scores):
        self.scores = [float(s) for s in scores]
        self.pairs = None

    def predict(self, pairs, show_progress_bar=False):
        self.pairs = list(pairs)
        return list(self.scores)


def _chunk(name, path="a.py", content=None):
    body = content if content is not None else f"def {name}():\n    return 1\n"
    return Chunk(
        id=f"func:{path}:{name}",
        content=body,
        entity_id=f"func:{path}:{name}",
        entity_name=name,
        entity_type=EntityType.FUNCTION,
        file_path=path,
        start_line=1,
        end_line=3,
    )


def _rr(name, score, path="a.py", content=None):
    return RetrievalResult(chunk=_chunk(name, path=path, content=content), score=score, source="fused")


def _retriever(**ce_kwargs):
    cfg = Config()
    ce = dict(cfg.retrieval.get("cross_encoder") or {})
    ce.update(ce_kwargs)
    cfg.retrieval["cross_encoder"] = ce
    cfg.retrieval["expand_neighbors"] = 0
    kg = KnowledgeGraph(cfg, repo_name="fixture")
    kg.enabled = False
    return Retriever(cfg, FakeEmbedder(), FakeVectorStore(), kg, repo_name="fixture")


class TestRerankFix(unittest.TestCase):
    def tearDown(self):
        _cross_encoder_cache.clear()

    def test_default_config_is_off_content(self):
        cfg = Config()
        ce = cfg.retrieval["cross_encoder"]
        self.assertFalse(ce.get("enabled", True))
        self.assertEqual(ce.get("mode") or "off", "off")
        self.assertEqual(ce.get("input") or "content", "content")
        self.assertIsNone(ce.get("gate_margin"))

    def test_replace_uses_ce_scores_only(self):
        retriever = _retriever(enabled=True, mode="replace")
        fake = FakeCE([0.1, 0.9, 0.2])
        _cross_encoder_cache[retriever.ce_model_name] = fake
        results = [_rr("low", 0.9), _rr("high", 0.5), _rr("mid", 0.1)]
        out = retriever._cross_encoder_rerank("q", results)
        self.assertEqual([r.chunk.entity_name for r in out], ["high", "mid", "low"])
        self.assertAlmostEqual(out[0].score, 0.9)

    def test_blend_alpha_keeps_fused_when_alpha_low(self):
        retriever = _retriever(enabled=True, mode="blend", blend_alpha=0.3)
        fake = FakeCE([0.0, 1.0])
        _cross_encoder_cache[retriever.ce_model_name] = fake
        results = [_rr("fused_top", 1.0), _rr("ce_top", 0.0)]
        out = retriever._cross_encoder_rerank("q", results)
        self.assertEqual([r.chunk.entity_name for r in out], ["fused_top", "ce_top"])

    def test_blend_alpha_follows_ce_when_alpha_high(self):
        retriever = _retriever(enabled=True, mode="blend", blend_alpha=0.7)
        fake = FakeCE([0.0, 1.0])
        _cross_encoder_cache[retriever.ce_model_name] = fake
        results = [_rr("fused_top", 1.0), _rr("ce_top", 0.0)]
        out = retriever._cross_encoder_rerank("q", results)
        self.assertEqual([r.chunk.entity_name for r in out], ["ce_top", "fused_top"])

    def test_gate_keeps_fused_when_margin_small(self):
        retriever = _retriever(enabled=True, mode="gate", gate_margin=1.0)
        fake = FakeCE([5.0, 4.9, 1.0])
        _cross_encoder_cache[retriever.ce_model_name] = fake
        results = [_rr("a", 0.9), _rr("b", 0.5), _rr("c", 0.1)]
        out = retriever._cross_encoder_rerank("q", results)
        self.assertEqual([r.chunk.entity_name for r in out], ["a", "b", "c"])
        self.assertAlmostEqual(out[0].score, 0.9)

    def test_gate_applies_ce_when_margin_large(self):
        retriever = _retriever(enabled=True, mode="gate", gate_margin=1.0)
        fake = FakeCE([1.0, 5.0, 0.0])
        _cross_encoder_cache[retriever.ce_model_name] = fake
        results = [_rr("a", 0.9), _rr("b", 0.5), _rr("c", 0.1)]
        out = retriever._cross_encoder_rerank("q", results)
        self.assertEqual([r.chunk.entity_name for r in out], ["b", "a", "c"])
        self.assertAlmostEqual(out[0].score, 5.0)

    def test_path_symbol_text_input_prefixes_path_and_name(self):
        retriever = _retriever(enabled=True, mode="replace", input="path_symbol_text")
        fake = FakeCE([1.0])
        _cross_encoder_cache[retriever.ce_model_name] = fake
        results = [_rr("cmd_query", 0.5, path="main.py", content="parser.set_defaults(func=cmd_query)\n")]
        retriever._cross_encoder_rerank("Where is cmd_query?", results)
        self.assertIsNotNone(fake.pairs)
        scored = fake.pairs[0][1]
        self.assertTrue(scored.startswith("main.py cmd_query"), scored[:80])
        self.assertIn("parser.set_defaults", scored)

    def test_content_input_omits_path(self):
        retriever = _retriever(enabled=True, mode="replace", input="content")
        fake = FakeCE([1.0])
        _cross_encoder_cache[retriever.ce_model_name] = fake
        results = [_rr("cmd_query", 0.5, path="main.py", content="def cmd_query(args):\n    pass\n")]
        retriever._cross_encoder_rerank("Where is cmd_query?", results)
        scored = fake.pairs[0][1]
        self.assertFalse(scored.startswith("main.py"), scored[:80])

    def test_disabled_leaves_fused_order(self):
        retriever = _retriever(enabled=False)
        results = [_rr("a", 0.9), _rr("b", 0.1)]
        out = retriever._cross_encoder_rerank("q", results)
        self.assertEqual([r.chunk.entity_name for r in out], ["a", "b"])
        self.assertAlmostEqual(out[0].score, 0.9)
