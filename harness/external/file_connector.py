"""Local-folder connector for markdown/txt ticket or doc exports."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Generator, Iterable, List, Optional, Sequence

from .models import Checkpoint, ConnectorFailure, ExternalDoc, Section, YieldItem

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
DEFAULT_EXTENSIONS = (".md", ".markdown", ".txt")


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "section"


class FileConnector:
    """Walk a folder of ticket/doc exports. No network."""

    name = "file"

    def __init__(
        self,
        root: str,
        *,
        extensions: Optional[Sequence[str]] = None,
        source: str = "file",
    ):
        self.root = os.path.abspath(root)
        self.extensions = tuple(
            e.lower() if e.startswith(".") else f".{e.lower()}"
            for e in (extensions or DEFAULT_EXTENSIONS)
        )
        self.source = source

    def validate(self) -> None:
        if not self.root:
            raise ValueError("file connector needs --path")
        if not os.path.isdir(self.root):
            raise ValueError(f"file connector path is not a directory: {self.root}")

    def load(
        self, checkpoint: Optional[Checkpoint] = None
    ) -> Generator[YieldItem, None, Checkpoint]:
        self.validate()
        resume_after = (checkpoint.cursor if checkpoint else None) or ""
        seen_resume = not resume_after
        last = resume_after
        for rel, abs_path in self._iter_files():
            if not seen_resume:
                if rel <= resume_after:
                    continue
                seen_resume = True
            try:
                yield self._read_doc(rel, abs_path)
                last = rel
            except OSError as exc:
                yield ConnectorFailure(failure_message=str(exc), failed_id=rel)
        now = datetime.now(timezone.utc).isoformat()
        return Checkpoint(has_more=False, cursor=last, completed_at=now)

    def poll(
        self,
        start: Optional[str],
        end: Optional[str],
        checkpoint: Optional[Checkpoint] = None,
    ) -> Generator[YieldItem, None, Checkpoint]:
        self.validate()
        start_ts = _parse_ts(start)
        end_ts = _parse_ts(end)
        last = checkpoint.cursor if checkpoint else ""
        for rel, abs_path in self._iter_files():
            try:
                mtime = os.path.getmtime(abs_path)
            except OSError as exc:
                yield ConnectorFailure(failure_message=str(exc), failed_id=rel)
                continue
            if start_ts is not None and mtime < start_ts:
                continue
            if end_ts is not None and mtime > end_ts:
                continue
            try:
                yield self._read_doc(rel, abs_path)
                last = rel
            except OSError as exc:
                yield ConnectorFailure(failure_message=str(exc), failed_id=rel)
        now = datetime.now(timezone.utc).isoformat()
        return Checkpoint(has_more=False, cursor=last or (checkpoint.cursor if checkpoint else None), completed_at=now)

    def slim_ids(self) -> Iterable[str]:
        self.validate()
        return [self._doc_id(rel) for rel, _ in self._iter_files()]

    def _iter_files(self) -> List[tuple]:
        found = []
        for dirpath, _dirnames, filenames in os.walk(self.root):
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext not in self.extensions:
                    continue
                abs_path = os.path.join(dirpath, name)
                rel = os.path.relpath(abs_path, self.root).replace("\\", "/")
                found.append((rel, abs_path))
        found.sort(key=lambda item: item[0])
        return found

    def _doc_id(self, rel: str) -> str:
        return f"file:{rel}"

    def _read_doc(self, rel: str, abs_path: str) -> ExternalDoc:
        with open(abs_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        title = os.path.splitext(os.path.basename(rel))[0]
        sections = _split_sections(text, abs_path)
        if not sections:
            sections = [Section(text=text, link=f"file://{abs_path}", heading=title)]
        if sections and sections[0].heading:
            title = sections[0].heading
        mtime = datetime.fromtimestamp(os.path.getmtime(abs_path), tz=timezone.utc).isoformat()
        meta = {"path": rel, "abs_path": abs_path}
        stem_key = os.path.splitext(os.path.basename(rel))[0]
        from .linking import extract_ticket_keys

        keys = extract_ticket_keys(stem_key) or extract_ticket_keys(rel)
        if keys:
            meta["ticket"] = keys[0]
        return ExternalDoc(
            id=self._doc_id(rel),
            source=self.source,
            title=title,
            url=f"file://{abs_path}",
            sections=sections,
            metadata=meta,
            updated_at=mtime,
        )


def _split_sections(text: str, abs_path: str) -> List[Section]:
    lines = (text or "").splitlines()
    sections: List[Section] = []
    heading = ""
    buf: List[str] = []

    def flush():
        body = "\n".join(buf).strip()
        if not heading and not body:
            return
        link = f"file://{abs_path}"
        if heading:
            link = f"{link}#{_slug(heading)}"
        sections.append(Section(text=body, link=link, heading=heading))

    for line in lines:
        match = HEADING.match(line)
        if match:
            flush()
            heading = match.group(2).strip()
            buf = []
        else:
            buf.append(line)
    flush()
    return sections


def _parse_ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    raw = value.strip()
    if raw.isdigit():
        return float(raw)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
