"""Default-fail independent completion verify gate.

Builder / agent paths cannot self-grade ``done``. Criteria start false;
only a separate read-only verifier (or an explicit force override) can
accept completion. Prefer runnable checks (file / command / contains /
coverage) over an LLM judge.

Default is **off** (``--verify`` / ``CODEHARNESS_SESSION_VERIFY=1`` /
``session.verify``). Citation ``retrieval.verify`` is a different hook.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


_FALSEY = frozenset({"0", "false", "off", "no"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

BUILDER_ROLE = "builder"
VERIFIER_ROLE = "verifier"
VERIFIER_WRITE_TOOLS: tuple = ()
VERIFIER_READ_TOOLS = ("read", "stat", "run_check")

RUNNABLE_KINDS = frozenset({"file", "command", "contains", "coverage", "assertion"})
KNOWN_KINDS = RUNNABLE_KINDS | {"llm"}

VERIFIER_SYSTEM = (
    "You are an independent read-only verifier. You have no write tools. "
    "You cannot mark work done by asserting it. Criteria start false; "
    "the default is FAIL unless evidence proves a criterion. "
    "Evaluate each criterion against evidence only. "
    "Return JSON: {\"criteria\": [{\"id\": \"...\", \"met\": false, \"evidence\": \"...\"}]}"
)

_DONE_SHORT = re.compile(
    r"^\s*(?:i(?:'m| am)\s+)?(?:all\s+)?(?:done|completed|finished)(?:\s*[.!]*)?\s*$",
    re.IGNORECASE,
)
_DONE_PHRASES = (
    "the task is complete",
    "work is complete",
    "implementation is complete",
    "all criteria are met",
    "i've completed",
    "i have completed",
    "marking this done",
    "this is done",
    "task is complete",
    "task is done",
)


class VerifyRoleError(PermissionError):
    """Raised when the builder path tries to grade or run the gate."""


@dataclass
class Criterion:
    id: str
    description: str = ""
    kind: str = "file"
    check: str = ""
    expect: Any = None
    met: bool = False
    evidence: str = ""

    def __post_init__(self) -> None:
        self.id = str(self.id)
        self.kind = (self.kind or "file").strip().lower() or "file"
        self.description = self.description or self.id
        self.met = False
        self.evidence = ""


@dataclass
class VerifyResult:
    passed: bool
    ran: bool = True
    skipped: bool = False
    forced: bool = False
    role: str = VERIFIER_ROLE
    message: str = ""
    criteria: List[Criterion] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": bool(self.passed),
            "ran": bool(self.ran),
            "skipped": bool(self.skipped),
            "forced": bool(self.forced),
            "role": self.role,
            "message": self.message,
            "reason": self.reason,
            "criteria": [
                {
                    "id": c.id,
                    "description": c.description,
                    "kind": c.kind,
                    "check": c.check,
                    "met": bool(c.met),
                    "evidence": c.evidence,
                }
                for c in self.criteria
            ],
        }


@dataclass
class CompletionDecision:
    accepted: bool
    reason: str
    forced: bool = False
    verified: bool = False
    message: str = ""


def verifier_has_write_tools() -> bool:
    return bool(VERIFIER_WRITE_TOOLS)


def looks_like_done_claim(text: str) -> bool:
    blob = (text or "").strip()
    if not blob:
        return False
    if _DONE_SHORT.match(blob):
        return True
    lower = blob.lower()
    if lower.startswith("/done"):
        return True
    return any(phrase in lower for phrase in _DONE_PHRASES)


def verify_gate_enabled(
    args=None,
    environ=None,
    config=None,
) -> bool:
    """Session completion gate. Default off. Env ``0`` / ``--no-verify`` win."""
    env = environ if environ is not None else os.environ
    raw = str(
        env.get("CODEHARNESS_SESSION_VERIFY")
        or env.get("CODEHARNESS_VERIFY")
        or ""
    ).strip().lower()
    if args is not None and getattr(args, "no_verify", False):
        return False
    if raw in _FALSEY:
        return False
    if args is not None and (
        getattr(args, "verify", False) or getattr(args, "session_verify", False)
    ):
        return True
    if raw in _TRUTHY:
        return True
    if config is not None:
        session = getattr(config, "session", None) or {}
        if "verify" in session:
            return bool(session.get("verify"))
    return False


def force_done_enabled(args=None, environ=None, config=None) -> bool:
    env = environ if environ is not None else os.environ
    raw = str(env.get("CODEHARNESS_FORCE_DONE") or "").strip().lower()
    if args is not None and getattr(args, "force_done", False):
        return True
    if raw in _TRUTHY:
        return True
    if config is not None:
        session = getattr(config, "session", None) or {}
        return bool(session.get("force_done"))
    return False


def apply_verify_config(config, args=None, environ=None):
    """Stamp resolved knobs onto ``config.session`` (default remains off)."""
    session = getattr(config, "session", None)
    if session is None:
        config.session = {}
        session = config.session
    session["verify"] = verify_gate_enabled(args, environ=environ, config=config)
    session["force_done"] = force_done_enabled(args, environ=environ, config=config)
    return config


def parse_criterion(raw: Any) -> Criterion:
    if isinstance(raw, Criterion):
        return Criterion(
            id=raw.id,
            description=raw.description,
            kind=raw.kind,
            check=raw.check,
            expect=raw.expect,
            met=False,
        )
    if not isinstance(raw, dict):
        raise ValueError(f"criterion must be a mapping, got {type(raw).__name__}")
    cid = raw.get("id") or raw.get("name")
    if not cid:
        raise ValueError("criterion requires 'id'")
    return Criterion(
        id=str(cid),
        description=str(raw.get("description") or cid),
        kind=str(raw.get("kind") or raw.get("type") or "file"),
        check=str(raw.get("check") or raw.get("path") or raw.get("command") or ""),
        expect=raw.get("expect", raw.get("threshold")),
        met=False,
    )


def load_criteria(raw: Optional[Sequence[Any]]) -> List[Criterion]:
    return [parse_criterion(item) for item in (raw or [])]


class CompletionGate:
    def __init__(
        self,
        *,
        enabled: bool = False,
        criteria: Optional[Sequence[Any]] = None,
        cwd: Optional[str] = None,
        force: bool = False,
        command_timeout: float = 30.0,
    ):
        self.enabled = bool(enabled)
        self.cwd = cwd or os.getcwd()
        self.command_timeout = float(command_timeout)
        self.forced = bool(force)
        self.verify_ran = False
        self.last_result: Optional[VerifyResult] = None
        self.criteria: List[Criterion] = load_criteria(criteria)

    def add_criterion(self, spec: Any, *, role: str = BUILDER_ROLE) -> Criterion:
        """Builder may add criteria. They always start false."""
        crit = parse_criterion(spec)
        crit.met = False
        crit.evidence = ""
        self.criteria.append(crit)
        return crit

    def set_met(self, criterion_id: str, met: bool, *, role: str) -> Criterion:
        if role != VERIFIER_ROLE:
            raise VerifyRoleError("builder cannot mark criteria true")
        crit = self._find(criterion_id)
        crit.met = bool(met)
        return crit

    def is_complete(self) -> bool:
        if not self.enabled:
            return True
        if self.forced:
            return True
        if not self.verify_ran:
            return False
        if not self.criteria:
            return False
        return all(c.met for c in self.criteria)

    def claim_done(
        self,
        text: str = "",
        *,
        role: str = BUILDER_ROLE,
        force: bool = False,
    ) -> CompletionDecision:
        if not self.enabled:
            return CompletionDecision(
                accepted=True,
                reason="verify_off",
                message="verify gate is off",
            )
        if force:
            self.forced = True
            return CompletionDecision(
                accepted=True,
                reason="force_done",
                forced=True,
                message="completion forced (--force-done / /done --force)",
            )
        if self.is_complete():
            return CompletionDecision(
                accepted=True,
                reason="verified",
                verified=True,
                message="verify gate green",
            )
        return CompletionDecision(
            accepted=False,
            reason="verify_required",
            message=(
                "verify gate: not done (criteria still fail). "
                "Run /verify or override with /done --force"
            ),
        )

    def run_verify(
        self,
        *,
        role: str = VERIFIER_ROLE,
        evidence: Optional[Dict[str, Any]] = None,
        llm: Optional[Callable[[str, str, str], str]] = None,
        decider=None,
    ) -> VerifyResult:
        if role != VERIFIER_ROLE:
            raise VerifyRoleError("only the independent verifier role may run the gate")
        if not self.enabled:
            result = VerifyResult(
                passed=True,
                ran=False,
                skipped=True,
                reason="verify_off",
                message="verify gate is off (default)",
                criteria=list(self.criteria),
            )
            self.last_result = result
            return result

        evidence = dict(evidence or {})
        for crit in self.criteria:
            met, note = self._evaluate(crit, evidence=evidence, llm=llm, decider=decider)
            crit.met = bool(met)
            crit.evidence = note
        self.verify_ran = True
        passed = bool(self.criteria) and all(c.met for c in self.criteria)
        result = VerifyResult(
            passed=passed,
            ran=True,
            role=VERIFIER_ROLE,
            reason="verified" if passed else "criteria_failed",
            message=self.format_checklist(),
            criteria=list(self.criteria),
        )
        self.last_result = result
        return result

    def format_checklist(self) -> str:
        if not self.criteria:
            return "verify: no completion criteria (default-fail until a rubric is set)"
        lines = ["verify checklist (independent, default-fail):"]
        for crit in self.criteria:
            mark = "PASS" if crit.met else "FAIL"
            detail = f" — {crit.evidence}" if crit.evidence else ""
            lines.append(f"  [{mark}] {crit.id} ({crit.kind}){detail}")
        return "\n".join(lines)

    def _find(self, criterion_id: str) -> Criterion:
        for crit in self.criteria:
            if crit.id == criterion_id:
                return crit
        raise KeyError(f"unknown criterion {criterion_id!r}")

    def _evaluate(
        self,
        crit: Criterion,
        *,
        evidence: Dict[str, Any],
        llm: Optional[Callable[[str, str, str], str]],
        decider=None,
    ) -> tuple:
        kind = crit.kind if crit.kind in KNOWN_KINDS else "file"
        if kind == "file":
            return self._check_file(crit.check)
        if kind == "contains":
            return self._check_contains(crit.check, crit.expect)
        if kind == "command":
            return self._check_command(crit.check)
        if kind == "coverage":
            return self._check_coverage(crit.expect, evidence)
        if kind == "assertion":
            return self._check_assertion(crit, evidence)
        return self._check_llm(crit, evidence, llm, decider=decider)

    def _resolve(self, rel: str) -> str:
        path = (rel or "").strip()
        if not path:
            return ""
        if os.path.isabs(path):
            return path
        return os.path.join(self.cwd, path)

    def _check_file(self, rel: str) -> tuple:
        path = self._resolve(rel)
        if not path:
            return False, "empty file path"
        if os.path.isfile(path):
            return True, f"exists: {rel}"
        return False, f"missing file: {rel}"

    def _check_contains(self, rel: str, expect: Any) -> tuple:
        path = self._resolve(rel)
        needle = "" if expect is None else str(expect)
        if not path or not os.path.isfile(path):
            return False, f"missing file: {rel}"
        try:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        except OSError as exc:
            return False, str(exc)
        if needle and needle in body:
            return True, f"found {needle!r} in {rel}"
        return False, f"{needle!r} not in {rel}"

    def _check_command(self, check: str) -> tuple:
        raw = (check or "").strip()
        if not raw:
            return False, "empty command"
        try:
            args = shlex.split(raw)
        except ValueError as exc:
            return False, str(exc)
        if not args:
            return False, "empty command"
        try:
            proc = subprocess.run(
                args,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-400:]
        note = f"exit={proc.returncode}"
        if tail:
            note = f"{note} {tail}"
        return proc.returncode == 0, note

    def _check_coverage(self, expect: Any, evidence: Dict[str, Any]) -> tuple:
        score = evidence.get("coverage")
        if score is None:
            return False, "no coverage score in evidence"
        try:
            value = float(score)
            threshold = 0.5 if expect is None else float(expect)
        except (TypeError, ValueError):
            return False, f"bad coverage values score={score!r} expect={expect!r}"
        if value >= threshold:
            return True, f"coverage {value:.3f} >= {threshold:.3f}"
        return False, f"coverage {value:.3f} < {threshold:.3f}"

    def _check_assertion(self, crit: Criterion, evidence: Dict[str, Any]) -> tuple:
        actual = evidence.get(crit.check) if crit.check else evidence.get(crit.id)
        if crit.expect is None:
            ok = bool(actual)
        else:
            ok = actual == crit.expect
        return ok, f"assertion {crit.id} actual={actual!r} expect={crit.expect!r}"

    def _check_llm(
        self,
        crit: Criterion,
        evidence: Dict[str, Any],
        llm: Optional[Callable[[str, str, str], str]],
        decider=None,
    ) -> tuple:
        if decider is not None:
            from .decision import decide_verify_criterion

            decided = decide_verify_criterion(decider, crit, evidence)
            if decided is not None:
                return decided
        if llm is None:
            return False, "no independent llm verifier"
        context = json.dumps(evidence, ensure_ascii=False, default=str)[:4000]
        user = (
            f"Criterion id={crit.id} description={crit.description} "
            f"check={crit.check}. Default is FAIL."
        )
        raw = llm(VERIFIER_SYSTEM, context, user) or ""
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            payload = json.loads(raw[start : end + 1] if start >= 0 and end > start else raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False, f"unparseable verifier payload: {raw[:200]}"
        rows = payload.get("criteria") or []
        if isinstance(payload.get("met"), bool) and not rows:
            return bool(payload.get("met")), str(payload.get("evidence") or raw[:200])
        for row in rows:
            if str(row.get("id")) == crit.id:
                return bool(row.get("met")), str(row.get("evidence") or "")
        return False, "verifier did not score this criterion"


def run_fixture_verify(
    criteria: Optional[Sequence[Any]],
    *,
    enabled: bool,
    cwd: str,
    evidence: Optional[Dict[str, Any]] = None,
) -> VerifyResult:
    """Eval-stage helper: skip fixtures with no rubric; default-fail otherwise."""
    if not enabled:
        return VerifyResult(passed=True, ran=False, skipped=True, reason="verify_off")
    specs = list(criteria or [])
    if not specs:
        return VerifyResult(
            passed=False,
            ran=False,
            skipped=True,
            reason="no_criteria",
            message="no completion_criteria on fixture",
        )
    gate = CompletionGate(enabled=True, criteria=specs, cwd=cwd)
    return gate.run_verify(role=VERIFIER_ROLE, evidence=evidence)
