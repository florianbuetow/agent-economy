"""MP-05 acceptance contract for durable native Task Board publication.

The current baseline has no native publication routes. Until those routes exist,
failures in this module are interface-absence failures; the archived MP-05
old-interface suites remain the genuine product-behavior red evidence.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair

from db_gateway_service.config import clear_settings_cache
from db_gateway_service.core.state import init_app_state
from db_gateway_service.task_time import (
    add_seconds_to_epoch_us,
    epoch_us_to_utc_text,
    parse_utc_text_to_epoch_us,
    utc_now_us,
)
from tests.fakes.mp05_publication_history import (
    PublicationIntent,
    assert_uniform_error,
    create_funded_poster,
    lock_intent,
    publication_intent,
    release_escrow,
)
from tests.fakes.native_board_history import (
    TaskGraph,
    accept_bid,
    register_worker,
    submit_bid,
)
from tests.unit.conftest import _create_test_config

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit

_PUBLICATIONS = "/board/task-publications"


@dataclass(frozen=True, slots=True)
class PublicationGateway:
    """Real authenticated Gateway with two independent Board writers."""

    client: TestClient
    signers: Mapping[str, GatewayRequestSigner]


class _DomainAuth(httpx.Auth):
    """Sign ordinary setup calls with the matching trusted service key."""

    def __init__(self, signers: Mapping[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        domain = request.url.path.split("/", maxsplit=2)[1]
        signer = self._signers[domain]
        access = "read" if request.method == "GET" else "write"
        yield signer.sign_request(request, action=f"gateway.{domain}.{access}")


@pytest.fixture
def publication_gateway(
    db_writer: DbWriter,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[PublicationGateway]:
    """Start the actual app with a foreign but equally capable Board caller."""
    foreign_kid = "gateway-task-board-foreign"
    generate_keypair(foreign_kid, tmp_path)
    foreign = GatewayRequestSigner(
        GatewaySignerConfig(
            kid=foreign_kid,
            private_key_path=Path(f"{foreign_kid}.key"),
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )
    signers = {**gateway_test_signers, "board_foreign": foreign}
    config_path = tmp_path / "mp05-publication-config.yaml"
    _create_test_config(db_writer._db_path, config_path)
    with config_path.open("a", encoding="utf-8") as config_file:
        config_file.write(
            f"""
    {foreign_kid}:
      public_key_path: "{foreign_kid}.pub"
      capabilities: ["gateway.board.write"]
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
        yield PublicationGateway(client, signers)

    os.environ.pop("CONFIG_PATH", None)
    clear_settings_cache()


def _signed_request(
    gateway: PublicationGateway,
    caller: str,
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
    action: str = "gateway.board.write",
) -> httpx.Response:
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = httpx.Request(
        method,
        f"http://testserver{path}",
        content=body,
        headers={"Content-Type": "application/json"} if payload is not None else None,
    )
    signed = gateway.signers[caller].sign_request(request, action=action)
    headers = {"Authorization": signed.headers["Authorization"]}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    return gateway.client.request(method, path, content=body, headers=headers, auth=None)


def _post(
    gateway: PublicationGateway,
    intent: PublicationIntent,
    *,
    caller: str = "board",
) -> httpx.Response:
    return _signed_request(gateway, caller, "POST", _PUBLICATIONS, payload=intent.body())


def _reconcile(
    gateway: PublicationGateway,
    operation_id: str,
    *,
    caller: str = "board",
) -> httpx.Response:
    return _signed_request(
        gateway,
        caller,
        "POST",
        f"{_PUBLICATIONS}/{operation_id}/reconcile",
        payload={},
    )


def _get_status(
    gateway: PublicationGateway,
    operation_id: str,
    *,
    caller: str = "board",
) -> httpx.Response:
    return _signed_request(gateway, caller, "GET", f"{_PUBLICATIONS}/{operation_id}")


def _get_recovery(
    gateway: PublicationGateway,
    *,
    caller: str = "board",
    after: str = "",
    limit: int | str = 100,
) -> httpx.Response:
    query = urlencode({"after": after, "limit": limit})
    return _signed_request(gateway, caller, "GET", f"/board/posting-recovery?{query}")


def _assert_base_envelope(
    payload: dict[str, Any],
    intent: PublicationIntent,
    state: str,
) -> None:
    assert set(payload) == {
        "protocol_version",
        "operation_id",
        "task_id",
        "request_sha256",
        "publication_state",
        "lock_operation_id",
        "escrow_id",
        "admitted_at",
        "published_at",
        "result",
        "error",
    }
    assert payload["protocol_version"] == 1
    assert payload["operation_id"] == intent.operation_id
    assert payload["task_id"] == intent.task_id
    assert payload["request_sha256"] == intent.request_sha256()
    assert payload["publication_state"] == state
    assert payload["lock_operation_id"] == intent.lock_operation_id
    assert parse_utc_text_to_epoch_us(payload["admitted_at"]) >= 0


def _assert_awaiting(payload: dict[str, Any], intent: PublicationIntent) -> None:
    _assert_base_envelope(payload, intent, "awaiting_lock")
    assert payload["escrow_id"] is None
    assert payload["published_at"] is None
    assert payload["result"] is None
    assert payload["error"] is None


def _assert_published(
    payload: dict[str, Any],
    intent: PublicationIntent,
    escrow_id: str,
) -> None:
    _assert_base_envelope(payload, intent, "published")
    assert payload["escrow_id"] == escrow_id
    assert isinstance(payload["published_at"], str)
    assert payload["error"] is None
    published_us = parse_utc_text_to_epoch_us(payload["published_at"])
    bidding_us = add_seconds_to_epoch_us(published_us, intent.bidding_deadline_seconds)
    acceptance_us = add_seconds_to_epoch_us(bidding_us, intent.acceptance_window_seconds)
    assert payload["result"] == {
        "task_id": intent.task_id,
        "poster_id": intent.poster_id,
        "title": intent.title,
        "spec": intent.spec,
        "reward": intent.reward,
        "bidding_deadline_seconds": intent.bidding_deadline_seconds,
        "acceptance_window_seconds": intent.acceptance_window_seconds,
        "execution_deadline_seconds": intent.execution_deadline_seconds,
        "review_deadline_seconds": intent.review_deadline_seconds,
        "status": "open",
        "escrow_id": escrow_id,
        "created_at": payload["published_at"],
        "bidding_deadline": epoch_us_to_utc_text(bidding_us),
        "acceptance_deadline": epoch_us_to_utc_text(acceptance_us),
        "bidding_cutoff_us": bidding_us,
        "acceptance_cutoff_us": acceptance_us,
        "lifecycle_version": 0,
        "timing_contract_version": 2,
        "event_id": payload["result"]["event_id"],
    }
    assert type(payload["result"]["event_id"]) is int
    assert payload["result"]["event_id"] > 0


def _assert_no_task_visibility(writer: DbWriter, task_id: str) -> None:
    assert (
        writer.connection.execute(
            "SELECT COUNT(*) FROM board_tasks WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND event_type='task.created'", (task_id,)
        ).fetchone()[0]
        == 0
    )


def _assert_blocked(
    response: httpx.Response,
    intent: PublicationIntent,
    reason: str,
) -> dict[str, Any]:
    payload = assert_uniform_error(response, 409, "publication_integrity_blocked")
    assert payload["message"] == "Publication evidence requires reconciliation"
    assert set(payload["details"]) == {"publication"}
    publication = payload["details"]["publication"]
    _assert_base_envelope(publication, intent, "blocked_integrity")
    assert publication["escrow_id"] is None
    assert publication["published_at"] is None
    assert publication["result"] is None
    assert publication["error"] == {
        "error": "publication_integrity_blocked",
        "message": "Publication evidence requires reconciliation",
        "details": {"reason": reason},
    }
    return payload


def test_admission_replay_hash_conflicts_and_distinct_intents(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
) -> None:
    poster_id = create_funded_poster(publication_gateway.client)
    intent = publication_intent(poster_id, title="Résumé publication")

    admitted = _post(publication_gateway, intent)
    assert admitted.status_code == 202, admitted.text
    _assert_awaiting(admitted.json(), intent)
    replay = _post(publication_gateway, intent)
    assert replay.status_code == 202, replay.text
    assert replay.json() == admitted.json()

    stored = db_writer.connection.execute(
        "SELECT canonical_request_json,request_sha256,caller_kid,state "
        "FROM board_task_publications WHERE operation_id=?",
        (intent.operation_id,),
    ).fetchone()
    assert stored is not None
    assert tuple(stored) == (
        json.dumps(
            intent.body(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
        intent.request_sha256(),
        "gateway-task-board",
        "awaiting_lock",
    )

    changed = replace(intent, title="Changed publication")
    conflict = _post(publication_gateway, changed)
    assert_uniform_error(conflict, 409, "operation_id_conflict")

    second = publication_intent(
        poster_id,
        title=intent.title,
        spec=intent.spec,
        reward=intent.reward,
    )
    second_response = _post(publication_gateway, second)
    assert second_response.status_code == 202, second_response.text
    _assert_awaiting(second_response.json(), second)
    assert second.operation_id != intent.operation_id
    assert second.task_id != intent.task_id
    assert second.request_sha256() != intent.request_sha256()


def test_publication_and_lock_operation_identities_must_differ(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
) -> None:
    poster_id = create_funded_poster(publication_gateway.client)
    original = publication_intent(poster_id)
    reused = replace(original, operation_id=original.lock_operation_id)

    response = _post(publication_gateway, reused)

    assert_uniform_error(response, 400, "invalid_payload")
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM board_task_publications WHERE operation_id=?",
            (reused.operation_id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
            (reused.operation_id,),
        ).fetchone()[0]
        == 0
    )
    _assert_no_task_visibility(db_writer, reused.task_id)


def test_task_reservation_precedes_bank_for_different_operation_or_payer(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
) -> None:
    owner = create_funded_poster(publication_gateway.client)
    foreign_poster = create_funded_poster(publication_gateway.client)
    first = publication_intent(owner)
    admitted = _post(publication_gateway, first)
    assert admitted.status_code == 202, admitted.text

    for conflicting in (
        publication_intent(owner, task_id=first.task_id),
        publication_intent(foreign_poster, task_id=first.task_id),
    ):
        before_receipts = db_writer.connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts"
        ).fetchone()
        response = _post(publication_gateway, conflicting)
        assert_uniform_error(response, 409, "task_id_conflict")
        assert (
            db_writer.connection.execute("SELECT COUNT(*) FROM bank_operation_receipts").fetchone()
            == before_receipts
        )
        assert (
            db_writer.connection.execute(
                "SELECT COUNT(*) FROM board_task_publications WHERE operation_id=?",
                (conflicting.operation_id,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("history", ["same_locked", "foreign_locked", "same_released"])
def test_fresh_admission_never_adopts_old_financial_history(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
    history: str,
) -> None:
    owner = create_funded_poster(publication_gateway.client)
    intent = publication_intent(owner)
    history_payer = owner
    history_operation = intent.lock_operation_id
    if history == "foreign_locked":
        history_payer = create_funded_poster(publication_gateway.client)
        history_operation = f"op-{uuid4()}"
    escrow_id, _receipt = lock_intent(
        publication_gateway.client,
        intent,
        payer_id=history_payer,
        operation_id=history_operation,
    )
    if history == "same_released":
        release_escrow(
            publication_gateway.client,
            escrow_id=escrow_id,
            recipient_id=owner,
            task_id=intent.task_id,
        )

    response = _post(publication_gateway, intent)
    assert_uniform_error(response, 409, "task_id_conflict")
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM board_task_publications WHERE operation_id=?",
            (intent.operation_id,),
        ).fetchone()[0]
        == 0
    )
    _assert_no_task_visibility(db_writer, intent.task_id)


def test_genuine_receipt_publishes_atomically_and_replay_stays_immutable(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
) -> None:
    poster_id = create_funded_poster(publication_gateway.client)
    intent = publication_intent(poster_id)
    admitted = _post(publication_gateway, intent)
    assert admitted.status_code == 202, admitted.text
    _assert_awaiting(admitted.json(), intent)
    escrow_id, receipt = lock_intent(publication_gateway.client, intent)
    receipt_payload = receipt.json()["receipt"]
    financial_times = db_writer.connection.execute(
        "SELECT r.committed_at,e.created_at,t.timestamp,v.timestamp "
        "FROM bank_operation_receipts AS r "
        "JOIN bank_escrow AS e ON e.escrow_id=r.escrow_id "
        "JOIN bank_operation_legs AS l ON l.operation_id=r.operation_id AND l.role='payer' "
        "JOIN bank_transactions AS t ON t.tx_id=l.transaction_id "
        "JOIN events AS v ON v.event_id=r.event_id "
        "WHERE r.operation_id=?",
        (intent.lock_operation_id,),
    ).fetchone()
    assert financial_times is not None
    assert tuple(financial_times) == (receipt_payload["committed_at"],) * 4

    before_publish_us = utc_now_us()
    published = _reconcile(publication_gateway, intent.operation_id)
    assert published.status_code == 201, published.text
    frozen = published.json()
    _assert_published(frozen, intent, escrow_id)
    assert parse_utc_text_to_epoch_us(frozen["published_at"]) >= parse_utc_text_to_epoch_us(
        frozen["admitted_at"]
    )
    assert parse_utc_text_to_epoch_us(frozen["published_at"]) >= before_publish_us

    publication = db_writer.connection.execute(
        "SELECT state,escrow_id,lock_request_sha256,admitted_at_us,published_at_us,"
        "creation_event_id,result_json "
        "FROM board_task_publications WHERE operation_id=?",
        (intent.operation_id,),
    ).fetchone()
    assert publication is not None
    assert tuple(publication) == (
        "published",
        escrow_id,
        receipt_payload["request_sha256"],
        parse_utc_text_to_epoch_us(frozen["admitted_at"]),
        parse_utc_text_to_epoch_us(frozen["published_at"]),
        frozen["result"]["event_id"],
        json.dumps(frozen["result"], sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    )
    task = db_writer.connection.execute(
        "SELECT poster_id,title,spec,reward,status,escrow_id,created_at,bidding_deadline "
        "FROM board_tasks WHERE task_id=?",
        (intent.task_id,),
    ).fetchone()
    assert task is not None
    assert tuple(task) == (
        poster_id,
        intent.title,
        intent.spec,
        intent.reward,
        "open",
        escrow_id,
        frozen["published_at"],
        frozen["result"]["bidding_deadline"],
    )
    contract = db_writer.connection.execute(
        "SELECT lifecycle_version,timing_contract_version,acceptance_window_seconds,"
        "bidding_cutoff_us,acceptance_cutoff_us FROM board_task_contracts WHERE task_id=?",
        (intent.task_id,),
    ).fetchone()
    assert contract is not None
    assert tuple(contract) == (
        0,
        2,
        intent.acceptance_window_seconds,
        frozen["result"]["bidding_cutoff_us"],
        frozen["result"]["acceptance_cutoff_us"],
    )
    creation_events = db_writer.connection.execute(
        "SELECT event_source,event_type,timestamp,task_id,agent_id FROM events "
        "WHERE task_id=? AND event_type='task.created'",
        (intent.task_id,),
    ).fetchall()
    assert [tuple(row) for row in creation_events] == [
        (
            "task-board",
            "task.created",
            frozen["published_at"],
            intent.task_id,
            intent.poster_id,
        )
    ]

    worker_id = register_worker(publication_gateway.client)
    graph = TaskGraph(intent.task_id, poster_id, escrow_id)
    bid_id, bid = submit_bid(publication_gateway.client, graph, worker_id, amount=75)
    assert bid.status_code == 200, bid.text
    accepted = accept_bid(
        publication_gateway.client,
        graph,
        worker_id,
        bid_id,
        bid_amount=75,
    )
    assert accepted.status_code == 200, accepted.text

    replay = _post(publication_gateway, intent)
    status = _get_status(publication_gateway, intent.operation_id)
    assert replay.status_code == 201, replay.text
    assert status.status_code == 200, status.text
    assert replay.json() == frozen
    assert status.json() == frozen
    assert (
        db_writer.connection.execute(
            "SELECT status FROM board_tasks WHERE task_id=?", (intent.task_id,)
        ).fetchone()[0]
        == "accepted"
    )
    recovery = _get_recovery(publication_gateway)
    assert recovery.status_code == 200, recovery.text
    assert recovery.json() == {"items": [], "next_after": None}


def test_pending_recovery_is_private_and_disappears_after_publication(
    publication_gateway: PublicationGateway,
) -> None:
    poster_id = create_funded_poster(publication_gateway.client)
    intent = publication_intent(poster_id)
    admitted = _post(publication_gateway, intent)
    assert admitted.status_code == 202, admitted.text

    owner_inventory = _get_recovery(publication_gateway)
    assert owner_inventory.status_code == 200, owner_inventory.text
    assert owner_inventory.json() == {
        "items": [
            {
                "cursor": f"native:{intent.operation_id}",
                "kind": "native",
                "identity": intent.operation_id,
            }
        ],
        "next_after": None,
    }
    foreign_inventory = _get_recovery(publication_gateway, caller="board_foreign")
    assert foreign_inventory.status_code == 200, foreign_inventory.text
    assert foreign_inventory.json() == {"items": [], "next_after": None}

    lock_intent(publication_gateway.client, intent)
    published = _reconcile(publication_gateway, intent.operation_id)
    assert published.status_code == 201, published.text
    after = _get_recovery(publication_gateway)
    assert after.status_code == 200, after.text
    assert after.json() == {"items": [], "next_after": None}


def test_recovery_enumeration_uses_sorted_tagged_cursor_pages(
    publication_gateway: PublicationGateway,
) -> None:
    poster_id = create_funded_poster(publication_gateway.client)
    operation_ids = [f"op-00000000-0000-4000-8000-00000000000{suffix}" for suffix in range(1, 6)]
    intents = [publication_intent(poster_id, operation_id=value) for value in operation_ids]
    for intent in reversed(intents):
        response = _post(publication_gateway, intent)
        assert response.status_code == 202, response.text

    foreign = publication_intent(
        poster_id,
        operation_id="op-00000000-0000-4000-8000-000000000000",
    )
    foreign_response = _post(publication_gateway, foreign, caller="board_foreign")
    assert foreign_response.status_code == 202, foreign_response.text

    first = _get_recovery(publication_gateway, limit=2)
    assert first.status_code == 200, first.text
    assert first.json() == {
        "items": [
            {
                "cursor": f"native:{operation_ids[0]}",
                "kind": "native",
                "identity": operation_ids[0],
            },
            {
                "cursor": f"native:{operation_ids[1]}",
                "kind": "native",
                "identity": operation_ids[1],
            },
        ],
        "next_after": f"native:{operation_ids[1]}",
    }
    second = _get_recovery(
        publication_gateway,
        after=first.json()["next_after"],
        limit=2,
    )
    assert second.status_code == 200, second.text
    assert second.json() == {
        "items": [
            {
                "cursor": f"native:{operation_ids[2]}",
                "kind": "native",
                "identity": operation_ids[2],
            },
            {
                "cursor": f"native:{operation_ids[3]}",
                "kind": "native",
                "identity": operation_ids[3],
            },
        ],
        "next_after": f"native:{operation_ids[3]}",
    }
    third = _get_recovery(
        publication_gateway,
        after=second.json()["next_after"],
        limit=2,
    )
    assert third.status_code == 200, third.text
    assert third.json() == {
        "items": [
            {
                "cursor": f"native:{operation_ids[4]}",
                "kind": "native",
                "identity": operation_ids[4],
            }
        ],
        "next_after": None,
    }


@pytest.mark.parametrize(
    "path",
    [
        "/board/posting-recovery?limit=1",
        "/board/posting-recovery?after=",
        "/board/posting-recovery?after=plain&limit=1",
        "/board/posting-recovery?after=unknown%3Aop-00000000-0000-4000-8000-000000000001&limit=1",
        "/board/posting-recovery?after=native%3Anot-an-operation&limit=1",
        "/board/posting-recovery?after=legacy%3Anot-an-observation&limit=1",
        "/board/posting-recovery?after=&limit=0",
        "/board/posting-recovery?after=&limit=-1",
        "/board/posting-recovery?after=&limit=1001",
        "/board/posting-recovery?after=&limit=1.0",
        "/board/posting-recovery?after=&limit=true",
    ],
)
def test_recovery_rejects_missing_or_invalid_cursor_and_limit(
    publication_gateway: PublicationGateway,
    path: str,
) -> None:
    response = _signed_request(publication_gateway, "board", "GET", path)
    assert_uniform_error(response, 400, "invalid_payload")


def test_publication_and_lifecycle_operation_ids_are_mutually_exclusive(
    publication_gateway: PublicationGateway,
) -> None:
    poster_id = create_funded_poster(publication_gateway.client)
    intent = publication_intent(poster_id)
    admitted = _post(publication_gateway, intent)
    assert admitted.status_code == 202, admitted.text
    escrow_id, _receipt = lock_intent(publication_gateway.client, intent)
    published = _reconcile(publication_gateway, intent.operation_id)
    assert published.status_code == 201, published.text

    worker_id = register_worker(publication_gateway.client)
    graph = TaskGraph(intent.task_id, poster_id, escrow_id)
    _bid_id, reused_publication_id = submit_bid(
        publication_gateway.client,
        graph,
        worker_id,
        operation_id=intent.operation_id,
    )
    assert_uniform_error(reused_publication_id, 409, "operation_id_conflict")

    lifecycle_id = f"op-{uuid4()}"
    _fresh_bid, lifecycle = submit_bid(
        publication_gateway.client,
        graph,
        worker_id,
        operation_id=lifecycle_id,
    )
    assert lifecycle.status_code == 200, lifecycle.text
    second_intent = publication_intent(poster_id, operation_id=lifecycle_id)
    reused_lifecycle_id = _post(publication_gateway, second_intent)
    assert_uniform_error(reused_lifecycle_id, 409, "operation_id_conflict")


def _lock_for_financial_case(
    gateway: PublicationGateway,
    intent: PublicationIntent,
    mismatch: str,
) -> tuple[str, httpx.Response]:
    """Create a genuine receipt, varying only the requested business identity."""
    payer = intent.poster_id
    task_id = intent.task_id
    amount = intent.reward
    if mismatch == "payer":
        payer = create_funded_poster(gateway.client)
    elif mismatch == "task":
        task_id = f"t-{uuid4()}"
    elif mismatch == "amount":
        amount += 1
    return lock_intent(
        gateway.client,
        intent,
        payer_id=payer,
        task_id=task_id,
        amount=amount,
    )


def _corrupt_persisted_lock(
    gateway: PublicationGateway,
    writer: DbWriter,
    intent: PublicationIntent,
    mismatch: str,
    escrow_id: str,
    receipt: httpx.Response,
) -> None:
    """Apply one narrowly named negative fixture after a real Bank commit."""
    receipt_payload = receipt.json()["receipt"]
    transaction_id = receipt_payload["legs"][0]["transaction_id"]
    if mismatch == "resolved":
        release_escrow(
            gateway.client,
            escrow_id=escrow_id,
            recipient_id=intent.poster_id,
            task_id=intent.task_id,
        )
        return

    other_account: str | None = None
    if mismatch in {"escrow_payer", "transaction_account"}:
        other_account = create_funded_poster(gateway.client)
    mutations: dict[str, tuple[str, tuple[object, ...]]] = {
        "escrow_payer": (
            "UPDATE bank_escrow SET payer_account_id=? WHERE escrow_id=?",
            (other_account, escrow_id),
        ),
        "escrow_task": (
            "UPDATE bank_escrow SET task_id=? WHERE escrow_id=?",
            (f"t-{uuid4()}", escrow_id),
        ),
        "escrow_amount": (
            "UPDATE bank_escrow SET amount=amount+1 WHERE escrow_id=?",
            (escrow_id,),
        ),
        "escrow_time": (
            "UPDATE bank_escrow SET created_at='2001-01-01T00:00:00Z' WHERE escrow_id=?",
            (escrow_id,),
        ),
        "receipt_time": (
            "UPDATE bank_operation_receipts SET committed_at='2001-01-01T00:00:00Z' "
            "WHERE operation_id=?",
            (intent.lock_operation_id,),
        ),
        "leg": (
            "UPDATE bank_operation_legs SET direction='credit' WHERE operation_id=?",
            (intent.lock_operation_id,),
        ),
        "transaction_account": (
            "UPDATE bank_transactions SET account_id=? WHERE tx_id=?",
            (other_account, transaction_id),
        ),
        "transaction_amount": (
            "UPDATE bank_transactions SET amount=amount+1 WHERE tx_id=?",
            (transaction_id,),
        ),
        "transaction_reference": (
            "UPDATE bank_transactions SET reference=? WHERE tx_id=?",
            (f"t-{uuid4()}", transaction_id),
        ),
        "transaction_time": (
            "UPDATE bank_transactions SET timestamp='2001-01-01T00:00:00Z' WHERE tx_id=?",
            (transaction_id,),
        ),
        "transaction_event": (
            "UPDATE bank_transactions SET event_id=NULL WHERE tx_id=?",
            (transaction_id,),
        ),
        "event_source": (
            "UPDATE events SET event_source='task-board' WHERE event_id=?",
            (receipt_payload["event_id"],),
        ),
        "event_type": (
            "UPDATE events SET event_type='escrow.released' WHERE event_id=?",
            (receipt_payload["event_id"],),
        ),
        "event_task": (
            "UPDATE events SET task_id=? WHERE event_id=?",
            (f"t-{uuid4()}", receipt_payload["event_id"]),
        ),
        "event_time": (
            "UPDATE events SET timestamp='2001-01-01T00:00:00Z' WHERE event_id=?",
            (receipt_payload["event_id"],),
        ),
    }
    mutation = mutations.get(mismatch)
    if mutation is not None:
        writer.connection.execute(*mutation)
        writer.connection.commit()


@pytest.mark.parametrize(
    ("mismatch", "reason"),
    [
        ("payer", "receipt_invalid"),
        ("task", "receipt_invalid"),
        ("amount", "receipt_invalid"),
        ("resolved", "settlement_conflict"),
        ("escrow_payer", "escrow_invalid"),
        ("escrow_task", "escrow_invalid"),
        ("escrow_amount", "escrow_invalid"),
        ("escrow_time", "escrow_invalid"),
        ("receipt_time", "receipt_invalid"),
        ("leg", "receipt_invalid"),
        ("transaction_account", "receipt_invalid"),
        ("transaction_amount", "receipt_invalid"),
        ("transaction_reference", "receipt_invalid"),
        ("transaction_time", "receipt_invalid"),
        ("transaction_event", "receipt_invalid"),
        ("event_source", "receipt_invalid"),
        ("event_type", "receipt_invalid"),
        ("event_task", "receipt_invalid"),
        ("event_time", "receipt_invalid"),
    ],
)
def test_financial_mismatch_blocks_without_partial_publication(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
    mismatch: str,
    reason: str,
) -> None:
    poster_id = create_funded_poster(publication_gateway.client)
    intent = publication_intent(poster_id)
    admitted = _post(publication_gateway, intent)
    assert admitted.status_code == 202, admitted.text
    _assert_no_task_visibility(db_writer, intent.task_id)

    escrow_id, receipt = _lock_for_financial_case(publication_gateway, intent, mismatch)
    _corrupt_persisted_lock(
        publication_gateway,
        db_writer,
        intent,
        mismatch,
        escrow_id,
        receipt,
    )

    blocked = _reconcile(publication_gateway, intent.operation_id)
    frozen = _assert_blocked(blocked, intent, reason)
    _assert_no_task_visibility(db_writer, intent.task_id)
    replay_post = _post(publication_gateway, intent)
    replay_reconcile = _reconcile(publication_gateway, intent.operation_id)
    assert replay_post.status_code == 409, replay_post.text
    assert replay_reconcile.status_code == 409, replay_reconcile.text
    assert replay_post.json() == frozen
    assert replay_reconcile.json() == frozen
    assert _get_recovery(publication_gateway).json() == {"items": [], "next_after": None}


def test_status_and_reconciliation_require_owner_write_capability(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
) -> None:
    poster_id = create_funded_poster(publication_gateway.client)
    intent = publication_intent(poster_id)
    admitted = _post(publication_gateway, intent)
    assert admitted.status_code == 202, admitted.text
    before = tuple(db_writer.connection.iterdump())

    for foreign in (
        _post(publication_gateway, intent, caller="board_foreign"),
        _get_status(publication_gateway, intent.operation_id, caller="board_foreign"),
        _reconcile(publication_gateway, intent.operation_id, caller="board_foreign"),
    ):
        assert_uniform_error(foreign, 403, "operation_caller_mismatch")
        assert tuple(db_writer.connection.iterdump()) == before

    wrong_capability_requests = (
        _signed_request(
            publication_gateway,
            "identity",
            "POST",
            _PUBLICATIONS,
            payload=publication_intent(poster_id).body(),
        ),
        _signed_request(
            publication_gateway,
            "identity",
            "POST",
            f"{_PUBLICATIONS}/{intent.operation_id}/reconcile",
            payload={},
        ),
        _signed_request(
            publication_gateway,
            "identity",
            "GET",
            "/board/posting-recovery?after=&limit=100",
        ),
        _signed_request(
            publication_gateway,
            "identity",
            "GET",
            f"{_PUBLICATIONS}/{intent.operation_id}",
        ),
    )
    for wrong_capability in wrong_capability_requests:
        assert_uniform_error(wrong_capability, 403, "forbidden")
        assert tuple(db_writer.connection.iterdump()) == before
    unsigned = publication_gateway.client.get(f"{_PUBLICATIONS}/{intent.operation_id}", auth=None)
    assert_uniform_error(unsigned, 401, "invalid_authorization")

    injected = intent.body()
    injected["caller_kid"] = "gateway-task-board"
    invalid = _signed_request(
        publication_gateway,
        "board",
        "POST",
        _PUBLICATIONS,
        payload=injected,
    )
    assert_uniform_error(invalid, 400, "invalid_payload")
    assert tuple(db_writer.connection.iterdump()) == before
