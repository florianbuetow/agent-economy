"""Lane U (2.4, G03): public V2 poster-input publication routes on the Gateway."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_auth.financial_identity import operation_id_for

from db_gateway_service.artifact_upload_requests import (
    parse_artifact_policy,
    parse_scoped_artifact_upload_request,
)
from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.artifact_writer import ArtifactWriter
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event
from tests.fakes.native_board_history import (
    FIXED_NOW_TEXT,
    BusinessClock,
    create_account,
    register_agent,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from service_auth.gateway import GatewayRequestSigner

pytestmark = pytest.mark.unit

_BOARD_KID = "gateway-task-board"
_INPUT_BODY = b"3 7 11\n"


@pytest.fixture(autouse=True)
def _fixed_gateway_business_clock(monkeypatch: pytest.MonkeyPatch) -> BusinessClock:
    clock = BusinessClock()
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", clock)
    return clock


_POLICY_FIELDS: dict[str, int] = {
    "contract_version": 1,
    "max_file_bytes": 1024,
    "max_files_per_manifest": 4,
    "max_manifest_bytes": 4096,
    "reservation_ttl_seconds": 300,
    "reservation_renew_before_expiry_seconds": 60,
    "max_upload_duration_seconds": 900,
}
_POLICY = parse_artifact_policy(_POLICY_FIELDS)


@pytest.fixture(autouse=True)
def _reader(app_with_writer: TestClient) -> None:  # noqa: ARG001 - ordering dependency
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(state.db_writer.connection)


def _admission(poster_id: str, task_id: str, publication_id: str, count: int) -> dict[str, Any]:
    return {
        "request": _admission_request(poster_id, task_id, publication_id, count),
        "policy": _POLICY_FIELDS,
    }


def _admission_request(
    poster_id: str, task_id: str, publication_id: str, count: int
) -> dict[str, Any]:
    return {
        "protocol_version": 2,
        "publication_operation_id": publication_id,
        "task_id": task_id,
        "actor_kind": "agent",
        "actor_id": poster_id,
        "action": "task_publication_admit",
        "resource": "POST /task-publications",
        "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
        "title": "Sum the inputs",
        "spec": "Sum the integers in input.txt.",
        "reward": 100,
        "bidding_deadline_seconds": 43200,
        "acceptance_window_seconds": 3600,
        "execution_deadline_seconds": 172800,
        "review_deadline_seconds": 43200,
        "expected_input_count": count,
    }


def _freeze(poster_id: str, task_id: str, publication_id: str, manifest_id: str) -> dict[str, Any]:
    return {
        "protocol_version": 2,
        "freeze_operation_id": f"op-{uuid4()}",
        "publication_operation_id": publication_id,
        "task_id": task_id,
        "actor_id": poster_id,
        "action": "task_publication_inputs_freeze",
        "resource": f"POST /task-publications/{publication_id}/inputs/freeze",
        "input_manifest_id": manifest_id,
    }


def _publish(poster_id: str, task_id: str, publication_id: str, contract: str) -> dict[str, Any]:
    return {
        "protocol_version": 2,
        "publish_operation_id": f"op-{uuid4()}",
        "publication_operation_id": publication_id,
        "task_id": task_id,
        "actor_id": poster_id,
        "action": "task_publication_publish",
        "resource": "POST /tasks",
        "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
        "publication_contract_sha256": contract,
    }


def _lock(client: TestClient, poster_id: str, task_id: str) -> None:
    response = client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": operation_id_for("task-lock", (task_id, poster_id)),
            "escrow_id": f"esc-{uuid4()}",
            "payer_account_id": poster_id,
            "amount": 100,
            "task_id": task_id,
            "created_at": FIXED_NOW_TEXT,
            "tx_id": f"tx-{uuid4()}",
            "event": {
                **make_event(
                    source="central-bank",
                    event_type="escrow.locked",
                    task_id=task_id,
                    agent_id=poster_id,
                ),
                "timestamp": FIXED_NOW_TEXT,
            },
        },
    )
    assert response.status_code == 201, response.text


def _upload_input(manifest_id: str, poster_id: str, task_id: str, publication_id: str) -> None:
    writer = get_app_state().db_writer
    assert writer is not None
    artifacts = ArtifactWriter(
        writer.connection,
        clock_us=BusinessClock(),
        policy=_POLICY,
        current_task_board_caller_kid=_BOARD_KID,
    )
    request = parse_scoped_artifact_upload_request(
        {
            "protocol_version": 2,
            "upload_operation_id": str(uuid4()),
            "resource": f"POST /task-publications/{publication_id}/inputs",
            "manifest_kind": "input",
            "publication_operation_id": publication_id,
            "task_id": task_id,
            "manifest_id": manifest_id,
            "actor_id": poster_id,
            "actor_role": "poster",
            "display_filename": "input.txt",
            "evidence_format": "text",
            "declared_size_bytes": len(_INPUT_BODY),
            "content_sha256": hashlib.sha256(_INPUT_BODY).hexdigest(),
        }
    )
    reserved = artifacts.reserve_upload(request, verified_caller_kid=_BOARD_KID)
    attempt = reserved["attempt"]
    artifacts.finalize_upload(
        reserved["upload_operation_id"],
        owner_id=attempt["owner_id"],
        generation=attempt["generation"],
        attempt_nonce=attempt["attempt_nonce"],
        observed_size_bytes=len(_INPUT_BODY),
        observed_sha256=hashlib.sha256(_INPUT_BODY).hexdigest(),
        verified_caller_kid=_BOARD_KID,
    )


def _private_get(
    client: TestClient,
    signers: dict[str, GatewayRequestSigner],
    path: str,
    params: dict[str, Any],
) -> Any:
    request = client.build_request("GET", path, params=params)
    signed = signers["board"].sign_request(request, action="gateway.board.write")
    return client.get(
        path,
        params=params,
        headers={"Authorization": signed.headers["Authorization"]},
        auth=None,
    )


def _poster(client: TestClient) -> str:
    poster_id = register_agent(client, name="Input Poster")
    create_account(client, poster_id, balance=500)
    return poster_id


def _publish_flow(client: TestClient, *, with_input: bool) -> tuple[str, str, dict[str, Any]]:
    poster_id = _poster(client)
    task_id = f"t-{uuid4()}"
    publication_id = f"op-{uuid4()}"
    admitted = client.post(
        "/board/prepublications",
        json=_admission(poster_id, task_id, publication_id, 1 if with_input else 0),
    )
    assert admitted.status_code == 202, admitted.text
    body = admitted.json()
    assert body["publication_state"] == "awaiting_inputs"
    manifest_id = body["input_manifest_id"]
    if with_input:
        _upload_input(manifest_id, poster_id, task_id, publication_id)
    frozen = client.post(
        f"/board/prepublications/{publication_id}/inputs/freeze",
        json=_freeze(poster_id, task_id, publication_id, manifest_id),
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["publication_state"] == "awaiting_publish"
    contract = frozen.json()["publication_contract_sha256"]
    authorized = client.post(
        f"/board/prepublications/{publication_id}/publish",
        json=_publish(poster_id, task_id, publication_id, contract),
    )
    assert authorized.status_code == 202, authorized.text
    assert authorized.json()["publication_state"] == "awaiting_lock"
    _lock(client, poster_id, task_id)
    published = client.post(f"/board/prepublications/{publication_id}/reconcile", json={})
    assert published.status_code == 201, published.text
    assert published.json()["publication_state"] == "published"
    return poster_id, task_id, frozen.json()


def test_publish_without_inputs_exposes_empty_frozen_manifest(app_with_writer: TestClient) -> None:
    _poster_id, task_id, frozen = _publish_flow(app_with_writer, with_input=False)
    task = app_with_writer.get(f"/board/tasks/{task_id}")
    assert task.status_code == 200, task.text
    assert task.json()["status"] == "open"
    manifest = app_with_writer.get(f"/board/tasks/{task_id}/input-manifest")
    assert manifest.status_code == 200, manifest.text
    body = manifest.json()
    assert body["task_id"] == task_id
    assert body["input_manifest"]["manifest_sha256"] == frozen["input_manifest_sha256"]
    assert body["input_manifest"]["state"] == "frozen"
    assert body["input_manifest"]["files"] == []


def test_publish_with_input_lists_exact_name_size_and_hash(app_with_writer: TestClient) -> None:
    poster_id, task_id, frozen = _publish_flow(app_with_writer, with_input=True)
    manifest = app_with_writer.get(f"/board/tasks/{task_id}/input-manifest")
    assert manifest.status_code == 200, manifest.text
    body = manifest.json()["input_manifest"]
    assert body["manifest_sha256"] == frozen["input_manifest_sha256"]
    assert body["actor_id"] == poster_id
    assert len(body["files"]) == 1
    entry = body["files"][0]
    assert entry["filename"] == "input.txt"
    assert entry["size_bytes"] == len(_INPUT_BODY)
    assert entry["content_sha256"] == hashlib.sha256(_INPUT_BODY).hexdigest()
    assert entry["role"] == "input"
    assert isinstance(entry["object_key"], str) and entry["object_key"]
    assert entry["artifact_id"]


def test_input_manifest_is_null_for_tasks_without_v2_publication(
    app_with_writer: TestClient,
) -> None:
    from tests.fakes.native_board_history import create_task

    graph = create_task(app_with_writer)
    response = app_with_writer.get(f"/board/tasks/{graph.task_id}/input-manifest")
    assert response.status_code == 200, response.text
    assert response.json() == {"task_id": graph.task_id, "input_manifest": None}


def test_input_manifest_unknown_task_is_404(app_with_writer: TestClient) -> None:
    response = app_with_writer.get(f"/board/tasks/t-{uuid4()}/input-manifest")
    assert response.status_code == 404
    assert response.json()["error"] == "task_not_found"


def test_freeze_by_non_poster_is_forbidden(app_with_writer: TestClient) -> None:
    poster_id = _poster(app_with_writer)
    intruder = register_agent(app_with_writer, name="Intruder")
    task_id = f"t-{uuid4()}"
    publication_id = f"op-{uuid4()}"
    admitted = app_with_writer.post(
        "/board/prepublications", json=_admission(poster_id, task_id, publication_id, 0)
    )
    assert admitted.status_code == 202, admitted.text
    manifest_id = admitted.json()["input_manifest_id"]
    response = app_with_writer.post(
        f"/board/prepublications/{publication_id}/inputs/freeze",
        json=_freeze(intruder, task_id, publication_id, manifest_id),
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_path_and_body_publication_must_match(app_with_writer: TestClient) -> None:
    poster_id = _poster(app_with_writer)
    task_id = f"t-{uuid4()}"
    publication_id = f"op-{uuid4()}"
    response = app_with_writer.post(
        f"/board/prepublications/op-{uuid4()}/inputs/freeze",
        json=_freeze(poster_id, task_id, publication_id, "manifest-input-x"),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


def test_get_prepublication_is_owner_scoped(
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
) -> None:
    poster_id = _poster(app_with_writer)
    task_id = f"t-{uuid4()}"
    publication_id = f"op-{uuid4()}"
    admitted = app_with_writer.post(
        "/board/prepublications", json=_admission(poster_id, task_id, publication_id, 0)
    )
    assert admitted.status_code == 202, admitted.text
    own = _private_get(
        app_with_writer,
        gateway_test_signers,
        f"/board/prepublications/{publication_id}",
        {"poster_id": poster_id},
    )
    assert own.status_code == 200, own.text
    assert own.json()["publication_state"] == "awaiting_inputs"
    assert own.json()["reward"] == 100
    other = _private_get(
        app_with_writer,
        gateway_test_signers,
        f"/board/prepublications/{publication_id}",
        {"poster_id": "someone-else"},
    )
    assert other.status_code == 404


def test_v1_posting_recovery_ignores_v2_publications(
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
) -> None:
    poster_id = _poster(app_with_writer)
    task_id = f"t-{uuid4()}"
    publication_id = f"op-{uuid4()}"
    admitted = app_with_writer.post(
        "/board/prepublications", json=_admission(poster_id, task_id, publication_id, 0)
    )
    manifest_id = admitted.json()["input_manifest_id"]
    frozen = app_with_writer.post(
        f"/board/prepublications/{publication_id}/inputs/freeze",
        json=_freeze(poster_id, task_id, publication_id, manifest_id),
    )
    authorized = app_with_writer.post(
        f"/board/prepublications/{publication_id}/publish",
        json=_publish(
            poster_id, task_id, publication_id, frozen.json()["publication_contract_sha256"]
        ),
    )
    assert authorized.json()["publication_state"] == "awaiting_lock"
    recovery = _private_get(
        app_with_writer,
        gateway_test_signers,
        "/board/posting-recovery",
        {"after": "", "limit": 100},
    )
    assert recovery.status_code == 200, recovery.text
    identities = [item["identity"] for item in recovery.json()["items"]]
    assert publication_id not in identities
    v2 = _private_get(
        app_with_writer,
        gateway_test_signers,
        "/board/prepublications-recovery",
        {"after": "", "limit": 100},
    )
    assert v2.status_code == 200, v2.text
    assert publication_id in [item["identity"] for item in v2.json()["items"]]


def test_admission_requires_explicit_policy(app_with_writer: TestClient) -> None:
    poster_id = _poster(app_with_writer)
    body = _admission(poster_id, f"t-{uuid4()}", f"op-{uuid4()}", 0)
    response = app_with_writer.post("/board/prepublications", json={"request": body["request"]})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


def test_whole_second_bank_lock_time_publishes(
    app_with_writer: TestClient,
    _fixed_gateway_business_clock: BusinessClock,
) -> None:
    """Central Bank stamps whole seconds; a lock in the authorizing second must publish."""
    _fixed_gateway_business_clock.now_us += 500_000
    poster_id = _poster(app_with_writer)
    task_id = f"t-{uuid4()}"
    publication_id = f"op-{uuid4()}"
    admitted = app_with_writer.post(
        "/board/prepublications", json=_admission(poster_id, task_id, publication_id, 0)
    )
    manifest_id = admitted.json()["input_manifest_id"]
    frozen = app_with_writer.post(
        f"/board/prepublications/{publication_id}/inputs/freeze",
        json=_freeze(poster_id, task_id, publication_id, manifest_id),
    )
    authorized = app_with_writer.post(
        f"/board/prepublications/{publication_id}/publish",
        json=_publish(
            poster_id, task_id, publication_id, frozen.json()["publication_contract_sha256"]
        ),
    )
    assert authorized.status_code == 202, authorized.text
    _lock(app_with_writer, poster_id, task_id)
    published = app_with_writer.post(f"/board/prepublications/{publication_id}/reconcile", json={})
    assert published.status_code == 201, published.text
