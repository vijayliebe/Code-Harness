"""External document / connector types. Ours, not Onyx's Document model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class Section:
    text: str
    link: str = ""
    heading: str = ""


@dataclass
class ExternalDoc:
    id: str
    source: str
    title: str
    url: str = ""
    sections: List[Section] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    updated_at: Optional[str] = None
    visibility: str = "public"

    def body(self) -> str:
        parts = []
        for section in self.sections:
            if section.heading:
                parts.append(section.heading)
            if section.text:
                parts.append(section.text)
        return "\n\n".join(parts)

    def ticket_keys(self) -> List[str]:
        from .linking import extract_ticket_keys

        blob = " ".join(
            [
                self.id,
                self.title,
                self.url,
                self.body(),
                " ".join(str(v) for v in (self.metadata or {}).values()),
            ]
        )
        keys = extract_ticket_keys(blob)
        extra = self.metadata.get("ticket") or self.metadata.get("key")
        if extra:
            keys.extend(extract_ticket_keys(str(extra)))
        seen = set()
        out = []
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    def to_record(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "sections": [
                {"text": s.text, "link": s.link, "heading": s.heading}
                for s in self.sections
            ],
            "metadata": dict(self.metadata or {}),
            "updated_at": self.updated_at,
            "visibility": self.visibility,
        }

    @classmethod
    def from_record(cls, raw: Dict[str, Any]) -> "ExternalDoc":
        sections = []
        for item in raw.get("sections") or []:
            if isinstance(item, dict):
                sections.append(
                    Section(
                        text=str(item.get("text") or ""),
                        link=str(item.get("link") or ""),
                        heading=str(item.get("heading") or ""),
                    )
                )
        return cls(
            id=str(raw.get("id") or ""),
            source=str(raw.get("source") or "file"),
            title=str(raw.get("title") or ""),
            url=str(raw.get("url") or ""),
            sections=sections,
            metadata=dict(raw.get("metadata") or {}),
            updated_at=raw.get("updated_at"),
            visibility=str(raw.get("visibility") or "public"),
        )


@dataclass
class ConnectorFailure:
    failure_message: str
    failed_id: Optional[str] = None
    missed_time_range: Optional[List[str]] = None

    def to_record(self) -> Dict[str, Any]:
        return {
            "failed_id": self.failed_id,
            "failure_message": self.failure_message,
            "missed_time_range": self.missed_time_range,
        }


@dataclass
class Checkpoint:
    has_more: bool = False
    cursor: Optional[str] = None
    completed_at: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        return {
            "has_more": self.has_more,
            "cursor": self.cursor,
            "completed_at": self.completed_at,
            "extra": dict(self.extra or {}),
        }

    @classmethod
    def from_record(cls, raw: Optional[Dict[str, Any]]) -> "Checkpoint":
        data = raw or {}
        return cls(
            has_more=bool(data.get("has_more")),
            cursor=data.get("cursor"),
            completed_at=data.get("completed_at"),
            extra=dict(data.get("extra") or {}),
        )


YieldItem = Union[ExternalDoc, ConnectorFailure]
