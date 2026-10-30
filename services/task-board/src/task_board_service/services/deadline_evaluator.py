"""Deadline evaluation and automatic task state transitions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger
from task_board_service.services.task_db_client import PlatformHttpError
from task_board_service.services.task_operations import complete_bank_operation
from task_board_service.task_operation_contract import MAX_INTEGER, invalid_gateway_response

if TYPE_CHECKING:
    from service_auth.platform import PlatformAgent

    from task_board_service.services.escrow_coordinator import EscrowCoordinator
    from task_board_service.services.protocol import TaskStorageInterface

# Terminal statuses — no further transitions
_TERMINAL_STATUSES = frozenset({"approved", "cancelled", "ruled", "expired"})

# Statuses the periodic sweep (Q-5, GAP-A3) visits every interval. This is exactly
# the complement of _TERMINAL_STATUSES: every status a task can still transition
# out of via evaluate_deadline.
_SWEEPABLE_STATUSES: tuple[str, ...] = ("open", "accepted", "submitted", "disputed")


def _now_us() -> int:
    """Return exact epoch microseconds through the module's injectable datetime seam."""
    now = datetime.now(UTC)
    epoch = datetime.fromisoformat("1970-01-01T00:00:00+00:00")
    delta = now - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _required_text(task: dict[str, Any], field: str) -> str:
    value = task.get(field)
    if not isinstance(value, str) or not value:
        raise invalid_gateway_response()
    return value


def _required_integer(task: dict[str, Any], field: str, *, positive: bool) -> int:
    value = task.get(field)
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise invalid_gateway_response()
    return value


class DeadlineEvaluator:
    """Evaluates and applies deadline-driven task transitions."""

    def __init__(
        self,
        store: TaskStorageInterface,
        escrow_coordinator: EscrowCoordinator,
        *,
        platform_agent: PlatformAgent | None,
        deadline_actor_id: str,
    ) -> None:
        if not deadline_actor_id or deadline_actor_id.strip() != deadline_actor_id:
            raise ValueError("deadline_actor_id must be a nonblank canonical string")

        self._store = store
        self._escrow_coordinator = escrow_coordinator
        self._platform_agent = platform_agent
        self._deadline_actor_id = deadline_actor_id

    @staticmethod
    def compute_deadline(base_timestamp: str | None, seconds: int) -> str | None:
        """Compute a deadline by adding seconds to a base ISO timestamp."""
        if base_timestamp is None:
            return None
        base_dt = datetime.fromisoformat(base_timestamp.replace("Z", "+00:00"))
        deadline_dt = base_dt + timedelta(seconds=seconds)
        return deadline_dt.isoformat(timespec="auto").replace("+00:00", "Z")

    async def evaluate_deadline(self, task: dict[str, Any]) -> dict[str, Any]:
        """Claim one eligible automatic transition, then return a fresh projection."""
        if task["status"] in _TERMINAL_STATUSES:
            return task

        if task["status"] == "disputed":
            if task.get("pending_transition") is not None or task.get("legacy_reconciliations"):
                return task
            return await self._maybe_trigger_ruling(task)

        task_id = _required_text(task, "task_id")
        name: str | None = None
        try:
            command = self._automatic_command(task, _now_us())
            if command is None:
                return task
            name, cutoff_us, arguments = command
            lifecycle_version = _required_integer(task, "lifecycle_version", positive=False)
            operation_id = operation_id_for(
                "task-deadline",
                (task_id, name, str(lifecycle_version), str(cutoff_us)),
            )
            envelope = self._store.claim_task_operation(
                {
                    "protocol_version": 1,
                    "operation_id": operation_id,
                    "task_id": task_id,
                    "command": name,
                    "expected_status": str(task["status"]),
                    "expected_lifecycle_version": lifecycle_version,
                    "actor_kind": "system",
                    "actor_id": self._deadline_actor_id,
                    "arguments": arguments,
                }
            )
            await complete_bank_operation(
                self._store,
                self._escrow_coordinator,
                envelope,
            )
        except (PlatformHttpError, ServiceError) as exc:
            if isinstance(exc, ServiceError) and exc.error == "invalid_gateway_response":
                raise
            get_logger(__name__).warning(
                "Automatic task transition did not complete; current state will be re-read",
                extra={"task_id": task_id, "command": name, "error": str(exc)},
            )
        refreshed = self._store.get_task(task_id)
        if refreshed is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})
        return refreshed

    def _automatic_command(
        self,
        task: dict[str, Any],
        now_us: int,
    ) -> tuple[str, int, dict[str, str | int]] | None:
        status = task["status"]
        task_id = _required_text(task, "task_id")
        poster_id = _required_text(task, "poster_id")
        escrow_id = _required_text(task, "escrow_id")
        if status == "open":
            bidding_cutoff = _required_integer(task, "bidding_cutoff_us", positive=False)
            if now_us < bidding_cutoff:
                return None
            has_bid = bool(self._store.get_bids_for_task(task_id))
            if has_bid:
                acceptance_cutoff = _required_integer(task, "acceptance_cutoff_us", positive=False)
                if now_us < acceptance_cutoff:
                    return None
                command = "expire_acceptance"
                cutoff_us = acceptance_cutoff
            else:
                command = "expire_bidding"
                cutoff_us = bidding_cutoff
            reward = _required_integer(task, "reward", positive=True)
            return (
                command,
                cutoff_us,
                {
                    "poster_id": poster_id,
                    "escrow_id": escrow_id,
                    "reward": reward,
                    "source_reference": f"task:{task_id}:expired",
                    "cutoff_us": cutoff_us,
                },
            )
        if status == "accepted":
            cutoff_us = _required_integer(task, "execution_cutoff_us", positive=False)
            if now_us < cutoff_us:
                return None
            worker_id = _required_text(task, "worker_id")
            reward = _required_integer(task, "accepted_reward", positive=True)
            return (
                "expire_execution",
                cutoff_us,
                {
                    "poster_id": poster_id,
                    "worker_id": worker_id,
                    "escrow_id": escrow_id,
                    "reward": reward,
                    "source_reference": f"task:{task_id}:expired",
                    "cutoff_us": cutoff_us,
                },
            )
        if status == "submitted":
            cutoff_us = _required_integer(task, "review_cutoff_us", positive=False)
            if now_us < cutoff_us:
                return None
            worker_id = _required_text(task, "worker_id")
            reward = _required_integer(task, "accepted_reward", positive=True)
            return (
                "auto_approve",
                cutoff_us,
                {
                    "poster_id": poster_id,
                    "worker_id": worker_id,
                    "escrow_id": escrow_id,
                    "reward": reward,
                    "source_reference": f"task:{task_id}:approved",
                    "cutoff_us": cutoff_us,
                },
            )
        return None

    def _rebuttal_window_closed(self, task: dict[str, Any]) -> bool:
        """Report whether ruling may proceed: a rebuttal exists or its deadline passed."""
        if task.get("rebuttal_submitted_at") is not None:
            return True
        deadline = task.get("rebuttal_deadline")
        if not isinstance(deadline, str) or deadline == "":
            return False
        deadline_dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        return datetime.now(UTC) >= deadline_dt

    async def _maybe_trigger_ruling(self, task: dict[str, Any]) -> dict[str, Any]:
        """Fire the Court ruling trigger for a disputed task whose window has closed.

        Idempotent against a concurrent or prior ruling: Court answers a re-trigger with
        ``dispute_already_ruled``/``dispute_not_ready`` (surfaced as an HTTP error), which
        is treated as "nothing to do here". Any failure leaves the task disputed so the
        next lazy evaluation retries — a trigger is never lost — and never breaks the read
        that provoked it.
        """
        if self._platform_agent is None or not self._rebuttal_window_closed(task):
            return task
        dispute_id = task.get("dispute_id")
        if not isinstance(dispute_id, str) or dispute_id == "":
            return task

        try:
            await self._platform_agent.trigger_ruling(dispute_id)
        except (PlatformHttpError, ServiceError) as exc:
            get_logger(__name__).warning(
                "Ruling trigger did not complete; will retry on next evaluation",
                extra={
                    "task_id": str(task["task_id"]),
                    "dispute_id": dispute_id,
                    "error": str(exc),
                },
            )

        refreshed = self._store.get_task(str(task["task_id"]))
        return refreshed if refreshed is not None else task

    async def evaluate_deadlines_batch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Evaluate deadlines for a list of tasks."""
        result: list[dict[str, Any]] = []
        for task in tasks:
            evaluated = await self.evaluate_deadline(task)
            result.append(evaluated)
        return result

    async def run_periodic_sweep(self, interval_seconds: int) -> None:
        """Background loop sweeping non-terminal tasks through deadline evaluation.

        Q-5 (GAP-A3): the only mechanism that guarantees forward progress when
        nothing polls a task via the API — without this, deadline transitions and
        ruling triggers depend entirely on an unrelated read arriving.

        Runs until the enclosing asyncio task is cancelled (service shutdown).
        Each task is evaluated one at a time via ``evaluate_deadline``, which does
        real ``await``s (escrow retry, possible Court calls) — control is yielded
        to the event loop between every task, so a large sweep never blocks other
        requests. A single task's failure is logged and does not abort the sweep
        or the loop; the next interval retries it. A gateway outage while listing
        tasks (transport or service error) is logged and retried next interval.

        Concurrent sweep/read evaluators submit the same deterministic operation identity.
        Gateway owns status/version/cutoff predicates and exact replay. Typed claim or
        delivery failures are contained per task; unexpected programming errors propagate.
        """
        logger = get_logger(__name__)
        while True:
            await asyncio.sleep(interval_seconds)
            for status in _SWEEPABLE_STATUSES:
                try:
                    tasks = self._store.list_tasks(
                        status=status,
                        poster_id=None,
                        worker_id=None,
                        limit=None,
                        offset=None,
                    )
                except (PlatformHttpError, ServiceError):
                    logger.exception(
                        "Deadline sweep could not list tasks; will retry next interval",
                        extra={"status": status},
                    )
                    continue
                for task in tasks:
                    try:
                        await self.evaluate_deadline(task)
                    except (PlatformHttpError, ServiceError, RuntimeError):
                        logger.exception(
                            "Deadline sweep failed for task; will retry next interval",
                            extra={"task_id": str(task.get("task_id")), "status": status},
                        )
