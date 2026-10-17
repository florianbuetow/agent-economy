"""T-116 acceptance tests for authoritative financial operation identity."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.conftest import make_event

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient
    from httpx import Response

    from db_gateway_service.services.db_writer import DbWriter

_EMPTY_SET_HASH = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
_EMPTY_MARKER_HASH = "a81b1a47e8cd319a73362c83a992f181986e799b1e82753bf139e60b35d0a4f6"


def _install_receipt_sidecar(db_writer: DbWriter) -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "db_gateway_service"
        / "schema_resources"
        / "receipt_v2.sql"
    )
    db_writer.connection.executescript(path.read_text())
    db_writer.connection.execute(
        "INSERT INTO bank_receipt_cutover "
        "(singleton, attestation_version, cutover_kind, legacy_transaction_count, "
        "legacy_escrow_count, legacy_transaction_set_sha256, "
        "legacy_escrow_set_sha256, marker_set_sha256) "
        "VALUES (1, 1, 'fresh_v2', 0, 0, ?, ?, ?)",
        (_EMPTY_SET_HASH, _EMPTY_SET_HASH, _EMPTY_MARKER_HASH),
    )
    db_writer.connection.commit()


@pytest.fixture
def receipt_router(app_with_writer: TestClient) -> Iterator[TestClient]:
    """Use the real v2-started writer for public receipt identity proof."""
    yield app_with_writer


def _register_agent(client: TestClient, agent_id: str) -> None:
    response = client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-10-16T08:00:00Z",
            "event": make_event(agent_id=agent_id),
        },
    )
    assert response.status_code == 201


def _create_account(
    client: TestClient,
    account_id: str,
    balance: int,
    operation_id: str | None,
) -> Response:
    body: dict[str, Any] = {
        "account_id": account_id,
        "balance": balance,
        "created_at": "2026-10-16T08:01:00Z",
        "event": make_event(source="bank", event_type="account.created", agent_id=account_id),
    }
    if balance > 0:
        assert operation_id is not None
        body["operation_id"] = operation_id
        body["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-10-16T08:01:00Z",
        }
    else:
        assert operation_id is None
    response: Response = client.post("/bank/accounts", json=body)
    return response


def _register_zero_account(client: TestClient, account_id: str) -> None:
    _register_agent(client, account_id)
    response = _create_account(client, account_id, 0, None)
    assert response.status_code == 201


def _register_funded_account(
    client: TestClient,
    account_id: str,
    balance: int,
    operation_id: str,
) -> None:
    _register_agent(client, account_id)
    response = _create_account(client, account_id, balance, operation_id)
    assert response.status_code == 201


def _credit_body(
    account_id: str,
    amount: int,
    reference: str,
    operation_id: object,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "tx_id": f"tx-{uuid4()}",
        "account_id": account_id,
        "amount": amount,
        "reference": reference,
        "timestamp": "2026-10-16T08:02:00Z",
        "event": make_event(source="bank", event_type="account.credited", agent_id=account_id),
    }


def _lock_body(
    payer_account_id: str,
    task_id: str,
    escrow_id: str,
    amount: int,
    operation_id: object,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "escrow_id": escrow_id,
        "payer_account_id": payer_account_id,
        "amount": amount,
        "task_id": task_id,
        "created_at": "2026-10-16T08:03:00Z",
        "tx_id": f"tx-{uuid4()}",
        "event": make_event(source="bank", event_type="escrow.locked", task_id=task_id),
    }


def _lock_for_setup(
    client: TestClient,
    payer_account_id: str,
    task_id: str,
    escrow_id: str,
    amount: int,
    operation_id: str,
) -> None:
    response = client.post(
        "/bank/escrow/lock",
        json=_lock_body(payer_account_id, task_id, escrow_id, amount, operation_id),
    )
    assert response.status_code == 201


def _release_body(
    escrow_id: str,
    recipient_account_id: str,
    source_reference: str,
    operation_id: object,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "escrow_id": escrow_id,
        "recipient_account_id": recipient_account_id,
        "source_reference": source_reference,
        "tx_id": f"tx-{uuid4()}",
        "resolved_at": "2026-10-16T08:04:00Z",
        "event": make_event(source="bank", event_type="escrow.released"),
    }


def _split_body(
    escrow_id: str,
    worker_account_id: str,
    poster_account_id: str,
    worker_pct: int,
    source_reference: str,
    operation_id: object,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "escrow_id": escrow_id,
        "worker_account_id": worker_account_id,
        "poster_account_id": poster_account_id,
        "worker_pct": worker_pct,
        "source_reference": source_reference,
        "worker_tx_id": f"tx-{uuid4()}",
        "poster_tx_id": f"tx-{uuid4()}",
        "resolved_at": "2026-10-16T08:04:00Z",
        "event": make_event(source="bank", event_type="escrow.split"),
    }


def _command_for_identity_validation(
    client: TestClient,
    operation_kind: str,
    operation_id: object,
) -> tuple[str, dict[str, Any]]:
    suffix = str(uuid4())
    if operation_kind == "account_issuance":
        account_id = f"a-issuance-{suffix}"
        _register_agent(client, account_id)
        body = {
            "account_id": account_id,
            "balance": 10,
            "created_at": "2026-10-16T08:01:00Z",
            "initial_credit": {
                "tx_id": f"tx-{uuid4()}",
                "amount": 10,
                "reference": "initial_balance",
                "timestamp": "2026-10-16T08:01:00Z",
            },
            "event": make_event(source="bank", event_type="account.created"),
            "operation_id": operation_id,
        }
        return "/bank/accounts", body

    payer_id = f"a-payer-{suffix}"
    payer_issuance_id = f"op-{uuid4()}"
    _register_funded_account(client, payer_id, 100, payer_issuance_id)
    if operation_kind == "credit":
        return "/bank/credit", _credit_body(payer_id, 10, f"credit-{suffix}", operation_id)
    if operation_kind == "escrow_lock":
        return (
            "/bank/escrow/lock",
            _lock_body(payer_id, f"t-{suffix}", f"esc-{suffix}", 40, operation_id),
        )

    escrow_id = f"esc-{suffix}"
    _lock_for_setup(
        client,
        payer_id,
        f"t-{suffix}",
        escrow_id,
        40,
        f"op-{uuid4()}",
    )
    worker_id = f"a-worker-{suffix}"
    _register_zero_account(client, worker_id)
    if operation_kind == "escrow_release":
        return (
            "/bank/escrow/release",
            _release_body(escrow_id, worker_id, f"release-{suffix}", operation_id),
        )
    assert operation_kind == "escrow_split"
    return (
        "/bank/escrow/split",
        _split_body(escrow_id, worker_id, payer_id, 50, f"split-{suffix}", operation_id),
    )


def _account_balance(db_writer: DbWriter, account_id: str) -> int:
    cursor = db_writer.connection.execute(
        "SELECT balance FROM bank_accounts WHERE account_id = ?",
        (account_id,),
    )
    row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _count_rows(db_writer: DbWriter, account_id: str, transaction_type: str) -> int:
    cursor = db_writer.connection.execute(
        "SELECT COUNT(*) FROM bank_transactions WHERE account_id = ? AND type = ?",
        (account_id, transaction_type),
    )
    row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _financial_snapshot(
    db_writer: DbWriter,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    """Capture every financial fact, including receipt sidecars when installed."""
    table_cursor = db_writer.connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND (name = 'events' OR name LIKE 'bank_%') "
        "ORDER BY name"
    )
    table_names = [str(row[0]) for row in table_cursor.fetchall()]
    snapshot: dict[str, tuple[tuple[object, ...], ...]] = {}
    for table_name in table_names:
        assert table_name == "events" or table_name.startswith("bank_")
        row_cursor = db_writer.connection.execute(f'SELECT * FROM "{table_name}"')
        rows = [tuple(row) for row in row_cursor.fetchall()]
        snapshot[table_name] = tuple(sorted(rows, key=repr))
    return snapshot


def _assert_operation_conflict(
    response: Response,
    error: str,
    winning_operation_id: str,
) -> None:
    payload = response.json()
    details = payload.get("details")
    assert (
        response.status_code,
        payload.get("error"),
        details.get("winning_operation_id") if isinstance(details, dict) else None,
    ) == (409, error, winning_operation_id)


INVALID_OPERATION_IDS = [
    pytest.param("missing", None, id="missing"),
    pytest.param("provided", "00000000-0000-4000-8000-000000000001", id="noncanonical"),
    pytest.param(
        "provided",
        "op-00000000-0000-4000-8000-00000000000A",
        id="uppercase",
    ),
    pytest.param("provided", "op-not-a-uuid", id="malformed"),
    pytest.param("provided", 7, id="wrong-type"),
]

UNKNOWN_FINANCIAL_FIELDS = [
    pytest.param("account_issuance", "arbitrary_unknown", "unexpected", id="issuance-arbitrary"),
    pytest.param("credit", "arbitrary_unknown", "unexpected", id="credit-arbitrary"),
    pytest.param("escrow_lock", "arbitrary_unknown", "unexpected", id="lock-arbitrary"),
    pytest.param("escrow_release", "arbitrary_unknown", "unexpected", id="release-arbitrary"),
    pytest.param("escrow_split", "arbitrary_unknown", "unexpected", id="split-arbitrary"),
    pytest.param("escrow_release", "constraints", {}, id="release-constraints"),
    pytest.param("escrow_split", "constraints", {}, id="split-constraints"),
    pytest.param("escrow_split", "worker_amount", 20, id="split-worker-amount"),
    pytest.param("escrow_split", "poster_amount", 20, id="split-poster-amount"),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "nested_field",
    ["operation_id", "override_amount", "candidate_reference"],
)
def test_initial_credit_rejects_unknown_nested_fields_without_mutation(
    receipt_router: TestClient,
    db_writer: DbWriter,
    nested_field: str,
) -> None:
    account_id = f"a-issuance-{uuid4()}"
    _register_agent(receipt_router, account_id)
    body = {
        "operation_id": f"op-{uuid4()}",
        "account_id": account_id,
        "balance": 25,
        "created_at": "2026-10-16T08:01:00Z",
        "initial_credit": {
            "tx_id": f"tx-{uuid4()}",
            "amount": 25,
            "reference": "initial_balance",
            "timestamp": "2026-10-16T08:01:00Z",
            nested_field: "ignored-value",
        },
        "event": make_event(source="bank", event_type="account.created"),
    }
    before = _financial_snapshot(db_writer)

    response = receipt_router.post("/bank/accounts", json=body)

    assert (
        response.status_code,
        response.json().get("error"),
        response.json().get("details", {}).get("fields"),
        _financial_snapshot(db_writer),
    ) == (400, "unknown_field", [f"initial_credit.{nested_field}"], before)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("operation_kind", "unknown_field", "unknown_value"),
    UNKNOWN_FINANCIAL_FIELDS,
)
def test_every_positive_command_rejects_unknown_fields_without_mutation(
    receipt_router: TestClient,
    db_writer: DbWriter,
    operation_kind: str,
    unknown_field: str,
    unknown_value: object,
) -> None:
    path, body = _command_for_identity_validation(
        receipt_router,
        operation_kind,
        f"op-{uuid4()}",
    )
    body[unknown_field] = unknown_value
    before = _financial_snapshot(db_writer)

    response = receipt_router.post(path, json=body)

    assert (
        response.status_code,
        response.json().get("error"),
        response.json().get("details", {}).get("fields"),
        _financial_snapshot(db_writer),
    ) == (400, "unknown_field", [unknown_field], before)


@pytest.mark.unit
@pytest.mark.parametrize(
    "operation_kind",
    ["account_issuance", "credit", "escrow_lock", "escrow_release", "escrow_split"],
)
@pytest.mark.parametrize(("presence", "operation_id"), INVALID_OPERATION_IDS)
def test_positive_financial_commands_require_canonical_operation_identity(
    receipt_router: TestClient,
    db_writer: DbWriter,
    operation_kind: str,
    presence: str,
    operation_id: object,
) -> None:
    """Every positive monetary command rejects absent or noncanonical identities."""
    path, body = _command_for_identity_validation(receipt_router, operation_kind, operation_id)
    if presence == "missing":
        del body["operation_id"]

    before = _financial_snapshot(db_writer)
    response = receipt_router.post(path, json=body)
    after = _financial_snapshot(db_writer)
    payload = response.json()
    expected_error = "missing_field" if presence == "missing" else "invalid_operation_id"
    details = payload.get("details")
    assert (
        response.status_code,
        payload.get("error"),
        details.get("field") if isinstance(details, dict) else None,
        after,
    ) == (400, expected_error, "operation_id", before)


@pytest.mark.unit
def test_operation_identity_is_global_across_financial_kinds(
    receipt_router: TestClient,
    db_writer: DbWriter,
) -> None:
    payer_id = f"a-payer-{uuid4()}"
    _register_funded_account(receipt_router, payer_id, 50, f"op-{uuid4()}")
    operation_id = f"op-{uuid4()}"
    credit = receipt_router.post(
        "/bank/credit",
        json=_credit_body(payer_id, 10, "global-operation-owner", operation_id),
    )
    assert credit.status_code == 200

    before = _financial_snapshot(db_writer)
    lock = receipt_router.post(
        "/bank/escrow/lock",
        json=_lock_body(payer_id, f"t-{uuid4()}", f"esc-{uuid4()}", 20, operation_id),
    )
    after = _financial_snapshot(db_writer)
    payload = lock.json()
    details = payload.get("details")
    assert (
        lock.status_code,
        payload.get("error"),
        details.get("winning_operation_id") if isinstance(details, dict) else None,
        _account_balance(db_writer, payer_id),
        _count_rows(db_writer, payer_id, "escrow_lock"),
        after,
    ) == (409, "operation_id_conflict", operation_id, 60, 0, before)


@pytest.mark.unit
def test_same_release_identity_with_changed_recipient_conflicts_with_winner(
    receipt_router: TestClient,
    db_writer: DbWriter,
) -> None:
    suffix = str(uuid4())
    payer_id = f"a-payer-{suffix}"
    first_recipient = f"a-first-{suffix}"
    changed_recipient = f"a-changed-{suffix}"
    _register_funded_account(receipt_router, payer_id, 50, f"op-{uuid4()}")
    _register_zero_account(receipt_router, first_recipient)
    _register_zero_account(receipt_router, changed_recipient)
    escrow_id = f"esc-{suffix}"
    _lock_for_setup(
        receipt_router,
        payer_id,
        f"t-{suffix}",
        escrow_id,
        50,
        f"op-{uuid4()}",
    )
    operation_id = f"op-{uuid4()}"
    first = receipt_router.post(
        "/bank/escrow/release",
        json=_release_body(escrow_id, first_recipient, f"release-{suffix}", operation_id),
    )
    assert first.status_code == 200

    before = _financial_snapshot(db_writer)
    changed = receipt_router.post(
        "/bank/escrow/release",
        json=_release_body(escrow_id, changed_recipient, f"release-{suffix}", operation_id),
    )
    after = _financial_snapshot(db_writer)
    _assert_operation_conflict(changed, "operation_id_conflict", operation_id)
    assert (
        _account_balance(db_writer, first_recipient),
        _account_balance(db_writer, changed_recipient),
    ) == (50, 0)
    assert after == before


@pytest.mark.unit
def test_same_split_identity_with_changed_percentage_conflicts_with_winner(
    receipt_router: TestClient,
    db_writer: DbWriter,
) -> None:
    """Reject percentage changes once the new worker_pct command shape is admitted.

    The pre-MP04 Gateway rejects the first command because it still requires retired
    caller-supplied amounts, so its identity layer is not observable until that shape changes.
    """
    suffix = str(uuid4())
    payer_id = f"a-payer-{suffix}"
    worker_id = f"a-worker-{suffix}"
    _register_funded_account(receipt_router, payer_id, 100, f"op-{uuid4()}")
    _register_zero_account(receipt_router, worker_id)
    escrow_id = f"esc-{suffix}"
    _lock_for_setup(
        receipt_router,
        payer_id,
        f"t-{suffix}",
        escrow_id,
        100,
        f"op-{uuid4()}",
    )
    operation_id = f"op-{uuid4()}"
    first = receipt_router.post(
        "/bank/escrow/split",
        json=_split_body(escrow_id, worker_id, payer_id, 60, f"split-{suffix}", operation_id),
    )
    assert first.status_code == 200

    before = _financial_snapshot(db_writer)
    changed = receipt_router.post(
        "/bank/escrow/split",
        json=_split_body(escrow_id, worker_id, payer_id, 40, f"split-{suffix}", operation_id),
    )
    after = _financial_snapshot(db_writer)
    _assert_operation_conflict(changed, "operation_id_conflict", operation_id)
    assert (
        _account_balance(db_writer, worker_id),
        _account_balance(db_writer, payer_id),
    ) == (60, 40)
    assert after == before


@pytest.mark.unit
def test_different_identity_for_same_natural_credit_reports_true_winner(
    receipt_router: TestClient,
    db_writer: DbWriter,
) -> None:
    account_id = f"a-credit-{uuid4()}"
    _register_zero_account(receipt_router, account_id)
    winning_operation_id = f"op-{uuid4()}"
    first = receipt_router.post(
        "/bank/credit",
        json=_credit_body(account_id, 10, "one-natural-credit", winning_operation_id),
    )
    assert first.status_code == 200

    losing_operation_id = f"op-{uuid4()}"
    before = _financial_snapshot(db_writer)
    losing = receipt_router.post(
        "/bank/credit",
        json=_credit_body(account_id, 10, "one-natural-credit", losing_operation_id),
    )
    after = _financial_snapshot(db_writer)
    _assert_operation_conflict(
        losing,
        "financial_operation_already_committed",
        winning_operation_id,
    )
    assert (
        _account_balance(db_writer, account_id),
        _count_rows(db_writer, account_id, "credit"),
    ) == (10, 1)
    assert after == before


@pytest.mark.unit
def test_different_identity_for_same_natural_lock_reports_true_winner(
    receipt_router: TestClient,
    db_writer: DbWriter,
) -> None:
    suffix = str(uuid4())
    payer_id = f"a-payer-{suffix}"
    _register_funded_account(receipt_router, payer_id, 100, f"op-{uuid4()}")
    task_id = f"t-{suffix}"
    winning_operation_id = f"op-{uuid4()}"
    first = receipt_router.post(
        "/bank/escrow/lock",
        json=_lock_body(payer_id, task_id, f"esc-first-{suffix}", 20, winning_operation_id),
    )
    assert first.status_code == 201

    losing_operation_id = f"op-{uuid4()}"
    before = _financial_snapshot(db_writer)
    losing = receipt_router.post(
        "/bank/escrow/lock",
        json=_lock_body(payer_id, task_id, f"esc-losing-{suffix}", 20, losing_operation_id),
    )
    after = _financial_snapshot(db_writer)
    _assert_operation_conflict(
        losing,
        "financial_operation_already_committed",
        winning_operation_id,
    )
    assert (
        _account_balance(db_writer, payer_id),
        _count_rows(db_writer, payer_id, "escrow_lock"),
    ) == (80, 1)
    assert after == before


@pytest.mark.unit
def test_exact_credit_replay_keeps_original_receipt_after_later_credit(
    receipt_router: TestClient,
    db_writer: DbWriter,
) -> None:
    account_id = f"a-credit-{uuid4()}"
    _register_zero_account(receipt_router, account_id)
    operation_id = f"op-{uuid4()}"
    original_body = _credit_body(account_id, 10, "first-credit", operation_id)
    original = receipt_router.post("/bank/credit", json=original_body)
    assert original.status_code == 200

    intervening = receipt_router.post(
        "/bank/credit",
        json=_credit_body(account_id, 5, "intervening-credit", f"op-{uuid4()}"),
    )
    assert intervening.status_code == 200
    before_replay = _financial_snapshot(db_writer)
    replay = receipt_router.post("/bank/credit", json=original_body)
    after_replay = _financial_snapshot(db_writer)
    assert replay.status_code == 200

    original_envelope = original.json()
    replay_envelope = replay.json()
    assert original_envelope.get("outcome") == "native_receipt"
    assert replay_envelope.get("outcome") == "native_receipt"
    assert (
        original_envelope["receipt"]["operation_id"],
        original_envelope["receipt"]["operation_kind"],
        original_envelope["receipt"]["source_reference"],
    ) == (operation_id, "credit", "first-credit")
    assert replay_envelope["receipt"] == original_envelope["receipt"]
    assert replay_envelope["receipt"]["result"]["balance_after"] == 10
    assert replay_envelope["current_escrow"] is None
    assert _account_balance(db_writer, account_id) == 15
    assert _count_rows(db_writer, account_id, "credit") == 2
    assert after_replay == before_replay
