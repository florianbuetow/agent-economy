"""Dispute storage and business logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from service_commons.exceptions import ServiceError

from court_service.services.dispute_store import DisputeStore, DuplicateDisputeError
from court_service.services.ruling_orchestrator import RulingOrchestrator

if TYPE_CHECKING:
    from court_service.judges.base import Judge


class TaskBoardRulingClient(Protocol):
    """Protocol for task-board ruling callback."""

    async def record_ruling(self, task_id: str, ruling_payload: dict[str, Any]) -> None:
        """Record a dispute ruling for a task."""
        ...


class CentralBankSplitClient(Protocol):
    """Protocol for central-bank escrow split calls."""

    async def split_escrow(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_pct: int,
    ) -> dict[str, Any]:
        """Split escrow into worker/poster portions."""
        ...


class ReputationFeedbackClient(Protocol):
    """Protocol for reputation feedback submission."""

    async def record_feedback(self, feedback_payload: dict[str, Any]) -> dict[str, Any]:
        """Submit a feedback payload."""
        ...


class DisputeService:
    """Manage dispute lifecycle and ruling orchestration."""

    def __init__(
        self,
        store: DisputeStore,
        *args: object,
        **kwargs: object,
    ) -> None:
        orchestrator_arg: object | None = None
        if len(args) > 1:
            msg = "DisputeService accepts at most one positional argument after store"
            raise TypeError(msg)
        if len(args) == 1:
            orchestrator_arg = args[0]

        if "orchestrator" in kwargs:
            if len(args) == 1:
                msg = "orchestrator provided both positionally and by keyword"
                raise TypeError(msg)
            orchestrator_arg = kwargs.pop("orchestrator")

        if len(kwargs) > 0:
            unknown = ", ".join(sorted(kwargs))
            msg = f"Unexpected keyword argument(s): {unknown}"
            raise TypeError(msg)

        self._store = store
        if orchestrator_arg is None:
            self._orchestrator = RulingOrchestrator(store)
        elif isinstance(orchestrator_arg, RulingOrchestrator):
            self._orchestrator = orchestrator_arg
        else:
            msg = "orchestrator must be a RulingOrchestrator or None"
            raise TypeError(msg)

    def file_dispute(
        self,
        task_id: str,
        claimant_id: str,
        respondent_id: str,
        claim: str,
        escrow_id: str,
        rebuttal_deadline_seconds: int,
    ) -> dict[str, Any]:
        """Create a new dispute in rebuttal_pending status."""
        rebuttal_deadline = (
            datetime.now(UTC) + timedelta(seconds=rebuttal_deadline_seconds)
        ).isoformat()

        try:
            return self._store.insert_dispute(
                task_id=task_id,
                claimant_id=claimant_id,
                respondent_id=respondent_id,
                claim=claim,
                escrow_id=escrow_id,
                rebuttal_deadline=rebuttal_deadline,
            )
        except DuplicateDisputeError as exc:
            raise ServiceError(
                "DISPUTE_ALREADY_EXISTS",
                "A dispute already exists for this task",
                409,
                {},
            ) from exc

    def submit_rebuttal(self, dispute_id: str, rebuttal: str) -> dict[str, Any]:
        """Submit rebuttal for a dispute."""
        dispute = self._store.get_dispute(dispute_id)
        if dispute is None:
            raise ServiceError("DISPUTE_NOT_FOUND", "Dispute not found", 404, {})

        status = str(dispute["status"])
        if status != "rebuttal_pending":
            raise ServiceError(
                "INVALID_DISPUTE_STATUS",
                "Dispute is not in rebuttal_pending status",
                409,
                {},
            )

        if dispute["rebuttal"] is not None:
            raise ServiceError(
                "REBUTTAL_ALREADY_SUBMITTED",
                "Rebuttal has already been submitted",
                409,
                {},
            )

        self._store.update_rebuttal(dispute_id, rebuttal)

        updated_dispute = self.get_dispute(dispute_id)
        if updated_dispute is None:
            msg = "Failed to load dispute after rebuttal update"
            raise RuntimeError(msg)
        return updated_dispute

    async def execute_ruling(
        self,
        dispute_id: str,
        judges: list[Judge],
        task_data: dict[str, Any],
        task_board_client: TaskBoardRulingClient | None,
        central_bank_client: CentralBankSplitClient | None,
        reputation_client: ReputationFeedbackClient | None,
        platform_agent_id: str,
    ) -> dict[str, Any]:
        """Evaluate dispute via judges and commit ruled outcome with side-effects."""
        return await self._orchestrator.execute_ruling(
            dispute_id=dispute_id,
            judges=judges,
            task_data=task_data,
            task_board_client=task_board_client,
            central_bank_client=central_bank_client,
            reputation_client=reputation_client,
            platform_agent_id=platform_agent_id,
        )

    def get_dispute(self, dispute_id: str) -> dict[str, Any] | None:
        """Return dispute details with votes, or None."""
        return self._store.get_dispute(dispute_id)

    def list_disputes(self, task_id: str | None, status: str | None) -> list[dict[str, Any]]:
        """List disputes with optional AND filters."""
        return self._store.list_disputes(task_id, status)

    def count_disputes(self) -> int:
        """Count all disputes."""
        return self._store.count_disputes()

    def count_active(self) -> int:
        """Count disputes not yet ruled."""
        return self._store.count_active()

    def close(self) -> None:
        """Close database connection."""
        self._store.close()
