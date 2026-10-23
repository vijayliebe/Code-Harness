"""JSONL + BM25 sidecar for external docs. Separate from the code index."""

from __future__ import annotations

import json
import os
import pickle
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set

from rank_bm25 import BM25Okapi

from .models import Checkpoint, ConnectorFailure, ExternalDoc


def external_dir(config, repo_path: str = "") -> str:
    ext = getattr(config, "external", None) or {}
    base = ext.get("persist_directory") or ".code-harness/external"
    root = os.path.abspath(repo_path or getattr(config, "repo_path", "") or ".")
    if os.path.isabs(base):
        return base
    return os.path.join(root, base)


def _tokenize(text: str) -> List[str]:
    import re

    return re.findall(r"[a-z0-9_]+", (text or "").lower())


class ExternalStore:
    """Local-first store. Never writes into the code Chroma collection."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.docs_path = os.path.join(self.root, "docs.jsonl")
        self.failures_path = os.path.join(self.root, "failures.jsonl")
        self.checkpoint_path = os.path.join(self.root, "checkpoint.json")
        self.bm25_path = os.path.join(self.root, "bm25.pkl")
        self._docs: Optional[Dict[str, ExternalDoc]] = None
        self._bm25 = None
        self._bm25_ids: List[str] = []

    @classmethod
    def for_config(cls, config, repo_path: str = "") -> "ExternalStore":
        return cls(external_dir(config, repo_path))

    def ensure(self) -> None:
        os.makedirs(self.root, exist_ok=True)

    def load_docs(self) -> Dict[str, ExternalDoc]:
        if self._docs is not None:
            return self._docs
        docs: Dict[str, ExternalDoc] = {}
        if os.path.isfile(self.docs_path):
            with open(self.docs_path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    doc = ExternalDoc.from_record(rec)
                    if doc.id:
                        docs[doc.id] = doc
        self._docs = docs
        return docs

    def upsert(self, docs: Sequence[ExternalDoc]) -> int:
        self.ensure()
        current = self.load_docs()
        changed = 0
        for doc in docs:
            if not doc.id:
                continue
            current[doc.id] = doc
            changed += 1
        self._docs = current
        self._rewrite_docs()
        self._rebuild_bm25()
        return changed

    def prune(self, keep_ids: Iterable[str]) -> int:
        keep = set(keep_ids)
        current = self.load_docs()
        before = len(current)
        self._docs = {k: v for k, v in current.items() if k in keep}
        removed = before - len(self._docs)
        if removed:
            self._rewrite_docs()
            self._rebuild_bm25()
        return removed

    def record_failures(self, failures: Sequence[ConnectorFailure]) -> None:
        if not failures:
            return
        self.ensure()
        with open(self.failures_path, "a") as fh:
            for failure in failures:
                rec = failure.to_record()
                rec["recorded_at"] = datetime.now(timezone.utc).isoformat()
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def save_checkpoint(self, connector: str, checkpoint: Checkpoint) -> None:
        self.ensure()
        payload = {}
        if os.path.isfile(self.checkpoint_path):
            try:
                with open(self.checkpoint_path) as fh:
                    payload = json.load(fh) or {}
            except (json.JSONDecodeError, OSError):
                payload = {}
        payload[connector] = checkpoint.to_record()
        with open(self.checkpoint_path, "w") as fh:
            json.dump(payload, fh, indent=2)

    def load_checkpoint(self, connector: str) -> Checkpoint:
        if not os.path.isfile(self.checkpoint_path):
            return Checkpoint()
        try:
            with open(self.checkpoint_path) as fh:
                payload = json.load(fh) or {}
        except (json.JSONDecodeError, OSError):
            return Checkpoint()
        return Checkpoint.from_record(payload.get(connector) or {})

    def docs_for_ticket(self, key: str) -> List[ExternalDoc]:
        if not key:
            return []
        needle = key.upper()
        hits = []
        for doc in self.load_docs().values():
            if needle in {k.upper() for k in doc.ticket_keys()}:
                hits.append(doc)
                continue
            blob = f"{doc.id} {doc.title} {doc.url} {doc.body()}".upper()
            if needle in blob:
                hits.append(doc)
        return hits

    def search(self, query: str, top_k: int = 10) -> List[tuple]:
        """Return ``(doc, score)`` pairs from the sidecar BM25 index.

        rank_bm25 IDF is 0 when a term appears in every document (typical
        for 1–2 doc stores). Fall back to token overlap in that case.
        """
        tokens = _tokenize(query)
        if not tokens:
            return []
        docs = self.load_docs()
        if not docs:
            return []
        self._ensure_bm25()
        if self._bm25 is not None and self._bm25_ids:
            scores = self._bm25.get_scores(tokens)
            if any(score > 0 for score in scores):
                ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
                out = []
                for idx, score in ranked[:top_k]:
                    if score <= 0:
                        continue
                    doc = docs.get(self._bm25_ids[idx])
                    if doc is not None:
                        out.append((doc, float(score)))
                return out
        qset = set(tokens)
        overlap_hits = []
        for doc in docs.values():
            blob = " ".join([doc.id, doc.title, doc.url, doc.body(), " ".join(doc.ticket_keys())])
            hit = len(qset & set(_tokenize(blob)))
            if hit:
                overlap_hits.append((doc, float(hit)))
        overlap_hits.sort(key=lambda item: item[1], reverse=True)
        return overlap_hits[:top_k]

    def list_docs(self) -> List[ExternalDoc]:
        return list(self.load_docs().values())

    def _rewrite_docs(self) -> None:
        self.ensure()
        with open(self.docs_path, "w") as fh:
            for doc in self.load_docs().values():
                fh.write(json.dumps(doc.to_record(), ensure_ascii=False) + "\n")

    def _rebuild_bm25(self) -> None:
        docs = self.load_docs()
        ids = list(docs.keys())
        if not ids:
            self._bm25 = None
            self._bm25_ids = []
            if os.path.isfile(self.bm25_path):
                try:
                    os.remove(self.bm25_path)
                except OSError:
                    pass
            return
        tokenized = []
        for doc_id in ids:
            doc = docs[doc_id]
            blob = " ".join([doc.id, doc.title, doc.url, doc.body(), " ".join(doc.ticket_keys())])
            tokenized.append(_tokenize(blob))
        self._bm25 = BM25Okapi(tokenized)
        self._bm25_ids = ids
        self.ensure()
        with open(self.bm25_path, "wb") as fh:
            pickle.dump({"ids": ids, "tokenized": tokenized}, fh)

    def _ensure_bm25(self) -> None:
        if self._bm25 is not None:
            return
        if os.path.isfile(self.bm25_path):
            try:
                with open(self.bm25_path, "rb") as fh:
                    data = pickle.load(fh)
                self._bm25 = BM25Okapi(data["tokenized"])
                self._bm25_ids = list(data["ids"])
                return
            except Exception:
                pass
        self._rebuild_bm25()
