"""Task lifecycle management — all business logic lives here."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger
from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.task_store import DuplicateBidError, DuplicateTaskError, TaskStore
from task_board_service.services.token_validator import decode_base64url_json

if TYPE_CHECKING:
    from task_board_service.clients.central_bank_client import CentralBankClient
    from task_board_service.clients.identity_client import IdentityClient
    from task_board_service.clients.platform_signer import PlatformSigner
    from task_board_service.services.asset_manager import AssetManager
    from task_board_service.services.escrow_coordinator import EscrowCoordinator
    from task_board_service.services.token_validator import TokenValidator

# Regex for task_id format: t-<uuid4>
_TASK_ID_RE = re.compile(
    r"^t-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Valid task statuses
_VALID_STATUSES = frozenset(
    {"open", "accepted", "submitted", "approved", "cancelled", "disputed", "ruled", "expired"}
)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_positive_int(value: object) -> bool:
    """Check if value is a positive integer (not float, not bool)."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_valid_worker_pct(value: object) -> bool:
    """Check if value is an integer 0-100 (not float, not bool)."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100


class TaskManager:
    """
    Manages the full task lifecycle: creation, bidding, acceptance,
    execution, submission, review, dispute, and ruling.

    Delegates persistence to TaskStore, authentication to the Identity
    service via IdentityClient, and escrow operations via CentralBankClient.
    """

    def __init__(
        self,
        store: TaskStore,
        identity_client: IdentityClient,
        central_bank_client: CentralBankClient,
        escrow_coordinator: EscrowCoordinator,
        token_validator: TokenValidator,
        deadline_evaluator: DeadlineEvaluator,
        asset_manager: AssetManager,
        platform_signer: PlatformSigner,
        platform_agent_id: str,
    ) -> None:
        self._store = store
        self._identity_client = identity_client
        self._central_bank_client = central_bank_client
        self._escrow_coordinator = escrow_coordinator
        self._token_validator = token_validator
        self._deadline_evaluator = deadline_evaluator
        self._asset_manager = asset_manager
        self._platform_signer = platform_signer
        self._platform_agent_id = platform_agent_id
        self._logger = get_logger(__name__)

    # ------------------------------------------------------------------
    # Private helper methods
    # ------------------------------------------------------------------

    def _task_to_response(self, row: dict[str, Any]) -> dict[str, Any]:
        """Convert a DB row dict to a full task response dict."""
        bidding_deadline = DeadlineEvaluator.compute_deadline(
            row["created_at"], row["bidding_deadline_seconds"]
        )
        execution_deadline = DeadlineEvaluator.compute_deadline(
            row["accepted_at"], row["deadline_seconds"]
        )
        review_deadline = DeadlineEvaluator.compute_deadline(
            row["submitted_at"], row["review_deadline_seconds"]
        )
        return {
            "task_id": row["task_id"],
            "poster_id": row["poster_id"],
            "title": row["title"],
            "spec": row["spec"],
            "reward": row["reward"],
            "bidding_deadline_seconds": row["bidding_deadline_seconds"],
            "deadline_seconds": row["deadline_seconds"],
            "review_deadline_seconds": row["review_deadline_seconds"],
            "status": row["status"],
            "escrow_id": row["escrow_id"],
            "bid_count": row["bid_count"],
            "worker_id": row["worker_id"],
            "accepted_bid_id": row["accepted_bid_id"],
            "created_at": row["created_at"],
            "accepted_at": row["accepted_at"],
            "submitted_at": row["submitted_at"],
            "approved_at": row["approved_at"],
            "cancelled_at": row["cancelled_at"],
            "disputed_at": row["disputed_at"],
            "dispute_reason": row["dispute_reason"],
            "ruling_id": row["ruling_id"],
            "ruled_at": row["ruled_at"],
            "worker_pct": row["worker_pct"],
            "ruling_summary": row["ruling_summary"],
            "expired_at": row["expired_at"],
            "escrow_pending": bool(row["escrow_pending"]),
            "bidding_deadline": bidding_deadline,
            "execution_deadline": execution_deadline,
            "review_deadline": review_deadline,
        }

    def _task_to_summary(self, row: dict[str, Any]) -> dict[str, Any]:
        """Convert a DB row dict to a summary dict for list views."""
        bidding_deadline = DeadlineEvaluator.compute_deadline(
            row["created_at"], row["bidding_deadline_seconds"]
        )
        execution_deadline = DeadlineEvaluator.compute_deadline(
            row["accepted_at"], row["deadline_seconds"]
        )
        review_deadline = DeadlineEvaluator.compute_deadline(
            row["submitted_at"], row["review_deadline_seconds"]
        )
        return {
            "task_id": row["task_id"],
            "poster_id": row["poster_id"],
            "title": row["title"],
            "reward": row["reward"],
            "status": row["status"],
            "bid_count": row["bid_count"],
            "worker_id": row["worker_id"],
            "created_at": row["created_at"],
            "bidding_deadline": bidding_deadline,
            "execution_deadline": execution_deadline,
            "review_deadline": review_deadline,
        }

    # ------------------------------------------------------------------
    # Public methods — called by routers
    # ------------------------------------------------------------------

    async def create_task(self, task_token: str, escrow_token: str) -> dict[str, Any]:
        """
        Create a new task with escrow.

        Error precedence:
        1. INVALID_JWS — malformed task_token (via _validate_jws_token)
        2. IDENTITY_SERVICE_UNAVAILABLE — Identity unreachable
        3. FORBIDDEN — invalid signature or signer mismatch (payload-level)
        4. INVALID_PAYLOAD — wrong action, missing fields, invalid values
        5. TOKEN_MISMATCH — cross-token validation
        6. TASK_ALREADY_EXISTS — duplicate task_id
        7. CENTRAL_BANK_UNAVAILABLE / INSUFFICIENT_FUNDS — escrow lock
        """
        # Steps 4-7a: Verify task_token via Identity service, validate action
        payload = await self._token_validator.validate_jws_token(task_token, "create_task")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields in task_token payload
        required_fields = [
            "task_id",
            "poster_id",
            "title",
            "spec",
            "reward",
            "bidding_deadline_seconds",
            "review_deadline_seconds",
        ]
        for field_name in required_fields:
            if field_name not in payload:
                raise ServiceError(
                    "INVALID_PAYLOAD",
                    f"Missing required field: {field_name}",
                    400,
                    {},
                )

        if "deadline_seconds" in payload:
            deadline_seconds_field = "deadline_seconds"
        elif "execution_deadline_seconds" in payload:
            deadline_seconds_field = "execution_deadline_seconds"
        else:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "Missing required field: execution_deadline_seconds",
                400,
                {},
            )

        # Step 9a: Signer must match poster_id in payload (payload-level check)
        poster_id: str = payload["poster_id"]
        if signer_id != poster_id:
            raise ServiceError(
                "FORBIDDEN",
                "Signer does not match poster_id",
                403,
                {},
            )

        task_id_obj: object = payload["task_id"]
        title_obj: object = payload["title"]
        spec_obj: object = payload["spec"]
        reward: object = payload["reward"]
        bidding_deadline_seconds: object = payload["bidding_deadline_seconds"]
        deadline_seconds: object = payload[deadline_seconds_field]
        review_deadline_seconds: object = payload["review_deadline_seconds"]

        # Step 7c: Validate task_id format
        if not isinstance(task_id_obj, str) or not _TASK_ID_RE.match(task_id_obj):
            raise ServiceError(
                "INVALID_TASK_ID",
                "task_id must match the format t-<uuid4>",
                400,
                {},
            )
        task_id = task_id_obj

        # Step 7d: Validate title
        if not isinstance(title_obj, str) or len(title_obj) < 1:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "Title must be a non-empty string",
                400,
                {},
            )
        if len(title_obj) > 200:
            raise ServiceError(
                "TITLE_TOO_LONG",
                "Title must not exceed 200 characters",
                400,
                {},
            )
        title = title_obj

        # Step 7e: Validate spec
        if not isinstance(spec_obj, str) or len(spec_obj) < 1 or len(spec_obj) > 10000:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "Spec must be between 1 and 10,000 characters",
                400,
                {},
            )
        spec = spec_obj

        # Step 7f: Validate reward (must be positive integer, not float, not bool)
        if not _is_positive_int(reward):
            raise ServiceError(
                "INVALID_REWARD",
                "Reward must be a positive integer",
                400,
                {},
            )

        # Step 7g: Validate deadlines (each must be a positive integer)
        for dl_name, dl_value in [
            ("bidding_deadline_seconds", bidding_deadline_seconds),
            ("deadline_seconds", deadline_seconds),
            ("review_deadline_seconds", review_deadline_seconds),
        ]:
            if not _is_positive_int(dl_value):
                raise ServiceError(
                    "INVALID_DEADLINE",
                    f"{dl_name} must be a positive integer",
                    400,
                    {},
                )

        reward_int = cast("int", reward)
        bidding_deadline_seconds_int = cast("int", bidding_deadline_seconds)
        deadline_seconds_int = cast("int", deadline_seconds)
        review_deadline_seconds_int = cast("int", review_deadline_seconds)

        # Step 8: Cross-validate escrow_token payload (decoded without sig verification)
        escrow_payload = self._token_validator.decode_escrow_token_payload(escrow_token)
        escrow_header = decode_base64url_json(escrow_token.split(".", maxsplit=1)[0], "header")

        escrow_task_id = escrow_payload.get("task_id")
        escrow_amount = escrow_payload.get("amount")

        # Missing fields in escrow payload means cross-validation cannot proceed
        if escrow_task_id is None or escrow_amount is None:
            raise ServiceError(
                "TOKEN_MISMATCH",
                "Escrow token payload must include task_id and amount",
                400,
                {},
            )

        if escrow_task_id != task_id:
            raise ServiceError(
                "TOKEN_MISMATCH",
                "task_id mismatch between task_token and escrow_token",
                400,
                {},
            )

        if escrow_amount != reward_int:
            raise ServiceError(
                "TOKEN_MISMATCH",
                "reward/amount mismatch between task_token and escrow_token",
                400,
                {},
            )

        escrow_signer_id = escrow_header.get("kid")
        if not isinstance(escrow_signer_id, str) or escrow_signer_id != signer_id:
            raise ServiceError(
                "TOKEN_MISMATCH",
                "escrow signer does not match task signer",
                400,
                {},
            )

        escrow_agent_id = escrow_payload.get("agent_id")
        if isinstance(escrow_agent_id, str) and escrow_agent_id != poster_id:
            raise ServiceError(
                "TOKEN_MISMATCH",
                "escrow signer agent_id does not match poster_id",
                400,
                {},
            )

        # Step 10 (variant): Check task_id not already in DB
        existing = self._store.get_task(task_id)
        if existing is not None:
            raise ServiceError(
                "TASK_ALREADY_EXISTS",
                f"A task with task_id '{task_id}' already exists",
                409,
                {},
            )

        # Step 13: Lock escrow via Central Bank (forwards the poster's escrow_token)
        # CentralBankClient.lock_escrow raises:
        #   ServiceError("CENTRAL_BANK_UNAVAILABLE", ..., 502) on connection/timeout
        #   ServiceError("INSUFFICIENT_FUNDS", ..., 402) when CB reports insufficient funds
        try:
            escrow_result = await self._central_bank_client.escrow_lock(escrow_token)
        except ServiceError:
            raise
        except Exception as exc:
            error_text = str(exc).upper()
            if "INSUFFICIENT_FUNDS" in error_text:
                raise ServiceError(
                    "INSUFFICIENT_FUNDS",
                    "Poster has insufficient funds to cover the task reward",
                    402,
                    {},
                ) from exc
            raise ServiceError(
                "CENTRAL_BANK_UNAVAILABLE",
                "Cannot connect to Central Bank",
                502,
                {},
            ) from exc
        escrow_id: str = escrow_result["escrow_id"]

        # Insert task into DB
        created_at = _now_iso()
        try:
            self._store.insert_task(
                {
                    "task_id": task_id,
                    "poster_id": poster_id,
                    "title": title,
                    "spec": spec,
                    "reward": reward_int,
                    "bidding_deadline_seconds": bidding_deadline_seconds_int,
                    "deadline_seconds": deadline_seconds_int,
                    "review_deadline_seconds": review_deadline_seconds_int,
                    "status": "open",
                    "escrow_id": escrow_id,
                    "bid_count": 0,
                    "worker_id": None,
                    "accepted_bid_id": None,
                    "created_at": created_at,
                    "accepted_at": None,
                    "submitted_at": None,
                    "approved_at": None,
                    "cancelled_at": None,
                    "disputed_at": None,
                    "dispute_reason": None,
                    "ruling_id": None,
                    "ruled_at": None,
                    "worker_pct": None,
                    "ruling_summary": None,
                    "expired_at": None,
                    "escrow_pending": 0,
                }
            )
        except DuplicateTaskError as exc:
            # DB insert failed (e.g., race condition on duplicate task_id)
            # Rollback escrow: release back to poster
            try:
                await self._escrow_coordinator.release_escrow(escrow_id, poster_id)
            except ServiceError:
                self._logger.error(
                    "Failed to release escrow during rollback",
                    extra={"task_id": task_id, "escrow_id": escrow_id},
                )
            raise ServiceError(
                "TASK_ALREADY_EXISTS",
                f"A task with task_id '{task_id}' already exists",
                409,
                {},
            ) from exc

        task = self._store.get_task(task_id)
        if task is None:
            msg = f"Task {task_id} not found after insert"
            raise RuntimeError(msg)
        return self._task_to_response(task)

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """
        Get a single task by ID with deadline evaluation.

        Raises:
            ServiceError: TASK_NOT_FOUND
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})
        task = await self._deadline_evaluator.evaluate_deadline(task)
        return self._task_to_response(task)

    async def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
        offset: int | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        """
        List tasks with optional filters. All filters use AND logic.

        Returns a list of task summary dicts.
        """
        tasks = self._store.list_tasks(
            status=status,
            poster_id=poster_id,
            worker_id=worker_id,
            limit=limit,
            offset=offset,
        )

        # Evaluate deadlines for all tasks
        tasks = await self._deadline_evaluator.evaluate_deadlines_batch(tasks)

        return [self._task_to_summary(t) for t in tasks]

    async def cancel_task(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Cancel a task and release escrow to the poster.

        Error precedence:
        1-6. JWS verification (via _validate_jws_token)
        7.   INVALID_PAYLOAD — wrong action, missing fields, task_id mismatch
        9a.  FORBIDDEN — signer != poster_id in payload
        10.  TASK_NOT_FOUND
        11.  INVALID_STATUS — not OPEN
        9b.  FORBIDDEN — signer != task's poster
        13.  CENTRAL_BANK_UNAVAILABLE
        """
        # Steps 4-7a: Verify JWS, validate action
        payload = await self._token_validator.validate_jws_token(token, "cancel_task")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate task_id in payload
        if "task_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: task_id", 400, {})

        if "poster_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: poster_id", 400, {})

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        # Step 9a: Signer must match poster_id in payload
        if signer_id != payload["poster_id"]:
            raise ServiceError("FORBIDDEN", "Signer does not match poster_id", 403, {})

        # Step 10: Load task
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        # Evaluate deadline first (task may have expired)
        task = await self._deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "open":
            raise ServiceError(
                "INVALID_STATUS",
                f"Cannot cancel task in '{task['status']}' status, must be 'open'",
                409,
                {},
            )

        # Step 9b: Signer must be the task's poster
        if signer_id != task["poster_id"]:
            raise ServiceError("FORBIDDEN", "Only the poster can cancel this task", 403, {})

        # Step 13: Release escrow to poster
        await self._escrow_coordinator.release_escrow(task["escrow_id"], task["poster_id"])

        # Update task status
        cancelled_at = _now_iso()
        self._store.update_task(
            task_id,
            {"status": "cancelled", "cancelled_at": cancelled_at},
            expected_status=None,
        )

        updated = self._store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        return self._task_to_response(updated)

    async def submit_bid(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Submit a bid on a task.

        Error precedence:
        1-6. JWS verification
        7.   INVALID_PAYLOAD — wrong action, missing fields, task_id mismatch
        9a.  FORBIDDEN — signer != bidder_id in payload
        10.  TASK_NOT_FOUND
        11.  INVALID_STATUS — not OPEN
        12a. SELF_BID — bidder is the poster
        12b. BID_ALREADY_EXISTS — duplicate bid
        """
        # Steps 4-7a: Verify JWS, validate action
        payload = await self._token_validator.validate_jws_token(token, "submit_bid")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        if "task_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: task_id", 400, {})

        if "bidder_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: bidder_id", 400, {})

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        # Step 7d: Validate amount
        if "amount" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: amount", 400, {})

        amount: object = payload["amount"]
        if not _is_positive_int(amount):
            raise ServiceError("INVALID_REWARD", "Bid amount must be a positive integer", 400, {})
        amount_int = cast("int", amount)

        # Step 9a: Signer must match bidder_id in payload
        bidder_id: str = payload["bidder_id"]
        if signer_id != bidder_id:
            raise ServiceError("FORBIDDEN", "Signer does not match bidder_id", 403, {})

        # Step 10: Load task
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        # Evaluate deadline first
        task = await self._deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "open":
            raise ServiceError(
                "INVALID_STATUS",
                f"Cannot bid on task in '{task['status']}' status, must be 'open'",
                409,
                {},
            )

        bidding_deadline = DeadlineEvaluator.compute_deadline(
            task["created_at"],
            task["bidding_deadline_seconds"],
        )
        if bidding_deadline is not None:
            deadline_dt = datetime.fromisoformat(bidding_deadline.replace("Z", "+00:00"))
            if datetime.now(UTC) >= deadline_dt:
                raise ServiceError(
                    "INVALID_STATUS",
                    "Bidding deadline has passed",
                    409,
                    {},
                )

        # Step 12a: Bidder must not be the poster (SELF_BID, not FORBIDDEN)
        if bidder_id == task["poster_id"]:
            raise ServiceError("SELF_BID", "Cannot bid on your own task", 400, {})

        # Step 12b: Check for duplicate bid
        bid_id = f"bid-{uuid.uuid4()}"
        submitted_at = _now_iso()

        try:
            self._store.insert_bid(
                {
                    "bid_id": bid_id,
                    "task_id": task_id,
                    "bidder_id": bidder_id,
                    "amount": amount_int,
                    "submitted_at": submitted_at,
                }
            )
        except DuplicateBidError as exc:
            raise ServiceError(
                "BID_ALREADY_EXISTS",
                "This agent already bid on this task",
                409,
                {},
            ) from exc

        return {
            "bid_id": bid_id,
            "task_id": task_id,
            "bidder_id": bidder_id,
            "amount": amount_int,
            "submitted_at": submitted_at,
        }

    async def list_bids(self, task_id: str, auth_token: str | None) -> dict[str, Any]:
        """
        List bids for a task. Sealed during OPEN phase (requires poster auth).

        Error precedence during OPEN phase:
        4-6. JWS verification
        7.   INVALID_PAYLOAD — wrong action
        9.   FORBIDDEN — signer is not the poster
        10.  TASK_NOT_FOUND
        """
        # Step 10: Load task first — TASK_NOT_FOUND takes priority over auth
        # for non-OPEN tasks (public access). For OPEN tasks, auth errors
        # come before task lookup in standard precedence, BUT the task_id
        # is in the URL (not the token), so we need the task to determine
        # if auth is required. Load task, then check status, then enforce auth.
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        # Evaluate deadline
        task = await self._deadline_evaluator.evaluate_deadline(task)

        if task["status"] == "open":
            # Sealed bids — require poster authentication
            if not auth_token:
                raise ServiceError(
                    "INVALID_JWS",
                    "Authorization required to list bids during OPEN phase",
                    400,
                    {},
                )

            payload = await self._token_validator.validate_jws_token(auth_token, "list_bids")
            signer_id: str = payload["_signer_id"]

            # Validate task_id in payload matches URL
            if "task_id" in payload and payload["task_id"] != task_id:
                raise ServiceError(
                    "INVALID_PAYLOAD",
                    "task_id in payload does not match URL path",
                    400,
                    {},
                )

            # Validate poster_id in payload
            if "poster_id" in payload and signer_id != payload["poster_id"]:
                raise ServiceError("FORBIDDEN", "Signer does not match poster_id", 403, {})

            # Signer must be the task's poster
            if signer_id != task["poster_id"]:
                raise ServiceError(
                    "FORBIDDEN",
                    "Only the poster can list bids during OPEN phase",
                    403,
                    {},
                )

        # Fetch all bids for this task
        bids = [
            {
                "bid_id": str(row["bid_id"]),
                "bidder_id": str(row["bidder_id"]),
                "amount": int(row["amount"]),
                "submitted_at": str(row["submitted_at"]),
            }
            for row in self._store.get_bids_for_task(task_id)
        ]

        return {"task_id": task_id, "bids": bids}

    async def accept_bid(self, task_id: str, bid_id: str, token: str) -> dict[str, Any]:
        """
        Accept a bid, assigning the worker and starting the execution deadline.

        Error precedence:
        1-6. JWS verification
        7.   INVALID_PAYLOAD — wrong action, missing fields, task_id/bid_id mismatch
        9a.  FORBIDDEN — signer != poster_id in payload
        10.  TASK_NOT_FOUND
        11.  INVALID_STATUS — not OPEN
        9b.  FORBIDDEN — signer != task's poster
        12.  BID_NOT_FOUND
        """
        # Steps 4-7a: Verify JWS, validate action
        payload = await self._token_validator.validate_jws_token(token, "accept_bid")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        for field_name in ["task_id", "bid_id", "poster_id"]:
            if field_name not in payload:
                raise ServiceError(
                    "INVALID_PAYLOAD",
                    f"Missing required field: {field_name}",
                    400,
                    {},
                )

        # Step 7c: task_id and bid_id must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        if payload["bid_id"] != bid_id:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "bid_id in payload does not match URL path",
                400,
                {},
            )

        # Step 9a: Signer must match poster_id in payload
        if signer_id != payload["poster_id"]:
            raise ServiceError("FORBIDDEN", "Signer does not match poster_id", 403, {})

        # Step 10: Load task
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        # Evaluate deadline
        task = await self._deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "open":
            raise ServiceError(
                "INVALID_STATUS",
                f"Cannot accept bid on task in '{task['status']}' status, must be 'open'",
                409,
                {},
            )

        # Step 9b: Signer must be the task's poster
        if signer_id != task["poster_id"]:
            raise ServiceError("FORBIDDEN", "Only the poster can accept bids", 403, {})

        # Step 12: Find the bid
        bid = self._store.get_bid(bid_id, task_id)
        if bid is None:
            raise ServiceError("BID_NOT_FOUND", "Bid not found", 404, {})

        worker_id = str(bid["bidder_id"])
        accepted_at = _now_iso()

        # Update task
        self._store.update_task(
            task_id,
            {
                "status": "accepted",
                "worker_id": worker_id,
                "accepted_bid_id": bid_id,
                "accepted_at": accepted_at,
            },
            expected_status=None,
        )

        updated = self._store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        return self._task_to_response(updated)

    async def submit_deliverable(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Submit deliverables for review.

        Error precedence:
        1-6. JWS verification
        7.   INVALID_PAYLOAD — wrong action, task_id mismatch
        9a.  FORBIDDEN — signer != worker_id in payload
        10.  TASK_NOT_FOUND
        11.  INVALID_STATUS — not ACCEPTED
        9b.  FORBIDDEN — signer != task's worker_id
        12.  NO_ASSETS — no assets uploaded
        """
        # Steps 4-7a: Verify JWS, validate action
        payload = await self._token_validator.validate_jws_token(token, "submit_deliverable")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        if "task_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: task_id", 400, {})

        if "worker_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: worker_id", 400, {})

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        # Step 9a: Signer must match worker_id in payload
        if signer_id != payload["worker_id"]:
            raise ServiceError("FORBIDDEN", "Signer does not match worker_id", 403, {})

        # Step 10: Load task
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        # Evaluate deadline
        task = await self._deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "accepted":
            raise ServiceError(
                "INVALID_STATUS",
                (
                    "Cannot submit deliverable for task in "
                    f"'{task['status']}' status, must be 'accepted'"
                ),
                409,
                {},
            )

        # Step 9b: Signer must be the task's assigned worker
        if signer_id != task["worker_id"]:
            raise ServiceError(
                "FORBIDDEN",
                "Only the assigned worker can submit deliverables",
                403,
                {},
            )

        # Step 12: At least one asset must exist
        asset_count = self._asset_manager.count_assets(task_id)
        if asset_count == 0:
            raise ServiceError(
                "NO_ASSETS",
                "At least one asset must be uploaded before submitting",
                400,
                {},
            )

        # Update task
        submitted_at = _now_iso()
        self._store.update_task(
            task_id,
            {"status": "submitted", "submitted_at": submitted_at},
            expected_status=None,
        )

        updated = self._store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        return self._task_to_response(updated)

    async def approve_task(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Approve deliverables and release escrow to the worker.

        Error precedence:
        1-6. JWS verification
        7.   INVALID_PAYLOAD — wrong action, task_id mismatch
        9a.  FORBIDDEN — signer != poster_id in payload
        10.  TASK_NOT_FOUND
        11.  INVALID_STATUS — not SUBMITTED
        9b.  FORBIDDEN — signer != task's poster
        13.  CENTRAL_BANK_UNAVAILABLE
        """
        # Steps 4-7a: Verify JWS, validate action
        payload = await self._token_validator.validate_jws_token(token, "approve_task")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        if "task_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: task_id", 400, {})

        if "poster_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: poster_id", 400, {})

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        # Step 9a: Signer must match poster_id in payload
        if signer_id != payload["poster_id"]:
            raise ServiceError("FORBIDDEN", "Signer does not match poster_id", 403, {})

        # Step 10: Load task
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        # Evaluate deadline
        task = await self._deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "submitted":
            raise ServiceError(
                "INVALID_STATUS",
                f"Cannot approve task in '{task['status']}' status, must be 'submitted'",
                409,
                {},
            )

        # Step 9b: Signer must be the task's poster
        if signer_id != task["poster_id"]:
            raise ServiceError("FORBIDDEN", "Only the poster can approve", 403, {})

        # Step 13: Release escrow to worker
        await self._escrow_coordinator.release_escrow(task["escrow_id"], task["worker_id"])

        # Update task
        approved_at = _now_iso()
        self._store.update_task(
            task_id,
            {"status": "approved", "approved_at": approved_at},
            expected_status=None,
        )

        updated = self._store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        return self._task_to_response(updated)

    async def dispute_task(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Dispute deliverables — sends task to the Court for resolution.

        Error precedence:
        1-6. JWS verification
        7.   INVALID_PAYLOAD — wrong action, task_id mismatch
        9a.  FORBIDDEN — signer != poster_id in payload
        10.  TASK_NOT_FOUND
        11.  INVALID_STATUS — not SUBMITTED
        9b.  FORBIDDEN — signer != task's poster
        12.  INVALID_REASON — empty or too long
        """
        # Steps 4-7a: Verify JWS, validate action
        payload = await self._token_validator.validate_jws_token(
            token,
            ("dispute_task", "file_dispute"),
        )
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        if "task_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: task_id", 400, {})

        if "poster_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: poster_id", 400, {})

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        # Step 9a: Signer must match poster_id in payload
        if signer_id != payload["poster_id"]:
            raise ServiceError("FORBIDDEN", "Signer does not match poster_id", 403, {})

        # Step 10: Load task
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        # Evaluate deadline
        task = await self._deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "submitted":
            raise ServiceError(
                "INVALID_STATUS",
                f"Cannot dispute task in '{task['status']}' status, must be 'submitted'",
                409,
                {},
            )

        # Step 9b: Signer must be the task's poster
        if signer_id != task["poster_id"]:
            raise ServiceError("FORBIDDEN", "Only the poster can dispute", 403, {})

        # Step 12: Validate reason
        reason = payload.get("reason")
        if not isinstance(reason, str) or len(reason) < 1:
            raise ServiceError(
                "INVALID_REASON",
                "Dispute reason must be a non-empty string",
                400,
                {},
            )

        if len(reason) > 10000:
            raise ServiceError(
                "INVALID_REASON",
                "Dispute reason must not exceed 10,000 characters",
                400,
                {},
            )

        # Update task
        disputed_at = _now_iso()
        self._store.update_task(
            task_id,
            {"status": "disputed", "disputed_at": disputed_at, "dispute_reason": reason},
            expected_status=None,
        )

        updated = self._store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        return self._task_to_response(updated)

    async def record_ruling(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Record a Court ruling. Platform-signed operation.

        Error precedence:
        1-6. JWS verification
        7.   INVALID_PAYLOAD — wrong action, task_id mismatch, missing fields
        9a.  FORBIDDEN — signer is not the platform agent
        10.  TASK_NOT_FOUND
        11.  INVALID_STATUS — not DISPUTED
        12.  INVALID_WORKER_PCT
        """
        # Steps 4-7a: Verify JWS, validate action
        payload = await self._token_validator.validate_jws_token(
            token,
            ("record_ruling", "submit_ruling"),
        )
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        if "task_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: task_id", 400, {})

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        action = payload["action"]
        if action == "record_ruling":
            # Step 7d: Validate ruling_id present and non-empty
            if "ruling_id" not in payload:
                raise ServiceError("INVALID_PAYLOAD", "Missing required field: ruling_id", 400, {})

            ruling_id = payload["ruling_id"]
            if not isinstance(ruling_id, str) or len(ruling_id) < 1:
                raise ServiceError(
                    "INVALID_PAYLOAD",
                    "ruling_id must be a non-empty string",
                    400,
                    {},
                )
        else:
            payload_ruling_id = payload.get("ruling_id")
            if payload_ruling_id is None:
                ruling_id = f"rul-{uuid.uuid4()}"
            elif isinstance(payload_ruling_id, str) and len(payload_ruling_id) > 0:
                ruling_id = payload_ruling_id
            else:
                raise ServiceError(
                    "INVALID_PAYLOAD",
                    "ruling_id must be a non-empty string",
                    400,
                    {},
                )

        # Step 7e: Validate ruling_summary present and non-empty
        if "ruling_summary" not in payload:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "Missing required field: ruling_summary",
                400,
                {},
            )

        ruling_summary = payload["ruling_summary"]
        if not isinstance(ruling_summary, str) or len(ruling_summary) < 1:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "ruling_summary must be a non-empty string",
                400,
                {},
            )

        # Step 7f: Validate worker_pct present
        if "worker_pct" not in payload:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "Missing required field: worker_pct",
                400,
                {},
            )

        worker_pct = payload["worker_pct"]

        # Step 9a: Signer must be the platform agent
        if signer_id != self._platform_agent_id:
            raise ServiceError(
                "FORBIDDEN",
                "Only the platform agent can record rulings",
                403,
                {},
            )

        # Step 10: Load task
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        # Step 11: Check status
        if task["status"] != "disputed":
            raise ServiceError(
                "INVALID_STATUS",
                f"Cannot record ruling for task in '{task['status']}' status, must be 'disputed'",
                409,
                {},
            )

        # Step 12: Validate worker_pct value (after status check)
        if not _is_valid_worker_pct(worker_pct):
            raise ServiceError(
                "INVALID_WORKER_PCT",
                "worker_pct must be an integer between 0 and 100",
                400,
                {},
            )

        worker_pct_int = int(worker_pct)

        # Escrow distribution based on ruling:
        # - 0% => full refund to poster
        # - 100% => full payout to worker
        # - otherwise => split between both
        if worker_pct_int == 0:
            await self._escrow_coordinator.release_escrow(task["escrow_id"], task["poster_id"])
        elif worker_pct_int == 100:
            await self._escrow_coordinator.release_escrow(task["escrow_id"], task["worker_id"])
        else:
            await self._escrow_coordinator.split_escrow(
                escrow_id=task["escrow_id"],
                worker_id=task["worker_id"],
                poster_id=task["poster_id"],
                worker_pct=worker_pct_int,
            )

        # Update task
        ruled_at = _now_iso()
        self._store.update_task(
            task_id,
            {
                "status": "ruled",
                "ruled_at": ruled_at,
                "ruling_id": ruling_id,
                "worker_pct": worker_pct_int,
                "ruling_summary": ruling_summary,
            },
            expected_status=None,
        )

        updated = self._store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        return self._task_to_response(updated)

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
        counts: dict[str, int] = dict.fromkeys(_VALID_STATUSES, 0)
        for status_val, count in self._store.count_tasks_by_status().items():
            if status_val in counts:
                counts[status_val] = int(count)
        return counts

    def close(self) -> None:
        """Close the database connection."""
        self._store.close()
