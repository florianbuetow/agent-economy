"""Dispute storage and business logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from court_service.services.errors import DuplicateDisputeError
from court_service.services.ruling_orchestrator import RulingClaim, RulingOrchestrator

if TYPE_CHECKING:
    from service_auth.platform import PlatformAgent

    from court_service.judges.base import Judge
    from court_service.services.protocol import DisputeStorageInterface
    from court_service.services.ruling_orchestrator import TaskLoader


class DisputeService:
    """Manage dispute lifecycle and ruling orchestration."""

    def __init__(
        self,
        store: DisputeStorageInterface,
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

        feedback_extremely_satisfied_cutoff = kwargs.pop(
            "feedback_extremely_satisfied_cutoff", None
        )
        feedback_satisfied_cutoff = kwargs.pop("feedback_satisfied_cutoff", None)
        feedback_comment_max_length = kwargs.pop("feedback_comment_max_length", None)
        judging_lease_seconds = kwargs.pop("judging_lease_seconds", None)

        if len(kwargs) > 0:
            unknown = ", ".join(sorted(kwargs))
            msg = f"Unexpected keyword argument(s): {unknown}"
            raise TypeError(msg)

        self._store = store
        if orchestrator_arg is None:
            if (
                not isinstance(feedback_extremely_satisfied_cutoff, int)
                or not isinstance(feedback_satisfied_cutoff, int)
                or not isinstance(feedback_comment_max_length, int)
                or not isinstance(judging_lease_seconds, int)
            ):
                msg = (
                    "feedback_extremely_satisfied_cutoff, feedback_satisfied_cutoff, and "
                    "feedback_comment_max_length and judging_lease_seconds are required when "
                    "orchestrator is not provided"
                )
                raise TypeError(msg)
            self._orchestrator = RulingOrchestrator(
                store,
                feedback_extremely_satisfied_cutoff=feedback_extremely_satisfied_cutoff,
                feedback_satisfied_cutoff=feedback_satisfied_cutoff,
                feedback_comment_max_length=feedback_comment_max_length,
                judging_lease_seconds=judging_lease_seconds,
            )
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
                "dispute_already_exists",
                "A dispute already exists for this task",
                409,
                {},
            ) from exc

    def submit_rebuttal(self, dispute_id: str, rebuttal: str) -> dict[str, Any]:
        """Submit a rebuttal once; an identical replay returns the stored record.

        A changed rebuttal is a 409 conflict, and a first rebuttal after the
        rebuttal deadline is rejected (plan item 3.3).
        """
        dispute = self._store.get_dispute(dispute_id)
        if dispute is None:
            raise ServiceError("dispute_not_found", "Dispute not found", 404, {})

        existing = dispute["rebuttal"]
        if existing is not None and existing == rebuttal:
            return dispute

        status = str(dispute["status"])
        if status != "rebuttal_pending":
            raise ServiceError(
                "invalid_dispute_status",
                "Dispute is not in rebuttal_pending status",
                409,
                {},
            )

        if existing is not None:
            raise ServiceError(
                "rebuttal_already_submitted",
                "Rebuttal has already been submitted",
                409,
                {},
            )

        if self._rebuttal_deadline_passed(dispute):
            raise ServiceError(
                "rebuttal_deadline_passed",
                "The rebuttal deadline has passed",
                409,
                {},
            )

        self._store.update_rebuttal(dispute_id, rebuttal)

        updated_dispute = self.get_dispute(dispute_id)
        if updated_dispute is None:
            msg = "Failed to load dispute after rebuttal update"
            raise RuntimeError(msg)
        return updated_dispute

    @staticmethod
    def _rebuttal_deadline_passed(dispute: dict[str, Any]) -> bool:
        deadline_raw = dispute.get("rebuttal_deadline")
        if not isinstance(deadline_raw, str) or deadline_raw == "":
            return False
        deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return datetime.now(UTC) >= deadline

    async def execute_ruling(
        self,
        dispute_id: str,
        judges: list[Judge],
        task_data: dict[str, Any],
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """Evaluate dispute via judges and commit ruled outcome with side-effects."""
        return await self._orchestrator.execute_ruling(
            dispute_id=dispute_id,
            judges=judges,
            task_data=task_data,
            platform_agent=platform_agent,
        )

    def begin_ruling(self, dispute_id: str) -> RulingClaim:
        """Validate preconditions and take the judging lease before any Task Board
        call that could reenter this same dispute's ruling trigger."""
        return self._orchestrator.begin_ruling(dispute_id)

    async def finish_ruling(
        self,
        dispute_id: str,
        claim: RulingClaim,
        judges: list[Judge],
        task_data: dict[str, Any] | None,
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """Judge unless already decided, persist, then deliver the stored decision."""
        return await self._orchestrator.finish_ruling(
            dispute_id=dispute_id,
            claim=claim,
            judges=judges,
            task_data=task_data,
            platform_agent=platform_agent,
        )

    async def rule(
        self,
        dispute_id: str,
        judges: list[Judge],
        platform_agent: PlatformAgent,
        load_task: TaskLoader,
    ) -> dict[str, Any]:
        """Run (or resume) one ruling end to end under a judging lease."""
        return await self._orchestrator.rule(dispute_id, judges, platform_agent, load_task)

    async def reclaim_stalled_rulings(
        self,
        judges: list[Judge],
        platform_agent: PlatformAgent,
        load_task: TaskLoader,
    ) -> list[str]:
        """Resume every ``judging`` dispute whose lease expired or was released."""
        return await self._orchestrator.reclaim_stalled_rulings(judges, platform_agent, load_task)

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
