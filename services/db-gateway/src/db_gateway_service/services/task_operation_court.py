"""Narrow Court evidence helpers for native Task Board operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    import sqlite3

    from db_gateway_service.task_operation_requests import TaskOperationRequest


@dataclass(frozen=True, slots=True)
class CourtClaimEvidence:
    """Authoritative persisted claim facts needed by Board finalization."""

    claim_id: str
    rebuttal_deadline: str | None


@dataclass(frozen=True, slots=True)
class CourtEvidenceResult:
    """Three-way Court evidence verdict: absent, matching, or contradictory."""

    state: Literal["pending", "matching", "conflict"]
    evidence: CourtClaimEvidence | None


def configured_platform_actor(cursor: sqlite3.Cursor, public_key: str) -> str:
    """Resolve the current registered platform identity by configured public key."""
    row = cursor.execute(
        "SELECT agent_id FROM identity_agents WHERE public_key = ?",
        (public_key,),
    ).fetchone()
    if row is None:
        raise ServiceError(
            "platform_identity_not_ready",
            "Configured platform identity is not registered",
            503,
            {},
        )
    return str(row[0])


def build_court_claim_intent(
    request: TaskOperationRequest,
    task: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, object]:
    """Validate and derive the immutable Court claim plus local escrow predicate."""
    accepted_reward = contract["accepted_reward"]
    frozen_reward = (
        int(accepted_reward)
        if isinstance(accepted_reward, int)
        else int(request.argument("reward"))
    )
    valid = (
        request.argument("poster_id") == task["poster_id"]
        and request.argument("worker_id") == task["worker_id"]
        and request.argument("escrow_id") == task["escrow_id"]
        and request.argument("reward") == frozen_reward
    )
    if not valid:
        raise ServiceError("payload_mismatch", "Court claim facts do not match", 409, {})
    return {
        "court_claim": {
            "task_id": request.task_id,
            "claimant_id": str(task["poster_id"]),
            "respondent_id": str(task["worker_id"]),
            "reason": str(request.argument("reason")),
        },
        "expected_escrow": {
            "escrow_id": str(task["escrow_id"]),
            "task_id": request.task_id,
            "payer_account_id": str(task["poster_id"]),
            "amount": frozen_reward,
        },
    }


def locked_escrow_matches(cursor: sqlite3.Cursor, expected: dict[object, object]) -> bool:
    """Validate the local frozen escrow predicate Court itself does not persist."""
    row = cursor.execute(
        "SELECT payer_account_id, task_id, amount, status, resolved_at "
        "FROM bank_escrow WHERE escrow_id = ?",
        (expected.get("escrow_id"),),
    ).fetchone()
    return row is not None and tuple(row) == (
        expected.get("payer_account_id"),
        expected.get("task_id"),
        expected.get("amount"),
        "locked",
        None,
    )


def evaluate_court_claim(
    cursor: sqlite3.Cursor,
    expected_intent: dict[str, object],
) -> CourtEvidenceResult:
    """Read the unique claim and compare every authoritative persisted fact."""
    claim = expected_intent.get("court_claim")
    escrow = expected_intent.get("expected_escrow")
    if not isinstance(claim, dict) or not isinstance(escrow, dict):
        return CourtEvidenceResult("conflict", None)
    row = cursor.execute(
        "SELECT claim_id, task_id, claimant_id, respondent_id, reason, rebuttal_deadline "
        "FROM court_claims WHERE task_id = ?",
        (claim.get("task_id"),),
    ).fetchone()
    if row is None:
        return CourtEvidenceResult("pending", None)
    exact_claim = tuple(row[1:5]) == (
        claim.get("task_id"),
        claim.get("claimant_id"),
        claim.get("respondent_id"),
        claim.get("reason"),
    )
    if not exact_claim or not locked_escrow_matches(cursor, escrow):
        return CourtEvidenceResult("conflict", None)
    return CourtEvidenceResult(
        "matching",
        CourtClaimEvidence(
            claim_id=str(row[0]),
            rebuttal_deadline=None if row[5] is None else str(row[5]),
        ),
    )


__all__ = [
    "CourtClaimEvidence",
    "CourtEvidenceResult",
    "build_court_claim_intent",
    "configured_platform_actor",
    "evaluate_court_claim",
    "locked_escrow_matches",
]
