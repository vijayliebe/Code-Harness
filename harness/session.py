"""Interactive session: slash commands, heuristic /compact, /cost, sage profile.

Conversation compact is not the CCR packer. Session JSONL is not typed memory
and is not written into the code index.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from .config import DEFAULT_CONFIG, Config


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
  /cost                    Session tokens + approx $ if a rate is set
  /profile default|sage    Switch pack/expand profile (does not retune RRF)
  /expand <id|path:symbol> Print a cached chunk (alias: /retrieve)
  /memory brief            Dump the typed memory brief
  /wiki <page>             Show a generated wiki page
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


@dataclass
class SessionCost:
    prompt_tokens: int = 0
    packed_tokens: int = 0
    full_tokens: int = 0
    completion_tokens: int = 0
    loop_attempts: int = 0
    approx_usd: Optional[float] = None


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
    memory_brief: bool = False
    llm: Optional[bool] = None
    clear_screen: bool = False


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
        self.turns: List[SessionTurn] = []
        self._prompt_tokens = 0
        self._packed_tokens = 0
        self._full_tokens = 0
        self._completion_tokens = 0
        self._loop_attempts = 0
        if self.session_dir:
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
        if not self.session_dir:
            raise ValueError("session_dir is not set")
        return os.path.join(self.session_dir, f"{self.session_id}.jsonl")

    def _tokens(self, text: str) -> int:
        return int(self.estimate_fn(text or ""))

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
        self.turns.append(turn)
        if turn.role == "assistant" or turn.packed_tokens or turn.completion_tokens:
            self._prompt_tokens += int(turn.packed_tokens or 0)
            self._packed_tokens += int(turn.packed_tokens or 0)
            self._full_tokens += int(turn.full_tokens or 0)
            self._completion_tokens += int(turn.completion_tokens or 0)
            self._loop_attempts += int(turn.loop_attempts or 0)
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
                }
            )

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
        )

    def format_cost(self) -> str:
        cost = self.cost()
        if cost.approx_usd is None:
            usd_line = "approx $: rate unknown"
        else:
            usd_line = f"approx $: ${cost.approx_usd:.4f}"
        return (
            "session cost\n"
            f"  prompt tokens (packed): {cost.prompt_tokens}\n"
            f"  prompt tokens (full):   {cost.full_tokens}\n"
            f"  completion tokens:      {cost.completion_tokens}\n"
            f"  loop attempts:          {cost.loop_attempts}\n"
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

    def compact(self, keep_recent: Optional[int] = None) -> CompactResult:
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

    def set_profile(self, name: str) -> str:
        key = (name or "").strip().lower()
        if key not in KNOWN_PROFILES:
            raise ValueError(
                f"unknown profile {name!r}; choose one of: {', '.join(KNOWN_PROFILES)}"
            )
        self.profile = key
        if self.session_dir:
            self._append_jsonl({"event": "profile", "profile": key})
        return key

    def _render_turn(self, turn: SessionTurn) -> str:
        label = turn.role.capitalize()
        ids = f"  pack={','.join(turn.chunk_ids)}" if turn.chunk_ids else ""
        return f"{label}: {turn.text}{ids}"

    def _render_stub(self, turn: SessionTurn) -> str:
        if turn.chunk_ids:
            return f"{turn.role} pack: {', '.join(turn.chunk_ids)}"
        snippet = _first_line(turn.text, 80)
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
    if kind == "cost":
        return CommandResult(kind="cost", message=session.format_cost())
    if kind == "exit":
        return CommandResult(kind="exit", message="bye", should_exit=True)
    if kind == "compact":
        result = session.compact()
        msg = (
            f"[*] compact: dropped {result.dropped_turns} turn(s), "
            f"kept {result.kept_turns}\n{result.summary}"
        )
        return CommandResult(kind="compact", message=msg)
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
        if not page:
            return CommandResult(kind="wiki", message="[!] Usage: /wiki <page>")
        return CommandResult(kind="wiki", message="", wiki_page=page)
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
