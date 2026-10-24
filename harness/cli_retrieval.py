"""Cheap, config-gated CLI / config retrieval helpers.

``symbol_boost`` default is 0.5. ``doc_index`` stays off — catalog
stubs share a gold entity id and must not count as the handler.
Packed ``chunk.content`` on real source chunks is unchanged.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Sequence, Set

from .models import Chunk, EntityType

CLI_RETRIEVAL_DEFAULTS = {
    "ident_split": False,
    "argparse_enrich": False,
    "symbol_boost": 0.5,
    "doc_index": False,
}

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|$)|\d+")
_ADD_PARSER_RE = re.compile(
    r"""(?:(\w+)\s*=\s*)?[\w.]+\.add_parser\(\s*['"]([^'"]+)['"]"""
)
_HELP_RE = re.compile(r"""help\s*=\s*['"]([^'"]*)['"]""")
_SET_DEFAULTS_RE = re.compile(
    r"""(\w+)\.set_defaults\((?:[^)]*?)func\s*=\s*(\w+)"""
)
_NAMED_CLI_RE = re.compile(
    r"""\b(?:the\s+)?([a-z][\w-]*)\s+cli\s+command\b"""
)
_CLI_THEN_NAME_RE = re.compile(
    r"""\bcli\s+command\s+([a-z][\w-]*)\b"""
)
_CONFIG_HINT_RE = re.compile(
    r"""default.+(?:retrieval\s+weights|top_k|cross-encoder)|"""
    r"""retrieval\s+weights.+top_k""",
    re.IGNORECASE | re.DOTALL,
)
_CLI_NAME_STOP = frozenset({
    "the", "a", "an", "is", "are", "in", "on", "to", "of", "and",
    "defined", "used", "located", "found", "this", "that", "for",
    "from", "with", "into", "where", "what", "how", "does",
    "command", "commands", "cli",
})


def cli_retrieval_cfg(config) -> Dict:
    raw = {}
    if config is not None:
        retrieval = getattr(config, "retrieval", None) or {}
        raw = dict(retrieval.get("cli_retrieval") or {})
    out = dict(CLI_RETRIEVAL_DEFAULTS)
    out.update(raw)
    try:
        out["symbol_boost"] = float(out.get("symbol_boost") or 0.0)
    except (TypeError, ValueError):
        out["symbol_boost"] = 0.0
    out["ident_split"] = bool(out.get("ident_split"))
    out["argparse_enrich"] = bool(out.get("argparse_enrich"))
    out["doc_index"] = bool(out.get("doc_index"))
    return out


def split_identifier(token: str) -> List[str]:
    parts: List[str] = []
    for piece in (token or "").split("_"):
        if not piece:
            continue
        camel = _CAMEL_RE.findall(piece)
        if camel:
            parts.extend(p.lower() for p in camel)
        else:
            parts.append(piece.lower())
    return parts


def tokenize(text: str, ident_split: bool = False) -> List[str]:
    raw = _WORD_RE.findall(text or "")
    tokens = [tok.lower() for tok in raw]
    if not ident_split:
        return tokens
    out: List[str] = []
    for original, tok in zip(raw, tokens):
        out.append(tok)
        pieces = split_identifier(original)
        extra = [p for p in pieces if p != tok]
        if extra:
            out.extend(extra)
    return out


def parse_argparse_commands(source: str) -> Dict[str, Dict[str, str]]:
    """Map handler names (``cmd_query``) to ``{name, help}`` from argparse wiring."""
    catalog: Dict[str, Dict[str, str]] = {}
    var_to_cmd: Dict[str, Dict[str, str]] = {}
    text = source or ""
    for match in _ADD_PARSER_RE.finditer(text):
        var, name = match.group(1), match.group(2)
        window = text[match.end() : match.end() + 600]
        help_m = _HELP_RE.search(window)
        help_text = help_m.group(1) if help_m else ""
        info = {"name": name, "help": help_text}
        if var:
            var_to_cmd[var] = info
        handler = f"cmd_{name.replace('-', '_')}"
        catalog.setdefault(handler, info)
    for match in _SET_DEFAULTS_RE.finditer(text):
        var, func = match.group(1), match.group(2)
        info = var_to_cmd.get(var)
        if not info:
            continue
        catalog.setdefault(func, info)
    return catalog


def argparse_catalog_from_chunks(chunks: Sequence[Chunk]) -> Dict[str, Dict[str, str]]:
    by_file: Dict[str, List[str]] = {}
    for chunk in chunks:
        path = getattr(chunk, "file_path", None) or ""
        by_file.setdefault(path, []).append(getattr(chunk, "content", None) or "")
    catalog: Dict[str, Dict[str, str]] = {}
    for texts in by_file.values():
        catalog.update(parse_argparse_commands("\n".join(texts)))
    return catalog


def index_text_for_chunk(
    chunk: Chunk,
    catalog: Optional[Dict[str, Dict[str, str]]] = None,
    argparse_enrich: bool = False,
) -> str:
    content = getattr(chunk, "content", None) or ""
    if not argparse_enrich:
        return content
    name = getattr(chunk, "entity_name", None) or ""
    info = (catalog or {}).get(name)
    if not info:
        return content
    cmd = info.get("name") or name
    help_text = info.get("help") or ""
    prefix = f"CLI command {cmd}. Handler {name}."
    if help_text:
        prefix = f"{prefix} Help: {help_text}."
    return f"{prefix}\n{content}"


def infer_cli_symbols(query: str, known: Optional[Set[str]] = None) -> Set[str]:
    """Handler / class names implied by a natural-language CLI or config question."""
    text = (query or "").strip().lower()
    names: Set[str] = set()
    if not text:
        return names
    for match in _NAMED_CLI_RE.finditer(text):
        stem = match.group(1).replace("-", "_")
        if stem not in _CLI_NAME_STOP:
            names.add(f"cmd_{stem}")
    for match in _CLI_THEN_NAME_RE.finditer(text):
        stem = match.group(1).replace("-", "_")
        if stem not in _CLI_NAME_STOP:
            names.add(f"cmd_{stem}")
    if _CONFIG_HINT_RE.search(query or ""):
        names.add("Config")
        names.add("DEFAULT_CONFIG")
    if known:
        known_l = {k.lower(): k for k in known}
        names = {known_l[n.lower()] for n in names if n.lower() in known_l}
    return names


def make_cli_doc_chunks(
    chunks: Sequence[Chunk],
    catalog: Optional[Dict[str, Dict[str, str]]] = None,
) -> List[Chunk]:
    """Tiny catalog docs that share the gold entity id of the real handler / Config."""
    catalog = catalog or argparse_catalog_from_chunks(chunks)
    seen: Set[str] = set()
    docs: List[Chunk] = []
    by_name: Dict[str, Chunk] = {}
    for chunk in chunks:
        name = getattr(chunk, "entity_name", None) or ""
        if name and name not in by_name:
            by_name[name] = chunk

    for func, info in catalog.items():
        real = by_name.get(func)
        if real is None:
            continue
        key = real.entity_id or func
        if key in seen:
            continue
        seen.add(key)
        cmd = info.get("name") or func
        help_text = info.get("help") or ""
        body = (
            f"The {cmd} CLI command is defined as {func} in {real.file_path}. "
            f"argparse add_parser({cmd!r})."
        )
        if help_text:
            body = f"{body} Help: {help_text}."
        docs.append(
            Chunk(
                id=f"{real.entity_id}:cli-doc",
                content=body,
                entity_id=real.entity_id,
                entity_name=real.entity_name,
                entity_type=getattr(real, "entity_type", None) or EntityType.FUNCTION,
                file_path=real.file_path,
                start_line=0,
                end_line=0,
                docstring=real.docstring,
                repo_name=real.repo_name,
                metadata={**(real.metadata or {}), "kind": "cli_doc", "generated": True},
            )
        )

    config_chunk = None
    for chunk in chunks:
        path = getattr(chunk, "file_path", None) or ""
        if (getattr(chunk, "entity_name", None) == "Config") and path.endswith("config.py"):
            config_chunk = chunk
            break
    if config_chunk is not None:
        docs.append(
            Chunk(
                id=f"{config_chunk.entity_id}:cli-doc",
                content=(
                    "Default retrieval weights, top_k, and cross-encoder settings "
                    "are defined on Config and DEFAULT_CONFIG in harness/config.py. "
                    "dense_weight, sparse_weight, graph_weight, rerank_top_k, "
                    "cross_encoder.enabled / mode."
                ),
                entity_id=config_chunk.entity_id,
                entity_name=config_chunk.entity_name,
                entity_type=getattr(config_chunk, "entity_type", None) or EntityType.CLASS,
                file_path=config_chunk.file_path,
                start_line=0,
                end_line=0,
                docstring=config_chunk.docstring,
                repo_name=config_chunk.repo_name,
                metadata={**(config_chunk.metadata or {}), "kind": "cli_doc", "generated": True},
            )
        )
    return docs


def index_texts(
    chunks: Sequence[Chunk],
    config=None,
    catalog: Optional[Dict[str, Dict[str, str]]] = None,
) -> List[str]:
    cfg = cli_retrieval_cfg(config)
    if cfg["argparse_enrich"] and catalog is None:
        catalog = argparse_catalog_from_chunks(chunks)
    return [
        index_text_for_chunk(chunk, catalog, argparse_enrich=cfg["argparse_enrich"])
        for chunk in chunks
    ]


def apply_cli_retrieval_config(config, args=None, environ=None) -> object:
    """Fold CLI flags / env into ``config.retrieval.cli_retrieval``."""
    env = environ if environ is not None else os.environ
    cli = cli_retrieval_cfg(config)

    def _truthy(raw: str) -> Optional[bool]:
        text = (raw or "").strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        return None

    if args is not None:
        if getattr(args, "ident_split", None):
            cli["ident_split"] = True
        if getattr(args, "no_ident_split", False):
            cli["ident_split"] = False
        if getattr(args, "argparse_enrich", None):
            cli["argparse_enrich"] = True
        if getattr(args, "no_argparse_enrich", False):
            cli["argparse_enrich"] = False
        if getattr(args, "cli_doc_index", None):
            cli["doc_index"] = True
        if getattr(args, "no_cli_doc_index", False):
            cli["doc_index"] = False
        boost = getattr(args, "cli_symbol_boost", None)
        if boost is not None:
            cli["symbol_boost"] = float(boost)

    ident_env = _truthy(env.get("CODEHARNESS_IDENT_SPLIT", ""))
    if ident_env is not None and not getattr(args, "ident_split", None) and not getattr(args, "no_ident_split", False):
        cli["ident_split"] = ident_env
    enrich_env = _truthy(env.get("CODEHARNESS_ARGPARSE_ENRICH", ""))
    if enrich_env is not None and not getattr(args, "argparse_enrich", None) and not getattr(args, "no_argparse_enrich", False):
        cli["argparse_enrich"] = enrich_env
    doc_env = _truthy(env.get("CODEHARNESS_CLI_DOC_INDEX", ""))
    if doc_env is not None and not getattr(args, "cli_doc_index", None) and not getattr(args, "no_cli_doc_index", False):
        cli["doc_index"] = doc_env
    boost_env = env.get("CODEHARNESS_CLI_SYMBOL_BOOST", "").strip()
    if boost_env and getattr(args, "cli_symbol_boost", None) is None:
        try:
            cli["symbol_boost"] = float(boost_env)
        except ValueError:
            pass

    config.retrieval["cli_retrieval"] = cli
    return config
