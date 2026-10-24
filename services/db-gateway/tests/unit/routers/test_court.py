"""Court domain tests — Categories 12-14 (CLM, REB, RUL)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

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

    from db_gateway_service.services.db_writer import DbWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _register_agent(
    client: TestClient,
    agent_id: str | None = None,
    name: str = "Test",
) -> str:
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
    client: TestClient,
    agent_id: str | None = None,
    balance: int = 500,
    name: str = "Test",
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
    client: TestClient,
    payer_id: str,
    amount: int = 100,
    task_id: str | None = None,
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
    """Create a full task. Returns (task_id, poster_id, escrow_id)."""
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


def _setup_court_prerequisites(
    client: TestClient,
    clock: BusinessClock,
) -> tuple[str, str, str]:
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
    status: str = "filed",
    rebuttal_deadline: str | None = None,
) -> str:
    cid = claim_id or f"clm-{uuid4()}"
    body: dict[str, object] = {
        "claim_id": cid,
        "task_id": task_id,
        "claimant_id": claimant_id,
        "respondent_id": respondent_id,
        "reason": _DISPUTE_REASON,
        "status": status,
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
    """Persist a real Board history and matching Court claim for rebuttal tests."""
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


# ===================================================================
# Category 12: Dispute Claims (POST /court/claims)
# ===================================================================


@pytest.mark.unit
class TestDisputeClaims:
    """Dispute claims tests — CLM-01 through CLM-08."""

    def test_file_valid_claim(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """CLM-01: File a valid claim returns 201 with claim_id and event_id."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = f"clm-{uuid4()}"

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": claim_id,
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "The login page does not validate email format",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "claim_id" in data
        assert "event_id" in data
        assert data["claim_id"] == claim_id

    def test_duplicate_claim_id_rejected(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """CLM-02: Duplicate claim_id is rejected with 409 claim_exists."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = f"clm-{uuid4()}"

        _file_claim(app_with_writer, tid, alice, bob, claim_id=claim_id)

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": claim_id,
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "Different reason",
                "status": "filed",
                "filed_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "claim_exists"

    def test_fk_violation_task_id(self, app_with_writer: TestClient) -> None:
        """CLM-03: Foreign key violation on task_id returns 409."""
        alice = _register_agent(app_with_writer, name="Alice")
        bob = _register_agent(app_with_writer, name="Bob")
        fake_task = f"t-{uuid4()}"

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": f"clm-{uuid4()}",
                "task_id": fake_task,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "Bad work",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=fake_task),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_fk_violation_claimant_id(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """CLM-04: Foreign key violation on claimant_id returns 409."""
        _alice, bob, tid = _setup_court_prerequisites(
            app_with_writer, _fixed_gateway_business_clock
        )
        fake_claimant = f"a-{uuid4()}"

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": f"clm-{uuid4()}",
                "task_id": tid,
                "claimant_id": fake_claimant,
                "respondent_id": bob,
                "reason": "Bad work",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_fk_violation_respondent_id(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """CLM-05: Foreign key violation on respondent_id returns 409."""
        alice, _bob, tid = _setup_court_prerequisites(
            app_with_writer, _fixed_gateway_business_clock
        )
        fake_respondent = f"a-{uuid4()}"

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": f"clm-{uuid4()}",
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": fake_respondent,
                "reason": "Bad work",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_missing_reason(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """CLM-06: Missing reason returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": f"clm-{uuid4()}",
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": bob,
                # reason omitted
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """CLM-07: Missing event returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": f"clm-{uuid4()}",
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "Bad work",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                # event omitted
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_claim_and_event_atomic(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """CLM-08: Claim and event are written atomically."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = f"clm-{uuid4()}"

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": claim_id,
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "The login page does not validate email format",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")

        claim_row = conn.execute(
            "SELECT claim_id FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        assert claim_row is not None

        event_row = conn.execute(
            "SELECT event_type FROM events WHERE task_id = ? AND event_type = 'claim.filed'",
            (tid,),
        ).fetchone()
        assert event_row is not None
        assert event_row[0] == "claim.filed"
        conn.close()


# ===================================================================
# Category 13: Rebuttals (POST /court/rebuttals)
# ===================================================================


@pytest.mark.unit
class TestRebuttals:
    """Rebuttal tests — REB-01 through REB-09."""

    def test_submit_valid_rebuttal(
        self,
        app_with_writer: TestClient,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """REB-01: Submit a valid rebuttal returns 201 with rebuttal_id and event_id."""
        _alice, bob, tid, claim_id = _prepare_disputed_claim(
            app_with_writer, _fixed_gateway_business_clock
        )
        rebuttal_id = f"reb-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": rebuttal_id,
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "The specification did not mention email validation",
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "rebuttal_id" in data
        assert "event_id" in data
        assert data["rebuttal_id"] == rebuttal_id

    def test_rebuttal_with_claim_status_update(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """REB-02: Rebuttal with claim_status_update changes claim status."""
        _alice, bob, tid, claim_id = _prepare_disputed_claim(
            app_with_writer, _fixed_gateway_business_clock
        )

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "The specification did not mention email validation",
                "submitted_at": "2026-02-28T17:00:00Z",
                "claim_status_update": "rebuttal",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "rebuttal"

    def test_rebuttal_without_claim_status_update(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """REB-03: Rebuttal without claim_status_update leaves claim unchanged."""
        _alice, bob, tid, claim_id = _prepare_disputed_claim(
            app_with_writer, _fixed_gateway_business_clock
        )

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "The specification did not mention email validation",
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "filed"

    def test_duplicate_rebuttal_id_rejected(
        self,
        app_with_writer: TestClient,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """REB-04: Duplicate rebuttal_id is rejected with 409 rebuttal_exists."""
        _alice, bob, tid, claim_id = _prepare_disputed_claim(
            app_with_writer, _fixed_gateway_business_clock
        )
        rebuttal_id = f"reb-{uuid4()}"

        app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": rebuttal_id,
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "First rebuttal",
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": rebuttal_id,
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "Second rebuttal with same ID",
                "submitted_at": "2026-02-28T17:30:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "rebuttal_exists"

    def test_unknown_claim_rejected_before_insert(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """REB-05: Unknown claims are rejected before the rebuttal insert."""
        _alice, bob, _tid = _setup_court_prerequisites(
            app_with_writer, _fixed_gateway_business_clock
        )
        fake_claim = f"clm-{uuid4()}"
        before = db_writer.connection.execute(
            "SELECT (SELECT COUNT(*) FROM court_rebuttals), (SELECT COUNT(*) FROM events)"
        ).fetchone()

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": fake_claim,
                "agent_id": bob,
                "content": "Rebuttal to non-existent claim",
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted"),
            },
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "claim_not_found"
        after = db_writer.connection.execute(
            "SELECT (SELECT COUNT(*) FROM court_rebuttals), (SELECT COUNT(*) FROM events)"
        ).fetchone()
        assert after == before

    def test_non_worker_rejected_before_insert(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """REB-06: A non-worker is rejected before the rebuttal insert."""
        _alice, _bob, tid, claim_id = _prepare_disputed_claim(
            app_with_writer, _fixed_gateway_business_clock
        )
        fake_agent = f"a-{uuid4()}"
        before = db_writer.connection.execute(
            "SELECT (SELECT COUNT(*) FROM court_rebuttals), (SELECT COUNT(*) FROM events)"
        ).fetchone()

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": fake_agent,
                "content": "Rebuttal from fake agent",
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"
        after = db_writer.connection.execute(
            "SELECT (SELECT COUNT(*) FROM court_rebuttals), (SELECT COUNT(*) FROM events)"
        ).fetchone()
        assert after == before

    def test_missing_content(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """REB-07: Missing content returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": bob,
                # content omitted
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """REB-08: Missing event returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "Rebuttal text",
                "submitted_at": "2026-02-28T17:00:00Z",
                # event omitted
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_rebuttal_and_claim_update_atomic(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """REB-09: Rebuttal, claim update, and event are all written atomically."""
        _alice, bob, tid, claim_id = _prepare_disputed_claim(
            app_with_writer, _fixed_gateway_business_clock
        )
        rebuttal_id = f"reb-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": rebuttal_id,
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "The specification was ambiguous",
                "submitted_at": "2026-02-28T17:00:00Z",
                "claim_status_update": "rebuttal",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")

        # Rebuttal row exists
        reb_row = conn.execute(
            "SELECT rebuttal_id FROM court_rebuttals WHERE rebuttal_id = ?",
            (rebuttal_id,),
        ).fetchone()
        assert reb_row is not None

        # Claim status updated
        claim_row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        assert claim_row is not None
        assert claim_row[0] == "rebuttal"

        # Event exists
        event_row = conn.execute(
            "SELECT event_type FROM events WHERE task_id = ? AND event_type = 'rebuttal.submitted'",
            (tid,),
        ).fetchone()
        assert event_row is not None
        conn.close()


# ===================================================================
# Category 14: Rulings (POST /court/rulings)
# ===================================================================


@pytest.mark.unit
class TestRulings:
    """Ruling tests — RUL-01 through RUL-10."""

    def test_record_valid_ruling(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """RUL-01: Record a valid ruling returns 201 with ruling_id and event_id."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        ruling_id = f"rul-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": ruling_id,
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "The specification was ambiguous about email validation",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
                '"reasoning": "Spec did not mention email format"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "ruling_id" in data
        assert "event_id" in data
        assert data["ruling_id"] == ruling_id

    def test_ruling_with_claim_status_update(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """RUL-02: Ruling with claim_status_update changes claim status to 'ruled'."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob, status="rebuttal")

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "Spec was ambiguous",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
                '"reasoning": "Ambiguous spec"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "claim_status_update": "ruled",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "ruled"

    def test_ruling_without_claim_status_update(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """RUL-03: Ruling without claim_status_update leaves claim unchanged."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob, status="rebuttal")

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "Spec was ambiguous",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
                '"reasoning": "Ambiguous spec"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "rebuttal"

    def test_duplicate_ruling_id_rejected(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """RUL-04: Duplicate ruling_id is rejected with 409 ruling_exists."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        ruling_id = f"rul-{uuid4()}"

        app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": ruling_id,
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "First ruling",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "First"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": ruling_id,
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 50,
                "summary": "Second ruling with same ID",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 50, "reasoning": "Second"}]',
                "ruled_at": "2026-02-28T19:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "ruling_exists"

    def test_fk_violation_claim_id(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """RUL-05: Foreign key violation on claim_id returns 409."""
        _alice, _bob, tid = _setup_court_prerequisites(
            app_with_writer, _fixed_gateway_business_clock
        )
        fake_claim = f"clm-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": fake_claim,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "Ruling on non-existent claim",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "Test"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_fk_violation_task_id(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """RUL-06: Foreign key violation on task_id returns 409."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        fake_task = f"t-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": fake_task,
                "worker_pct": 70,
                "summary": "Ruling with wrong task",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "Test"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=fake_task,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_missing_summary(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """RUL-07: Missing summary returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                # summary omitted
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "Test"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_judge_votes(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """RUL-08: Missing judge_votes returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "Spec was ambiguous",
                # judge_votes omitted
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(
        self, app_with_writer: TestClient, _fixed_gateway_business_clock: BusinessClock
    ) -> None:
        """RUL-09: Missing event returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "Spec was ambiguous",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "Test"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                # event omitted
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_ruling_and_claim_update_atomic(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """RUL-10: Ruling, claim update, and event are all written atomically."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer, _fixed_gateway_business_clock)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        ruling_id = f"rul-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": ruling_id,
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "The specification was ambiguous about email validation",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
                '"reasoning": "Spec did not mention email format"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "claim_status_update": "ruled",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")

        # Ruling row exists
        rul_row = conn.execute(
            "SELECT ruling_id FROM court_rulings WHERE ruling_id = ?", (ruling_id,)
        ).fetchone()
        assert rul_row is not None

        # Claim status updated
        claim_row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        assert claim_row is not None
        assert claim_row[0] == "ruled"

        # Event exists
        event_row = conn.execute(
            "SELECT event_type FROM events WHERE task_id = ? AND event_type = 'ruling.delivered'",
            (tid,),
        ).fetchone()
        assert event_row is not None
        conn.close()
