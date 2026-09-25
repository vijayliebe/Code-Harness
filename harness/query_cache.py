"""Local-first query-hash cache for retrieve + pack.

Serve already used a SQLite sidecar. Interactive ``query`` / ``chat`` and
``eval`` share the same keying and optional on-disk table so identical
(index fingerprint + query + knobs) lookups reuse packs/results.
Default is **off** for query/chat/eval (``--query-cache`` /
``CODEHARNESS_QUERY_CACHE=1``). Serve keeps its own default-on ``--no-cache``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import weakref
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import Config


_LIVE: List[weakref.ref] = []
_FALSEY = frozenset({"0", "false", "off", "no"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def normalize_query(query: str) -> str:
    return " ".join((query or "").split())


class QueryCache:
    """In-process map with optional SQLite sidecar."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.hits = 0
        self.misses = 0
        self._mem: Dict[str, Dict[str, Any]] = {}
        self._conn: Optional[sqlite3.Connection] = None
        if path:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(path)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS retrieve_cache ("
                " cache_key TEXT PRIMARY KEY,"
                " payload TEXT NOT NULL,"
                " created_at TEXT NOT NULL)"
            )
            self._conn.commit()
        _LIVE.append(weakref.ref(self))

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        if not key:
            return None
        if key in self._mem:
            return self._mem[key]
        if self._conn is None:
            return None
        row = self._conn.execute(
            "SELECT payload FROM retrieve_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row[0])
        except json.JSONDecodeError:
            return None
        self._mem[key] = payload
        return payload

    def put(self, key: str, payload: Dict[str, Any]) -> None:
        if not key:
            return
        stored = dict(payload)
        self._mem[key] = stored
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO retrieve_cache(cache_key, payload, created_at) "
            "VALUES (?, ?, ?)",
            (
                key,
                json.dumps(stored, ensure_ascii=False),
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )
        self._conn.commit()

    def invalidate(self) -> None:
        self._mem.clear()
        if self._conn is not None:
            self._conn.execute("DELETE FROM retrieve_cache")
            self._conn.commit()

    def stats(self) -> Dict[str, int]:
        return {"hits": int(self.hits), "misses": int(self.misses)}


def notify_retrieval_changed(repo_path: Optional[str] = None) -> None:
    """Bust caches after reindex or a vault memory write that can change retrieve."""
    _bump_epoch(repo_path)
    alive: List[weakref.ref] = []
    for ref in _LIVE:
        cache = ref()
        if cache is None:
            continue
        alive.append(ref)
        try:
            cache.invalidate()
        except Exception:
            pass
    _LIVE[:] = alive


def query_cache_enabled(args=None, environ=None, config: Optional[Config] = None) -> bool:
    """Query/chat/eval default off. ``--no-query-cache`` / env ``0`` always wins."""
    env = environ if environ is not None else os.environ
    raw = str(env.get("CODEHARNESS_QUERY_CACHE") or "").strip().lower()
    if args is not None and getattr(args, "no_query_cache", False):
        return False
    if raw in _FALSEY:
        return False
    if args is not None and getattr(args, "query_cache", False):
        return True
    if raw in _TRUTHY:
        return True
    if config is not None:
        qc = getattr(config, "query_cache", None) or {}
        if "enabled" in qc:
            return bool(qc.get("enabled"))
    return False


def resolve_cache_path(args=None, config: Optional[Config] = None) -> str:
    repo = "."
    if config is not None:
        repo = getattr(config, "repo_path", None) or "."
    path = None
    if args is not None:
        path = getattr(args, "cache_path", None)
    qc = getattr(config, "query_cache", None) or {} if config is not None else {}
    serve = getattr(config, "serve", None) or {} if config is not None else {}
    path = path or qc.get("path") or serve.get("cache_path") or ".code-harness/query_cache.sqlite"
    if not os.path.isabs(path):
        path = os.path.join(repo, path)
    return path


def resolve_query_cache(args=None, config: Optional[Config] = None, environ=None):
    if not query_cache_enabled(args=args, environ=environ, config=config):
        return None
    return QueryCache(resolve_cache_path(args, config))


def index_fingerprint(config: Config, repo_path: str, repo_name: str) -> str:
    parts: List[str] = []
    from .vector_store import normalize_backend_name, resolved_persist_directory

    persist = resolved_persist_directory(config)
    if not os.path.isabs(persist):
        persist = os.path.join(repo_path or ".", persist)
    kind = normalize_backend_name((config.vector_store or {}).get("type", "chromadb"))
    sqlite = os.path.join(persist, "chroma.sqlite3")
    if os.path.isfile(sqlite):
        parts.append(f"chroma:{_file_stamp(sqlite)}")
    for name in ("index.tvim", "sidecar.json", "store.json", "manifest.json"):
        path = os.path.join(persist, name)
        if os.path.isfile(path):
            parts.append(f"{kind}:{name}:{_file_stamp(path)}")
            break
    from .knowledge_graph import KnowledgeGraph

    kg_path = KnowledgeGraph(config, repo_name=repo_name).persist_path
    if os.path.isfile(kg_path):
        parts.append(f"graph:{_file_stamp(kg_path)}")
    parts.append(f"epoch:{_read_epoch(repo_path)}")
    ret = getattr(config, "retrieval", None) or {}
    ctx = getattr(config, "context", None) or {}
    chat = getattr(config, "chat", None) or {}
    if float(ret.get("wiki_weight", 0.0) or 0.0) or chat.get("wiki_mode"):
        parts.append(f"wiki:{_dir_stamp(os.path.join(repo_path or '.', 'knowledge', 'wiki'))}")
    if (
        float(ret.get("memory_weight", 0.0) or 0.0)
        or ctx.get("include_memory_brief")
        or ctx.get("include_memory_search")
    ):
        parts.append(f"memory:{_dir_stamp(os.path.join(repo_path or '.', 'knowledge', 'memory'))}")
        local_mem = os.path.join(repo_path or ".", ".code-harness", "memory")
        parts.append(f"memory_local:{_dir_stamp(local_mem)}")
    if ctx.get("knowledge_prefix"):
        parts.append(f"knowledge:{_dir_stamp(os.path.join(repo_path or '.', 'knowledge'))}")
    return "|".join(parts) or "none"


def make_cache_key(
    query: str,
    config: Config,
    *,
    top_k: Optional[int] = None,
    repo_name: str = "",
    repo_path: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    emb = getattr(config, "embedding", None) or {}
    ret = getattr(config, "retrieval", None) or {}
    ctx = getattr(config, "context", None) or {}
    chat = getattr(config, "chat", None) or {}
    ccr = getattr(config, "ccr", None) or {}
    vs = getattr(config, "vector_store", None) or {}
    fingerprint = index_fingerprint(config, repo_path or getattr(config, "repo_path", None) or ".", repo_name)
    from .vector_store import normalize_backend_name

    blob = json.dumps(
        {
            "q": normalize_query(query),
            "repo": repo_name,
            "embed": {"provider": emb.get("provider"), "model": emb.get("model")},
            "retrieval": {
                "top_k": top_k if top_k is not None else ret.get("top_k"),
                "dense_weight": ret.get("dense_weight"),
                "sparse_weight": ret.get("sparse_weight"),
                "graph_weight": ret.get("graph_weight"),
                "wiki_weight": ret.get("wiki_weight"),
                "memory_weight": ret.get("memory_weight"),
                "max_loops": ret.get("max_loops"),
                "expand_mode": ret.get("expand_mode"),
                "expand_neighbors": ret.get("expand_neighbors"),
                "rerank_top_k": ret.get("rerank_top_k"),
            },
            "flags": {
                "wiki_mode": bool(chat.get("wiki_mode")),
                "include_memory_brief": bool(ctx.get("include_memory_brief")),
                "include_memory_search": bool(ctx.get("include_memory_search")),
                "knowledge_prefix": bool(ctx.get("knowledge_prefix")),
                "knowledge_token_budget": ctx.get("knowledge_token_budget"),
                "backend": normalize_backend_name(vs.get("type", "chromadb")),
            },
            "packer": {
                "pack_mode": ctx.get("pack_mode"),
                "ccr_first": ccr.get("first_lines"),
                "ccr_last": ccr.get("last_lines"),
                "ccr_omit": ccr.get("omit_threshold"),
            },
            "index": fingerprint,
            "extra": extra or {},
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def run_with_query_cache(
    loop,
    query: str,
    *,
    top_k: int,
    cache: Optional[QueryCache],
    config: Config,
    repo_name: str = "",
    repo_path: str = "",
    extra: Optional[Dict[str, Any]] = None,
    **run_kwargs,
):
    """Run ``QueryLoop.run`` with retrieve/pack reuse. LLM answer is not stored."""
    if cache is None:
        return loop.run(query, top_k=top_k, **run_kwargs)
    generate = run_kwargs.pop("generate", None)
    verifier = run_kwargs.pop("verifier", None)
    key_extra = dict(extra or {})
    if run_kwargs.get("must_cite_paths"):
        key_extra["must_cite_paths"] = list(run_kwargs.get("must_cite_paths") or [])
    key = make_cache_key(
        query,
        config,
        top_k=top_k,
        repo_name=repo_name,
        repo_path=repo_path or getattr(config, "repo_path", None) or ".",
        extra=key_extra,
    )
    hit = cache.get(key)
    if hit is not None:
        cache.hits += 1
        outcome = deserialize_outcome(hit)
        outcome.cached = True
        if generate is not None and getattr(outcome, "packed", None) is not None:
            outcome = _apply_generate(loop, outcome, query, generate, verifier)
        return outcome
    cache.misses += 1
    outcome = loop.run(query, top_k=top_k, generate=generate, verifier=verifier, **run_kwargs)
    outcome.cached = False
    cache.put(key, serialize_outcome(outcome))
    return outcome


def serialize_outcome(outcome) -> Dict[str, Any]:
    return {
        "results": [_ser_result(item) for item in (getattr(outcome, "results", None) or [])],
        "packed": _ser_report(getattr(outcome, "packed", None)),
        "attempts": getattr(outcome, "attempts", 1),
        "grade": getattr(outcome, "grade", 0.0),
        "coverage": getattr(outcome, "coverage", 0.0),
        "action": getattr(outcome, "action", "stop"),
        "stop_reason": getattr(outcome, "stop_reason", ""),
        "traces": [_ser_trace(trace) for trace in (getattr(outcome, "traces", None) or [])],
        "query_effective": getattr(outcome, "query_effective", "") or "",
        "mode": getattr(outcome, "mode", "hybrid") or "hybrid",
    }


def deserialize_outcome(payload: Dict[str, Any]):
    from .loop import LoopOutcome

    packed = _de_report(payload.get("packed"))
    return LoopOutcome(
        results=[_de_result(item) for item in (payload.get("results") or [])],
        packed=packed,
        attempts=int(payload.get("attempts") or 1),
        grade=float(payload.get("grade") or 0.0),
        coverage=float(payload.get("coverage") or 0.0),
        action=payload.get("action") or "stop",
        stop_reason=payload.get("stop_reason") or "",
        traces=[_de_trace(trace) for trace in (payload.get("traces") or [])],
        query_effective=payload.get("query_effective") or "",
        mode=payload.get("mode") or "hybrid",
        answer=None,
        verify=None,
        cached=True,
    )


def _apply_generate(loop, outcome, query: str, generate, verifier):
    packed = outcome.packed
    system = ""
    builder = getattr(loop, "context_builder", None)
    if builder is not None and hasattr(builder, "build_system_prompt"):
        system = builder.build_system_prompt() or ""
    answer = generate(system, getattr(packed, "context", "") or "", query)
    outcome.answer = answer
    if getattr(getattr(loop, "config", None), "verify", False) and answer and verifier is not None:
        outcome.verify = verifier(query, answer, getattr(packed, "context", "") or "")
    return outcome


def _ser_chunk(chunk) -> Optional[Dict[str, Any]]:
    if chunk is None:
        return None
    et = getattr(chunk, "entity_type", None)
    et_val = et.value if hasattr(et, "value") else str(et or "function")
    return {
        "id": getattr(chunk, "id", ""),
        "content": getattr(chunk, "content", "") or "",
        "entity_id": getattr(chunk, "entity_id", "") or "",
        "entity_name": getattr(chunk, "entity_name", "") or "",
        "entity_type": et_val,
        "file_path": getattr(chunk, "file_path", "") or "",
        "start_line": int(getattr(chunk, "start_line", 0) or 0),
        "end_line": int(getattr(chunk, "end_line", 0) or 0),
        "docstring": getattr(chunk, "docstring", "") or "",
        "repo_name": getattr(chunk, "repo_name", "") or "",
        "metadata": dict(getattr(chunk, "metadata", None) or {}),
    }


def _de_chunk(data: Optional[Dict[str, Any]]):
    from .models import Chunk, EntityType

    data = data or {}
    raw = data.get("entity_type") or "function"
    try:
        et = EntityType(raw)
    except ValueError:
        et = EntityType.FUNCTION
    return Chunk(
        id=data.get("id") or "",
        content=data.get("content") or "",
        entity_id=data.get("entity_id") or "",
        entity_name=data.get("entity_name") or "",
        entity_type=et,
        file_path=data.get("file_path") or "",
        start_line=int(data.get("start_line") or 0),
        end_line=int(data.get("end_line") or 0),
        docstring=data.get("docstring") or "",
        repo_name=data.get("repo_name") or "",
        metadata=dict(data.get("metadata") or {}),
    )


def _ser_result(item) -> Dict[str, Any]:
    chunk = getattr(item, "chunk", item)
    return {
        "chunk": _ser_chunk(chunk),
        "score": float(getattr(item, "score", 0.0) or 0.0),
        "source": getattr(item, "source", "") or "",
    }


def _de_result(data: Dict[str, Any]):
    from .models import RetrievalResult

    return RetrievalResult(
        chunk=_de_chunk(data.get("chunk") if isinstance(data, dict) else None),
        score=float((data or {}).get("score") or 0.0),
        source=(data or {}).get("source") or "",
    )


def _ser_report(report) -> Optional[Dict[str, Any]]:
    if report is None:
        return None
    return {
        "context": getattr(report, "context", "") or "",
        "prompt_tokens": int(getattr(report, "prompt_tokens", 0) or 0),
        "packed_chunk_ids": list(getattr(report, "packed_chunk_ids", None) or []),
        "packed_paths": list(getattr(report, "packed_paths", None) or []),
        "mmr_latency_ms": float(getattr(report, "mmr_latency_ms", 0.0) or 0.0),
        "pack_mode": getattr(report, "pack_mode", None) or "full",
        "prompt_tokens_full": int(getattr(report, "prompt_tokens_full", 0) or 0),
        "prompt_tokens_packed": int(getattr(report, "prompt_tokens_packed", 0) or 0),
        "prefix_hash": getattr(report, "prefix_hash", "") or "",
        "omitted_chunk_ids": list(getattr(report, "omitted_chunk_ids", None) or []),
        "redaction_count": int(getattr(report, "redaction_count", 0) or 0),
    }


def _de_report(data: Optional[Dict[str, Any]]):
    if not data:
        return None
    from .context_builder import ContextReport

    return ContextReport(
        context=data.get("context") or "",
        prompt_tokens=int(data.get("prompt_tokens") or 0),
        packed_chunk_ids=list(data.get("packed_chunk_ids") or []),
        packed_paths=list(data.get("packed_paths") or []),
        mmr_latency_ms=float(data.get("mmr_latency_ms") or 0.0),
        pack_mode=data.get("pack_mode") or "full",
        prompt_tokens_full=int(data.get("prompt_tokens_full") or 0),
        prompt_tokens_packed=int(data.get("prompt_tokens_packed") or 0),
        prefix_hash=data.get("prefix_hash") or "",
        omitted_chunk_ids=list(data.get("omitted_chunk_ids") or []),
        redaction_count=int(data.get("redaction_count") or 0),
    )


def _ser_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in (trace or {}).items():
        if isinstance(val, list) and val and (hasattr(val[0], "chunk") or hasattr(val[0], "file_path")):
            out[key] = [_ser_result(item) for item in val]
        elif key == "latencies_ms" and isinstance(val, dict):
            out[key] = dict(val)
        else:
            try:
                json.dumps(val)
                out[key] = val
            except TypeError:
                out[key] = str(val)
    return out


def _de_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    result_keys = {"dense", "sparse", "graph", "wiki", "memory", "fused", "reranked"}
    for key, val in (trace or {}).items():
        if key in result_keys and isinstance(val, list):
            out[key] = [_de_result(item) if isinstance(item, dict) else item for item in val]
        else:
            out[key] = val
    return out


def _file_stamp(path: str) -> str:
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    mtime = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
    return f"{st.st_size}:{mtime}"


def _dir_stamp(root: str) -> str:
    if not root or not os.path.isdir(root):
        return "none"
    parts: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            mtime = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
            parts.append(f"{rel}:{st.st_size}:{mtime}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{len(parts)}:{digest}"


def _epoch_path(repo_path: Optional[str]) -> str:
    return os.path.join(repo_path or ".", ".code-harness", "query_cache.epoch")


def _read_epoch(repo_path: Optional[str]) -> str:
    path = _epoch_path(repo_path)
    try:
        with open(path, encoding="utf-8") as fh:
            return (fh.read() or "0").strip() or "0"
    except OSError:
        return "0"


def _bump_epoch(repo_path: Optional[str]) -> None:
    path = _epoch_path(repo_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        current = int(_read_epoch(repo_path) or "0")
    except ValueError:
        current = 0
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(current + 1))
