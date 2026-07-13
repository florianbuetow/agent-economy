"""Bidding lifecycle: submit, list, accept (WP-11, B16 god-class decomposition).

Extracted from task_manager.py. See task_creation.py's module docstring for
why coordinators read dependencies through ``self._manager`` rather than a
constructor-time snapshot.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from service_commons.exceptions import ServiceError

from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.errors import DuplicateBidError
from task_board_service.services.task_helpers import is_positive_int, now_iso, task_to_response

if TYPE_CHECKING:
    from task_board_service.services.task_manager import TaskManager


class TaskBiddingCoordinator:
    """Handles bid submission, listing, and acceptance."""

    def __init__(self, manager: TaskManager) -> None:
        self._manager = manager

    async def submit_bid(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Submit a bid on a task.

        Error precedence:
        1-6. JWS verification
        7.   invalid_payload — wrong action, missing fields, task_id mismatch
        9a.  forbidden — signer != bidder_id in payload
        10.  task_not_found
        11.  invalid_status — not OPEN
        12a. self_bid — bidder is the poster
        12b. bid_already_exists — duplicate bid
        """
        manager = self._manager
        # Steps 4-7a: Verify JWS, validate action
        payload = await manager.token_validator.validate_jws_token(token, "submit_bid")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        if "task_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: task_id", 400, {})

        if "bidder_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: bidder_id", 400, {})

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "invalid_payload",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        # Step 7d: Validate amount
        if "amount" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: amount", 400, {})

        amount: object = payload["amount"]
        if not is_positive_int(amount):
            raise ServiceError("invalid_reward", "Bid amount must be a positive integer", 400, {})
        amount_int = cast("int", amount)

        # Step 9a: Signer must match bidder_id in payload
        bidder_id: str = payload["bidder_id"]
        if signer_id != bidder_id:
            raise ServiceError("forbidden", "Signer does not match bidder_id", 403, {})

        # Step 10: Load task
        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})

        # Evaluate deadline first
        task = await manager.deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "open":
            raise ServiceError(
                "invalid_status",
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
                    "invalid_status",
                    "Bidding deadline has passed",
                    409,
                    {},
                )

        # Step 12a: Bidder must not be the poster (self_bid, not forbidden)
        if bidder_id == task["poster_id"]:
            raise ServiceError("self_bid", "Cannot bid on your own task", 400, {})

        # Step 12b: Check for duplicate bid
        bid_id = f"bid-{uuid.uuid4()}"
        submitted_at = now_iso()

        try:
            manager.store.insert_bid(
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
                "bid_already_exists",
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
        7.   invalid_payload — wrong action
        9.   forbidden — signer is not the poster
        10.  task_not_found
        """
        manager = self._manager
        # Step 10: Load task first — task_not_found takes priority over auth
        # for non-OPEN tasks (public access). For OPEN tasks, auth errors
        # come before task lookup in standard precedence, BUT the task_id
        # is in the URL (not the token), so we need the task to determine
        # if auth is required. Load task, then check status, then enforce auth.
        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})

        # Evaluate deadline
        task = await manager.deadline_evaluator.evaluate_deadline(task)

        if task["status"] == "open":
            # Sealed bids — require poster authentication
            if not auth_token:
                raise ServiceError(
                    "invalid_jws",
                    "Authorization required to list bids during OPEN phase",
                    400,
                    {},
                )

            payload = await manager.token_validator.validate_jws_token(auth_token, "list_bids")
            signer_id: str = payload["_signer_id"]

            # Validate task_id in payload matches URL
            if "task_id" in payload and payload["task_id"] != task_id:
                raise ServiceError(
                    "invalid_payload",
                    "task_id in payload does not match URL path",
                    400,
                    {},
                )

            # Validate poster_id in payload
            if "poster_id" in payload and signer_id != payload["poster_id"]:
                raise ServiceError("forbidden", "Signer does not match poster_id", 403, {})

            # Signer must be the task's poster
            if signer_id != task["poster_id"]:
                raise ServiceError(
                    "forbidden",
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
            for row in manager.store.get_bids_for_task(task_id)
        ]

        return {"task_id": task_id, "bids": bids}

    async def accept_bid(self, task_id: str, bid_id: str, token: str) -> dict[str, Any]:
        """
        Accept a bid, assigning the worker and starting the execution deadline.

        Error precedence:
        1-6. JWS verification
        7.   invalid_payload — wrong action, missing fields, task_id/bid_id mismatch
        9a.  forbidden — signer != poster_id in payload
        10.  task_not_found
        11.  invalid_status — not OPEN
        9b.  forbidden — signer != task's poster
        12.  bid_not_found
        """
        manager = self._manager
        # Steps 4-7a: Verify JWS, validate action
        payload = await manager.token_validator.validate_jws_token(token, "accept_bid")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        for field_name in ["task_id", "bid_id", "poster_id"]:
            if field_name not in payload:
                raise ServiceError(
                    "invalid_payload",
                    f"Missing required field: {field_name}",
                    400,
                    {},
                )

        # Step 7c: task_id and bid_id must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "invalid_payload",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        if payload["bid_id"] != bid_id:
            raise ServiceError(
                "invalid_payload",
                "bid_id in payload does not match URL path",
                400,
                {},
            )

        # Step 9a: Signer must match poster_id in payload
        if signer_id != payload["poster_id"]:
            raise ServiceError("forbidden", "Signer does not match poster_id", 403, {})

        # Step 10: Load task
        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})

        # Evaluate deadline
        task = await manager.deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "open":
            raise ServiceError(
                "invalid_status",
                f"Cannot accept bid on task in '{task['status']}' status, must be 'open'",
                409,
                {},
            )

        # Step 9b: Signer must be the task's poster
        if signer_id != task["poster_id"]:
            raise ServiceError("forbidden", "Only the poster can accept bids", 403, {})

        # Step 12: Find the bid
        bid = manager.store.get_bid(bid_id, task_id)
        if bid is None:
            raise ServiceError("bid_not_found", "Bid not found", 404, {})

        worker_id = str(bid["bidder_id"])
        accepted_at = now_iso()

        # Update task
        manager.store.update_task(
            task_id,
            {
                "status": "accepted",
                "worker_id": worker_id,
                "accepted_bid_id": bid_id,
                "accepted_at": accepted_at,
            },
            expected_status=None,
        )

        updated = manager.store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        return task_to_response(updated)
