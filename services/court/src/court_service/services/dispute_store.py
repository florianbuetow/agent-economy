"""Backward-compatible DisputeStore import shim."""

from court_service.services.errors import DuplicateDisputeError
from court_service.services.in_memory_dispute_store import InMemoryDisputeStore

DisputeStore = InMemoryDisputeStore

__all__ = ["DisputeStore", "DuplicateDisputeError", "InMemoryDisputeStore"]
