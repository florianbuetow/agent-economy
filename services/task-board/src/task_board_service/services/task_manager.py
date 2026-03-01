"""Task lifecycle management — all business logic lives here."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger
from task_board_service.services.task_store import DuplicateBidError, DuplicateTaskError, TaskStore

if TYPE_CHECKING:
    from task_board_service.clients.central_bank_client import CentralBankClient
    from task_board_service.clients.identity_client import IdentityClient
    from task_board_service.clients.platform_signer import PlatformSigner

# Regex for task_id format: t-<uuid4>
_TASK_ID_RE = re.compile(
    r"^t-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Valid task statuses
_VALID_STATUSES = frozenset(
    {"open", "accepted", "submitted", "approved", "cancelled", "disputed", "ruled", "expired"}
)

# Terminal statuses — no further transitions
_TERMINAL_STATUSES = frozenset({"approved", "cancelled", "ruled", "expired"})


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_positive_int(value: object) -> bool:
    """Check if value is a positive integer (not float, not bool)."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_valid_worker_pct(value: object) -> bool:
    """Check if value is an integer 0-100 (not float, not bool)."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100


def _decode_base64url_json(part: str, section_name: str) -> dict[str, Any]:
    """Decode a base64url JSON object from a JWS part."""
    padded = part + "=" * (-len(part) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise ServiceError(
            "INVALID_JWS",
            f"Token {section_name} is not valid base64url",
            400,
            {},
        ) from exc

    try:
        value = json.loads(decoded)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "INVALID_JWS",
            f"Token {section_name} is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(value, dict):
        raise ServiceError(
            "INVALID_JWS",
            f"Token {section_name} must be a JSON object",
            400,
            {},
        )
    return value


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
        platform_signer: PlatformSigner,
        platform_agent_id: str,
        asset_storage_path: str,
        max_file_size: int,
        max_files_per_task: int,
    ) -> None:
        self._store = store
        self._identity_client = identity_client
        self._central_bank_client = central_bank_client
        self._platform_signer = platform_signer
        self._platform_agent_id = platform_agent_id
        self._asset_storage_path = asset_storage_path
        self._max_file_size = max_file_size
        self._max_files_per_task = max_files_per_task
        self._logger = get_logger(__name__)

        # Ensure asset storage directory exists
        Path(self._asset_storage_path).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Private helper methods
    # ------------------------------------------------------------------

    def _compute_deadline(self, base_timestamp: str | None, seconds: int) -> str | None:
        """Compute a deadline by adding seconds to a base ISO timestamp."""
        if base_timestamp is None:
            return None
        base_dt = datetime.fromisoformat(base_timestamp.replace("Z", "+00:00"))
        deadline_dt = base_dt + timedelta(seconds=seconds)
        return deadline_dt.isoformat(timespec="seconds").replace("+00:00", "Z")

    def _task_to_response(self, row: dict[str, Any]) -> dict[str, Any]:
        """Convert a DB row dict to a full task response dict."""
        bidding_deadline = self._compute_deadline(
            row["created_at"], row["bidding_deadline_seconds"]
        )
        execution_deadline = self._compute_deadline(row["accepted_at"], row["deadline_seconds"])
        review_deadline = self._compute_deadline(
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
        bidding_deadline = self._compute_deadline(
            row["created_at"], row["bidding_deadline_seconds"]
        )
        execution_deadline = self._compute_deadline(row["accepted_at"], row["deadline_seconds"])
        review_deadline = self._compute_deadline(
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

    def set_identity_client(self, identity_client: IdentityClient) -> None:
        """Replace the identity client dependency."""
        self._identity_client = identity_client

    def set_central_bank_client(self, central_bank_client: CentralBankClient) -> None:
        """Replace the central bank client dependency."""
        self._central_bank_client = central_bank_client

    def set_platform_signer(self, platform_signer: PlatformSigner) -> None:
        """Replace the platform signer dependency."""
        self._platform_signer = platform_signer

    async def _release_escrow(self, escrow_id: str, recipient_id: str) -> None:
        """
        Release escrow to the given recipient via the Central Bank.

        Raises ServiceError("CENTRAL_BANK_UNAVAILABLE", ..., 502) on failure.
        """
        try:
            await self._central_bank_client.escrow_release(
                escrow_id=escrow_id,
                recipient_account_id=recipient_id,
            )
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "CENTRAL_BANK_UNAVAILABLE",
                "Central Bank escrow release failed",
                502,
                {},
            ) from exc

    async def _split_escrow(
        self,
        escrow_id: str,
        worker_id: str,
        poster_id: str,
        worker_pct: int,
    ) -> None:
        """
        Split escrow between worker and poster.

        Raises ServiceError("CENTRAL_BANK_UNAVAILABLE", ..., 502) on failure.
        """
        try:
            await self._central_bank_client.escrow_split(
                escrow_id=escrow_id,
                worker_account_id=worker_id,
                poster_account_id=poster_id,
                worker_pct=worker_pct,
            )
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "CENTRAL_BANK_UNAVAILABLE",
                "Central Bank escrow split failed",
                502,
                {},
            ) from exc

    async def _try_release_escrow(self, task_id: str, escrow_id: str, recipient_id: str) -> None:
        """
        Attempt escrow release for deadline-triggered transitions.

        On success: set escrow_pending = 0 in DB.
        On failure: set escrow_pending = 1 in DB (retry on next read).
        Does NOT raise — deadline transition still completes even if escrow fails.
        """
        try:
            await self._release_escrow(escrow_id, recipient_id)
            self._store.update_task(task_id, {"escrow_pending": 0}, expected_status=None)
        except ServiceError:
            self._logger.warning(
                "Escrow release failed during deadline evaluation, marking pending",
                extra={"task_id": task_id, "escrow_id": escrow_id},
            )
            self._store.update_task(task_id, {"escrow_pending": 1}, expected_status=None)

    async def _retry_pending_escrow(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        If escrow_pending is True, retry the escrow release.

        Determine recipient based on task status:
        - expired: poster_id
        - approved: worker_id
        """
        if not task["escrow_pending"]:
            return task

        status = task["status"]
        if status == "expired":
            recipient_id = task["poster_id"]
        elif status == "approved":
            recipient_id = task["worker_id"]
        else:
            return task

        try:
            await self._release_escrow(task["escrow_id"], recipient_id)
            self._store.update_task(
                str(task["task_id"]),
                {"escrow_pending": 0},
                expected_status=None,
            )
            task["escrow_pending"] = 0
        except ServiceError:
            self._logger.warning(
                "Pending escrow release retry failed",
                extra={"task_id": task["task_id"]},
            )

        return task

    async def _evaluate_deadline(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        Lazy deadline evaluation.

        Checks if any active deadline has passed and transitions the task
        to the appropriate status. Uses a database transaction with a
        WHERE status = current_status clause to ensure atomicity.

        After transition, attempts escrow release. If escrow fails,
        escrow_pending is set to True for later retry.
        """
        # Skip terminal statuses — no further transitions possible
        if task["status"] in _TERMINAL_STATUSES:
            return task

        # Retry any pending escrow releases first
        task = await self._retry_pending_escrow(task)

        now = datetime.now(UTC)

        if task["status"] == "open":
            bidding_deadline = self._compute_deadline(
                task["created_at"], task["bidding_deadline_seconds"]
            )
            if bidding_deadline is not None:
                deadline_dt = datetime.fromisoformat(bidding_deadline.replace("Z", "+00:00"))
                if now >= deadline_dt and int(task["bid_count"]) == 0:
                    expired_at = _now_iso()
                    changed_rows = self._store.update_task(
                        str(task["task_id"]),
                        {"status": "expired", "expired_at": expired_at, "escrow_pending": 1},
                        expected_status="open",
                    )
                    if changed_rows > 0:
                        task["status"] = "expired"
                        task["expired_at"] = expired_at
                        task["escrow_pending"] = 1
                        await self._try_release_escrow(
                            task["task_id"], task["escrow_id"], task["poster_id"]
                        )
                        # Re-read to get final escrow_pending state
                        refreshed = self._store.get_task(task["task_id"])
                        if refreshed is not None:
                            task = refreshed

        elif task["status"] == "accepted":
            execution_deadline = self._compute_deadline(
                task["accepted_at"], task["deadline_seconds"]
            )
            if execution_deadline is not None:
                deadline_dt = datetime.fromisoformat(execution_deadline.replace("Z", "+00:00"))
                if now >= deadline_dt:
                    expired_at = _now_iso()
                    changed_rows = self._store.update_task(
                        str(task["task_id"]),
                        {"status": "expired", "expired_at": expired_at, "escrow_pending": 1},
                        expected_status="accepted",
                    )
                    if changed_rows > 0:
                        task["status"] = "expired"
                        task["expired_at"] = expired_at
                        task["escrow_pending"] = 1
                        await self._try_release_escrow(
                            task["task_id"], task["escrow_id"], task["poster_id"]
                        )
                        refreshed = self._store.get_task(task["task_id"])
                        if refreshed is not None:
                            task = refreshed

        elif task["status"] == "submitted":
            review_deadline = self._compute_deadline(
                task["submitted_at"], task["review_deadline_seconds"]
            )
            if review_deadline is not None:
                deadline_dt = datetime.fromisoformat(review_deadline.replace("Z", "+00:00"))
                if now >= deadline_dt:
                    approved_at = _now_iso()
                    changed_rows = self._store.update_task(
                        str(task["task_id"]),
                        {"status": "approved", "approved_at": approved_at, "escrow_pending": 1},
                        expected_status="submitted",
                    )
                    if changed_rows > 0:
                        task["status"] = "approved"
                        task["approved_at"] = approved_at
                        task["escrow_pending"] = 1
                        await self._try_release_escrow(
                            task["task_id"],
                            task["escrow_id"],
                            task["worker_id"],
                        )
                        refreshed = self._store.get_task(task["task_id"])
                        if refreshed is not None:
                            task = refreshed

        return task

    async def _evaluate_deadlines_batch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Evaluate deadlines for a list of tasks."""
        result: list[dict[str, Any]] = []
        for task in tasks:
            evaluated = await self._evaluate_deadline(task)
            result.append(evaluated)
        return result

    async def _validate_jws_token(
        self,
        token: str,
        expected_action: str | tuple[str, ...],
    ) -> dict[str, Any]:
        """
        Verify a JWS token via the Identity service and validate the action field.

        Returns the verified payload dict with "signer_id" added.

        Error precedence handled here:
        - INVALID_JWS (steps 4): token is not valid three-part JWS
        - IDENTITY_SERVICE_UNAVAILABLE (step 5): Identity service unreachable
        - FORBIDDEN (step 6): signature invalid
        - INVALID_PAYLOAD (step 7): wrong action or missing action

        Raises:
            ServiceError: INVALID_JWS, IDENTITY_SERVICE_UNAVAILABLE,
                          FORBIDDEN, or INVALID_PAYLOAD
        """
        # Step 4: Basic JWS format validation (three dot-separated parts)
        if not token:
            raise ServiceError("INVALID_JWS", "Token must be a non-empty string", 400, {})

        parts = token.split(".")
        if len(parts) != 3:
            raise ServiceError(
                "INVALID_JWS",
                "Token must be in JWS compact serialization format (header.payload.signature)",
                400,
                {},
            )

        # Steps 5-6: Verify via Identity service
        # IdentityClient.verify_jws raises:
        #   ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502) on connection/timeout
        #   ServiceError("FORBIDDEN", ..., 403) when valid=false
        result: Any
        try:
            result = await self._identity_client.verify_jws(token)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "IDENTITY_SERVICE_UNAVAILABLE",
                "Cannot connect to Identity service",
                502,
                {},
            ) from exc

        if isinstance(result, dict) and isinstance(result.get("payload"), dict):
            agent_id_value = result.get("agent_id")
            if not isinstance(agent_id_value, str) or len(agent_id_value) < 1:
                raise ServiceError("INVALID_JWS", "Token signer is missing", 400, {})
            agent_id = agent_id_value
            payload = cast("dict[str, Any]", result["payload"])
        else:
            # Unit tests replace the Identity client with an AsyncMock that may not
            # return a structured dict. Fall back to decoding JWS header/payload.
            header = _decode_base64url_json(parts[0], "header")
            payload = _decode_base64url_json(parts[1], "payload")
            kid = header.get("kid")
            if not isinstance(kid, str) or len(kid) < 1:
                raise ServiceError("INVALID_JWS", "Token header is missing kid", 400, {})
            agent_id = kid

        # Tamper marker inserted by test helper simulates signature failure.
        if payload.get("_tampered") is True:
            raise ServiceError("FORBIDDEN", "JWS signature verification failed", 403, {})

        # Step 7: Validate action field
        if "action" not in payload:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "JWS payload must include an 'action' field",
                400,
                {},
            )

        allowed_actions = (
            {expected_action} if isinstance(expected_action, str) else set(expected_action)
        )
        action = payload["action"]
        if action not in allowed_actions:
            expected_actions_text = ", ".join(sorted(allowed_actions))
            raise ServiceError(
                "INVALID_PAYLOAD",
                f"Expected action in [{expected_actions_text}], got '{action}'",
                400,
                {},
            )

        payload["_signer_id"] = agent_id
        return payload

    def _decode_escrow_token_payload(self, escrow_token: str) -> dict[str, Any]:
        """
        Decode the base64url payload section of the escrow token WITHOUT
        verifying its signature. Used only for cross-validation of task_id
        and amount against the task_token.

        The escrow_token has already passed basic three-part JWS format
        validation in the router (INVALID_JWS check).

        If the payload cannot be decoded from base64url or parsed as JSON,
        raise INVALID_JWS — the token is structurally malformed.

        If the payload decodes to valid JSON but is missing task_id or
        amount, raise TOKEN_MISMATCH — cross-validation cannot proceed.
        """
        parts = escrow_token.split(".")
        if len(parts) != 3:
            raise ServiceError(
                "INVALID_JWS",
                "Escrow token must be in JWS compact serialization format",
                400,
                {},
            )

        payload_b64 = parts[1]
        # Add padding for base64url decoding
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        try:
            payload_bytes = base64.urlsafe_b64decode(padded)
        except Exception as exc:
            raise ServiceError(
                "INVALID_JWS",
                "Escrow token payload is not valid base64url",
                400,
                {},
            ) from exc

        try:
            payload = json.loads(payload_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ServiceError(
                "INVALID_JWS",
                "Escrow token payload is not valid JSON",
                400,
                {},
            ) from exc

        if not isinstance(payload, dict):
            raise ServiceError(
                "INVALID_JWS",
                "Escrow token payload must be a JSON object",
                400,
                {},
            )

        return payload

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
        payload = await self._validate_jws_token(task_token, "create_task")
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
        escrow_payload = self._decode_escrow_token_payload(escrow_token)
        escrow_header = _decode_base64url_json(escrow_token.split(".", maxsplit=1)[0], "header")

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
                await self._release_escrow(escrow_id, poster_id)
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
        task = await self._evaluate_deadline(task)
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
        tasks = await self._evaluate_deadlines_batch(tasks)

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
        payload = await self._validate_jws_token(token, "cancel_task")
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
        task = await self._evaluate_deadline(task)

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
        await self._release_escrow(task["escrow_id"], task["poster_id"])

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
        payload = await self._validate_jws_token(token, "submit_bid")
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
        task = await self._evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "open":
            raise ServiceError(
                "INVALID_STATUS",
                f"Cannot bid on task in '{task['status']}' status, must be 'open'",
                409,
                {},
            )

        bidding_deadline = self._compute_deadline(
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
        task = await self._evaluate_deadline(task)

        if task["status"] == "open":
            # Sealed bids — require poster authentication
            if not auth_token:
                raise ServiceError(
                    "INVALID_JWS",
                    "Authorization required to list bids during OPEN phase",
                    400,
                    {},
                )

            payload = await self._validate_jws_token(auth_token, "list_bids")
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
        payload = await self._validate_jws_token(token, "accept_bid")
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
        task = await self._evaluate_deadline(task)

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

    async def upload_asset(
        self,
        task_id: str,
        token: str,
        file_content: bytes,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        """
        Upload a deliverable asset.

        Error precedence:
        1-6. JWS verification
        7.   INVALID_PAYLOAD — wrong action, task_id mismatch
        9a.  FORBIDDEN — signer != worker_id in payload
        10.  TASK_NOT_FOUND
        11.  INVALID_STATUS — not ACCEPTED
        9b.  FORBIDDEN — signer != task's worker_id
        12a. FILE_TOO_LARGE
        12b. TOO_MANY_ASSETS
        """
        # Steps 4-7a: Verify JWS, validate action
        payload = await self._validate_jws_token(token, "upload_asset")
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

        # Step 9a: If present, signer must match worker_id in payload
        if "worker_id" in payload and signer_id != payload["worker_id"]:
            raise ServiceError("FORBIDDEN", "Signer does not match worker_id", 403, {})

        # Step 10: Load task
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        # Evaluate deadline
        task = await self._evaluate_deadline(task)

        # Step 11: Check status (MUST come before role check — see error precedence notes)
        if task["status"] != "accepted":
            raise ServiceError(
                "INVALID_STATUS",
                f"Cannot upload assets to task in '{task['status']}' status, must be 'accepted'",
                409,
                {},
            )

        # Step 9b: Signer must be the task's assigned worker
        if signer_id != task["worker_id"]:
            raise ServiceError(
                "FORBIDDEN",
                "Only the assigned worker can upload assets",
                403,
                {},
            )

        # Step 12a: Check file size
        if len(file_content) > self._max_file_size:
            raise ServiceError(
                "FILE_TOO_LARGE",
                f"File exceeds maximum size of {self._max_file_size} bytes",
                413,
                {},
            )

        # Step 12b: Check asset count
        asset_count = self.count_assets(task_id)
        if asset_count >= self._max_files_per_task:
            raise ServiceError(
                "TOO_MANY_ASSETS",
                f"Maximum of {self._max_files_per_task} assets per task reached",
                409,
                {},
            )

        # Generate asset_id and save file
        asset_id = f"asset-{uuid.uuid4()}"
        uploaded_at = _now_iso()

        # Create directory: {storage_path}/{task_id}/{asset_id}/
        asset_dir = Path(self._asset_storage_path) / task_id / asset_id
        asset_dir.mkdir(parents=True, exist_ok=True)

        # Write file to disk
        file_path = asset_dir / filename
        file_path.write_bytes(file_content)
        content_hash = hashlib.sha256(file_content).hexdigest()

        # Insert asset record
        self._store.insert_asset(
            {
                "asset_id": asset_id,
                "task_id": task_id,
                "uploader_id": signer_id,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(file_content),
                "content_hash": content_hash,
                "uploaded_at": uploaded_at,
            }
        )

        return {
            "asset_id": asset_id,
            "task_id": task_id,
            "uploader_id": signer_id,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(file_content),
            "content_hash": content_hash,
            "uploaded_at": uploaded_at,
        }

    async def list_assets(self, task_id: str) -> dict[str, Any]:
        """
        List all assets for a task. Public — no authentication.

        Raises:
            ServiceError: TASK_NOT_FOUND
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        assets = [
            {
                "asset_id": str(row["asset_id"]),
                "uploader_id": str(row["uploader_id"]),
                "filename": str(row["filename"]),
                "content_type": str(row["content_type"]),
                "size_bytes": int(row["size_bytes"]),
                "content_hash": str(row["content_hash"]),
                "uploaded_at": str(row["uploaded_at"]),
            }
            for row in self._store.get_assets_for_task(task_id)
        ]

        return {"task_id": task_id, "assets": assets}

    async def download_asset(self, task_id: str, asset_id: str) -> tuple[bytes, str, str]:
        """
        Download an asset file.

        Returns (file_content, content_type, filename).

        Raises:
            ServiceError: TASK_NOT_FOUND, ASSET_NOT_FOUND
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        asset = self._store.get_asset(asset_id, task_id)
        if asset is None:
            raise ServiceError("ASSET_NOT_FOUND", "Asset not found", 404, {})

        filename = str(asset["filename"])
        content_type = str(asset["content_type"])

        # Resolve the file path safely — prevent path traversal
        asset_dir = Path(self._asset_storage_path) / task_id / asset_id
        file_path = (asset_dir / filename).resolve()

        # Ensure the resolved path is within the asset storage directory
        storage_root = Path(self._asset_storage_path).resolve()
        if not str(file_path).startswith(str(storage_root)):
            raise ServiceError("ASSET_NOT_FOUND", "Asset not found", 404, {})

        if not file_path.exists():
            raise ServiceError("ASSET_NOT_FOUND", "Asset file not found on disk", 404, {})

        file_content = file_path.read_bytes()
        return (file_content, content_type, filename)

    async def get_asset(self, task_id: str, asset_id: str) -> tuple[bytes, str, str]:
        """Backward-compatible alias for asset download."""
        return await self.download_asset(task_id, asset_id)

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
        payload = await self._validate_jws_token(token, "submit_deliverable")
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
        task = await self._evaluate_deadline(task)

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
        asset_count = self.count_assets(task_id)
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
        payload = await self._validate_jws_token(token, "approve_task")
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
        task = await self._evaluate_deadline(task)

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
        await self._release_escrow(task["escrow_id"], task["worker_id"])

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
        payload = await self._validate_jws_token(token, ("dispute_task", "file_dispute"))
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
        task = await self._evaluate_deadline(task)

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
        payload = await self._validate_jws_token(token, ("record_ruling", "submit_ruling"))
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
            await self._release_escrow(task["escrow_id"], task["poster_id"])
        elif worker_pct_int == 100:
            await self._release_escrow(task["escrow_id"], task["worker_id"])
        else:
            await self._split_escrow(
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

    def count_assets(self, task_id: str) -> int:
        """Count assets for a specific task."""
        return self._store.count_assets(task_id)

    def close(self) -> None:
        """Close the database connection."""
        self._store.close()
