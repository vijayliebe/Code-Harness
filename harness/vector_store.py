"""Vector store facade. Default backend is Chroma; TurboVec is opt-in/experimental."""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Protocol, runtime_checkable
from uuid import uuid4

import numpy as np
from rich.progress import track

from .config import DEFAULT_CONFIG, Config
from .models import Chunk, EntityType, RetrievalResult

CHROMA_DEFAULT_PERSIST = DEFAULT_CONFIG["vector_store"]["persist_directory"]
TURBOVEC_DEFAULT_PERSIST = ".code-harness/turbovec"
STUB_DEFAULT_PERSIST = ".code-harness/vector-stub"

BACKEND_ALIASES = {
    "chroma": "chromadb",
    "chromadb": "chromadb",
    "turbovec": "turbovec",
    "turbo-vec": "turbovec",
    "turbo_vec": "turbovec",
    "stub": "stub",
    "memory": "stub",
}


class BackendUnavailable(ImportError):
    """Optional backend (or native wheel) is not installed."""


class EmbeddingModelMismatch(RuntimeError):
    """Persisted index was built with a different embedding model."""


@runtime_checkable
class VectorStoreBackend(Protocol):
    backend_name: str
    experimental: bool
    supports_allowlist: bool

    def add_chunks(
        self, chunks: List[Chunk], embeddings: List[List[float]], repo_name: str = ""
    ) -> None: ...

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 20,
        filter_dict: Optional[Dict] = None,
        repo_name: Optional[str] = None,
        allowlist_ids: Optional[List[str]] = None,
    ) -> List[RetrievalResult]: ...

    def get_all(self, repo_name: Optional[str] = None) -> List[Chunk]: ...

    def delete_collection(self) -> None: ...

    def delete_by_repo(self, repo_name: str) -> None: ...

    def count(self) -> int: ...

    def persist(self) -> None: ...


def normalize_backend_name(name: Optional[str]) -> str:
    key = (name or "chromadb").strip().lower().replace(" ", "")
    if key not in BACKEND_ALIASES:
        known = "chromadb|turbovec (experimental)|stub"
        raise ValueError(f"Unknown vector backend {name!r}. Use {known}.")
    return BACKEND_ALIASES[key]


def resolved_persist_directory(config: Config) -> str:
    vs = config.vector_store or {}
    kind = normalize_backend_name(vs.get("type", "chromadb"))
    extra = vs.get("turbovec") if isinstance(vs.get("turbovec"), dict) else {}
    extra = extra or {}
    if kind == "turbovec":
        return extra.get("persist_directory") or TURBOVEC_DEFAULT_PERSIST
    if kind == "stub":
        if vs.get("persist_directory") and vs.get("persist_directory") != CHROMA_DEFAULT_PERSIST:
            return vs["persist_directory"]
        return extra.get("persist_directory") or STUB_DEFAULT_PERSIST
    return vs.get("persist_directory") or CHROMA_DEFAULT_PERSIST


def describe_backend(config: Config) -> str:
    kind = normalize_backend_name((config.vector_store or {}).get("type", "chromadb"))
    extra = (config.vector_store or {}).get("turbovec") or {}
    persist = resolved_persist_directory(config)
    if kind == "chromadb":
        return f"chromadb (default) persist={persist}"
    if kind == "turbovec" and extra.get("use_stub"):
        return (
            f"turbovec-stub (EXPERIMENTAL exact-cosine stand-in, not TurboQuant) "
            f"persist={persist} — rebuild required after switching backends"
        )
    if kind == "turbovec":
        bits = extra.get("bits", 4)
        return (
            f"turbovec (EXPERIMENTAL, {bits}-bit TurboQuant; recall-gated) "
            f"persist={persist} — rebuild required after switching backends"
        )
    return f"{kind} persist={persist}"


def apply_vector_backend(config: Config, name: Optional[str]) -> Config:
    if not name:
        return config
    config.vector_store["type"] = normalize_backend_name(name)
    return config


def _chunk_from_metadata(md: Dict, content: str, fallback_id: str = "") -> Chunk:
    et_str = md.get("entity_type", "file")
    et = EntityType(et_str) if et_str in {e.value for e in EntityType} else EntityType.FILE
    return Chunk(
        id=md.get("chunk_id", fallback_id),
        content=content,
        entity_id=md.get("entity_id", ""),
        entity_name=md.get("entity_name", ""),
        entity_type=et,
        file_path=md.get("file_path", ""),
        start_line=int(md.get("start_line", 0) or 0),
        end_line=int(md.get("end_line", 0) or 0),
        docstring="",
        repo_name=md.get("repo_name", ""),
        metadata=md,
    )


def _metadata_for_chunk(chunk: Chunk, repo_name: str = "") -> Dict[str, str]:
    return {
        "chunk_id": chunk.id,
        "entity_id": chunk.entity_id,
        "entity_name": chunk.entity_name,
        "entity_type": chunk.entity_type.value,
        "file_path": chunk.file_path,
        "start_line": str(chunk.start_line),
        "end_line": str(chunk.end_line),
        "repo_name": repo_name or chunk.repo_name or "",
        **{k: str(v) for k, v in (chunk.metadata or {}).items()},
    }


class ChromaVectorStore:
    """Default dense backend: ChromaDB HNSW. Unchanged behavior vs the pre-protocol class."""

    backend_name = "chromadb"
    experimental = False
    supports_allowlist = False

    def __init__(self, config: Config):
        self.config = config
        self.collection_name = config.vector_store.get("collection_name", "code_chunks")
        self.persist_directory = config.vector_store.get("persist_directory", CHROMA_DEFAULT_PERSIST)
        self.similarity_metric = config.vector_store.get("similarity_metric", "cosine")
        self.ef_search = config.vector_store.get("hnsw_ef_search", 256)
        self.ef_construction = config.vector_store.get("hnsw_ef_construction", 200)
        self.hnsw_m = config.vector_store.get("hnsw_m", 32)
        self._collection = None
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            persist = os.path.abspath(self.persist_directory)
            os.makedirs(persist, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=persist,
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=False,
                ),
            )
            return self._client
        except ImportError:
            raise ImportError("chromadb not installed. Install with: pip install chromadb")

    def _get_collection(self):
        if self._collection is not None:
            return self._collection
        client = self._get_client()
        try:
            self._collection = client.get_collection(self.collection_name)
        except Exception:
            try:
                client.delete_collection(self.collection_name)
            except Exception:
                pass
            self._collection = client.create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        try:
            self._collection.modify(metadata={
                "hnsw:search_ef": str(self.ef_search),
            })
        except Exception:
            pass
        return self._collection

    def add_chunks(self, chunks: List[Chunk], embeddings: List[List[float]], repo_name: str = ""):
        if not chunks or not embeddings:
            return

        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have same length")

        collection = self._get_collection()
        ids = []
        metadatas = []
        documents = []

        for chunk, emb in track(
            zip(chunks, embeddings),
            total=len(chunks),
            description="  Storing chunks",
        ):
            chunk.embedding = emb
            uid = str(uuid4())
            ids.append(uid)
            documents.append(chunk.content)
            metadatas.append(_metadata_for_chunk(chunk, repo_name))

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 20,
        filter_dict: Optional[Dict] = None,
        repo_name: Optional[str] = None,
        allowlist_ids: Optional[List[str]] = None,
    ) -> List[RetrievalResult]:
        collection = self._get_collection()

        where = filter_dict.copy() if filter_dict else None
        if repo_name:
            repo_filter = {"repo_name": repo_name}
            if where:
                where = {"$and": [repo_filter, where]}
            else:
                where = repo_filter

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, 200),
            where=where,
        )

        retrieved = []
        if results["ids"]:
            for i in range(len(results["ids"][0])):
                chunk_data = {
                    "id": results["ids"][0][i],
                    "content": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i] if results.get("distances") else 0,
                }
                md = chunk_data["metadata"]
                chunk = _chunk_from_metadata(md, chunk_data["content"], chunk_data["id"])
                score = float(1.0 - chunk_data["distance"])
                retrieved.append(RetrievalResult(chunk=chunk, score=score, source="dense"))

        return retrieved

    def get_all(self, repo_name: Optional[str] = None) -> List[Chunk]:
        collection = self._get_collection()
        where = None
        if repo_name:
            where = {"repo_name": repo_name}
        results = collection.get(
            where=where,
            include=["documents", "metadatas"],
        )
        chunks = []
        if results["ids"]:
            for i in range(len(results["ids"])):
                md = results["metadatas"][i]
                chunks.append(_chunk_from_metadata(md, results["documents"][i] or "", results["ids"][i]))
        return chunks

    def delete_collection(self):
        client = self._get_client()
        try:
            client.delete_collection(self.collection_name)
            self._collection = None
        except Exception:
            pass

    def delete_by_repo(self, repo_name: str):
        collection = self._get_collection()
        try:
            collection.delete(where={"repo_name": repo_name})
        except Exception:
            pass

    def count(self) -> int:
        collection = self._get_collection()
        return collection.count()

    def persist(self) -> None:
        return None


class MemoryExactStore:
    """Exact cosine store used as a protocol stub / CI stand-in. Not TurboQuant."""

    def __init__(
        self,
        config: Config,
        backend_name: str = "stub",
        experimental: bool = False,
    ):
        self.config = config
        self.backend_name = backend_name
        self.experimental = experimental or backend_name != "chromadb"
        self.supports_allowlist = True
        self.persist_directory = resolved_persist_directory(config)
        self.embedding_model = (config.embedding or {}).get("model") or ""
        self._rows: List[Dict] = []
        self._load()

    def _store_path(self) -> str:
        return os.path.join(os.path.abspath(self.persist_directory), "store.json")

    def _load(self) -> None:
        path = self._store_path()
        if not os.path.isfile(path):
            return
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        stored_model = data.get("embedding_model") or ""
        if stored_model:
            self.embedding_model = stored_model
        self._rows = list(data.get("rows") or [])

    def persist(self) -> None:
        path = self._store_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "backend": self.backend_name,
                    "embedding_model": self.embedding_model,
                    "rows": self._rows,
                },
                fh,
            )
            fh.write("\n")

    def _check_model(self) -> None:
        current = (self.config.embedding or {}).get("model") or ""
        stored = self.embedding_model or ""
        if stored and current and stored != current:
            raise EmbeddingModelMismatch(
                f"Index was built with embedding.model={stored!r}, config has {current!r}. "
                "Rebuild the index after switching models or backends "
                f"(python main.py index --vector-backend {self.backend_name})."
            )

    def add_chunks(self, chunks: List[Chunk], embeddings: List[List[float]], repo_name: str = ""):
        if not chunks or not embeddings:
            return
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have same length")
        self._check_model()
        if not self.embedding_model:
            self.embedding_model = (self.config.embedding or {}).get("model") or ""
        by_id = {row["metadata"]["chunk_id"]: i for i, row in enumerate(self._rows)}
        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb
            record = {
                "content": chunk.content,
                "embedding": [float(x) for x in emb],
                "metadata": _metadata_for_chunk(chunk, repo_name),
            }
            existing = by_id.get(chunk.id)
            if existing is not None:
                self._rows[existing] = record
            else:
                by_id[chunk.id] = len(self._rows)
                self._rows.append(record)
        self.persist()

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 20,
        filter_dict: Optional[Dict] = None,
        repo_name: Optional[str] = None,
        allowlist_ids: Optional[List[str]] = None,
    ) -> List[RetrievalResult]:
        self._check_model()
        allowed = set(allowlist_ids or [])
        query = np.asarray(query_embedding, dtype=np.float64)
        qn = float(np.linalg.norm(query)) or 1.0
        scored = []
        for row in self._rows:
            md = row["metadata"]
            if repo_name and md.get("repo_name") != repo_name:
                continue
            if allowed and md.get("chunk_id") not in allowed:
                continue
            if filter_dict:
                skip = False
                for key, value in filter_dict.items():
                    if str(md.get(key, "")) != str(value):
                        skip = True
                        break
                if skip:
                    continue
            vec = np.asarray(row["embedding"], dtype=np.float64)
            denom = (float(np.linalg.norm(vec)) * qn) or 1.0
            score = float(np.dot(query, vec) / denom)
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        retrieved = []
        for score, row in scored[:top_k]:
            chunk = _chunk_from_metadata(row["metadata"], row["content"])
            retrieved.append(RetrievalResult(chunk=chunk, score=score, source="dense"))
        return retrieved

    def get_all(self, repo_name: Optional[str] = None) -> List[Chunk]:
        chunks = []
        for row in self._rows:
            md = row["metadata"]
            if repo_name and md.get("repo_name") != repo_name:
                continue
            chunks.append(_chunk_from_metadata(md, row["content"]))
        return chunks

    def delete_collection(self):
        self._rows = []
        path = self._store_path()
        if os.path.isfile(path):
            os.remove(path)

    def delete_by_repo(self, repo_name: str):
        self._rows = [row for row in self._rows if row["metadata"].get("repo_name") != repo_name]
        self.persist()

    def count(self) -> int:
        return len(self._rows)


def create_backend(config: Config, backend: Optional[VectorStoreBackend] = None) -> VectorStoreBackend:
    if backend is not None:
        return backend
    kind = normalize_backend_name((config.vector_store or {}).get("type", "chromadb"))
    extra = (config.vector_store or {}).get("turbovec") if isinstance(
        (config.vector_store or {}).get("turbovec"), dict
    ) else {}
    extra = extra or {}
    if kind == "chromadb":
        return ChromaVectorStore(config)
    if kind == "stub":
        return MemoryExactStore(config, backend_name="stub", experimental=True)
    if kind == "turbovec":
        if extra.get("use_stub"):
            return MemoryExactStore(config, backend_name="turbovec-stub", experimental=True)
        from .turbovec_store import TurboVecStore

        return TurboVecStore(config)
    raise ValueError(f"Unknown vector backend {kind!r}")


class VectorStore:
    """Public facade. Call sites keep constructing ``VectorStore(config)``."""

    def __init__(self, config: Config, backend: Optional[VectorStoreBackend] = None):
        self.config = config
        self._impl = create_backend(config, backend)
        self.backend_name = getattr(self._impl, "backend_name", "chromadb")
        self.experimental = bool(getattr(self._impl, "experimental", False))
        self.supports_allowlist = bool(getattr(self._impl, "supports_allowlist", False))
        self.persist_directory = getattr(
            self._impl, "persist_directory", resolved_persist_directory(config)
        )
        self.collection_name = getattr(
            self._impl, "collection_name", config.vector_store.get("collection_name", "code_chunks")
        )

    def add_chunks(self, chunks: List[Chunk], embeddings: List[List[float]], repo_name: str = ""):
        return self._impl.add_chunks(chunks, embeddings, repo_name=repo_name)

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 20,
        filter_dict: Optional[Dict] = None,
        repo_name: Optional[str] = None,
        allowlist_ids: Optional[List[str]] = None,
    ) -> List[RetrievalResult]:
        return self._impl.search(
            query_embedding,
            top_k=top_k,
            filter_dict=filter_dict,
            repo_name=repo_name,
            allowlist_ids=allowlist_ids,
        )

    def get_all(self, repo_name: Optional[str] = None) -> List[Chunk]:
        return self._impl.get_all(repo_name=repo_name)

    def delete_collection(self):
        return self._impl.delete_collection()

    def delete_by_repo(self, repo_name: str):
        return self._impl.delete_by_repo(repo_name)

    def count(self) -> int:
        return self._impl.count()

    def persist(self) -> None:
        persist = getattr(self._impl, "persist", None)
        if callable(persist):
            persist()
