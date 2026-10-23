"""Ingest / poll / prune external docs. Redacts text before it hits disk."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from .file_connector import FileConnector
from .models import Checkpoint, ConnectorFailure, ExternalDoc, YieldItem
from .store import ExternalStore


def consume_load(generator) -> Tuple[List[YieldItem], Checkpoint]:
    items: List[YieldItem] = []
    try:
        while True:
            items.append(next(generator))
    except StopIteration as stop:
        checkpoint = stop.value if isinstance(stop.value, Checkpoint) else Checkpoint()
    return items, checkpoint


def redact_doc(doc: ExternalDoc, config) -> ExternalDoc:
    from harness.redact import redact_and_audit, redact_text, redaction_enabled

    if not redaction_enabled(config):
        return doc
    action = "external_ingest"
    title = redact_and_audit(doc.title or "", action=action, config=config, extra={"field": "title"})
    doc.title = title.text
    for section in doc.sections:
        body = redact_and_audit(
            section.text or "",
            action=action,
            config=config,
            extra={"field": "section", "heading": section.heading},
        )
        section.text = body.text
        if section.heading:
            heading = redact_text(section.heading, enabled=True)
            section.heading = heading.text
    return doc


def run_ingest(
    config,
    path: str,
    *,
    poll: bool = False,
    start: Optional[str] = None,
    end: Optional[str] = None,
    resume: bool = False,
    prune: bool = True,
) -> dict:
    ext = getattr(config, "external", None) or {}
    connectors = ext.get("connectors") or {}
    file_cfg = connectors.get("file") or {}
    extensions = file_cfg.get("extensions") or None
    connector = FileConnector(path, extensions=extensions)
    connector.validate()
    store = ExternalStore.for_config(config)
    checkpoint = store.load_checkpoint(connector.name) if resume else Checkpoint()
    if poll:
        generator = connector.poll(start, end, checkpoint)
    else:
        generator = connector.load(checkpoint)
    items, new_checkpoint = consume_load(generator)
    docs: List[ExternalDoc] = []
    failures: List[ConnectorFailure] = []
    for item in items:
        if isinstance(item, ConnectorFailure):
            failures.append(item)
        elif isinstance(item, ExternalDoc):
            docs.append(redact_doc(item, config))
    upserted = store.upsert(docs) if docs else 0
    store.record_failures(failures)
    store.save_checkpoint(connector.name, new_checkpoint or Checkpoint(completed_at=datetime.now(timezone.utc).isoformat()))
    pruned = 0
    if prune and not poll:
        pruned = store.prune(connector.slim_ids())
    return {
        "upserted": upserted,
        "failures": len(failures),
        "pruned": pruned,
        "docs": len(store.list_docs()),
        "path": path,
        "connector": connector.name,
    }


def run_link(config, repo_path: str, knowledge_graph=None) -> dict:
    from .linking import apply_mentions, mine_repo, persist_mentions

    mentions = mine_repo(repo_path)
    persist_mentions(mentions, config, repo_path)
    applied = 0
    if knowledge_graph is not None:
        applied = apply_mentions(knowledge_graph, mentions)
        if hasattr(knowledge_graph, "save"):
            knowledge_graph.save()
    tickets = sorted({m.source_id for m in mentions if m.source_kind == "ticket"})
    return {
        "mentions": len(mentions),
        "applied_edges": applied,
        "tickets": tickets,
        "repo": repo_path,
    }
