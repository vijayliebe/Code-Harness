"""Optional TurboVec (TurboQuant) dense backend.

Uses the real ``turbovec`` package (`IdMapIndex`, ``add_with_ids``, ``search``,
``sync``). The wheel is an extra — never imported on the default Chroma path.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional

import numpy as np

from .config import Config
from .models import RetrievalResult
from .vector_store import (
    TURBOVEC_DEFAULT_PERSIST,
    BackendUnavailable,
    EmbeddingModelMismatch,
    _chunk_from_metadata,
    _metadata_for_chunk,
    resolved_persist_directory,
)


def _import_turbovec():
    try:
        import turbovec
    except ImportError as exc:
        raise BackendUnavailable(
            "turbovec is not installed. Experimental extra: "
            "pip install -r requirements-turbovec.txt  (or pip install turbovec)"
        ) from exc
    return turbovec


def chunk_id_to_u64(chunk_id: str) -> int:
    digest = hashlib.blake2b(chunk_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little")


def pad_dim(dim: int) -> int:
    if dim <= 0:
        return 8
    rem = dim % 8
    return dim if rem == 0 else dim + (8 - rem)


def pad_vectors(vectors: np.ndarray, dim: int) -> np.ndarray:
    if vectors.shape[1] == dim:
        return vectors
    if vectors.shape[1] > dim:
        return vectors[:, :dim]
    out = np.zeros((vectors.shape[0], dim), dtype=np.float32)
    out[:, : vectors.shape[1]] = vectors
    return out


class TurboVecStore:
    """Adapter over ``turbovec.IdMapIndex`` plus a JSON sidecar for chunk text."""

    backend_name = "turbovec"
    experimental = True
    supports_allowlist = True

    def __init__(
        self,
        config: Config,
        *,
        index=None,
        sidecar: Optional[Dict[int, Dict]] = None,
        embedding_model: Optional[str] = None,
        id_map: Optional[Dict[str, int]] = None,
        dim: Optional[int] = None,
    ):
        self.config = config
        extra = (config.vector_store or {}).get("turbovec") or {}
        self.bits = int(extra.get("bits") or 4)
        if self.bits not in (2, 3, 4):
            raise ValueError("vector_store.turbovec.bits must be 2, 3, or 4")
        self.persist_directory = extra.get("persist_directory") or resolved_persist_directory(config) or TURBOVEC_DEFAULT_PERSIST
        self.embedding_model = embedding_model or (config.embedding or {}).get("model") or ""
        self.dim = dim
        self._index = index
        self._sidecar: Dict[int, Dict] = {int(k): v for k, v in (sidecar or {}).items()}
        self._id_map: Dict[str, int] = dict(id_map or {})
        if index is None:
            _import_turbovec()
            self._load()

    def _index_path(self) -> str:
        return os.path.join(os.path.abspath(self.persist_directory), "index.tvim")

    def _sidecar_path(self) -> str:
        return os.path.join(os.path.abspath(self.persist_directory), "sidecar.json")

    def _manifest_path(self) -> str:
        return os.path.join(os.path.abspath(self.persist_directory), "manifest.json")

    def _load(self) -> None:
        sidecar_path = self._sidecar_path()
        if os.path.isfile(sidecar_path):
            with open(sidecar_path, encoding="utf-8") as fh:
                data = json.load(fh)
            self._sidecar = {int(k): v for k, v in (data.get("rows") or {}).items()}
            self._id_map = {str(k): int(v) for k, v in (data.get("id_map") or {}).items()}
        manifest_path = self._manifest_path()
        if os.path.isfile(manifest_path):
            with open(manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
            self.embedding_model = manifest.get("embedding_model") or self.embedding_model
            if manifest.get("dim"):
                self.dim = int(manifest["dim"])
            if manifest.get("bits"):
                self.bits = int(manifest["bits"])
        index_path = self._index_path()
        if os.path.isfile(index_path):
            tv = _import_turbovec()
            self._index = tv.IdMapIndex.load(index_path)
            if getattr(self._index, "dim", None):
                self.dim = self._index.dim

    def _ensure_index(self, dim: int):
        if self._index is not None:
            return self._index
        tv = _import_turbovec()
        self.dim = pad_dim(dim)
        self._index = tv.IdMapIndex(dim=self.dim, bit_width=self.bits)
        return self._index

    def _allocate_id(self, chunk_id: str) -> int:
        existing = self._id_map.get(chunk_id)
        if existing is not None:
            return existing
        value = chunk_id_to_u64(chunk_id)
        used = set(self._sidecar)
        used.update(self._id_map.values())
        while value in used:
            value = (value + 1) & 0xFFFFFFFFFFFFFFFF
            if value == 0:
                value = 1
        self._id_map[chunk_id] = value
        return value

    def _check_model(self) -> None:
        current = (self.config.embedding or {}).get("model") or ""
        stored = self.embedding_model or ""
        if stored and current and stored != current:
            raise EmbeddingModelMismatch(
                f"TurboVec index was built with embedding.model={stored!r}, config has {current!r}. "
                "Rebuild required: python main.py index --vector-backend turbovec"
            )

    def add_chunks(self, chunks, embeddings, repo_name: str = ""):
        if not chunks or not embeddings:
            return
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have same length")
        self._check_model()
        if not self.embedding_model:
            self.embedding_model = (self.config.embedding or {}).get("model") or ""
        matrix = np.asarray(embeddings, dtype=np.float32)
        if matrix.ndim != 2:
            raise ValueError("embeddings must be a 2-D array")
        index = self._ensure_index(matrix.shape[1])
        matrix = pad_vectors(matrix, int(self.dim or pad_dim(matrix.shape[1])))
        ids = []
        for chunk in chunks:
            uid = self._allocate_id(chunk.id)
            if self._index is not None and hasattr(self._index, "remove") and uid in self._sidecar:
                try:
                    self._index.remove(uid)
                except Exception:
                    pass
            ids.append(uid)
        id_arr = np.asarray(ids, dtype=np.uint64)
        index.add_with_ids(matrix, id_arr)
        for chunk, uid in zip(chunks, ids):
            self._sidecar[int(uid)] = {
                "content": chunk.content,
                **_metadata_for_chunk(chunk, repo_name),
                "metadata": _metadata_for_chunk(chunk, repo_name),
            }
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
        if self._index is None:
            return []
        query = np.asarray([query_embedding], dtype=np.float32)
        target_dim = int(self.dim or query.shape[1])
        query = pad_vectors(query, target_dim)
        kwargs = {"k": min(int(top_k), 200)}
        candidate_ids = None
        if allowlist_ids:
            mapped = [self._id_map[cid] for cid in allowlist_ids if cid in self._id_map]
            if repo_name:
                mapped = [
                    uid for uid in mapped
                    if (self._sidecar.get(int(uid)) or {}).get("repo_name") == repo_name
                    or (self._sidecar.get(int(uid)) or {}).get("metadata", {}).get("repo_name") == repo_name
                ]
            if mapped:
                candidate_ids = np.asarray(mapped, dtype=np.uint64)
                kwargs["allowlist"] = candidate_ids
        scores, ids = self._index.search(query, **kwargs)
        retrieved = []
        score_row = scores[0] if len(scores) else []
        id_row = ids[0] if len(ids) else []
        for score, uid in zip(score_row, id_row):
            uid_int = int(uid)
            row = self._sidecar.get(uid_int)
            if not row:
                continue
            md = row.get("metadata") or {k: v for k, v in row.items() if k not in ("content", "metadata")}
            if repo_name and md.get("repo_name") != repo_name:
                continue
            if filter_dict:
                skip = False
                for key, value in filter_dict.items():
                    if str(md.get(key, "")) != str(value):
                        skip = True
                        break
                if skip:
                    continue
            chunk = _chunk_from_metadata(md, row.get("content") or "")
            retrieved.append(RetrievalResult(chunk=chunk, score=float(score), source="dense"))
        return retrieved

    def get_all(self, repo_name: Optional[str] = None):
        chunks = []
        for row in self._sidecar.values():
            md = row.get("metadata") or {k: v for k, v in row.items() if k not in ("content", "metadata")}
            if repo_name and md.get("repo_name") != repo_name:
                continue
            chunks.append(_chunk_from_metadata(md, row.get("content") or ""))
        return chunks

    def delete_collection(self):
        if self._index is not None:
            for uid in list(self._sidecar):
                try:
                    self._index.remove(uid)
                except Exception:
                    pass
        self._sidecar = {}
        self._id_map = {}
        self._index = None
        for path in (self._index_path(), self._sidecar_path(), self._manifest_path()):
            if os.path.isfile(path):
                os.remove(path)

    def delete_by_repo(self, repo_name: str):
        drop = []
        for uid, row in list(self._sidecar.items()):
            md = row.get("metadata") or row
            if md.get("repo_name") == repo_name:
                drop.append(uid)
        for uid in drop:
            if self._index is not None:
                try:
                    self._index.remove(uid)
                except Exception:
                    pass
            chunk_id = (self._sidecar.get(uid) or {}).get("chunk_id") or (
                (self._sidecar.get(uid) or {}).get("metadata") or {}
            ).get("chunk_id")
            self._sidecar.pop(uid, None)
            if chunk_id:
                self._id_map.pop(chunk_id, None)
        self.persist()

    def count(self) -> int:
        return len(self._sidecar)

    def persist(self) -> None:
        persist_dir = os.path.abspath(self.persist_directory)
        os.makedirs(persist_dir, exist_ok=True)
        with open(self._sidecar_path(), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "rows": {str(k): v for k, v in self._sidecar.items()},
                    "id_map": self._id_map,
                },
                fh,
            )
            fh.write("\n")
        with open(self._manifest_path(), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "backend": "turbovec",
                    "embedding_model": self.embedding_model,
                    "dim": self.dim,
                    "bits": self.bits,
                },
                fh,
                indent=2,
            )
            fh.write("\n")
        if self._index is not None and hasattr(self._index, "sync"):
            self._index.sync(self._index_path())
        elif self._index is not None and hasattr(self._index, "write"):
            self._index.write(self._index_path())
