import math
import os
import pickle
import re
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set

from rank_bm25 import BM25Okapi
from rich.progress import track

from .models import Chunk, EntityType, RetrievalResult


def _external_doc_chunk(doc, repo_name: str = "") -> Chunk:
    body = doc.body() if hasattr(doc, "body") else ""
    tickets = []
    try:
        tickets = list(doc.ticket_keys() or [])
    except Exception:
        tickets = []
    return Chunk(
        id=f"external:{doc.source}:{doc.id}",
        content=body or (getattr(doc, "title", "") or ""),
        entity_id=f"external:{doc.source}:{doc.id}",
        entity_name=getattr(doc, "title", None) or doc.id,
        entity_type=EntityType.DOCUMENTATION,
        file_path=getattr(doc, "url", "") or doc.id,
        start_line=1,
        end_line=max(1, (body or "").count("\n") + 1),
        repo_name=repo_name,
        metadata={
            "kind": "external",
            "source": getattr(doc, "source", "") or "file",
            "tickets": tickets,
        },
    )
from .config import Config
from .embedder import Embedder
from .index_text import chunk_index_text
from .vector_store import VectorStore
from .knowledge_graph import KnowledgeGraph
from .okf import is_memory_path, is_wiki_path


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
        self.wiki_weight = float(ret_cfg.get("wiki_weight", 0.0) or 0.0)
        self.memory_weight = float(ret_cfg.get("memory_weight", 0.0) or 0.0)
        ext_cfg = getattr(config, "external", None) or {}
        self.external_enabled = bool(ext_cfg.get("enabled"))
        self.external_weight = float(ext_cfg.get("weight", 0.0) or 0.0)
        if not self.external_enabled:
            self.external_weight = 0.0
        self.ticket_lookup = bool(ext_cfg.get("ticket_lookup", True))
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
        self.ce_config = ret_cfg.get("cross_encoder", {})
        self.ce_enabled = self.ce_config.get("enabled", True)
        self.ce_model_name = self.ce_config.get("model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        self.skip_expansion = False

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
                self._tokenize(chunk_index_text(c, self.config))
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
                 expand_neighbors: Optional[int] = None,
                 skip_expansion: Optional[bool] = None) -> List[RetrievalResult]:
        k = top_k or self.top_k
        latencies_ms: Dict[str, float] = {}
        mode = (mode or "hybrid").lower()
        skip = self.skip_expansion if skip_expansion is None else bool(skip_expansion)
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
                latencies_ms["wiki"] = 0.0
                latencies_ms["memory"] = 0.0
                latencies_ms["external"] = 0.0
                latencies_ms["ticket"] = 0.0
                latencies_ms["ce"] = 0.0
                top = self._maybe_expand(query, sparse_results[:k])
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
            variants = self._query_variants(query, skip_expansion=skip)
            started = time.perf_counter()
            dense_results, sparse_results, variant_lists, variant_weights = self._search_variants(
                variants, k=k, expand=not skip
            )
            latencies_ms["dense"] = (time.perf_counter() - started) * 1000.0
            latencies_ms["bm25"] = 0.0
            if len(variants) > 1:
                latencies_ms["multi_query"] = latencies_ms["dense"]

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

        started = time.perf_counter()
        ticket_results = self._ticket_search(query, top_k=k * 2)
        latencies_ms["ticket"] = (time.perf_counter() - started) * 1000.0

        external_results: List[RetrievalResult] = []
        if self.external_weight > 0:
            started = time.perf_counter()
            external_results = self._external_search(query, top_k=k * 2)
            latencies_ms["external"] = (time.perf_counter() - started) * 1000.0
        else:
            latencies_ms["external"] = 0.0

        if len(variant_lists) > 1:
            mq = (self.config.retrieval or {}).get("multi_query") or {}
            rrf_k = float(mq.get("rrf_k") or 50)
            fused = self._reciprocal_rank_fusion(
                variant_lists, weights=variant_weights, k=rrf_k
            )
            extra_lists = []
            extra_weights = []
            if wiki_results:
                extra_lists.append(wiki_results)
                extra_weights.append(self.wiki_weight)
            if memory_results:
                extra_lists.append(memory_results)
                extra_weights.append(self.memory_weight)
            if external_results:
                extra_lists.append(external_results)
                extra_weights.append(self.external_weight)
            if extra_lists:
                fused = self._reciprocal_rank_fusion(
                    [fused] + extra_lists,
                    weights=[1.0] + extra_weights,
                    k=rrf_k,
                )
        else:
            fuse_lists = [dense_results, sparse_results]
            fuse_weights = [self.dense_weight, self.sparse_weight]
            if wiki_results:
                fuse_lists.append(wiki_results)
                fuse_weights.append(self.wiki_weight)
            if memory_results:
                fuse_lists.append(memory_results)
                fuse_weights.append(self.memory_weight)
            if external_results:
                fuse_lists.append(external_results)
                fuse_weights.append(self.external_weight)
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

        if ticket_results:
            max_fused = max((r.score for r in fused), default=0)
            seen_ids = {f.chunk.id for f in fused}
            for r in ticket_results:
                if r.chunk.id in seen_ids:
                    for existing in fused:
                        if existing.chunk.id == r.chunk.id:
                            existing.score = existing.score + 0.25 * max(max_fused, 0.1)
                            break
                else:
                    r.score = max(r.score, 0.35 + 0.2 * max(max_fused, 0.1))
                    fused.append(r)
                    seen_ids.add(r.chunk.id)

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
        try:
            from .wiki_chat import prefer_wiki_results, wiki_mode_enabled

            if wiki_mode_enabled(self.config):
                top = prefer_wiki_results(top)
        except Exception:
            pass
        top = self._maybe_expand(query, top)

        if debug:
            return top, {
                "dense": dense_results,
                "sparse": sparse_results,
                "graph": graph_results,
                "wiki": wiki_results,
                "memory": memory_results,
                "ticket": ticket_results,
                "external": external_results,
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
        results.sort(key=lambda r: r.score, reverse=True)
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

    def _ticket_search(self, query: str, top_k: int = 20) -> List[RetrievalResult]:
        """Surface KG-linked code and ingested docs when the query names a ticket."""
        if not self.ticket_lookup:
            return []
        try:
            from .external.linking import (
                extract_ticket_keys,
                linked_entity_ids_for_ticket,
                linked_paths_for_ticket,
            )
        except Exception:
            return []
        keys = extract_ticket_keys(query or "")
        if not keys:
            return []
        results: List[RetrievalResult] = []
        seen = set()
        path_hits = set()
        for key in keys:
            for eid in linked_entity_ids_for_ticket(self.knowledge_graph, key):
                for chunk in self._all_chunks:
                    if chunk.entity_id == eid and chunk.id not in seen:
                        results.append(RetrievalResult(chunk=chunk, score=0.72, source="ticket"))
                        seen.add(chunk.id)
                        path_hits.add((chunk.file_path or "").replace("\\", "/"))
            for path in linked_paths_for_ticket(self.knowledge_graph, key):
                norm = path.replace("\\", "/")
                if norm in path_hits:
                    continue
                for chunk in self._all_chunks:
                    if (chunk.file_path or "").replace("\\", "/") == norm and chunk.id not in seen:
                        results.append(RetrievalResult(chunk=chunk, score=0.62, source="ticket"))
                        seen.add(chunk.id)
                        path_hits.add(norm)
            try:
                from .external.store import ExternalStore

                store = ExternalStore.for_config(self.config)
                for doc in store.docs_for_ticket(key):
                    chunk = _external_doc_chunk(doc, self.repo_name)
                    if chunk.id in seen:
                        continue
                    results.append(RetrievalResult(chunk=chunk, score=0.68, source="ticket"))
                    seen.add(chunk.id)
            except Exception:
                continue
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def _external_search(self, query: str, top_k: int = 20) -> List[RetrievalResult]:
        if self.external_weight <= 0:
            return []
        try:
            from .external.store import ExternalStore
        except Exception:
            return []
        store = ExternalStore.for_config(self.config)
        results = []
        for doc, raw in store.search(query, top_k=top_k):
            if raw <= 0:
                continue
            normalized = float(1.0 / (1.0 + math.exp(-raw / 5)))
            results.append(
                RetrievalResult(
                    chunk=_external_doc_chunk(doc, self.repo_name),
                    score=normalized,
                    source="external",
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

    def _query_variants(self, query: str, skip_expansion: bool = False) -> List[tuple]:
        """Original + identifier + free HyDE template. Dedupe case-fold, sum weights."""
        mq = (self.config.retrieval or {}).get("multi_query") or {}
        enabled = bool(mq.get("enabled")) and not skip_expansion
        original_w = float(mq.get("original_weight") or 1.0)
        variants = [("original", query, original_w)]
        if not enabled:
            return variants
        from .loop import extract_focus_terms

        ident = " ".join(extract_focus_terms(query) or [])
        ident_w = float(mq.get("identifier_weight") or 1.0)
        if ident.strip() and ident.strip().lower() != (query or "").strip().lower():
            variants.append(("identifier", ident, ident_w))
        hyde_w = float(mq.get("hyde_weight") or 0.7)
        hyde_text = ""
        if getattr(self, "embedder", None) is not None:
            try:
                hyde_text = self.embedder._generate_hypothetical(query)
            except Exception:
                hyde_text = ""
        if hyde_text.strip() and hyde_text.strip().lower() != (query or "").strip().lower():
            variants.append(("hyde", hyde_text, hyde_w))
        return self._dedupe_query_variants(variants)

    @staticmethod
    def _dedupe_query_variants(variants: List[tuple]) -> List[tuple]:
        merged: Dict[str, list] = {}
        order = []
        for name, text, weight in variants:
            key = (text or "").strip().lower()
            if not key:
                continue
            if key not in merged:
                merged[key] = [name, text, float(weight)]
                order.append(key)
            else:
                merged[key][2] += float(weight)
        return [(merged[key][0], merged[key][1], merged[key][2]) for key in order]

    def _search_variants(self, variants: List[tuple], k: int, expand: bool = True):
        """Run dense + BM25 per variant. First variant is the displayed dense/sparse."""
        dense_results: List[RetrievalResult] = []
        sparse_results: List[RetrievalResult] = []
        variant_lists: List[List[RetrievalResult]] = []
        variant_weights: List[float] = []
        use_allowlist = bool(getattr(self.vector_store, "supports_allowlist", False))
        for idx, (_name, text, weight) in enumerate(variants):
            expand_this = expand if idx == 0 else False
            query_embedding = self.embedder.embed_query(text, expand=expand_this)
            if use_allowlist:
                sparse = self._bm25_search(text, top_k=max(k * 3, 200)) if self._bm25_index else []
                allowlist = [r.chunk.id for r in sparse[:200] if getattr(r.chunk, "id", None)]
                allow_arg = allowlist if len(allowlist) >= 20 else None
                dense = self.vector_store.search(
                    query_embedding,
                    top_k=k * 2,
                    repo_name=self.repo_name or None,
                    allowlist_ids=allow_arg,
                )
            else:
                dense = self.vector_store.search(
                    query_embedding, top_k=k * 2, repo_name=self.repo_name or None
                )
                sparse = self._bm25_search(text, top_k=k * 3) if self._bm25_index else []
            hybrid = self._reciprocal_rank_fusion(
                [dense, sparse], weights=[self.dense_weight, self.sparse_weight]
            )
            variant_lists.append(hybrid)
            variant_weights.append(float(weight))
            if idx == 0:
                dense_results = dense
                sparse_results = sparse
        return dense_results, sparse_results, variant_lists, variant_weights

    def _maybe_expand(self, query: str, results: List[RetrievalResult]) -> List[RetrievalResult]:
        from .expansion import apply_expansion, expansion_enabled

        if not expansion_enabled(self.config) or not results:
            return results
        decider = None
        try:
            from .decision import build_decision_client

            decider = build_decision_client(self.config)
        except Exception:
            decider = None
        return apply_expansion(
            query,
            results,
            all_chunks=self._all_chunks,
            knowledge_graph=self.knowledge_graph,
            config=self.config,
            decider=decider,
        )
