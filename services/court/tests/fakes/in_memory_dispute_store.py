"""In-memory dispute storage used for local tests and fallback mode."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, ClassVar

from service_commons.exceptions import ServiceError

from court_service.services.errors import DuplicateDisputeError


@dataclass
class _DisputeDbState:
    lock: RLock = field(default_factory=RLock)
    disputes: dict[str, dict[str, Any]] = field(default_factory=dict)
    dispute_id_by_task: dict[str, str] = field(default_factory=dict)
    votes_by_dispute: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    leases: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)


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
            "spec_quality": row.get("spec_quality"),
            "delivery_quality": row.get("delivery_quality"),
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
        """Test-only setter: force a status to arrange precondition scenarios."""
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            if row is None:
                return
            row["status"] = status

    def set_rebuttal_deadline(self, dispute_id: str, rebuttal_deadline: str) -> None:
        """Test-only setter: arrange a dispute's rebuttal deadline for a test.

        Used to set up an already-closed rebuttal window without waiting on a real
        clock (GAP-A8, T-039).
        """
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            if row is None:
                return
            row["rebuttal_deadline"] = rebuttal_deadline

    def _live_lease_owner(self, dispute_id: str) -> str | None:
        lease = self._state.leases.get(dispute_id)
        if lease is None or lease["released"]:
            return None
        return str(lease["lease_id"])

    def _require_lease(self, dispute_id: str, lease_id: str) -> dict[str, Any]:
        row = self._state.disputes.get(dispute_id)
        if row is None:
            raise ServiceError("dispute_not_found", "Dispute not found", 404, {})
        if row["status"] != "judging" or self._live_lease_owner(dispute_id) != lease_id:
            raise ServiceError(
                "ruling_lease_lost", "This ruling run no longer owns the dispute", 409, {}
            )
        return row

    def acquire_judging_lease(
        self, dispute_id: str, lease_id: str, lease_seconds: int
    ) -> dict[str, Any]:
        """Mirror the Gateway CAS: rebuttal_pending or expired/released judging only."""
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            if row is None:
                raise ServiceError("dispute_not_found", "Dispute not found", 404, {})
            now = datetime.now(UTC)
            reclaimed = False
            if row["status"] == "judging":
                lease = self._state.leases.get(dispute_id)
                if lease is not None and not lease["released"] and lease["expires"] > now:
                    raise ServiceError(
                        "dispute_not_ready",
                        "A ruling for this dispute is already in progress",
                        409,
                        {},
                    )
                reclaimed = True
            elif row["status"] == "ruled":
                raise ServiceError(
                    "dispute_already_ruled", "Dispute has already been ruled", 409, {}
                )
            elif row["status"] != "rebuttal_pending":
                raise ServiceError("dispute_not_ready", "Dispute is not ready for ruling", 409, {})
            row["status"] = "judging"
            self._state.leases[dispute_id] = {
                "lease_id": lease_id,
                "expires": now + timedelta(seconds=lease_seconds),
                "released": False,
            }
            return {
                "claim_id": dispute_id,
                "status": "judging",
                "lease_id": lease_id,
                "reclaimed": reclaimed,
                "decided": dispute_id in self._state.decisions,
            }

    def release_judging_lease(self, dispute_id: str, lease_id: str) -> None:
        with self._state.lock:
            row = self._require_lease(dispute_id, lease_id)
            self._state.leases[dispute_id]["released"] = True
            if dispute_id not in self._state.decisions:
                row["status"] = "rebuttal_pending"

    def claim_decision(
        self,
        dispute_id: str,
        lease_id: str,
        worker_pct: int,
        ruling_summary: str,
        votes: list[dict[str, Any]],
        request: dict[str, Any],
    ) -> None:
        with self._state.lock:
            self._require_lease(dispute_id, lease_id)
            if dispute_id in self._state.decisions:
                raise ServiceError("ruling_lease_lost", "A decision is already recorded", 409, {})
            self._state.decisions[dispute_id] = {
                "worker_pct": worker_pct,
                "summary": ruling_summary,
                "votes": [dict(vote) for vote in votes],
                "request": dict(request),
                "decided_at": self._now_iso(),
                "acknowledgments": set(),
            }

    def record_acknowledgment(self, dispute_id: str, lease_id: str, step: str) -> None:
        with self._state.lock:
            self._require_lease(dispute_id, lease_id)
            decision = self._state.decisions.get(dispute_id)
            if decision is None:
                raise ServiceError("ruling_lease_lost", "No decision is recorded", 409, {})
            decision["acknowledgments"].add(step)

    def complete_ruling(self, dispute_id: str, lease_id: str) -> None:
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            if row is not None and row["status"] == "ruled":
                return
            row = self._require_lease(dispute_id, lease_id)
            decision = self._state.decisions.get(dispute_id)
            if decision is None or decision["acknowledgments"] != {"task_board", "reputation"}:
                raise ServiceError(
                    "ruling_lease_lost", "Ruling deliveries are not all acknowledged", 409, {}
                )
            row["status"] = "ruled"
            row["worker_pct"] = decision["worker_pct"]
            row["ruling_summary"] = decision["summary"]
            row["ruled_at"] = decision["decided_at"]
            row["spec_quality"] = decision["request"].get("spec_quality")
            row["delivery_quality"] = decision["request"].get("delivery_quality")
            self._state.votes_by_dispute[dispute_id] = [
                {
                    "vote_id": self._new_vote_id(),
                    "dispute_id": dispute_id,
                    "judge_id": str(vote["judge_id"]),
                    "worker_pct": int(vote["worker_pct"]),
                    "spec_quality": vote.get("spec_quality"),
                    "spec_quality_explanation": vote.get("spec_quality_explanation"),
                    "delivery_quality": vote.get("delivery_quality"),
                    "delivery_quality_explanation": vote.get("delivery_quality_explanation"),
                    "reasoning": str(vote["reasoning"]),
                    "voted_at": str(vote["voted_at"]),
                }
                for vote in decision["votes"]
            ]

    def get_ruling_progress(self, dispute_id: str) -> dict[str, Any]:
        with self._state.lock:
            row = self._state.disputes.get(dispute_id)
            if row is None:
                raise ServiceError("dispute_not_found", "Dispute not found", 404, {})
            decision = self._state.decisions.get(dispute_id)
            return {
                "claim_id": dispute_id,
                "status": row["status"],
                "decision": None
                if decision is None
                else {
                    "worker_pct": decision["worker_pct"],
                    "summary": decision["summary"],
                    "request": dict(decision["request"]),
                },
                "acknowledgments": [] if decision is None else sorted(decision["acknowledgments"]),
            }

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
