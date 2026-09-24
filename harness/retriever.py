import math
import os
import pickle
import re
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set

from rank_bm25 import BM25Okapi
from rich.progress import track

from .models import Chunk, RetrievalResult
from .config import Config
from .embedder import Embedder
from .vector_store import VectorStore
from .knowledge_graph import KnowledgeGraph

_cross_encoder_cache = {}


class Retriever:
    def __init__(self, config: Config, embedder: Embedder,
                 vector_store: VectorStore, knowledge_graph: KnowledgeGraph,
                 repo_name: str = ""):
        self.config = config
        self.embedder = embedder
        self.vector_store = vector_store
        self.knowledge_graph = knowledge_graph
        self.repo_name = repo_name
        ret_cfg = config.retrieval
        self.dense_weight = ret_cfg.get("dense_weight", 0.3)
        self.sparse_weight = ret_cfg.get("sparse_weight", 0.25)
        self.graph_weight = ret_cfg.get("graph_weight", 0.2)
        self.top_k = ret_cfg.get("top_k", 30)
        self.rerank_top_k = ret_cfg.get("rerank_top_k", 15)
        self.expand_neighbors = ret_cfg.get("expand_neighbors", 3)
        self.expand_mode = (ret_cfg.get("expand_mode") or "beam").lower()
        self.beam_width = int(ret_cfg.get("beam_width") or 6)
        self.beam_depth = int(ret_cfg.get("beam_depth") or 2)
        self.ce_config = ret_cfg.get("cross_encoder", {})
        self.ce_enabled = self.ce_config.get("enabled", True)
        self.ce_model_name = self.ce_config.get("model", "cross-encoder/ms-marco-MiniLM-L-6-v2")

        self._bm25_index: Optional[BM25Okapi] = None
        self._bm25_chunks: List[Chunk] = []
        self._all_chunks: List[Chunk] = []

    def _bm25_persist_path(self) -> str:
        base = os.path.dirname(
            self.config.vector_store.get("persist_directory", ".code-harness/chromadb")
        )
        suffix = f"_{self.repo_name}" if self.repo_name else ""
        return os.path.join(base, f"bm25{suffix}.pkl")

    def index_chunks(self, chunks: List[Chunk], persist: bool = True):
        self._all_chunks = chunks
        if chunks:
            tokenized = [
                self._tokenize(c.content)
                for c in track(chunks, description="  Tokenizing BM25 index")
            ]
            self._bm25_index = BM25Okapi(tokenized)
            self._bm25_chunks = chunks
            if persist:
                try:
                    path = self._bm25_persist_path()
                    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                    with open(path, "wb") as f:
                        pickle.dump({
                            "tokenized": tokenized,
                            "chunks": [(c.id, c.content, c.entity_id, c.entity_name,
                                       c.entity_type.value, c.file_path,
                                       c.start_line, c.end_line, c.docstring,
                                       c.repo_name, c.metadata)
                                      for c in chunks],
                        }, f)
                except Exception:
                    pass

    def try_load_bm25(self):
        path = self._bm25_persist_path()
        if not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            self._bm25_index = BM25Okapi(data["tokenized"])
            chunks_data = data["chunks"]
            from .models import EntityType
            self._bm25_chunks = []
            for c in chunks_data:
                self._bm25_chunks.append(Chunk(
                    id=c[0], content=c[1], entity_id=c[2],
                    entity_name=c[3], entity_type=EntityType(c[4]),
                    file_path=c[5], start_line=c[6], end_line=c[7],
                    docstring=c[8], repo_name=c[9], metadata=c[10],
                ))
            self._all_chunks = self._bm25_chunks
            return True
        except Exception:
            return False

    def retrieve(self, query: str,
                 entity_id_map: Optional[Dict[str, str]] = None,
                 top_k: Optional[int] = None,
                 debug: bool = False,
                 mode: str = "hybrid",
                 hyde: Optional[bool] = None,
                 expand_neighbors: Optional[int] = None) -> List[RetrievalResult]:
        k = top_k or self.top_k
        latencies_ms: Dict[str, float] = {}
        mode = (mode or "hybrid").lower()
        prev_hyde = None
        if hyde is not None and getattr(self, "embedder", None) is not None:
            prev_hyde = getattr(self.embedder, "hyde_enabled", None)
            self.embedder.hyde_enabled = bool(hyde)
        neighbor_override = self.expand_neighbors if expand_neighbors is None else int(expand_neighbors)
        deepen = expand_neighbors is not None and neighbor_override > self.expand_neighbors

        if mode == "bm25":
            try:
                started = time.perf_counter()
                sparse_results = self._bm25_search(query, top_k=k * 3) if self._bm25_index else []
                latencies_ms["bm25"] = (time.perf_counter() - started) * 1000.0
                latencies_ms["dense"] = 0.0
                latencies_ms["graph"] = 0.0
                latencies_ms["ce"] = 0.0
                top = sparse_results[:k]
                if debug:
                    return top, {
                        "dense": [],
                        "sparse": sparse_results,
                        "graph": [],
                        "fused": list(sparse_results),
                        "reranked": [],
                        "latencies_ms": latencies_ms,
                        "mode": "bm25",
                    }
                return top
            finally:
                if prev_hyde is not None:
                    self.embedder.hyde_enabled = prev_hyde

        old_neighbors = self.expand_neighbors
        self.expand_neighbors = neighbor_override
        try:
            started = time.perf_counter()
            query_embedding = self.embedder.embed_query(query, expand=True)

            dense_results = self.vector_store.search(
                query_embedding, top_k=k * 2, repo_name=self.repo_name or None
            )
            latencies_ms["dense"] = (time.perf_counter() - started) * 1000.0

            started = time.perf_counter()
            sparse_results = self._bm25_search(query, top_k=k * 3) if self._bm25_index else []
            latencies_ms["bm25"] = (time.perf_counter() - started) * 1000.0

            started = time.perf_counter()
            graph_results = self._graph_search(
                query,
                dense_results + sparse_results,
                entity_id_map,
                deepen=deepen,
            )
            latencies_ms["graph"] = (time.perf_counter() - started) * 1000.0
        finally:
            self.expand_neighbors = old_neighbors
            if prev_hyde is not None:
                self.embedder.hyde_enabled = prev_hyde

        fused = self._reciprocal_rank_fusion(
            [dense_results, sparse_results],
            weights=[self.dense_weight, self.sparse_weight],
        )
        for r in fused:
            if (r.chunk.metadata or {}).get("kind") == "gloss":
                r.score = r.score + 0.12

        if graph_results and self.knowledge_graph.enabled:
            max_fused = max((r.score for r in fused), default=0)
            for r in fused:
                if any(gr.chunk.id == r.chunk.id for gr in graph_results):
                    r.score = r.score + self.graph_weight * max(max_fused, 0.1)
            seen_ids = {f.chunk.id for f in fused}
            for r in graph_results:
                if r.chunk.id not in seen_ids:
                    r.score = r.score * max_fused if max_fused > 0 else r.score * 0.5
                    fused.append(r)

        fused_pre_ce = list(fused)

        rerank_k = self.rerank_top_k
        started = time.perf_counter()
        if self.ce_enabled:
            reranked = self._cross_encoder_rerank(query, fused[:rerank_k * 2])
            tail = fused[rerank_k * 2:]
            fused = reranked + tail
        else:
            reranked = []
        latencies_ms["ce"] = (time.perf_counter() - started) * 1000.0

        fused.sort(key=lambda r: r.score, reverse=True)
        top = fused[:k]

        if debug:
            return top, {
                "dense": dense_results,
                "sparse": sparse_results,
                "graph": graph_results,
                "fused": fused_pre_ce,
                "reranked": reranked[:k] if self.ce_enabled else [],
                "latencies_ms": latencies_ms,
                "mode": mode,
            }
        return top

    def _cross_encoder_rerank(self, query: str,
                              results: List[RetrievalResult]) -> List[RetrievalResult]:
        if not results:
            return results

        global _cross_encoder_cache
        if self.ce_model_name not in _cross_encoder_cache:
            try:
                from sentence_transformers import CrossEncoder
                _cross_encoder_cache[self.ce_model_name] = CrossEncoder(
                    self.ce_model_name, trust_remote_code=True
                )
            except Exception:
                return results

        if not _cross_encoder_cache[self.ce_model_name]:
            return results

        model = _cross_encoder_cache[self.ce_model_name]
        pairs = [(query, r.chunk.content[:2048]) for r in results]
        try:
            scores = model.predict(pairs, show_progress_bar=False)
        except Exception:
            return results

        for r, score in zip(results, scores):
            r.score = float(score)
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _bm25_search(self, query: str, top_k: int = 20) -> List[RetrievalResult]:
        if not self._bm25_index:
            return []

        tokenized_query = self._tokenize(query)
        scores = self._bm25_index.get_scores(tokenized_query)

        results = []
        for i, score in enumerate(scores):
            if score > 0:
                normalized = float(1.0 / (1.0 + math.exp(-score / 5)))
                results.append(RetrievalResult(
                    chunk=self._bm25_chunks[i],
                    score=normalized,
                    source="sparse",
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def _graph_search(self, query: str,
                      existing: List[RetrievalResult],
                      entity_id_map: Optional[Dict[str, str]] = None,
                      deepen: bool = False) -> List[RetrievalResult]:
        if not self.knowledge_graph.enabled:
            return []

        query_lower = query.lower()
        query_tokens = set(self._tokenize(query))

        matched_entity_ids = set()
        for r in existing:
            matched_entity_ids.add(r.chunk.entity_id)
            if entity_id_map and r.chunk.entity_id in entity_id_map:
                matched_entity_ids.add(entity_id_map[r.chunk.entity_id])
        if deepen:
            for chunk in self._all_chunks:
                name = (chunk.entity_name or "").lower()
                if name and (name in query_lower or name in query_tokens):
                    matched_entity_ids.add(chunk.entity_id)

        seed_scores: Dict[str, float] = {}
        for r in existing:
            eid = r.chunk.entity_id
            seed_scores[eid] = max(seed_scores.get(eid, 0.0), float(r.score or 0.0))
        for eid in matched_entity_ids:
            seed_scores.setdefault(eid, 0.35)

        if self.expand_mode == "bfs":
            graph_ids = self.knowledge_graph.get_related_chunks(
                list(matched_entity_ids), max_depth=self.expand_neighbors
            )
        else:
            width = self.beam_width
            depth = self.beam_depth
            max_added = max(self.expand_neighbors * 2, width)
            if deepen:
                width = max(width, self.expand_neighbors)
                depth = max(depth, 2)
                max_added = max(max_added, self.expand_neighbors * 2)
            graph_ids = self.knowledge_graph.expand_beam(
                seed_scores,
                width=width,
                depth=depth,
                max_added=max_added,
            )

        if not graph_ids:
            return []

        graph_chunks = {c.entity_id: c for c in self._all_chunks}
        results = []
        for gid in graph_ids:
            if gid in graph_chunks:
                chunk = graph_chunks[gid]
                chunk_tokens = set(self._tokenize(chunk.content))
                overlap = len(query_tokens & chunk_tokens)
                score = min(0.8, 0.3 + (overlap / max(len(query_tokens), 1)) * 0.5)
                if (chunk.metadata or {}).get("kind") == "gloss":
                    score = min(0.95, score + 0.15)
                results.append(RetrievalResult(
                    chunk=chunk, score=score, source="graph"
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:self.top_k]

    @staticmethod
    def _reciprocal_rank_fusion(
        result_lists: List[List[RetrievalResult]],
        weights: Optional[List[float]] = None,
        k: float = 60
    ) -> List[RetrievalResult]:
        scores = defaultdict(float)
        chunk_map = {}

        for rank_list, weight in zip(result_lists, weights or [1.0] * len(result_lists)):
            for rank, result in enumerate(rank_list):
                chunk_id = result.chunk.id
                scores[chunk_id] += weight * (1.0 / (k + rank + 1))
                chunk_map[chunk_id] = result

        fused = []
        for chunk_id, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            result = chunk_map[chunk_id]
            fused.append(RetrievalResult(
                chunk=result.chunk,
                score=float(score),
                source="fused",
            ))

        return fused

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        text = text.lower()
        tokens = re.findall(r'[a-z0-9_]+', text)
        return tokens
