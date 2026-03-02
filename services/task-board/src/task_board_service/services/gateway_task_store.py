"""Backward-compatibility shim for legacy GatewayTaskStore imports."""

from task_board_service.services.task_db_client import TaskDbClient as GatewayTaskStore

__all__ = ["GatewayTaskStore"]
