"""Test-only writer for constructing frozen pre-MP-05 schema-v3 histories.

This adapter is not a production compatibility path. It admits only the exact
v3 catalog, reruns the historical money/receipt/task audits, and exposes only
the retired v3 task-creation transaction needed by migration fixtures.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from service_commons.exceptions import ServiceError

from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaClassification, SchemaState, classify_schema
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.db_writer_helpers import (
    insert_event,
    is_foreign_key_violation,
)
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_time import add_seconds_to_epoch_us, parse_utc_text_to_epoch_us

_HISTORICAL_NULL_FIELDS = frozenset(
    {
        "execution_deadline",
        "review_deadline",
        "worker_id",
        "accepted_bid_id",
        "dispute_reason",
        "dispute_id",
        "rebuttal_deadline",
        "rebuttal_submitted_at",
        "ruling_id",
        "worker_pct",
        "ruling_summary",
        "accepted_at",
        "submitted_at",
        "approved_at",
        "cancelled_at",
        "disputed_at",
        "ruled_at",
        "expired_at",
        "execution_cutoff_us",
        "review_cutoff_us",
        "accepted_reward",
        "accepted_bid_amount",
    }
)


class HistoricalV3Writer(DbWriter):
    """Exact-v3 fixture writer isolated from current production admission."""

    @staticmethod
    def _admit_existing_schema(classification: SchemaClassification) -> None:
        if classification.state is SchemaState.CURRENT_V3:
            return
        raise ServiceError(
            "historical_fixture_schema_invalid",
            "Historical fixture writer requires the exact frozen v3 schema",
            500,
            {
                "state": classification.state.value,
                "version": classification.version,
                "signature_sha256": classification.signature_sha256,
            },
        )

    def _admit_receipt_ownership(self) -> None:
        money = audit_money(self.connection)
        if not money.financially_clean:
            raise ServiceError(
                "historical_fixture_money_invalid",
                "Historical fixture money audit failed",
                500,
                {"finding_codes": sorted({finding.code for finding in money.findings})},
            )
        receipts = audit_receipt_ownership(self.connection)
        if receipts.ownership_clean:
            return
        raise ServiceError(
            "historical_fixture_receipts_invalid",
            "Historical fixture receipt audit failed",
            500,
            {"finding_codes": sorted({finding.code for finding in receipts.findings})},
        )

    def _admit_task_contracts(self) -> None:
        contracts = audit_task_contracts(self.connection)
        if contracts.contracts_clean:
            return
        raise ServiceError(
            "historical_fixture_tasks_invalid",
            "Historical fixture task-contract audit failed",
            500,
            {"finding_codes": sorted({finding.code for finding in contracts.findings})},
        )

    def _admit_publications(self) -> None:
        """V3 has no publication tables; bypass only after an exact-v3 recheck."""
        classification = classify_schema(self.connection)
        self._admit_existing_schema(classification)

    def _admit_effect_recovery(self) -> None:
        """V3 predates recovery deliveries; bypass only for the frozen exact catalog."""
        classification = classify_schema(self.connection)
        self._admit_existing_schema(classification)

    def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        """Run the frozen timing-v2 task creation transaction on exact v3."""
        self._admit_existing_schema(classify_schema(self.connection))
        _validate_historical_task(data)
        acceptance_window, bidding_cutoff, acceptance_cutoff = _historical_cutoffs(data)
        cursor = self.connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO board_tasks "
                "(task_id, poster_id, title, spec, reward, status, "
                "bidding_deadline_seconds, deadline_seconds, review_deadline_seconds, "
                "bidding_deadline, bid_count, escrow_pending, escrow_id, created_at, "
                "dispute_id) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, 0, 0, ?, ?, NULL)",
                (
                    data["task_id"],
                    data["poster_id"],
                    data["title"],
                    data["spec"],
                    data["reward"],
                    data["bidding_deadline_seconds"],
                    data["deadline_seconds"],
                    data["review_deadline_seconds"],
                    data["bidding_deadline"],
                    data["escrow_id"],
                    data["created_at"],
                ),
            )
            cursor.execute(
                "INSERT INTO board_task_contracts "
                "(task_id, lifecycle_version, timing_contract_version, "
                "acceptance_window_seconds, bidding_cutoff_us, acceptance_cutoff_us, "
                "execution_cutoff_us, review_cutoff_us, accepted_reward, "
                "accepted_bid_amount) VALUES (?, 0, 2, ?, ?, ?, NULL, NULL, NULL, NULL)",
                (data["task_id"], acceptance_window, bidding_cutoff, acceptance_cutoff),
            )
            event_id = insert_event(cursor, data["event"])
            self.connection.commit()
            return {"task_id": data["task_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "task_exists",
                "Task with this task_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            self.connection.rollback()
            raise


def open_historical_v3_writer(
    database: str,
    *,
    busy_timeout_ms: int,
    journal_mode: str,
    current_task_board_caller_kid: str,
) -> HistoricalV3Writer:
    """Open one owned exact-v3 fixture without production fallback admission."""
    return HistoricalV3Writer(
        db_path=database,
        busy_timeout_ms=busy_timeout_ms,
        journal_mode=journal_mode,
        schema_sql=None,
        current_task_board_caller_kid=current_task_board_caller_kid,
    )


def _validate_historical_task(data: dict[str, Any]) -> None:
    if "status" in data and (type(data["status"]) is not str or data["status"] != "open"):
        raise ServiceError(
            "invalid_payload",
            "Task publication status must be open",
            400,
            {"field": "status"},
        )
    for field in ("bid_count", "escrow_pending", "lifecycle_version"):
        if field in data and (type(data[field]) is not int or data[field] != 0):
            raise ServiceError(
                "invalid_payload",
                f"Task publication {field} must be integer zero",
                400,
                {"field": field},
            )
    for field in _HISTORICAL_NULL_FIELDS:
        if field in data and data[field] is not None:
            raise ServiceError(
                "invalid_payload",
                f"Task publication {field} must be null",
                400,
                {"field": field},
            )


def _historical_cutoffs(data: dict[str, Any]) -> tuple[int, int, int]:
    for name in (
        "bidding_deadline_seconds",
        "deadline_seconds",
        "review_deadline_seconds",
        "acceptance_window_seconds",
    ):
        if name not in data:
            raise ServiceError(
                "missing_field",
                f"Missing required field: {name}",
                400,
                {"field": name},
            )
        if type(data[name]) is not int or data[name] <= 0:
            raise ServiceError(
                "invalid_deadline",
                f"{name} must be a positive integer",
                400,
                {"field": name},
            )
    try:
        created_us = parse_utc_text_to_epoch_us(data["created_at"])
        bidding_us = add_seconds_to_epoch_us(created_us, data["bidding_deadline_seconds"])
        supplied_bidding_us = parse_utc_text_to_epoch_us(data["bidding_deadline"])
        acceptance_us = add_seconds_to_epoch_us(bidding_us, data["acceptance_window_seconds"])
    except ValueError as exc:
        raise ServiceError(
            "invalid_deadline",
            "Task timing is invalid or out of range",
            400,
            {},
        ) from exc
    if bidding_us != supplied_bidding_us:
        raise ServiceError(
            "invalid_deadline",
            "Bidding cutoff does not match the published duration",
            400,
            {},
        )
    return data["acceptance_window_seconds"], bidding_us, acceptance_us


__all__ = ["HistoricalV3Writer", "open_historical_v3_writer"]
