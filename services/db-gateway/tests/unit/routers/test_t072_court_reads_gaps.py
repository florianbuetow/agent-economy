"""T-072 coverage-vs-spec-ID sweep: gap-fill tests for court read endpoints.

Covers READ-27 (GET /court/claims filters by claimant_id — status filter was
already covered), READ-29/READ-30 (GET /court/claims/{claim_id}/rebuttal
found/not found), and READ-31/READ-32 (GET /court/rulings/{claim_id}
found/not found). All four endpoints already exist and are correctly wired in
routers/court.py + services/db_reader.py; only the tests were missing
(verified via a full literal-ID grep across services/db-gateway/tests/ before
writing).

New file — no existing test file was modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.conftest import make_event
from tests.fakes.native_board_history import (
    BusinessClock,
    TaskGraph,
    accept_bid,
    finalize,
    operation_body,
    publish_task,
    submit_bid,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_BUSINESS_START_US = parse_utc_text_to_epoch_us("2026-02-28T12:00:00Z")
_CLAIM_TIME_US = parse_utc_text_to_epoch_us("2026-02-28T16:00:00Z")
_REBUTTAL_TIME_US = parse_utc_text_to_epoch_us("2026-02-28T17:00:00Z")
_TASK_CREATED_US = parse_utc_text_to_epoch_us("2026-02-28T10:15:00Z")
_DISPUTE_REASON = "The login page does not validate email format"
_REBUTTAL_DEADLINE = "2026-03-01T17:00:00Z"


@pytest.fixture(autouse=True)
def _fixed_gateway_business_clock(monkeypatch: pytest.MonkeyPatch) -> BusinessClock:
    """Control Board business time without changing JWT validation time."""
    clock = BusinessClock(now_us=_BUSINESS_START_US)
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", clock)
    return clock


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


def _register_agent(client: TestClient, agent_id: str | None = None, name: str = "Test") -> str:
    aid = agent_id or f"a-{uuid4()}"
    client.post(
        "/identity/agents",
        json={
            "agent_id": aid,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        },
    )
    return aid


def _setup_funded_account(
    client: TestClient, agent_id: str | None = None, balance: int = 500, name: str = "Test"
) -> str:
    aid = _register_agent(client, agent_id, name=name)
    data: dict[str, Any] = {
        "account_id": aid,
        "balance": balance,
        "created_at": "2026-02-28T10:00:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        data["operation_id"] = f"op-{uuid4()}"
        data["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-02-28T10:00:00Z",
        }
    client.post("/bank/accounts", json=data)
    return aid


def _lock_escrow(
    client: TestClient, payer_id: str, amount: int = 100, task_id: str | None = None
) -> tuple[str, str]:
    tid = task_id or f"t-{uuid4()}"
    eid = f"esc-{uuid4()}"
    client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": f"op-{uuid4()}",
            "escrow_id": eid,
            "payer_account_id": payer_id,
            "amount": amount,
            "task_id": tid,
            "created_at": "2026-02-28T10:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=tid),
        },
    )
    return eid, tid


def _create_task(
    client: TestClient,
    clock: BusinessClock,
    poster_id: str | None = None,
) -> tuple[str, str, str]:
    pid = poster_id or _setup_funded_account(client)
    eid, tid = f"esc-{uuid4()}", f"t-{uuid4()}"
    previous = clock.now_us
    clock.now_us = _TASK_CREATED_US
    try:
        publish_task(
            client,
            task_id=tid,
            poster_id=pid,
            escrow_id=eid,
            title="Test Task",
            spec="Build a login page",
            reward=100,
            bidding_deadline_seconds=86400,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=172800,
            review_deadline_seconds=43200,
            lock_timestamp="2026-02-28T10:10:00Z",
        )
    finally:
        clock.now_us = previous
    return tid, pid, eid


def _setup_court_prerequisites(client: TestClient, clock: BusinessClock) -> tuple[str, str, str]:
    """Create alice, bob, and a task. Returns (alice, bob, task_id)."""
    alice = _setup_funded_account(client, name="Alice")
    bob = _register_agent(client, name="Bob")
    tid, _pid, _eid = _create_task(client, clock, poster_id=alice)
    return alice, bob, tid


def _file_claim(
    client: TestClient,
    task_id: str,
    claimant_id: str,
    respondent_id: str,
    claim_id: str | None = None,
    rebuttal_deadline: str | None = None,
) -> str:
    cid = claim_id or f"clm-{uuid4()}"
    body: dict[str, object] = {
        "claim_id": cid,
        "task_id": task_id,
        "claimant_id": claimant_id,
        "respondent_id": respondent_id,
        "reason": _DISPUTE_REASON,
        "status": "filed",
        "filed_at": "2026-02-28T16:00:00Z",
        "event": make_event(source="court", event_type="claim.filed", task_id=task_id),
    }
    if rebuttal_deadline is not None:
        body["rebuttal_deadline"] = rebuttal_deadline
    response = client.post("/court/claims", json=body)
    assert response.status_code == 201
    return cid


def _prepare_disputed_claim(
    client: TestClient,
    clock: BusinessClock,
) -> tuple[str, str, str, str]:
    """Persist a real Board history and matching Court claim for rebuttal reads."""
    alice = _setup_funded_account(client, name="Alice")
    bob = _register_agent(client, name="Bob")
    task_id, _poster_id, escrow_id = _create_task(client, clock, poster_id=alice)
    graph = TaskGraph(task_id=task_id, poster_id=alice, escrow_id=escrow_id)

    bid_id, bid = submit_bid(client, graph, bob)
    assert bid.status_code == 200
    accepted = accept_bid(client, graph, bob, bid_id)
    assert accepted.status_code == 200
    asset = client.post(
        "/board/assets",
        json={
            "admission": {
                "expected_status": "accepted",
                "expected_lifecycle_version": 1,
                "max_files_per_task": 10,
            },
            "asset_id": f"asset-{uuid4()}",
            "task_id": task_id,
            "uploader_id": bob,
            "filename": "deliverable.txt",
            "content_type": "text/plain",
            "size_bytes": 7,
            "storage_path": f"assets/{task_id}/deliverable.txt",
            "content_hash": "a" * 64,
            "uploaded_at": "2026-02-28T12:01:00Z",
            "event": make_event(source="board", event_type="asset.uploaded", task_id=task_id),
        },
    )
    assert asset.status_code == 201
    submitted = client.post(
        "/board/task-operations",
        json=operation_body(
            operation_id=f"op-{uuid4()}",
            task_id=task_id,
            command="submit_deliverable",
            expected_status="accepted",
            expected_lifecycle_version=1,
            actor_kind="agent",
            actor_id=bob,
            arguments={"worker_id": bob},
        ),
    )
    assert submitted.status_code == 200

    dispute_operation_id = f"op-{uuid4()}"
    pending = client.post(
        "/board/task-operations",
        json=operation_body(
            operation_id=dispute_operation_id,
            task_id=task_id,
            command="dispute_task",
            expected_status="submitted",
            expected_lifecycle_version=2,
            actor_kind="agent",
            actor_id=alice,
            arguments={
                "poster_id": alice,
                "worker_id": bob,
                "escrow_id": escrow_id,
                "reward": 100,
                "reason": _DISPUTE_REASON,
            },
        ),
    )
    assert pending.status_code == 202
    clock.now_us = _CLAIM_TIME_US
    claim_id = _file_claim(
        client,
        task_id,
        alice,
        bob,
        rebuttal_deadline=_REBUTTAL_DEADLINE,
    )
    completed = finalize(client, dispute_operation_id)
    assert completed.status_code == 200
    clock.now_us = _REBUTTAL_TIME_US
    return alice, bob, task_id, claim_id


def _submit_rebuttal(client: TestClient, claim_id: str, agent_id: str, task_id: str) -> str:
    rebuttal_id = f"reb-{uuid4()}"
    response = client.post(
        "/court/rebuttals",
        json={
            "rebuttal_id": rebuttal_id,
            "claim_id": claim_id,
            "agent_id": agent_id,
            "content": "The specification did not mention email validation",
            "submitted_at": "2026-02-28T17:00:00Z",
            "event": make_event(source="court", event_type="rebuttal.submitted", task_id=task_id),
        },
    )
    assert response.status_code == 201
    return rebuttal_id


def _record_ruling(client: TestClient, claim_id: str, task_id: str) -> str:
    ruling_id = f"rul-{uuid4()}"
    client.post(
        "/court/rulings",
        json={
            "ruling_id": ruling_id,
            "claim_id": claim_id,
            "task_id": task_id,
            "worker_pct": 70,
            "summary": "The specification was ambiguous about email validation",
            "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
            '"reasoning": "Spec did not mention email format"}]',
            "ruled_at": "2026-02-28T18:00:00Z",
            "event": make_event(
                source="court", event_type="ruling.delivered", task_id=task_id, agent_id=None
            ),
        },
    )
    return ruling_id


@pytest.mark.unit
class TestReadClaimsFilterByClaimantId:
    """READ-27: GET /court/claims filters by claimant_id (status filter is
    already covered by test_court_reads.py)."""

    def test_filter_by_claimant_id(
        self,
        app_with_writer: TestClient,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        alice, bob, tid1 = _setup_court_prerequisites(
            app_with_writer, _fixed_gateway_business_clock
        )
        carol = _register_agent(app_with_writer, name="Carol")
        tid2, _pid2, _eid2 = _create_task(
            app_with_writer, _fixed_gateway_business_clock, poster_id=alice
        )

        claim_from_alice = _file_claim(app_with_writer, tid1, alice, bob)
        _file_claim(app_with_writer, tid2, carol, bob)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/claims?claimant_id={alice}")

        assert response.status_code == 200
        claims = response.json()["claims"]
        assert len(claims) == 1
        assert claims[0]["claim_id"] == claim_from_alice
        assert claims[0]["claimant_id"] == alice


@pytest.mark.unit
class TestReadRebuttalByClaimId:
    """READ-29/READ-30: GET /court/claims/{claim_id}/rebuttal."""

    def test_get_rebuttal_returns_the_record(
        self,
        app_with_writer: TestClient,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        _alice, bob, tid, claim_id = _prepare_disputed_claim(
            app_with_writer, _fixed_gateway_business_clock
        )
        rebuttal_id = _submit_rebuttal(app_with_writer, claim_id, bob, tid)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/claims/{claim_id}/rebuttal")

        assert response.status_code == 200
        data = response.json()
        assert data["rebuttal_id"] == rebuttal_id
        assert data["claim_id"] == claim_id
        assert data["agent_id"] == bob

    def test_get_rebuttal_not_found(
        self,
        app_with_writer: TestClient,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/claims/{claim_id}/rebuttal")

        assert response.status_code == 404
        assert response.json()["error"] == "rebuttal_not_found"


@pytest.mark.unit
class TestReadRulingByClaimId:
    """READ-31/READ-32: GET /court/rulings/{claim_id}."""

    def test_get_ruling_returns_the_record(
        self,
        app_with_writer: TestClient,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        ruling_id = _record_ruling(app_with_writer, claim_id, tid)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/rulings/{claim_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["ruling_id"] == ruling_id
        assert data["claim_id"] == claim_id
        assert data["worker_pct"] == 70

    def test_get_ruling_not_found(
        self,
        app_with_writer: TestClient,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/rulings/{claim_id}")

        assert response.status_code == 404
        assert response.json()["error"] == "ruling_not_found"
