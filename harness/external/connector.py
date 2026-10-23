"""Connector protocol: full load, time-window poll, checkpoint, slim prune."""

from __future__ import annotations

from typing import Generator, Iterable, Optional, Protocol, Union

from .models import Checkpoint, ConnectorFailure, ExternalDoc, YieldItem


class Connector(Protocol):
    """Minimal local-first connector. Network connectors come later (X3+)."""

    name: str

    def validate(self) -> None:
        """Fail fast on missing path / credentials. No network by default."""

    def load(
        self, checkpoint: Optional[Checkpoint] = None
    ) -> Generator[YieldItem, None, Checkpoint]:
        """Full (or resumed) load. Return the new checkpoint."""

    def poll(
        self,
        start: Optional[str],
        end: Optional[str],
        checkpoint: Optional[Checkpoint] = None,
    ) -> Generator[YieldItem, None, Checkpoint]:
        """Time-window incremental sync. File connector uses mtime."""

    def slim_ids(self) -> Iterable[str]:
        """IDs only, for pruning deleted docs without a full reload."""
