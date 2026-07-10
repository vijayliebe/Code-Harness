import os
import re
from typing import List, Optional

import numpy as np

from .config import Config
from .utils import retry_with_backoff

_model_cache = {}


_PROVIDER_FROM_MODEL = {
    "voyage-": "voyage",
    "jina-": "jina",
    "text-embedding-": "openai",
}


class Embedder:
    _PROVIDER_ENV = {
        "openai": "OPENAI_API_KEY",
        "voyage": "VOYAGE_API_KEY",
        "jina": "JINA_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }

    def __init__(self, config: Config):
        self.config = config
        self.model_name = config.embedding.get("model", "all-MiniLM-L6-v2")
        explicit = config.embedding.get("provider", "local")
        self.provider = self._infer_provider(explicit)
        self.dimensions = config.embedding.get("dimensions", 384)
        self.api_key = (
            config.embedding.get("api_key")
            or os.environ.get(self._PROVIDER_ENV.get(self.provider, ""))
            or os.environ.get("OPENAI_API_KEY")
        )
        self.api_base = config.embedding.get("api_base")
        self.hyde_enabled = config.retrieval.get("hyde", {}).get("enabled", False)

    def _infer_provider(self, explicit: str) -> str:
        if explicit != "local":
            return explicit
        for prefix, provider in _PROVIDER_FROM_MODEL.items():
            if self.model_name.startswith(prefix):
                return provider
        return "local"

    def _load_local_model(self):
        global _model_cache
        if self.model_name in _model_cache:
            cached = _model_cache[self.model_name]
            self.dimensions = cached.get_embedding_dimension()
            return cached
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(self.model_name, trust_remote_code=True)
            self.dimensions = model.get_embedding_dimension()
            _model_cache[self.model_name] = model
            return model
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )

    def _embed_openai(self, texts: List[str]) -> List[List[float]]:
        try:
            import openai
            client = openai.OpenAI(api_key=self.api_key, base_url=self.api_base)
            model = self.model_name if "text-embedding" in self.model_name else "text-embedding-3-small"
            results: List[List[float]] = [None] * len(texts)
            batches = [texts[i:i + self._BATCH_SIZE]
                       for i in range(0, len(texts), self._BATCH_SIZE)]
            from rich.progress import track
            idx = 0
            for batch in track(batches, description="  Embedding batches"):
                def _call(b=batch):
                    resp = client.embeddings.create(input=b, model=model)
                    return [r.embedding for r in resp.data]
                batch_embs = retry_with_backoff(_call, max_retries=5, base_delay=2.0, backoff=3.0)
                for emb in batch_embs:
                    results[idx] = emb
                    idx += 1
                time.sleep(self._BATCH_DELAY)
            return results
        except Exception as e:
            raise RuntimeError(f"OpenAI embedding failed: {e}")

    _BATCH_SIZE = 128
    _BATCH_DELAY = 0.5

    @staticmethod
    def _is_429(e: Exception) -> bool:
        return hasattr(e, "response") and getattr(e.response, "status_code", 0) == 429

    def _batch_embed(self, texts: List[str], api_url: str,
                     headers: dict, payload_fn) -> List[List[float]]:
        from rich.progress import track
        import httpx
        results: List[List[float]] = [None] * len(texts)
        batches = [texts[i:i + self._BATCH_SIZE]
                   for i in range(0, len(texts), self._BATCH_SIZE)]
        idx = 0
        for batch in track(batches, description="  Embedding batches"):
            def _call(b=batch):
                resp = httpx.post(
                    api_url, headers=headers,
                    json=payload_fn(b), timeout=120,
                )
                if resp.status_code == 429:
                    raise IOError("rate_limited")
                resp.raise_for_status()
                data = resp.json()
                return [d["embedding"] for d in data["data"]]
            batch_embs = retry_with_backoff(
                _call, max_retries=5, base_delay=2.0, backoff=3.0,
            )
            for emb in batch_embs:
                results[idx] = emb
                idx += 1
            time.sleep(self._BATCH_DELAY)
        return results

    def _embed_voyage(self, texts: List[str]) -> List[List[float]]:
        api_key = self.api_key
        if not api_key:
            raise RuntimeError("VOYAGE_API_KEY required for voyage embeddings. "
                               "Set it in .env or export VOYAGE_API_KEY='your-key'")
        model = self.model_name if self.model_name != "all-MiniLM-L6-v2" else "voyage-code-2"
        def payload(texts):
            return {"input": texts, "model": model, "input_type": "document"}
        return self._batch_embed(
            texts,
            "https://api.voyageai.com/v1/embeddings",
            {"Authorization": f"Bearer {api_key}"},
            payload,
        )

    def _embed_jina(self, texts: List[str]) -> List[List[float]]:
        api_key = self.api_key
        if not api_key:
            raise RuntimeError("JINA_API_KEY required for jina embeddings. "
                               "Set it in .env or export JINA_API_KEY='your-key'")
        model = self.model_name if self.model_name != "all-MiniLM-L6-v2" else "jina-embeddings-v3"
        def payload(texts):
            return {"input": texts, "model": model, "task": "text-matching"}
        return self._batch_embed(
            texts,
            "https://api.jina.ai/v1/embeddings",
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload,
        )

    def _expand_query(self, query: str) -> str:
        code_keywords = [
            "function", "class", "method", "import", "def", "return",
            "implement", "interface", "abstract", "extends", "implements",
        ]
        expanded = query
        has_code_term = any(kw in query.lower() for kw in code_keywords)
        if not has_code_term:
            expanded = f"code that {query}"
        return expanded

    def _generate_hypothetical(self, query: str) -> str:
        parts = [p.strip() for p in re.split(r'[?\n]', query) if p.strip()]
        core = parts[0] if parts else query
        return (
            f"The following code implements {core}. "
            f"It contains classes, functions, and methods related to {core}. "
            f"The implementation handles {core} efficiently."
        )

    def embed_query(self, text: str, expand: bool = True) -> List[float]:
        if self.hyde_enabled and expand:
            hyde_text = self._generate_hypothetical(text)
            expanded = f"{text} {hyde_text}"
        elif expand:
            expanded = self._expand_query(text)
        else:
            expanded = text
        embeddings = self.embed([expanded])
        return embeddings[0] if embeddings else []

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        if self.provider == "openai":
            return self._embed_openai(texts)
        elif self.provider == "voyage":
            return self._embed_voyage(texts)
        elif self.provider == "jina":
            return self._embed_jina(texts)

        model = self._load_local_model()
        embeddings = model.encode(texts, show_progress_bar=True, normalize_embeddings=True)
        return embeddings.tolist() if isinstance(embeddings, np.ndarray) else embeddings
