"""Reconciled Category 21 constraint coverage for T-072 and MP-07.

CAS-04/CAS-05 now cover native structural/domain precedence after retirement
of generic Board status writes. CAS-09/CAS-10/CAS-11 retain the supported
asset and Court constraint behavior on real native lifecycle histories.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event
from tests.fakes.native_board_history import (
    BIDDING_CUTOFF_US,
    BusinessClock,
    accept_bid,
    file_claim,
    finalize,
    operation_body,
    prepare_submitted,
    publish_task,
    register_worker,
    submit_bid,
)
from tests.fakes.native_board_history import (
    create_task as create_native_task,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


@pytest.fixture(autouse=True)
def _fixed_gateway_business_clock(monkeypatch: pytest.MonkeyPatch) -> BusinessClock:
    """Use deterministic lifecycle time without changing real JWT time."""
    clock = BusinessClock()
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", clock)
    return clock


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


def _register_agent(client: TestClient, *, name: str) -> str:
    agent_id = f"a-{uuid4()}"
    response = client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-03-01T09:00:00Z",
            "event": make_event(),
        },
    )
    assert response.status_code == 201
    return agent_id


def _create_funded_account(client: TestClient, *, name: str, balance: int) -> str:
    agent_id = _register_agent(client, name=name)
    payload: dict[str, Any] = {
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2026-03-01T09:05:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        payload["operation_id"] = f"op-{uuid4()}"
        payload["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-03-01T09:05:00Z",
        }
    response = client.post("/bank/accounts", json=payload)
    assert response.status_code == 201
    return agent_id


def _create_task(client: TestClient, *, poster_id: str) -> tuple[str, str]:
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    publish_task(
        client,
        task_id=task_id,
        poster_id=poster_id,
        escrow_id=escrow_id,
        title="Test Task",
        spec="Build a login page",
        reward=100,
        bidding_deadline_seconds=43200,
        acceptance_window_seconds=3600,
        execution_deadline_seconds=172800,
        review_deadline_seconds=43200,
        lock_timestamp="2026-03-01T09:10:00Z",
    )
    return task_id, escrow_id


def _snapshot(db_writer: DbWriter) -> tuple[str, ...]:
    return tuple(db_writer.connection.iterdump())


def _missing_task_cancel(task_id: str, actor_id: str) -> dict[str, object]:
    return operation_body(
        operation_id=f"op-{uuid4()}",
        task_id=task_id,
        command="cancel_task",
        expected_status="open",
        expected_lifecycle_version=0,
        actor_kind="agent",
        actor_id=actor_id,
        arguments={
            "poster_id": actor_id,
            "escrow_id": f"esc-{uuid4()}",
            "reward": 100,
            "source_reference": f"task:{task_id}:cancelled",
            "cutoff_us": BIDDING_CUTOFF_US,
        },
    )


@pytest.mark.unit
class TestCas04Cas05NotFoundNuance:
    """CAS-04/CAS-05: structural validation precedes native task lookup."""

    def test_constrained_update_on_missing_task_returns_generic_not_found(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """CAS-04: an obsolete constraints field is rejected before task lookup."""
        payload = _missing_task_cancel("t-does-not-exist", "a-does-not-exist")
        payload["constraints"] = {"status": "open"}

        before = _snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        assert _snapshot(db_writer) == before

    def test_unconstrained_update_on_missing_task_returns_task_not_found(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """CAS-05: a valid native command for an absent task is `task_not_found`."""
        actor_id = _register_agent(app_with_writer, name="Missing task actor")
        payload = _missing_task_cancel("t-does-not-exist", actor_id)

        before = _snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 404
        assert response.json()["error"] == "task_not_found"
        assert _snapshot(db_writer) == before


@pytest.mark.unit
class TestCas09AssetCrossTableConstraint:
    """CAS-09: Asset recording with a cross-table constraint."""

    def test_asset_recording_rejected_when_task_no_longer_open(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        graph = create_native_task(app_with_writer)
        uploader_id = register_worker(app_with_writer, name="Uploader")
        bid_id, bid = submit_bid(app_with_writer, graph, uploader_id, amount=55)
        assert bid.status_code == 200
        accepted = accept_bid(app_with_writer, graph, uploader_id, bid_id, bid_amount=55)
        assert accepted.status_code == 200

        before = _snapshot(db_writer)
        response = app_with_writer.post(
            "/board/assets",
            json={
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
                "asset_id": f"asset-{uuid4()}",
                "task_id": graph.task_id,
                "uploader_id": uploader_id,
                "filename": "deliverable.zip",
                "content_type": "application/zip",
                "size_bytes": 1024,
                "storage_path": f"{graph.task_id}/deliverable.zip",
                "uploaded_at": "2099-01-01T12:00:01Z",
                "constraints": {"status": "open"},
                "event": make_event(
                    source="board",
                    event_type="asset.uploaded",
                    task_id=graph.task_id,
                ),
            },
        )
        assert response.status_code == 409
        assert response.json()["error"] == "constraint_violation"
        assert response.json()["details"] == {
            "table": "board_tasks",
            "conditions": {"task_id": graph.task_id, "status": "open"},
        }
        assert _snapshot(db_writer) == before


@pytest.mark.unit
class TestCas10RebuttalConstraint:
    """CAS-10: Rebuttal claim-status update honors constraints."""

    def test_rebuttal_with_matching_claim_status_constraint_succeeds(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        graph = create_native_task(app_with_writer)
        worker_id, _bid_id = prepare_submitted(app_with_writer, graph)
        reason = "Work was not delivered"
        dispute_operation_id = f"op-{uuid4()}"
        dispute = operation_body(
            operation_id=dispute_operation_id,
            task_id=graph.task_id,
            command="dispute_task",
            expected_status="submitted",
            expected_lifecycle_version=2,
            actor_kind="agent",
            actor_id=graph.poster_id,
            arguments={
                "poster_id": graph.poster_id,
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "reason": reason,
            },
        )
        pending = app_with_writer.post("/board/task-operations", json=dispute)
        assert pending.status_code == 202
        claim_id = f"clm-{uuid4()}"
        claim_response = file_claim(
            app_with_writer,
            graph,
            worker_id=worker_id,
            claim_id=claim_id,
            reason=reason,
        )
        assert claim_response.status_code == 201
        disputed = finalize(app_with_writer, dispute_operation_id)
        assert disputed.status_code == 200
        assert disputed.json()["result"]["status"] == "disputed"

        event_cursor = db_writer.connection.execute(
            "SELECT COALESCE(MAX(event_id),0) FROM events"
        ).fetchone()[0]
        response = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": worker_id,
                "content": "The work was delivered on time",
                "submitted_at": "2099-01-01T12:00:03Z",
                "claim_status_update": "rebuttal",
                "constraints": {"status": "rebuttal_pending"},
                "event": make_event(
                    source="court",
                    event_type="rebuttal.submitted",
                    task_id=graph.task_id,
                ),
            },
        )
        assert response.status_code == 201

        _wire_db_reader()
        claim_check = app_with_writer.get(f"/court/claims/{claim_id}")
        assert claim_check.status_code == 200
        assert claim_check.json()["status"] == "rebuttal"
        task = db_writer.connection.execute(
            "SELECT status,rebuttal_submitted_at FROM board_tasks WHERE task_id=?",
            (graph.task_id,),
        ).fetchone()
        contract = db_writer.connection.execute(
            "SELECT lifecycle_version FROM board_task_contracts WHERE task_id=?",
            (graph.task_id,),
        ).fetchone()
        events = db_writer.connection.execute(
            "SELECT event_source,event_type,task_id,agent_id FROM events WHERE event_id>?",
            (event_cursor,),
        ).fetchall()
        assert task is not None and tuple(task) == (
            "disputed",
            "2099-01-01T12:00:03Z",
        )
        assert contract is not None and tuple(contract) == (3,)
        assert len(events) == 1
        assert tuple(events[0]) == (
            "court",
            "rebuttal.submitted",
            graph.task_id,
            worker_id,
        )


@pytest.mark.unit
class TestCas07AlreadyResolvedEscrow:
    """The financial command owns the locked-state precondition."""

    def test_opposing_split_returns_true_release_winner(self, app_with_writer: TestClient) -> None:
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=100)
        worker_id = _create_funded_account(app_with_writer, name="Worker", balance=0)
        task_id, escrow_id = _create_task(app_with_writer, poster_id=poster_id)

        release_response = app_with_writer.post(
            "/bank/escrow/release",
            json={
                "operation_id": f"op-{uuid4()}",
                "escrow_id": escrow_id,
                "recipient_account_id": worker_id,
                "source_reference": f"release-{task_id}",
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-03-01T09:20:00Z",
                "event": make_event(source="bank", event_type="escrow.released", task_id=task_id),
            },
        )
        assert release_response.status_code == 200

        split_response = app_with_writer.post(
            "/bank/escrow/split",
            json={
                "operation_id": f"op-{uuid4()}",
                "escrow_id": escrow_id,
                "worker_account_id": worker_id,
                "poster_account_id": poster_id,
                "worker_pct": 60,
                "source_reference": f"split-{task_id}",
                "worker_tx_id": f"tx-{uuid4()}",
                "poster_tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-03-01T09:21:00Z",
                "event": make_event(source="bank", event_type="escrow.split", task_id=task_id),
            },
        )
        assert split_response.status_code == 409
        assert split_response.json()["error"] == "financial_operation_already_committed"
        assert (
            split_response.json()["details"]["winning_operation_id"]
            == release_response.json()["receipt"]["operation_id"]
        )


@pytest.mark.unit
class TestCas11InvalidConstraintsType:
    """CAS-11: constraints value present but not an object is rejected."""

    def test_string_constraints_value_is_rejected(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        graph = create_native_task(app_with_writer)
        uploader_id = _register_agent(app_with_writer, name="Uploader")
        before = _snapshot(db_writer)
        response = app_with_writer.post(
            "/board/assets",
            json={
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
                "asset_id": f"asset-{uuid4()}",
                "task_id": graph.task_id,
                "uploader_id": uploader_id,
                "filename": "deliverable.zip",
                "content_type": "application/zip",
                "size_bytes": 1024,
                "storage_path": f"{graph.task_id}/deliverable.zip",
                "uploaded_at": "2099-01-01T12:00:01Z",
                "constraints": "open",
                "event": make_event(
                    source="board",
                    event_type="asset.uploaded",
                    task_id=graph.task_id,
                ),
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_constraints"
        assert _snapshot(db_writer) == before
