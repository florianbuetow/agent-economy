"""T-122 HTTP boundary for native task-operation claims and finalization.

The baseline lacks both operation routes. The scenario test therefore reports
one missing operation-interface capability after a real signed HTTP request;
post-claim assertions remain intentionally unexecuted until that producer
exists. The separate control proves the signed router/DbWriter graph is real.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair

from db_gateway_service.config import clear_settings_cache
from db_gateway_service.core.state import init_app_state
from tests.conftest import make_event
from tests.unit.conftest import _create_test_config

if TYPE_CHECKING:
    from collections.abc import Iterator

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit

_CLAIM_PATH = "/board/task-operations"


@dataclass(frozen=True)
class _Graph:
    task_id: str
    poster_id: str
    worker_id: str
    escrow_id: str
    bid_id: str


@dataclass(frozen=True)
class _OperationGateway:
    client: TestClient
    signers: dict[str, GatewayRequestSigner]


class _DomainAuth(httpx.Auth):
    """Sign ordinary setup requests with each trusted domain key."""

    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        domain = request.url.path.split("/", maxsplit=2)[1]
        signer = self._signers[domain]
        access = "read" if request.method == "GET" else "write"
        yield signer.sign_request(request, action=f"gateway.{domain}.{access}")


@pytest.fixture
def operation_gateway(
    db_writer: DbWriter,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_OperationGateway]:
    """Real Gateway with two distinct trusted Board writer identities."""
    recovery_kid = "gateway-task-board-recovery"
    generate_keypair(recovery_kid, tmp_path)
    recovery_signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid=recovery_kid,
            private_key_path=Path(f"{recovery_kid}.key"),
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )
    signers = {**gateway_test_signers, "board_recovery": recovery_signer}
    config_path = tmp_path / "operation-config.yaml"
    _create_test_config(db_writer._db_path, config_path)
    with config_path.open("a") as config_file:
        config_file.write(
            f"""
    {recovery_kid}:
      public_key_path: "{recovery_kid}.pub"
      capabilities: ["gateway.board.read", "gateway.board.write"]
"""
        )

    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    clear_settings_cache()
    from db_gateway_service.app import create_app

    app = create_app()
    with TestClient(app) as client:
        client.auth = _DomainAuth(signers)
        state = init_app_state()
        state.db_writer = db_writer
        yield _OperationGateway(client, signers)

    os.environ.pop("CONFIG_PATH", None)
    clear_settings_cache()


def _agent_body(agent_id: str) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "name": agent_id,
        "public_key": f"ed25519:{uuid4()}",
        "registered_at": "2026-10-17T09:00:00Z",
        "event": make_event(source="identity", event_type="agent.registered", agent_id=agent_id),
    }


def _seed_graph(client: TestClient) -> _Graph:
    suffix = str(uuid4())
    poster_id = f"a-mp07-poster-{suffix}"
    worker_id = f"a-mp07-worker-{suffix}"
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    bid_id = f"bid-{uuid4()}"

    for agent_id in (poster_id, worker_id):
        response = client.post("/identity/agents", json=_agent_body(agent_id))
        assert response.status_code == 201, response.text
    poster_account = client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid4()}",
            "account_id": poster_id,
            "balance": 100,
            "created_at": "2026-10-17T09:00:01Z",
            "initial_credit": {
                "tx_id": f"tx-{uuid4()}",
                "amount": 100,
                "reference": "initial_balance",
                "timestamp": "2026-10-17T09:00:01Z",
            },
            "event": make_event(source="bank", event_type="account.created", agent_id=poster_id),
        },
    )
    assert poster_account.status_code == 201, poster_account.text
    worker_account = client.post(
        "/bank/accounts",
        json={
            "account_id": worker_id,
            "balance": 0,
            "created_at": "2026-10-17T09:00:02Z",
            "event": make_event(source="bank", event_type="account.created", agent_id=worker_id),
        },
    )
    assert worker_account.status_code == 201, worker_account.text
    lock = client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": f"op-{uuid4()}",
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": 100,
            "task_id": task_id,
            "created_at": "2026-10-17T09:00:03Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=task_id),
        },
    )
    assert lock.status_code == 201, lock.text
    task = client.post(
        "/board/tasks",
        json={
            "task_id": task_id,
            "poster_id": poster_id,
            "title": "MP-07 operation boundary",
            "spec": "Exercise native command claim and finalization semantics.",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 86400,
            "acceptance_window_seconds": 86400,
            "deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
            "bidding_deadline": "2099-01-02T00:00:00Z",
            "bidding_cutoff_us": 4070995200000000,
            "acceptance_cutoff_us": 4071081600000000,
            "escrow_id": escrow_id,
            "created_at": "2099-01-01T00:00:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=task_id),
        },
    )
    assert task.status_code == 201, task.text
    bid = client.post(
        _CLAIM_PATH,
        json={
            "protocol_version": 1,
            "operation_id": f"op-{uuid4()}",
            "task_id": task_id,
            "command": "submit_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": worker_id,
            "arguments": {"bid_id": bid_id, "bidder_id": worker_id, "amount": 90},
        },
    )
    assert bid.status_code == 200, bid.text
    assert bid.json()["operation_state"] == "completed"
    return _Graph(task_id, poster_id, worker_id, escrow_id, bid_id)


def _accept_command(graph: _Graph, operation_id: str) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": graph.task_id,
        "command": "accept_bid",
        "expected_status": "open",
        "expected_lifecycle_version": 0,
        "actor_kind": "agent",
        "actor_id": graph.poster_id,
        "arguments": {
            "bid_id": graph.bid_id,
            "worker_id": graph.worker_id,
            "escrow_id": graph.escrow_id,
            "reward": 100,
            "bid_amount": 90,
        },
    }


def _approve_command(graph: _Graph, operation_id: str) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": graph.task_id,
        "command": "approve_task",
        "expected_status": "submitted",
        "expected_lifecycle_version": 2,
        "actor_kind": "agent",
        "actor_id": graph.poster_id,
        "arguments": {
            "poster_id": graph.poster_id,
            "worker_id": graph.worker_id,
            "escrow_id": graph.escrow_id,
            "reward": 100,
            "source_reference": f"task:{graph.task_id}:approved",
        },
    }


def _signed_post(
    client: TestClient,
    signer: GatewayRequestSigner,
    *,
    path: str,
    action: str,
    payload: dict[str, object],
) -> httpx.Response:
    body = json.dumps(payload, separators=(",", ":")).encode()
    signed = signer.sign_request(
        httpx.Request(
            "POST",
            f"http://testserver{path}",
            content=body,
            headers={"Content-Type": "application/json"},
        ),
        action=action,
    )
    return client.post(
        path,
        content=body,
        headers={
            "Authorization": signed.headers["Authorization"],
            "Content-Type": "application/json",
        },
        auth=None,
    )


def _task_snapshot(writer: DbWriter, graph: _Graph) -> tuple[object, ...]:
    task_row = writer.connection.execute(
        "SELECT status, worker_id, accepted_bid_id, accepted_at, submitted_at, approved_at "
        "FROM board_tasks WHERE task_id = ?",
        (graph.task_id,),
    ).fetchone()
    assert task_row is not None
    bid_rows = writer.connection.execute(
        "SELECT bid_id, bidder_id, amount FROM board_bids WHERE task_id = ? ORDER BY bid_id",
        (graph.task_id,),
    ).fetchall()
    event_rows = writer.connection.execute(
        "SELECT event_type FROM events WHERE task_id = ? AND event_source = 'board' "
        "ORDER BY event_id",
        (graph.task_id,),
    ).fetchall()
    return (
        tuple(task_row),
        tuple(tuple(row) for row in bid_rows),
        tuple(tuple(row) for row in event_rows),
    )


def _assert_accept_envelope(payload: dict[str, Any], graph: _Graph, operation_id: str) -> None:
    assert payload["operation_id"] == operation_id
    assert payload["task_id"] == graph.task_id
    assert payload["command"] == "accept_bid"
    assert payload["operation_state"] == "completed"
    assert payload["effect_kind"] == "none"
    assert payload["effect_state"] == "none"
    assert payload["event_id"] is not None
    result = payload["result"]
    assert isinstance(result, dict)
    assert result["task_id"] == graph.task_id
    assert result["status"] == "accepted"
    assert result["bid_id"] == graph.bid_id
    assert result["worker_id"] == graph.worker_id
    assert result["reward"] == 100
    assert result["bid_amount"] == 90


def _advance_claim_fixture_to_submitted(writer: DbWriter, graph: _Graph) -> None:
    """Install a consistent phase fixture; this is not public submit-workflow proof."""
    writer.connection.execute(
        "UPDATE board_tasks SET status='submitted', submitted_at='2099-01-01T00:00:03Z', "
        "review_deadline='2099-01-02T00:00:03Z' WHERE task_id = ?",
        (graph.task_id,),
    )
    writer.connection.execute(
        "UPDATE board_task_contracts SET lifecycle_version=2, "
        "review_cutoff_us=4070995203000000 WHERE task_id = ?",
        (graph.task_id,),
    )
    writer.connection.commit()


def _mismatched_release_body(graph: _Graph, operation_id: str) -> dict[str, object]:
    return {
        "operation_id": operation_id,
        "escrow_id": graph.escrow_id,
        "recipient_account_id": graph.poster_id,
        "source_reference": f"task:{graph.task_id}:cancelled",
        "tx_id": f"tx-{uuid4()}",
        "resolved_at": "2099-01-01T00:00:04Z",
        "event": make_event(source="bank", event_type="escrow.released", task_id=graph.task_id),
    }


def _financial_mismatch_facts(
    writer: DbWriter,
    graph: _Graph,
    operation_id: str,
) -> tuple[object, ...]:
    receipt = writer.connection.execute(
        "SELECT operation_kind, source_reference, escrow_id, event_id "
        "FROM bank_operation_receipts WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    assert receipt is not None
    event = writer.connection.execute(
        "SELECT event_source, event_type, task_id FROM events WHERE event_id = ?",
        (receipt[3],),
    ).fetchone()
    escrow = writer.connection.execute(
        "SELECT status, resolved_at FROM bank_escrow WHERE escrow_id = ?",
        (graph.escrow_id,),
    ).fetchone()
    balance = writer.connection.execute(
        "SELECT balance FROM bank_accounts WHERE account_id = ?",
        (graph.poster_id,),
    ).fetchone()
    assert event is not None and escrow is not None and balance is not None
    return tuple(receipt), tuple(event), tuple(escrow), tuple(balance)


def _assert_accept_replay_conflicts_and_authority(
    client: TestClient,
    writer: DbWriter,
    signers: dict[str, GatewayRequestSigner],
    graph: _Graph,
    operation_id: str,
    command: dict[str, object],
    envelope: dict[str, Any],
) -> None:
    accepted_state = _task_snapshot(writer, graph)

    replay = client.post(_CLAIM_PATH, json=command)
    assert replay.status_code == 200, replay.text
    _assert_accept_envelope(replay.json(), graph, operation_id)
    assert replay.json() == envelope
    assert _task_snapshot(writer, graph) == accepted_state

    changed = json.loads(json.dumps(command))
    changed_arguments = changed.get("arguments")
    assert isinstance(changed_arguments, dict)
    changed_arguments["bid_amount"] = 80
    for foreign_payload in (command, changed):
        foreign_replay = _signed_post(
            client,
            signers["board_recovery"],
            path=_CLAIM_PATH,
            action="gateway.board.write",
            payload=foreign_payload,
        )
        assert foreign_replay.status_code == 403, foreign_replay.text
        assert foreign_replay.json()["error"] == "operation_caller_mismatch"
        assert _task_snapshot(writer, graph) == accepted_state
    changed_response = client.post(_CLAIM_PATH, json=changed)
    assert changed_response.status_code == 409, changed_response.text
    assert changed_response.json()["error"] == "operation_id_conflict"
    assert _task_snapshot(writer, graph) == accepted_state

    cross_response = client.post(_CLAIM_PATH, json=_approve_command(graph, operation_id))
    assert cross_response.status_code == 409, cross_response.text
    assert cross_response.json()["error"] == "operation_id_conflict"
    assert _task_snapshot(writer, graph) == accepted_state

    injected = _accept_command(graph, f"op-{uuid4()}")
    injected["caller_kid"] = "gateway-task-board"
    injected_response = client.post(_CLAIM_PATH, json=injected)
    assert injected_response.status_code == 400, injected_response.text
    assert injected_response.json()["error"] == "invalid_payload"
    assert _task_snapshot(writer, graph) == accepted_state

    wrong_capability = _signed_post(
        client,
        signers["identity"],
        path=_CLAIM_PATH,
        action="gateway.board.write",
        payload=_accept_command(graph, f"op-{uuid4()}"),
    )
    assert wrong_capability.status_code == 403, wrong_capability.text
    assert wrong_capability.json()["error"] == "forbidden"
    assert _task_snapshot(writer, graph) == accepted_state


def _assert_approve_claim_and_evidence_finalization(
    client: TestClient,
    writer: DbWriter,
    signers: dict[str, GatewayRequestSigner],
    graph: _Graph,
) -> None:
    _advance_claim_fixture_to_submitted(writer, graph)
    operation_id = f"op-{uuid4()}"
    command = _approve_command(graph, operation_id)

    approve = client.post(_CLAIM_PATH, json=command)
    assert approve.status_code == 202, approve.text
    pending = approve.json()
    assert pending["operation_id"] == operation_id
    assert pending["command"] == "approve_task"
    assert pending["operation_state"] == "claimed"
    assert pending["effect_kind"] == "bank_release"
    assert pending["effect_state"] == "pending"
    assert pending["effect_operation_id"] == operation_id
    assert pending["event_id"] is None
    assert pending["result"] is None

    pending_state = _task_snapshot(writer, graph)
    pending_replay = client.post(_CLAIM_PATH, json=command)
    assert pending_replay.status_code == 202, pending_replay.text
    assert pending_replay.json() == pending
    assert _task_snapshot(writer, graph) == pending_state

    finalize_path = f"{_CLAIM_PATH}/{operation_id}/finalize"
    forged = client.post(
        finalize_path,
        json={"receipt": {"status": "released"}, "caller_kid": "gateway-task-board"},
    )
    assert forged.status_code == 400, forged.text
    assert forged.json()["error"] == "invalid_payload"
    assert _task_snapshot(writer, graph) == pending_state

    wrong_finalizer = _signed_post(
        client,
        signers["identity"],
        path=finalize_path,
        action="gateway.board.write",
        payload={},
    )
    assert wrong_finalizer.status_code == 403, wrong_finalizer.text
    assert wrong_finalizer.json()["error"] == "forbidden"
    wrong_caller = _signed_post(
        client,
        signers["board_recovery"],
        path=finalize_path,
        action="gateway.board.write",
        payload={},
    )
    assert wrong_caller.status_code == 403, wrong_caller.text
    assert wrong_caller.json()["error"] == "operation_caller_mismatch"
    missing = client.post(finalize_path, json={})
    assert missing.status_code == 202, missing.text
    assert missing.json() == pending
    assert _task_snapshot(writer, graph) == pending_state

    _assert_mismatched_evidence_blocks(
        client,
        writer,
        graph,
        operation_id,
        command,
        finalize_path,
        pending_state,
    )


def _assert_mismatched_evidence_blocks(
    client: TestClient,
    writer: DbWriter,
    graph: _Graph,
    operation_id: str,
    command: dict[str, object],
    finalize_path: str,
    pending_state: tuple[object, ...],
) -> None:
    """Prove contradictory durable evidence remains blocked across replay."""

    mismatched_receipt = client.post(
        "/bank/escrow/release",
        json=_mismatched_release_body(graph, operation_id),
    )
    assert mismatched_receipt.status_code == 200, mismatched_receipt.text
    mismatch_facts = _financial_mismatch_facts(writer, graph, operation_id)
    assert mismatch_facts[0][:3] == (
        "escrow_release",
        f"task:{graph.task_id}:cancelled",
        graph.escrow_id,
    )
    assert mismatch_facts[1] == ("bank", "escrow.released", graph.task_id)
    assert mismatch_facts[2][0] == "released"
    assert mismatch_facts[2][1] is not None
    assert mismatch_facts[3] == (100,)
    assert _task_snapshot(writer, graph) == pending_state
    conflict = client.post(finalize_path, json={})
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"] == "effect_evidence_conflict"
    conflict_payload = conflict.json()
    operation_row = writer.connection.execute(
        "SELECT operation_state, effect_state FROM board_task_operations WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    assert operation_row is not None
    assert tuple(operation_row) == ("blocked", "conflict")
    assert _task_snapshot(writer, graph) == pending_state
    command_retry = client.post(_CLAIM_PATH, json=command)
    finalize_retry = client.post(finalize_path, json={})
    assert command_retry.status_code == 409, command_retry.text
    assert finalize_retry.status_code == 409, finalize_retry.text
    assert command_retry.json() == conflict_payload
    assert finalize_retry.json() == conflict_payload
    assert _task_snapshot(writer, graph) == pending_state


def test_signed_router_fixture_reaches_real_writer(
    operation_gateway: _OperationGateway,
    db_writer: DbWriter,
) -> None:
    graph = _seed_graph(operation_gateway.client)
    snapshot = _task_snapshot(db_writer, graph)

    assert snapshot[0] == ("open", None, None, None, None, None)
    assert snapshot[1] == ((graph.bid_id, graph.worker_id, 90),)
    assert snapshot[2][-2:] == (("task.created",), ("bid.submitted",))


def test_native_task_operation_claim_replay_and_finalization_boundary(
    operation_gateway: _OperationGateway,
    db_writer: DbWriter,
) -> None:
    """Run ten bounded scenarios once the operation producer exists.

    Baseline stops after scenario1 with one missing-interface failure. Scenarios
    2-10 deliberately remain behind that capability gate instead of becoming
    misleading independent product failures.
    """
    client = operation_gateway.client
    graph = _seed_graph(client)
    accept_operation_id = f"op-{uuid4()}"
    accept_command = _accept_command(graph, accept_operation_id)
    before = _task_snapshot(db_writer, graph)

    # 1. Pure accept claim through the real signed route.
    accept = client.post(_CLAIM_PATH, json=accept_command)
    missing_route_policy = (
        accept.status_code == 403
        and accept.json().get("error") == "forbidden"
        and accept.json().get("message") == "Gateway route has no explicit capability policy"
    )
    if accept.status_code == 404 or missing_route_policy:
        assert _task_snapshot(db_writer, graph) == before
        pytest.fail(
            "Missing operation-interface capability: POST /board/task-operations has no route "
            "and explicit gateway.board.write policy; "
            "scenarios 2-10 (replay, conflicts, approve claim, authorization, and evidence "
            "finalization) were not executed"
        )
    assert accept.status_code == 200, accept.text
    accepted_envelope = accept.json()
    _assert_accept_envelope(accepted_envelope, graph, accept_operation_id)
    _assert_accept_replay_conflicts_and_authority(
        client,
        db_writer,
        operation_gateway.signers,
        graph,
        accept_operation_id,
        accept_command,
        accepted_envelope,
    )
    _assert_approve_claim_and_evidence_finalization(
        client,
        db_writer,
        operation_gateway.signers,
        graph,
    )
