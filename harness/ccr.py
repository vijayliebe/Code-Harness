"""CCR-lite: pack signatures + key spans; cache originals for retrieve-back."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Dict, Optional

from .models import Chunk, EntityType, PackedChunk

DEFAULT_FIRST_LINES = 12
DEFAULT_LAST_LINES = 8
DEFAULT_OMIT_THRESHOLD = 8
DEFAULT_SPILL_DIR = os.path.join(".code-harness", "ccr")

_EXT_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
}

_DOCSTRING_RE = re.compile(
    r"^\s*(?:\"\"\"|''')([\s\S]*?)(?:\"\"\"|''')",
    re.MULTILINE,
)


def sanitize_chunk_id(chunk_id: str) -> str:
    """Filesystem-safe cache key that stays unique for path-like ids."""
    digest = hashlib.sha1(chunk_id.encode("utf-8")).hexdigest()[:10]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", chunk_id).strip("._") or "chunk"
    return f"{safe[:120]}.{digest}"


def fence_lang(file_path: str) -> str:
    ext = os.path.splitext(file_path or "")[1].lower()
    return _EXT_LANG.get(ext, "")


def chunk_signature(chunk: Chunk) -> str:
    et = chunk.entity_type
    if isinstance(et, EntityType):
        et_val = et
    else:
        try:
            et_val = EntityType(str(et))
        except ValueError:
            et_val = None
    name = chunk.entity_name or ""
    meta = chunk.metadata or {}
    params = meta.get("params")
    bases = meta.get("bases")
    if et_val in (EntityType.FUNCTION, EntityType.METHOD):
        if isinstance(params, list):
            return f"{name}({', '.join(str(p) for p in params)})"
        first = (chunk.content or "").split("\n", 1)[0].strip()
        return first[:200] if first else name
    if et_val == EntityType.CLASS:
        if isinstance(bases, list) and bases:
            return f"{name}({', '.join(str(b) for b in bases)})"
        return name or (chunk.content or "").split("\n", 1)[0].strip()[:200]
    return name or (chunk.file_path or chunk.id)


def chunk_doc_preview(chunk: Chunk, max_len: int = 160) -> str:
    raw = (chunk.docstring or "").strip()
    if not raw:
        match = _DOCSTRING_RE.search(chunk.content or "")
        if match:
            raw = match.group(1).strip()
    if not raw:
        return ""
    first = raw.split("\n\n", 1)[0].replace("\n", " ").strip()
    for sep in ".!?":
        idx = first.find(sep)
        if 0 <= idx <= 140:
            first = first[: idx + 1]
            break
    if len(first) > max_len:
        first = first[: max_len - 1] + "…"
    return first


def pack_chunk(
    chunk: Chunk,
    first_lines: int = DEFAULT_FIRST_LINES,
    last_lines: int = DEFAULT_LAST_LINES,
    omit_threshold: int = DEFAULT_OMIT_THRESHOLD,
) -> PackedChunk:
    """Compress one chunk to signature + docstring + first/last spans."""
    lines = (chunk.content or "").splitlines()
    et = chunk.entity_type.value if hasattr(chunk.entity_type, "value") else str(chunk.entity_type)
    header = (
        f"### {chunk.id}  {et}  "
        f"{chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
    )
    lang = fence_lang(chunk.file_path)
    fence = f"```{lang}" if lang else "```"
    keep = first_lines + last_lines + omit_threshold
    char_keep = keep * 16
    # Never overlap first/last spans — that produced "(-1 lines omitted)".
    can_split = len(lines) > first_lines + last_lines
    should_omit = can_split and (
        len(lines) > keep or len(chunk.content or "") > char_keep
    )
    if not should_omit:
        omitted = False
        omitted_n = 0
        code = f"{fence}\n{chunk.content}\n```"
    else:
        omitted = True
        omitted_n = len(lines) - first_lines - last_lines
        head = "\n".join(lines[:first_lines])
        tail = "\n".join(lines[-last_lines:])
        code = (
            f"{fence}\n{head}\n"
            f"… ({omitted_n} lines omitted; retrieve_chunk {chunk.id})\n"
            f"{tail}\n```"
        )
    parts = [header, chunk_signature(chunk)]
    doc = chunk_doc_preview(chunk)
    if doc:
        parts.append(doc)
    parts.append("")
    parts.append(code)
    return PackedChunk(
        id=chunk.id,
        preview="\n".join(parts) + "\n",
        omitted=omitted,
        omitted_line_count=omitted_n,
        original=chunk.content,
    )


class CCRCache:
    """Process-local originals, optionally spilled under `.code-harness/ccr/`."""

    def __init__(self, spill_dir: Optional[str] = None):
        self._mem: Dict[str, str] = {}
        self.spill_dir = spill_dir
        self._index: Dict[str, str] = {}
        if spill_dir:
            os.makedirs(spill_dir, exist_ok=True)
            self._load_index()

    def _index_path(self) -> str:
        return os.path.join(self.spill_dir or "", "index.json")

    def _load_index(self) -> None:
        path = self._index_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path) as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                self._index = {str(k): str(v) for k, v in raw.items()}
        except (OSError, ValueError):
            self._index = {}

    def _write_index(self) -> None:
        if not self.spill_dir:
            return
        path = self._index_path()
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self._index, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)

    def put(self, chunk_id: str, text: str) -> None:
        self._mem[chunk_id] = text
        if not self.spill_dir:
            return
        name = sanitize_chunk_id(chunk_id) + ".txt"
        dest = os.path.join(self.spill_dir, name)
        with open(dest, "w") as fh:
            fh.write(text)
        self._index[chunk_id] = name
        self._write_index()

    def get(self, chunk_id: str) -> Optional[str]:
        if chunk_id in self._mem:
            return self._mem[chunk_id]
        if not self.spill_dir:
            return None
        name = self._index.get(chunk_id) or (sanitize_chunk_id(chunk_id) + ".txt")
        path = os.path.join(self.spill_dir, name)
        if not os.path.isfile(path):
            return None
        with open(path) as fh:
            text = fh.read()
        self._mem[chunk_id] = text
        return text

    def __contains__(self, chunk_id: str) -> bool:
        return self.get(chunk_id) is not None


def retrieve_chunk(
    chunk_id: str,
    cache: Optional[CCRCache] = None,
    spill_dir: Optional[str] = None,
) -> Optional[str]:
    """Materialize a packed original from memory or the on-disk CCR spill."""
    if cache is not None:
        found = cache.get(chunk_id)
        if found is not None:
            return found
    directory = spill_dir
    if directory is None and cache is not None:
        directory = cache.spill_dir
    if directory is None:
        directory = DEFAULT_SPILL_DIR
    if directory and os.path.isdir(directory):
        return CCRCache(spill_dir=directory).get(chunk_id)
    return None
