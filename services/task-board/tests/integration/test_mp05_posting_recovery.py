"""T-118 native Board coordination proofs over the real v4 DB Gateway."""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from service_auth.financial_identity import operation_id_for
from service_auth.jws import create_jws
from service_auth.task_postings import (
    TaskPostingIntent,
    parse_task_posting_status_response,
    parse_task_posting_submit_response,
)
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_INITIAL_BALANCE = 500
_REWARD = 100
_TITLE = "Recover this native posting"
_SPEC = "Publish these exact fixed terms once after every ambiguous boundary."
_BIDDING_SECONDS = 3_600
_ACCEPTANCE_SECONDS = 1_800
_EXECUTION_SECONDS = 7_200
_REVIEW_SECONDS = 900
_RESULT_KEYS = {
    "task_id",
    "poster_id",
    "title",
    "spec",
    "reward",
    "bidding_deadline_seconds",
    "acceptance_window_seconds",
    "execution_deadline_seconds",
    "review_deadline_seconds",
    "status",
    "escrow_id",
    "created_at",
    "bidding_deadline",
    "acceptance_deadline",
    "bidding_cutoff_us",
    "acceptance_cutoff_us",
    "lifecycle_version",
    "timing_contract_version",
    "event_id",
}
_NOT_FOUND = {
    "error": "publication_not_found",
    "message": "Publication not found",
    "details": {},
}


@dataclass(slots=True)
class _Posting:
    intent: TaskPostingIntent
    private_key: Ed25519PrivateKey
    task_token: str
    escrow_token: str
    escrow_id: str
    lock_created_at: str | None = None
    lock_tx_id: str | None = None

    @property
    def request(self) -> dict[str, str]:
        return {"task_token": self.task_token, "escrow_token": self.escrow_token}


def _rows(
    db_path: str,
    query: str,
    params: tuple[object, ...] = (),
) -> list[dict[str, object]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _formatted_public_key(private_key: Ed25519PrivateKey) -> str:
    public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return f"ed25519:{base64.b64encode(public_bytes).decode()}"


async def _register_signer(
    gateway_client: httpx.AsyncClient,
    agent_id: str,
    private_key: Ed25519PrivateKey,
) -> None:
    timestamp = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    response = await gateway_client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": _formatted_public_key(private_key),
            "registered_at": timestamp,
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": timestamp,
                "agent_id": agent_id,
                "summary": f"{agent_id} registered",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text


async def _create_funded_account(gateway_client: httpx.AsyncClient, poster_id: str) -> None:
    timestamp = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    response = await gateway_client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid.uuid4()}",
            "account_id": poster_id,
            "created_at": timestamp,
            "balance": _INITIAL_BALANCE,
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": timestamp,
                "agent_id": poster_id,
                "summary": f"Created account for {poster_id}",
                "payload": "{}",
            },
            "initial_credit": {
                "tx_id": f"tx-{uuid.uuid4()}",
                "amount": _INITIAL_BALANCE,
                "reference": "initial_balance",
                "timestamp": timestamp,
            },
        },
    )
    assert response.status_code == 201, response.text


def _tokens(
    private_key: Ed25519PrivateKey,
    intent: TaskPostingIntent,
) -> tuple[str, str]:
    task_token = make_jws_token(
        private_key,
        intent.actor_id,
        {
            "action": "create_task",
            "protocol_version": intent.protocol_version,
            "operation_id": intent.operation_id,
            "task_id": intent.task_id,
            "poster_id": intent.actor_id,
            "lock_operation_id": intent.lock_operation_id,
            "title": intent.title,
            "spec": intent.spec,
            "reward": intent.reward,
            "bidding_deadline_seconds": intent.bidding_deadline_seconds,
            "acceptance_window_seconds": intent.acceptance_window_seconds,
            "execution_deadline_seconds": intent.execution_deadline_seconds,
            "review_deadline_seconds": intent.review_deadline_seconds,
        },
    )
    escrow_token = make_jws_token(
        private_key,
        intent.actor_id,
        {
            "action": "escrow_lock",
            "operation_id": intent.lock_operation_id,
            "task_id": intent.task_id,
            "agent_id": intent.actor_id,
            "amount": intent.reward,
        },
        audience="central-bank",
        resource="POST /escrow/lock",
    )
    return task_token, escrow_token


def _escrow_payload(intent: TaskPostingIntent) -> dict[str, object]:
    return {
        "action": "escrow_lock",
        "operation_id": intent.lock_operation_id,
        "task_id": intent.task_id,
        "agent_id": intent.actor_id,
        "amount": intent.reward,
    }


async def _new_posting(
    gateway_client: httpx.AsyncClient,
    private_key: Ed25519PrivateKey,
) -> _Posting:
    poster_id = f"a-mp05-native-{uuid.uuid4()}"
    task_id = f"t-{uuid.uuid4()}"
    intent = TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid.uuid4()}",
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": poster_id,
            "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
            "title": _TITLE,
            "spec": _SPEC,
            "reward": _REWARD,
            "bidding_deadline_seconds": _BIDDING_SECONDS,
            "acceptance_window_seconds": _ACCEPTANCE_SECONDS,
            "execution_deadline_seconds": _EXECUTION_SECONDS,
            "review_deadline_seconds": _REVIEW_SECONDS,
        }
    )
    await _register_signer(gateway_client, poster_id, private_key)
    await _create_funded_account(gateway_client, poster_id)
    task_token, escrow_token = _tokens(private_key, intent)
    return _Posting(intent, private_key, task_token, escrow_token, f"esc-{uuid.uuid4()}")


def _assert_signed_context(posting: _Posting, escrow_token: str) -> None:
    state = get_app_state()
    assert state.platform_agent is not None
    verified_task = state.platform_agent.validate_certificate(posting.task_token)
    verified_escrow = state.platform_agent.validate_certificate(escrow_token)
    assert verified_task.header.kid == posting.intent.actor_id
    assert verified_task.payload == {
        "action": "create_task",
        "protocol_version": 1,
        "operation_id": posting.intent.operation_id,
        "task_id": posting.intent.task_id,
        "poster_id": posting.intent.actor_id,
        "lock_operation_id": posting.intent.lock_operation_id,
        "title": posting.intent.title,
        "spec": posting.intent.spec,
        "reward": posting.intent.reward,
        "bidding_deadline_seconds": posting.intent.bidding_deadline_seconds,
        "acceptance_window_seconds": posting.intent.acceptance_window_seconds,
        "execution_deadline_seconds": posting.intent.execution_deadline_seconds,
        "review_deadline_seconds": posting.intent.review_deadline_seconds,
    }
    assert verified_escrow.header.kid == posting.intent.actor_id
    assert verified_escrow.payload == {
        "action": "escrow_lock",
        "operation_id": posting.intent.lock_operation_id,
        "task_id": posting.intent.task_id,
        "agent_id": posting.intent.actor_id,
        "amount": posting.intent.reward,
    }


async def _gateway_status(
    gateway_client: httpx.AsyncClient,
    posting: _Posting,
) -> httpx.Response:
    state = get_app_state()
    assert state.task_manager is not None
    store = state.task_manager.store
    request = gateway_client.build_request(
        "GET",
        f"/board/task-publications/{posting.intent.operation_id}",
        params={"poster_id": posting.intent.actor_id},
    )
    signed = store._signer.sign_request(request, action="gateway.board.write")
    return await gateway_client.send(signed, auth=None)


async def _admit_gateway(
    gateway_client: httpx.AsyncClient,
    posting: _Posting,
) -> httpx.Response:
    return await gateway_client.post(
        "/board/task-publications",
        json=posting.intent.as_json(),
    )


async def _reconcile_gateway(
    gateway_client: httpx.AsyncClient,
    posting: _Posting,
) -> httpx.Response:
    return await gateway_client.post(
        f"/board/task-publications/{posting.intent.operation_id}/reconcile",
        json={},
    )


def _publication_row(db_path: str, posting: _Posting) -> list[dict[str, object]]:
    return _rows(
        db_path,
        "SELECT operation_id,task_id,actor_id,lock_operation_id,canonical_request_json,"
        "request_sha256,state,escrow_id,result_json FROM board_task_publications "
        "WHERE operation_id=?",
        (posting.intent.operation_id,),
    )


async def _assert_saved_before_bank(
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
    posting: _Posting,
) -> dict[str, Any]:
    status = await _gateway_status(gateway_client, posting)
    if status.status_code != 200:
        raise ServiceError(
            "central_bank_unavailable",
            "Publication was not admitted before Bank transport",
            502,
            {"gateway_status": status.status_code},
        )
    envelope = cast("dict[str, Any]", status.json())
    assert envelope["publication_state"] == "awaiting_lock"
    assert envelope["operation_id"] == posting.intent.operation_id
    assert envelope["task_id"] == posting.intent.task_id
    assert envelope["lock_operation_id"] == posting.intent.lock_operation_id
    assert envelope["request_sha256"] == posting.intent.request_sha256
    rows = _publication_row(gateway_db_path, posting)
    assert rows == [
        {
            "operation_id": posting.intent.operation_id,
            "task_id": posting.intent.task_id,
            "actor_id": posting.intent.actor_id,
            "lock_operation_id": posting.intent.lock_operation_id,
            "canonical_request_json": _canonical(posting.intent.as_json()),
            "request_sha256": posting.intent.request_sha256,
            "state": "awaiting_lock",
            "escrow_id": None,
            "result_json": None,
        }
    ]
    return envelope


async def _persist_real_lock(
    gateway_client: httpx.AsyncClient,
    posting: _Posting,
) -> dict[str, Any]:
    if posting.lock_created_at is None:
        posting.lock_created_at = (
            datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        posting.lock_tx_id = f"tx-{uuid.uuid4()}"
    assert posting.lock_tx_id is not None
    response = await gateway_client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": posting.intent.lock_operation_id,
            "escrow_id": posting.escrow_id,
            "payer_account_id": posting.intent.actor_id,
            "amount": posting.intent.reward,
            "task_id": posting.intent.task_id,
            "created_at": posting.lock_created_at,
            "tx_id": posting.lock_tx_id,
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.locked",
                "timestamp": posting.lock_created_at,
                "task_id": posting.intent.task_id,
                "agent_id": posting.intent.actor_id,
                "summary": f"Locked escrow for {posting.intent.task_id}",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text
    envelope = cast("dict[str, Any]", response.json())
    assert envelope["outcome"] == "native_receipt"
    return envelope


def _install_real_lock_after_admission(
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
    posting: _Posting,
) -> tuple[AsyncMock, list[dict[str, Any]]]:
    state = get_app_state()
    assert state.central_bank_client is not None
    observations: list[dict[str, Any]] = []

    async def _lock(escrow_token: str) -> dict[str, Any]:
        assert escrow_token == posting.escrow_token
        _assert_signed_context(posting, escrow_token)
        observations.append(
            await _assert_saved_before_bank(gateway_client, gateway_db_path, posting)
        )
        return await _persist_real_lock(gateway_client, posting)

    lock = AsyncMock(side_effect=_lock)
    state.central_bank_client.escrow_lock = lock
    return lock, observations


def _assert_awaiting(response: httpx.Response, posting: _Posting) -> dict[str, Any]:
    parsed = parse_task_posting_submit_response(response, posting.intent)
    assert parsed.status_code == 202
    assert parsed.body["publication_state"] == "awaiting_lock"
    assert parsed.body["result"] is None
    return parsed.body


def _assert_published(response: httpx.Response, posting: _Posting) -> dict[str, Any]:
    parsed = parse_task_posting_submit_response(response, posting.intent)
    assert parsed.status_code == 201
    body = parsed.body
    assert body["publication_state"] == "published"
    assert body["escrow_id"] == posting.escrow_id
    result = body["result"]
    assert isinstance(result, dict) and set(result) == _RESULT_KEYS
    assert result["task_id"] == posting.intent.task_id
    assert result["poster_id"] == posting.intent.actor_id
    assert result["title"] == posting.intent.title
    assert result["spec"] == posting.intent.spec
    assert result["reward"] == posting.intent.reward
    assert result["bidding_deadline_seconds"] == posting.intent.bidding_deadline_seconds
    assert result["acceptance_window_seconds"] == posting.intent.acceptance_window_seconds
    assert result["execution_deadline_seconds"] == posting.intent.execution_deadline_seconds
    assert result["review_deadline_seconds"] == posting.intent.review_deadline_seconds
    assert result["status"] == "open"
    assert result["escrow_id"] == posting.escrow_id
    assert result["lifecycle_version"] == 0
    assert result["timing_contract_version"] == 2
    return body


def _status_headers(
    private_key: Ed25519PrivateKey,
    poster_id: str,
    operation_id: str,
) -> dict[str, str]:
    resource = f"GET /task-publications/{operation_id}"
    token = make_jws_token(
        private_key,
        poster_id,
        {
            "action": "get_task_publication",
            "operation_id": operation_id,
            "poster_id": poster_id,
        },
        resource=resource,
    )
    return {"Authorization": f"Bearer {token}"}


async def test_gateway_status_control_uses_board_write_capability(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    health = await client.get("/health")
    assert health.status_code == 200
    posting = await _new_posting(gateway_client, poster_keypair[0])
    admitted = await _admit_gateway(gateway_client, posting)
    assert admitted.status_code == 202, admitted.text

    status = await _gateway_status(gateway_client, posting)
    assert status.status_code == 200, status.text
    assert status.json() == admitted.json()


async def test_native_create_admits_saved_request_before_real_lock_and_publishes(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    lock, observations = _install_real_lock_after_admission(
        gateway_client, gateway_db_path, posting
    )

    response = await client.post("/tasks", json=posting.request)
    published = _assert_published(response, posting)

    assert lock.await_count == 1
    assert len(observations) == 1
    assert observations[0]["publication_state"] == "awaiting_lock"
    row = _publication_row(gateway_db_path, posting)
    assert row[0]["state"] == "published"
    assert row[0]["escrow_id"] == posting.escrow_id
    assert json.loads(cast("str", row[0]["result_json"])) == published["result"]


async def test_lost_reconcile_response_exact_replay_returns_frozen_envelope(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    lock, _observations = _install_real_lock_after_admission(
        gateway_client, gateway_db_path, posting
    )
    state = get_app_state()
    assert state.task_manager is not None
    store = state.task_manager.store
    original_request = store._request
    retained: httpx.Response | None = None
    fault_count = 0

    def _drop_reconcile_once(
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        nonlocal retained, fault_count
        response = original_request(method, path, json=json, params=params)
        expected = f"/board/task-publications/{posting.intent.operation_id}/reconcile"
        if (
            method == "POST"
            and path == expected
            and response.status_code == 201
            and fault_count == 0
        ):
            retained = response
            fault_count += 1
            raise httpx.ReadError(
                "injected loss after native publication reconcile committed",
                request=response.request,
            )
        return response

    monkeypatch.setattr(store, "_request", _drop_reconcile_once)
    with pytest.raises(httpx.ReadError, match="reconcile committed"):
        await client.post("/tasks", json=posting.request)

    assert fault_count == 1
    assert retained is not None
    original = _assert_published(retained, posting)
    replay = await client.post("/tasks", json=posting.request)
    replayed = _assert_published(replay, posting)
    assert replayed == original
    assert lock.await_count == 1
    assert (
        len(
            _rows(
                gateway_db_path,
                "SELECT 1 FROM board_tasks WHERE task_id=?",
                (posting.intent.task_id,),
            )
        )
        == 1
    )
    assert (
        len(
            _rows(
                gateway_db_path,
                "SELECT 1 FROM events WHERE task_id=? AND event_type='task.created'",
                (posting.intent.task_id,),
            )
        )
        == 1
    )


async def test_concurrent_same_intent_is_pending_before_one_lock_and_one_publication(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    state = get_app_state()
    assert state.central_bank_client is not None
    both_at_bank = asyncio.Event()
    admission_states: list[str] = []
    arrivals = 0

    async def _barrier_lock(escrow_token: str) -> dict[str, Any]:
        nonlocal arrivals
        assert escrow_token == posting.escrow_token
        _assert_signed_context(posting, escrow_token)
        status = await _assert_saved_before_bank(gateway_client, gateway_db_path, posting)
        admission_states.append(cast("str", status["publication_state"]))
        arrivals += 1
        if arrivals == 2:
            both_at_bank.set()
        await asyncio.wait_for(both_at_bank.wait(), timeout=2)
        return await _persist_real_lock(gateway_client, posting)

    lock = AsyncMock(side_effect=_barrier_lock)
    state.central_bank_client.escrow_lock = lock
    first, second = await asyncio.gather(
        client.post("/tasks", json=posting.request),
        client.post("/tasks", json=posting.request),
    )
    first_body = _assert_published(first, posting)
    second_body = _assert_published(second, posting)

    assert arrivals == 2
    assert admission_states == ["awaiting_lock", "awaiting_lock"]
    assert first_body == second_body
    assert lock.await_count == 2
    assert (
        len(
            _rows(
                gateway_db_path,
                "SELECT 1 FROM bank_operation_receipts WHERE operation_id=?",
                (posting.intent.lock_operation_id,),
            )
        )
        == 1
    )
    assert (
        len(
            _rows(
                gateway_db_path,
                "SELECT 1 FROM board_tasks WHERE task_id=?",
                (posting.intent.task_id,),
            )
        )
        == 1
    )
    assert (
        len(
            _rows(
                gateway_db_path,
                "SELECT 1 FROM events WHERE task_id=? AND event_type='task.created'",
                (posting.intent.task_id,),
            )
        )
        == 1
    )
    assert (
        _rows(
            gateway_db_path,
            "SELECT operation_kind FROM bank_operation_receipts WHERE escrow_id=? "
            "AND operation_kind IN ('escrow_release','escrow_split')",
            (posting.escrow_id,),
        )
        == []
    )


async def test_changed_terms_conflict_before_any_new_bank_effect(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    state = get_app_state()
    assert state.central_bank_client is not None

    async def _ambiguous_no_effect(escrow_token: str) -> dict[str, Any]:
        assert escrow_token == posting.escrow_token
        await _assert_saved_before_bank(gateway_client, gateway_db_path, posting)
        raise ServiceError("central_bank_unavailable", "Injected ambiguous Bank response", 502, {})

    lock = AsyncMock(side_effect=_ambiguous_no_effect)
    state.central_bank_client.escrow_lock = lock
    pending_response = await client.post("/tasks", json=posting.request)
    _assert_awaiting(pending_response, posting)
    assert lock.await_count == 1
    assert (
        _rows(
            gateway_db_path,
            "SELECT 1 FROM bank_operation_receipts WHERE operation_id=?",
            (posting.intent.lock_operation_id,),
        )
        == []
    )

    changed_intent = replace(posting.intent, title="Changed after admission")
    changed_task_token, _same_escrow_token = _tokens(posting.private_key, changed_intent)
    conflict = await client.post(
        "/tasks",
        json={"task_token": changed_task_token, "escrow_token": posting.escrow_token},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"] == "operation_id_conflict"
    assert lock.await_count == 1


async def test_late_bank_without_receipt_stays_pending_and_fresh_retry_publishes(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    state = get_app_state()
    assert state.central_bank_client is not None
    attempts = 0

    async def _late_then_commit(escrow_token: str) -> dict[str, Any]:
        nonlocal attempts
        _assert_signed_context(posting, escrow_token)
        attempts += 1
        await _assert_saved_before_bank(gateway_client, gateway_db_path, posting)
        if attempts == 1:
            raise ServiceError("central_bank_unavailable", "No receipt observed yet", 502, {})
        return await _persist_real_lock(gateway_client, posting)

    lock = AsyncMock(side_effect=_late_then_commit)
    state.central_bank_client.escrow_lock = lock
    pending = await client.post("/tasks", json=posting.request)
    _assert_awaiting(pending, posting)

    fresh_task_token, fresh_escrow_token = _tokens(posting.private_key, posting.intent)
    retry = await client.post(
        "/tasks",
        json={"task_token": fresh_task_token, "escrow_token": fresh_escrow_token},
    )
    _assert_published(retry, posting)
    assert attempts == 2
    assert lock.await_count == 2


@pytest.mark.parametrize("publication_state", ["awaiting_lock", "published"])
async def test_public_status_filters_verified_owner_and_hides_foreign_or_missing(
    publication_state: str,
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    foreign_id = f"a-mp05-foreign-{uuid.uuid4()}"
    await _register_signer(gateway_client, foreign_id, worker_keypair[0])
    admitted = await _admit_gateway(gateway_client, posting)
    assert admitted.status_code == 202, admitted.text
    expected = cast("dict[str, Any]", admitted.json())
    if publication_state == "published":
        await _persist_real_lock(gateway_client, posting)
        reconciled = await _reconcile_gateway(gateway_client, posting)
        assert reconciled.status_code == 201, reconciled.text
        expected = cast("dict[str, Any]", reconciled.json())

    state = get_app_state()
    assert state.task_manager is not None
    store = state.task_manager.store
    original_request = store._request
    owner_filters: list[dict[str, Any] | None] = []

    def _record_owner_filter(
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        if method == "GET" and path.startswith("/board/task-publications/"):
            owner_filters.append(params)
        return original_request(method, path, json=json, params=params)

    monkeypatch.setattr(store, "_request", _record_owner_filter)
    path = f"/task-publications/{posting.intent.operation_id}"
    owner = await client.get(
        path,
        headers=_status_headers(
            posting.private_key, posting.intent.actor_id, posting.intent.operation_id
        ),
    )
    assert owner.status_code == 200, owner.text
    assert parse_task_posting_status_response(owner, posting.intent.operation_id).body == expected

    foreign = await client.get(
        path,
        headers=_status_headers(worker_keypair[0], foreign_id, posting.intent.operation_id),
    )
    assert foreign.status_code == 404
    assert foreign.json() == _NOT_FOUND
    missing_id = f"op-{uuid.uuid4()}"
    for key, owner_id in (
        (posting.private_key, posting.intent.actor_id),
        (worker_keypair[0], foreign_id),
    ):
        missing = await client.get(
            f"/task-publications/{missing_id}",
            headers=_status_headers(key, owner_id, missing_id),
        )
        assert missing.status_code == 404
        assert missing.json() == _NOT_FOUND
    assert owner_filters == [
        {"poster_id": posting.intent.actor_id},
        {"poster_id": foreign_id},
        {"poster_id": posting.intent.actor_id},
        {"poster_id": foreign_id},
    ]


async def test_wrong_escrow_signature_is_rejected_before_admission_or_bank(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    state = get_app_state()
    assert state.platform_agent is not None
    assert state.central_bank_client is not None
    verified_task = state.platform_agent.validate_certificate(posting.task_token)
    assert verified_task.header.kid == posting.intent.actor_id
    invalid_escrow = create_jws(
        _escrow_payload(posting.intent),
        worker_keypair[0],
        kid=posting.intent.actor_id,
        audience="central-bank",
        resource="POST /escrow/lock",
        token_ttl_seconds=60,
    )
    lock = cast("AsyncMock", state.central_bank_client.escrow_lock)

    response = await client.post(
        "/tasks",
        json={"task_token": posting.task_token, "escrow_token": invalid_escrow},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    assert lock.await_count == 0
    assert _publication_row(gateway_db_path, posting) == []


@pytest.mark.parametrize(
    ("audience", "resource"),
    [
        ("task-board", "POST /escrow/lock"),
        ("central-bank", "POST /escrow/other"),
    ],
)
async def test_wrong_escrow_context_is_rejected_before_admission_or_bank(
    audience: str,
    resource: str,
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    state = get_app_state()
    assert state.platform_agent is not None
    assert state.central_bank_client is not None
    verified_task = state.platform_agent.validate_certificate(posting.task_token)
    assert verified_task.header.kid == posting.intent.actor_id
    invalid_escrow = create_jws(
        _escrow_payload(posting.intent),
        posting.private_key,
        kid=posting.intent.actor_id,
        audience=audience,
        resource=resource,
        token_ttl_seconds=60,
    )
    lock = cast("AsyncMock", state.central_bank_client.escrow_lock)

    response = await client.post(
        "/tasks",
        json={"task_token": posting.task_token, "escrow_token": invalid_escrow},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"
    assert lock.await_count == 0
    assert _publication_row(gateway_db_path, posting) == []


async def test_invalid_fresh_escrow_context_is_rejected_before_admission(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    invalid_escrow = make_jws_token(
        posting.private_key,
        posting.intent.actor_id,
        {
            "action": "escrow_lock",
            "operation_id": posting.intent.lock_operation_id,
            "task_id": posting.intent.task_id,
            "agent_id": posting.intent.actor_id,
            "amount": posting.intent.reward + 1,
        },
        audience="central-bank",
        resource="POST /escrow/lock",
    )
    state = get_app_state()
    assert state.central_bank_client is not None
    lock = cast("AsyncMock", state.central_bank_client.escrow_lock)
    response = await client.post(
        "/tasks",
        json={"task_token": posting.task_token, "escrow_token": invalid_escrow},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "token_mismatch"
    assert lock.await_count == 0
    assert _publication_row(gateway_db_path, posting) == []


def _assert_no_publication_or_refund(
    gateway_db_path: str,
    posting: _Posting,
    *,
    escrow_id: str,
) -> None:
    assert (
        _rows(
            gateway_db_path,
            "SELECT 1 FROM board_tasks WHERE task_id=?",
            (posting.intent.task_id,),
        )
        == []
    )
    assert (
        _rows(
            gateway_db_path,
            "SELECT 1 FROM events WHERE task_id=? AND event_type='task.created'",
            (posting.intent.task_id,),
        )
        == []
    )
    assert (
        _rows(
            gateway_db_path,
            "SELECT operation_kind FROM bank_operation_receipts WHERE escrow_id=? "
            "AND operation_kind IN ('escrow_release','escrow_split')",
            (escrow_id,),
        )
        == []
    )


async def _persist_real_lock_with_operation_id(
    gateway_client: httpx.AsyncClient,
    posting: _Posting,
    operation_id: str,
    escrow_id: str,
) -> dict[str, Any]:
    created_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    response = await gateway_client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "payer_account_id": posting.intent.actor_id,
            "amount": posting.intent.reward,
            "task_id": posting.intent.task_id,
            "created_at": created_at,
            "tx_id": f"tx-{uuid.uuid4()}",
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.locked",
                "timestamp": created_at,
                "task_id": posting.intent.task_id,
                "agent_id": posting.intent.actor_id,
                "summary": f"Locked escrow for {posting.intent.task_id}",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text
    envelope = cast("dict[str, Any]", response.json())
    assert envelope["outcome"] == "native_receipt"
    return envelope


async def test_bank_response_without_persisted_receipt_never_publishes(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    state = get_app_state()
    assert state.central_bank_client is not None

    async def _unpersisted_legacy_response(escrow_token: str) -> dict[str, Any]:
        assert escrow_token == posting.escrow_token
        _assert_signed_context(posting, escrow_token)
        await _assert_saved_before_bank(gateway_client, gateway_db_path, posting)
        return {"outcome": "legacy_observation", "escrow_id": posting.escrow_id}

    lock = AsyncMock(side_effect=_unpersisted_legacy_response)
    state.central_bank_client.escrow_lock = lock

    first = await client.post("/tasks", json=posting.request)
    first_body = _assert_awaiting(first, posting)
    replay = await client.post("/tasks", json=posting.request)
    replay_body = _assert_awaiting(replay, posting)

    assert replay_body == first_body
    assert lock.await_count == 2
    assert _publication_row(gateway_db_path, posting) == [
        {
            "operation_id": posting.intent.operation_id,
            "task_id": posting.intent.task_id,
            "actor_id": posting.intent.actor_id,
            "lock_operation_id": posting.intent.lock_operation_id,
            "canonical_request_json": _canonical(posting.intent.as_json()),
            "request_sha256": posting.intent.request_sha256,
            "state": "awaiting_lock",
            "escrow_id": None,
            "result_json": None,
        }
    ]
    assert (
        _rows(
            gateway_db_path,
            "SELECT 1 FROM bank_operation_receipts WHERE operation_id=?",
            (posting.intent.lock_operation_id,),
        )
        == []
    )
    _assert_no_publication_or_refund(
        gateway_db_path,
        posting,
        escrow_id=posting.escrow_id,
    )


async def test_matching_lock_under_different_operation_blocks_without_adoption(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    poster_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    posting = await _new_posting(gateway_client, poster_keypair[0])
    other_lock_operation_id = f"op-{uuid.uuid4()}"
    other_escrow_id = f"esc-{uuid.uuid4()}"
    state = get_app_state()
    assert state.central_bank_client is not None

    async def _persist_other_operation(escrow_token: str) -> dict[str, Any]:
        assert escrow_token == posting.escrow_token
        _assert_signed_context(posting, escrow_token)
        await _assert_saved_before_bank(gateway_client, gateway_db_path, posting)
        return await _persist_real_lock_with_operation_id(
            gateway_client,
            posting,
            other_lock_operation_id,
            other_escrow_id,
        )

    lock = AsyncMock(side_effect=_persist_other_operation)
    state.central_bank_client.escrow_lock = lock

    blocked = await client.post("/tasks", json=posting.request)
    assert blocked.status_code == 409, blocked.text
    blocked_body = cast("dict[str, Any]", blocked.json())
    assert blocked_body["error"] == "publication_integrity_blocked"
    assert blocked_body["details"]["publication"]["publication_state"] == "blocked_integrity"
    assert blocked_body["details"]["publication"]["operation_id"] == posting.intent.operation_id
    assert blocked_body["details"]["publication"]["task_id"] == posting.intent.task_id
    assert blocked_body["details"]["publication"]["lock_operation_id"] == (
        posting.intent.lock_operation_id
    )
    assert blocked_body["details"]["publication"]["error"] == {
        "error": "publication_integrity_blocked",
        "message": "Publication evidence requires reconciliation",
        "details": {"reason": "receipt_invalid"},
    }

    replay = await client.post("/tasks", json=posting.request)
    assert replay.status_code == 409, replay.text
    assert replay.json() == blocked_body
    assert lock.await_count == 1
    assert _rows(
        gateway_db_path,
        "SELECT operation_id,operation_kind,escrow_id FROM bank_operation_receipts "
        "WHERE operation_kind='escrow_lock' AND source_reference=?",
        (posting.intent.task_id,),
    ) == [
        {
            "operation_id": other_lock_operation_id,
            "operation_kind": "escrow_lock",
            "escrow_id": other_escrow_id,
        }
    ]
    row = _publication_row(gateway_db_path, posting)
    assert row[0]["state"] == "blocked_integrity"
    assert row[0]["escrow_id"] is None
    assert row[0]["result_json"] is None
    _assert_no_publication_or_refund(
        gateway_db_path,
        posting,
        escrow_id=other_escrow_id,
    )
