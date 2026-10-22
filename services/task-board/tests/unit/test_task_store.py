"""Task storage outcomes through production Gateway persistence and commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from db_gateway_service.board_authority import BoardAuthority
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from service_auth.financial_identity import operation_id_for
from service_auth.signing import public_key_to_b64
from service_commons.exceptions import ServiceError

from tests.fakes.gateway_task_store import GatewayTaskStore
from tests.fakes.native_task_fixture import TaskTerms, publish_open_task

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit

_AT = "2026-10-18T12:00:00Z"
_NOW_US = parse_utc_text_to_epoch_us(_AT)
_TASK_1 = "t-00000000-0000-4000-8000-000000000101"
_TASK_2 = "t-00000000-0000-4000-8000-000000000102"


def _new_public_key() -> str:
    return f"ed25519:{public_key_to_b64(Ed25519PrivateKey.generate().public_key())}"


def _event(source: str, event_type: str, task_id: str | None) -> dict[str, Any]:
    return {
        "event_source": source,
        "event_type": event_type,
        "timestamp": _AT,
        "task_id": task_id,
        "summary": event_type,
        "payload": "{}",
    }


@pytest.fixture
def store(tmp_path: Path) -> Iterator[GatewayTaskStore]:
    """Own one v4 database and explicitly provision fixture identities and funds."""
    platform_public_key = _new_public_key()
    adapter = GatewayTaskStore(
        db_path=tmp_path / "task-board.db",
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V4),
        caller_kid="gateway-task-board",
        authority=BoardAuthority(
            platform_public_key=platform_public_key,
            deadline_actor_id="system-task-store-test-deadline",
        ),
        clock_us=lambda: _NOW_US,
    )
    try:
        for agent_id, public_key in (
            ("a-platform", platform_public_key),
            ("a-poster", _new_public_key()),
            ("a-worker-1", _new_public_key()),
        ):
            adapter.writer.register_agent(
                {
                    "agent_id": agent_id,
                    "name": agent_id,
                    "public_key": public_key,
                    "registered_at": _AT,
                    "event": _event("identity", "agent.registered", None),
                }
            )
        adapter.writer.create_account(
            {
                "operation_id": operation_id_for("test-task-store", ("fund-poster",)),
                "account_id": "a-poster",
                "balance": 200,
                "created_at": _AT,
                "initial_credit": {
                    "tx_id": "tx-fund-poster",
                    "amount": 200,
                    "reference": "initial_balance",
                    "timestamp": _AT,
                },
                "event": _event("bank", "account.created", None),
            }
        )
        adapter.writer.create_account(
            {
                "account_id": "a-worker-1",
                "balance": 0,
                "created_at": _AT,
                "event": _event("bank", "account.created", None),
            }
        )
        yield adapter
    finally:
        adapter.close()


def _create_task(store: GatewayTaskStore, task_id: str) -> None:
    publish_open_task(
        store,
        task_id=task_id,
        poster_id="a-poster",
        title=f"Task {task_id}",
        spec="Specification",
        terms=TaskTerms(100, 3600, 600, 7200, 1800),
        timestamp=_AT,
        event=_event("central-bank", "escrow.locked", task_id),
    )


def _command(
    *,
    task_id: str,
    operation_id: str,
    command: str,
    status: str,
    version: int,
    actor_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": task_id,
        "command": command,
        "expected_status": status,
        "expected_lifecycle_version": version,
        "actor_kind": "agent",
        "actor_id": actor_id,
        "arguments": arguments,
    }


def _bid_command(task_id: str, bid_id: str, amount: int) -> dict[str, Any]:
    return _command(
        task_id=task_id,
        operation_id=operation_id_for("test-task-store", (task_id, "submit-bid", bid_id)),
        command="submit_bid",
        status="open",
        version=0,
        actor_id="a-worker-1",
        arguments={"bid_id": bid_id, "bidder_id": "a-worker-1", "amount": amount},
    )


def _accept_command(task_id: str, bid_id: str, version: int) -> dict[str, Any]:
    return _command(
        task_id=task_id,
        operation_id=operation_id_for("test-task-store", (task_id, "accept", str(version))),
        command="accept_bid",
        status="open",
        version=version,
        actor_id="a-poster",
        arguments={
            "bid_id": bid_id,
            "worker_id": "a-worker-1",
            "escrow_id": f"esc-{task_id}",
            "reward": 100,
            "bid_amount": 90,
        },
    )


def _accept_task(store: GatewayTaskStore, task_id: str, bid_id: str) -> None:
    bid = store.claim_task_operation(_bid_command(task_id, bid_id, 90))
    assert bid["operation_state"] == "completed"
    accepted = store.claim_task_operation(_accept_command(task_id, bid_id, 0))
    assert accepted["operation_state"] == "completed"
    assert accepted["target_status"] == "accepted"
    assert accepted["target_lifecycle_version"] == 1


def _asset_data(task_id: str) -> dict[str, Any]:
    return {
        "asset_id": "asset-1",
        "task_id": task_id,
        "uploader_id": "a-worker-1",
        "filename": "result.txt",
        "content_type": "text/plain",
        "size_bytes": 12,
        "storage_path": f"{task_id}/asset-1/result.txt",
        "content_hash": "a" * 64,
        "uploaded_at": _AT,
    }


def test_task_crud_and_counts(store: GatewayTaskStore) -> None:
    """Native transitions preserve task get/list/count and reject wrong source state."""
    _create_task(store, _TASK_1)
    _create_task(store, _TASK_2)
    _accept_task(store, _TASK_2, "bid-task-2")

    task = store.get_task(_TASK_1)
    assert task is not None
    assert task["task_id"] == _TASK_1
    assert task["status"] == "open"

    _accept_task(store, _TASK_1, "bid-task-1")
    store.insert_asset(_asset_data(_TASK_1))
    changed = store.claim_task_operation(
        _command(
            task_id=_TASK_1,
            operation_id=operation_id_for("test-task-store", (_TASK_1, "submit")),
            command="submit_deliverable",
            status="accepted",
            version=1,
            actor_id="a-worker-1",
            arguments={"worker_id": "a-worker-1"},
        )
    )
    assert changed["operation_state"] == "completed"
    assert changed["target_status"] == "submitted"
    assert changed["target_lifecycle_version"] == 2

    before = store.get_task(_TASK_2)
    before_events = store.writer.get_total_events()
    with pytest.raises(ServiceError) as mismatch:
        store.claim_task_operation(_accept_command(_TASK_2, "bid-task-2", 1))
    assert (mismatch.value.status_code, mismatch.value.error) == (409, "invalid_status")
    assert store.get_task(_TASK_2) == before
    assert store.writer.get_total_events() == before_events

    listed = store.list_tasks(status=None, poster_id=None, worker_id=None, limit=None, offset=None)
    assert len(listed) == 2
    assert {row["task_id"]: row["status"] for row in listed} == {
        _TASK_1: "submitted",
        _TASK_2: "accepted",
    }
    assert store.count_tasks() == 2
    grouped = store.count_tasks_by_status()
    assert grouped["submitted"] == 1
    assert grouped["accepted"] == 1


def test_bid_operations(store: GatewayTaskStore) -> None:
    """Native bid insert/get/list preserves one bid when the bidder submits a new intent."""
    _create_task(store, _TASK_1)
    store.claim_task_operation(_bid_command(_TASK_1, "bid-1", 90))

    task = store.get_task(_TASK_1)
    assert task is not None
    assert task["bid_count"] == 1
    bid = store.get_bid("bid-1", _TASK_1)
    assert bid is not None
    assert bid["bidder_id"] == "a-worker-1"
    bids = store.get_bids_for_task(_TASK_1)
    assert len(bids) == 1
    assert bids[0]["bid_id"] == "bid-1"

    before_events = store.writer.get_total_events()
    with pytest.raises(ServiceError) as duplicate:
        store.claim_task_operation(_bid_command(_TASK_1, "bid-2", 85))
    assert (duplicate.value.status_code, duplicate.value.error) == (409, "bid_exists")
    assert store.get_task(_TASK_1) == task
    assert store.get_bid("bid-1", _TASK_1) == bid
    assert store.get_bid("bid-2", _TASK_1) is None
    assert store.get_bids_for_task(_TASK_1) == bids
    assert store.writer.get_total_events() == before_events


def test_asset_operations(store: GatewayTaskStore) -> None:
    """Asset insert/get/list/count works for the accepted worker's task."""
    _create_task(store, _TASK_1)
    _accept_task(store, _TASK_1, "bid-1")
    store.insert_asset(_asset_data(_TASK_1))

    asset = store.get_asset("asset-1", _TASK_1)
    assert asset is not None
    assert asset["filename"] == "result.txt"
    assets = store.get_assets_for_task(_TASK_1)
    assert len(assets) == 1
    assert assets[0]["asset_id"] == "asset-1"
    assert store.count_assets(_TASK_1) == 1


def test_close_succeeds(store: GatewayTaskStore, tmp_path: Path) -> None:
    """Repeated close releases the connection and retains the fixture's database."""
    store.close()
    store.close()
    assert (tmp_path / "task-board.db").is_file()
    with pytest.raises(RuntimeError, match="closed"):
        store.count_tasks()
