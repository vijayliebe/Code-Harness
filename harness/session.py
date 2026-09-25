"""Interactive session: slash commands, heuristic /compact, /search, /cost, sage profile.

Conversation compact is not the CCR packer. Tool-result clearing (opt-in)
is not compact: it only stubs re-fetchable retrieve dumps. Session JSONL is
not typed memory and is not written into the code index.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from .config import DEFAULT_CONFIG, Config
from .tool_clear import (
    ClearToolResult,
    clear_tool_results as apply_clear_tool_results,
    dump_payload,
    is_placeholder,
    is_refetchable_dump,
    should_clear,
    tool_result_clearing_enabled,
)
from .verify import (
    BUILDER_ROLE,
    CompletionDecision,
    CompletionGate,
    VerifyResult,
    force_done_enabled,
    looks_like_done_claim,
    verify_gate_enabled,
)


CITATION_INSTRUCTION = (
    "Cite evidence as `path:symbol` using chunk headers "
    "(example: `harness/context_builder.py:ContextBuilder.build_context`)."
)

KNOWN_PROFILES = ("default", "sage")

SLASH_ALIASES = {
    "quit": "exit",
    "q": "exit",
    "retrieve": "expand",
}

SAGE_PROFILE = {
    "pack_mode": "ccr_lite",
    "max_loops": 1,
    "expand_neighbors": 6,
    "beam_width": 8,
    "beam_depth": 3,
    "max_tokens_multiplier": 3,
    "expand_on": ["explain"],
}

HELP_TEXT = """Commands:
  /help                    Show this help
  /compact                 Summarize older turns; keep latest pack
  /search <query>          FTS over the event log (requires --event-session)
  /clear-tool-results      Replace aged retrieve dumps with re-fetch stubs
  /cost                    Session tokens + approx $ if a rate is set
  /profile default|sage    Switch pack/expand profile (does not retune RRF)
  /expand <id|path:symbol> Print a cached chunk (alias: /retrieve)
  /memory brief            Dump the typed memory brief
  /wiki                    Toggle chat-over-wiki (ask the living wiki)
  /wiki on|off             Enable or disable wiki mode
  /wiki <page>             Show a generated wiki page
  /verify                  Independent completion-criteria check (default-fail)
  /done                    Accept completion only if the verify gate is green
  /done --force            Override the verify gate (explicit)
  /llm on|off              Enable/disable LLM responses
  /context                 Show the last retrieved context
  /clear                   Clear the screen
  /exit                    Leave the session (alias: /quit)
  Any other text           Query the indexed repository
"""

_FIRST_SENTENCE = re.compile(r"(.+?[.!?])(?:\s|$)", re.DOTALL)


def estimate_tokens(text: str) -> int:
    """Same estimator as ContextBuilder: characters // 4."""
    return len(text or "") // 4


def known_profiles() -> tuple:
    return KNOWN_PROFILES


@dataclass(frozen=True)
class SlashCommand:
    name: str
    args: str
    raw: str

    @property
    def canonical(self) -> str:
        return SLASH_ALIASES.get(self.name, self.name)


@dataclass
class SessionTurn:
    role: str
    text: str
    chunk_ids: List[str] = field(default_factory=list)
    packed_tokens: int = 0
    full_tokens: int = 0
    completion_tokens: int = 0
    loop_attempts: int = 0
    pack_mode: str = "full"
    paths: List[str] = field(default_factory=list)
    tool_name: str = ""
    tool_args: str = ""
    tool_result: str = ""
    cleared: bool = False
    event_id: str = ""


@dataclass
class SessionCost:
    prompt_tokens: int = 0
    packed_tokens: int = 0
    full_tokens: int = 0
    completion_tokens: int = 0
    loop_attempts: int = 0
    approx_usd: Optional[float] = None
    tool_result_tokens_freed: int = 0
    tool_results_cleared: int = 0


@dataclass
class CompactResult:
    summary: str
    dropped_turns: int
    kept_turns: int


@dataclass
class CommandResult:
    kind: str
    message: str = ""
    should_exit: bool = False
    profile: Optional[str] = None
    expand_ref: Optional[str] = None
    wiki_page: Optional[str] = None
    wiki_mode: Optional[bool] = None
    memory_brief: bool = False
    llm: Optional[bool] = None
    clear_screen: bool = False
    done_accepted: Optional[bool] = None
    force_done: bool = False


def parse_slash(line: str) -> Optional[SlashCommand]:
    text = (line or "").strip()
    if not text.startswith("/"):
        return None
    parts = text.split(None, 1)
    name = parts[0][1:].lower()
    if not name:
        return None
    args = parts[1].strip() if len(parts) > 1 else ""
    return SlashCommand(name=name, args=args, raw=text)


def apply_profile(config: Config, name: str) -> Config:
    """Apply a named flag pack. Never retunes RRF weights."""
    key = (name or "").strip().lower()
    if key not in KNOWN_PROFILES:
        raise ValueError(
            f"unknown profile {name!r}; choose one of: {', '.join(KNOWN_PROFILES)}"
        )
    if key == "default":
        config.context["pack_mode"] = DEFAULT_CONFIG["context"]["pack_mode"]
        config.context["max_tokens_multiplier"] = DEFAULT_CONFIG["context"][
            "max_tokens_multiplier"
        ]
        config.retrieval["max_loops"] = DEFAULT_CONFIG["retrieval"]["max_loops"]
        config.retrieval["expand_neighbors"] = DEFAULT_CONFIG["retrieval"][
            "expand_neighbors"
        ]
        config.retrieval["beam_width"] = DEFAULT_CONFIG["retrieval"]["beam_width"]
        config.retrieval["beam_depth"] = DEFAULT_CONFIG["retrieval"]["beam_depth"]
        config.ccr["expand_on"] = list(DEFAULT_CONFIG["ccr"]["expand_on"])
        return config
    spec = SAGE_PROFILE
    config.context["pack_mode"] = spec["pack_mode"]
    config.context["max_tokens_multiplier"] = spec["max_tokens_multiplier"]
    config.retrieval["max_loops"] = spec["max_loops"]
    config.retrieval["expand_neighbors"] = spec["expand_neighbors"]
    config.retrieval["beam_width"] = spec["beam_width"]
    config.retrieval["beam_depth"] = spec["beam_depth"]
    cues = list(config.ccr.get("expand_on") or [])
    for cue in spec["expand_on"]:
        if cue not in cues:
            cues.append(cue)
    config.ccr["expand_on"] = cues
    return config


def query_matches_expand_on(query: str, cues: Sequence[str]) -> bool:
    text = (query or "").lower()
    return any((cue or "").lower() in text for cue in cues if cue)


def should_auto_expand(
    *,
    query: str,
    expand_on: Sequence[str],
    omitted_ids: Sequence[str],
    packed_ids: Sequence[str],
    max_loops: int,
) -> bool:
    if not omitted_ids:
        return False
    if query_matches_expand_on(query, expand_on):
        return True
    packed = len(packed_ids) or 1
    if (len(omitted_ids) / packed) > 0.5 and not int(max_loops or 0):
        return True
    return False


def _first_line(text: str, limit: int = 160) -> str:
    blob = " ".join((text or "").split())
    if not blob:
        return ""
    match = _FIRST_SENTENCE.match(blob)
    snippet = match.group(1) if match else blob
    if len(snippet) > limit:
        return snippet[: limit - 3].rstrip() + "..."
    return snippet


class Session:
    def __init__(
        self,
        *,
        repo: str = ".",
        profile: str = "default",
        session_dir: Optional[str] = None,
        session_id: Optional[str] = None,
        estimate_fn: Optional[Callable[[str], int]] = None,
        input_usd_per_1m: Optional[float] = None,
        output_usd_per_1m: Optional[float] = None,
        budget_tokens: int = 8192,
        keep_recent: int = 1,
        redact: Optional[bool] = None,
        audit_path: Optional[str] = None,
        config: Optional[Config] = None,
        clear_tool_results: Optional[bool] = None,
        clear_tool_keep: Optional[int] = None,
        clear_tool_token_trigger: Optional[int] = None,
        verify: Optional[bool] = None,
        verify_criteria: Optional[Sequence] = None,
        force_done: Optional[bool] = None,
        event_session: Optional[bool] = None,
    ):
        self.repo = repo
        self.profile = (profile or "default").strip().lower() or "default"
        self.session_dir = session_dir
        self.session_id = session_id or time.strftime("%Y%m%d-%H%M%S")
        self.estimate_fn = estimate_fn or estimate_tokens
        self.input_usd_per_1m = input_usd_per_1m
        self.output_usd_per_1m = output_usd_per_1m
        self.budget_tokens = int(budget_tokens)
        self.keep_recent = max(1, int(keep_recent))
        self._config = config
        self._redact = redact
        self._audit_path = audit_path
        session_cfg = getattr(config, "session", None) or {}
        if clear_tool_results is None:
            clear_tool_results = tool_result_clearing_enabled(config=config)
        self.clear_tool_results_enabled = bool(clear_tool_results)
        if clear_tool_keep is None:
            clear_tool_keep = session_cfg.get("clear_tool_keep") or session_cfg.get(
                "keep_recent"
            ) or keep_recent
        self.clear_tool_keep = max(1, int(clear_tool_keep or 1))
        if clear_tool_token_trigger is None:
            clear_tool_token_trigger = session_cfg.get("clear_tool_token_trigger") or 0
        self.clear_tool_token_trigger = max(0, int(clear_tool_token_trigger or 0))
        self.wiki_mode = bool((getattr(config, "chat", None) or {}).get("wiki_mode"))
        self.query_cache = None
        if verify is None:
            verify = verify_gate_enabled(config=config)
        self.verify_enabled = bool(verify)
        if force_done is None:
            force_done = force_done_enabled(config=config)
        specs = verify_criteria
        if specs is None:
            specs = session_cfg.get("criteria") or []
        cwd = getattr(config, "repo_path", None) if config is not None else None
        if not cwd:
            cwd = self.repo if os.path.isdir(self.repo or "") else "."
        self.gate = CompletionGate(
            enabled=self.verify_enabled,
            criteria=specs,
            cwd=cwd,
            force=bool(force_done),
        )
        if event_session is None:
            from .events import event_session_enabled

            event_session = event_session_enabled(config=config)
        self.event_session = bool(event_session)
        self._log = None
        self._fts = None
        self._prefix_freeze = None
        self.prefix_digest = ""
        self.turns: List[SessionTurn] = []
        self._prompt_tokens = 0
        self._packed_tokens = 0
        self._full_tokens = 0
        self._completion_tokens = 0
        self._loop_attempts = 0
        self._tool_result_tokens_freed = 0
        self._tool_results_cleared = 0
        if self.event_session:
            from .events import EventLog, make_event

            path = None
            if self.session_dir:
                os.makedirs(self.session_dir, exist_ok=True)
                path = os.path.join(self.session_dir, f"{self.session_id}.jsonl")
            self._log = EventLog(path)
            self._bind_fts(path)
            self._log.append(
                make_event(
                    "meta",
                    kind="session_start",
                    extra={
                        "repo": self.repo,
                        "profile": self.profile,
                        "session_id": self.session_id,
                    },
                )
            )
        elif self.session_dir:
            os.makedirs(self.session_dir, exist_ok=True)
            self._append_jsonl(
                {
                    "event": "session_start",
                    "id": self.session_id,
                    "repo": self.repo,
                    "profile": self.profile,
                }
            )

    def jsonl_path(self) -> str:
        if self._log is not None and self._log.path:
            return self._log.path
        if not self.session_dir:
            raise ValueError("session_dir is not set")
        return os.path.join(self.session_dir, f"{self.session_id}.jsonl")

    @property
    def events(self):
        if self._log is None:
            return []
        return list(self._log.events)

    def derive_messages(self):
        from .events import derive_messages

        if self._log is None:
            return []
        return self._log.derive_messages()

    def bind_prefix(self, freeze) -> None:
        """Pin a prefix-stable freeze to this session route."""
        from .events import make_event

        self._prefix_freeze = freeze
        self.prefix_digest = getattr(freeze, "digest", "") or ""
        if self._log is None:
            return
        system = getattr(freeze, "system", "") or ""
        if system:
            self._log.append(make_event("system", text=system, kind="system_prompt"))
        self._log.append(
            make_event(
                "meta",
                kind="prefix_freeze",
                extra={
                    "digest": self.prefix_digest,
                    "route": getattr(getattr(freeze, "route", None), "to_dict", lambda: {})(),
                },
            )
        )
        self._refresh_derived()

    @classmethod
    def resume(cls, path: str, **kwargs) -> "Session":
        """Reload an event JSONL (or migrate a legacy turn file) and re-derive."""
        from .events import EventLog

        log = EventLog.load(path)
        kwargs.setdefault("event_session", True)
        session_dir = os.path.dirname(os.path.abspath(path)) or None
        session_id = os.path.splitext(os.path.basename(path))[0]
        session = cls(
            session_dir=None,
            session_id=session_id,
            **kwargs,
        )
        session.session_dir = session_dir
        session._log = log
        session.event_session = True
        session._bind_fts(path)
        session._rebuild_cost_from_events()
        session._refresh_derived()
        return session

    def _refresh_derived(self) -> None:
        if self._log is None:
            return
        from .events import derive_turns

        self.turns = derive_turns(self._log.events)

    def _rebuild_cost_from_events(self) -> None:
        self._prompt_tokens = 0
        self._packed_tokens = 0
        self._full_tokens = 0
        self._completion_tokens = 0
        self._loop_attempts = 0
        if self._log is None:
            return
        for event in self._log.events:
            if event.type == "assistant" or event.packed_tokens or event.completion_tokens:
                self._prompt_tokens += int(event.packed_tokens or 0)
                self._packed_tokens += int(event.packed_tokens or 0)
                self._full_tokens += int(event.full_tokens or 0)
                self._completion_tokens += int(event.completion_tokens or 0)
                self._loop_attempts += int(event.loop_attempts or 0)
            if event.type == "clear":
                self._tool_result_tokens_freed += int(event.tokens_freed or 0)
                self._tool_results_cleared += len(event.cleared_ids or event.placeholders or {})

    def _tokens(self, text: str) -> int:
        return int(self.estimate_fn(text or ""))

    def _bind_fts(self, path: Optional[str] = None) -> None:
        from .session_fts import SessionEventIndex, default_fts_path

        jsonl = path or (self._log.path if self._log is not None else None)
        self._fts = SessionEventIndex(default_fts_path(jsonl) if jsonl else None)
        if self._log is not None:
            self._log.fts = self._fts
            if self._log.events:
                self._fts.sync(
                    self._log.events,
                    source_path=jsonl,
                    session_id=self.session_id,
                )

    def search_events(self, query: str, top_k: int = 10):
        """Ranked FTS hits over this session's event log."""
        from .session_fts import SessionSearchError

        if not self.event_session or self._log is None:
            raise SessionSearchError(
                "/search requires --event-session (typed event log). "
                "Legacy transcripts are not indexed."
            )
        if self._fts is None:
            self._bind_fts(self._log.path)
        self._fts.sync(
            self._log.events,
            source_path=self._log.path,
            session_id=self.session_id,
        )
        return self._fts.search(query, top_k=top_k)

    def record_turn(self, turn: SessionTurn) -> None:
        from .redact import redact_and_audit, redaction_enabled

        session_on = True
        if self._config is not None:
            session_on = (getattr(self._config, "redaction", None) or {}).get("session", True)
        if session_on and redaction_enabled(self._config, explicit=self._redact):
            result = redact_and_audit(
                turn.text or "",
                action="redact.session",
                config=self._config,
                enabled=True,
                audit_path=self._audit_path,
                session_id=self.session_id,
            )
            turn.text = result.text
            if turn.tool_result:
                dumped = redact_and_audit(
                    turn.tool_result,
                    action="redact.session",
                    config=self._config,
                    enabled=True,
                    audit_path=self._audit_path,
                    session_id=self.session_id,
                )
                turn.tool_result = dumped.text
            if turn.tool_args:
                args = redact_and_audit(
                    turn.tool_args,
                    action="redact.session",
                    config=self._config,
                    enabled=True,
                    audit_path=self._audit_path,
                    session_id=self.session_id,
                )
                turn.tool_args = args.text
        if turn.role == "assistant" or turn.packed_tokens or turn.completion_tokens:
            self._prompt_tokens += int(turn.packed_tokens or 0)
            self._packed_tokens += int(turn.packed_tokens or 0)
            self._full_tokens += int(turn.full_tokens or 0)
            self._completion_tokens += int(turn.completion_tokens or 0)
            self._loop_attempts += int(turn.loop_attempts or 0)
        if self.event_session and self._log is not None:
            self._append_turn_events(turn)
            self._refresh_derived()
            return
        self.turns.append(turn)
        if self.session_dir:
            self._append_jsonl(
                {
                    "event": "turn",
                    "role": turn.role,
                    "text": turn.text,
                    "chunk_ids": list(turn.chunk_ids or []),
                    "packed_tokens": turn.packed_tokens,
                    "full_tokens": turn.full_tokens,
                    "completion_tokens": turn.completion_tokens,
                    "loop_attempts": turn.loop_attempts,
                    "pack_mode": turn.pack_mode,
                    "paths": list(turn.paths or []),
                    "tool_name": turn.tool_name,
                    "tool_args": turn.tool_args,
                    "tool_result": turn.tool_result,
                    "cleared": bool(turn.cleared),
                }
            )

    def _append_turn_events(self, turn: SessionTurn) -> None:
        from .events import make_event

        if turn.role == "user":
            ev = self._log.append(make_event("user", text=turn.text or ""))
            turn.event_id = ev.id
            return
        if turn.role == "compact":
            ev = self._log.append(
                make_event(
                    "compact",
                    text=turn.text or "",
                    chunk_ids=list(turn.chunk_ids or []),
                    kind="compact",
                )
            )
            turn.event_id = ev.id
            return
        if turn.role == "assistant" or turn.tool_name or turn.tool_result:
            if turn.tool_name or turn.tool_result or turn.chunk_ids:
                use = self._log.append(
                    make_event(
                        "tool_use",
                        tool_name=turn.tool_name or "retrieve",
                        tool_args=turn.tool_args or "",
                        chunk_ids=list(turn.chunk_ids or []),
                        paths=list(turn.paths or []),
                    )
                )
                result = self._log.append(
                    make_event(
                        "tool_result",
                        text=turn.tool_result or "",
                        tool_use_id=use.id,
                        chunk_ids=list(turn.chunk_ids or []),
                        paths=list(turn.paths or []),
                        tool_name=turn.tool_name or "retrieve",
                    )
                )
                turn.event_id = result.id
            ev = self._log.append(
                make_event(
                    "assistant",
                    text=turn.text or "",
                    chunk_ids=list(turn.chunk_ids or []),
                    packed_tokens=turn.packed_tokens,
                    full_tokens=turn.full_tokens,
                    completion_tokens=turn.completion_tokens,
                    loop_attempts=turn.loop_attempts,
                    pack_mode=turn.pack_mode,
                )
            )
            if not turn.event_id:
                turn.event_id = ev.id
            return
        self._log.append(make_event("meta", kind=turn.role, text=turn.text or ""))

    def turn_count(self) -> int:
        return len(self.turns)

    def latest_pack_ids(self) -> List[str]:
        for turn in reversed(self.turns):
            if turn.chunk_ids:
                return list(turn.chunk_ids)
        return []

    def all_kept_chunk_ids(self) -> List[str]:
        ids: List[str] = []
        seen = set()
        for turn in self.turns:
            for cid in turn.chunk_ids or []:
                if cid not in seen:
                    seen.add(cid)
                    ids.append(cid)
        return ids

    def cost(self) -> SessionCost:
        usd = None
        if self.input_usd_per_1m is not None or self.output_usd_per_1m is not None:
            usd = (
                (self._prompt_tokens / 1_000_000.0) * float(self.input_usd_per_1m or 0.0)
                + (self._completion_tokens / 1_000_000.0)
                * float(self.output_usd_per_1m or 0.0)
            )
        return SessionCost(
            prompt_tokens=self._prompt_tokens,
            packed_tokens=self._packed_tokens,
            full_tokens=self._full_tokens,
            completion_tokens=self._completion_tokens,
            loop_attempts=self._loop_attempts,
            approx_usd=usd,
            tool_result_tokens_freed=self._tool_result_tokens_freed,
            tool_results_cleared=self._tool_results_cleared,
        )

    def format_cost(self) -> str:
        cost = self.cost()
        if cost.approx_usd is None:
            usd_line = "approx $: rate unknown"
        else:
            usd_line = f"approx $: ${cost.approx_usd:.4f}"
        cache_line = ""
        cache = getattr(self, "query_cache", None)
        if cache is not None:
            cache_line = (
                f"  query cache:            {int(getattr(cache, 'hits', 0) or 0)} hit / "
                f"{int(getattr(cache, 'misses', 0) or 0)} miss\n"
            )
        clear_line = ""
        if cost.tool_result_tokens_freed or cost.tool_results_cleared:
            clear_line = (
                f"  tool-result tokens freed: {cost.tool_result_tokens_freed} "
                f"({cost.tool_results_cleared} dump(s))\n"
            )
        return (
            "session cost\n"
            f"  prompt tokens (packed): {cost.prompt_tokens}\n"
            f"  prompt tokens (full):   {cost.full_tokens}\n"
            f"  completion tokens:      {cost.completion_tokens}\n"
            f"  loop attempts:          {cost.loop_attempts}\n"
            f"{cache_line}"
            f"{clear_line}"
            f"  {usd_line}"
        )

    def history_for_prompt(self) -> str:
        if not self.turns:
            return ""
        last_user_i = last_pack_i = None
        for i in range(len(self.turns) - 1, -1, -1):
            turn = self.turns[i]
            if last_user_i is None and turn.role == "user":
                last_user_i = i
            if last_pack_i is None and turn.chunk_ids:
                last_pack_i = i
            if last_user_i is not None and last_pack_i is not None:
                break

        must: List[str] = []
        if last_user_i is not None:
            must.append(f"User: {self.turns[last_user_i].text}")
        latest_ids = self.latest_pack_ids()
        if latest_ids:
            must.append("Latest pack: " + ", ".join(latest_ids))
        must_text = "\n".join(must)
        used = self._tokens(must_text)
        remain = max(0, self.budget_tokens - used)

        older_idx = [
            i
            for i in range(len(self.turns))
            if i not in {last_user_i, last_pack_i}
        ]
        kept_older: List[str] = []
        for i in reversed(older_idx):
            block = self._render_turn(self.turns[i])
            cost = self._tokens(block)
            if cost <= remain:
                kept_older.append(block)
                remain -= cost
            else:
                stub = self._render_stub(self.turns[i])
                stub_cost = self._tokens(stub)
                if stub and stub_cost <= remain:
                    kept_older.append(stub)
                    remain -= stub_cost
                break
        kept_older.reverse()
        return "\n".join([*kept_older, must_text]).strip()

    def history_tokens(self) -> int:
        return self._tokens(self.history_for_prompt())

    def clear_tool_results(
        self,
        keep_n: Optional[int] = None,
        token_trigger: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> ClearToolResult:
        """Replace aged retrieve/tool dumps. Slash can pass ``enabled=True``."""
        on = self.clear_tool_results_enabled if enabled is None else bool(enabled)
        if self.event_session and self._log is not None:
            return self._clear_tool_results_event(
                keep_n=keep_n, token_trigger=token_trigger, enabled=on
            )
        result = apply_clear_tool_results(
            self.turns,
            keep_n=keep_n if keep_n is not None else self.clear_tool_keep,
            token_trigger=(
                token_trigger
                if token_trigger is not None
                else self.clear_tool_token_trigger
            ),
            estimate_fn=self.estimate_fn,
            enabled=on,
        )
        if result.fired:
            self._tool_result_tokens_freed += int(result.tokens_freed or 0)
            self._tool_results_cleared += int(result.cleared or 0)
            if self.session_dir:
                self._append_jsonl(
                    {
                        "event": "clear_tool_results",
                        "cleared": result.cleared,
                        "kept": result.kept,
                        "tokens_freed": result.tokens_freed,
                    }
                )
        return result

    def _clear_tool_results_event(
        self,
        keep_n: Optional[int] = None,
        token_trigger: Optional[int] = None,
        enabled: bool = True,
    ) -> ClearToolResult:
        from .events import make_event
        from .tool_clear import placeholder_for, select_indexes_to_clear

        if not enabled:
            return ClearToolResult(
                kept=sum(1 for t in self.turns if is_refetchable_dump(t))
            )
        keep = keep_n if keep_n is not None else self.clear_tool_keep
        trigger = (
            token_trigger
            if token_trigger is not None
            else self.clear_tool_token_trigger
        )
        if not should_clear(
            self.turns,
            keep_n=keep,
            token_trigger=trigger,
            estimate_fn=self.estimate_fn,
        ):
            return ClearToolResult(
                kept=sum(1 for t in self.turns if is_refetchable_dump(t))
            )
        indexes = select_indexes_to_clear(
            self.turns,
            keep_n=keep,
            token_trigger=trigger,
            estimate_fn=self.estimate_fn,
        )
        placeholders: Dict[str, str] = {}
        tokens_freed = 0
        for idx in indexes:
            turn = self.turns[idx]
            eid = turn.event_id
            if not eid:
                continue
            stub = placeholder_for(turn)
            placeholders[eid] = stub
            before = self._tokens(dump_payload(turn))
            tokens_freed += max(0, before - self._tokens(stub))
        result = ClearToolResult(
            cleared=len(placeholders),
            kept=sum(1 for t in self.turns if is_refetchable_dump(t)) - len(placeholders),
            tokens_freed=tokens_freed,
            placeholders=list(placeholders.values()),
            fired=bool(placeholders),
        )
        if result.fired:
            self._log.append(
                make_event(
                    "clear",
                    cleared_ids=list(placeholders),
                    placeholders=placeholders,
                    tokens_freed=tokens_freed,
                    kept=result.kept,
                )
            )
            self._tool_result_tokens_freed += tokens_freed
            self._tool_results_cleared += result.cleared
            self._refresh_derived()
        return result

    def maybe_clear_tool_results(self) -> ClearToolResult:
        """Auto-clear before a model call when the opt-in policy fires."""
        if not self.clear_tool_results_enabled:
            return ClearToolResult(kept=sum(1 for t in self.turns if is_refetchable_dump(t)))
        if not should_clear(
            self.turns,
            keep_n=self.clear_tool_keep,
            token_trigger=self.clear_tool_token_trigger,
            estimate_fn=self.estimate_fn,
        ):
            return ClearToolResult(
                kept=sum(1 for t in self.turns if is_refetchable_dump(t))
            )
        return self.clear_tool_results(enabled=True)

    def history_for_model(self) -> str:
        """History framed for the next LLM call; clears aged dumps when enabled."""
        self.maybe_clear_tool_results()
        return self.history_for_prompt()

    def compact(self, keep_recent: Optional[int] = None) -> CompactResult:
        if self.clear_tool_results_enabled:
            self.clear_tool_results(enabled=True)
        if self.event_session and self._log is not None:
            return self._compact_event(keep_recent=keep_recent)
        keep_n = max(1, int(keep_recent or self.keep_recent))
        pairs = _dialogue_pairs(self.turns)
        if len(pairs) <= keep_n and not any(t.role == "compact" for t in self.turns):
            # Still collapse stray non-recent assistant bodies if we have extra turns.
            if self.turn_count() <= keep_n * 2:
                summary = _extractive_summary(self.turns)
                return CompactResult(
                    summary=summary,
                    dropped_turns=0,
                    kept_turns=self.turn_count(),
                )

        recent = pairs[-keep_n:]
        older = pairs[:-keep_n]
        older_turns: List[SessionTurn] = []
        for user, assistant in older:
            if user is not None:
                older_turns.append(user)
            if assistant is not None:
                older_turns.append(assistant)

        latest_ids = []
        if recent:
            _, last_asst = recent[-1]
            if last_asst is not None:
                latest_ids = list(last_asst.chunk_ids or [])
        if not latest_ids:
            latest_ids = self.latest_pack_ids()

        summary = _extractive_summary(older_turns)
        if not summary:
            summary = "Prior dialogue compacted."
        older_ids: List[str] = []
        seen = set()
        for turn in older_turns:
            for cid in turn.chunk_ids or []:
                if cid not in seen:
                    seen.add(cid)
                    older_ids.append(cid)
        for cid in latest_ids:
            if cid not in seen:
                seen.add(cid)
                older_ids.append(cid)

        compact_turn = SessionTurn(
            role="compact",
            text=summary,
            chunk_ids=older_ids,
        )
        new_turns = [compact_turn]
        for user, assistant in recent:
            if user is not None:
                new_turns.append(user)
            if assistant is not None:
                new_turns.append(assistant)

        dropped = max(0, len(self.turns) - len(new_turns))
        self.turns = new_turns
        if self.session_dir:
            self._append_jsonl(
                {
                    "event": "compact",
                    "dropped": dropped,
                    "kept": len(new_turns),
                    "summary_tokens": self._tokens(summary),
                    "latest_pack": latest_ids,
                }
            )
        return CompactResult(
            summary=summary,
            dropped_turns=dropped,
            kept_turns=len(new_turns),
        )

    def _compact_event(self, keep_recent: Optional[int] = None) -> CompactResult:
        from .events import SURFACE_TYPES, make_event

        keep_n = max(1, int(keep_recent or self.keep_recent))
        pairs = _dialogue_pairs(self.turns)
        if len(pairs) <= keep_n and not any(t.role == "compact" for t in self.turns):
            if self.turn_count() <= keep_n * 2:
                summary = _extractive_summary(self.turns)
                return CompactResult(
                    summary=summary,
                    dropped_turns=0,
                    kept_turns=self.turn_count(),
                )
        recent = pairs[-keep_n:]
        older = pairs[:-keep_n]
        older_turns: List[SessionTurn] = []
        for user, assistant in older:
            if user is not None:
                older_turns.append(user)
            if assistant is not None:
                older_turns.append(assistant)
        latest_ids = []
        if recent:
            _, last_asst = recent[-1]
            if last_asst is not None:
                latest_ids = list(last_asst.chunk_ids or [])
        if not latest_ids:
            latest_ids = self.latest_pack_ids()
        summary = _extractive_summary(older_turns) or "Prior dialogue compacted."
        first_kept = None
        for user, assistant in recent:
            for turn in (user, assistant):
                if turn is not None and turn.event_id:
                    first_kept = turn.event_id
                    break
            if first_kept:
                break
        from .events import _id_seq

        first_seq = _id_seq(first_kept) if first_kept else 10**9
        replace_ids = [
            ev.id
            for ev in self._log.events
            if ev.type in SURFACE_TYPES and ev.id and _id_seq(ev.id) < first_seq
        ]
        before = self.turn_count()
        self._log.append(
            make_event(
                "compact",
                text=summary,
                replace_ids=replace_ids,
                chunk_ids=latest_ids,
                kind="compact",
            )
        )
        self._refresh_derived()
        return CompactResult(
            summary=summary,
            dropped_turns=max(0, before - self.turn_count()),
            kept_turns=self.turn_count(),
        )

    def run_verify(self, evidence: Optional[Dict] = None, llm=None) -> VerifyResult:
        result = self.gate.run_verify(evidence=evidence, llm=llm)
        if self.event_session and self._log is not None:
            from .events import make_event

            self._log.append(make_event("verify", extra=result.to_dict()))
        elif self.session_dir:
            self._append_jsonl({"event": "verify", **result.to_dict()})
        return result

    def claim_done(self, text: str = "", *, force: bool = False) -> CompletionDecision:
        decision = self.gate.claim_done(text, role=BUILDER_ROLE, force=force)
        payload = {
            "accepted": decision.accepted,
            "reason": decision.reason,
            "forced": decision.forced,
        }
        if self.event_session and self._log is not None:
            from .events import make_event

            self._log.append(make_event("meta", kind="done", extra=payload))
        elif self.session_dir:
            self._append_jsonl({"event": "done", **payload})
        return decision

    def refuse_done_claim(self, text: str) -> Optional[str]:
        """End-of-turn / user-claim gate. None when the gate is off or not a claim."""
        if not self.verify_enabled:
            return None
        if not looks_like_done_claim(text):
            return None
        decision = self.claim_done(text, force=False)
        if decision.accepted:
            return None
        return decision.message or "verify gate: not done"

    def format_verify(self) -> str:
        if not self.verify_enabled:
            return (
                "verify gate is off (default). "
                "Pass --verify / CODEHARNESS_SESSION_VERIFY=1 / session.verify"
            )
        if self.gate.last_result is not None:
            return self.gate.last_result.message or self.gate.format_checklist()
        return self.gate.format_checklist()

    def set_profile(self, name: str) -> str:
        key = (name or "").strip().lower()
        if key not in KNOWN_PROFILES:
            raise ValueError(
                f"unknown profile {name!r}; choose one of: {', '.join(KNOWN_PROFILES)}"
            )
        self.profile = key
        if self.event_session and self._log is not None:
            from .events import make_event

            self._log.append(make_event("meta", kind="profile", extra={"profile": key}))
        elif self.session_dir:
            self._append_jsonl({"event": "profile", "profile": key})
        return key

    def _render_turn(self, turn: SessionTurn) -> str:
        label = turn.role.capitalize()
        ids = f"  pack={','.join(turn.chunk_ids)}" if turn.chunk_ids else ""
        body = turn.text or ""
        dump = turn.tool_result or ""
        include_dump = bool(dump) and (
            self.clear_tool_results_enabled or turn.cleared or is_placeholder(dump)
        )
        if include_dump and dump not in body:
            body = f"{body}\n{dump}".strip() if body else dump
        return f"{label}: {body}{ids}"

    def _render_stub(self, turn: SessionTurn) -> str:
        if turn.chunk_ids:
            return f"{turn.role} pack: {', '.join(turn.chunk_ids)}"
        snippet = _first_line(turn.text or dump_payload(turn), 80)
        return f"{turn.role}: {snippet}" if snippet else ""

    def _append_jsonl(self, event: Dict) -> None:
        path = self.jsonl_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _dialogue_pairs(turns: Sequence[SessionTurn]) -> List[tuple]:
    pairs: List[tuple] = []
    pending_user: Optional[SessionTurn] = None
    for turn in turns:
        if turn.role == "user":
            if pending_user is not None:
                pairs.append((pending_user, None))
            pending_user = turn
        elif turn.role == "assistant":
            pairs.append((pending_user, turn))
            pending_user = None
        elif turn.role == "compact":
            # Compact memory is not a dialogue pair; fold into the next keep window.
            continue
    if pending_user is not None:
        pairs.append((pending_user, None))
    return pairs


def _extractive_summary(turns: Sequence[SessionTurn]) -> str:
    lines: List[str] = []
    for turn in turns:
        if turn.role == "user":
            snippet = _first_line(turn.text)
            if snippet:
                lines.append(f"- user: {snippet}")
        elif turn.role == "compact":
            if turn.text:
                lines.append(turn.text)
        elif turn.chunk_ids:
            lines.append(f"- pack: {', '.join(turn.chunk_ids[:4])}")
    return "Prior dialogue:\n" + "\n".join(lines) if lines else ""


def handle_slash(session: Session, command: SlashCommand) -> CommandResult:
    kind = command.canonical
    if kind == "help":
        return CommandResult(kind="help", message=HELP_TEXT.strip())
    if kind == "search":
        from .session_fts import SessionSearchError, format_hits

        if not session.event_session or session._log is None:
            return CommandResult(
                kind="search",
                message=(
                    "[!] /search requires --event-session (event log). "
                    "Legacy transcripts are not indexed."
                ),
            )
        if not command.args:
            return CommandResult(kind="search", message="[!] Usage: /search <query>")
        try:
            hits = session.search_events(command.args)
        except SessionSearchError as exc:
            return CommandResult(kind="search", message=f"[!] {exc}")
        return CommandResult(
            kind="search",
            message=format_hits(hits) or "[*] no matches",
        )
    if kind == "cost":
        return CommandResult(kind="cost", message=session.format_cost())
    if kind == "exit":
        return CommandResult(kind="exit", message="bye", should_exit=True)
    if kind == "compact":
        result = session.compact()
        extra = ""
        if session._tool_result_tokens_freed:
            extra = f" (tool-result tokens freed: {session._tool_result_tokens_freed})"
        msg = (
            f"[*] compact: dropped {result.dropped_turns} turn(s), "
            f"kept {result.kept_turns}{extra}\n{result.summary}"
        )
        return CommandResult(kind="compact", message=msg)
    if kind in ("clear-tool-results", "clear_tool_results"):
        result = session.clear_tool_results(enabled=True)
        if result.fired:
            msg = (
                f"[*] clear-tool-results: cleared {result.cleared} dump(s), "
                f"kept {result.kept}, tokens freed {result.tokens_freed}"
            )
        else:
            msg = "[*] clear-tool-results: nothing to clear (latest pack kept)"
        return CommandResult(kind="clear_tool_results", message=msg)
    if kind == "profile":
        name = (command.args or "").split(None, 1)[0] if command.args else ""
        if not name:
            return CommandResult(
                kind="profile",
                message=f"profile={session.profile} (default|sage)",
                profile=session.profile,
            )
        try:
            session.set_profile(name)
        except ValueError as exc:
            return CommandResult(kind="unknown", message=f"[!] {exc}")
        return CommandResult(
            kind="profile",
            message=f"[*] profile={session.profile}",
            profile=session.profile,
        )
    if kind == "expand":
        if not command.args:
            return CommandResult(
                kind="expand",
                message="[!] Usage: /expand <chunk_id|path:symbol>",
            )
        return CommandResult(kind="expand", message="", expand_ref=command.args)
    if kind == "memory":
        action = (command.args or "brief").split(None, 1)[0].lower()
        if action != "brief":
            return CommandResult(
                kind="unknown",
                message="[!] Usage: /memory brief",
            )
        return CommandResult(kind="memory", message="", memory_brief=True)
    if kind == "wiki":
        page = (command.args or "").strip()
        key = page.lower()
        if not page:
            session.wiki_mode = not session.wiki_mode
            return CommandResult(
                kind="wiki",
                message=f"[*] wiki mode={'on' if session.wiki_mode else 'off'}",
                wiki_mode=session.wiki_mode,
            )
        if key in ("on", "off"):
            session.wiki_mode = key == "on"
            return CommandResult(
                kind="wiki",
                message=f"[*] wiki mode={'on' if session.wiki_mode else 'off'}",
                wiki_mode=session.wiki_mode,
            )
        return CommandResult(kind="wiki", message="", wiki_page=page)
    if kind == "verify":
        result = session.run_verify()
        return CommandResult(kind="verify", message=session.format_verify() or result.message)
    if kind == "done":
        args = (command.args or "").strip().lower()
        force = args in ("--force", "--force-done", "force") or "--force" in args.split()
        decision = session.claim_done(command.raw, force=force)
        if decision.accepted:
            msg = f"[*] done: {decision.reason}"
        else:
            msg = f"[!] {decision.message}"
        return CommandResult(
            kind="done",
            message=msg,
            done_accepted=decision.accepted,
            force_done=bool(decision.forced),
        )
    if kind == "llm":
        flag = (command.args or "").split(None, 1)[0].lower() if command.args else ""
        if flag not in ("on", "off"):
            return CommandResult(kind="llm", message="[!] Usage: /llm on|off")
        return CommandResult(
            kind="llm",
            message=f"[*] LLM {'enabled' if flag == 'on' else 'disabled'}",
            llm=(flag == "on"),
        )
    if kind == "context":
        return CommandResult(kind="context", message="")
    if kind == "clear":
        return CommandResult(kind="clear", message="", clear_screen=True)
    return CommandResult(
        kind="unknown",
        message=f"[!] unknown command /{command.name}. Try /help",
    )


def resolve_profile_name(args=None, environ=None) -> str:
    env = environ if environ is not None else os.environ
    raw = None
    if args is not None:
        raw = getattr(args, "profile", None)
    if not raw:
        raw = (env.get("CODEHARNESS_PROFILE") or "").strip() or None
    if not raw:
        return "default"
    name = str(raw).strip().lower()
    if name not in KNOWN_PROFILES:
        raise ValueError(
            f"unknown profile {raw!r}; choose one of: {', '.join(KNOWN_PROFILES)}"
        )
    return name
