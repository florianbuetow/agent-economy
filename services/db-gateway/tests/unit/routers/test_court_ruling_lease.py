"""Court ruling durability: judging lease CAS, decision claim, acknowledgments.

Plan item 0.3. The Gateway owns the compare-and-set boundary that keeps one
Court run per dispute: ``rebuttal_pending`` -> ``judging`` only through a
leased claim, the decision is persisted before any delivery, deliveries are
acknowledged, and ``ruled`` is reachable only once every acknowledgment exists.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.conftest import make_event
from tests.fakes.native_board_history import BusinessClock, publish_task

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit

_START_US = parse_utc_text_to_epoch_us("2026-02-28T12:00:00Z")
_LEASE_SECONDS = 60
_REQUEST = {"expected_lifecycle_version": 3, "reward": 100}
_VOTES = json.dumps([{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "ok", "voted_at": "x"}])


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> BusinessClock:
    business_clock = BusinessClock(now_us=_START_US)
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", business_clock)
    return business_clock


def _agent(client: TestClient, balance: int) -> str:
    agent_id = f"a-{uuid4()}"
    registered = client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        },
    )
    assert registered.status_code == 201
    if balance > 0:
        account = client.post(
            "/bank/accounts",
            json={
                "operation_id": f"op-{uuid4()}",
                "account_id": agent_id,
                "balance": balance,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": balance,
                    "reference": "initial_balance",
                    "timestamp": "2026-02-28T10:00:00Z",
                },
            },
        )
        assert account.status_code == 201
    return agent_id


def _claim(client: TestClient, clock: BusinessClock, status: str) -> str:
    poster = _agent(client, 500)
    worker = _agent(client, 0)
    task_id = f"t-{uuid4()}"
    previous = clock.now_us
    clock.now_us = parse_utc_text_to_epoch_us("2026-02-28T10:15:00Z")
    try:
        published = publish_task(
            client,
            task_id=task_id,
            poster_id=poster,
            escrow_id=f"esc-{uuid4()}",
            title="Lease task",
            spec="Spec",
            reward=100,
            bidding_deadline_seconds=86400,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=172800,
            review_deadline_seconds=43200,
            lock_timestamp="2026-02-28T10:10:00Z",
        )
        assert published.status_code in {200, 201}
    finally:
        clock.now_us = previous
    claim_id = f"clm-{uuid4()}"
    filed = client.post(
        "/court/claims",
        json={
            "claim_id": claim_id,
            "task_id": task_id,
            "claimant_id": poster,
            "respondent_id": worker,
            "reason": "Bad work",
            "status": status,
            "filed_at": "2026-02-28T11:00:00Z",
            "event": make_event(source="court", event_type="claim.filed", task_id=task_id),
        },
    )
    assert filed.status_code == 201
    return claim_id


def _acquire(client: TestClient, claim_id: str, lease_id: str) -> Any:
    return client.post(
        f"/court/claims/{claim_id}/judging-lease",
        json={"lease_id": lease_id, "lease_seconds": _LEASE_SECONDS},
    )


def _decide(client: TestClient, claim_id: str, lease_id: str) -> Any:
    return client.post(
        f"/court/claims/{claim_id}/decision",
        json={
            "lease_id": lease_id,
            "worker_pct": 70,
            "summary": "Mostly delivered",
            "judge_votes": _VOTES,
            "request": _REQUEST,
        },
    )


def _ack(client: TestClient, claim_id: str, lease_id: str, step: str) -> Any:
    return client.post(
        f"/court/claims/{claim_id}/acknowledgments",
        json={"lease_id": lease_id, "step": step},
    )


def _progress(client: TestClient, claim_id: str) -> dict[str, Any]:
    response = client.get(f"/court/claims/{claim_id}/ruling-progress")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _status(db_writer: DbWriter, claim_id: str) -> str:
    connection = sqlite3.connect(db_writer._db_path)
    try:
        row = connection.execute(
            "SELECT status FROM court_claims WHERE claim_id=?", (claim_id,)
        ).fetchone()
    finally:
        connection.close()
    return str(row[0])


class TestJudgingLease:
    def test_acquire_moves_rebuttal_pending_to_judging(
        self, app_with_writer: TestClient, db_writer: DbWriter, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "rebuttal_pending")
        response = _acquire(app_with_writer, claim_id, "lease-a")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "judging"
        assert body["lease_id"] == "lease-a"
        assert body["reclaimed"] is False
        assert body["decided"] is False
        assert _status(db_writer, claim_id) == "judging"

    def test_second_acquire_while_lease_live_conflicts(
        self, app_with_writer: TestClient, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "rebuttal_pending")
        assert _acquire(app_with_writer, claim_id, "lease-a").status_code == 200
        second = _acquire(app_with_writer, claim_id, "lease-b")
        assert second.status_code == 409
        assert second.json()["error"] == "judging_lease_held"

    def test_expired_lease_is_reclaimed(
        self, app_with_writer: TestClient, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "rebuttal_pending")
        assert _acquire(app_with_writer, claim_id, "lease-a").status_code == 200
        clock.now_us += (_LEASE_SECONDS + 1) * 1_000_000
        reclaimed = _acquire(app_with_writer, claim_id, "lease-b")
        assert reclaimed.status_code == 200, reclaimed.text
        assert reclaimed.json()["reclaimed"] is True
        # The superseded owner can no longer write a decision.
        stale = _decide(app_with_writer, claim_id, "lease-a")
        assert stale.status_code == 409
        assert stale.json()["error"] == "judging_lease_lost"

    def test_ruled_claim_cannot_be_leased(
        self, app_with_writer: TestClient, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "ruled")
        response = _acquire(app_with_writer, claim_id, "lease-a")
        assert response.status_code == 409
        assert response.json()["error"] == "invalid_claim_status"

    def test_unknown_claim_is_404(self, app_with_writer: TestClient) -> None:
        assert _acquire(app_with_writer, "clm-missing", "lease-a").status_code == 404

    def test_release_without_decision_returns_to_rebuttal_pending(
        self, app_with_writer: TestClient, db_writer: DbWriter, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "rebuttal_pending")
        assert _acquire(app_with_writer, claim_id, "lease-a").status_code == 200
        released = app_with_writer.post(
            f"/court/claims/{claim_id}/judging-lease/release", json={"lease_id": "lease-a"}
        )
        assert released.status_code == 200, released.text
        assert _status(db_writer, claim_id) == "rebuttal_pending"
        assert _acquire(app_with_writer, claim_id, "lease-b").status_code == 200

    def test_release_after_decision_keeps_judging_and_frees_lease(
        self, app_with_writer: TestClient, db_writer: DbWriter, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "rebuttal_pending")
        assert _acquire(app_with_writer, claim_id, "lease-a").status_code == 200
        assert _decide(app_with_writer, claim_id, "lease-a").status_code == 201
        released = app_with_writer.post(
            f"/court/claims/{claim_id}/judging-lease/release", json={"lease_id": "lease-a"}
        )
        assert released.status_code == 200
        assert _status(db_writer, claim_id) == "judging"
        resumed = _acquire(app_with_writer, claim_id, "lease-b")
        assert resumed.status_code == 200
        assert resumed.json()["decided"] is True


class TestDecisionAndDelivery:
    def test_decision_requires_lease(
        self, app_with_writer: TestClient, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "rebuttal_pending")
        response = _decide(app_with_writer, claim_id, "lease-a")
        assert response.status_code == 409
        assert response.json()["error"] == "judging_lease_lost"

    def test_decision_is_persisted_once_and_never_overwritten(
        self, app_with_writer: TestClient, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "rebuttal_pending")
        assert _acquire(app_with_writer, claim_id, "lease-a").status_code == 200
        assert _decide(app_with_writer, claim_id, "lease-a").status_code == 201
        duplicate = _decide(app_with_writer, claim_id, "lease-a")
        assert duplicate.status_code == 409
        assert duplicate.json()["error"] == "ruling_exists"
        progress = _progress(app_with_writer, claim_id)
        assert progress["status"] == "judging"
        assert progress["decision"]["worker_pct"] == 70
        assert progress["decision"]["summary"] == "Mostly delivered"
        assert json.loads(progress["decision"]["judge_votes"])[0]["worker_pct"] == 70
        assert progress["decision"]["request"] == _REQUEST
        assert progress["acknowledgments"] == []

    def test_ruled_requires_every_acknowledgment(
        self, app_with_writer: TestClient, db_writer: DbWriter, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "rebuttal_pending")
        assert _acquire(app_with_writer, claim_id, "lease-a").status_code == 200
        assert _decide(app_with_writer, claim_id, "lease-a").status_code == 201
        assert _ack(app_with_writer, claim_id, "lease-a", "task_board").status_code == 200
        early = app_with_writer.post(
            f"/court/claims/{claim_id}/ruled", json={"lease_id": "lease-a"}
        )
        assert early.status_code == 409
        assert early.json()["error"] == "acknowledgments_pending"
        # Acknowledging twice is idempotent.
        assert _ack(app_with_writer, claim_id, "lease-a", "task_board").status_code == 200
        assert _ack(app_with_writer, claim_id, "lease-a", "reputation").status_code == 200
        assert _progress(app_with_writer, claim_id)["acknowledgments"] == [
            "reputation",
            "task_board",
        ]
        ruled = app_with_writer.post(
            f"/court/claims/{claim_id}/ruled", json={"lease_id": "lease-a"}
        )
        assert ruled.status_code == 200, ruled.text
        assert _status(db_writer, claim_id) == "ruled"
        again = app_with_writer.post(
            f"/court/claims/{claim_id}/ruled", json={"lease_id": "lease-a"}
        )
        assert again.status_code == 200

    def test_unknown_acknowledgment_step_rejected(
        self, app_with_writer: TestClient, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "rebuttal_pending")
        assert _acquire(app_with_writer, claim_id, "lease-a").status_code == 200
        assert _decide(app_with_writer, claim_id, "lease-a").status_code == 201
        response = _ack(app_with_writer, claim_id, "lease-a", "bank")
        assert response.status_code == 400

    def test_progress_for_undecided_claim(
        self, app_with_writer: TestClient, clock: BusinessClock
    ) -> None:
        claim_id = _claim(app_with_writer, clock, "rebuttal_pending")
        progress = _progress(app_with_writer, claim_id)
        assert progress == {
            "claim_id": claim_id,
            "status": "rebuttal_pending",
            "decision": None,
            "acknowledgments": [],
        }
