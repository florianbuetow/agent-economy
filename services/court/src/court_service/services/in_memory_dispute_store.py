"""In-memory dispute storage used for local tests and fallback mode."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any, ClassVar

from court_service.services.errors import DuplicateDisputeError


@dataclass
class _DisputeDbState:
    lock: RLock = field(default_factory=RLock)
    disputes: dict[str, dict[str, Any]] = field(default_factory=dict)
    dispute_id_by_task: dict[str, str] = field(default_factory=dict)
    votes_by_dispute: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class InMemoryDisputeStore:
    """Thread-safe in-memory storage for disputes and judge votes."""

    _DATABASES: ClassVar[dict[str, _DisputeDbState]] = {}
    _DATABASES_LOCK: ClassVar[RLock] = RLock()

    def __init__(self, db_path: str) -> None:
        with self._DATABASES_LOCK:
            if db_path not in self._DATABASES:
                self._DATABASES[db_path] = _DisputeDbState()
            self._state = self._DATABASES[db_path]

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _new_dispute_id() -> str:
        return f"disp-{uuid.uuid4()}"

    @staticmethod
    def _new_vote_id() -> str:
        return f"vote-{uuid.uuid4()}"

    def _build_dispute(self, row: dict[str, Any], votes: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "dispute_id": str(row["dispute_id"]),
            "task_id": str(row["task_id"]),
            "claimant_id": str(row["claimant_id"]),
            "respondent_id": str(row["respondent_id"]),
            "claim": str(row["claim"]),
            "rebuttal": row.get("rebuttal"),
            "status": str(row["status"]),
            "rebuttal_deadline": str(row["rebuttal_deadline"]),
            "worker_pct": row.get("worker_pct"),
            "ruling_summary": row.get("ruling_summary"),
            "escrow_id": str(row["escrow_id"]),
            "filed_at": str(row["filed_at"]),
            "rebutted_at": row.get("rebutted_at"),
            "ruled_at": row.get("ruled_at"),
            "votes": votes,
        }

    def get_dispute_row(self, dispute_id: str) -> dict[str, Any] | None:
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            return None if row is None else dict(row)

    def get_votes(self, dispute_id: str) -> list[dict[str, Any]]:
        with self._state.lock:
            votes = self._state.votes_by_dispute.get(dispute_id, [])
            return [dict(vote) for vote in votes]

    def insert_dispute(
        self,
        task_id: str,
        claimant_id: str,
        respondent_id: str,
        claim: str,
        escrow_id: str,
        rebuttal_deadline: str,
    ) -> dict[str, Any]:
        with self._state.lock:
            if task_id in self._state.dispute_id_by_task:
                raise DuplicateDisputeError(f"A dispute already exists for task_id={task_id}")

            dispute_id = self._new_dispute_id()
            filed_at = self._now_iso()
            row = {
                "dispute_id": dispute_id,
                "task_id": task_id,
                "claimant_id": claimant_id,
                "respondent_id": respondent_id,
                "claim": claim,
                "rebuttal": None,
                "status": "rebuttal_pending",
                "rebuttal_deadline": rebuttal_deadline,
                "worker_pct": None,
                "ruling_summary": None,
                "escrow_id": escrow_id,
                "filed_at": filed_at,
                "rebutted_at": None,
                "ruled_at": None,
            }
            self._state.disputes[dispute_id] = row
            self._state.dispute_id_by_task[task_id] = dispute_id
            self._state.votes_by_dispute[dispute_id] = []
            return self._build_dispute(row, [])

    def get_dispute(self, dispute_id: str) -> dict[str, Any] | None:
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            if row is None:
                return None
            votes = [dict(vote) for vote in self._state.votes_by_dispute.get(dispute_id, [])]
            return self._build_dispute(row, votes)

    def update_rebuttal(self, dispute_id: str, rebuttal: str) -> None:
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            if row is None:
                return
            row["rebuttal"] = rebuttal
            row["rebutted_at"] = self._now_iso()

    def set_status(self, dispute_id: str, status: str) -> None:
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            if row is None:
                return
            row["status"] = status

    def revert_to_rebuttal_pending(self, dispute_id: str) -> None:
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            if row is None:
                return
            row["status"] = "rebuttal_pending"
            row["worker_pct"] = None
            row["ruling_summary"] = None
            row["ruled_at"] = None
            self._state.votes_by_dispute[dispute_id] = []

    def persist_ruling(
        self,
        dispute_id: str,
        worker_pct: int,
        ruling_summary: str,
        votes: list[dict[str, Any]],
    ) -> None:
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            if row is None:
                return

            row["status"] = "ruled"
            row["worker_pct"] = worker_pct
            row["ruling_summary"] = ruling_summary
            row["ruled_at"] = self._now_iso()

            persisted_votes: list[dict[str, Any]] = []
            for vote in votes:
                persisted_votes.append(
                    {
                        "vote_id": self._new_vote_id(),
                        "dispute_id": dispute_id,
                        "judge_id": str(vote["judge_id"]),
                        "worker_pct": int(vote["worker_pct"]),
                        "reasoning": str(vote["reasoning"]),
                        "voted_at": str(vote["voted_at"]),
                    }
                )
            self._state.votes_by_dispute[dispute_id] = persisted_votes

    def list_disputes(self, task_id: str | None, status: str | None) -> list[dict[str, Any]]:
        with self._state.lock:
            rows = list(self._state.disputes.values())
            if task_id is not None:
                rows = [row for row in rows if str(row["task_id"]) == task_id]
            if status is not None:
                rows = [row for row in rows if str(row["status"]) == status]
            rows.sort(key=lambda row: str(row["filed_at"]))

            result: list[dict[str, Any]] = []
            for row in rows:
                result.append(
                    {
                        "dispute_id": str(row["dispute_id"]),
                        "task_id": str(row["task_id"]),
                        "claimant_id": str(row["claimant_id"]),
                        "respondent_id": str(row["respondent_id"]),
                        "status": str(row["status"]),
                        "worker_pct": row.get("worker_pct"),
                        "filed_at": str(row["filed_at"]),
                        "ruled_at": row.get("ruled_at"),
                    }
                )
            return result

    def count_disputes(self) -> int:
        with self._state.lock:
            return len(self._state.disputes)

    def count_active(self) -> int:
        with self._state.lock:
            return sum(1 for row in self._state.disputes.values() if str(row["status"]) != "ruled")

    def close(self) -> None:
        """No-op close for API compatibility."""
