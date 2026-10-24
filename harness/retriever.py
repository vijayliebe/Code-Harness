import math
import os
import pickle
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
from .okf import is_memory_path, is_wiki_path
from .cli_retrieval import (
    argparse_catalog_from_chunks,
    cli_retrieval_cfg,
    index_text_for_chunk,
    infer_cli_symbols,
    make_cli_doc_chunks,
    tokenize,
)


def is_wiki_chunk(chunk) -> bool:
    """True for wiki vault chunks (``kind=wiki`` or ``knowledge/wiki/...``)."""
    if chunk is None:
        return False
    meta = getattr(chunk, "metadata", None) or {}
    if str(meta.get("kind") or "").lower() == "wiki":
        return True
    source = str(meta.get("source") or "").strip().lower()
    if source in {"wiki", "knowledge/wiki"}:
        return True
    return is_wiki_path(getattr(chunk, "file_path", None) or "")


def is_memory_chunk(chunk) -> bool:
    """True for typed memory hits (``kind=memory`` or ``knowledge/memory/...``)."""
    if chunk is None:
        return False
    meta = getattr(chunk, "metadata", None) or {}
    if str(meta.get("kind") or "").lower() == "memory":
        return True
    if str(meta.get("memory_kind") or "").strip():
        return True
    return is_memory_path(getattr(chunk, "file_path", None) or "")

_cross_encoder_cache = {}


def _score_desc_id(result) -> tuple:
    """Stable rank key: higher score first, then chunk id for ties."""
    chunk = getattr(result, "chunk", None)
    cid = getattr(chunk, "id", "") if chunk is not None else ""
    return (-float(getattr(result, "score", 0.0) or 0.0), cid or "")


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
        # Ablation-only skips. Absent keys keep the default hybrid path.
        self.dense_enabled = ret_cfg.get("dense_enabled", True)
        self.sparse_enabled = ret_cfg.get("sparse_enabled", True)
        self.graph_enabled = ret_cfg.get("graph_enabled", True)
        self.wiki_weight = float(ret_cfg.get("wiki_weight", 0.0) or 0.0)
        self.memory_weight = float(ret_cfg.get("memory_weight", 0.0) or 0.0)
        try:
            from .wiki_chat import WIKI_MODE_WEIGHT, wiki_mode_enabled

            if wiki_mode_enabled(config) and self.wiki_weight <= 0:
                self.wiki_weight = WIKI_MODE_WEIGHT
        except Exception:
            pass
        ctx = getattr(config, "context", None) or {}
        if ctx.get("include_memory_search") and self.memory_weight <= 0:
            try:
                from .memory import MEMORY_SEARCH_WEIGHT

                self.memory_weight = float(MEMORY_SEARCH_WEIGHT)
            except Exception:
                self.memory_weight = 0.15
        self.top_k = ret_cfg.get("top_k", 30)
        self.rerank_top_k = ret_cfg.get("rerank_top_k", 15)
        self.expand_neighbors = ret_cfg.get("expand_neighbors", 3)
        self.expand_mode = (ret_cfg.get("expand_mode") or "beam").lower()
        self.beam_width = int(ret_cfg.get("beam_width") or 6)
        self.beam_depth = int(ret_cfg.get("beam_depth") or 2)
        self.ce_config = dict(ret_cfg.get("cross_encoder") or {})
        self.ce_enabled = self.ce_config.get("enabled", True)
        self.ce_model_name = self.ce_config.get("model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        cli_cfg = cli_retrieval_cfg(config)
        self.ident_split = bool(cli_cfg.get("ident_split"))
        self.argparse_enrich = bool(cli_cfg.get("argparse_enrich"))
        self.cli_doc_index = bool(cli_cfg.get("doc_index"))
        try:
            self.cli_symbol_boost = float(cli_cfg.get("symbol_boost") or 0.0)
        except (TypeError, ValueError):
            self.cli_symbol_boost = 0.0
        self._argparse_catalog: Dict[str, Dict[str, str]] = {}
        self._cli_doc_chunks: List[Chunk] = []

        self._bm25_index: Optional[BM25Okapi] = None
        self._bm25_chunks: List[Chunk] = []
        self._all_chunks: List[Chunk] = []

    def _bm25_persist_path(self) -> str:
        base = os.path.dirname(
            self.config.vector_store.get("persist_directory", ".code-harness/chromadb")
        )
        suffix = f"_{self.repo_name}" if self.repo_name else ""
        return os.path.join(base, f"bm25{suffix}.pkl")

    def _index_text(self, chunk: Chunk) -> str:
        return index_text_for_chunk(
            chunk,
            self._argparse_catalog,
            argparse_enrich=self.argparse_enrich,
        )

    def _prepare_cli_index(self, chunks: List[Chunk]) -> List[Chunk]:
        catalog: Dict[str, Dict[str, str]] = {}
        if self.argparse_enrich or self.cli_doc_index or self.cli_symbol_boost > 0:
            catalog = argparse_catalog_from_chunks(chunks)
        self._argparse_catalog = catalog
        docs: List[Chunk] = []
        if self.cli_doc_index:
            docs = make_cli_doc_chunks(chunks, catalog)
        self._cli_doc_chunks = docs
        if not docs:
            return list(chunks)
        seen = {c.id for c in chunks}
        extra = [d for d in docs if d.id not in seen]
        return list(chunks) + extra

    def index_chunks(self, chunks: List[Chunk], persist: bool = True):
        prepared = self._prepare_cli_index(chunks)
        self._all_chunks = prepared
        if prepared:
            tokenized = [
                self._tokenize(self._index_text(c))
                for c in track(prepared, description="  Tokenizing BM25 index")
            ]
            self._bm25_index = BM25Okapi(tokenized)
            self._bm25_chunks = prepared
            if persist:
                try:
                    path = self._bm25_persist_path()
                    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                    # Disk cache stays vanilla so a later query with flags
                    # off does not inherit enriched tokens or synthetic docs.
                    vanilla = [tokenize(c.content, ident_split=False) for c in chunks]
                    with open(path, "wb") as f:
                        pickle.dump({
                            "tokenized": vanilla,
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
            chunks_data = data["chunks"]
            from .models import EntityType
            loaded: List[Chunk] = []
            for c in chunks_data:
                loaded.append(Chunk(
                    id=c[0], content=c[1], entity_id=c[2],
                    entity_name=c[3], entity_type=EntityType(c[4]),
                    file_path=c[5], start_line=c[6], end_line=c[7],
                    docstring=c[8], repo_name=c[9], metadata=c[10],
                ))
            rebuild = self.ident_split or self.argparse_enrich or self.cli_doc_index
            if rebuild:
                self.index_chunks(loaded, persist=False)
                return True
            self._bm25_index = BM25Okapi(data["tokenized"])
            self._bm25_chunks = loaded
            self._all_chunks = loaded
            self._prepare_cli_index(loaded)
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
                if self.sparse_enabled and self._bm25_index:
                    sparse_results = self._bm25_search(query, top_k=k * 3)
                else:
                    sparse_results = []
                latencies_ms["bm25"] = (time.perf_counter() - started) * 1000.0
                latencies_ms["dense"] = 0.0
                latencies_ms["graph"] = 0.0
                latencies_ms["wiki"] = 0.0
                latencies_ms["memory"] = 0.0
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
            dense_results: List[RetrievalResult] = []
            sparse_results: List[RetrievalResult] = []
            graph_results: List[RetrievalResult] = []
            started = time.perf_counter()
            query_embedding = None
            if self.dense_enabled:
                query_embedding = self.embedder.embed_query(query, expand=True)

            use_allowlist = bool(getattr(self.vector_store, "supports_allowlist", False))
            if use_allowlist and self.dense_enabled:
                embed_ms = (time.perf_counter() - started) * 1000.0
                started_sparse = time.perf_counter()
                if self.sparse_enabled and self._bm25_index:
                    sparse_results = self._bm25_search(query, top_k=max(k * 3, 200))
                latencies_ms["bm25"] = (time.perf_counter() - started_sparse) * 1000.0
                allowlist = [r.chunk.id for r in sparse_results[:200] if getattr(r.chunk, "id", None)]
                allow_arg = allowlist if len(allowlist) >= 20 else None
                started_dense = time.perf_counter()
                dense_results = self.vector_store.search(
                    query_embedding,
                    top_k=k * 2,
                    repo_name=self.repo_name or None,
                    allowlist_ids=allow_arg,
                )
                latencies_ms["dense"] = embed_ms + (time.perf_counter() - started_dense) * 1000.0
            else:
                if self.dense_enabled:
                    dense_results = self.vector_store.search(
                        query_embedding, top_k=k * 2, repo_name=self.repo_name or None
                    )
                    latencies_ms["dense"] = (time.perf_counter() - started) * 1000.0
                else:
                    latencies_ms["dense"] = 0.0

                started = time.perf_counter()
                if self.sparse_enabled and self._bm25_index:
                    sparse_results = self._bm25_search(query, top_k=k * 3)
                latencies_ms["bm25"] = (
                    (time.perf_counter() - started) * 1000.0 if self.sparse_enabled else 0.0
                )

            if self.graph_enabled and self.expand_neighbors > 0:
                started = time.perf_counter()
                graph_results = self._graph_search(
                    query,
                    dense_results + sparse_results,
                    entity_id_map,
                    deepen=deepen,
                )
                latencies_ms["graph"] = (time.perf_counter() - started) * 1000.0
            else:
                latencies_ms["graph"] = 0.0
        finally:
            self.expand_neighbors = old_neighbors
            if prev_hyde is not None:
                self.embedder.hyde_enabled = prev_hyde

        wiki_results: List[RetrievalResult] = []
        if self.wiki_weight > 0:
            started = time.perf_counter()
            wiki_results = self._wiki_search(query, top_k=k * 2)
            latencies_ms["wiki"] = (time.perf_counter() - started) * 1000.0
        else:
            latencies_ms["wiki"] = 0.0

        memory_results: List[RetrievalResult] = []
        if self.memory_weight > 0:
            started = time.perf_counter()
            memory_results = self._memory_search(query, top_k=k * 2)
            latencies_ms["memory"] = (time.perf_counter() - started) * 1000.0
        else:
            latencies_ms["memory"] = 0.0

        symbol_results: List[RetrievalResult] = []
        if self.cli_symbol_boost > 0:
            symbol_results = self._cli_symbol_search(query)
        doc_results: List[RetrievalResult] = []
        if self.cli_doc_index:
            doc_results = self._cli_doc_search(query, top_k=k * 2)

        fuse_lists = [dense_results, sparse_results]
        fuse_weights = [self.dense_weight, self.sparse_weight]
        if wiki_results:
            fuse_lists.append(wiki_results)
            fuse_weights.append(self.wiki_weight)
        if memory_results:
            fuse_lists.append(memory_results)
            fuse_weights.append(self.memory_weight)
        if symbol_results:
            fuse_lists.append(symbol_results)
            fuse_weights.append(self.cli_symbol_boost)
        if doc_results:
            fuse_lists.append(doc_results)
            fuse_weights.append(self.sparse_weight)
        fused = self._reciprocal_rank_fusion(fuse_lists, weights=fuse_weights)
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

        fused.sort(key=_score_desc_id)
        top = fused[:k]
        try:
            from .wiki_chat import prefer_wiki_results, wiki_mode_enabled

            if wiki_mode_enabled(self.config):
                top = prefer_wiki_results(top)
        except Exception:
            pass

        if debug:
            return top, {
                "dense": dense_results,
                "sparse": sparse_results,
                "graph": graph_results,
                "wiki": wiki_results,
                "memory": memory_results,
                "fused": fused_pre_ce,
                "reranked": reranked[:k] if self.ce_enabled else [],
                "latencies_ms": latencies_ms,
                "mode": mode,
            }
        return top

    def _ce_input_text(self, chunk) -> str:
        content = getattr(chunk, "content", None) or ""
        kind = str((self.ce_config or {}).get("input") or "content").strip().lower()
        if kind in ("path_symbol", "path_symbol_text", "path+symbol+text"):
            path = getattr(chunk, "file_path", None) or ""
            name = getattr(chunk, "entity_name", None) or ""
            return f"{path} {name}\n{content}"
        return content

    @staticmethod
    def _minmax(values: List[float]) -> List[float]:
        if not values:
            return []
        lo, hi = min(values), max(values)
        if hi - lo < 1e-12:
            return [0.5] * len(values)
        return [(v - lo) / (hi - lo) for v in values]

    def _cross_encoder_rerank(self, query: str,
                              results: List[RetrievalResult]) -> List[RetrievalResult]:
        if not results:
            return results
        if not self.ce_enabled:
            return results
        mode = str((self.ce_config or {}).get("mode") or "replace").strip().lower()
        if mode in ("off", "disabled", "none"):
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
        fused_scores = [float(r.score or 0.0) for r in results]
        pairs = [(query, self._ce_input_text(r.chunk)[:2048]) for r in results]
        try:
            raw = model.predict(pairs, show_progress_bar=False)
        except Exception:
            return results
        ce_scores = [float(score) for score in raw]

        if mode == "gate":
            raw_margin = (self.ce_config or {}).get("gate_margin")
            try:
                threshold = float(raw_margin) if raw_margin is not None else 0.0
            except (TypeError, ValueError):
                threshold = 0.0
            ranked = sorted(ce_scores, reverse=True)
            margin = ranked[0] - ranked[1] if len(ranked) > 1 else abs(ranked[0])
            if margin < threshold:
                return results
            mode = "replace"

        if mode == "blend":
            try:
                alpha = float((self.ce_config or {}).get("blend_alpha") or 0.5)
            except (TypeError, ValueError):
                alpha = 0.5
            alpha = max(0.0, min(1.0, alpha))
            n_ce = self._minmax(ce_scores)
            n_rrf = self._minmax(fused_scores)
            for result, ce_n, rrf_n in zip(results, n_ce, n_rrf):
                result.score = alpha * ce_n + (1.0 - alpha) * rrf_n
            results.sort(key=_score_desc_id)
            return results

        for result, score in zip(results, ce_scores):
            result.score = score
        results.sort(key=_score_desc_id)
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

        results.sort(key=_score_desc_id)
        return results[:top_k]

    def _wiki_search(self, query: str, top_k: int = 20) -> List[RetrievalResult]:
        if self.wiki_weight <= 0 or not self._all_chunks:
            return []
        query_tokens = set(self._tokenize(query))
        if not query_tokens:
            return []
        results = []
        for chunk in self._all_chunks:
            if not is_wiki_chunk(chunk):
                continue
            tokens = set(self._tokenize(chunk.content))
            overlap = len(query_tokens & tokens)
            if overlap <= 0:
                continue
            score = min(0.9, 0.25 + (overlap / max(len(query_tokens), 1)) * 0.65)
            results.append(RetrievalResult(chunk=chunk, score=score, source="wiki"))
        results.sort(key=_score_desc_id)
        return results[:top_k]

    def _cli_symbol_search(self, query: str) -> List[RetrievalResult]:
        known = {
            getattr(c, "entity_name", None) or ""
            for c in (self._all_chunks or [])
        }
        known.discard("")
        names = infer_cli_symbols(query, known)
        if not names or not self._all_chunks:
            return []
        wanted = {n.lower() for n in names}
        hits: List[RetrievalResult] = []
        seen = set()
        for chunk in self._all_chunks:
            name = (getattr(chunk, "entity_name", None) or "")
            if name.lower() not in wanted:
                continue
            if (chunk.metadata or {}).get("kind") == "cli_doc":
                continue
            key = chunk.id
            if key in seen:
                continue
            seen.add(key)
            hits.append(RetrievalResult(chunk=chunk, score=0.9, source="cli_symbol"))
        hits.sort(key=_score_desc_id)
        return hits

    def _cli_doc_search(self, query: str, top_k: int = 20) -> List[RetrievalResult]:
        docs = self._cli_doc_chunks or [
            c for c in (self._all_chunks or [])
            if (c.metadata or {}).get("kind") == "cli_doc"
        ]
        if not docs:
            return []
        query_tokens = set(self._tokenize(query))
        if not query_tokens:
            return []
        results = []
        for chunk in docs:
            tokens = set(self._tokenize(self._index_text(chunk)))
            overlap = len(query_tokens & tokens)
            if overlap <= 0:
                continue
            score = min(0.95, 0.3 + (overlap / max(len(query_tokens), 1)) * 0.65)
            results.append(RetrievalResult(chunk=chunk, score=score, source="cli_doc"))
        results.sort(key=_score_desc_id)
        return results[:top_k]

    def _memory_search(self, query: str, top_k: int = 20) -> List[RetrievalResult]:
        if self.memory_weight <= 0:
            return []
        try:
            from .memory import MemoryStore, memory_entry_to_chunk
        except Exception:
            return []
        ctx = getattr(self.config, "context", None) or {}
        store = MemoryStore.for_repo(
            getattr(self.config, "repo_path", None) or ".",
            memory_dir=ctx.get("memory_dir") or None,
        )
        hits = store.search(query, top_k=top_k)
        results = []
        for hit in hits:
            raw = float(hit.score or 0.0)
            if raw <= 0:
                continue
            normalized = float(1.0 / (1.0 + math.exp(-raw / 5)))
            results.append(
                RetrievalResult(
                    chunk=memory_entry_to_chunk(hit.entry, repo_name=self.repo_name),
                    score=normalized,
                    source="memory",
                )
            )
        return results

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

        results.sort(key=_score_desc_id)
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
        for chunk_id, score in sorted(scores.items(), key=lambda x: (-x[1], x[0])):
            result = chunk_map[chunk_id]
            fused.append(RetrievalResult(
                chunk=result.chunk,
                score=float(score),
                source="fused",
            ))

        return fused

    def _tokenize(self, text: str) -> List[str]:
        return tokenize(text, ident_split=bool(getattr(self, "ident_split", False)))
