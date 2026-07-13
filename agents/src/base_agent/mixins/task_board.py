"""Compatibility shim — Task Board mixin moved to ``service_auth`` (WP-02).

``uuid`` is re-exported so ``base_agent.mixins.task_board.uuid`` still resolves
(patched by the frozen agents test suite); it is the same stdlib module the
implementation in ``service_auth.mixins.task_board`` calls, so patching it there
takes effect on the moved code.
"""

import uuid

from service_auth.mixins.task_board import TaskBoardMixin

__all__ = ["TaskBoardMixin", "uuid"]
