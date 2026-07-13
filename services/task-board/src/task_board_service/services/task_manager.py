"""Task lifecycle management facade (WP-11, B16 god-class decomposition).

TaskManager itself no longer holds business logic — it constructs one
coordinator per lifecycle domain (creation, bidding, review, ruling) and
delegates every public method to the matching coordinator, keeping the
public method names/signatures routers depend on byte-stable. Each
coordinator reads its dependencies through this facade at call time (not a
constructor-captured snapshot) so tests that reassign
``task_manager._store``/``._central_bank_client`` post-construction (see
routers/conftest.py) keep working unchanged for every coordinator too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from task_board_service.logging import get_logger
from task_board_service.services.task_bidding import TaskBiddingCoordinator
from task_board_service.services.task_creation import TaskCreationCoordinator
from task_board_service.services.task_helpers import VALID_STATUSES
from task_board_service.services.task_review import TaskReviewCoordinator
from task_board_service.services.task_ruling import TaskRulingCoordinator

if TYPE_CHECKING:
    import logging

    from service_auth import PlatformSigner
    from service_auth.platform import PlatformAgent
    from service_clients.bank import BankClient

    from task_board_service.services.asset_manager import AssetManager
    from task_board_service.services.deadline_evaluator import DeadlineEvaluator
    from task_board_service.services.escrow_coordinator import EscrowCoordinator
    from task_board_service.services.protocol import TaskStorageInterface
    from task_board_service.services.token_validator import TokenValidator


class TaskManager:
    """
    Manages the full task lifecycle: creation, bidding, acceptance,
    execution, submission, review, dispute, and ruling.

    Delegates persistence to TaskStore, authentication to the Identity
    service via IdentityClient, and escrow operations via BankClient.
    """

    def __init__(
        self,
        store: TaskStorageInterface,
        central_bank_client: BankClient,
        escrow_coordinator: EscrowCoordinator,
        token_validator: TokenValidator,
        deadline_evaluator: DeadlineEvaluator,
        asset_manager: AssetManager,
        platform_signer: PlatformSigner,
        platform_agent_id: str,
    ) -> None:
        self._store = store
        self._central_bank_client = central_bank_client
        self._escrow_coordinator = escrow_coordinator
        self._token_validator = token_validator
        self._deadline_evaluator = deadline_evaluator
        self._asset_manager = asset_manager
        self._platform_signer = platform_signer
        self._platform_agent_id = platform_agent_id
        self._logger = get_logger(__name__)

        self._creation = TaskCreationCoordinator(self)
        self._bidding = TaskBiddingCoordinator(self)
        self._review = TaskReviewCoordinator(self)
        self._ruling = TaskRulingCoordinator(self)

    # ------------------------------------------------------------------
    # Public accessors for coordinators (task_creation.py/task_bidding.py/
    # task_review.py/task_ruling.py). Coordinators read these at call time
    # rather than snapshotting them at construction, so tests that reassign
    # e.g. ``task_manager._store`` post-construction (routers/conftest.py)
    # keep working for every coordinator too — these properties always
    # return the current value of the same private attribute.
    # ------------------------------------------------------------------

    @property
    def store(self) -> TaskStorageInterface:
        """The current task storage backend."""
        return self._store

    @property
    def central_bank_client(self) -> BankClient:
        """The current Central Bank escrow HTTP client."""
        return self._central_bank_client

    @property
    def escrow_coordinator(self) -> EscrowCoordinator:
        """The escrow release/split coordinator."""
        return self._escrow_coordinator

    @property
    def token_validator(self) -> TokenValidator:
        """The JWS token validator."""
        return self._token_validator

    @property
    def deadline_evaluator(self) -> DeadlineEvaluator:
        """The deadline evaluator."""
        return self._deadline_evaluator

    @property
    def asset_manager(self) -> AssetManager:
        """The task asset manager."""
        return self._asset_manager

    @property
    def platform_agent_id(self) -> str:
        """The platform agent's id."""
        return self._platform_agent_id

    @property
    def logger(self) -> logging.Logger:
        """The task manager's logger."""
        return self._logger

    # ------------------------------------------------------------------
    # Public methods — called by routers. Each delegates to the coordinator
    # that owns the corresponding lifecycle domain.
    # ------------------------------------------------------------------

    async def create_task(self, task_token: str, escrow_token: str) -> dict[str, Any]:
        """Create a new task with escrow. See TaskCreationCoordinator.create_task."""
        return await self._creation.create_task(task_token, escrow_token)

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """Get a single task by ID with deadline evaluation."""
        return await self._creation.get_task(task_id)

    async def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
        offset: int | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        """List tasks with optional filters. All filters use AND logic."""
        return await self._creation.list_tasks(status, poster_id, worker_id, offset, limit)

    async def cancel_task(self, task_id: str, token: str) -> dict[str, Any]:
        """Cancel a task and release escrow to the poster."""
        return await self._creation.cancel_task(task_id, token)

    async def submit_bid(self, task_id: str, token: str) -> dict[str, Any]:
        """Submit a bid on a task."""
        return await self._bidding.submit_bid(task_id, token)

    async def list_bids(self, task_id: str, auth_token: str | None) -> dict[str, Any]:
        """List bids for a task. Sealed during OPEN phase (requires poster auth)."""
        return await self._bidding.list_bids(task_id, auth_token)

    async def accept_bid(self, task_id: str, bid_id: str, token: str) -> dict[str, Any]:
        """Accept a bid, assigning the worker and starting the execution deadline."""
        return await self._bidding.accept_bid(task_id, bid_id, token)

    async def submit_deliverable(self, task_id: str, token: str) -> dict[str, Any]:
        """Submit deliverables for review."""
        return await self._review.submit_deliverable(task_id, token)

    async def approve_task(self, task_id: str, token: str) -> dict[str, Any]:
        """Approve deliverables and release escrow to the worker."""
        return await self._review.approve_task(task_id, token)

    async def dispute_task(
        self,
        task_id: str,
        token: str,
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """Dispute deliverables — sends task to the Court for resolution."""
        return await self._ruling.dispute_task(task_id, token, platform_agent)

    async def submit_rebuttal(
        self,
        task_id: str,
        token: str,
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """Submit a worker rebuttal via the platform-signed Court path."""
        return await self._ruling.submit_rebuttal(task_id, token, platform_agent)

    async def record_ruling(self, task_id: str, token: str) -> dict[str, Any]:
        """Record a Court ruling. Platform-signed operation."""
        return await self._ruling.record_ruling(task_id, token)

    # ------------------------------------------------------------------
    # Statistics — used by health endpoint
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return aggregate task statistics for health reporting."""
        return {
            "total_tasks": self.count_tasks(),
            "tasks_by_status": self.count_tasks_by_status(),
        }

    def count_tasks(self) -> int:
        """Count total tasks."""
        return self._store.count_tasks()

    def count_tasks_by_status(self) -> dict[str, int]:
        """Count tasks grouped by status. Returns all 8 statuses with 0 defaults."""
        counts: dict[str, int] = dict.fromkeys(VALID_STATUSES, 0)
        for status_val, count in self._store.count_tasks_by_status().items():
            if status_val in counts:
                counts[status_val] = int(count)
        return counts

    def close(self) -> None:
        """Close the database connection."""
        self._store.close()
