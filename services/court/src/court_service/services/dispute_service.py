"""Dispute storage and business logic."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any

from service_commons.exceptions import ServiceError

from court_service.judges import DisputeContext, JudgeVote


class DisputeService:
    """Manage dispute lifecycle and ruling orchestration."""

    def __init__(self, db_path: str) -> None:
        self._lock = RLock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS disputes (
                    dispute_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    claimant_id TEXT NOT NULL,
                    respondent_id TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    rebuttal TEXT,
                    status TEXT NOT NULL DEFAULT 'rebuttal_pending',
                    rebuttal_deadline TEXT NOT NULL,
                    worker_pct INTEGER,
                    ruling_summary TEXT,
                    escrow_id TEXT NOT NULL,
                    filed_at TEXT NOT NULL,
                    rebutted_at TEXT,
                    ruled_at TEXT
                );

                CREATE TABLE IF NOT EXISTS votes (
                    vote_id TEXT PRIMARY KEY,
                    dispute_id TEXT NOT NULL REFERENCES disputes(dispute_id),
                    judge_id TEXT NOT NULL,
                    worker_pct INTEGER NOT NULL,
                    reasoning TEXT NOT NULL,
                    voted_at TEXT NOT NULL,
                    UNIQUE(dispute_id, judge_id)
                );
                """
            )
            self._db.commit()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _new_dispute_id() -> str:
        return f"disp-{uuid.uuid4()}"

    @staticmethod
    def _new_vote_id() -> str:
        return f"vote-{uuid.uuid4()}"

    def _row_to_dispute(self, row: sqlite3.Row, votes: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "dispute_id": str(row["dispute_id"]),
            "task_id": str(row["task_id"]),
            "claimant_id": str(row["claimant_id"]),
            "respondent_id": str(row["respondent_id"]),
            "claim": str(row["claim"]),
            "rebuttal": str(row["rebuttal"]) if row["rebuttal"] is not None else None,
            "status": str(row["status"]),
            "rebuttal_deadline": str(row["rebuttal_deadline"]),
            "worker_pct": int(row["worker_pct"]) if row["worker_pct"] is not None else None,
            "ruling_summary": (
                str(row["ruling_summary"]) if row["ruling_summary"] is not None else None
            ),
            "escrow_id": str(row["escrow_id"]),
            "filed_at": str(row["filed_at"]),
            "rebutted_at": str(row["rebutted_at"]) if row["rebutted_at"] is not None else None,
            "ruled_at": str(row["ruled_at"]) if row["ruled_at"] is not None else None,
            "votes": votes,
        }

    def _get_dispute_row(self, dispute_id: str) -> sqlite3.Row | None:
        cursor = self._db.execute("SELECT * FROM disputes WHERE dispute_id = ?", (dispute_id,))
        return cursor.fetchone()

    def _get_votes(self, dispute_id: str) -> list[dict[str, Any]]:
        cursor = self._db.execute(
            """
            SELECT vote_id, dispute_id, judge_id, worker_pct, reasoning, voted_at
            FROM votes
            WHERE dispute_id = ?
            ORDER BY voted_at, vote_id
            """,
            (dispute_id,),
        )
        return [
            {
                "vote_id": str(row["vote_id"]),
                "dispute_id": str(row["dispute_id"]),
                "judge_id": str(row["judge_id"]),
                "worker_pct": int(row["worker_pct"]),
                "reasoning": str(row["reasoning"]),
                "voted_at": str(row["voted_at"]),
            }
            for row in cursor.fetchall()
        ]

    def _set_status(self, dispute_id: str, status: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE disputes SET status = ? WHERE dispute_id = ?",
                (status, dispute_id),
            )
            self._db.commit()

    def _revert_to_rebuttal_pending(self, dispute_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE disputes SET status = 'rebuttal_pending' WHERE dispute_id = ?",
                (dispute_id,),
            )
            self._db.execute("DELETE FROM votes WHERE dispute_id = ?", (dispute_id,))
            self._db.commit()

    def file_dispute(
        self,
        task_id: str,
        claimant_id: str,
        respondent_id: str,
        claim: str,
        escrow_id: str,
        rebuttal_deadline_seconds: int,
    ) -> dict[str, Any]:
        """Create a new dispute in rebuttal_pending status."""
        dispute_id = self._new_dispute_id()
        filed_at_dt = datetime.now(UTC)
        filed_at = filed_at_dt.isoformat()
        rebuttal_deadline = (filed_at_dt + timedelta(seconds=rebuttal_deadline_seconds)).isoformat()

        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    """
                    INSERT INTO disputes (
                        dispute_id, task_id, claimant_id, respondent_id, claim,
                        status, rebuttal_deadline, escrow_id, filed_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'rebuttal_pending', ?, ?, ?)
                    """,
                    (
                        dispute_id,
                        task_id,
                        claimant_id,
                        respondent_id,
                        claim,
                        rebuttal_deadline,
                        escrow_id,
                        filed_at,
                    ),
                )
                self._db.commit()
            except sqlite3.IntegrityError as exc:
                self._db.rollback()
                raise ServiceError(
                    "DISPUTE_ALREADY_EXISTS",
                    "A dispute already exists for this task",
                    409,
                    {},
                ) from exc
            except Exception:
                self._db.rollback()
                raise

        dispute = self.get_dispute(dispute_id)
        if dispute is None:
            msg = "Failed to load newly created dispute"
            raise RuntimeError(msg)
        return dispute

    def submit_rebuttal(self, dispute_id: str, rebuttal: str) -> dict[str, Any]:
        """Submit rebuttal for a dispute."""
        with self._lock:
            row = self._get_dispute_row(dispute_id)
            if row is None:
                raise ServiceError("DISPUTE_NOT_FOUND", "Dispute not found", 404, {})

            status = str(row["status"])
            if status != "rebuttal_pending":
                raise ServiceError(
                    "INVALID_DISPUTE_STATUS",
                    "Dispute is not in rebuttal_pending status",
                    409,
                    {},
                )

            if row["rebuttal"] is not None:
                raise ServiceError(
                    "REBUTTAL_ALREADY_SUBMITTED",
                    "Rebuttal has already been submitted",
                    409,
                    {},
                )

            rebutted_at = self._now_iso()
            self._db.execute(
                "UPDATE disputes SET rebuttal = ?, rebutted_at = ? WHERE dispute_id = ?",
                (rebuttal, rebutted_at, dispute_id),
            )
            self._db.commit()

        dispute = self.get_dispute(dispute_id)
        if dispute is None:
            msg = "Failed to load dispute after rebuttal update"
            raise RuntimeError(msg)
        return dispute

    @staticmethod
    def _normalize_deliverables(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        return []

    @staticmethod
    def _normalize_vote(raw_vote: object, index: int) -> JudgeVote:
        if isinstance(raw_vote, JudgeVote):
            vote = raw_vote
        elif isinstance(raw_vote, dict):
            worker_pct = raw_vote.get("worker_pct")
            reasoning = raw_vote.get("reasoning")
            judge_id = raw_vote.get("judge_id")
            voted_at = raw_vote.get("voted_at")
            if not isinstance(judge_id, str) or judge_id == "":
                judge_id = f"judge-{index}"
            if not isinstance(voted_at, str) or voted_at == "":
                voted_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            vote = JudgeVote(
                judge_id=judge_id,
                worker_pct=int(worker_pct) if isinstance(worker_pct, int) else -1,
                reasoning=str(reasoning) if isinstance(reasoning, str) else "",
                voted_at=voted_at,
            )
        else:
            raise ValueError("Judge returned unsupported vote type")

        if not isinstance(vote.worker_pct, int) or not 0 <= vote.worker_pct <= 100:
            raise ValueError("worker_pct must be an integer in [0, 100]")
        if not isinstance(vote.reasoning, str) or vote.reasoning.strip() == "":
            raise ValueError("Judge reasoning must be non-empty")
        if not isinstance(vote.judge_id, str) or vote.judge_id.strip() == "":
            raise ValueError("judge_id must be non-empty")
        if not isinstance(vote.voted_at, str) or vote.voted_at.strip() == "":
            raise ValueError("voted_at must be non-empty")

        return vote

    @staticmethod
    def _delivery_rating(worker_pct: int) -> str:
        if worker_pct >= 80:
            return "extremely_satisfied"
        if worker_pct >= 40:
            return "satisfied"
        return "dissatisfied"

    @staticmethod
    def _spec_rating(worker_pct: int) -> str:
        if worker_pct >= 80:
            return "dissatisfied"
        if worker_pct >= 40:
            return "satisfied"
        return "extremely_satisfied"

    async def execute_ruling(  # noqa: PLR0912, PLR0915
        self,
        dispute_id: str,
        judges: list[object],
        task_data: dict[str, Any],
        task_board_client: object | None,
        central_bank_client: object | None,
        reputation_client: object | None,
        platform_agent_id: str,
    ) -> dict[str, Any]:
        """Evaluate dispute via judges and commit ruled outcome with side-effects."""
        with self._lock:
            row = self._get_dispute_row(dispute_id)
            if row is None:
                raise ServiceError("DISPUTE_NOT_FOUND", "Dispute not found", 404, {})

            if str(row["status"]) == "ruled" or row["ruled_at"] is not None:
                raise ServiceError(
                    "DISPUTE_ALREADY_RULED",
                    "Dispute has already been ruled",
                    409,
                    {},
                )

            if str(row["status"]) != "rebuttal_pending":
                raise ServiceError(
                    "INVALID_DISPUTE_STATUS",
                    "Dispute is not in rebuttal_pending status",
                    409,
                    {},
                )

        self._set_status(dispute_id, "judging")

        try:
            if len(judges) == 0:
                raise ServiceError("JUDGE_UNAVAILABLE", "No judges configured", 502, {})

            context = DisputeContext(
                task_spec=str(task_data.get("spec", "")),
                deliverables=self._normalize_deliverables(task_data.get("deliverables")),
                claim=str(row["claim"]),
                rebuttal=str(row["rebuttal"]) if row["rebuttal"] is not None else None,
                task_title=str(task_data.get("title", "")),
                reward=int(task_data.get("reward", 0)),
            )

            normalized_votes: list[JudgeVote] = []
            for index, judge in enumerate(judges):
                try:
                    raw_vote = await judge.evaluate(context)  # type: ignore[attr-defined]
                except ServiceError as exc:
                    raise ServiceError(
                        "JUDGE_UNAVAILABLE",
                        f"Judge {index} failed to evaluate dispute",
                        502,
                        {},
                    ) from exc
                except Exception as exc:
                    raise ServiceError(
                        "JUDGE_UNAVAILABLE",
                        f"Judge {index} failed to evaluate dispute",
                        502,
                        {},
                    ) from exc

                normalized_votes.append(self._normalize_vote(raw_vote, index))

            sorted_worker_pcts = sorted(v.worker_pct for v in normalized_votes)
            median_worker_pct = sorted_worker_pcts[len(sorted_worker_pcts) // 2]
            ruling_summary = "\n\n".join(v.reasoning for v in normalized_votes)

            if central_bank_client is None:
                raise ServiceError(
                    "CENTRAL_BANK_UNAVAILABLE",
                    "Central Bank client not initialized",
                    502,
                    {},
                )
            try:
                await central_bank_client.split_escrow(
                    str(row["escrow_id"]),
                    str(row["respondent_id"]),
                    str(row["claimant_id"]),
                    median_worker_pct,
                )
            except ServiceError:
                raise
            except Exception as exc:
                raise ServiceError(
                    "CENTRAL_BANK_UNAVAILABLE",
                    "Cannot reach Central Bank service",
                    502,
                    {},
                ) from exc

            if reputation_client is None:
                raise ServiceError(
                    "REPUTATION_SERVICE_UNAVAILABLE",
                    "Reputation client not initialized",
                    502,
                    {},
                )

            spec_feedback_payload = {
                "action": "submit_feedback",
                "task_id": str(row["task_id"]),
                "from_agent_id": platform_agent_id,
                "to_agent_id": str(row["claimant_id"]),
                "category": "spec_quality",
                "rating": self._spec_rating(median_worker_pct),
                "comment": ruling_summary,
            }
            delivery_feedback_payload = {
                "action": "submit_feedback",
                "task_id": str(row["task_id"]),
                "from_agent_id": platform_agent_id,
                "to_agent_id": str(row["respondent_id"]),
                "category": "delivery_quality",
                "rating": self._delivery_rating(median_worker_pct),
                "comment": ruling_summary,
            }

            try:
                if hasattr(reputation_client, "record_feedback"):
                    await reputation_client.record_feedback(spec_feedback_payload)
                    await reputation_client.record_feedback(delivery_feedback_payload)
                else:
                    await reputation_client.submit_feedback(spec_feedback_payload)
                    await reputation_client.submit_feedback(delivery_feedback_payload)
            except ServiceError:
                raise
            except Exception as exc:
                raise ServiceError(
                    "REPUTATION_SERVICE_UNAVAILABLE",
                    "Cannot reach Reputation service",
                    502,
                    {},
                ) from exc

            if task_board_client is None:
                raise ServiceError(
                    "TASK_BOARD_UNAVAILABLE",
                    "Task Board client not initialized",
                    502,
                    {},
                )
            try:
                await task_board_client.record_ruling(
                    str(row["task_id"]),
                    {
                        "action": "record_ruling",
                        "ruling_id": dispute_id,
                        "worker_pct": median_worker_pct,
                        "ruling_summary": ruling_summary,
                    },
                )
            except ServiceError:
                raise
            except Exception as exc:
                raise ServiceError(
                    "TASK_BOARD_UNAVAILABLE",
                    "Cannot reach Task Board service",
                    502,
                    {},
                ) from exc

            ruled_at = self._now_iso()
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    """
                    UPDATE disputes
                    SET status = 'ruled', worker_pct = ?, ruling_summary = ?, ruled_at = ?
                    WHERE dispute_id = ?
                    """,
                    (median_worker_pct, ruling_summary, ruled_at, dispute_id),
                )
                for vote in normalized_votes:
                    self._db.execute(
                        """
                        INSERT INTO votes (
                            vote_id, dispute_id, judge_id, worker_pct, reasoning, voted_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self._new_vote_id(),
                            dispute_id,
                            vote.judge_id,
                            vote.worker_pct,
                            vote.reasoning,
                            vote.voted_at,
                        ),
                    )
                self._db.commit()

        except ServiceError:
            self._revert_to_rebuttal_pending(dispute_id)
            raise
        except Exception as exc:
            self._revert_to_rebuttal_pending(dispute_id)
            raise ServiceError(
                "JUDGE_UNAVAILABLE",
                "Failed to evaluate dispute",
                502,
                {},
            ) from exc

        dispute = self.get_dispute(dispute_id)
        if dispute is None:
            msg = "Failed to load ruled dispute"
            raise RuntimeError(msg)
        return dispute

    def get_dispute(self, dispute_id: str) -> dict[str, Any] | None:
        """Return dispute details with votes, or None."""
        with self._lock:
            row = self._get_dispute_row(dispute_id)
            if row is None:
                return None
            votes = self._get_votes(dispute_id)
            return self._row_to_dispute(row, votes)

    def list_disputes(self, task_id: str | None, status: str | None) -> list[dict[str, Any]]:
        """List disputes with optional AND filters."""
        query = (
            "SELECT dispute_id, task_id, claimant_id, respondent_id, status, "
            "worker_pct, filed_at, ruled_at FROM disputes"
        )
        clauses: list[str] = []
        params: list[object] = []

        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        if len(clauses) > 0:
            query += " WHERE " + " AND ".join(clauses)

        query += " ORDER BY filed_at"

        with self._lock:
            rows = self._db.execute(query, params).fetchall()

        return [
            {
                "dispute_id": str(row["dispute_id"]),
                "task_id": str(row["task_id"]),
                "claimant_id": str(row["claimant_id"]),
                "respondent_id": str(row["respondent_id"]),
                "status": str(row["status"]),
                "worker_pct": int(row["worker_pct"]) if row["worker_pct"] is not None else None,
                "filed_at": str(row["filed_at"]),
                "ruled_at": str(row["ruled_at"]) if row["ruled_at"] is not None else None,
            }
            for row in rows
        ]

    def count_disputes(self) -> int:
        """Count all disputes."""
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM disputes").fetchone()
        return int(row[0]) if row is not None else 0

    def count_active(self) -> int:
        """Count disputes not yet ruled."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM disputes WHERE status != 'ruled'"
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            self._db.close()
