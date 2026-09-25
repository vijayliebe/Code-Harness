import json
import os
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

    "context": {
        "pack_mode": "full",
        "prefix_files": ["ARCHITECTURE.md", "AGENTS.md", "CLAUDE.md"],
        "include_memory_brief": False,
        "memory_brief_tokens": 800,
        "memory_dir": None,
        "max_tokens_multiplier": 2,
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

    "retrieval": {
        "dense_weight": 0.3,
        "sparse_weight": 0.25,
        "graph_weight": 0.2,
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
    embedding: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["embedding"]))
    chunking: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["chunking"]))
    vector_store: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["vector_store"]))
    knowledge_graph: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["knowledge_graph"]))
    repo_graph: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["repo_graph"]))
    retrieval: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["retrieval"]))
    llm: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["llm"]))
    indexing: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["indexing"]))
    context: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["context"]))
    ccr: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["ccr"]))
    session: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["session"]))
    redaction: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["redaction"]))
    serve: Dict = field(default_factory=lambda: dict(DEFAULT_CONFIG["serve"]))
    repo_path: str = "."
    verbose: bool = False

    @classmethod
    def from_dict(cls, d: Dict) -> "Config":
        config = cls()
        for section in DEFAULT_CONFIG:
            if section in d:
                merged = {**getattr(config, section), **d[section]}
                if section == "vector_store":
                    base_tv = getattr(config, section).get("turbovec") or {}
                    incoming_tv = d[section].get("turbovec") if isinstance(d[section], dict) else None
                    if isinstance(base_tv, dict) or isinstance(incoming_tv, dict):
                        merged["turbovec"] = {**base_tv, **(incoming_tv or {})}
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
            "indexing": self.indexing,
            "context": self.context,
            "ccr": self.ccr,
            "session": self.session,
            "redaction": self.redaction,
            "serve": self.serve,
            "repo_path": self.repo_path,
            "verbose": self.verbose,
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
