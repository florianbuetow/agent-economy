"""Protected native task-effect finalizer HTTP boundary for MP-08."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair

from db_gateway_service import task_time
from db_gateway_service.config import clear_settings_cache
from db_gateway_service.core.state import init_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.fakes.gateway_mp08_config import write_gateway_config
from tests.fakes.gateway_mp08_effects import (
    delivery,
    lease,
    prepare_native_pending,
)
from tests.fakes.native_board_history import (
    FIXED_NOW_US,
    BusinessClock,
    TaskGraph,
    settle_release,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
_WORKER = "recovery-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class _DomainAuth(httpx.Auth):
    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        domain = request.url.path.split("/", maxsplit=2)[1]
        protected_delivery = request.url.path.startswith(
            "/board/task-effect-deliveries"
        ) or request.url.path.startswith("/board/legacy-effect-observations/")
        access = "write" if protected_delivery or request.method != "GET" else "read"
        yield self._signers[domain].sign_request(
            request,
            action=f"gateway.{domain}.{access}",
        )


def _signer(kid: str, directory: Path) -> GatewayRequestSigner:
    generate_keypair(kid, directory)
    return GatewayRequestSigner(
        GatewaySignerConfig(
            kid=kid,
            private_key_path=Path(f"{kid}.key"),
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=directory,
    )


@pytest.fixture
def finalizer_boundary(
    db_writer: DbWriter,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    tmp_path: Path,
) -> Iterator[tuple[TestClient, dict[str, GatewayRequestSigner]]]:
    foreign = _signer("gateway-task-board-foreign-finalizer", tmp_path)
    read_only = _signer("gateway-task-board-read-only-finalizer", tmp_path)
    config_path = tmp_path / "mp08-finalizer-config.yaml"
    write_gateway_config(db_writer._db_path, config_path)
    with config_path.open("a") as config:
        config.write(
            """
    gateway-task-board-foreign-finalizer:
      public_key_path: "gateway-task-board-foreign-finalizer.pub"
      capabilities: ["gateway.board.read", "gateway.board.write"]
    gateway-task-board-read-only-finalizer:
      public_key_path: "gateway-task-board-read-only-finalizer.pub"
      capabilities: ["gateway.board.read"]
"""
        )
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    from db_gateway_service.app import create_app

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=FIXED_NOW_US))
    try:
        with TestClient(create_app()) as client:
            client.auth = _DomainAuth(gateway_test_signers)
            state = init_app_state()
            state.db_writer = db_writer
            state.db_reader = DbReader(db_writer.connection)
            yield (
                client,
                {
                    "owner": gateway_test_signers["board"],
                    "foreign": foreign,
                    "read_only": read_only,
                },
            )
    finally:
        monkeypatch.undo()
        os.environ.pop("CONFIG_PATH", None)
        clear_settings_cache()


def _signed(
    client: TestClient,
    signer: GatewayRequestSigner,
    method: str,
    path: str,
    *,
    body: dict[str, object] | None,
) -> httpx.Response:
    content = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = signer.sign_request(
        httpx.Request(
            method,
            f"http://testserver{path}",
            content=content,
            headers={"Content-Type": "application/json"},
        ),
        action="gateway.board.write",
    )
    return client.request(
        method,
        path,
        content=content,
        headers={
            "Authorization": request.headers["Authorization"],
            "Content-Type": "application/json",
        },
        auth=None,
    )


def _path(operation_id: str) -> str:
    return f"/board/task-effect-deliveries/{operation_id}/finalize"


def _snapshot(writer: DbWriter) -> tuple[str, ...]:
    return tuple(writer.connection.iterdump())


def test_auth_capability_and_owner_precedence(
    finalizer_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
) -> None:
    client, signers = finalizer_boundary
    effect = prepare_native_pending(client)
    path = _path(effect.operation_id)
    unauthenticated = client.request(
        "POST",
        path,
        content=b"{}",
        headers={"Content-Type": "application/json"},
        auth=None,
    )
    assert unauthenticated.status_code == 401
    denied = _signed(client, signers["read_only"], "POST", path, body={})
    assert denied.status_code == 403
    hidden = _signed(client, signers["foreign"], "POST", path, body={})
    assert hidden.status_code == 404


@pytest.mark.parametrize(
    ("operation_id", "body"),
    [("not-an-operation", {}), ("op-11111111-1111-4111-8111-111111111111", {"x": 1})],
)
def test_invalid_identifier_and_nonempty_body_are_rejected_without_mutation(
    finalizer_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    db_writer: DbWriter,
    operation_id: str,
    body: dict[str, object],
) -> None:
    client, signers = finalizer_boundary
    if operation_id.startswith("op-"):
        effect = prepare_native_pending(client)
        operation_id = effect.operation_id
    before = _snapshot(db_writer)
    response = _signed(client, signers["owner"], "POST", _path(operation_id), body=body)
    assert response.status_code == 400
    assert response.json()["error"] in {"invalid_payload", "invalid_operation_id"}
    assert _snapshot(db_writer) == before


@pytest.mark.parametrize("state", ["pending", "leased", "dead_letter"])
def test_receipt_absent_returns_202_with_exact_unchanged_nonterminal_state(
    finalizer_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    db_writer: DbWriter,
    state: str,
) -> None:
    client, signers = finalizer_boundary
    effect = prepare_native_pending(client)
    if state == "leased":
        lease(client, _WORKER)
    elif state == "dead_letter":
        db_writer.connection.execute(
            "UPDATE board_task_effect_deliveries SET delivery_state='dead_letter',"
            "next_attempt_at_us=NULL,next_evidence_check_at_us=enqueued_at_us,"
            "last_failure_code='central_bank_unavailable',last_failure_at_us=enqueued_at_us "
            "WHERE effect_operation_id=?",
            (effect.operation_id,),
        )
        db_writer.connection.commit()
    before_delivery = delivery(client, effect.operation_id)
    before_database = _snapshot(db_writer)

    response = _signed(
        client,
        signers["owner"],
        "POST",
        _path(effect.operation_id),
        body={},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {"protocol_version": 1, "delivery": before_delivery}
    assert delivery(client, effect.operation_id)["delivery_state"] == state
    assert _snapshot(db_writer) == before_database
    diagnostic = response.json()["delivery"]
    assert diagnostic["receipt_reference"] is None
    if state == "leased":
        assert diagnostic["lease_owner_id"] == _WORKER
        assert diagnostic["lease_generation"] > 0
    else:
        assert diagnostic["lease_owner_id"] is None
        assert diagnostic["lease_acquired_at_us"] is None
        assert diagnostic["lease_expires_at_us"] is None


def test_clock_exhaustion_is_503_and_transaction_is_unchanged(
    finalizer_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, signers = finalizer_boundary
    effect = prepare_native_pending(client)
    monkeypatch.setattr(task_time, "utc_now_us", lambda: 253_402_300_800_000_000)
    before = _snapshot(db_writer)
    response = _signed(client, signers["owner"], "POST", _path(effect.operation_id), body={})
    assert response.status_code == 503
    assert response.json()["error"] == "effect_delivery_clock_exhausted"
    assert _snapshot(db_writer) == before


def test_exact_receipt_completes_operation_sidecar_task_and_replays_without_mutation(
    finalizer_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    db_writer: DbWriter,
) -> None:
    client, signers = finalizer_boundary
    effect = prepare_native_pending(client)
    lease(client, _WORKER)
    receipt = settle_release(
        client,
        TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
        operation_id=effect.operation_id,
        recipient_id=effect.worker_id,
        source_reference=effect.source_reference,
    )
    assert receipt.status_code in {200, 201}, receipt.text
    bank_event_id = int(receipt.json()["receipt"]["event_id"])

    response = _signed(client, signers["owner"], "POST", _path(effect.operation_id), body={})
    assert response.status_code == 200, response.text
    result = response.json()["delivery"]
    assert result["delivery_state"] == "completed"
    assert result["receipt_reference"] == effect.operation_id
    assert result["completion_event_id"] == bank_event_id
    operation = db_writer.connection.execute(
        "SELECT operation_state,effect_state,effect_result_reference FROM board_task_operations "
        "WHERE operation_id=?",
        (effect.operation_id,),
    ).fetchone()
    assert operation is not None
    assert tuple(operation) == ("completed", "succeeded", effect.operation_id)
    task = db_writer.connection.execute(
        "SELECT status,escrow_pending FROM board_tasks WHERE task_id=?",
        (effect.task_id,),
    ).fetchone()
    assert task is not None
    assert tuple(task) == ("approved", 0)
    after = _snapshot(db_writer)
    replay = _signed(client, signers["owner"], "POST", _path(effect.operation_id), body={})
    assert replay.status_code == 200
    assert replay.json() == response.json()
    assert _snapshot(db_writer) == after


def test_conflicting_receipt_returns_409_and_blocks_native_source_atomically(
    finalizer_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    db_writer: DbWriter,
) -> None:
    client, signers = finalizer_boundary
    effect = prepare_native_pending(client)
    receipt = settle_release(
        client,
        TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
        operation_id=effect.operation_id,
        recipient_id=effect.poster_id,
        source_reference=effect.source_reference,
    )
    assert receipt.status_code in {200, 201}, receipt.text
    response = _signed(client, signers["owner"], "POST", _path(effect.operation_id), body={})
    assert response.status_code == 409
    assert response.json()["error"] == "effect_evidence_conflict"
    operation = db_writer.connection.execute(
        "SELECT operation_state,effect_state FROM board_task_operations WHERE operation_id=?",
        (effect.operation_id,),
    ).fetchone()
    assert operation is not None
    assert tuple(operation) == ("blocked", "conflict")
    assert delivery(client, effect.operation_id)["delivery_state"] == "integrity_blocked"


def test_old_mp07_operation_finalizer_remains_a_distinct_operation_envelope(
    finalizer_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
) -> None:
    client, _signers = finalizer_boundary
    effect = prepare_native_pending(client)
    response = client.post(f"/board/task-operations/{effect.operation_id}/finalize", json={})
    assert response.status_code == 202
    assert response.json()["operation_id"] == effect.operation_id
    assert "operation_state" in response.json()
    assert "delivery" not in response.json()
