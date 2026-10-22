"""MP-02 acceptance proofs for fail-closed Database Gateway authority."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
from fastapi import Response
from fastapi.routing import APIRoute
from service_auth import jws
from service_auth.financial_identity import operation_id_for
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair
from service_auth.task_postings import TaskPostingIntent

from db_gateway_service import task_time
from db_gateway_service.core.middleware import GATEWAY_ROUTE_POLICIES
from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event
from tests.unit.conftest import _create_test_config

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


def _agent_body(agent_id: str) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "name": f"Agent {agent_id}",
        "public_key": f"ed25519:{uuid4()}",
        "registered_at": "2026-02-28T10:00:00Z",
        "event": make_event(),
    }


def _seed_domain_graph(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    now_us = task_time.parse_utc_text_to_epoch_us("2026-02-28T10:04:00Z")
    monkeypatch.setattr(task_time, "utc_now_us", lambda: now_us)
    poster = f"a-{uuid4()}"
    worker = f"a-{uuid4()}"
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    for agent_id in (poster, worker):
        assert client.post("/identity/agents", json=_agent_body(agent_id)).status_code == 201
    assert (
        client.post(
            "/bank/accounts",
            json={
                "operation_id": f"op-{uuid4()}",
                "account_id": poster,
                "balance": 500,
                "created_at": "2026-02-28T10:01:00Z",
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": 500,
                    "reference": "initial_balance",
                    "timestamp": "2026-02-28T10:01:00Z",
                },
                "event": make_event(source="bank", event_type="account.created"),
            },
        ).status_code
        == 201
    )
    intent = TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid4()}",
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": poster,
            "lock_operation_id": operation_id_for("task-lock", (task_id, poster)),
            "title": "Authority fixture",
            "spec": "Persist one valid task",
            "reward": 100,
            "bidding_deadline_seconds": 86400,
            "acceptance_window_seconds": 3600,
            "execution_deadline_seconds": 172800,
            "review_deadline_seconds": 43200,
        }
    )
    admitted = client.post("/board/task-publications", json=intent.as_json())
    assert admitted.status_code == 202, admitted.text
    assert admitted.json()["publication_state"] == "awaiting_lock"
    locked_at = task_time.epoch_us_to_utc_text(now_us)
    assert (
        client.post(
            "/bank/escrow/lock",
            json={
                "operation_id": intent.lock_operation_id,
                "escrow_id": escrow_id,
                "payer_account_id": poster,
                "amount": 100,
                "task_id": task_id,
                "created_at": locked_at,
                "tx_id": f"tx-{uuid4()}",
                "event": {
                    **make_event(
                        source="central-bank",
                        event_type="escrow.locked",
                        task_id=task_id,
                        agent_id=poster,
                    ),
                    "timestamp": locked_at,
                },
            },
        ).status_code
        == 201
    )
    published = client.post(f"/board/task-publications/{intent.operation_id}/reconcile", json={})
    assert published.status_code == 201, published.text
    assert published.json()["publication_state"] == "published"
    assert published.json()["task_id"] == task_id
    assert published.json()["escrow_id"] == escrow_id
    return {"poster": poster, "worker": worker, "task_id": task_id}


def _write_case(domain: str, graph: dict[str, str]) -> tuple[str, dict[str, Any], str]:
    if domain == "identity":
        return "/identity/agents", _agent_body(f"a-{uuid4()}"), "identity_agents"
    if domain == "bank":
        return (
            "/bank/credit",
            {
                "operation_id": f"op-{uuid4()}",
                "tx_id": f"tx-{uuid4()}",
                "account_id": graph["poster"],
                "amount": 5,
                "reference": f"authority-{uuid4()}",
                "timestamp": "2026-02-28T10:04:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
            "bank_transactions",
        )
    if domain == "board":
        return (
            "/board/task-operations",
            {
                "protocol_version": 1,
                "operation_id": f"op-{uuid4()}",
                "task_id": graph["task_id"],
                "command": "submit_bid",
                "expected_status": "open",
                "expected_lifecycle_version": 0,
                "actor_kind": "agent",
                "actor_id": graph["worker"],
                "arguments": {
                    "bid_id": f"bid-{uuid4()}",
                    "bidder_id": graph["worker"],
                    "amount": 75,
                },
            },
            "board_bids",
        )
    if domain == "reputation":
        return (
            "/reputation/feedback",
            {
                "feedback_id": f"fb-{uuid4()}",
                "task_id": graph["task_id"],
                "from_agent_id": graph["poster"],
                "to_agent_id": graph["worker"],
                "role": "poster",
                "category": "delivery_quality",
                "rating": 5,
                "comment": "Valid authority proof feedback",
                "submitted_at": "2026-02-28T10:06:00Z",
                "event": make_event(
                    source="reputation",
                    event_type="feedback.submitted",
                    task_id=graph["task_id"],
                ),
            },
            "reputation_feedback",
        )
    return (
        "/court/claims",
        {
            "claim_id": f"clm-{uuid4()}",
            "task_id": graph["task_id"],
            "claimant_id": graph["poster"],
            "respondent_id": graph["worker"],
            "reason": "Valid authority proof claim",
            "status": "filed",
            "filed_at": "2026-02-28T10:07:00Z",
            "event": make_event(source="court", event_type="claim.filed", task_id=graph["task_id"]),
        },
        "court_claims",
    )


@pytest.mark.unit
@pytest.mark.parametrize("domain", ["identity", "bank", "board", "reputation", "court"])
def test_unsigned_domain_write_is_rejected_without_state_or_event_changes(
    domain: str,
    app_with_writer: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _seed_domain_graph(app_with_writer, monkeypatch)
    path, body, table = _write_case(domain, graph)
    row_count = db_writer.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    event_count = db_writer.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    response = app_with_writer.post(path, json=body, auth=None)

    assert response.status_code == 401
    assert db_writer.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == row_count
    assert db_writer.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == event_count


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "/identity/agents/count",
        "/bank/accounts/count",
        "/board/tasks/count",
        "/reputation/feedback/count",
        "/court/claims/count",
    ],
)
def test_unsigned_domain_read_is_rejected(path: str, app_with_writer: TestClient) -> None:
    response = app_with_writer.get(path, auth=None)

    assert response.status_code == 401


@pytest.mark.unit
def test_health_is_the_only_public_route(app_with_writer: TestClient) -> None:
    assert app_with_writer.get("/health", auth=None).status_code == 200
    assert app_with_writer.head("/health", auth=None).status_code == 401


def _signed_authorization(
    signer: GatewayRequestSigner,
    method: str,
    path: str,
    *,
    action: str,
    body: bytes = b"",
) -> str:
    request = signer.sign_request(
        httpx.Request(method, f"http://testserver{path}", content=body),
        action=action,
    )
    return request.headers["Authorization"]


@pytest.mark.unit
def test_wrong_domain_capability_is_forbidden(
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
) -> None:
    authorization = _signed_authorization(
        gateway_test_signers["identity"],
        "GET",
        "/bank/accounts/count",
        action="gateway.bank.read",
    )

    response = app_with_writer.get(
        "/bank/accounts/count", headers={"Authorization": authorization}, auth=None
    )

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("header_values", "expected_status"),
    [
        (["Basic abc"], 401),
        (["Bearer"], 401),
        (["Bearer "], 401),
        (["Bearer abc", "Bearer def"], 401),
        (["Bearer abc"], 400),
    ],
)
def test_malformed_or_duplicate_bearer_is_rejected(
    header_values: list[str],
    expected_status: int,
    app_with_writer: TestClient,
) -> None:
    headers = [("Authorization", value) for value in header_values]

    response = app_with_writer.get("/identity/agents/count", headers=headers, auth=None)

    assert response.status_code == expected_status


@pytest.mark.unit
def test_unknown_key_and_bad_signature_are_forbidden(
    app_with_writer: TestClient,
    tmp_path: Path,
) -> None:
    unknown_dir = tmp_path / "unknown"
    unknown_dir.mkdir()
    generate_keypair("gateway-unknown", unknown_dir)
    unknown_signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-unknown",
            private_key_path="gateway-unknown.key",
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=unknown_dir,
    )
    unknown_auth = _signed_authorization(
        unknown_signer,
        "GET",
        "/identity/agents/count",
        action="gateway.identity.read",
    )

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    generate_keypair("gateway-identity", bad_dir)
    bad_signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-identity",
            private_key_path="gateway-identity.key",
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=bad_dir,
    )
    bad_auth = _signed_authorization(
        bad_signer,
        "GET",
        "/identity/agents/count",
        action="gateway.identity.read",
    )

    unknown_response = app_with_writer.get(
        "/identity/agents/count", headers={"Authorization": unknown_auth}, auth=None
    )
    bad_response = app_with_writer.get(
        "/identity/agents/count", headers={"Authorization": bad_auth}, auth=None
    )

    assert unknown_response.status_code == 403
    assert bad_response.status_code == 403


@pytest.mark.unit
def test_expired_and_future_tokens_are_unauthorized(
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued_at = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(jws, "_clock", lambda: issued_at)
    expired_auth = _signed_authorization(
        gateway_test_signers["identity"],
        "GET",
        "/identity/agents/count",
        action="gateway.identity.read",
    )
    monkeypatch.setattr(jws, "_clock", lambda: issued_at + timedelta(seconds=31))
    expired = app_with_writer.get(
        "/identity/agents/count", headers={"Authorization": expired_auth}, auth=None
    )

    future_time = issued_at + timedelta(minutes=5)
    monkeypatch.setattr(jws, "_clock", lambda: future_time)
    future_auth = _signed_authorization(
        gateway_test_signers["identity"],
        "GET",
        "/identity/agents/count",
        action="gateway.identity.read",
    )
    monkeypatch.setattr(jws, "_clock", lambda: issued_at)
    future = app_with_writer.get(
        "/identity/agents/count", headers={"Authorization": future_auth}, auth=None
    )

    assert expired.status_code == 401
    assert expired.json()["error"] == "token_expired"
    assert future.status_code == 401
    assert future.json()["error"] == "token_not_yet_valid"


@pytest.mark.unit
def test_body_and_query_tampering_are_invalid_payload(
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
) -> None:
    original_body = json.dumps(_agent_body(f"a-{uuid4()}"), separators=(",", ":")).encode()
    body_auth = _signed_authorization(
        gateway_test_signers["identity"],
        "POST",
        "/identity/agents",
        action="gateway.identity.write",
        body=original_body,
    )
    body_response = app_with_writer.post(
        "/identity/agents",
        content=original_body + b" ",
        headers={"Authorization": body_auth, "Content-Type": "application/json"},
        auth=None,
    )

    query_auth = _signed_authorization(
        gateway_test_signers["identity"],
        "GET",
        "/identity/agents?public_key=one%20key",
        action="gateway.identity.read",
    )
    query_response = app_with_writer.get(
        "/identity/agents?public_key=two%20keys",
        headers={"Authorization": query_auth},
        auth=None,
    )

    assert body_response.status_code == 400
    assert body_response.json()["error"] == "invalid_payload"
    assert query_response.status_code == 400
    assert query_response.json()["error"] == "invalid_payload"


@pytest.mark.unit
def test_body_bearing_delete_is_authenticated_and_replayed(
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
) -> None:
    body = json.dumps(
        {"event": make_event(source="court", event_type="ruling.deleted")},
        separators=(",", ":"),
    ).encode()
    authorization = _signed_authorization(
        gateway_test_signers["court"],
        "DELETE",
        "/court/rulings/missing",
        action="gateway.court.write",
        body=body,
    )

    response = app_with_writer.request(
        "DELETE",
        "/court/rulings/missing",
        content=body,
        headers={"Authorization": authorization, "Content-Type": "application/json"},
        auth=None,
    )

    assert response.status_code == 200
    assert response.json()["claim_id"] == "missing"


@pytest.mark.unit
def test_court_has_only_the_singular_board_task_read_grant(
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _seed_domain_graph(app_with_writer, monkeypatch)
    get_app_state().db_reader = DbReader(db=db_writer.connection)
    task_path = f"/board/tasks/{graph['task_id']}"
    narrow_auth = _signed_authorization(
        gateway_test_signers["court"],
        "GET",
        task_path,
        action="gateway.board.task.read",
    )
    list_auth = _signed_authorization(
        gateway_test_signers["court"],
        "GET",
        "/board/tasks",
        action="gateway.board.read",
    )

    narrow = app_with_writer.get(task_path, headers={"Authorization": narrow_auth}, auth=None)
    task_list = app_with_writer.get("/board/tasks", headers={"Authorization": list_auth}, auth=None)

    assert narrow.status_code == 200
    assert task_list.status_code == 403


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "/board/tasks/count",
        "/board/tasks/count-by-status",
        "/board/tasks",
        "/board/tasks/task-id/bids",
        "/board/tasks/task-id/assets",
    ],
)
def test_court_narrow_grant_denies_non_singular_board_task_reads(
    path: str,
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
) -> None:
    authorization = _signed_authorization(
        gateway_test_signers["court"],
        "GET",
        path,
        action="gateway.board.read",
    )

    response = app_with_writer.get(path, headers={"Authorization": authorization}, auth=None)

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/board/tasks/count", "/board/tasks/count-by-status"])
def test_court_singular_task_action_cannot_authorize_static_aggregate_routes(
    path: str,
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
) -> None:
    authorization = _signed_authorization(
        gateway_test_signers["court"],
        "GET",
        path,
        action="gateway.board.task.read",
    )

    response = app_with_writer.get(path, headers={"Authorization": authorization}, auth=None)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


@pytest.mark.unit
def test_new_route_without_explicit_policy_fails_closed(
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
) -> None:
    app_with_writer.app.add_api_route(
        "/new-domain/probe",
        lambda: Response(status_code=204),
        methods=["GET"],
    )
    authorization = _signed_authorization(
        gateway_test_signers["identity"],
        "GET",
        "/new-domain/probe",
        action="gateway.identity.read",
    )

    response = app_with_writer.get(
        "/new-domain/probe",
        headers={"Authorization": authorization},
        auth=None,
    )

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.unit
def test_every_non_health_route_has_an_authority_policy(app_with_writer: TestClient) -> None:
    routes = [route for route in app_with_writer.app.routes if isinstance(route, APIRoute)]
    health_routes = [route for route in routes if route.path == "/health"]
    protected_routes = {
        (method, route.path)
        for route in routes
        if route.path != "/health" and route.methods is not None
        for method in route.methods
    }

    assert len(health_routes) == 1
    assert health_routes[0].methods == {"GET"}
    assert protected_routes == set(GATEWAY_ROUTE_POLICIES)


@pytest.mark.unit
def test_duplicate_trusted_key_material_fails_app_creation(
    gateway_test_signers: dict[str, GatewayRequestSigner],
    db_writer: DbWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del gateway_test_signers
    (tmp_path / "gateway-central-bank.pub").write_bytes(
        (tmp_path / "gateway-identity.pub").read_bytes()
    )
    config_path = tmp_path / "duplicate-config.yaml"
    _create_test_config(db_writer._db_path, config_path)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))

    from db_gateway_service.app import create_app
    from db_gateway_service.config import clear_settings_cache

    clear_settings_cache()
    with pytest.raises(ValueError, match="share key material"):
        create_app()
