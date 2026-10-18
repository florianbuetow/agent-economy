"""Gateway-backed test storage preserves production native command semantics."""

from __future__ import annotations

import importlib
import importlib.util
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, Any

import pytest
from db_gateway_service.board_authority import BoardAuthority
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from service_commons.exceptions import ServiceError

from task_board_service.services.task_db_client import TaskDbClient

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit

_AT = "2026-10-18T12:00:00Z"
_NOW_US = parse_utc_text_to_epoch_us(_AT)
_PLATFORM_KEY = "ed25519:AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="
_AUTHORITY = BoardAuthority(
    platform_public_key=_PLATFORM_KEY,
    deadline_actor_id="system-task-board-test-deadline",
)


def _adapter_class() -> Any:
    module_name = "tests.fakes.gateway_task_store"
    assert importlib.util.find_spec(module_name) is not None, (
        "Gateway-backed test storage is missing; the dictionary fake has no native commands"
    )
    return importlib.import_module(module_name).GatewayTaskStore


def _new_store(path: Path, schema_sql: str | None, caller_kid: str) -> Any:
    return _adapter_class()(
        db_path=path,
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=schema_sql,
        caller_kid=caller_kid,
        authority=_AUTHORITY,
        clock_us=lambda: _NOW_US,
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Any]:
    adapter = _new_store(
        tmp_path / "gateway.db", load_schema_sql(SchemaState.CURRENT_V3), "gateway-task-board"
    )
    try:
        yield adapter
    finally:
        adapter.close()


def _event(source: str, event_type: str) -> dict[str, Any]:
    return {
        "event_source": source,
        "event_type": event_type,
        "timestamp": _AT,
        "task_id": "t-fixture",
        "summary": event_type,
        "payload": "{}",
    }


def _seed_open(store: Any) -> None:
    """Persist explicit identity, funding, escrow, and native publication facts."""
    for agent_id, public_key in (
        ("a-poster", "ed25519:AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="),
        ("a-worker", "ed25519:AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM="),
        ("a-platform", _PLATFORM_KEY),
    ):
        store.writer.register_agent(
            {
                "agent_id": agent_id,
                "name": agent_id,
                "public_key": public_key,
                "registered_at": _AT,
                "event": _event("identity", "agent.registered"),
            }
        )
    store.writer.create_account(
        {
            "operation_id": "op-00000000-0000-4000-8000-000000000001",
            "account_id": "a-poster",
            "balance": 100,
            "created_at": _AT,
            "initial_credit": {
                "tx_id": "tx-funding",
                "amount": 100,
                "reference": "initial_balance",
                "timestamp": _AT,
            },
            "event": _event("bank", "account.created"),
        }
    )
    store.writer.create_account(
        {
            "account_id": "a-worker",
            "balance": 0,
            "created_at": _AT,
            "event": _event("bank", "account.created"),
        }
    )
    store.writer.escrow_lock(
        {
            "operation_id": "op-00000000-0000-4000-8000-000000000002",
            "escrow_id": "esc-fixture",
            "payer_account_id": "a-poster",
            "amount": 100,
            "task_id": "t-fixture",
            "created_at": _AT,
            "tx_id": "tx-lock",
            "event": _event("bank", "escrow.locked"),
        }
    )
    store.insert_task(
        {
            "task_id": "t-fixture",
            "poster_id": "a-poster",
            "title": "Explicit fixture",
            "spec": "Persist actual command effects",
            "reward": 100,
            "bidding_deadline_seconds": 60,
            "acceptance_window_seconds": 120,
            "deadline_seconds": 180,
            "review_deadline_seconds": 240,
            "status": "open",
            "escrow_id": "esc-fixture",
            "bid_count": 0,
            "escrow_pending": 0,
            "created_at": _AT,
        }
    )


def _command(
    operation_id: str,
    command: str,
    expected_status: str,
    version: int,
    actor: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": "t-fixture",
        "command": command,
        "expected_status": expected_status,
        "expected_lifecycle_version": version,
        "actor_kind": "agent",
        "actor_id": actor,
        "arguments": arguments,
    }


def _bid() -> dict[str, Any]:
    return _command(
        "op-00000000-0000-4000-8000-000000000003",
        "submit_bid",
        "open",
        0,
        "a-worker",
        {"bid_id": "bid-fixture", "bidder_id": "a-worker", "amount": 90},
    )


def _seed_submitted(store: Any) -> None:
    _seed_open(store)
    store.claim_task_operation(_bid())
    store.claim_task_operation(
        _command(
            "op-00000000-0000-4000-8000-000000000004",
            "accept_bid",
            "open",
            0,
            "a-poster",
            {
                "bid_id": "bid-fixture",
                "worker_id": "a-worker",
                "escrow_id": "esc-fixture",
                "reward": 100,
                "bid_amount": 90,
            },
        )
    )
    store.insert_asset(
        {
            "asset_id": "asset-fixture",
            "task_id": "t-fixture",
            "uploader_id": "a-worker",
            "filename": "answer.txt",
            "content_type": "text/plain",
            "size_bytes": 2,
            "storage_path": "t-fixture/asset-fixture/answer.txt",
            "content_hash": "a" * 64,
            "uploaded_at": _AT,
        }
    )
    store.claim_task_operation(
        _command(
            "op-00000000-0000-4000-8000-000000000005",
            "submit_deliverable",
            "accepted",
            1,
            "a-worker",
            {"worker_id": "a-worker"},
        )
    )


def _approval() -> dict[str, Any]:
    return _command(
        "op-00000000-0000-4000-8000-000000000006",
        "approve_task",
        "submitted",
        2,
        "a-poster",
        {
            "poster_id": "a-poster",
            "worker_id": "a-worker",
            "escrow_id": "esc-fixture",
            "reward": 100,
            "source_reference": "task:t-fixture:approved",
        },
    )


def test_empty_adapter_does_not_invent_fixture_facts(store: Any) -> None:
    assert store.count_tasks() == 0
    assert store.reader.list_agents(None) == []
    assert store.reader.count_accounts() == 0
    assert store.writer.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert isinstance(store, TaskDbClient)


def test_native_bid_replay_preserves_one_bid_and_event(store: Any) -> None:
    _seed_open(store)
    first = store.claim_task_operation(_bid())
    assert first["operation_state"] == "completed"
    assert store.claim_task_operation(_bid()) == first
    with pytest.raises(ServiceError) as caught:
        store.finalize_task_operation("op-00000000-0000-4000-8000-000000000003")
    assert (caught.value.status_code, caught.value.error) == (409, "operation_not_finalizable")
    assert store.get_task("t-fixture")["lifecycle_version"] == 0
    assert store.get_task("t-fixture")["bid_count"] == 1
    assert len(store.get_bids_for_task("t-fixture")) == 1
    assert (
        store.writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='bid.submitted'"
        ).fetchone()[0]
        == 1
    )


def test_native_finalization_requires_persisted_bank_evidence(store: Any) -> None:
    _seed_submitted(store)
    claimed = store.claim_task_operation(_approval())
    assert claimed["operation_state"] == "claimed"
    assert (
        store.finalize_task_operation("op-00000000-0000-4000-8000-000000000006")["operation_state"]
        == "claimed"
    )
    pending = store.get_task("t-fixture")
    assert pending["status"] == "submitted"
    assert pending["lifecycle_version"] == 2
    assert (
        pending["pending_transition"]["operation_id"] == "op-00000000-0000-4000-8000-000000000006"
    )
    assert pending["accepted_reward"] == 100
    assert pending["accepted_bid_amount"] == 90
    store.writer.escrow_release(
        {
            "operation_id": "op-00000000-0000-4000-8000-000000000006",
            "escrow_id": "esc-fixture",
            "recipient_account_id": "a-worker",
            "source_reference": "task:t-fixture:approved",
            "tx_id": "tx-release",
            "resolved_at": _AT,
            "event": _event("central-bank", "escrow.released"),
        }
    )
    completed = store.finalize_task_operation("op-00000000-0000-4000-8000-000000000006")
    assert completed["operation_state"] == "completed"
    assert store.claim_task_operation(_approval()) == completed
    assert store.finalize_task_operation("op-00000000-0000-4000-8000-000000000006") == completed
    current = store.get_task("t-fixture")
    assert (current["status"], current["lifecycle_version"], current["pending_transition"]) == (
        "approved",
        3,
        None,
    )
    assert (
        store.writer.connection.execute(
            "SELECT balance FROM bank_accounts WHERE account_id='a-worker'"
        ).fetchone()[0]
        == 100
    )
    assert (
        store.writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='task.approved'"
        ).fetchone()[0]
        == 1
    )


def test_changed_command_identity_is_rejected_without_new_bid(store: Any) -> None:
    _seed_open(store)
    store.claim_task_operation(_bid())
    changed = _bid()
    changed["arguments"]["amount"] = 89
    with pytest.raises(ServiceError) as caught:
        store.claim_task_operation(changed)
    assert (caught.value.status_code, caught.value.error) == (409, "operation_id_conflict")
    assert store.get_bid("bid-fixture", "t-fixture")["amount"] == 90


def test_court_finalization_requires_the_persisted_matching_claim(store: Any) -> None:
    _seed_submitted(store)
    command = _command(
        "op-00000000-0000-4000-8000-000000000007",
        "dispute_task",
        "submitted",
        2,
        "a-poster",
        {
            "poster_id": "a-poster",
            "worker_id": "a-worker",
            "escrow_id": "esc-fixture",
            "reward": 100,
            "reason": "The deliverable is incomplete",
        },
    )
    claimed = store.claim_task_operation(command)
    assert claimed["effect_kind"] == "court_claim"
    assert store.finalize_task_operation(command["operation_id"])["operation_state"] == "claimed"
    assert store.get_task("t-fixture")["status"] == "submitted"
    store.writer.file_claim(
        {
            "claim_id": "clm-fixture",
            "task_id": "t-fixture",
            "claimant_id": "a-poster",
            "respondent_id": "a-worker",
            "reason": "The deliverable is incomplete",
            "status": "rebuttal_pending",
            "rebuttal_deadline": "2026-10-18T13:00:00Z",
            "filed_at": _AT,
            "event": _event("court", "claim.filed"),
        }
    )
    completed = store.finalize_task_operation(command["operation_id"])
    assert completed["operation_state"] == "completed"
    assert completed["effect_result_reference"] == "clm-fixture"
    assert store.claim_task_operation(command) == completed
    assert store.get_task("t-fixture")["dispute_id"] == "clm-fixture"
    assert store.get_task("t-fixture")["status"] == "disputed"


def test_task_asset_and_bid_reads_share_real_projection_and_counts(store: Any) -> None:
    _seed_submitted(store)
    task = store.get_task("t-fixture")
    assert store.list_tasks("submitted", "a-poster", "a-worker", 1, 0) == [task]
    assert store.list_tasks("open", None, None, None, None) == []
    assert store.count_tasks_by_status() == {"submitted": 1}
    assert store.get_task("t-missing") is None
    assert store.get_bid("bid-missing", "t-fixture") is None
    assert store.get_asset("asset-missing", "t-fixture") is None
    assert store.get_bid("bid-fixture", "t-fixture")["amount"] == 90
    assets = store.get_assets_for_task("t-fixture")
    assert len(assets) == store.count_assets("t-fixture") == 1
    assert assets[0] == store.get_asset("asset-fixture", "t-fixture")
    assert assets[0]["content_hash"] == "a" * 64


@pytest.mark.parametrize("operation_id", ["", "wrong-prefix", True, "op-has space"])
def test_malformed_operation_id_never_reaches_storage(store: Any, operation_id: object) -> None:
    payload = _bid()
    payload["operation_id"] = operation_id
    with pytest.raises(ServiceError) as caught:
        store.claim_task_operation(payload)
    assert caught.value.status_code == 400
    assert (
        store.writer.connection.execute("SELECT COUNT(*) FROM board_task_operations").fetchone()[0]
        == 0
    )


def test_missing_finalize_identity_is_typed_not_silent(store: Any) -> None:
    with pytest.raises(ServiceError) as caught:
        store.finalize_task_operation("op-00000000-0000-4000-8000-000000000099")
    assert (caught.value.status_code, caught.value.error) == (404, "operation_not_found")


def test_legacy_status_writer_cannot_steal_native_claim(store: Any) -> None:
    _seed_submitted(store)
    store.claim_task_operation(_approval())
    before = store.get_task("t-fixture")
    assert store.update_task("t-fixture", {"status": "approved"}, expected_status="submitted") == 0
    assert store.get_task("t-fixture") == before


def test_missing_contract_is_reported_without_filling_defaults(store: Any) -> None:
    _seed_open(store)
    # Deliberate corruption, not a fixture-history shortcut.
    store.writer.connection.execute("DELETE FROM board_task_contracts WHERE task_id='t-fixture'")
    store.writer.connection.commit()
    with pytest.raises(ServiceError) as caught:
        store.get_task("t-fixture")
    assert (caught.value.status_code, caught.value.error) == (500, "task_contract_missing")


def test_malformed_projection_still_uses_real_client_validation(
    store: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_open(store)
    malformed = store.reader.get_task("t-fixture")
    del malformed["lifecycle_version"]

    def _malformed_projection(_task_id: str) -> Any:
        return malformed

    monkeypatch.setattr(store.reader, "get_task", _malformed_projection)
    with pytest.raises(ServiceError) as caught:
        store.get_task("t-fixture")
    assert (caught.value.status_code, caught.value.error) == (502, "invalid_gateway_response")


def test_close_reopen_and_other_caller_preserve_owned_database(tmp_path: Path) -> None:
    path = tmp_path / "persistent.db"
    first = _new_store(path, load_schema_sql(SchemaState.CURRENT_V3), "gateway-task-board")
    try:
        _seed_open(first)
        result = first.claim_task_operation(_bid())
    finally:
        first.close()
    first.close()
    assert path.is_file()
    with pytest.raises(RuntimeError, match="closed"):
        first.count_tasks()
    reopened = _new_store(path, None, "gateway-task-board")
    other = _new_store(path, None, "gateway-other-board")
    try:
        assert reopened.claim_task_operation(_bid()) == result
        with pytest.raises(ServiceError) as caught:
            other.claim_task_operation(_bid())
        assert caught.value.status_code == 403
        other.close()
        assert reopened.count_tasks() == 1
    finally:
        other.close()
        reopened.close()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM board_bids").fetchone()[0] == 1


def test_explicit_clock_controls_native_cutoff(tmp_path: Path) -> None:
    clock = [_NOW_US]
    adapter = _adapter_class()(
        db_path=tmp_path / "clock.db",
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V3),
        caller_kid="gateway-task-board",
        authority=_AUTHORITY,
        clock_us=lambda: clock[0],
    )
    try:
        _seed_open(adapter)
        clock[0] = _NOW_US + 60_000_000
        with pytest.raises(ServiceError) as caught:
            adapter.claim_task_operation(_bid())
        assert (caught.value.status_code, caught.value.error) == (409, "cutoff_reached")
        assert adapter.get_bids_for_task("t-fixture") == []
    finally:
        adapter.close()


def test_constructor_failure_releases_its_connection_without_deleting_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter_class = _adapter_class()
    module = importlib.import_module(adapter_class.__module__)
    real_writer = module.DbWriter
    captured: list[Any] = []

    def _capture_writer(*args: Any, **kwargs: Any) -> Any:
        writer = real_writer(*args, **kwargs)
        captured.append(writer)
        return writer

    def _fail_initialization(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("injected operation writer initialization failure")

    monkeypatch.setattr(module, "DbWriter", _capture_writer)
    monkeypatch.setattr(module, "TaskOperationWriter", _fail_initialization)
    path = tmp_path / "failed-initialization.db"
    try:
        with pytest.raises(RuntimeError, match="injected operation writer"):
            _new_store(path, load_schema_sql(SchemaState.CURRENT_V3), "gateway-task-board")
        assert len(captured) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            captured[0].connection.execute("SELECT 1")
        assert path.is_file()
    finally:
        for writer in captured:
            writer.close()
