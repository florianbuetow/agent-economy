"""Backward-compatible TaskStore import shim."""

from task_board_service.services.errors import DuplicateBidError, DuplicateTaskError
from task_board_service.services.in_memory_task_store import InMemoryTaskStore

TaskStore = InMemoryTaskStore

__all__ = [
    "DuplicateBidError",
    "DuplicateTaskError",
    "InMemoryTaskStore",
    "TaskStore",
]
