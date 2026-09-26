"""Optional typed decision-model client (Jev / TypeSafe / Venice System One).

Used only when ``decision.enabled`` is on. Decision models return yes/no
(noul), choice, or score answers with probabilities — they do not write
prose and they do **not** replace HyDE. HyDE in ``harness/embedder.py``
stays a free template string.

Fail-soft: missing key, timeout, or provider error logs a warning and
returns ``None`` so the caller keeps the current heuristic / LLM path.
"""

from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USE_LOOP_GRADE = "loop_grade"
USE_VERIFY = "verify"
DEFAULT_USES = (USE_LOOP_GRADE, USE_VERIFY)
LOOP_ACTIONS = ("proceed", "rewrite", "deepen", "hyde")
ACTION_TO_LOOP = {
    "proceed": "stop",
    "rewrite": "rewrite",
    "deepen": "deepen_graph",
    "deepen_graph": "deepen_graph",
    "hyde": "hyde",
}

_FALSEY = frozenset({"0", "false", "off", "no"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

HTTP_PROVIDERS = frozenset({"jev", "typesafe", "systemone", "venice"})
LOCAL_PROVIDERS = frozenset({"mock", "heuristic"})

PROVIDER_ENDPOINTS = {
    "jev": ("https://api.typesafe.ai/v1", "/systemone"),
    "typesafe": ("https://api.typesafe.ai/v1", "/systemone"),
    "systemone": ("https://api.typesafe.ai/v1", "/systemone"),
    "venice": ("https://api.venice.ai/api/v1", "/decisions"),
}

KEY_ENVS = (
    "CODEHARNESS_DECISION_API_KEY",
    "JEV_API_KEY",
    "TYPESAFE_API_KEY",
    "VENICE_API_KEY",
)


def _copy_decision(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from .config import DEFAULT_CONFIG

    base = dict(DEFAULT_CONFIG["decision"])
    base["uses"] = list(base.get("uses") or DEFAULT_USES)
    if raw:
        uses = raw.get("uses", base["uses"])
        base.update(raw)
        base["uses"] = list(uses or DEFAULT_USES)
    return base


def decision_section(config=None) -> Dict[str, Any]:
    if config is None:
        return _copy_decision()
    section = getattr(config, "decision", None)
    if not isinstance(section, dict):
        return _copy_decision()
    return section


def decision_enabled(config=None, environ=None) -> bool:
    env = environ if environ is not None else os.environ
    raw = str(env.get("CODEHARNESS_DECISION_ENABLED") or "").strip().lower()
    if raw in _FALSEY:
        return False
    if raw in _TRUTHY:
        return True
    return bool(decision_section(config).get("enabled"))


def resolve_decision_api_key(decision: Optional[Dict[str, Any]] = None, environ=None) -> Optional[str]:
    section = decision or {}
    if section.get("api_key"):
        return str(section["api_key"])
    env = environ if environ is not None else os.environ
    for name in KEY_ENVS:
        val = (env.get(name) or "").strip()
        if val:
            return val
    return None


def apply_decision_config(config, args=None, environ=None):
    """Stamp env / flags onto ``config.decision``. Default remains disabled."""
    del args  # reserved for a future CLI flag; env + config file are the knobs
    env = environ if environ is not None else os.environ
    section = getattr(config, "decision", None)
    if section is None:
        config.decision = _copy_decision()
        section = config.decision
    section["enabled"] = decision_enabled(config, environ=env)
    provider = (env.get("CODEHARNESS_DECISION_PROVIDER") or "").strip()
    if provider:
        section["provider"] = provider
    base = (env.get("CODEHARNESS_DECISION_API_BASE") or "").strip()
    if base:
        section["api_base"] = base
    model = (env.get("CODEHARNESS_DECISION_MODEL") or "").strip()
    if model:
        section["model"] = model
    timeout = (env.get("CODEHARNESS_DECISION_TIMEOUT_MS") or "").strip()
    if timeout:
        try:
            section["timeout_ms"] = int(timeout)
        except ValueError:
            pass
    min_c = (env.get("CODEHARNESS_DECISION_MIN_CONFIDENCE") or "").strip()
    if min_c:
        try:
            section["min_confidence"] = float(min_c)
        except ValueError:
            pass
    uses = (env.get("CODEHARNESS_DECISION_USES") or "").strip()
    if uses:
        section["uses"] = [part.strip() for part in uses.split(",") if part.strip()]
    key = resolve_decision_api_key(section, environ=env)
    if key and not section.get("api_key"):
        section["api_key"] = key
    return config


def _warn(message: str) -> None:
    warnings.warn(message, RuntimeWarning, stacklevel=3)


@dataclass
class DecisionAnswer:
    id: str
    type: str
    value: Any = None
    confidence: float = 0.0
    probabilities: Dict[str, float] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def confident(self, min_confidence: float = 0.0) -> bool:
        try:
            return float(self.confidence) >= float(min_confidence or 0.0)
        except (TypeError, ValueError):
            return False


@dataclass
class DecisionResult:
    answers: Dict[str, DecisionAnswer] = field(default_factory=dict)
    model: str = ""
    used: bool = True
    fallback: bool = False
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def get(self, qid: str) -> Optional[DecisionAnswer]:
        return self.answers.get(qid)

    def noul(self, qid: str) -> Optional[float]:
        ans = self.get(qid)
        if ans is None or ans.value is None:
            return None
        try:
            return float(ans.value)
        except (TypeError, ValueError):
            return None

    def choice(self, qid: str) -> Optional[str]:
        ans = self.get(qid)
        if ans is None or ans.value is None:
            return None
        return str(ans.value)

    def score(self, qid: str) -> Optional[float]:
        return self.noul(qid)

    def confidence(self, qid: str) -> Optional[float]:
        ans = self.get(qid)
        if ans is None:
            return None
        return float(ans.confidence)


def parse_answers(payload: Any) -> DecisionResult:
    data = payload if isinstance(payload, dict) else {}
    raw_answers = data.get("answers") if isinstance(data.get("answers"), dict) else data
    parsed: Dict[str, DecisionAnswer] = {}
    if isinstance(raw_answers, dict):
        for qid, row in raw_answers.items():
            if not isinstance(row, dict):
                continue
            kind = str(row.get("type") or "").strip().lower()
            if kind == "noul" or "noul" in row:
                value = row.get("noul")
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    value = None
                confidence = row.get("confidence")
                if confidence is None and value is not None:
                    confidence = abs(value - 0.5) * 2.0
                parsed[str(qid)] = DecisionAnswer(
                    id=str(qid),
                    type="noul",
                    value=value,
                    confidence=_as_float(confidence, 0.0),
                    probabilities=dict(row.get("probabilities") or {}),
                    raw=row,
                )
            elif kind == "score" or "score" in row:
                parsed[str(qid)] = DecisionAnswer(
                    id=str(qid),
                    type="score",
                    value=_as_float(row.get("score"), None),
                    confidence=_as_float(row.get("confidence"), 0.0),
                    probabilities=_string_keys(row.get("probabilities") or {}),
                    raw=row,
                )
            else:
                parsed[str(qid)] = DecisionAnswer(
                    id=str(qid),
                    type="choice",
                    value=row.get("choice"),
                    confidence=_as_float(row.get("confidence"), 0.0),
                    probabilities=dict(row.get("probabilities") or {}),
                    raw=row,
                )
    return DecisionResult(
        answers=parsed,
        model=str(data.get("model") or ""),
        raw=data if isinstance(data, dict) else {},
    )


def _as_float(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _string_keys(mapping: Any) -> Dict[str, float]:
    if not isinstance(mapping, dict):
        return {}
    out: Dict[str, float] = {}
    for key, val in mapping.items():
        try:
            out[str(key)] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def _urlopen_post(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, val in headers.items():
        req.add_header(key, val)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


class HttpDecisionProvider:
    """POST ``state`` + typed ``questions`` to a System One / decisions endpoint."""

    def __init__(
        self,
        *,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_ms: int = 2000,
        provider: str = "jev",
        post: Optional[Callable[..., Dict[str, Any]]] = None,
    ):
        self.provider = (provider or "jev").strip().lower() or "jev"
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key
        self.model = model or "jev-latest"
        self.timeout_ms = int(timeout_ms or 2000)
        self._post = post or _urlopen_post

    def endpoint(self) -> str:
        default_base, default_path = PROVIDER_ENDPOINTS.get(
            self.provider, PROVIDER_ENDPOINTS["jev"]
        )
        base = self.api_base or default_base
        lowered = base.lower()
        if lowered.endswith(("/systemone", "/decisions", "/decide")):
            return base
        return base.rstrip("/") + default_path

    def decide(self, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("decision model enabled but no API key")
        payload = {
            "model": self.model,
            "state": state,
            "questions": questions,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = max(self.timeout_ms, 1) / 1000.0
        return self._post(self.endpoint(), headers, payload, timeout)


class MockDecisionProvider:
    """Scripted answers for tests. ``answers`` may be a dict or a callable."""

    def __init__(self, answers: Any = None, error: Optional[BaseException] = None):
        self.answers = answers or {}
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    def decide(self, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"state": state, "questions": questions})
        if self.error is not None:
            raise self.error
        raw = self.answers(state, questions) if callable(self.answers) else self.answers
        if isinstance(raw, dict) and "answers" in raw:
            return raw
        return {"model": "mock", "answers": dict(raw or {})}


class HeuristicDecisionProvider:
    """Local stand-in: map a numeric grade onto noul / proceed-vs-rewrite."""

    def __init__(self, grade_threshold: float = 0.35):
        self.grade_threshold = float(grade_threshold)
        self.calls: List[Dict[str, Any]] = []

    def decide(self, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"state": state, "questions": questions})
        grade = _heuristic_grade_from_state(state)
        adequate = max(0.0, min(1.0, grade))
        proceed = adequate >= self.grade_threshold
        answers: Dict[str, Any] = {}
        for qid, spec in (questions or {}).items():
            kind = str((spec or {}).get("type") or "noul").lower()
            if kind == "choice":
                answers[qid] = {
                    "type": "choice",
                    "choice": "proceed" if proceed else "rewrite",
                    "confidence": 1.0,
                    "probabilities": {
                        "proceed": 1.0 if proceed else 0.0,
                        "rewrite": 0.0 if proceed else 1.0,
                    },
                }
            elif kind == "score":
                answers[qid] = {
                    "type": "score",
                    "score": adequate,
                    "confidence": 1.0,
                    "probabilities": {"0": 1.0 - adequate, "1": adequate},
                }
            else:
                answers[qid] = {"type": "noul", "noul": adequate}
        return {"model": "heuristic", "answers": answers}


def _heuristic_grade_from_state(state: Any) -> float:
    if isinstance(state, dict):
        for key in ("heuristic_grade", "grade"):
            if key in state:
                try:
                    return float(state[key])
                except (TypeError, ValueError):
                    return 0.0
        return 0.0
    if isinstance(state, str):
        try:
            data = json.loads(state)
        except (TypeError, ValueError, json.JSONDecodeError):
            return 0.0
        return _heuristic_grade_from_state(data)
    return 0.0


def build_provider(config=None, environ=None, provider=None):
    if provider is not None:
        return provider
    section = decision_section(config)
    name = str(section.get("provider") or "").strip().lower()
    if name in ("mock",):
        return MockDecisionProvider()
    if name in ("heuristic",):
        retrieval = getattr(config, "retrieval", None) or {}
        return HeuristicDecisionProvider(
            grade_threshold=float(retrieval.get("grade_threshold", 0.35)),
        )
    if name in HTTP_PROVIDERS or name:
        return HttpDecisionProvider(
            api_base=section.get("api_base"),
            api_key=resolve_decision_api_key(section, environ=environ),
            model=section.get("model") or "jev-latest",
            timeout_ms=int(section.get("timeout_ms") or 2000),
            provider=name or "jev",
        )
    return None


class DecisionClient:
    """Thin ``decide(state, questions) -> typed answers`` wrapper."""

    def __init__(self, config=None, provider=None, environ=None):
        self.config = config
        self.environ = environ
        self.section = decision_section(config)
        self.provider = provider if provider is not None else build_provider(
            config, environ=environ
        )
        self.last_error: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return decision_enabled(self.config, environ=self.environ)

    @property
    def min_confidence(self) -> float:
        try:
            return float(self.section.get("min_confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def uses(self, name: str) -> bool:
        if not self.enabled:
            return False
        listed = self.section.get("uses")
        if listed is None:
            return True
        return str(name) in {str(item) for item in listed}

    def decide(self, state: Any, questions: Dict[str, Any]) -> Optional[DecisionResult]:
        self.last_error = None
        if not self.enabled:
            return None
        if self.provider is None:
            self.last_error = "decision model enabled but no provider"
            _warn(self.last_error + "; falling back to heuristics")
            return None
        name = str(self.section.get("provider") or "").strip().lower()
        needs_key = name in HTTP_PROVIDERS or isinstance(self.provider, HttpDecisionProvider)
        if needs_key and not resolve_decision_api_key(self.section, environ=self.environ):
            if not getattr(self.provider, "api_key", None):
                self.last_error = "decision model enabled but no API key"
                _warn(self.last_error + "; falling back to heuristics")
                return None
        try:
            raw = self.provider.decide(state, questions)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, RuntimeError) as exc:
            self.last_error = str(exc)
            _warn(f"decision model failed; falling back to heuristics: {exc}")
            return None
        except Exception as exc:  # fail-soft: never crash the query path
            self.last_error = str(exc)
            _warn(f"decision model failed; falling back to heuristics: {exc}")
            return None
        if not raw:
            self.last_error = "empty decision response"
            _warn(self.last_error + "; falling back to heuristics")
            return None
        return parse_answers(raw)


def build_decision_client(config=None, environ=None, provider=None) -> Optional[DecisionClient]:
    if not decision_enabled(config, environ=environ):
        return None
    return DecisionClient(config, provider=provider, environ=environ)


def loop_grade_questions() -> Dict[str, Any]:
    return {
        "adequate": {
            "type": "noul",
            "instructions": (
                "Is the retrieved context adequate to answer the query? "
                "Yes if names/paths match and packed chunks support the question."
            ),
        },
        "action": {
            "type": "choice",
            "instructions": (
                "Choose the next retrieval action. proceed: stop looping. "
                "rewrite: lexical rewrite of the query. deepen: expand graph "
                "neighbors. hyde: retry retrieve with HyDE embedding expansion "
                "(do not generate hypothetical text; HyDE is a template)."
            ),
            "criteria": {
                "proceed": "retrieved context is sufficient; stop extra retrieves",
                "rewrite": "narrow the query to identifiers or file names",
                "deepen": "walk callers, callees, or graph neighbors",
                "hyde": "retry retrieve with HyDE embedding expansion only",
            },
        },
    }


def build_loop_state(
    *,
    query: str,
    results: Sequence[Any],
    packed: Any,
    heuristic_grade: float,
    coverage: float,
    attempt: int,
    mode: str,
) -> Dict[str, Any]:
    hits = []
    for item in list(results or [])[:8]:
        chunk = getattr(item, "chunk", item)
        hits.append({
            "name": getattr(chunk, "entity_name", "") or "",
            "path": getattr(chunk, "file_path", "") or "",
            "score": getattr(item, "score", None),
        })
    return {
        "query": query,
        "heuristic_grade": float(heuristic_grade),
        "coverage": float(coverage),
        "attempt": int(attempt),
        "mode": mode,
        "packed_paths": list(getattr(packed, "packed_paths", []) or [])[:12],
        "hits": hits,
    }


def interpret_loop_decision(
    result: Optional[DecisionResult],
    *,
    min_confidence: float = 0.0,
    grade_threshold: float = 0.35,
) -> Optional[Dict[str, Any]]:
    """Return ``{grade, action}`` when the model is confident enough."""
    if result is None:
        return None
    action_ans = result.get("action")
    adequate_ans = result.get("adequate")
    chosen = None
    grade = None
    if action_ans is not None and action_ans.confident(min_confidence):
        raw = str(action_ans.value or "").strip().lower()
        if raw in ACTION_TO_LOOP:
            chosen = raw if raw != "deepen_graph" else "deepen"
    if adequate_ans is not None and adequate_ans.confident(min_confidence):
        try:
            grade = float(adequate_ans.value)
        except (TypeError, ValueError):
            grade = None
        if chosen is None and grade is not None:
            chosen = "proceed" if grade >= grade_threshold else "rewrite"
    if chosen is None and grade is None:
        return None
    return {"grade": grade, "action": chosen, "result": result}


def decide_loop_grade(decider: Optional[DecisionClient], **state_kwargs) -> Optional[Dict[str, Any]]:
    if decider is None or not decider.uses(USE_LOOP_GRADE):
        return None
    result = decider.decide(build_loop_state(**state_kwargs), loop_grade_questions())
    retrieval = getattr(getattr(decider, "config", None), "retrieval", None) or {}
    return interpret_loop_decision(
        result,
        min_confidence=decider.min_confidence,
        grade_threshold=float(retrieval.get("grade_threshold", 0.35)),
    )


def citation_verify_questions() -> Dict[str, Any]:
    return {
        "supported": {
            "type": "noul",
            "instructions": (
                "Is the answer supported by the packed code chunks? "
                "Yes only when cited paths and claims appear in the context."
            ),
        },
    }


def decide_citation_supported(
    decider: Optional[DecisionClient],
    query: str,
    answer: str,
    packed_context: str,
) -> Optional[Dict[str, Any]]:
    if decider is None or not decider.uses(USE_VERIFY):
        return None
    result = decider.decide(
        {
            "query": query,
            "answer": answer,
            "packed_context": (packed_context or "")[:4000],
        },
        citation_verify_questions(),
    )
    if result is None:
        return None
    ans = result.get("supported")
    if ans is None or not ans.confident(decider.min_confidence):
        return None
    try:
        noul = float(ans.value)
    except (TypeError, ValueError):
        return None
    return {
        "supported": noul >= 0.5,
        "missing_paths": [],
        "noul": noul,
        "confidence": ans.confidence,
        "source": "decision",
        "raw": result.raw,
    }


def decide_verify_criterion(
    decider: Optional[DecisionClient],
    crit,
    evidence: Optional[Dict[str, Any]] = None,
) -> Optional[tuple]:
    if decider is None or not decider.uses(USE_VERIFY):
        return None
    cid = getattr(crit, "id", None) or "criterion"
    result = decider.decide(
        {
            "criterion": {
                "id": cid,
                "description": getattr(crit, "description", "") or "",
                "kind": getattr(crit, "kind", "") or "llm",
                "check": getattr(crit, "check", "") or "",
            },
            "evidence": evidence or {},
        },
        {
            str(cid): {
                "type": "noul",
                "instructions": (
                    f"Is criterion {cid!r} met given the evidence? "
                    f"{getattr(crit, 'description', '') or ''}. Default is no."
                ),
            }
        },
    )
    if result is None:
        return None
    ans = result.get(str(cid))
    if ans is None or not ans.confident(decider.min_confidence):
        return None
    try:
        noul = float(ans.value)
    except (TypeError, ValueError):
        return None
    met = noul >= 0.5
    return met, f"decision noul={noul:.3f} confidence={ans.confidence:.3f}"
