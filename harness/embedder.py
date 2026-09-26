import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np

from .config import Config
from .utils import retry_with_backoff

_model_cache = {}


_PROVIDER_FROM_MODEL = {
    "voyage-": "voyage",
    "jina-": "jina",
    "text-embedding-": "openai",
}

DEFAULT_EMBED_MODEL = "all-MiniLM-L6-v2"
JINA_CODE_EMBED_MODEL = "jina-embeddings-v2-base-code"
JINA_CODE_HF_ID = "jinaai/jina-embeddings-v2-base-code"


@dataclass(frozen=True)
class EmbedSpec:
    """Resolved embedder identity. Catalog entries stay local unless provider is explicit."""

    id: str
    hf_id: str
    dimensions: int
    local: bool
    in_catalog: bool = False


_CATALOG: Dict[str, EmbedSpec] = {
    DEFAULT_EMBED_MODEL: EmbedSpec(
        id=DEFAULT_EMBED_MODEL,
        hf_id=DEFAULT_EMBED_MODEL,
        dimensions=384,
        local=True,
        in_catalog=True,
    ),
    JINA_CODE_EMBED_MODEL: EmbedSpec(
        id=JINA_CODE_EMBED_MODEL,
        hf_id=JINA_CODE_HF_ID,
        dimensions=768,
        local=True,
        in_catalog=True,
    ),
}

_ALIASES = {
    "minilm": DEFAULT_EMBED_MODEL,
    "all-minilm": DEFAULT_EMBED_MODEL,
    "all-minilm-l6-v2": DEFAULT_EMBED_MODEL,
    DEFAULT_EMBED_MODEL.lower(): DEFAULT_EMBED_MODEL,
    DEFAULT_EMBED_MODEL: DEFAULT_EMBED_MODEL,
    "jina-code": JINA_CODE_EMBED_MODEL,
    JINA_CODE_EMBED_MODEL: JINA_CODE_EMBED_MODEL,
    JINA_CODE_EMBED_MODEL.lower(): JINA_CODE_EMBED_MODEL,
    JINA_CODE_HF_ID: JINA_CODE_EMBED_MODEL,
    JINA_CODE_HF_ID.lower(): JINA_CODE_EMBED_MODEL,
}


def normalize_embed_model(name: Optional[str]) -> str:
    key = (name or "").strip()
    if not key:
        return DEFAULT_EMBED_MODEL
    return _ALIASES.get(key) or _ALIASES.get(key.lower()) or key


def resolve_embed_spec(name: Optional[str]) -> EmbedSpec:
    nid = normalize_embed_model(name)
    if nid in _CATALOG:
        return _CATALOG[nid]
    return EmbedSpec(id=nid, hf_id=nid, dimensions=0, local=False, in_catalog=False)


def persist_dir_for_embed(name: Optional[str]) -> str:
    from .vector_store import CHROMA_DEFAULT_PERSIST

    spec = resolve_embed_spec(name)
    if spec.id == DEFAULT_EMBED_MODEL:
        return CHROMA_DEFAULT_PERSIST
    slug = spec.id.replace("/", "-")
    return f".code-harness/chromadb-{slug}"


def apply_embedding_model(config: Config, name: Optional[str], *, isolate_persist: bool = True) -> Config:
    """Set catalog model / dims. Default MiniLM persist stays; Jina-code gets its own Chroma dir."""
    spec = resolve_embed_spec(name)
    config.embedding["model"] = spec.id
    if spec.dimensions:
        config.embedding["dimensions"] = spec.dimensions
    explicit = str(config.embedding.get("provider") or "local").strip().lower()
    if spec.in_catalog and spec.local and explicit == "local":
        config.embedding["provider"] = "local"
    if isolate_persist and spec.in_catalog:
        from .vector_store import CHROMA_DEFAULT_PERSIST, normalize_backend_name

        vs = config.vector_store
        kind = normalize_backend_name(vs.get("type", "chromadb"))
        if kind == "chromadb":
            current = vs.get("persist_directory") or CHROMA_DEFAULT_PERSIST
            auto_dirs = {
                persist_dir_for_embed(DEFAULT_EMBED_MODEL),
                persist_dir_for_embed(JINA_CODE_EMBED_MODEL),
                CHROMA_DEFAULT_PERSIST,
            }
            if current in auto_dirs:
                vs["persist_directory"] = persist_dir_for_embed(spec.id)
    return config


def resolve_requested_embed_model(
    *,
    embed_model: Optional[str] = None,
    environ: Optional[Dict[str, str]] = None,
    config_model: Optional[str] = None,
) -> str:
    """CLI ``--embed-model`` wins, then ``CODEHARNESS_EMBED_MODEL``, then config/default."""
    env = environ if environ is not None else os.environ
    raw = (embed_model or "").strip() or (env.get("CODEHARNESS_EMBED_MODEL") or "").strip() or (
        config_model or ""
    ).strip() or DEFAULT_EMBED_MODEL
    return normalize_embed_model(raw)


def parse_embedder_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    names = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        names.append(normalize_embed_model(part))
    return names


def hf_model_cached(hf_id: str) -> bool:
    """True when Hugging Face / sentence-transformers already has the weights locally."""
    if not hf_id:
        return False
    cache_home = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface"
    )
    hub = os.path.join(cache_home, "hub", "models--" + hf_id.replace("/", "--"))
    if os.path.isdir(hub):
        return True
    st_name = hf_id.replace("/", "_")
    for root in (
        os.path.join(os.path.expanduser("~"), ".cache", "torch", "sentence_transformers"),
        os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "sentence_transformers"),
    ):
        if os.path.isdir(os.path.join(root, st_name)) or os.path.isdir(os.path.join(root, hf_id)):
            return True
    return False


def probe_embedder(
    name: Optional[str],
    config=None,
    *,
    try_load: bool = False,
    cache_lookup: Optional[Callable[[str], bool]] = None,
    load_fn: Optional[Callable[[str], object]] = None,
) -> tuple:
    """Return (available, reason). Never requires a Jina download on the default MiniLM path."""
    spec = resolve_embed_spec(name)
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False, "sentence-transformers not installed; pip install sentence-transformers"

    if spec.id == DEFAULT_EMBED_MODEL:
        return True, ""

    if spec.in_catalog and spec.local:
        lookup = cache_lookup or hf_model_cached
        if try_load:
            loader = load_fn or (lambda hf_id: _try_load_local(hf_id))
            try:
                loader(spec.hf_id)
                return True, ""
            except Exception as exc:
                return False, f"failed to download or load {spec.id}: {exc}"
        try:
            if lookup(spec.hf_id):
                return True, ""
        except Exception as exc:
            return False, f"failed to download or load {spec.id}: {exc}"
        return False, (
            f"{spec.id} weights not cached; first run downloads from Hugging Face "
            "(optional: make eval-ab-embed)"
        )

    return True, ""


def _try_load_local(hf_id: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(hf_id, trust_remote_code=True)


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
        spec = resolve_embed_spec(config.embedding.get("model", DEFAULT_EMBED_MODEL))
        self.model_name = spec.id
        explicit = config.embedding.get("provider", "local")
        self.provider = self._infer_provider(explicit)
        self.dimensions = spec.dimensions or config.embedding.get("dimensions", 384)
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
        spec = resolve_embed_spec(self.model_name)
        if spec.in_catalog and spec.local:
            return "local"
        for prefix, provider in _PROVIDER_FROM_MODEL.items():
            if self.model_name.startswith(prefix):
                return provider
        return "local"

    def _load_local_model(self):
        global _model_cache
        spec = resolve_embed_spec(self.model_name)
        cache_key = spec.hf_id
        if cache_key in _model_cache:
            cached = _model_cache[cache_key]
            self.dimensions = cached.get_embedding_dimension()
            return cached
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(cache_key, trust_remote_code=True)
            self.dimensions = model.get_embedding_dimension()
            _model_cache[cache_key] = model
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
