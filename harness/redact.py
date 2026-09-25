"""High-precision secret redaction for outbound text.

Call :func:`redact_text` (or :func:`redact_and_audit`) on any payload that
can leave the machine: packed LLM context, session prints, wiki/memory
exports, and future retrieve/API response bodies (Fusion PR #5).

Default-on for outbound LLM payloads. Disable only via
``CODEHARNESS_REDACT=0|false|off|no``, ``redaction.enabled: false``, or
``--no-redact``. Stdlib only.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Pattern, Sequence, Tuple

PLACEHOLDER_VALUES = frozenset({
    "changeme",
    "change_me",
    "changemeplease",
    "your_api_key",
    "your-api-key",
    "yourapikey",
    "placeholder",
    "example",
    "dummy",
    "password",
    "secret",
    "token",
    "undefined",
    "none",
    "null",
    "todo",
    "fixme",
    "redacted",
    "...",
    "xxx",
    "xxxx",
    "xxxxx",
    "******",
    "sk-...",
    "<token>",
    "<secret>",
    "not_a_secret",
})

# Secret-ish left-hand names for .env / assignment heuristics.
_SECRET_NAME = (
    r"(?:[A-Za-z][A-Za-z0-9_]*_)?(?:"
    r"API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD|"
    r"ACCESS[_-]?KEY|CLIENT[_-]?SECRET|PRIVATE[_-]?KEY|"
    r"AWS_ACCESS_KEY_ID|DATABASE_URL|REDIS_URL"
    r")"
)

_PEM = re.compile(
    r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----",
    re.DOTALL,
)
_AWS = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GITHUB = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")
_GITHUB_FINE = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")
_GITLAB = re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")
_SLACK = re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")
_STRIPE = re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")
_OPENAI = re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b")
_HF = re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")
_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_BEARER = re.compile(
    r"\bBearer\s+([A-Za-z0-9\-._~+/]+=*)",
    re.IGNORECASE,
)
_CONN = re.compile(
    r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|amqps?|amqp)"
    r"://[^:\s/@]+:([^@\s/]+)@",
    re.IGNORECASE,
)
_ENV = re.compile(
    rf"(?i)((?:export\s+)?{_SECRET_NAME}\s*[=:]\s*)(['\"]?)([^'\"\s#;]{{8,}})\2"
)

# High-precision named patterns first; assignment / entropy last.
_NAMED: Sequence[Tuple[str, Pattern[str], int]] = (
    ("private_key", _PEM, 0),
    ("aws_access_key", _AWS, 0),
    ("github_token", _GITHUB, 0),
    ("github_token", _GITHUB_FINE, 0),
    ("gitlab_token", _GITLAB, 0),
    ("slack_token", _SLACK, 0),
    ("stripe_key", _STRIPE, 0),
    ("api_key", _OPENAI, 0),
    ("api_key", _HF, 0),
    ("jwt", _JWT, 0),
)


@dataclass
class RedactionHit:
    kind: str
    fingerprint: str
    start: int
    end: int


@dataclass
class RedactionResult:
    text: str
    hits: List[RedactionHit] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.hits)

    def counts_by_kind(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for hit in self.hits:
            out[hit.kind] = out.get(hit.kind, 0) + 1
        return out


def fingerprint(secret: str) -> str:
    """SHA-256 prefix of a secret. Never return the raw value."""
    return hashlib.sha256((secret or "").encode("utf-8")).hexdigest()[:12]


def query_id_for(query: str) -> str:
    return hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:16]


def redaction_enabled(config=None, environ=None, explicit: Optional[bool] = None) -> bool:
    if explicit is False:
        return False
    if explicit is True:
        return True
    env = environ if environ is not None else os.environ
    raw = (env.get("CODEHARNESS_REDACT") or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if config is not None:
        red = getattr(config, "redaction", None) or {}
        if red.get("enabled") is False:
            return False
    return True


def audit_enabled(config=None, environ=None) -> bool:
    env = environ if environ is not None else os.environ
    raw = (env.get("CODEHARNESS_AUDIT") or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if config is not None:
        red = getattr(config, "redaction", None) or {}
        if red.get("audit") is False:
            return False
    return True


def redact_text(text: str, *, enabled: bool = True) -> RedactionResult:
    if not enabled or not text:
        return RedactionResult(text=text or "", hits=[])
    spans = _collect_spans(text)
    if not spans:
        return RedactionResult(text=text, hits=[])
    parts: List[str] = []
    hits: List[RedactionHit] = []
    cursor = 0
    for start, end, kind, secret in spans:
        parts.append(text[cursor:start])
        fp = fingerprint(secret)
        parts.append(f"[REDACTED:{kind}:{fp}]")
        hits.append(RedactionHit(kind=kind, fingerprint=fp, start=start, end=end))
        cursor = end
    parts.append(text[cursor:])
    return RedactionResult(text="".join(parts), hits=hits)


def redact_and_audit(
    text: str,
    *,
    action: str,
    config=None,
    enabled: Optional[bool] = None,
    audit: Optional[bool] = None,
    audit_path: Optional[str] = None,
    query: Optional[str] = None,
    query_id: Optional[str] = None,
    session_id: Optional[str] = None,
    chunk_ids: Optional[List[str]] = None,
    tokens: Optional[int] = None,
    model: Optional[str] = None,
    extra: Optional[Dict] = None,
) -> RedactionResult:
    """Redact ``text`` and, on hits, append an audit row (no raw secrets)."""
    on = redaction_enabled(config, explicit=enabled)
    result = redact_text(text, enabled=on)
    should_audit = audit if audit is not None else audit_enabled(config)
    if should_audit and result.count:
        from .audit import append_audit

        append_audit(
            action=action,
            path=audit_path,
            config=config,
            redaction=result,
            query=query,
            query_id=query_id,
            session_id=session_id,
            chunk_ids=chunk_ids,
            tokens=tokens,
            model=model,
            extra=extra,
        )
    return result


def _shannon(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    n = float(len(value))
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _normalize_placeholder(value: str) -> str:
    return (value or "").strip().strip("'\"`").lower()


def _is_placeholder(value: str) -> bool:
    norm = _normalize_placeholder(value)
    if not norm:
        return True
    if "redacted" in norm:
        return True
    if norm in PLACEHOLDER_VALUES:
        return True
    if norm.startswith("your_") or norm.startswith("<") or norm.endswith(">"):
        return True
    if set(norm) <= set("x*.-"):
        return True
    return False


def _looks_like_token(value: str) -> bool:
    if not value or _is_placeholder(value):
        return False
    if value[:1] in "{$<" or "(" in value or ")" in value:
        return False
    if len(value) < 16:
        return False
    return True


def _looks_like_secret_value(value: str) -> bool:
    """Assignment-value filter: prefer mixed/high-entropy over English words."""
    v = (value or "").strip().strip("'\"")
    if len(v) < 8 or _is_placeholder(v):
        return False
    if v[:1] in "{$<" or "(" in v or ")" in v:
        return False
    if "/" in v and "://" not in v:
        return False
    entropy = _shannon(v)
    has_digit = any(c.isdigit() for c in v)
    has_alpha = any(c.isalpha() for c in v)
    mixed = has_digit and has_alpha
    if len(v) >= 20 and entropy >= 3.2:
        return True
    if len(v) >= 12 and mixed:
        return True
    if len(v) >= 16 and (has_digit or any(c.isupper() for c in v)) and entropy >= 3.5:
        return True
    return False


def _collect_spans(text: str) -> List[Tuple[int, int, str, str]]:
    found: List[Tuple[int, int, str, str]] = []
    for kind, pattern, group in _NAMED:
        for match in pattern.finditer(text):
            secret = match.group(group) if group else match.group(0)
            start, end = match.span(group) if group else match.span(0)
            if _is_placeholder(secret):
                continue
            found.append((start, end, kind, secret))

    for match in _BEARER.finditer(text):
        secret = match.group(1)
        if not _looks_like_token(secret):
            continue
        start, end = match.span(1)
        found.append((start, end, "bearer", secret))

    for match in _CONN.finditer(text):
        secret = match.group(1)
        if _is_placeholder(secret) or len(secret) < 4:
            continue
        start, end = match.span(1)
        found.append((start, end, "connection_string", secret))

    for match in _ENV.finditer(text):
        secret = match.group(3)
        if not _looks_like_secret_value(secret):
            continue
        start, end = match.span(3)
        # Connection strings already handled; skip overlapping password span later.
        found.append((start, end, "env_assignment", secret))

    return _merge_spans(found)


def _merge_spans(spans: Iterable[Tuple[int, int, str, str]]) -> List[Tuple[int, int, str, str]]:
    ordered = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
    merged: List[Tuple[int, int, str, str]] = []
    for start, end, kind, secret in ordered:
        if start >= end:
            continue
        overlap = False
        for m_start, m_end, _, _ in merged:
            if start < m_end and end > m_start:
                overlap = True
                break
        if not overlap:
            merged.append((start, end, kind, secret))
    return merged
