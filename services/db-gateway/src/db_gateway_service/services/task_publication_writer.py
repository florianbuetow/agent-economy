"""Atomic admission and receipt-backed completion of native task publications."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import TYPE_CHECKING, Any, Final, cast

from service_commons.exceptions import ServiceError

from db_gateway_service.task_operation_requests import parse_operation_id
from db_gateway_service.task_publication_financial import (
    PublicationBlockReason,
    PublicationEvidenceError,
    PublicationFinancialProof,
    inspect_publication_lock,
)
from db_gateway_service.task_publication_requests import (
    TaskPublicationRequest,
    parse_task_publication_request,
)
from db_gateway_service.task_time import (
    add_seconds_to_epoch_us,
    epoch_us_to_utc_text,
    parse_utc_text_to_epoch_us,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_OBSERVATION_ID: Final[re.Pattern[str]] = re.compile(
    r"\Aobs-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
_BLOCK_ERROR: Final[str] = "publication_integrity_blocked"
_BLOCK_MESSAGE: Final[str] = "Publication evidence requires reconciliation"
_BLOCK_REASONS: Final[frozenset[str]] = frozenset(
    {
        "receipt_invalid",
        "escrow_invalid",
        "settlement_conflict",
        "publication_conflict",
        "clock_out_of_range",
    }
)


def _json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _error(
    code: str,
    message: str,
    status: int,
    details: dict[str, object],
) -> ServiceError:
    return ServiceError(code, message, status, details)


class TaskPublicationWriter:
    """Own native publication state on one caller-supplied SQLite connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock_us: Callable[[], int],
    ) -> None:
        self._db = connection
        self._clock_us = clock_us

    def admit(
        self,
        request: TaskPublicationRequest,
        caller_kid: str,
    ) -> dict[str, Any]:
        """Reserve one exact task intent before any external financial effect."""
        owner = self._caller_kid(caller_kid)
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing = self._publication(cursor, request.operation_id)
            if existing is not None:
                envelope = self._replay(existing, request, owner)
                self._db.rollback()
                if existing[8] == "blocked_integrity":
                    self._raise_blocked(envelope)
                return envelope
            if (
                cursor.execute(
                    "SELECT 1 FROM board_task_operations WHERE operation_id=?",
                    (request.operation_id,),
                ).fetchone()
                is not None
            ):
                raise _error(
                    "operation_id_conflict",
                    "Operation identity already belongs to a task lifecycle command",
                    409,
                    {"winning_operation_id": request.operation_id},
                )
            self._assert_task_available(cursor, request.task_id)
            if (
                cursor.execute(
                    "SELECT 1 FROM identity_agents WHERE agent_id=?",
                    (request.actor_id,),
                ).fetchone()
                is None
            ):
                raise _error(
                    "foreign_key_violation",
                    "Task publication actor is not registered",
                    409,
                    {},
                )
            admitted_at_us = self._admission_clock()
            self._validate_admission_cutoffs(request, admitted_at_us)
            cursor.execute(
                "INSERT INTO board_task_publications ("
                "operation_id,publication_contract_version,task_id,canonical_request_json,"
                "request_sha256,caller_kid,actor_id,lock_operation_id,state,admitted_at_us,"
                "escrow_id,lock_request_sha256,lock_receipt_event_id,published_at_us,"
                "creation_event_id,result_json,error_json,request_protocol_version"
                ") VALUES (?,1,?,?,?,?,?,?,'awaiting_lock',?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,1)",
                (
                    request.operation_id,
                    request.task_id,
                    request.canonical_json,
                    request.request_sha256,
                    owner,
                    request.actor_id,
                    request.lock_operation_id,
                    admitted_at_us,
                ),
            )
            row = self._required_publication(cursor, request.operation_id)
            envelope = self._envelope(row)
            self._db.commit()
            return envelope
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise

    def reconcile(self, operation_id: str, caller_kid: str) -> dict[str, Any]:
        """Publish only from authoritative persisted Bank facts; accept no receipt input."""
        parsed_operation_id = parse_operation_id(operation_id)
        owner = self._caller_kid(caller_kid)
        cursor = self._db.cursor()
        blocked: dict[str, Any] | None = None
        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = self._owned_publication(cursor, parsed_operation_id, owner)
            if row[8] == "published":
                envelope = self._envelope(row)
                self._db.rollback()
                return envelope
            if row[8] == "blocked_integrity":
                envelope = self._envelope(row)
                self._db.rollback()
                self._raise_blocked(envelope)
            blocked = self._reconcile_pending(cursor, row)
            if blocked["publication_state"] == "awaiting_lock":
                self._db.rollback()
                return blocked
            self._db.commit()
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise
        assert blocked is not None
        if blocked["publication_state"] == "blocked_integrity":
            self._raise_blocked(blocked)
        return blocked

    def _reconcile_pending(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
    ) -> dict[str, Any]:
        try:
            request = self._stored_request(row)
        except ServiceError as exc:
            if exc.error != "publication_state_corrupt":
                raise
            return self._block(cursor, row, "publication_conflict")
        if self._publication_conflict(cursor, row, request):
            return self._block(cursor, row, "publication_conflict")
        try:
            proof = inspect_publication_lock(cursor, request)
        except PublicationEvidenceError as exc:
            return self._block(cursor, row, exc.reason)
        if proof is None:
            return self._envelope(row)
        return self._publish_or_block(cursor, row, request, proof)

    def get(
        self, operation_id: str, caller_kid: str, *, expected_actor_id: str | None
    ) -> dict[str, Any]:
        """Return the original caller's immutable publication view in every state."""
        parsed_operation_id = parse_operation_id(operation_id)
        owner = self._caller_kid(caller_kid)
        if expected_actor_id is not None and (
            not isinstance(expected_actor_id, str) or not expected_actor_id
        ):
            raise _error("invalid_payload", "poster_id must be a nonempty string", 400, {})
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN")
            row = self._owned_publication(cursor, parsed_operation_id, owner)
            if expected_actor_id is not None and row[6] != expected_actor_id:
                raise _error("publication_not_found", "Publication not found", 404, {})
            envelope = self._envelope(row)
            self._db.rollback()
            return envelope
        except Exception:
            self._db.rollback()
            raise

    def recovery(
        self,
        after: str,
        limit: int,
        caller_kid: str,
    ) -> dict[str, object]:
        """Enumerate this caller's unfinished native and legacy recovery work."""
        owner = self._caller_kid(caller_kid)
        cursor_key = self._recovery_cursor(after)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise _error(
                "invalid_payload",
                "limit must be an integer in 1..1000",
                400,
                {"field": "limit"},
            )
        rows = self._db.execute(
            "SELECT cursor,kind,identity FROM ("
            "SELECT ('legacy:' || observation_id) AS cursor,'legacy' AS kind,"
            "observation_id AS identity FROM board_legacy_posting_observations "
            "WHERE caller_kid=? AND state='refund_pending' "
            "UNION ALL "
            "SELECT ('native:' || operation_id) AS cursor,'native' AS kind,"
            "operation_id AS identity FROM board_task_publications "
            "WHERE caller_kid=? AND state='awaiting_lock' AND request_protocol_version=1"
            ") WHERE cursor > ? COLLATE BINARY ORDER BY cursor COLLATE BINARY LIMIT ?",
            (owner, owner, cursor_key, limit + 1),
        ).fetchall()
        visible = rows[:limit]
        items = [
            {
                "cursor": str(row[0]),
                "kind": str(row[1]),
                "identity": str(row[2]),
            }
            for row in visible
        ]
        next_after = items[-1]["cursor"] if len(rows) > limit and items else None
        return {"items": items, "next_after": next_after}

    @staticmethod
    def _caller_kid(value: object) -> str:
        if not isinstance(value, str) or value == "":
            raise _error("forbidden", "Verified Gateway caller is required", 403, {})
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _error("forbidden", "Verified Gateway caller is invalid", 403, {}) from exc
        return value

    def _admission_clock(self) -> int:
        try:
            value = self._clock_us()
            self._timestamp(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise _error(
                "invalid_clock",
                "Task publication clock is outside the supported range",
                500,
                {},
            ) from exc
        return value

    def _publication_clock(self) -> int:
        value = self._clock_us()
        if type(value) is not int:
            raise ValueError("clock is not an integer")
        self._timestamp(value)
        return value

    def _validate_admission_cutoffs(
        self,
        request: TaskPublicationRequest,
        admitted_at_us: int,
    ) -> None:
        """Reject signed cutoffs that cannot be represented from admission time."""
        try:
            bidding_cutoff_us = add_seconds_to_epoch_us(
                admitted_at_us,
                request.bidding_deadline_seconds,
            )
            self._timestamp(bidding_cutoff_us)
        except (OverflowError, TypeError, ValueError) as exc:
            raise _error(
                "invalid_deadline",
                "bidding_deadline_seconds exceeds the supported publication cutoff",
                400,
                {"field": "bidding_deadline_seconds"},
            ) from exc
        try:
            acceptance_cutoff_us = add_seconds_to_epoch_us(
                bidding_cutoff_us,
                request.acceptance_window_seconds,
            )
            self._timestamp(acceptance_cutoff_us)
        except (OverflowError, TypeError, ValueError) as exc:
            raise _error(
                "invalid_deadline",
                "acceptance_window_seconds exceeds the supported composed cutoff",
                400,
                {"field": "acceptance_window_seconds"},
            ) from exc

    @staticmethod
    def _timestamp(value: object) -> str:
        rendered = epoch_us_to_utc_text(value)
        head, separator, fraction = rendered.partition(".")
        if separator == "":
            return rendered[:-1] + ".000000Z"
        return head + "." + fraction[:-1].ljust(6, "0") + "Z"

    @staticmethod
    def _publication(cursor: sqlite3.Cursor, operation_id: str) -> tuple[Any, ...] | None:
        row = cursor.execute(
            "SELECT operation_id,publication_contract_version,task_id,canonical_request_json,"
            "request_sha256,caller_kid,actor_id,lock_operation_id,state,admitted_at_us,"
            "escrow_id,lock_request_sha256,lock_receipt_event_id,published_at_us,"
            "creation_event_id,result_json,error_json "
            "FROM board_task_publications WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        return None if row is None else tuple(row)

    def _required_publication(
        self,
        cursor: sqlite3.Cursor,
        operation_id: str,
    ) -> tuple[Any, ...]:
        row = self._publication(cursor, operation_id)
        if row is None:
            raise _error(
                "publication_state_corrupt",
                "Task publication disappeared during its transaction",
                500,
                {},
            )
        return row

    def _owned_publication(
        self,
        cursor: sqlite3.Cursor,
        operation_id: str,
        caller_kid: str,
    ) -> tuple[Any, ...]:
        row = self._publication(cursor, operation_id)
        if row is None:
            raise _error("publication_not_found", "Publication not found", 404, {})
        if row[5] != caller_kid:
            raise _error(
                "operation_caller_mismatch",
                "Task publication identity belongs to another verified Gateway caller",
                403,
                {"operation_id": operation_id},
            )
        return row

    @staticmethod
    def _assert_task_available(cursor: sqlite3.Cursor, task_id: str) -> None:
        conflicts = (
            ("board_task_publications", "task_id"),
            ("board_tasks", "task_id"),
            ("board_legacy_posting_observations", "task_id"),
            ("bank_escrow", "task_id"),
        )
        for table, column in conflicts:
            row = cursor.execute(
                f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1",  # nosec B608
                (task_id,),
            ).fetchone()
            if row is not None:
                raise _error(
                    "task_id_conflict",
                    "Task identity is already reserved or has financial history",
                    409,
                    {"task_id": task_id},
                )

    def _replay(
        self,
        row: tuple[Any, ...],
        request: TaskPublicationRequest,
        caller_kid: str,
    ) -> dict[str, Any]:
        if row[5] != caller_kid:
            raise _error(
                "operation_caller_mismatch",
                "Task publication identity belongs to another verified Gateway caller",
                403,
                {"operation_id": request.operation_id},
            )
        if row[3] != request.canonical_json or row[4] != request.request_sha256:
            raise _error(
                "operation_id_conflict",
                "Operation identity already belongs to different publication terms",
                409,
                {"winning_operation_id": request.operation_id},
            )
        return self._envelope(row)

    @staticmethod
    def _stored_request(row: tuple[Any, ...]) -> TaskPublicationRequest:
        try:
            decoded: object = json.loads(str(row[3]))
            if not isinstance(decoded, dict):
                raise ValueError("stored request is not an object")
            payload = dict(decoded)
            payload["operation_id"] = row[0]
            request = parse_task_publication_request(payload)
        except (ServiceError, TypeError, ValueError) as exc:
            raise _error(
                "publication_state_corrupt",
                "Stored task publication request is invalid",
                500,
                {"operation_id": str(row[0])},
            ) from exc
        if (
            request.canonical_json != row[3]
            or request.request_sha256 != row[4]
            or request.task_id != row[2]
            or request.actor_id != row[6]
            or request.lock_operation_id != row[7]
        ):
            raise _error(
                "publication_state_corrupt",
                "Stored task publication identity is inconsistent",
                500,
                {"operation_id": str(row[0])},
            )
        return request

    @staticmethod
    def _publication_conflict(
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        request: TaskPublicationRequest,
    ) -> bool:
        if (
            cursor.execute(
                "SELECT 1 FROM board_task_operations WHERE operation_id=?",
                (request.operation_id,),
            ).fetchone()
            is not None
        ):
            return True
        if (
            cursor.execute(
                "SELECT 1 FROM board_tasks WHERE task_id=?",
                (request.task_id,),
            ).fetchone()
            is not None
        ):
            return True
        other = cursor.execute(
            "SELECT 1 FROM board_task_publications WHERE task_id=? AND operation_id<>? LIMIT 1",
            (request.task_id, row[0]),
        ).fetchone()
        legacy = cursor.execute(
            "SELECT 1 FROM board_legacy_posting_observations WHERE task_id=? LIMIT 1",
            (request.task_id,),
        ).fetchone()
        return other is not None or legacy is not None

    def _publish_or_block(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        request: TaskPublicationRequest,
        proof: PublicationFinancialProof,
    ) -> dict[str, Any]:
        try:
            published_at_us = self._publication_clock()
            lock_committed_at_us = parse_utc_text_to_epoch_us(proof.committed_at)
            if published_at_us < row[9] or published_at_us < lock_committed_at_us:
                return self._block(cursor, row, "clock_out_of_range")
            bidding_cutoff_us = add_seconds_to_epoch_us(
                published_at_us,
                request.bidding_deadline_seconds,
            )
            acceptance_cutoff_us = add_seconds_to_epoch_us(
                bidding_cutoff_us,
                request.acceptance_window_seconds,
            )
            published_at = self._timestamp(published_at_us)
            bidding_deadline = self._timestamp(bidding_cutoff_us)
            acceptance_deadline = self._timestamp(acceptance_cutoff_us)
        except (OverflowError, TypeError, ValueError):
            return self._block(cursor, row, "clock_out_of_range")

        cursor.execute("SAVEPOINT native_publication")
        try:
            cursor.execute(
                "INSERT INTO board_tasks (task_id,poster_id,title,spec,reward,status,"
                "bidding_deadline_seconds,deadline_seconds,review_deadline_seconds,"
                "bidding_deadline,bid_count,escrow_pending,escrow_id,created_at,dispute_id) "
                "VALUES (?,?,?,?,?,'open',?,?,?,?,0,0,?,?,NULL)",
                (
                    request.task_id,
                    request.actor_id,
                    request.title,
                    request.spec,
                    request.reward,
                    request.bidding_deadline_seconds,
                    request.execution_deadline_seconds,
                    request.review_deadline_seconds,
                    bidding_deadline,
                    proof.escrow_id,
                    published_at,
                ),
            )
            cursor.execute(
                "INSERT INTO board_task_contracts (task_id,lifecycle_version,"
                "timing_contract_version,acceptance_window_seconds,bidding_cutoff_us,"
                "acceptance_cutoff_us,execution_cutoff_us,review_cutoff_us,"
                "accepted_reward,accepted_bid_amount) "
                "VALUES (?,0,2,?,?,?,NULL,NULL,NULL,NULL)",
                (
                    request.task_id,
                    request.acceptance_window_seconds,
                    bidding_cutoff_us,
                    acceptance_cutoff_us,
                ),
            )
            event_payload = _json(
                {
                    "bidding_deadline": bidding_deadline,
                    "reward": request.reward,
                    "title": request.title,
                }
            )
            cursor.execute(
                "INSERT INTO events (event_source,event_type,timestamp,task_id,agent_id,"
                "summary,payload) VALUES ('task-board','task.created',?,?,?,?,?)",
                (
                    published_at,
                    request.task_id,
                    request.actor_id,
                    f"Task created: {request.title}",
                    event_payload,
                ),
            )
            if cursor.lastrowid is None or cursor.lastrowid <= 0:
                raise sqlite3.IntegrityError("task creation event has no positive identity")
            event_id = int(cursor.lastrowid)
            result: dict[str, object] = {
                "task_id": request.task_id,
                "poster_id": request.actor_id,
                "title": request.title,
                "spec": request.spec,
                "reward": request.reward,
                "bidding_deadline_seconds": request.bidding_deadline_seconds,
                "acceptance_window_seconds": request.acceptance_window_seconds,
                "execution_deadline_seconds": request.execution_deadline_seconds,
                "review_deadline_seconds": request.review_deadline_seconds,
                "status": "open",
                "escrow_id": proof.escrow_id,
                "created_at": published_at,
                "bidding_deadline": bidding_deadline,
                "acceptance_deadline": acceptance_deadline,
                "bidding_cutoff_us": bidding_cutoff_us,
                "acceptance_cutoff_us": acceptance_cutoff_us,
                "lifecycle_version": 0,
                "timing_contract_version": 2,
                "event_id": event_id,
            }
            cursor.execute(
                "UPDATE board_task_publications SET state='published',escrow_id=?,"
                "lock_request_sha256=?,lock_receipt_event_id=?,published_at_us=?,"
                "creation_event_id=?,result_json=?,error_json=NULL "
                "WHERE operation_id=? AND state='awaiting_lock'",
                (
                    proof.escrow_id,
                    proof.request_sha256,
                    proof.receipt_event_id,
                    published_at_us,
                    event_id,
                    _json(result),
                    request.operation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise sqlite3.IntegrityError("publication claim changed during completion")
            cursor.execute("RELEASE SAVEPOINT native_publication")
        except sqlite3.IntegrityError:
            cursor.execute("ROLLBACK TO SAVEPOINT native_publication")
            cursor.execute("RELEASE SAVEPOINT native_publication")
            return self._block(cursor, row, "publication_conflict")
        return self._envelope(self._required_publication(cursor, request.operation_id))

    def _block(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        reason: PublicationBlockReason,
    ) -> dict[str, Any]:
        stored_error = {
            "error": _BLOCK_ERROR,
            "message": _BLOCK_MESSAGE,
            "details": {"reason": reason},
        }
        cursor.execute(
            "UPDATE board_task_publications SET state='blocked_integrity',escrow_id=NULL,"
            "lock_request_sha256=NULL,lock_receipt_event_id=NULL,published_at_us=NULL,"
            "creation_event_id=NULL,result_json=NULL,error_json=? "
            "WHERE operation_id=? AND state='awaiting_lock'",
            (_json(stored_error), row[0]),
        )
        if cursor.rowcount != 1:
            raise _error(
                "publication_state_corrupt",
                "Task publication could not record its integrity block",
                500,
                {"operation_id": str(row[0])},
            )
        return self._envelope(self._required_publication(cursor, str(row[0])))

    def _envelope(self, row: tuple[Any, ...]) -> dict[str, Any]:
        state = row[8]
        if row[1] != 1 or state not in {"awaiting_lock", "published", "blocked_integrity"}:
            raise _error(
                "publication_state_corrupt",
                "Task publication has an invalid persisted state",
                500,
                {"operation_id": str(row[0])},
            )
        result = self._stored_object(row[15], "result_json")
        error = self._stored_object(row[16], "error_json")
        if state == "awaiting_lock":
            valid = all(row[index] is None for index in range(10, 17))
        elif state == "blocked_integrity":
            valid = all(row[index] is None for index in range(10, 16)) and self._valid_block_error(
                error
            )
        else:
            valid = (
                isinstance(row[10], str)
                and row[10] != ""
                and isinstance(row[11], str)
                and len(row[11]) == 64
                and type(row[12]) is int
                and type(row[13]) is int
                and type(row[14]) is int
                and result is not None
                and error is None
            )
        if not valid:
            raise _error(
                "publication_state_corrupt",
                "Task publication state fields are inconsistent",
                500,
                {"operation_id": str(row[0])},
            )
        return {
            "protocol_version": 1,
            "operation_id": str(row[0]),
            "task_id": str(row[2]),
            "request_sha256": str(row[4]),
            "publication_state": str(state),
            "lock_operation_id": str(row[7]),
            "escrow_id": None if row[10] is None else str(row[10]),
            "admitted_at": self._timestamp(row[9]),
            "published_at": None if row[13] is None else self._timestamp(row[13]),
            "result": result,
            "error": error,
        }

    @staticmethod
    def _stored_object(raw: object, field: str) -> dict[str, Any] | None:
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise _error(
                "publication_state_corrupt",
                f"Task publication {field} is not text",
                500,
                {},
            )
        try:
            decoded: object = json.loads(raw)
        except ValueError as exc:
            raise _error(
                "publication_state_corrupt",
                f"Task publication {field} is not valid JSON",
                500,
                {},
            ) from exc
        if not isinstance(decoded, dict) or _json(decoded) != raw:
            raise _error(
                "publication_state_corrupt",
                f"Task publication {field} is not a canonical object",
                500,
                {},
            )
        return cast("dict[str, Any]", decoded)

    @staticmethod
    def _valid_block_error(value: dict[str, Any] | None) -> bool:
        if value is None or set(value) != {"error", "message", "details"}:
            return False
        details = value.get("details")
        return (
            value.get("error") == _BLOCK_ERROR
            and value.get("message") == _BLOCK_MESSAGE
            and isinstance(details, dict)
            and set(details) == {"reason"}
            and type(details.get("reason")) is str
            and details["reason"] in _BLOCK_REASONS
        )

    @staticmethod
    def _raise_blocked(envelope: dict[str, Any]) -> None:
        raise _error(
            _BLOCK_ERROR,
            _BLOCK_MESSAGE,
            409,
            {"publication": envelope},
        )

    @staticmethod
    def _recovery_cursor(value: object) -> str:
        if not isinstance(value, str):
            raise _error(
                "invalid_payload",
                "after must be an explicit tagged cursor or empty string",
                400,
                {"field": "after"},
            )
        if value == "":
            return value
        kind, separator, identity = value.partition(":")
        valid = False
        if separator != "":
            if kind == "native":
                try:
                    valid = parse_operation_id(identity) == identity
                except ServiceError:
                    valid = False
            elif kind == "legacy":
                valid = _OBSERVATION_ID.fullmatch(identity) is not None
        if not valid:
            raise _error(
                "invalid_payload",
                "after must be an explicit tagged publication-recovery cursor",
                400,
                {"field": "after"},
            )
        return value


__all__ = ["TaskPublicationWriter"]
