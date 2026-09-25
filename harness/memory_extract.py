"""Heuristic (offline) typed-memory extract from session JSONL.

Default path is regex/cue based — no LLM. ``--llm`` refines only when a
client and key exist; otherwise it is a clean no-op. Writes go through
:class:`harness.memory.MemoryStore` (supersede / dedupe, ``path:symbol``).
Auto-extract after ``/compact`` or session exit is opt-in
(``memory.auto_extract``, default false).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import Config
from .memory import MemoryEntry, MemoryStore, _tokenize
from .okf import _SYMBOL_KINDS, cite_from_entity_id, cite_path_symbol
from .session import Session, SessionTurn


class MemoryExtractError(ValueError):
    """No session to read, or extract inputs are invalid."""


@dataclass
class ExtractedMemory:
    kind: str
    title: str
    body: str
    links: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


@dataclass
class ExtractReport:
    candidates: List[ExtractedMemory] = field(default_factory=list)
    written: List[MemoryEntry] = field(default_factory=list)
    superseded: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    dry_run: bool = False
    used_llm: bool = False
    session_path: str = ""


_ERROR_CUES = re.compile(
    r"\b(oom(?:'d)?|out of memory|blew ram|traceback|exception|"
    r"crash(?:ed)?|error|failed|failure|bug|broke|regression)\b",
    re.I,
)
_DECISION_CUES = re.compile(
    r"\b((?:we\s+)?decided(?:\s+to)?|decision:|we(?:'ll| will) use|"
    r"default (?:is|stays|remains)|keep \w[\w-]* as the default|"
    r"going with|chose|choose to|instead of)\b",
    re.I,
)
_PREFERENCE_CUES = re.compile(
    r"\b(prefer(?:ence)?(?:\s+noted)?|please always|always use|"
    r"don'?t use|do not use|never use|i(?:'d| would) rather)\b",
    re.I,
)
_FACT_CUES = re.compile(
    r"\b(fact:|note that|for the record|is google spec|okf is|"
    r"implements it)\b",
    re.I,
)
_LEAD_STRIP = re.compile(
    r"^(?:we\s+)?(?:decided to|decided|will|we'll|should|fact:|"
    r"note that|preference:|prefer to|prefer|error:)\s+",
    re.I,
)
_FIRST_SENTENCE = re.compile(r"(.+?[.!?])(?:\s|$)", re.DOTALL)
_BACKTICK = re.compile(r"`([^`]+)`")
_PATH_SYMBOL = re.compile(
    r"\b([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+:[A-Za-z_][A-Za-z0-9_.]*)\b"
)
_HEX_SUFFIX = re.compile(r"^[0-9a-f]{4,}$")
_KIND_PRIORITY = ("error", "decision", "preference", "fact")


def extract_session(
    repo: str = ".",
    session_path: Optional[str] = None,
    store: Optional[MemoryStore] = None,
    *,
    dry_run: bool = False,
    use_llm: bool = False,
    config: Optional[Config] = None,
    llm: Any = None,
    session: Optional[Session] = None,
    session_dir: Optional[str] = None,
) -> ExtractReport:
    """Extract typed memories from a session JSONL (or in-memory turns)."""
    cfg = config or Config()
    repo_path = os.path.abspath(repo or getattr(cfg, "repo_path", None) or ".")
    cfg.repo_path = repo_path
    path = resolve_session_path(
        repo_path,
        session_path=session_path,
        session_dir=session_dir,
        config=cfg,
        session=session,
    )
    turns = load_session_turns(path) if path else []
    if not turns and session is not None:
        turns = list(session.turns or [])
    if not turns and not path:
        raise MemoryExtractError(
            f"no session JSONL found under {_session_dir(repo_path, session_dir, cfg)}"
        )

    candidates = extract_candidates(turns)
    candidates, used_llm = maybe_refine_with_llm(
        candidates, use_llm=use_llm, llm=llm, config=cfg
    )
    memory_store = store or MemoryStore(repo_path)
    written: List[MemoryEntry] = []
    superseded: List[str] = []
    skipped: List[str] = []
    for cand in candidates:
        action, existing = reconcile_candidate(memory_store, cand)
        if action == "skip":
            skipped.append(existing.id if existing is not None else cand.title)
            continue
        if dry_run:
            if action == "supersede" and existing is not None:
                superseded.append(existing.id)
            continue
        supersedes = existing.id if action == "supersede" and existing is not None else None
        entry = memory_store.add(
            kind=cand.kind,
            title=cand.title,
            body=cand.body,
            links=cand.links,
            tags=cand.tags or ["extracted"],
            supersedes=supersedes,
            generated=True,
            verified="heuristic",
        )
        written.append(entry)
        if supersedes:
            superseded.append(supersedes)
    return ExtractReport(
        candidates=candidates,
        written=written,
        superseded=superseded,
        skipped=skipped,
        dry_run=bool(dry_run),
        used_llm=bool(used_llm),
        session_path=path or "",
    )


def maybe_auto_extract(
    *,
    config: Config,
    session: Optional[Session] = None,
    session_path: Optional[str] = None,
    store: Optional[MemoryStore] = None,
    repo: Optional[str] = None,
    use_llm: bool = False,
    dry_run: bool = False,
) -> Optional[ExtractReport]:
    """Run extract after compact/exit only when ``memory.auto_extract`` is true."""
    memory_cfg = getattr(config, "memory", None) or {}
    if not memory_cfg.get("auto_extract"):
        return None
    repo_path = repo or getattr(config, "repo_path", None) or "."
    if session is not None and not repo:
        repo_path = getattr(session, "repo", None) or repo_path
    return extract_session(
        repo=repo_path,
        session_path=session_path,
        store=store,
        dry_run=dry_run,
        use_llm=use_llm,
        config=config,
        session=session,
    )


def extract_candidates(turns: Sequence[SessionTurn]) -> List[ExtractedMemory]:
    pairs = _dialogue_pairs(turns)
    raw: List[ExtractedMemory] = []
    for user, assistant in pairs:
        cand = _candidate_from_pair(user, assistant)
        if cand is not None:
            raw.append(cand)
    return _dedupe_candidates(raw)


def load_session_turns(path: str) -> List[SessionTurn]:
    if not path or not os.path.isfile(path):
        return []
    turns: List[SessionTurn] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") != "turn":
                continue
            turns.append(
                SessionTurn(
                    role=str(row.get("role") or "user"),
                    text=str(row.get("text") or ""),
                    chunk_ids=list(row.get("chunk_ids") or []),
                    packed_tokens=int(row.get("packed_tokens") or 0),
                    full_tokens=int(row.get("full_tokens") or 0),
                    completion_tokens=int(row.get("completion_tokens") or 0),
                    loop_attempts=int(row.get("loop_attempts") or 0),
                    pack_mode=str(row.get("pack_mode") or "full"),
                )
            )
    return turns


def latest_session_jsonl(session_dir: str) -> Optional[str]:
    if not session_dir or not os.path.isdir(session_dir):
        return None
    files = [
        os.path.join(session_dir, name)
        for name in os.listdir(session_dir)
        if name.endswith(".jsonl") and os.path.isfile(os.path.join(session_dir, name))
    ]
    if not files:
        return None
    files.sort(key=lambda p: (os.path.getmtime(p), os.path.basename(p)), reverse=True)
    return files[0]


def resolve_session_path(
    repo: str,
    *,
    session_path: Optional[str] = None,
    session_dir: Optional[str] = None,
    config: Optional[Config] = None,
    session: Optional[Session] = None,
) -> Optional[str]:
    if session_path:
        return os.path.abspath(session_path)
    if session is not None:
        try:
            path = session.jsonl_path()
        except ValueError:
            path = ""
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    directory = _session_dir(repo, session_dir, config)
    return latest_session_jsonl(directory)


def reconcile_candidate(
    store: MemoryStore, cand: ExtractedMemory
) -> Tuple[str, Optional[MemoryEntry]]:
    """Return ``(add|skip|supersede, existing_or_none)``."""
    best: Optional[MemoryEntry] = None
    best_title = 0.0
    best_body = 0.0
    best_links = False
    for entry in store.list(kind=cand.kind, include_inactive=False):
        title_o = _overlap(cand.title, entry.title)
        body_o = _overlap(cand.body, entry.body)
        links_o = bool(set(cand.links) & set(entry.links))
        score = title_o + body_o + (0.35 if links_o else 0.0)
        best_score = best_title + best_body + (0.35 if best_links else 0.0)
        if best is None or score > best_score:
            best = entry
            best_title = title_o
            best_body = body_o
            best_links = links_o
    if best is None:
        return "add", None
    if best_body >= 0.55 or (best_title >= 0.7 and best_body >= 0.3):
        return "skip", best
    if best_title >= 0.35 or best_links:
        return "supersede", best
    return "add", None


def maybe_refine_with_llm(
    candidates: List[ExtractedMemory],
    *,
    use_llm: bool,
    llm: Any = None,
    config: Optional[Config] = None,
) -> Tuple[List[ExtractedMemory], bool]:
    if not use_llm:
        return candidates, False
    client_llm = llm
    if client_llm is None:
        try:
            from .llm import LLMInterface

            client_llm = LLMInterface(config or Config())
        except Exception:
            return candidates, False
    if not _llm_ready(client_llm, config or getattr(client_llm, "config", None)):
        return candidates, False
    try:
        if getattr(client_llm, "client", None) is None:
            return candidates, False
    except Exception:
        return candidates, False
    try:
        return _refine_with_llm(candidates, client_llm), True
    except Exception:
        return candidates, False


def format_extract_report(report: ExtractReport) -> str:
    if not report.candidates:
        return "[*] extract: no typed memories found"
    if report.dry_run:
        head = f"[*] extract: {len(report.candidates)} candidate(s) (dry-run)"
    else:
        head = (
            f"[+] extract: wrote {len(report.written)}, "
            f"superseded {len(report.superseded)}, skipped {len(report.skipped)}"
        )
    if report.session_path:
        head += f"\n    session={report.session_path}"
    lines = [head]
    rows = report.written if report.written else report.candidates
    for item in rows:
        kind = getattr(item, "kind", "")
        title = getattr(item, "title", "")
        links = getattr(item, "links", None) or []
        cites = f"  {', '.join(links)}" if links else ""
        lines.append(f"    [{kind}] {title}{cites}")
    return "\n".join(lines)


def _session_dir(
    repo: str,
    session_dir: Optional[str],
    config: Optional[Config],
) -> str:
    if session_dir:
        raw = session_dir
    else:
        raw = ((getattr(config, "session", None) or {}).get("dir") if config else None) or os.path.join(
            ".code-harness", "sessions"
        )
    if os.path.isabs(raw):
        return raw
    return os.path.join(os.path.abspath(repo or "."), raw)


def _dialogue_pairs(turns: Sequence[SessionTurn]) -> List[Tuple[Optional[SessionTurn], Optional[SessionTurn]]]:
    pairs: List[Tuple[Optional[SessionTurn], Optional[SessionTurn]]] = []
    pending: Optional[SessionTurn] = None
    for turn in turns:
        if turn.role == "user":
            if pending is not None:
                pairs.append((pending, None))
            pending = turn
        elif turn.role == "assistant":
            pairs.append((pending, turn))
            pending = None
    if pending is not None:
        pairs.append((pending, None))
    return pairs


def _candidate_from_pair(
    user: Optional[SessionTurn], assistant: Optional[SessionTurn]
) -> Optional[ExtractedMemory]:
    user_text = (user.text if user else "") or ""
    asst_text = (assistant.text if assistant else "") or ""
    combined = f"{user_text}\n{asst_text}".strip()
    if not combined or combined.startswith("/"):
        return None
    kind = _classify(combined)
    if not kind:
        return None
    title = _title_from(user_text or asst_text, kind)
    if len(title) < 8:
        return None
    body = combined.strip()
    if len(body) < 8:
        return None
    links = _collect_links(user_text, asst_text, user, assistant)
    return ExtractedMemory(
        kind=kind,
        title=title,
        body=body,
        links=links,
        tags=["extracted"],
    )


def _classify(text: str) -> Optional[str]:
    checks = {
        "error": _ERROR_CUES,
        "decision": _DECISION_CUES,
        "preference": _PREFERENCE_CUES,
        "fact": _FACT_CUES,
    }
    for kind in _KIND_PRIORITY:
        if checks[kind].search(text or ""):
            return kind
    return None


def _title_from(text: str, kind: str) -> str:
    blob = " ".join((text or "").split())
    blob = _BACKTICK.sub(lambda m: m.group(1).split(":")[-1] if ":" in m.group(1) else m.group(1), blob)
    blob = _LEAD_STRIP.sub("", blob).strip()
    match = _FIRST_SENTENCE.match(blob)
    snippet = match.group(1) if match else blob
    snippet = snippet.strip().rstrip(".")
    if snippet:
        snippet = snippet[0].upper() + snippet[1:]
    if len(snippet) > 80:
        snippet = snippet[:77].rstrip() + "..."
    return snippet or kind


def _collect_links(
    user_text: str,
    asst_text: str,
    user: Optional[SessionTurn],
    assistant: Optional[SessionTurn],
) -> List[str]:
    found: List[str] = []
    seen = set()
    texts = (user_text, asst_text)
    for blob in texts:
        for raw in _BACKTICK.findall(blob or ""):
            link = _normalize_cite(raw)
            if link and link not in seen:
                seen.add(link)
                found.append(link)
        for raw in _PATH_SYMBOL.findall(blob or ""):
            link = _normalize_cite(raw)
            if link and link not in seen:
                seen.add(link)
                found.append(link)
    for turn in (user, assistant):
        if turn is None:
            continue
        for cid in turn.chunk_ids or []:
            link = cite_from_chunk_id(cid)
            if link and link not in seen:
                seen.add(link)
                found.append(link)
    return found


def cite_from_chunk_id(chunk_id: str) -> str:
    raw = (chunk_id or "").replace("\\", "/")
    if not raw:
        return ""
    parts = raw.split(":")
    if parts and parts[0] in _SYMBOL_KINDS and len(parts) >= 3:
        path = parts[1]
        symbol_parts = parts[2:]
        if symbol_parts and _HEX_SUFFIX.match(symbol_parts[-1]):
            symbol_parts = symbol_parts[:-1]
        symbol = symbol_parts[0] if symbol_parts else ""
        return cite_path_symbol(path, symbol)
    cited = cite_from_entity_id(raw)
    if cited and cited != raw:
        return cited
    return _normalize_cite(raw)


def _normalize_cite(raw: str) -> str:
    text = (raw or "").replace("\\", "/").strip().strip("`")
    if not text or " " in text:
        return ""
    if "/" not in text and ":" not in text:
        return ""
    if ":" in text:
        path, symbol = text.split(":", 1)
        if "/" not in path or "." not in os.path.basename(path):
            return ""
        return cite_path_symbol(path, symbol)
    if "/" in text and "." in os.path.basename(text):
        return cite_path_symbol(text, "")
    return ""


def _dedupe_candidates(cands: Sequence[ExtractedMemory]) -> List[ExtractedMemory]:
    kept: List[ExtractedMemory] = []
    for cand in cands:
        merged = False
        for existing in kept:
            if existing.kind != cand.kind:
                continue
            if _overlap(f"{existing.title} {existing.body}", f"{cand.title} {cand.body}") >= 0.55:
                existing.links = list(dict.fromkeys([*existing.links, *cand.links]))
                merged = True
                break
        if not merged:
            kept.append(cand)
    return kept


def _overlap(left: str, right: str) -> float:
    a = set(_tokenize(left))
    b = set(_tokenize(right))
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _llm_ready(llm: Any, config: Optional[Config]) -> bool:
    if llm is None:
        return False
    provider = getattr(llm, "provider", None)
    if not provider and config is not None:
        provider = (config.llm or {}).get("provider")
    if provider in ("ollama", "custom"):
        return True
    return bool(getattr(llm, "api_key", None))


def _refine_with_llm(candidates: List[ExtractedMemory], llm: Any) -> List[ExtractedMemory]:
    payload = [
        {"kind": c.kind, "title": c.title, "body": c.body, "links": list(c.links)}
        for c in candidates
    ]
    raw = llm.query(
        "Refine typed project memories. Return JSON only: a list of "
        "{kind, title, body, links} objects. Kinds: decision|error|preference|fact. "
        "Do not invent entries. Keep path:symbol links.",
        json.dumps(payload, ensure_ascii=False),
        "Refine these extracted memories.",
    )
    parsed = _parse_refine_json(raw)
    if not parsed:
        raise MemoryExtractError("LLM refine returned no JSON list")
    refined: List[ExtractedMemory] = []
    for i, cand in enumerate(candidates):
        row = parsed[i] if i < len(parsed) and isinstance(parsed[i], dict) else {}
        kind = str(row.get("kind") or cand.kind).strip().lower()
        if kind not in {"decision", "error", "preference", "fact"}:
            kind = cand.kind
        title = str(row.get("title") or cand.title).strip() or cand.title
        body = str(row.get("body") or cand.body).strip() or cand.body
        links = row.get("links") if isinstance(row.get("links"), list) else cand.links
        refined.append(
            ExtractedMemory(
                kind=kind,
                title=title,
                body=body,
                links=[_normalize_cite(x) or str(x) for x in links if str(x).strip()],
                tags=list(cand.tags or ["extracted"]),
            )
        )
    return refined


def _parse_refine_json(raw: str) -> List[Dict[str, Any]]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
    return data if isinstance(data, list) else []


__all__ = [
    "ExtractReport",
    "ExtractedMemory",
    "MemoryExtractError",
    "cite_from_chunk_id",
    "extract_candidates",
    "extract_session",
    "format_extract_report",
    "latest_session_jsonl",
    "load_session_turns",
    "maybe_auto_extract",
    "maybe_refine_with_llm",
    "reconcile_candidate",
]
