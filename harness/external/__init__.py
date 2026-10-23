"""Local-first external context (X0–X1). Off by default.

Inspired by Onyx's connector *contract* (load / poll / checkpoint / slim /
failures-as-data). The models here are ours. No Onyx source was copied.
"""

from .models import Checkpoint, ConnectorFailure, ExternalDoc, Section
from .store import ExternalStore

__all__ = [
    "Checkpoint",
    "ConnectorFailure",
    "ExternalDoc",
    "ExternalStore",
    "Section",
]
