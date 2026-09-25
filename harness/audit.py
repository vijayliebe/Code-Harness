"""Append-only audit JSONL for sensitive actions (redactions, LLM sends).

Never persist raw secrets — only counts, kinds, and fingerprint hashes.
Default path: ``.code-harness/audit/audit.jsonl``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .redact import RedactionResult, query_id_for


DEFAULT_AUDIT_REL = os.path.join(".code-harness", "audit", "audit.jsonl")


def default_audit_path(repo_path: str = ".", config=None) -> str:
    red = getattr(config, "redaction", None) or {}
    rel = red.get("audit_path") or DEFAULT_AUDIT_REL
    if os.path.isabs(rel):
        return rel
    root = os.path.abspath(repo_path or ".")
    return os.path.join(root, rel)


def append_audit(
    *,
    action: str,
    path: Optional[str] = None,
    config=None,
    redaction: Optional[RedactionResult] = None,
    query: Optional[str] = None,
    query_id: Optional[str] = None,
    session_id: Optional[str] = None,
    chunk_ids: Optional[List[str]] = None,
    tokens: Optional[int] = None,
    model: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append one JSON object. Returns the row that was written."""
    dest = path or default_audit_path(
        getattr(config, "repo_path", None) or ".",
        config=config,
    )
    kinds = redaction.counts_by_kind() if redaction is not None else {}
    fingerprints = [h.fingerprint for h in (redaction.hits if redaction else [])]
    qid = query_id or (query_id_for(query) if query else None)
    row: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": action,
        "redaction_count": redaction.count if redaction is not None else 0,
        "kinds": kinds,
        "fingerprints": fingerprints,
    }
    if qid:
        row["query_id"] = qid
    if session_id:
        row["session_id"] = session_id
    if chunk_ids:
        row["chunk_ids"] = list(chunk_ids)
    if tokens is not None:
        row["tokens"] = int(tokens)
    if model:
        row["model"] = model
    if extra:
        row.update(extra)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def tail_audit(n: int = 20, path: Optional[str] = None, config=None) -> List[Dict[str, Any]]:
    dest = path or default_audit_path(".", config=config)
    if not dest or not os.path.isfile(dest):
        return []
    take = max(0, int(n or 0))
    if take == 0:
        return []
    with open(dest, encoding="utf-8") as fh:
        lines = [line for line in fh if line.strip()]
    rows: List[Dict[str, Any]] = []
    for line in lines[-take:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
