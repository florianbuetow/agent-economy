"""Receipt-backed reconciliation for migrated posting-orphan refunds."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final, Literal, Never, cast

from service_commons.exceptions import ServiceError

from db_gateway_service.financial_requests import parse_canonical_request
from db_gateway_service.publication_audit import audit_publications
from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.task_publication_financial import (
    PublicationEvidenceError,
    inspect_lock_facts,
)
from db_gateway_service.task_time import epoch_us_to_utc_text, parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    import sqlite3

    from db_gateway_service.financial_requests import FinancialRequest

LegacyBlockReason = Literal[
    "observation_invalid",
    "receipt_invalid",
    "escrow_invalid",
    "settlement_conflict",
    "task_escrow_reference_conflict",
]

_OBSERVATION_SELECT: Final[str] = (
    "SELECT observation_id,observation_contract_version,escrow_id,task_id,poster_id,"
    "amount,observed_fact_json,observed_fact_sha256,caller_kid,state,refund_operation_id,"
    "refund_request_json,refund_request_sha256,refund_receipt_event_id,refunded_at_us,"
    "result_json,error_json FROM board_legacy_posting_observations WHERE observation_id=?"
)
_BLOCK_ERROR: Final[str] = "posting_refund_integrity_blocked"
_BLOCK_MESSAGE: Final[str] = "Posting refund evidence requires reconciliation"
_BLOCK_REASONS: Final[frozenset[str]] = frozenset(
    {
        "observation_invalid",
        "receipt_invalid",
        "escrow_invalid",
        "settlement_conflict",
        "task_escrow_reference_conflict",
    }
)
_RELEVANT_GLOBAL_AUDIT_CODES: Final[frozenset[str]] = frozenset(
    {
        "publication_cutover_row_count",
        "publication_cutover_invalid",
        "legacy_task_attestation_mismatch",
        "legacy_observation_attestation_mismatch",
    }
)
_RELEASE_RESULT_JSON: Final[str] = '{"escrow_status_at_commit":"released"}'


class _LegacyEvidenceError(Exception):
    def __init__(self, reason: LegacyBlockReason) -> None:
        self.reason: LegacyBlockReason = reason
        super().__init__(reason)


def _fail(reason: LegacyBlockReason) -> Never:
    raise _LegacyEvidenceError(reason)


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


class LegacyPostingWriter:
    """Finalize only the immutable refund intents created by verified cutover."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def reconcile(self, observation_id: str, caller_kid: str) -> dict[str, Any]:
        """Reconcile one observation exclusively from persisted authoritative facts."""
        owner = self._caller_kid(caller_kid)
        cursor = self._db.cursor()
        outcome: dict[str, Any] | None = None
        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = self._owned_observation(cursor, observation_id, owner)
            if row[9] == "blocked_integrity":
                outcome = self._envelope(row)
                self._db.rollback()
                self._raise_blocked(outcome)

            reason = self._observation_integrity_reason(row)
            if reason is None:
                reason = self._task_reference_reason(cursor, row)
            if reason is not None:
                outcome = self._block(cursor, row, reason)
                self._db.commit()
                self._raise_blocked(outcome)

            if row[9] == "refunded":
                reason = self._refunded_integrity_reason(cursor, row)
                if reason is not None:
                    raise _error(
                        "posting_observation_corrupt",
                        "Completed posting observation is inconsistent",
                        500,
                        {"observation_id": str(row[0]), "reason": reason},
                    )
                outcome = self._envelope(row)
                self._db.rollback()
                return outcome

            reason, receipt = self._pending_evidence(cursor, row)
            if reason is not None:
                outcome = self._block(cursor, row, reason)
                self._db.commit()
                self._raise_blocked(outcome)
            if receipt is None:
                outcome = self._envelope(row)
                self._db.rollback()
                return outcome

            event_id, committed_at, result_json = receipt
            refunded_at_us = parse_utc_text_to_epoch_us(committed_at)
            cursor.execute(
                "UPDATE board_legacy_posting_observations SET state='refunded',"
                "refund_receipt_event_id=?,refunded_at_us=?,result_json=?,error_json=NULL "
                "WHERE observation_id=? AND state='refund_pending'",
                (event_id, refunded_at_us, result_json, row[0]),
            )
            if cursor.rowcount != 1:
                raise _error(
                    "posting_observation_corrupt",
                    "Posting observation changed during reconciliation",
                    500,
                    {"observation_id": str(row[0])},
                )
            outcome = self._envelope(self._required_observation(cursor, str(row[0])))
            self._db.commit()
            return outcome
        except ServiceError:
            if self._db.in_transaction:
                self._db.rollback()
            raise
        except Exception:
            if self._db.in_transaction:
                self._db.rollback()
            raise

    @staticmethod
    def _caller_kid(value: object) -> str:
        if not isinstance(value, str) or value == "":
            raise _error("forbidden", "Verified Gateway caller is required", 403, {})
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _error("forbidden", "Verified Gateway caller is invalid", 403, {}) from exc
        return value

    def _observation(self, cursor: sqlite3.Cursor, observation_id: str) -> tuple[Any, ...] | None:
        row = cursor.execute(_OBSERVATION_SELECT, (observation_id,)).fetchone()
        return None if row is None else tuple(row)

    def _required_observation(self, cursor: sqlite3.Cursor, observation_id: str) -> tuple[Any, ...]:
        row = self._observation(cursor, observation_id)
        if row is None:
            raise _error(
                "posting_observation_corrupt",
                "Posting observation disappeared during reconciliation",
                500,
                {"observation_id": observation_id},
            )
        return row

    def _owned_observation(
        self, cursor: sqlite3.Cursor, observation_id: str, caller_kid: str
    ) -> tuple[Any, ...]:
        row = self._observation(cursor, observation_id)
        if row is None:
            raise _error(
                "posting_observation_not_found",
                "Posting observation not found",
                404,
                {},
            )
        if row[8] != caller_kid:
            raise _error(
                "operation_caller_mismatch",
                "Posting observation belongs to another verified Gateway caller",
                403,
                {"observation_id": observation_id},
            )
        return row

    def _observation_integrity_reason(self, row: tuple[Any, ...]) -> LegacyBlockReason | None:
        result = audit_publications(self._db)
        relevant = [
            finding
            for finding in result.findings
            if finding.identity == row[0] or finding.code in _RELEVANT_GLOBAL_AUDIT_CODES
        ]
        if not relevant:
            return None
        if all(finding.code == "legacy_refund_receipt_conflict" for finding in relevant):
            return "receipt_invalid"
        return "observation_invalid"

    @staticmethod
    def _task_reference_reason(
        cursor: sqlite3.Cursor, row: tuple[Any, ...]
    ) -> LegacyBlockReason | None:
        referenced = cursor.execute(
            "SELECT 1 FROM board_tasks WHERE escrow_id=? LIMIT 1", (row[2],)
        ).fetchone()
        return "task_escrow_reference_conflict" if referenced is not None else None

    def _refunded_integrity_reason(
        self, cursor: sqlite3.Cursor, row: tuple[Any, ...]
    ) -> LegacyBlockReason | None:
        if row[10] is None:
            return None
        fact = self._stored_object(row[6])
        origin = None if fact is None else fact.get("existing_refund_provenance")
        if fact is None:
            return "observation_invalid"
        refund_origin = fact.get("refund_origin")
        if refund_origin == "new_refund":
            valid_origin = origin is None
            require_new_identity = True
        else:
            valid_origin = bool(
                refund_origin == "existing_refund"
                and isinstance(origin, dict)
                and origin.get("kind") == "native"
                and origin.get("operation_id") == row[10]
                and origin.get("request_sha256") == row[12]
                and origin.get("event_id") == row[13]
            )
            require_new_identity = False
        if not valid_origin:
            return "observation_invalid"
        reason, receipt = self._release_receipt(
            cursor, row, require_new_identity=require_new_identity
        )
        if reason is not None or receipt is None:
            return reason or "receipt_invalid"
        event_id, committed_at, result_json = receipt
        try:
            committed_at_us = parse_utc_text_to_epoch_us(committed_at)
        except ValueError:
            committed_at_us = None
        if committed_at_us is None or (row[13], row[14], row[15]) != (
            event_id,
            committed_at_us,
            result_json,
        ):
            return "receipt_invalid"
        return None

    def _pending_evidence(
        self, cursor: sqlite3.Cursor, row: tuple[Any, ...]
    ) -> tuple[LegacyBlockReason | None, tuple[int, str, str] | None]:
        fact = self._stored_object(row[6])
        if fact is None or fact.get("refund_origin") != "new_refund":
            return "observation_invalid", None
        settlements = cursor.execute(
            "SELECT operation_id FROM bank_operation_receipts WHERE escrow_id=? "
            "AND operation_kind IN ('escrow_release','escrow_split') ORDER BY operation_id",
            (row[2],),
        ).fetchall()
        matching = cursor.execute(
            "SELECT 1 FROM bank_operation_receipts WHERE operation_id=?",
            (row[10],),
        ).fetchone()
        require_locked = matching is None and not settlements
        reason = self._original_lock_reason(cursor, row, fact, require_locked=require_locked)
        if reason is not None:
            return reason, None
        return self._settlement_evidence(cursor, row, settlements, matching)

    def _settlement_evidence(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        settlements: list[sqlite3.Row] | list[tuple[Any, ...]],
        matching: sqlite3.Row | tuple[Any, ...] | None,
    ) -> tuple[LegacyBlockReason | None, tuple[int, str, str] | None]:
        if matching is None:
            if settlements:
                return "settlement_conflict", None
            escrow = self._current_escrow(cursor, row)
            if escrow is None:
                return "escrow_invalid", None
            if escrow[4] != "locked" or escrow[6] is not None:
                return "settlement_conflict", None
            return None, None
        if len(settlements) != 1 or settlements[0][0] != row[10]:
            return "settlement_conflict", None
        return self._release_receipt(cursor, row, require_new_identity=True)

    def _original_lock_reason(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        fact: dict[str, Any],
        *,
        require_locked: bool,
    ) -> LegacyBlockReason | None:
        provenance = fact.get("lock_provenance")
        if not isinstance(provenance, dict):
            return "observation_invalid"
        if provenance.get("kind") != "native":
            return None
        operation_id = provenance.get("operation_id")
        if not isinstance(operation_id, str):
            return "observation_invalid"
        try:
            proof = inspect_lock_facts(
                cursor,
                operation_id=operation_id,
                payer_account_id=str(row[4]),
                task_id=str(row[3]),
                amount=cast("int", row[5]),
                require_locked=require_locked,
            )
        except PublicationEvidenceError as exc:
            reason: LegacyBlockReason = "observation_invalid"
            if exc.reason == "settlement_conflict":
                reason = "settlement_conflict"
            elif exc.reason == "escrow_invalid":
                reason = "escrow_invalid"
            return reason
        if (
            proof is None
            or proof.escrow_id != row[2]
            or proof.request_sha256 != provenance.get("request_sha256")
            or proof.receipt_event_id != provenance.get("event_id")
        ):
            return "observation_invalid"
        return None

    @staticmethod
    def _current_escrow(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...] | None:
        escrow = cursor.execute(
            "SELECT escrow_id,payer_account_id,amount,task_id,status,created_at,"
            "resolved_at,event_id "
            "FROM bank_escrow WHERE escrow_id=?",
            (row[2],),
        ).fetchone()
        if escrow is None:
            return None
        values = tuple(escrow)
        if (
            values[0] != row[2]
            or values[1] != row[4]
            or type(values[2]) is not int
            or values[2] != row[5]
            or values[3] != row[3]
        ):
            return None
        return values

    def _release_receipt(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        *,
        require_new_identity: bool,
    ) -> tuple[LegacyBlockReason | None, tuple[int, str, str] | None]:
        try:
            expected = self._verified_refund_request(row, require_new_identity=require_new_identity)
            event_id, committed_at, result_json = self._verified_receipt_header(
                cursor, row, expected
            )
            self._verify_release_state(cursor, row, committed_at)
            self._verify_release_event(cursor, row, event_id, committed_at)
            self._verify_release_leg(cursor, row, event_id, committed_at)
        except _LegacyEvidenceError as exc:
            return exc.reason, None
        return None, (event_id, committed_at, result_json)

    @staticmethod
    def _verified_refund_request(
        row: tuple[Any, ...], *, require_new_identity: bool
    ) -> FinancialRequest:
        try:
            request = parse_canonical_request(str(row[11]))
            payload = json.loads(request.canonical_json)
        except (TypeError, ValueError):
            _fail("observation_invalid")
        expected_source = f"task:{row[3]}:posting_refund:{row[2]}"
        if (
            request.operation_kind != "escrow_release"
            or payload.get("escrow_id") != row[2]
            or payload.get("recipient_account_id") != row[4]
            or (require_new_identity and payload.get("source_reference") != expected_source)
            or row[12] != request.request_sha256
            or not isinstance(row[10], str)
        ):
            _fail("observation_invalid")
        return request

    @staticmethod
    def _verified_receipt_header(
        cursor: sqlite3.Cursor, row: tuple[Any, ...], expected: FinancialRequest
    ) -> tuple[int, str, str]:
        receipt = cursor.execute(
            "SELECT receipt_version,operation_kind,canonical_request_json,request_sha256,"
            "source_reference,committed_at,event_id,escrow_id,result_json "
            "FROM bank_operation_receipts WHERE operation_id=?",
            (row[10],),
        ).fetchone()
        if receipt is None:
            _fail("receipt_invalid")
        values = tuple(receipt)
        event_id = values[6]
        committed_at = values[5]
        if (
            type(values[0]) is not int
            or values[0] != 1
            or values[1] != "escrow_release"
            or values[2] != expected.canonical_json
            or values[3] != expected.request_sha256
            or values[4] != expected.source_reference
            or not isinstance(committed_at, str)
            or type(event_id) is not int
            or not 1 <= event_id <= SQLITE_MAX_INTEGER
            or values[7] != row[2]
            or values[8] != _RELEASE_RESULT_JSON
        ):
            _fail("receipt_invalid")
        try:
            parse_utc_text_to_epoch_us(committed_at)
            result = json.loads(str(values[8]))
        except (TypeError, ValueError):
            _fail("receipt_invalid")
        if result != {"escrow_status_at_commit": "released"}:
            _fail("receipt_invalid")
        return event_id, committed_at, str(values[8])

    def _verify_release_state(
        self, cursor: sqlite3.Cursor, row: tuple[Any, ...], committed_at: str
    ) -> None:
        escrow = self._current_escrow(cursor, row)
        if escrow is None:
            _fail("escrow_invalid")
        if escrow[4] != "released" or escrow[6] != committed_at:
            _fail("settlement_conflict")

    @staticmethod
    def _verify_release_event(
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        event_id: int,
        committed_at: str,
    ) -> None:
        event = cursor.execute(
            "SELECT event_source,event_type,timestamp,task_id,agent_id "
            "FROM events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if event is None or tuple(event) != (
            "central-bank",
            "escrow.released",
            committed_at,
            row[3],
            row[4],
        ):
            _fail("receipt_invalid")

    @staticmethod
    def _verify_release_leg(
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        event_id: int,
        committed_at: str,
    ) -> None:
        legs = cursor.execute(
            "SELECT leg_index,role,account_id,direction,amount,transaction_id,balance_after "
            "FROM bank_operation_legs WHERE operation_id=? ORDER BY leg_index",
            (row[10],),
        ).fetchall()
        if len(legs) != 1:
            _fail("receipt_invalid")
        leg = tuple(legs[0])
        if (
            type(leg[0]) is not int
            or leg[0] != 0
            or leg[1:5] != ("recipient", row[4], "credit", row[5])
            or not isinstance(leg[5], str)
            or leg[5] == ""
            or type(leg[6]) is not int
            or not 0 <= leg[6] <= SQLITE_MAX_INTEGER
        ):
            _fail("receipt_invalid")
        transaction = cursor.execute(
            "SELECT account_id,type,amount,balance_after,reference,event_id,timestamp "
            "FROM bank_transactions WHERE tx_id=?",
            (leg[5],),
        ).fetchone()
        if transaction is None or tuple(transaction) != (
            row[4],
            "escrow_release",
            row[5],
            leg[6],
            row[2],
            event_id,
            committed_at,
        ):
            _fail("receipt_invalid")
        account = cursor.execute(
            "SELECT 1 FROM bank_accounts WHERE account_id=?", (row[4],)
        ).fetchone()
        if account is None:
            _fail("receipt_invalid")

    def _block(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        reason: LegacyBlockReason,
    ) -> dict[str, Any]:
        stored_error = {
            "error": _BLOCK_ERROR,
            "message": _BLOCK_MESSAGE,
            "details": {"reason": reason},
        }
        cursor.execute(
            "UPDATE board_legacy_posting_observations SET state='blocked_integrity',"
            "refund_receipt_event_id=NULL,refunded_at_us=NULL,result_json=NULL,error_json=? "
            "WHERE observation_id=? AND state='refund_pending'",
            (_json(stored_error), row[0]),
        )
        if cursor.rowcount != 1:
            raise _error(
                "posting_observation_corrupt",
                "Posting observation could not record its integrity block",
                500,
                {"observation_id": str(row[0])},
            )
        return self._envelope(self._required_observation(cursor, str(row[0])))

    def _envelope(self, row: tuple[Any, ...]) -> dict[str, Any]:
        state = row[9]
        if row[1] != 1 or state not in {"refund_pending", "refunded", "blocked_integrity"}:
            raise _error(
                "posting_observation_corrupt",
                "Posting observation has an invalid persisted state",
                500,
                {"observation_id": str(row[0])},
            )
        request = self._stored_object(row[11])
        result = self._stored_object(row[15])
        error = self._stored_object(row[16])
        if state == "refund_pending":
            valid = (
                isinstance(row[10], str)
                and request is not None
                and isinstance(row[12], str)
                and row[13:17] == (None, None, None, None)
            )
        elif state == "refunded":
            legacy_only = row[10:14] == (None, None, None, None)
            native = (
                isinstance(row[10], str)
                and request is not None
                and isinstance(row[12], str)
                and type(row[13]) is int
            )
            valid = (
                (legacy_only or native)
                and type(row[14]) is int
                and result is not None
                and error is None
            )
        else:
            valid = (
                row[13:16] == (None, None, None)
                and self._valid_block_error(error)
                and (
                    (row[10:13] == (None, None, None))
                    or (
                        isinstance(row[10], str)
                        and request is not None
                        and isinstance(row[12], str)
                    )
                )
            )
        if not valid:
            raise _error(
                "posting_observation_corrupt",
                "Posting observation state fields are inconsistent",
                500,
                {"observation_id": str(row[0])},
            )
        return {
            "protocol_version": 1,
            "observation_id": str(row[0]),
            "task_id": str(row[3]),
            "escrow_id": str(row[2]),
            "poster_id": str(row[4]),
            "amount": row[5],
            "observed_fact_sha256": str(row[7]),
            "observation_state": str(state),
            "refund_operation_id": row[10],
            "refund_request": request,
            "refund_request_sha256": row[12],
            "refund_receipt_event_id": row[13],
            "refunded_at": None if row[14] is None else self._timestamp(row[14]),
            "result": result,
            "error": error,
        }

    @staticmethod
    def _stored_object(raw: object) -> dict[str, Any] | None:
        if raw is None:
            return None
        if not isinstance(raw, str):
            return None
        try:
            value: object = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(value, dict) or _json(value) != raw:
            return None
        return cast("dict[str, Any]", value)

    @staticmethod
    def _timestamp(value: object) -> str:
        rendered = epoch_us_to_utc_text(value)
        head, separator, fraction = rendered.partition(".")
        if separator == "":
            return rendered[:-1] + ".000000Z"
        return head + "." + fraction[:-1].ljust(6, "0") + "Z"

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
            {"observation": envelope},
        )
