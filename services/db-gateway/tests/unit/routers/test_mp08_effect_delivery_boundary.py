"""T-124 protected HTTP boundary for financial-effect recovery."""

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
from service_commons.config import ConfigurationError

from db_gateway_service import task_time
from db_gateway_service.config import clear_settings_cache
from db_gateway_service.core.state import init_app_state
from tests.fakes.gateway_mp08_config import write_gateway_config
from tests.fakes.gateway_mp08_effects import prepare_native_pending
from tests.fakes.gateway_mp08_migration_helpers import database_snapshot
from tests.fakes.native_board_history import FIXED_NOW_US, BusinessClock

if TYPE_CHECKING:
    from collections.abc import Iterator

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit

_DIAGNOSTIC_KEYS = {
    "effect_operation_id",
    "task_id",
    "source_kind",
    "source_id",
    "effect_kind",
    "delivery_state",
    "enqueued_at_us",
    "observed_at_us",
    "total_attempt_count",
    "cycle_attempt_count",
    "lease_generation",
    "next_attempt_at_us",
    "lease_owner_id",
    "lease_acquired_at_us",
    "lease_expires_at_us",
    "last_failure_code",
    "last_failure_at_us",
    "operator_retry_count",
    "last_operator_retry_at_us",
    "next_evidence_check_at_us",
    "completed_at_us",
    "completion_event_id",
    "receipt_reference",
}
_WORKER_CODES = (
    "central_bank_unavailable",
    "gateway_unavailable",
    "invalid_gateway_response",
    "request_timeout",
    "unexpected_recovery_error",
)
_LEASE_KEYS = {
    "effect_operation_id",
    "task_id",
    "source_kind",
    "source_id",
    "effect_kind",
    "effect_intent",
    "worker_id",
    "lease_generation",
    "total_attempt_count",
    "cycle_attempt_count",
    "lease_acquired_at_us",
    "lease_expires_at_us",
}


class _DomainAuth(httpx.Auth):
    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        domain = request.url.path.split("/", maxsplit=2)[1]
        signer = self._signers[domain]
        protected_delivery = request.url.path.startswith(
            "/board/task-effect-deliveries"
        ) or request.url.path.startswith("/board/legacy-effect-observations/")
        access = "write" if protected_delivery or request.method != "GET" else "read"
        yield signer.sign_request(request, action=f"gateway.{domain}.{access}")


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
def delivery_boundary(
    db_writer: DbWriter,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    tmp_path: Path,
) -> Iterator[tuple[TestClient, dict[str, GatewayRequestSigner]]]:
    foreign = _signer("gateway-task-board-foreign", tmp_path)
    read_only = _signer("gateway-task-board-read-only", tmp_path)
    config_path = tmp_path / "mp08-config.yaml"
    write_gateway_config(db_writer._db_path, config_path)
    with config_path.open("a") as config:
        config.write(
            """
    gateway-task-board-foreign:
      public_key_path: "gateway-task-board-foreign.pub"
      capabilities: ["gateway.board.read", "gateway.board.write"]
    gateway-task-board-read-only:
      public_key_path: "gateway-task-board-read-only.pub"
      capabilities: ["gateway.board.read"]
"""
        )
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    from db_gateway_service.app import create_app

    app = create_app()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=FIXED_NOW_US))
    with TestClient(app) as client:
        client.auth = _DomainAuth(gateway_test_signers)
        init_app_state().db_writer = db_writer
        yield (
            client,
            {
                "owner": gateway_test_signers["board"],
                "foreign": foreign,
                "read_only": read_only,
            },
        )
    monkeypatch.undo()
    os.environ.pop("CONFIG_PATH", None)
    clear_settings_cache()


def _signed(
    client: TestClient,
    signer: GatewayRequestSigner,
    method: str,
    path: str,
    *,
    action: str,
    body: dict[str, object] | None = None,
) -> httpx.Response:
    content = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = signer.sign_request(
        httpx.Request(
            method,
            f"http://testserver{path}",
            content=content,
            headers={"Content-Type": "application/json"},
        ),
        action=action,
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


def test_owner_get_returns_exact_safe_diagnostic_shape(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
) -> None:
    client, signers = delivery_boundary
    effect = prepare_native_pending(client)
    response = _signed(
        client,
        signers["owner"],
        "GET",
        f"/board/task-effect-deliveries/{effect.operation_id}",
        action="gateway.board.write",
    )
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"protocol_version", "delivery"}
    assert response.json()["protocol_version"] == 1
    assert set(response.json()["delivery"]) == _DIAGNOSTIC_KEYS
    serialized = json.dumps(response.json())
    assert "effect_intent" not in serialized
    assert "financial_request" not in serialized
    assert "private_key" not in serialized


def test_capability_precedes_owner_hidden_lookup(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
) -> None:
    client, signers = delivery_boundary
    effect = prepare_native_pending(client)
    denied = _signed(
        client,
        signers["read_only"],
        "GET",
        f"/board/task-effect-deliveries/{effect.operation_id}",
        action="gateway.board.write",
    )
    assert denied.status_code == 403
    hidden = _signed(
        client,
        signers["foreign"],
        "GET",
        f"/board/task-effect-deliveries/{effect.operation_id}",
        action="gateway.board.write",
    )
    assert hidden.status_code == 404


@pytest.mark.parametrize(
    ("path_suffix", "body"),
    [
        (
            "attempts/1/fail",
            {
                "protocol_version": 1,
                "worker_id": "recovery-00000000-0000-4000-8000-000000000123",
                "error_code": "request_timeout",
            },
        ),
        ("retry", {}),
    ],
)
def test_capable_foreign_caller_gets_hidden_404_for_fail_and_retry(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    path_suffix: str,
    body: dict[str, object],
) -> None:
    client, signers = delivery_boundary
    effect = prepare_native_pending(client)
    response = _signed(
        client,
        signers["foreign"],
        "POST",
        f"/board/task-effect-deliveries/{effect.operation_id}/{path_suffix}",
        action="gateway.board.write",
        body=body,
    )
    assert response.status_code == 404


def test_foreign_owned_operation_id_is_a_nonrevealing_lexical_cursor(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    db_writer: DbWriter,
) -> None:
    client, signers = delivery_boundary
    effects = sorted([prepare_native_pending(client).operation_id for _index in range(3)])
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET owner_caller_kid=? WHERE effect_operation_id=?",
        ("gateway-task-board-foreign", effects[1]),
    )
    db_writer.connection.commit()
    response = _signed(
        client,
        signers["owner"],
        "GET",
        f"/board/task-effect-deliveries?state=all&after={effects[1]}&limit=10",
        action="gateway.board.write",
    )
    assert response.status_code == 200, response.text
    assert [item["effect_operation_id"] for item in response.json()["items"]] == [effects[2]]
    assert response.json()["next_after"] is None


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/board/task-effect-deliveries/lease",
            {
                "protocol_version": 1,
                "worker_id": "recovery-00000000-0000-4000-8000-000000000123",
            },
        ),
        (
            "POST",
            "/board/task-effect-deliveries/op-00000000-0000-4000-8000-000000000001/attempts/1/fail",
            {
                "protocol_version": 1,
                "worker_id": "recovery-00000000-0000-4000-8000-000000000123",
                "error_code": "request_timeout",
            },
        ),
        (
            "GET",
            "/board/task-effect-deliveries/op-00000000-0000-4000-8000-000000000001",
            None,
        ),
        (
            "GET",
            "/board/task-effect-deliveries?state=all&after=&limit=1",
            None,
        ),
        (
            "POST",
            "/board/task-effect-deliveries/op-00000000-0000-4000-8000-000000000001/retry",
            {},
        ),
        (
            "POST",
            "/board/legacy-effect-observations/obs-00000000-0000-4000-8000-000000000001/finalize",
            {},
        ),
    ],
)
def test_read_only_caller_is_forbidden_from_every_protected_route(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    client, signers = delivery_boundary
    response = _signed(
        client,
        signers["read_only"],
        method,
        path,
        action="gateway.board.write",
        body=body,
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.parametrize(
    "query",
    [
        "",
        "after=&limit=1",
        "state=pending&after=",
        "state=pending&limit=1",
        "state=pending&state=all&after=&limit=1",
        "state=pending&after=&after=op-00000000-0000-4000-8000-000000000001&limit=1",
        "state=pending&after=&limit=1&limit=2",
        "state=pending&after=&limit=1&extra=value",
        "state=bogus&after=&limit=1",
        "state=pending&after=&limit=0",
        "state=pending&after=&limit=1001",
        "state=pending&after=not-an-operation&limit=1",
    ],
)
def test_list_rejects_missing_duplicate_or_malformed_exact_query(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    db_writer: DbWriter,
    query: str,
) -> None:
    client, signers = delivery_boundary
    before = database_snapshot(Path(db_writer._db_path))
    response = _signed(
        client,
        signers["owner"],
        "GET",
        f"/board/task-effect-deliveries?{query}",
        action="gateway.board.write",
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"
    assert database_snapshot(Path(db_writer._db_path)) == before


@pytest.mark.parametrize("error_code", _WORKER_CODES)
def test_failure_route_accepts_only_finite_worker_codes(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    error_code: str,
) -> None:
    client, signers = delivery_boundary
    effect = prepare_native_pending(client)
    leased = _signed(
        client,
        signers["owner"],
        "POST",
        "/board/task-effect-deliveries/lease",
        action="gateway.board.write",
        body={
            "protocol_version": 1,
            "worker_id": "recovery-00000000-0000-4000-8000-000000000123",
        },
    )
    assert leased.status_code == 200, leased.text
    generation = leased.json()["delivery"]["lease_generation"]
    failed = _signed(
        client,
        signers["owner"],
        "POST",
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/{generation}/fail",
        action="gateway.board.write",
        body={
            "protocol_version": 1,
            "worker_id": "recovery-00000000-0000-4000-8000-000000000123",
            "error_code": error_code,
        },
    )
    assert failed.status_code == 200, failed.text


def test_gateway_only_failure_code_is_rejected_from_worker(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    db_writer: DbWriter,
) -> None:
    client, signers = delivery_boundary
    before = database_snapshot(Path(db_writer._db_path))
    response = _signed(
        client,
        signers["owner"],
        "POST",
        "/board/task-effect-deliveries/op-00000000-0000-4000-8000-000000000001/attempts/1/fail",
        action="gateway.board.write",
        body={
            "protocol_version": 1,
            "worker_id": "recovery-00000000-0000-4000-8000-000000000123",
            "error_code": "lease_expired",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"
    assert database_snapshot(Path(db_writer._db_path)) == before


def test_lease_returns_strict_stored_intent_envelope(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
) -> None:
    client, signers = delivery_boundary
    effect = prepare_native_pending(client)
    response = _signed(
        client,
        signers["owner"],
        "POST",
        "/board/task-effect-deliveries/lease",
        action="gateway.board.write",
        body={
            "protocol_version": 1,
            "worker_id": "recovery-00000000-0000-4000-8000-000000000777",
        },
    )
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"protocol_version", "delivery"}
    item = response.json()["delivery"]
    assert set(item) == _LEASE_KEYS
    assert item["effect_operation_id"] == effect.operation_id
    assert set(item["effect_intent"]) == {
        "financial_request",
        "request_sha256",
        "expected_escrow",
    }


def test_list_uses_exclusive_lexical_cursor_and_limit_plus_one_pagination(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
) -> None:
    client, signers = delivery_boundary
    effects = sorted(
        [prepare_native_pending(client).operation_id, prepare_native_pending(client).operation_id]
    )
    first = _signed(
        client,
        signers["owner"],
        "GET",
        "/board/task-effect-deliveries?state=all&after=&limit=1",
        action="gateway.board.write",
    )
    assert first.status_code == 200, first.text
    assert set(first.json()) == {"protocol_version", "items", "next_after"}
    assert [item["effect_operation_id"] for item in first.json()["items"]] == effects[:1]
    assert first.json()["next_after"] == effects[0]
    second = _signed(
        client,
        signers["owner"],
        "GET",
        f"/board/task-effect-deliveries?state=all&after={effects[0]}&limit=1",
        action="gateway.board.write",
    )
    assert second.status_code == 200, second.text
    assert [item["effect_operation_id"] for item in second.json()["items"]] == effects[1:]
    assert second.json()["next_after"] is None


def test_nonexistent_canonical_cursor_is_not_looked_up(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
) -> None:
    client, signers = delivery_boundary
    response = _signed(
        client,
        signers["owner"],
        "GET",
        "/board/task-effect-deliveries?state=all&"
        "after=op-00000000-0000-4000-8000-000000000555&limit=10",
        action="gateway.board.write",
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["next_after"] is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"protocol_version": 1},
        {"protocol_version": 2, "worker_id": "recovery-00000000-0000-4000-8000-000000000777"},
        {"protocol_version": 1, "worker_id": "recovery-not-a-uuid"},
        {
            "protocol_version": 1,
            "worker_id": "recovery-00000000-0000-4000-8000-000000000777",
            "money": 1,
        },
    ],
)
def test_lease_rejects_missing_malformed_or_extra_fields(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    db_writer: DbWriter,
    body: dict[str, object],
) -> None:
    client, signers = delivery_boundary
    before = database_snapshot(Path(db_writer._db_path))
    response = _signed(
        client,
        signers["owner"],
        "POST",
        "/board/task-effect-deliveries/lease",
        action="gateway.board.write",
        body=body,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"
    assert database_snapshot(Path(db_writer._db_path)) == before


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/board/task-effect-deliveries/not-an-operation", None),
        (
            "POST",
            "/board/task-effect-deliveries/not-an-operation/retry",
            {"unexpected": True},
        ),
        (
            "POST",
            "/board/legacy-effect-observations/not-an-observation/finalize",
            {"unexpected": True},
        ),
    ],
)
def test_other_invalid_request_families_leave_database_byte_identical(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
    db_writer: DbWriter,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    client, signers = delivery_boundary
    before = database_snapshot(Path(db_writer._db_path))
    response = _signed(
        client,
        signers["owner"],
        method,
        path,
        action="gateway.board.write",
        body=body,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"
    assert database_snapshot(Path(db_writer._db_path)) == before


def test_missing_authentication_precedes_delivery_lookup(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
) -> None:
    client, _signers = delivery_boundary
    response = client.get(
        "/board/task-effect-deliveries/op-00000000-0000-4000-8000-000000000001",
        auth=None,
    )
    assert response.status_code == 401
    assert response.json()["error"] == "missing_authorization"


def test_same_owner_unleased_failure_is_stale_conflict(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
) -> None:
    client, signers = delivery_boundary
    effect = prepare_native_pending(client)
    response = _signed(
        client,
        signers["owner"],
        "POST",
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/1/fail",
        action="gateway.board.write",
        body={
            "protocol_version": 1,
            "worker_id": "recovery-00000000-0000-4000-8000-000000000777",
            "error_code": "request_timeout",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "effect_delivery_attempt_stale"


def test_pending_delivery_is_not_operator_retryable(
    delivery_boundary: tuple[TestClient, dict[str, GatewayRequestSigner]],
) -> None:
    client, signers = delivery_boundary
    effect = prepare_native_pending(client)
    response = _signed(
        client,
        signers["owner"],
        "POST",
        f"/board/task-effect-deliveries/{effect.operation_id}/retry",
        action="gateway.board.write",
        body={},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "effect_delivery_not_retryable"


@pytest.mark.parametrize(
    ("current_kid", "capabilities", "register_caller"),
    [
        ("gateway-task-board-untrusted", (), False),
        ("gateway-task-board-read-only-current", ("gateway.board.read",), True),
    ],
)
def test_fresh_gateway_rejects_untrusted_or_read_only_current_board_kid_before_ready(
    tmp_path: Path,
    current_kid: str,
    capabilities: tuple[str, ...],
    register_caller: bool,
) -> None:
    config_path = tmp_path / f"{current_kid}.yaml"
    write_gateway_config(str(tmp_path / "fresh-v5.db"), config_path)
    content = config_path.read_text().replace(
        'current_task_board_caller_kid: "gateway-task-board"',
        f'current_task_board_caller_kid: "{current_kid}"',
    )
    if register_caller:
        _signer(current_kid, tmp_path)
        rendered_capabilities = ", ".join(f'"{value}"' for value in capabilities)
        content += f"""
    {current_kid}:
      public_key_path: "{current_kid}.pub"
      capabilities: [{rendered_capabilities}]
"""
    config_path.write_text(content)
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    from db_gateway_service.app import create_app

    state = init_app_state()
    try:
        with pytest.raises((ConfigurationError, ValueError)), TestClient(create_app()):
            pass
        assert state.db_writer is None
        assert state.db_reader is None
    finally:
        os.environ.pop("CONFIG_PATH", None)
        clear_settings_cache()
