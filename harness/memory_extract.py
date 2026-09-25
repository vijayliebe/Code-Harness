"""Heuristic (offline) typed-memory extract from session / query traces.

Default path uses regex + retrieve cites only — no LLM call. Optional
``refine_candidates`` runs only when a client and key are present.
Writes happen only when dry-run is off and the command was invoked or
``memory.auto_extract`` is true.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from .okf import cite_from_entity_id, cite_path_symbol


KINDS = ("decision", "error", "preference", "fact")

_HASH_SUFFIX = re.compile(r":[0-9a-fA-F]{4,16}$")
_PATH_SYMBOL_RE = re.compile(
    r"`?([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+):([A-Za-z_][A-Za-z0-9_\.]*)`?"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)

_ERROR_RE = re.compile(
    r"\b("
    r"OOM|OutOfMemory(?:Error)?|ImportError|ModuleNotFoundError|MemoryError|"
    r"AttributeError|TypeError|ValueError|KeyError|RuntimeError|"
    r"Traceback|fixed by|failed with|blew RAM"
    r")\b|"
    r"^\s*File \"",
    re.IGNORECASE | re.MULTILINE,
)
_STACKISH_RE = re.compile(
    r"Traceback \(most recent call last\)|^\s+File \".+\"|MemoryError|ImportError",
    re.IGNORECASE | re.MULTILINE,
)
_DECISION_RE = re.compile(
    r"\b("
    r"we use\b.+\binstead of\b|"
    r"(?:we\s+)?(?:chose|choose)\b|"
    r"default is\b|"
    r"we decided\b|"
    r"going with\b|"
    r"will use\b|"
    r"switch(?:ing)? to\b|"
    r"keep\b.+\buntil\b"
    r")",
    re.IGNORECASE,
)
_WEAK_CLAIM_RE = re.compile(
    r"^(see(?:\s+also)?|cf|via|from)\b",
    re.IGNORECASE,
)
_PREF_RE = re.compile(
    r"\b("
    r"always use\b|"
    r"prefer\b|"
    r"don't\b|"
    r"do not\b|"
    r"never (?:use|call|do)\b|"
    r"please use\b"
    r")",
    re.IGNORECASE,
)


@dataclass
class ExtractTurn:
    role: str
    text: str
    chunk_ids: List[str] = field(default_factory=list)


@dataclass
class MemoryCandidate:
    kind: str
    title: str
    body: str
    links: List[str] = field(default_factory=list)
    source: str = ""


@dataclass
class ExtractResult:
    candidates: List[MemoryCandidate] = field(default_factory=list)
    written: List[Any] = field(default_factory=list)
    superseded: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    dry_run: bool = False
    auto: bool = False


TurnLike = Union[ExtractTurn, Dict[str, Any], Any]


def auto_extract_enabled(config: Any) -> bool:
    if config is None:
        return False
    mem = getattr(config, "memory", None) or {}
    if not isinstance(mem, dict):
        return False
    return bool(mem.get("auto_extract"))


def should_write(*, dry_run: bool, invoked: bool, auto: bool) -> bool:
    return (not dry_run) and (invoked or auto)


def latest_session_path(session_dir: str) -> Optional[str]:
    if not session_dir or not os.path.isdir(session_dir):
        return None
    files = [
        os.path.join(session_dir, name)
        for name in os.listdir(session_dir)
        if name.endswith(".jsonl")
    ]
    if not files:
        return None
    files.sort()
    return files[-1]


def load_session_jsonl(path: str) -> List[ExtractTurn]:
    turns: List[ExtractTurn] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            event = row.get("event")
            if event == "session_start" or event == "compact" or event == "profile":
                continue
            if event == "query":
                query = row.get("query") or row.get("text") or ""
                if query:
                    turns.append(ExtractTurn(role="user", text=str(query), chunk_ids=[]))
                answer = row.get("answer") or ""
                if answer:
                    turns.append(
                        ExtractTurn(
                            role="assistant",
                            text=str(answer),
                            chunk_ids=[str(x) for x in (row.get("chunk_ids") or [])],
                        )
                    )
                continue
            role = row.get("role")
            text = row.get("text")
            if role and text is not None and role not in ("compact",):
                turns.append(
                    ExtractTurn(
                        role=str(role),
                        text=str(text),
                        chunk_ids=[str(x) for x in (row.get("chunk_ids") or [])],
                    )
                )
    return turns


def turns_from_session(session: Any) -> List[ExtractTurn]:
    path = None
    try:
        path = session.jsonl_path()
    except Exception:
        path = None
    if path and os.path.isfile(path):
        loaded = load_session_jsonl(path)
        if loaded:
            return loaded
    out: List[ExtractTurn] = []
    for turn in getattr(session, "turns", None) or []:
        role = getattr(turn, "role", "") or ""
        if role == "compact":
            continue
        out.append(
            ExtractTurn(
                role=role,
                text=getattr(turn, "text", "") or "",
                chunk_ids=list(getattr(turn, "chunk_ids", None) or []),
            )
        )
    return out


def links_from_cites(text: str, chunk_ids: Optional[Sequence[str]] = None) -> List[str]:
    links: List[str] = []
    for cid in chunk_ids or []:
        link = _link_from_chunk_id(str(cid))
        if link:
            links.append(link)
    for match in _PATH_SYMBOL_RE.finditer(text or ""):
        links.append(cite_path_symbol(match.group(1), match.group(2)))
    return _uniq(links)


def extract_candidates(turns: Iterable[TurnLike]) -> List[MemoryCandidate]:
    found: List[MemoryCandidate] = []
    for index, raw in enumerate(turns or []):
        turn = _as_turn(raw)
        if turn.role not in ("user", "assistant", "query"):
            continue
        found.extend(_extract_from_turn(turn, source=f"turn:{index}:{turn.role}"))
    return _dedupe_candidates(found)


def refine_candidates(
    candidates: Sequence[MemoryCandidate],
    *,
    llm: Any = None,
    config: Any = None,
) -> List[MemoryCandidate]:
    """Optional LLM polish. No-ops without a ready client/key (no network)."""
    current = list(candidates)
    if not current or llm is None:
        return current
    if not _llm_ready(llm, config):
        return current
    payload = [
        {
            "kind": c.kind,
            "title": c.title,
            "body": c.body,
            "links": list(c.links),
        }
        for c in current
    ]
    instruction = (
        "Refine these typed memory candidates. Return a JSON list of objects "
        "with keys kind, title, body, links. Do not add new items. "
        "kind must be one of decision, error, preference, fact.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        raw = llm.query(
            "You refine existing typed memory candidates. Reply with JSON only.",
            instruction,
            "refine memory candidates",
        )
        parsed = _parse_json_list(raw)
        if not parsed or len(parsed) != len(current):
            return current
        refined: List[MemoryCandidate] = []
        for src, row in zip(current, parsed):
            if not isinstance(row, dict):
                return current
            kind = str(row.get("kind") or src.kind).strip().lower()
            if kind not in KINDS:
                kind = src.kind
            title = str(row.get("title") or src.title).strip() or src.title
            body = str(row.get("body") or src.body).strip() or src.body
            links = row.get("links") if isinstance(row.get("links"), list) else src.links
            refined.append(
                MemoryCandidate(
                    kind=kind,
                    title=title,
                    body=body,
                    links=[str(x) for x in links if str(x).strip()],
                    source=src.source,
                )
            )
        return refined
    except Exception:
        return current


def run_extract(
    *,
    turns: Optional[Iterable[TurnLike]] = None,
    candidates: Optional[Sequence[MemoryCandidate]] = None,
    store: Any,
    dry_run: bool = False,
    invoked: bool = False,
    auto: bool = False,
    llm: Any = None,
    config: Any = None,
    use_llm: bool = False,
) -> ExtractResult:
    rows = list(candidates) if candidates is not None else extract_candidates(turns or [])
    if use_llm:
        rows = refine_candidates(rows, llm=llm, config=config)
    result = ExtractResult(candidates=rows, dry_run=bool(dry_run), auto=bool(auto))
    if not should_write(dry_run=bool(dry_run), invoked=bool(invoked), auto=bool(auto)):
        return result
    for cand in rows:
        match = _find_match(store, cand)
        if match is not None and _same_content(match, cand):
            result.skipped.append(match.id)
            continue
        supersedes = match.id if match is not None else None
        entry = store.add(
            kind=cand.kind,
            title=cand.title,
            body=cand.body or cand.title,
            links=cand.links,
            tags=["extract"],
            supersedes=supersedes,
            generated=True,
            verified="heuristic",
        )
        result.written.append(entry)
        if supersedes:
            result.superseded.append(supersedes)
    return result


def maybe_auto_extract(
    *,
    turns: Optional[Iterable[TurnLike]] = None,
    store: Any,
    config: Any = None,
    session: Any = None,
) -> ExtractResult:
    if not auto_extract_enabled(config):
        return ExtractResult(candidates=[], written=[], dry_run=False, auto=False)
    source = turns
    if session is not None:
        source = turns_from_session(session)
    return run_extract(
        turns=source or [],
        store=store,
        dry_run=False,
        invoked=False,
        auto=True,
        config=config,
        use_llm=bool((getattr(config, "memory", None) or {}).get("llm_refine")),
    )


def format_extract(result: ExtractResult, session_path: str = "") -> str:
    mode = "dry-run" if result.dry_run else ("auto" if result.auto else "wrote")
    header = f"[*] memory extract ({mode})"
    if session_path:
        header += f" from {session_path}"
    lines = [header]
    for cand in result.candidates:
        lines.append(f"    [{cand.kind}] {cand.title}")
        snippet = _SPACE_RE.sub(" ", cand.body or "").strip()
        if snippet and snippet != cand.title:
            if len(snippet) > 160:
                snippet = snippet[:157].rstrip() + "..."
            lines.append(f"      {snippet}")
        if cand.links:
            lines.append(f"      links: {', '.join(cand.links)}")
        if not result.dry_run:
            match_written = next(
                (
                    e
                    for e in result.written
                    if getattr(e, "title", None) == cand.title
                    and getattr(e, "kind", None) == cand.kind
                ),
                None,
            )
            if match_written is not None and getattr(match_written, "supersedes", None):
                lines.append(f"      supersedes: {match_written.supersedes}")
    lines.append(
        f"[*] candidates={len(result.candidates)} "
        f"written={len(result.written)} skipped={len(result.skipped)}"
    )
    return "\n".join(lines)


def resolve_session_dir(repo: str, config: Any = None) -> str:
    raw = ".code-harness/sessions"
    if config is not None:
        raw = (getattr(config, "session", None) or {}).get("dir") or raw
    if os.path.isabs(raw):
        return raw
    under_repo = os.path.join(repo or ".", raw)
    if os.path.isdir(under_repo):
        return under_repo
    cwd = os.path.abspath(raw)
    if os.path.isdir(cwd):
        return cwd
    return under_repo


def _as_turn(raw: TurnLike) -> ExtractTurn:
    if isinstance(raw, ExtractTurn):
        return raw
    if isinstance(raw, dict):
        role = str(raw.get("role") or "user")
        text = raw.get("text")
        if text is None:
            text = raw.get("query") or ""
        return ExtractTurn(
            role=role,
            text=str(text),
            chunk_ids=[str(x) for x in (raw.get("chunk_ids") or [])],
        )
    return ExtractTurn(
        role=str(getattr(raw, "role", None) or "user"),
        text=str(getattr(raw, "text", "") or ""),
        chunk_ids=[str(x) for x in (getattr(raw, "chunk_ids", None) or [])],
    )


def _extract_from_turn(turn: ExtractTurn, source: str) -> List[MemoryCandidate]:
    text = turn.text or ""
    if not text.strip():
        return []
    links = links_from_cites(text, turn.chunk_ids)
    sentences = _sentences(text)
    buckets: Dict[str, List[str]] = {kind: [] for kind in KINDS}
    classified: List[str] = []
    for sent in sentences:
        kind = _classify_sentence(sent)
        if kind:
            buckets[kind].append(sent)
            classified.append(sent)
    if _is_stackish(text) and not buckets["error"]:
        buckets["error"].append(_first_line(text) or text[:220])
    for sent in sentences:
        if sent in classified:
            continue
        if _PATH_SYMBOL_RE.search(sent) and _looks_like_claim(sent):
            buckets["fact"].append(sent)
    out: List[MemoryCandidate] = []
    for kind, sents in buckets.items():
        if not sents:
            continue
        title = _title_from(sents[0], kind)
        body = " ".join(sents).strip()
        if not title or not body:
            continue
        out.append(
            MemoryCandidate(
                kind=kind,
                title=title,
                body=body,
                links=list(links),
                source=source,
            )
        )
    return out


def _classify_sentence(sent: str) -> Optional[str]:
    if _ERROR_RE.search(sent):
        return "error"
    if _DECISION_RE.search(sent):
        return "decision"
    if _PREF_RE.search(sent):
        return "preference"
    return None


def _is_stackish(text: str) -> bool:
    return bool(_STACKISH_RE.search(text or ""))


def _looks_like_claim(sent: str) -> bool:
    stripped = _PATH_SYMBOL_RE.sub(" ", sent or "")
    stripped = stripped.replace("`", " ").strip(" \t.,;:-")
    if not stripped or _WEAK_CLAIM_RE.match(stripped):
        return False
    return bool(re.search(r"[A-Za-z]{4,}", stripped))


def _sentences(text: str) -> List[str]:
    parts = _SENTENCE_SPLIT.split(text or "")
    return [p.strip() for p in parts if p.strip()]


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _title_from(sent: str, kind: str) -> str:
    text = (sent or "").replace("`", "").strip().rstrip(".!?")
    text = _SPACE_RE.sub(" ", text).strip()
    if len(text) > 100:
        text = text[:97].rstrip() + "..."
    return text or kind.capitalize()


def _normalize_title(title: str) -> str:
    return _PUNCT_RE.sub("", _SPACE_RE.sub(" ", (title or "").strip().lower())).strip()


def _norm_text(text: str) -> str:
    return _SPACE_RE.sub(" ", (text or "").strip().lower())


def _same_content(entry: Any, cand: MemoryCandidate) -> bool:
    return (
        _normalize_title(getattr(entry, "title", "")) == _normalize_title(cand.title)
        and _norm_text(getattr(entry, "body", "")) == _norm_text(cand.body)
    )


def _find_match(store: Any, cand: MemoryCandidate) -> Optional[Any]:
    active = store.list(include_inactive=False)
    title_key = _normalize_title(cand.title)
    for entry in active:
        if _normalize_title(entry.title) == title_key:
            return entry
    cand_links = set(cand.links or [])
    if not cand_links:
        return None
    for entry in active:
        if entry.kind == cand.kind and cand_links & set(entry.links or []):
            return entry
    return None


def _dedupe_candidates(cands: Sequence[MemoryCandidate]) -> List[MemoryCandidate]:
    groups: List[MemoryCandidate] = []
    for cand in cands:
        placed = False
        for existing in groups:
            same_title = _normalize_title(existing.title) == _normalize_title(cand.title)
            shared = set(existing.links or []) & set(cand.links or [])
            if same_title or (cand.kind == existing.kind and shared):
                existing.links = _uniq(list(existing.links) + list(cand.links))
                extra = cand.body.strip()
                if extra and extra not in existing.body:
                    existing.body = (existing.body + " " + extra).strip()
                placed = True
                break
        if not placed:
            groups.append(
                MemoryCandidate(
                    kind=cand.kind,
                    title=cand.title,
                    body=cand.body,
                    links=list(cand.links),
                    source=cand.source,
                )
            )
    return groups


def _link_from_chunk_id(chunk_id: str) -> str:
    text = (chunk_id or "").replace("\\", "/")
    text = _HASH_SUFFIX.sub("", text)
    return cite_from_entity_id(text)


def _uniq(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        key = (item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _llm_ready(llm: Any, config: Any) -> bool:
    if llm is None:
        return False
    provider = None
    if config is not None:
        provider = (getattr(config, "llm", None) or {}).get("provider")
    if provider in ("ollama", "custom"):
        return True
    return bool(getattr(llm, "api_key", None))


def _parse_json_list(raw: str) -> Optional[List[Any]]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, list) else None
            except json.JSONDecodeError:
                return None
        return None
