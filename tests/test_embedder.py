"""Local embedder catalog + MiniLM vs Jina-code switch (no live Jina download)."""

from __future__ import annotations

import tempfile
import unittest

from harness.config import DEFAULT_CONFIG, Config
from harness.embedder import Embedder


class TestDefaultEmbedderUnchanged(unittest.TestCase):
    def test_default_config_stays_minilm_local_384(self):
        self.assertEqual(DEFAULT_CONFIG["embedding"]["model"], "all-MiniLM-L6-v2")
        self.assertEqual(DEFAULT_CONFIG["embedding"]["provider"], "local")
        self.assertEqual(DEFAULT_CONFIG["embedding"]["dimensions"], 384)
        self.assertEqual(DEFAULT_CONFIG["vector_store"]["type"], "chromadb")
        self.assertFalse(DEFAULT_CONFIG["decision"]["enabled"])

        cfg = Config()
        embedder = Embedder(cfg)
        self.assertEqual(embedder.model_name, "all-MiniLM-L6-v2")
        self.assertEqual(embedder.provider, "local")
        self.assertEqual(embedder.dimensions, 384)
        self.assertFalse(cfg.decision["enabled"])
        self.assertEqual(cfg.vector_store["type"], "chromadb")


class TestEmbedCatalog(unittest.TestCase):
    def test_jina_code_alias_stays_local_and_is_768d(self):
        from harness.embedder import (
            JINA_CODE_EMBED_MODEL,
            apply_embedding_model,
            normalize_embed_model,
            resolve_embed_spec,
        )

        self.assertEqual(normalize_embed_model("jina-code"), JINA_CODE_EMBED_MODEL)
        self.assertEqual(
            normalize_embed_model("jinaai/jina-embeddings-v2-base-code"),
            JINA_CODE_EMBED_MODEL,
        )
        spec = resolve_embed_spec("jina-embeddings-v2-base-code")
        self.assertEqual(spec.id, "jina-embeddings-v2-base-code")
        self.assertEqual(spec.hf_id, "jinaai/jina-embeddings-v2-base-code")
        self.assertEqual(spec.dimensions, 768)
        self.assertTrue(spec.local)

        cfg = Config()
        apply_embedding_model(cfg, "jina-code")
        embedder = Embedder(cfg)
        self.assertEqual(embedder.model_name, "jina-embeddings-v2-base-code")
        self.assertEqual(embedder.provider, "local")
        self.assertEqual(embedder.dimensions, 768)
        self.assertEqual(cfg.embedding["provider"], "local")
        self.assertFalse(cfg.decision["enabled"])
        self.assertEqual(cfg.vector_store["type"], "chromadb")

    def test_catalog_jina_does_not_infer_api_provider(self):
        cfg = Config()
        cfg.embedding["provider"] = "local"
        cfg.embedding["model"] = "jina-embeddings-v2-base-code"
        embedder = Embedder(cfg)
        self.assertEqual(embedder.provider, "local")

    def test_explicit_jina_api_provider_still_uses_api(self):
        cfg = Config()
        cfg.embedding["provider"] = "jina"
        cfg.embedding["model"] = "jina-embeddings-v3"
        embedder = Embedder(cfg)
        self.assertEqual(embedder.provider, "jina")

    def test_voyage_prefix_still_infers_api(self):
        cfg = Config()
        cfg.embedding["provider"] = "local"
        cfg.embedding["model"] = "voyage-code-2"
        embedder = Embedder(cfg)
        self.assertEqual(embedder.provider, "voyage")

    def test_jina_code_isolates_chroma_persist_from_minilm(self):
        from harness.embedder import apply_embedding_model, persist_dir_for_embed
        from harness.vector_store import CHROMA_DEFAULT_PERSIST

        minilm_dir = persist_dir_for_embed("all-MiniLM-L6-v2")
        jina_dir = persist_dir_for_embed("jina-embeddings-v2-base-code")
        self.assertEqual(minilm_dir, CHROMA_DEFAULT_PERSIST)
        self.assertNotEqual(jina_dir, minilm_dir)
        self.assertIn("jina", jina_dir)

        cfg = Config()
        self.assertEqual(cfg.vector_store["persist_directory"], CHROMA_DEFAULT_PERSIST)
        apply_embedding_model(cfg, "jina-embeddings-v2-base-code")
        self.assertEqual(cfg.vector_store["persist_directory"], jina_dir)
        self.assertEqual(cfg.vector_store["type"], "chromadb")

        custom = Config()
        custom.vector_store["persist_directory"] = "/tmp/custom-chroma"
        apply_embedding_model(custom, "jina-code")
        self.assertEqual(custom.vector_store["persist_directory"], "/tmp/custom-chroma")

    def test_apply_does_not_enable_decision_or_flip_chroma(self):
        from harness.embedder import apply_embedding_model

        cfg = Config()
        cfg.decision["enabled"] = False
        apply_embedding_model(cfg, "all-MiniLM-L6-v2")
        self.assertFalse(cfg.decision["enabled"])
        self.assertEqual(cfg.vector_store["type"], "chromadb")
        self.assertEqual(cfg.embedding["model"], "all-MiniLM-L6-v2")
        self.assertEqual(cfg.embedding["dimensions"], 384)


class TestProbeEmbedder(unittest.TestCase):
    def test_probe_skips_when_jina_weights_unavailable(self):
        from harness.embedder import probe_embedder

        available, reason = probe_embedder(
            "jina-embeddings-v2-base-code",
            try_load=False,
            cache_lookup=lambda hf_id: False,
        )
        self.assertFalse(available)
        self.assertIn("jina", reason.lower())

    def test_probe_ok_when_jina_cache_present(self):
        from harness.embedder import probe_embedder

        available, reason = probe_embedder(
            "jina-code",
            try_load=False,
            cache_lookup=lambda hf_id: True,
        )
        self.assertTrue(available)
        self.assertEqual(reason, "")

    def test_probe_swallows_download_failure(self):
        from harness.embedder import probe_embedder

        def boom(hf_id):
            raise RuntimeError("Hub is down; cannot download jina-embeddings-v2-base-code")

        available, reason = probe_embedder(
            "jina-embeddings-v2-base-code",
            try_load=True,
            load_fn=boom,
        )
        self.assertFalse(available)
        self.assertIn("download", reason.lower())

    def test_minilm_probe_does_not_require_jina(self):
        from harness.embedder import probe_embedder

        available, reason = probe_embedder("all-MiniLM-L6-v2", try_load=False)
        self.assertTrue(available)
        self.assertEqual(reason, "")


class TestLocalModelLoadUsesHfId(unittest.TestCase):
    def test_jina_code_loads_huggingface_id(self):
        from unittest.mock import patch

        from harness.embedder import apply_embedding_model

        cfg = Config()
        apply_embedding_model(cfg, "jina-embeddings-v2-base-code")
        embedder = Embedder(cfg)

        class FakeModel:
            def get_embedding_dimension(self):
                return 768

        loaded = []

        def fake_ctor(name, trust_remote_code=False):
            loaded.append((name, trust_remote_code))
            return FakeModel()

        import harness.embedder as embed_mod

        previous = dict(embed_mod._model_cache)
        embed_mod._model_cache.clear()
        try:
            with patch("sentence_transformers.SentenceTransformer", fake_ctor):
                model = embedder._load_local_model()
            self.assertEqual(model.get_embedding_dimension(), 768)
            self.assertEqual(loaded[0][0], "jinaai/jina-embeddings-v2-base-code")
            self.assertTrue(loaded[0][1])
            self.assertEqual(embedder.dimensions, 768)
        finally:
            embed_mod._model_cache.clear()
            embed_mod._model_cache.update(previous)


class TestEnvAndConfigApply(unittest.TestCase):
    def test_from_dict_catalog_model_keeps_decision_off(self):
        from harness.embedder import apply_embedding_model

        cfg = Config.from_dict(
            {
                "embedding": {
                    "provider": "local",
                    "model": "jina-embeddings-v2-base-code",
                }
            }
        )
        apply_embedding_model(cfg, cfg.embedding["model"])
        self.assertEqual(cfg.embedding["model"], "jina-embeddings-v2-base-code")
        self.assertEqual(cfg.embedding["dimensions"], 768)
        self.assertFalse(cfg.decision["enabled"])

    def test_env_override_helper(self):
        from harness.embedder import resolve_requested_embed_model

        with tempfile.TemporaryDirectory():
            self.assertEqual(
                resolve_requested_embed_model(
                    embed_model="jina-code",
                    environ={"CODEHARNESS_EMBED_MODEL": "all-MiniLM-L6-v2"},
                    config_model="all-MiniLM-L6-v2",
                ),
                "jina-embeddings-v2-base-code",
            )
            self.assertEqual(
                resolve_requested_embed_model(
                    embed_model=None,
                    environ={"CODEHARNESS_EMBED_MODEL": "jina-code"},
                    config_model="all-MiniLM-L6-v2",
                ),
                "jina-embeddings-v2-base-code",
            )
            self.assertEqual(
                resolve_requested_embed_model(
                    embed_model=None,
                    environ={},
                    config_model="all-MiniLM-L6-v2",
                ),
                "all-MiniLM-L6-v2",
            )


if __name__ == "__main__":
    unittest.main()
