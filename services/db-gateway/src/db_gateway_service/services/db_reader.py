"""Database reader — SQLite query executor for the Database Gateway."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    import sqlite3

_TASK_PROJECTION = """
WITH active_task_operations AS (
    SELECT task_id,
           json_object(
               'operation_id', operation_id,
               'command', command,
               'target_status', target_status,
               'target_lifecycle_version', target_lifecycle_version,
               'operation_state', operation_state,
               'effect_state', effect_state
           ) AS pending_transition_json
    FROM board_task_operations
    WHERE operation_state IN ('claimed', 'blocked')
),
legacy_reconciliation_rows AS (
    SELECT task_id,
           json_object(
               'observation_id', observation_id,
               'observation_kind', observation_kind,
               'effect_state', effect_state,
               'target_outcome_hint', target_outcome_hint
           ) AS reconciliation_json
    FROM board_legacy_effect_observations
    WHERE unresolved = 1
    ORDER BY task_id, observation_id
),
legacy_reconciliations AS (
    SELECT task_id,
           json_group_array(json(reconciliation_json)) AS reconciliations_json
    FROM legacy_reconciliation_rows
    GROUP BY task_id
)
SELECT t.*,
       c.task_id AS _contract_task_id,
       c.lifecycle_version,
       c.timing_contract_version,
       c.acceptance_window_seconds,
       c.bidding_cutoff_us,
       c.acceptance_cutoff_us,
       c.execution_cutoff_us,
       c.review_cutoff_us,
       c.accepted_reward,
       c.accepted_bid_amount,
       active.pending_transition_json AS _pending_transition_json,
       COALESCE(legacy.reconciliations_json, '[]') AS _legacy_reconciliations_json
FROM board_tasks AS t
LEFT JOIN board_task_contracts AS c ON c.task_id = t.task_id
LEFT JOIN active_task_operations AS active ON active.task_id = t.task_id
LEFT JOIN legacy_reconciliations AS legacy ON legacy.task_id = t.task_id
"""


def _projection_error(task_id: str) -> ServiceError:
    return ServiceError(
        "task_projection_invalid",
        "Task recovery projection is invalid",
        500,
        {"task_id": task_id},
    )


def _decode_json_object(raw: object, task_id: str) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise _projection_error(task_id)
    try:
        decoded: object = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise _projection_error(task_id) from exc
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        raise _projection_error(task_id)
    return {str(key): value for key, value in decoded.items()}


def _decode_json_array(raw: object, task_id: str) -> list[dict[str, Any]]:
    if not isinstance(raw, str):
        raise _projection_error(task_id)
    try:
        decoded: object = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise _projection_error(task_id) from exc
    if not isinstance(decoded, list):
        raise _projection_error(task_id)
    values: list[dict[str, Any]] = []
    for item in decoded:
        if not isinstance(item, dict) or any(not isinstance(key, str) for key in item):
            raise _projection_error(task_id)
        values.append({str(key): value for key, value in item.items()})
    return values


class DbReader:
    """
    SQLite query executor for read operations.

    Shares the same database connection as DbWriter.
    All methods are read-only SELECT queries.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Get a single agent by ID. Returns None if not found."""
        cursor = self._db.execute(
            "SELECT agent_id, name, public_key, registered_at "
            "FROM identity_agents WHERE agent_id = ?",
            (agent_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "agent_id": row[0],
            "name": row[1],
            "public_key": row[2],
            "registered_at": row[3],
        }

    def list_agents(self, public_key: str | None) -> list[dict[str, Any]]:
        """
        List all agents, optionally filtered by public_key.

        If public_key is provided, returns only the matching agent.
        Returns list of agent dicts sorted by registered_at.
        """
        if public_key is not None:
            cursor = self._db.execute(
                "SELECT agent_id, name, public_key, registered_at "
                "FROM identity_agents WHERE public_key = ? "
                "ORDER BY registered_at",
                (public_key,),
            )
        else:
            cursor = self._db.execute(
                "SELECT agent_id, name, public_key, registered_at "
                "FROM identity_agents ORDER BY registered_at"
            )
        rows = cursor.fetchall()
        return [
            {
                "agent_id": row[0],
                "name": row[1],
                "public_key": row[2],
                "registered_at": row[3],
            }
            for row in rows
        ]

    def count_agents(self) -> int:
        """Count total registered agents."""
        cursor = self._db.execute("SELECT COUNT(*) FROM identity_agents")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Bank
    # ------------------------------------------------------------------

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        """Get a bank account by ID."""
        cursor = self._db.execute(
            "SELECT account_id, balance, created_at FROM bank_accounts WHERE account_id = ?",
            (account_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"account_id": row[0], "balance": row[1], "created_at": row[2]}

    def get_transactions(self, account_id: str) -> list[dict[str, Any]]:
        """Get transaction history for an account."""
        cursor = self._db.execute(
            "SELECT tx_id, account_id, type, amount, balance_after, reference, timestamp, "
            "event_id "
            "FROM bank_transactions WHERE account_id = ? ORDER BY timestamp, tx_id",
            (account_id,),
        )
        return [
            {
                "tx_id": row[0],
                "account_id": row[1],
                "type": row[2],
                "amount": row[3],
                "balance_after": row[4],
                "reference": row[5],
                "timestamp": row[6],
                "event_id": row[7],
            }
            for row in cursor.fetchall()
        ]

    def count_accounts(self) -> int:
        """Count total bank accounts."""
        cursor = self._db.execute("SELECT COUNT(*) FROM bank_accounts")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def total_escrowed(self) -> int:
        """Sum locked integer amounts without limiting the aggregate to SQLite's range."""
        cursor = self._db.execute("SELECT amount FROM bank_escrow WHERE status = 'locked'")
        total = 0
        for row in cursor:
            total += row[0]
        return total

    def get_escrow(self, escrow_id: str) -> dict[str, Any] | None:
        """Get an escrow record by ID."""
        cursor = self._db.execute(
            "SELECT escrow_id, payer_account_id, amount, task_id, status, "
            "created_at, resolved_at FROM bank_escrow WHERE escrow_id = ?",
            (escrow_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "escrow_id": row[0],
            "payer_account_id": row[1],
            "amount": row[2],
            "task_id": row[3],
            "status": row[4],
            "created_at": row[5],
            "resolved_at": row[6],
        }

    # ------------------------------------------------------------------
    # Board
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a task by ID."""
        cursor = self._db.execute(
            f"{_TASK_PROJECTION} WHERE t.task_id = ?",  # nosec B608
            (task_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._task_row_to_dict(row)

    def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
        limit: int | None,
        offset: int | None,
    ) -> list[dict[str, Any]]:
        """List tasks with optional filters."""
        query = _TASK_PROJECTION
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("t.status = ?")
            params.append(status)
        if poster_id is not None:
            clauses.append("t.poster_id = ?")
            params.append(poster_id)
        if worker_id is not None:
            clauses.append("t.worker_id = ?")
            params.append(worker_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY t.created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        if offset is not None:
            query += " OFFSET ?"
            params.append(offset)
        rows = self._db.execute(query, params).fetchall()
        return [self._task_row_to_dict(row) for row in rows]

    @staticmethod
    def _task_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Decode one single-statement task/contract/recovery projection."""
        projected = dict(row)
        task_id = str(projected["task_id"])
        contract_task_id = projected.pop("_contract_task_id")
        if contract_task_id is None:
            raise ServiceError(
                "task_contract_missing",
                "Task contract sidecar is missing",
                500,
                {"task_id": task_id},
            )
        pending_raw = projected.pop("_pending_transition_json")
        legacy_raw = projected.pop("_legacy_reconciliations_json")
        pending = None if pending_raw is None else _decode_json_object(pending_raw, task_id)
        legacy = _decode_json_array(legacy_raw, task_id)
        projected["pending_transition"] = pending
        projected["legacy_reconciliations"] = legacy
        return projected

    def count_tasks(self) -> int:
        """Count total tasks."""
        cursor = self._db.execute("SELECT COUNT(*) FROM board_tasks")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def count_tasks_by_status(self) -> dict[str, int]:
        """Count tasks grouped by status."""
        rows = self._db.execute(
            "SELECT status, COUNT(*) FROM board_tasks GROUP BY status"
        ).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def get_bid(self, bid_id: str, task_id: str) -> dict[str, Any] | None:
        """Get a bid by ID and task_id."""
        cursor = self._db.execute(
            "SELECT bid_id, task_id, bidder_id, proposal, amount, submitted_at "
            "FROM board_bids WHERE bid_id = ? AND task_id = ?",
            (bid_id, task_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "bid_id": row[0],
            "task_id": row[1],
            "bidder_id": row[2],
            "proposal": row[3],
            "amount": row[4],
            "submitted_at": row[5],
        }

    def get_bids_for_task(self, task_id: str) -> list[dict[str, Any]]:
        """Get all bids for a task."""
        cursor = self._db.execute(
            "SELECT bid_id, task_id, bidder_id, proposal, amount, submitted_at "
            "FROM board_bids WHERE task_id = ? ORDER BY submitted_at",
            (task_id,),
        )
        return [
            {
                "bid_id": row[0],
                "task_id": row[1],
                "bidder_id": row[2],
                "proposal": row[3],
                "amount": row[4],
                "submitted_at": row[5],
            }
            for row in cursor.fetchall()
        ]

    def get_asset(self, asset_id: str, task_id: str) -> dict[str, Any] | None:
        """Get an asset by ID and task_id."""
        cursor = self._db.execute(
            "SELECT asset_id, task_id, uploader_id, filename, content_type, "
            "size_bytes, storage_path, content_hash, uploaded_at "
            "FROM board_assets WHERE asset_id = ? AND task_id = ?",
            (asset_id, task_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "asset_id": row[0],
            "task_id": row[1],
            "uploader_id": row[2],
            "filename": row[3],
            "content_type": row[4],
            "size_bytes": row[5],
            "storage_path": row[6],
            "content_hash": row[7],
            "uploaded_at": row[8],
        }

    def get_assets_for_task(self, task_id: str) -> list[dict[str, Any]]:
        """Get all assets for a task."""
        cursor = self._db.execute(
            "SELECT asset_id, task_id, uploader_id, filename, content_type, "
            "size_bytes, storage_path, content_hash, uploaded_at "
            "FROM board_assets WHERE task_id = ? ORDER BY uploaded_at",
            (task_id,),
        )
        return [
            {
                "asset_id": row[0],
                "task_id": row[1],
                "uploader_id": row[2],
                "filename": row[3],
                "content_type": row[4],
                "size_bytes": row[5],
                "storage_path": row[6],
                "content_hash": row[7],
                "uploaded_at": row[8],
            }
            for row in cursor.fetchall()
        ]

    def count_assets(self, task_id: str) -> int:
        """Count assets for a task."""
        cursor = self._db.execute(
            "SELECT COUNT(*) FROM board_assets WHERE task_id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Reputation
    # ------------------------------------------------------------------

    def get_feedback(self, feedback_id: str) -> dict[str, Any] | None:
        """Get feedback by ID."""
        cursor = self._db.execute(
            "SELECT feedback_id, task_id, from_agent_id, to_agent_id, role, "
            "category, rating, comment, submitted_at, visible "
            "FROM reputation_feedback WHERE feedback_id = ?",
            (feedback_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._feedback_row_to_dict(row)

    def get_feedback_by_task(self, task_id: str) -> list[dict[str, Any]]:
        """Get all feedback for a task."""
        cursor = self._db.execute(
            "SELECT feedback_id, task_id, from_agent_id, to_agent_id, role, "
            "category, rating, comment, submitted_at, visible "
            "FROM reputation_feedback WHERE task_id = ? ORDER BY submitted_at",
            (task_id,),
        )
        return [self._feedback_row_to_dict(row) for row in cursor.fetchall()]

    def get_feedback_by_agent(self, agent_id: str) -> list[dict[str, Any]]:
        """Get all feedback where to_agent_id matches."""
        cursor = self._db.execute(
            "SELECT feedback_id, task_id, from_agent_id, to_agent_id, role, "
            "category, rating, comment, submitted_at, visible "
            "FROM reputation_feedback WHERE to_agent_id = ? ORDER BY submitted_at",
            (agent_id,),
        )
        return [self._feedback_row_to_dict(row) for row in cursor.fetchall()]

    def count_feedback(self) -> int:
        """Count total feedback records."""
        cursor = self._db.execute("SELECT COUNT(*) FROM reputation_feedback")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def _feedback_row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert a feedback row into API response shape."""
        return {
            "feedback_id": row[0],
            "task_id": row[1],
            "from_agent_id": row[2],
            "to_agent_id": row[3],
            "role": row[4],
            "category": row[5],
            "rating": row[6],
            "comment": row[7],
            "submitted_at": row[8],
            "visible": bool(row[9]),
        }

    # ------------------------------------------------------------------
    # Court
    # ------------------------------------------------------------------

    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Get a court claim by ID."""
        cursor = self._db.execute(
            "SELECT claim_id, task_id, claimant_id, respondent_id, reason, "
            "status, rebuttal_deadline, filed_at "
            "FROM court_claims WHERE claim_id = ?",
            (claim_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "claim_id": row[0],
            "task_id": row[1],
            "claimant_id": row[2],
            "respondent_id": row[3],
            "reason": row[4],
            "status": row[5],
            "rebuttal_deadline": row[6],
            "filed_at": row[7],
        }

    def list_claims(
        self,
        status: str | None,
        claimant_id: str | None,
    ) -> list[dict[str, Any]]:
        """List claims with optional filters."""
        query = (
            "SELECT claim_id, task_id, claimant_id, respondent_id, reason, "
            "status, rebuttal_deadline, filed_at FROM court_claims"
        )
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if claimant_id is not None:
            clauses.append("claimant_id = ?")
            params.append(claimant_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY filed_at"
        return [
            {
                "claim_id": row[0],
                "task_id": row[1],
                "claimant_id": row[2],
                "respondent_id": row[3],
                "reason": row[4],
                "status": row[5],
                "rebuttal_deadline": row[6],
                "filed_at": row[7],
            }
            for row in self._db.execute(query, params).fetchall()
        ]

    def get_rebuttal(self, claim_id: str) -> dict[str, Any] | None:
        """Get rebuttal for a claim."""
        cursor = self._db.execute(
            "SELECT rebuttal_id, claim_id, agent_id, content, submitted_at "
            "FROM court_rebuttals WHERE claim_id = ?",
            (claim_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "rebuttal_id": row[0],
            "claim_id": row[1],
            "agent_id": row[2],
            "content": row[3],
            "submitted_at": row[4],
        }

    def get_ruling(self, claim_id: str) -> dict[str, Any] | None:
        """Get ruling for a claim."""
        cursor = self._db.execute(
            "SELECT ruling_id, claim_id, task_id, worker_pct, summary, "
            "judge_votes, ruled_at FROM court_rulings WHERE claim_id = ?",
            (claim_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "ruling_id": row[0],
            "claim_id": row[1],
            "task_id": row[2],
            "worker_pct": row[3],
            "summary": row[4],
            "judge_votes": row[5],
            "ruled_at": row[6],
        }

    def count_claims(self) -> int:
        """Count total claims."""
        cursor = self._db.execute("SELECT COUNT(*) FROM court_claims")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def count_active_claims(self) -> int:
        """Count claims not yet ruled."""
        cursor = self._db.execute("SELECT COUNT(*) FROM court_claims WHERE status != 'ruled'")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
