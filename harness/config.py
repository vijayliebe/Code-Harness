import json
import os
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, List


DEFAULT_CONFIG = {
    "embedding": {
        "provider": "local",
        "model": "all-MiniLM-L6-v2",
        "api_key": None,
        "api_base": None,
        "dimensions": 384,
    },
    "chunking": {
        "max_chunk_size": 1500,
        "min_chunk_size": 50,
        "overlap_lines": 20,
        "use_language_parsing": True,
    },
    "knowledge_graph": {
        "enabled": True,
        "persist_path": ".code-harness/graph.json",
        "enrich": True,
        "extract_exposes": True,
        "extract_tested_by": True,
        "extract_gloss": True,
        "gloss_dirs": [".code-harness/gloss", "knowledge/gloss"],
    },
    "repo_graph": {
        "enabled": True,
        "persist_path": ".code-harness/repo_graph.json",
        "skip_modules": [
            "os", "sys", "re", "json", "math", "typing", "collections",
            "dataclasses", "enum", "pathlib", "abc", "io", "functools",
            "itertools", "datetime", "copy", "hashlib", "uuid",
        ],
    },
    "llm": {
        "provider": "openai",
        "model": "gpt-4o",
        "api_key": None,
        "api_base": None,
        "temperature": 0.1,
        "max_tokens": 4096,
    },

    # Optional typed decision model (Jev / TypeSafe / Venice). Off by default.
    # Not used for HyDE text generation — HyDE stays a free template string.
    "decision": {
        "enabled": False,
        "provider": None,
        "api_key": None,
        "api_base": None,
        "model": None,
        "timeout_ms": 2000,
        "min_confidence": 0.0,
        "uses": ["loop_grade", "verify"],
    },

    "context": {
        "pack_mode": "full",
        "prefix_files": ["ARCHITECTURE.md", "AGENTS.md", "CLAUDE.md"],
        "include_memory_brief": False,
        "include_memory_search": False,
        "memory_brief_tokens": 800,
        "memory_dir": None,
        "knowledge_prefix": False,
        "knowledge_token_budget": 800,
        "prefix_stable": False,
        "max_tokens_multiplier": 2,
        # Applied after MMR, before the token-budget cut. 0 = unlimited.
        # Default 3: golden suite Recall/nDCG/cite unchanged, tokens slightly down.
        "per_file_chunk_cap": 3,
    },
    "ccr": {
        "first_lines": 12,
        "last_lines": 8,
        "omit_threshold": 8,
        "spill_dir": ".code-harness/ccr",
        "spill": None,
        "expand_on": [],
    },

    "session": {
        "dir": ".code-harness/sessions",
        "keep_recent": 1,
        "clear_tool_results": False,
        "clear_tool_keep": 1,
        "clear_tool_token_trigger": 0,
        "verify": False,
        "force_done": False,
        "criteria": [],
        "event_session": False,
    },

    "chat": {
        "wiki_mode": False,
    },

    "memory": {
        "auto_extract": False,
    },

    "redaction": {
        "enabled": True,
        "audit": True,
        "audit_path": ".code-harness/audit/audit.jsonl",
        "session": True,
        "wiki": True,
        "memory": False,
        "max_prompt_tokens": None,
    },

    "serve": {
        "host": "127.0.0.1",
        "port": 7432,
        "cache": True,
        "cache_path": ".code-harness/query_cache.sqlite",
    },

    "query_cache": {
        "enabled": False,
        "path": ".code-harness/query_cache.sqlite",
    },

    "prestep": {
        "retrieve": True,
    },

    # External tickets/docs (X0–X1). Separate store from the code index.
    # Off: ingest is CLI-only; retrieve ignores the sidecar unless enabled.
    "external": {
        "enabled": False,
        "weight": 0.15,
        "persist_directory": ".code-harness/external",
        "ticket_lookup": True,
        "connectors": {
            "file": {
                "extensions": [".md", ".txt", ".markdown"],
            }
        },
    },

    "retrieval": {
        "dense_weight": 0.3,
        "sparse_weight": 0.25,
        "graph_weight": 0.2,
        "wiki_weight": 0.0,
        "memory_weight": 0.0,
        "top_k": 30,
        "rerank_top_k": 15,
        "expand_neighbors": 3,
        "expand_mode": "beam",
        "beam_width": 6,
        "beam_depth": 2,
        "max_loops": 0,
        "grade_threshold": 0.35,
        "citation_threshold": 0.5,
        "verify": False,
        "deepen_neighbors": 8,
        "cross_encoder": {
            "enabled": True,
            "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        },
        "hyde": {
            "enabled": False,
            "on_retry": True,
        },
        "multi_query": {
            "enabled": True,
            "original_weight": 1.0,
            "identifier_weight": 1.0,
            "hyde_weight": 0.7,
            "rrf_k": 50,
        },
        "expansion": {
            "enabled": True,
            "drop_below": 0.02,
            "neighbor_score": 0.35,
            "file_score": 0.85,
        },
    },

    "vector_store": {
        "type": "chromadb",
        "persist_directory": ".code-harness/chromadb",
        "collection_name": "code_chunks",
        "similarity_metric": "cosine",
        "hnsw_ef_search": 256,
        "hnsw_ef_construction": 200,
        "hnsw_m": 32,
        "turbovec": {
            "bits": 4,
            "persist_directory": ".code-harness/turbovec",
            "use_stub": False,
        },
    },

    "indexing": {
        # Path + symbol + signature prefix on the *embedded/BM25* text only.
        # Display and packed context stay chunk.content. Requires a re-index.
        "enrich_index_text": False,
        "enrich_prefix_ratio": 0.25,
        "exclude_patterns": [
            "node_modules", "__pycache__", ".git", "venv",
            ".venv", ".tox", "dist", "build", ".next",
            "*.pyc", "*.pyo", "*.so", "*.dll", "*.dylib",
            ".DS_Store", "package-lock.json", "yarn.lock",
            "*.min.js", "*.min.css",
        ],
        "include_extensions": [
            ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs",
            ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".php",
            ".swift", ".kt", ".scala", ".ex", ".exs",
            ".md", ".rst", ".txt", ".yaml", ".yml", ".json",
            ".toml", ".cfg", ".ini",
        ],
        "max_file_size_kb": 512,
        "use_treesitter": True,
    },
}


@dataclass
class Config:
    embedding: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["embedding"]))
    chunking: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["chunking"]))
    vector_store: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["vector_store"]))
    knowledge_graph: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["knowledge_graph"]))
    repo_graph: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["repo_graph"]))
    retrieval: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["retrieval"]))
    llm: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["llm"]))
    decision: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["decision"]))
    indexing: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["indexing"]))
    context: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["context"]))
    ccr: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["ccr"]))
    session: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["session"]))
    chat: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["chat"]))
    memory: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["memory"]))
    redaction: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["redaction"]))
    serve: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["serve"]))
    query_cache: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["query_cache"]))
    prestep: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["prestep"]))
    external: Dict = field(default_factory=lambda: deepcopy(DEFAULT_CONFIG["external"]))
    repo_path: str = "."
    verbose: bool = False

    @classmethod
    def from_dict(cls, d: Dict) -> "Config":
        config = cls()
        for section in DEFAULT_CONFIG:
            if section in d:
                merged = {**getattr(config, section), **d[section]}
                if section == "decision" and "uses" in d[section]:
                    incoming_uses = d[section].get("uses")
                    if incoming_uses is None:
                        merged["uses"] = list(DEFAULT_CONFIG["decision"]["uses"])
                    else:
                        merged["uses"] = list(incoming_uses)
                if section == "vector_store":
                    base_tv = getattr(config, section).get("turbovec") or {}
                    incoming_tv = d[section].get("turbovec") if isinstance(d[section], dict) else None
                    if isinstance(base_tv, dict) or isinstance(incoming_tv, dict):
                        merged["turbovec"] = {**base_tv, **(incoming_tv or {})}
                if section == "retrieval":
                    for nested in ("hyde", "cross_encoder", "multi_query", "expansion"):
                        base_nested = getattr(config, section).get(nested) or {}
                        incoming_nested = d[section].get(nested) if isinstance(d[section], dict) else None
                        if isinstance(base_nested, dict) or isinstance(incoming_nested, dict):
                            merged[nested] = {**base_nested, **(incoming_nested or {})}
                if section == "external":
                    base_conn = getattr(config, section).get("connectors") or {}
                    incoming_conn = d[section].get("connectors") if isinstance(d[section], dict) else None
                    if isinstance(base_conn, dict) or isinstance(incoming_conn, dict):
                        merged_conn = dict(base_conn)
                        for name, cfg in (incoming_conn or {}).items():
                            if isinstance(cfg, dict) and isinstance(merged_conn.get(name), dict):
                                merged_conn[name] = {**merged_conn[name], **cfg}
                            else:
                                merged_conn[name] = cfg
                        merged["connectors"] = merged_conn
                setattr(config, section, merged)
        if "repo_path" in d:
            config.repo_path = d["repo_path"]
        if "verbose" in d:
            config.verbose = d["verbose"]
        return config

    @classmethod
    def from_file(cls, path: str) -> "Config":
        with open(path) as f:
            if path.endswith(".json"):
                return cls.from_dict(json.load(f))
            if path.endswith((".yaml", ".yml")):
                import yaml
                return cls.from_dict(yaml.safe_load(f))
        return cls()

    def save(self, path: str):
        data = {
            "embedding": self.embedding,
            "chunking": self.chunking,
            "vector_store": self.vector_store,
            "knowledge_graph": self.knowledge_graph,
            "repo_graph": self.repo_graph,
            "retrieval": self.retrieval,
            "llm": self.llm,
            "decision": self.decision,
            "indexing": self.indexing,
            "context": self.context,
            "ccr": self.ccr,
            "session": self.session,
            "chat": self.chat,
            "memory": self.memory,
            "redaction": self.redaction,
            "serve": self.serve,
            "query_cache": self.query_cache,
            "prestep": self.prestep,
            "external": self.external,
            "repo_path": self.repo_path,
            "verbose": self.verbose,
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
