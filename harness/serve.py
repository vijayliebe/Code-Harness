"""Localhost retrieve API + MCP (HTTP loopback and stdio).

Default HTTP bind is 127.0.0.1. Binding 0.0.0.0 / all interfaces requires
``--allow-public`` (dangerous: no auth). ``serve_stdio`` speaks JSON-RPC on
stdin/stdout and never binds a port. Response bodies go through
``redact_and_audit``. Not started on import.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, TextIO
from urllib.parse import urlparse

from .config import Config
from .redact import redact_and_audit, redaction_enabled


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7432
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "localhost.localdomain"})
PUBLIC_HOSTS = frozenset({"0.0.0.0", "::", "*", "[::]"})
PUBLIC_BIND_FLAG = "--allow-public"

MCP_TOOLS = (
    {
        "name": "retrieve",
        "description": "Hybrid retrieve + pack against the local code index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
                "pack_mode": {"type": "string", "enum": ["full", "ccr_lite"]},
                "include_memory_brief": {"type": "boolean"},
                "include_memory_search": {"type": "boolean"},
                "include_knowledge_prefix": {"type": "boolean"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "retrieve_chunk",
        "description": "Materialize a CCR-cached original chunk by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"chunk_id": {"type": "string"}},
            "required": ["chunk_id"],
        },
    },
    {
        "name": "doctor",
        "description": "Local health check (no network).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "wiki_show",
        "description": "Show one generated OKF wiki page.",
        "inputSchema": {
            "type": "object",
            "properties": {"page": {"type": "string"}},
            "required": ["page"],
        },
    },
    {
        "name": "memory_brief",
        "description": "Pack active typed memories (≤800 tokens).",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    },
    {
        "name": "graph_neighbors",
        "description": "Return knowledge-graph neighbors for an entity id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "max_depth": {"type": "integer"},
            },
            "required": ["entity_id"],
        },
    },
)


class BindError(ValueError):
    """Raised when a non-loopback bind is requested without --allow-public."""


def resolve_bind_host(host: Optional[str], allow_public: bool = False) -> str:
    if host is None or str(host).strip() == "":
        return DEFAULT_HOST
    raw = str(host).strip()
    key = raw.lower().strip("[]")
    is_loopback = key in LOOPBACK_HOSTS or key.startswith("127.")
    is_public = key in PUBLIC_HOSTS or key == ""
    if (is_public or not is_loopback) and not allow_public:
        raise BindError(
            f"refusing to bind {raw!r} (not loopback). "
            f"Pass {PUBLIC_BIND_FLAG} to listen on all interfaces "
            "(dangerous: no authentication)."
        )
    return raw


def handle_health(config: Optional[Config] = None, host: str = DEFAULT_HOST) -> Dict[str, Any]:
    cfg = config or Config()
    return {
        "ok": True,
        "bind": host or DEFAULT_HOST,
        "redact": redaction_enabled(cfg),
        "service": "code-harness",
    }


class QueryCache:
    """Optional SQLite query-hash → redacted retrieve payload."""

    def __init__(self, path: str):
        self.path = path
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

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        if not key:
            return None
        row = self._conn.execute(
            "SELECT payload FROM retrieve_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def put(self, key: str, payload: Dict[str, Any]) -> None:
        if not key:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO retrieve_cache(cache_key, payload, created_at) "
            "VALUES (?, ?, ?)",
            (
                key,
                json.dumps(payload, ensure_ascii=False),
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )
        self._conn.commit()


class RetrieveService:
    """Retrieve + pack. Retriever is injectable for tests."""

    def __init__(
        self,
        config: Optional[Config] = None,
        retriever=None,
        context_builder=None,
        repo_name: str = "",
        cache: Optional[QueryCache] = None,
        kg=None,
        repo_path: str = "",
    ):
        self.config = config or Config()
        self.retriever = retriever
        self.context_builder = context_builder
        self.repo_name = repo_name
        self.cache = cache
        self.kg = kg
        self.repo_path = repo_path or getattr(self.config, "repo_path", None) or "."

    def retrieve(self, request: Dict[str, Any]) -> Dict[str, Any]:
        req = request or {}
        query = (req.get("query") or req.get("q") or "").strip()
        if not query:
            raise ValueError("query is required")
        top_k = req.get("top_k")
        if top_k is not None:
            top_k = int(top_k)
        pack_mode = req.get("pack_mode")
        if pack_mode in ("full", "ccr_lite"):
            self.config.context["pack_mode"] = pack_mode
        if req.get("include_memory_brief"):
            self.config.context["include_memory_brief"] = True
        if req.get("include_memory_search"):
            from .memory import apply_memory_search

            apply_memory_search(self.config, True)
            if self.retriever is not None:
                self.retriever.config = self.config
                self.retriever.memory_weight = float(
                    (self.config.retrieval or {}).get("memory_weight", 0.0) or 0.0
                )
        if req.get("include_knowledge_prefix"):
            self.config.context["knowledge_prefix"] = True

        cache_key = self._cache_key(query, top_k) if self.cache is not None else None
        if cache_key:
            hit = self.cache.get(cache_key)
            if hit is not None:
                payload = dict(hit)
                payload["cached"] = True
                return self._redact_payload(payload, query)

        if self.retriever is None:
            raise RuntimeError("retrieve service has no retriever")

        from .context_builder import ContextBuilder
        from .loop import LoopConfig, QueryLoop

        builder = self.context_builder or ContextBuilder(self.config)
        k = int(top_k or (self.config.retrieval or {}).get("top_k") or 20)
        loop = QueryLoop(self.retriever, builder, LoopConfig.from_mapping(self.config.retrieval))
        outcome = loop.run(query, top_k=k)
        results = list(outcome.results or [])
        report = outcome.packed or builder.build_context_report(query, results)
        expand_ids = req.get("expand_chunks") or []
        context = report.context
        if expand_ids:
            context = builder.expand_into_context(context, list(expand_ids))

        payload = {
            "query": query,
            "pack_mode": getattr(report, "pack_mode", None) or builder.pack_mode,
            "cached": False,
            "chunk_ids": [r.chunk.id for r in results],
            "results": [_result_dict(r) for r in results],
            "context": context,
            "prompt_tokens": getattr(report, "prompt_tokens", None),
            "attempts": getattr(outcome, "attempts", 1),
        }
        redacted = self._redact_payload(payload, query)
        if cache_key:
            stored = dict(redacted)
            stored["cached"] = False
            self.cache.put(cache_key, stored)
        return redacted

    def _cache_key(self, query: str, top_k: Optional[int]) -> str:
        emb = getattr(self.config, "embedding", None) or {}
        ret = getattr(self.config, "retrieval", None) or {}
        ctx = getattr(self.config, "context", None) or {}
        fingerprint = _index_fingerprint(self.config, self.repo_path, self.repo_name)
        blob = json.dumps(
            {
                "q": query,
                "repo": self.repo_name,
                "embed": {"provider": emb.get("provider"), "model": emb.get("model")},
                "retrieval": {
                    "top_k": top_k or ret.get("top_k"),
                    "dense_weight": ret.get("dense_weight"),
                    "sparse_weight": ret.get("sparse_weight"),
                    "graph_weight": ret.get("graph_weight"),
                    "max_loops": ret.get("max_loops"),
                    "expand_mode": ret.get("expand_mode"),
                },
                "pack_mode": ctx.get("pack_mode"),
                "index": fingerprint,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _redact_payload(self, payload: Dict[str, Any], query: str) -> Dict[str, Any]:
        raw = json.dumps(payload, ensure_ascii=False)
        result = redact_and_audit(
            raw,
            action="redact.retrieve",
            config=self.config,
            query=query,
            chunk_ids=list(payload.get("chunk_ids") or []),
            tokens=payload.get("prompt_tokens"),
        )
        try:
            out = json.loads(result.text)
        except json.JSONDecodeError:
            out = dict(payload)
            out["context"] = result.text
        prior = int(payload.get("redaction_count") or 0)
        out["redaction_count"] = prior + result.count
        return out


def _result_dict(result) -> Dict[str, Any]:
    chunk = result.chunk
    return {
        "id": chunk.id,
        "path": chunk.file_path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "entity_name": chunk.entity_name,
        "score": result.score,
        "source": result.source,
        "text": chunk.content,
    }


def _index_fingerprint(config: Config, repo_path: str, repo_name: str) -> str:
    parts: List[str] = []
    from .vector_store import normalize_backend_name, resolved_persist_directory

    persist = resolved_persist_directory(config)
    if not os.path.isabs(persist):
        persist = os.path.join(repo_path or ".", persist)
    kind = normalize_backend_name((config.vector_store or {}).get("type", "chromadb"))
    sqlite = os.path.join(persist, "chroma.sqlite3")
    if os.path.isfile(sqlite):
        parts.append(f"chroma:{os.path.getmtime(sqlite)}")
    for name in ("index.tvim", "sidecar.json", "store.json", "manifest.json"):
        path = os.path.join(persist, name)
        if os.path.isfile(path):
            parts.append(f"{kind}:{name}:{os.path.getmtime(path)}")
            break
    from .knowledge_graph import KnowledgeGraph

    kg_path = KnowledgeGraph(config, repo_name=repo_name).persist_path
    if os.path.isfile(kg_path):
        parts.append(f"graph:{os.path.getmtime(kg_path)}")
    return "|".join(parts) or "none"


def handle_mcp(message: Dict[str, Any], service: RetrieveService) -> Dict[str, Any]:
    msg = message or {}
    req_id = msg.get("id")
    method = msg.get("method") or ""
    if method == "initialize":
        return _rpc(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "code-harness", "version": "0.1.0"},
            },
        )
    if method in ("notifications/initialized", "initialized"):
        return _rpc(req_id, {})
    if method == "tools/list":
        return _rpc(req_id, {"tools": list(MCP_TOOLS)})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        try:
            result = _call_tool(name, args, service)
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(exc)},
            }
        return _rpc(req_id, result)
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"unknown method: {method}"},
    }


def _rpc(req_id, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _call_tool(name: str, args: Dict[str, Any], service: RetrieveService) -> Dict[str, Any]:
    args = args or {}
    if name == "retrieve":
        payload = service.retrieve(args)
        return _tool_text(json.dumps(payload, ensure_ascii=False), payload)
    if name == "retrieve_chunk":
        from .ccr import retrieve_chunk

        chunk_id = args.get("chunk_id") or args.get("id") or ""
        spill = (getattr(service.config, "ccr", None) or {}).get("spill_dir") or ".code-harness/ccr"
        text = retrieve_chunk(chunk_id, spill_dir=spill) or f"chunk not in cache: {chunk_id}"
        redacted = redact_and_audit(
            text, action="redact.retrieve", config=service.config,
        ).text
        return _tool_text(redacted)
    if name == "doctor":
        from .doctor import run_doctor

        report = run_doctor(
            service.config,
            repo_path=service.repo_path,
            repo_name=service.repo_name,
        )
        return _tool_text(report.format())
    if name == "wiki_show":
        from .okf import default_wiki_dir
        from .wiki import show_page

        page = args.get("page") or "architecture"
        out_dir = default_wiki_dir(service.repo_path)
        try:
            text = show_page(out_dir, page)
        except FileNotFoundError as exc:
            text = str(exc)
        redacted = redact_and_audit(text, action="redact.wiki", config=service.config).text
        return _tool_text(redacted)
    if name == "memory_brief":
        from .memory import MemoryStore
        from .okf import default_memory_dir

        store = MemoryStore(default_memory_dir(service.repo_path))
        brief = store.brief(query=args.get("query") or "", redact=True)
        return _tool_text(brief.text or "[!] No active memory to brief")
    if name == "graph_neighbors":
        entity_id = args.get("entity_id") or ""
        depth = int(args.get("max_depth") or 2)
        kg = service.kg
        if kg is None:
            from .knowledge_graph import KnowledgeGraph

            kg = KnowledgeGraph(service.config, repo_name=service.repo_name)
            kg.load()
        neighbors = sorted(kg.get_neighbors(entity_id, max_depth=depth))
        blob = json.dumps({"entity_id": entity_id, "neighbors": neighbors}, ensure_ascii=False)
        blob = redact_and_audit(blob, action="redact.retrieve", config=service.config).text
        return _tool_text(blob, {"entity_id": entity_id, "neighbors": neighbors})
    raise ValueError(f"unknown tool: {name}")


def _tool_text(text: str, structured: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"content": [{"type": "text", "text": text or ""}]}
    if structured is not None:
        out["structuredContent"] = structured
    return out


def _is_rpc_notification(message: Dict[str, Any]) -> bool:
    """JSON-RPC 2.0 notification: request object with the id member omitted."""
    return isinstance(message, dict) and "id" not in message


def write_jsonrpc(stream, message: Dict[str, Any]) -> None:
    """Write one MCP message using Content-Length framing (LSP-style)."""
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    buf = getattr(stream, "buffer", None)
    if buf is not None and hasattr(buf, "write") and not isinstance(stream, io.StringIO):
        buf.write(header + body)
        buf.flush()
        return
    stream.write(header.decode("ascii") + body.decode("utf-8"))
    if hasattr(stream, "flush"):
        stream.flush()


def _readline_text(stream) -> Optional[str]:
    line = stream.readline()
    if line == "" or line == b"":
        return None
    if isinstance(line, bytes):
        return line.decode("utf-8")
    return line


def _read_exact(stream, nbytes: int) -> Optional[str]:
    """Read ``nbytes`` UTF-8 bytes when a binary buffer exists, else ``nbytes`` chars."""
    buf = getattr(stream, "buffer", None)
    if buf is not None and hasattr(buf, "read") and not isinstance(stream, io.StringIO):
        data = buf.read(nbytes)
        if not data:
            return None
        if isinstance(data, bytes):
            return data.decode("utf-8")
        return data
    data = stream.read(nbytes)
    if data == "" or data == b"":
        return None
    if isinstance(data, bytes):
        return data.decode("utf-8")
    return data


def read_jsonrpc(stream) -> Optional[Dict[str, Any]]:
    """Read one JSON-RPC object. Accepts Content-Length framing or NDJSON."""
    line = _readline_text(stream)
    while line is not None and not line.strip():
        line = _readline_text(stream)
    if line is None:
        return None
    if line.lower().startswith("content-length:"):
        headers = [line]
        while True:
            next_line = _readline_text(stream)
            if next_line is None:
                return None
            if next_line in ("\n", "\r\n", ""):
                break
            if not next_line.strip():
                break
            headers.append(next_line)
        nbytes = None
        for header in headers:
            if header.lower().startswith("content-length:"):
                try:
                    nbytes = int(header.split(":", 1)[1].strip())
                except ValueError:
                    nbytes = None
        if nbytes is None or nbytes < 0:
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "invalid Content-Length"},
            }
        body = _read_exact(stream, nbytes)
        if body is None:
            return None
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"parse error: {exc}"},
            }
        return data if isinstance(data, dict) else {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": f"parse error: {exc}"},
        }
    if not isinstance(data, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "invalid request"},
        }
    return data


def serve_stdio(
    service: RetrieveService,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> None:
    """Run the MCP tool surface over stdin/stdout. Never binds a socket.

    Reuses :func:`handle_mcp` (same tools as ``POST /mcp``). Status logs go to
    stderr so stdout stays protocol-only. Accidental ``print()`` is redirected
    to stderr for the duration of the session.
    """
    protocol_in = stdin if stdin is not None else sys.stdin
    protocol_out = stdout if stdout is not None else sys.stdout
    protocol_err = stderr if stderr is not None else sys.stderr
    print("[*] MCP stdio ready (no network bind)", file=protocol_err)
    repo = getattr(service, "repo_path", None)
    if repo:
        print(f"[*] repo: {repo}", file=protocol_err)

    saved_stdout = sys.stdout
    sys.stdout = protocol_err
    try:
        while True:
            message = read_jsonrpc(protocol_in)
            if message is None:
                break
            if message.get("error") and message.get("id") is None and "method" not in message:
                write_jsonrpc(protocol_out, message)
                continue
            if _is_rpc_notification(message):
                continue
            try:
                reply = handle_mcp(message, service)
            except Exception as exc:
                reply = {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32000, "message": str(exc)},
                }
            if reply is not None:
                write_jsonrpc(protocol_out, reply)
    finally:
        sys.stdout = saved_stdout


class _RetrieveHandler(BaseHTTPRequestHandler):
    service: Optional[RetrieveService] = None

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/health", "/v1/health"):
            host = self.server.server_address[0]
            self._json(200, handle_health(self.service.config if self.service else None, host=host))
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        raw = self._read_body()
        try:
            data = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid json"})
            return
        if not isinstance(data, dict):
            data = {}
        if self.service is None:
            self._json(503, {"error": "retrieve service not configured"})
            return
        if path == "/v1/retrieve":
            try:
                payload = self.service.retrieve(data)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, payload)
            return
        if path in ("/mcp", "/v1/mcp"):
            self._json(200, handle_mcp(data, self.service))
            return
        self._json(404, {"error": "not found"})

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        if os.environ.get("CODEHARNESS_SERVE_LOG"):
            super().log_message(fmt, *args)


def make_handler(service: RetrieveService):
    class Bound(_RetrieveHandler):
        pass

    Bound.service = service
    return Bound


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    service: Optional[RetrieveService] = None,
    allow_public: bool = False,
) -> ThreadingHTTPServer:
    bind = resolve_bind_host(host, allow_public=allow_public)
    handler = make_handler(service or RetrieveService())
    return ThreadingHTTPServer((bind, int(port)), handler)


def build_retrieve_service(
    config: Config,
    repo_name: str,
    cache: Optional[QueryCache] = None,
) -> RetrieveService:
    from .context_builder import ContextBuilder
    from .embedder import Embedder
    from .knowledge_graph import KnowledgeGraph
    from .retriever import Retriever
    from .vector_store import VectorStore

    vs = VectorStore(config)
    embedder = Embedder(config)
    kg = KnowledgeGraph(config, repo_name=repo_name)
    kg.load()
    retriever = Retriever(config, embedder, vs, kg, repo_name=repo_name)
    if not retriever.try_load_bm25():
        chunks = vs.get_all(repo_name=repo_name)
        retriever.index_chunks(chunks, persist=False)
    return RetrieveService(
        config=config,
        retriever=retriever,
        context_builder=ContextBuilder(config),
        repo_name=repo_name,
        cache=cache,
        kg=kg,
        repo_path=getattr(config, "repo_path", None) or ".",
    )


def serve_forever(server: ThreadingHTTPServer) -> None:
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] stopped", file=sys.stderr)
    finally:
        server.shutdown()
        server.server_close()
