import os
from typing import List, Optional, Dict
from uuid import uuid4

import numpy as np
from rich.progress import track

from .models import Chunk, EntityType, RetrievalResult
from .config import Config


class VectorStore:
    def __init__(self, config: Config):
        self.config = config
        self.collection_name = config.vector_store.get("collection_name", "code_chunks")
        self.persist_directory = config.vector_store.get("persist_directory", ".code-harness/chromadb")
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
            metadatas.append({
                "chunk_id": chunk.id,
                "entity_id": chunk.entity_id,
                "entity_name": chunk.entity_name,
                "entity_type": chunk.entity_type.value,
                "file_path": chunk.file_path,
                "start_line": str(chunk.start_line),
                "end_line": str(chunk.end_line),
                "repo_name": repo_name or chunk.repo_name or "",
                **{k: str(v) for k, v in chunk.metadata.items()},
            })

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def search(self, query_embedding: List[float], top_k: int = 20,
               filter_dict: Optional[Dict] = None,
               repo_name: Optional[str] = None) -> List[RetrievalResult]:
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
                et_str = md.get("entity_type", "file")
                et = EntityType(et_str) if et_str in {e.value for e in EntityType} else EntityType.FILE
                chunk = Chunk(
                    id=md.get("chunk_id", chunk_data["id"]),
                    content=chunk_data["content"],
                    entity_id=md.get("entity_id", ""),
                    entity_name=md.get("entity_name", ""),
                    entity_type=et,
                    file_path=md.get("file_path", ""),
                    start_line=int(md.get("start_line", 0)),
                    end_line=int(md.get("end_line", 0)),
                    metadata=md,
                )
                score = float(1.0 - chunk_data["distance"])
                retrieved.append(RetrievalResult(
                    chunk=chunk, score=score, source="dense"
                ))

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
                et_str = md.get("entity_type", "file")
                et = EntityType(et_str) if et_str in {e.value for e in EntityType} else EntityType.FILE
                chunk = Chunk(
                    id=md.get("chunk_id", results["ids"][i]),
                    content=results["documents"][i] or "",
                    entity_id=md.get("entity_id", ""),
                    entity_name=md.get("entity_name", ""),
                    entity_type=et,
                    file_path=md.get("file_path", ""),
                    start_line=int(md.get("start_line", 0)),
                    end_line=int(md.get("end_line", 0)),
                    docstring="",
                    repo_name=md.get("repo_name", ""),
                    metadata=md,
                )
                chunks.append(chunk)
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
