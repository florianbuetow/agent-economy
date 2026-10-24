"""Build authentic V5 financial histories for UI MP-08 acceptance tests."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair

if TYPE_CHECKING:
    from collections.abc import Generator

_GATEWAY_ROOT = Path.cwd()
if str(_GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(_GATEWAY_ROOT))

migration: Any = import_module("db_gateway_service.migration")
recovery: Any = import_module("db_gateway_service.recovery")
clear_settings_cache: Any = import_module("db_gateway_service.config").clear_settings_cache
init_app_state: Any = import_module("db_gateway_service.core.state").init_app_state
DbReader: Any = import_module("db_gateway_service.services.db_reader").DbReader
DbWriter: Any = import_module("db_gateway_service.services.db_writer").DbWriter
write_gateway_config: Any = import_module("tests.fakes.gateway_mp08_config").write_gateway_config
_effects: Any = import_module("tests.fakes.gateway_mp08_effects")
prepare_legacy_v5: Any = _effects.prepare_legacy_v5
prepare_native_pending: Any = _effects.prepare_native_pending
prepare_native_refund_pending: Any = _effects.prepare_native_refund_pending
prepare_native_split_pending: Any = _effects.prepare_native_split_pending
_legacy_history: Any = import_module("tests.fakes.gateway_mp08_legacy_history")
prepare_historical_approved_worker_payment_v4: Any = (
    _legacy_history.prepare_historical_approved_worker_payment_v4
)
prepare_legacy_court_claim_v4: Any = _legacy_history.prepare_legacy_court_claim_v4
_native_history: Any = import_module("tests.fakes.native_board_history")
TaskGraph: Any = _native_history.TaskGraph
BusinessClock: Any = _native_history.BusinessClock
FIXED_NOW_US: Any = _native_history.FIXED_NOW_US
create_task: Any = _native_history.create_task
finalize: Any = _native_history.finalize
register_worker: Any = _native_history.register_worker
settle_release: Any = _native_history.settle_release
settle_split: Any = _native_history.settle_split

HistoryKind = Literal[
    "legacy_pending",
    "legacy_succeeded",
    "historical_paid",
    "court_only",
    "native_pending",
    "receipt_projection_pending",
    "completed_refund",
    "completed_split",
    "source_free_approved",
    "source_free_ruled",
    "conflict_wrong_receipt",
    "conflict_competing_winner",
    "conflict_resolved_without_receipt",
    "conflict_confirmed_projection",
]


class _DomainAuth(httpx.Auth):
    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        domain = request.url.path.split("/", maxsplit=2)[1]
        signer = self._signers[domain]
        protected_delivery = request.url.path.startswith(
            "/board/task-effect-deliveries"
        ) or request.url.path.startswith("/board/legacy-effect-observations/")
        access = "write" if protected_delivery or request.method != "GET" else "read"
        yield signer.sign_request(request, action=f"gateway.{domain}.{access}")


def _migrate_v4(source: Any, *, backup_id: str) -> None:
    recovery.create_backup(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
    )
    migrate = getattr(migration, "migrate_v4_to_v5", None)
    assert callable(migrate), "Missing explicit backup-bound V4-to-V5 migration"
    migrate(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )


def _cutoff_iso(value: object) -> str | None:
    if value is None:
        return None
    assert type(value) is int
    seconds, microseconds = divmod(value, 1_000_000)
    return (
        datetime.fromtimestamp(seconds, UTC)
        .replace(microsecond=microseconds)
        .isoformat(timespec="auto")
        .replace("+00:00", "Z")
    )


def _board_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Capture a Board-shaped record from the Gateway's actual projection."""
    value = dict(row)
    value["bidding_deadline"] = _cutoff_iso(row["bidding_cutoff_us"])
    value["acceptance_deadline"] = _cutoff_iso(row["acceptance_cutoff_us"])
    value["execution_deadline"] = _cutoff_iso(row["execution_cutoff_us"])
    value["review_deadline"] = _cutoff_iso(row["review_cutoff_us"])
    value["escrow_pending"] = bool(row["escrow_pending"])
    return value


def _write_board_sidecar(database: Path, record: dict[str, Any]) -> None:
    database.with_suffix(".board-projections.json").write_text(json.dumps([record], sort_keys=True))


def _legacy(root: Path, kind: HistoryKind) -> dict[str, object]:
    if kind in {"legacy_pending", "legacy_succeeded"}:
        source, writer = prepare_legacy_v5(
            root,
            kind="pending" if kind == "legacy_pending" else "succeeded",
        )
        record = DbReader(writer.connection).get_task(source.task_id)
        assert record is not None
        _write_board_sidecar(source.database, _board_projection(record))
        writer.close()
        return {
            "database": str(source.database),
            "task_id": source.task_id,
            "operation_id": source.operation_id,
        }
    if kind == "historical_paid":
        source = prepare_historical_approved_worker_payment_v4(
            root,
            caller_kid="gateway-task-board",
        )
    else:
        source = prepare_legacy_court_claim_v4(
            root,
            caller_kid="gateway-task-board",
        )
    _migrate_v4(source, backup_id=f"before-v5-{kind}")
    connection = sqlite3.connect(str(source.database))
    connection.row_factory = sqlite3.Row
    try:
        record = DbReader(connection).get_task(source.task_id)
        assert record is not None
        _write_board_sidecar(source.database, _board_projection(record))
    finally:
        connection.close()
    return {
        "database": str(source.database),
        "task_id": source.task_id,
        "operation_id": None,
    }


def _signers(config_dir: Path) -> dict[str, GatewayRequestSigner]:
    handles = {
        "identity": "gateway-identity",
        "bank": "gateway-central-bank",
        "board": "gateway-task-board",
        "reputation": "gateway-reputation",
        "court": "gateway-court",
    }
    result: dict[str, GatewayRequestSigner] = {}
    for domain, handle in handles.items():
        generate_keypair(handle, config_dir)
        result[domain] = GatewayRequestSigner(
            GatewaySignerConfig(
                kid=handle,
                private_key_path=Path(f"{handle}.key"),
                audience="db-gateway",
                token_ttl_seconds=30,
            ),
            config_dir=config_dir,
        )
    return result


def _native_client(root: Path) -> tuple[TestClient, Any, Path]:
    source, initial_writer = prepare_legacy_v5(root / "seed", kind="pending")
    initial_writer.close()
    task_time: Any = import_module("db_gateway_service.task_time")
    task_time.utc_now_us = BusinessClock(now_us=FIXED_NOW_US)
    config_dir = root / "gateway-config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"
    signers = _signers(config_dir)
    write_gateway_config(str(source.database), config_path)
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    create_app: Any = import_module("db_gateway_service.app").create_app
    app = create_app()
    client = TestClient(app)
    client.auth = _DomainAuth(signers)
    writer = DbWriter(
        str(source.database),
        5000,
        "wal",
        None,
        current_task_board_caller_kid="gateway-task-board",  # type: ignore[call-arg]
    )
    state = init_app_state()
    state.db_writer = writer
    state.db_reader = DbReader(writer.connection)
    return client, writer, source.database


def _finalize(client: TestClient, operation_id: str, expected_status: int) -> None:
    response = finalize(client, operation_id)
    assert response.status_code == expected_status, response.text


def _source_free(client: TestClient, writer: Any, kind: HistoryKind) -> tuple[str, None]:
    graph = create_task(client)
    worker_id = register_worker(client)
    status = "approved" if kind == "source_free_approved" else "ruled"
    writer.connection.execute(
        "UPDATE board_tasks SET status=?,worker_id=? WHERE task_id=?",
        (status, worker_id, graph.task_id),
    )
    writer.connection.commit()
    return str(graph.task_id), None


def _completed_refund(client: TestClient) -> tuple[str, str]:
    pending = prepare_native_refund_pending(client)
    operation_id = str(pending.operation_id)
    receipt = settle_release(
        client,
        TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id),
        operation_id=operation_id,
        recipient_id=pending.poster_id,
        source_reference=pending.source_reference,
    )
    assert receipt.status_code in {200, 201}, receipt.text
    _finalize(client, operation_id, 200)
    return str(pending.task_id), operation_id


def _completed_split(client: TestClient) -> tuple[str, str]:
    pending = prepare_native_split_pending(client)
    operation_id = str(pending.operation_id)
    receipt = settle_split(
        client,
        TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id),
        operation_id=operation_id,
        worker_id=pending.worker_id,
        worker_pct=50,
        source_reference=pending.source_reference,
    )
    assert receipt.status_code in {200, 201}, receipt.text
    _finalize(client, operation_id, 200)
    return str(pending.task_id), operation_id


def _approval_case(client: TestClient, writer: Any, kind: HistoryKind) -> tuple[str, str]:
    pending = prepare_native_pending(client)
    operation_id = str(pending.operation_id)
    graph = TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id)
    if kind == "receipt_projection_pending":
        receipt = settle_release(
            client,
            graph,
            operation_id=operation_id,
            recipient_id=pending.worker_id,
            source_reference=pending.source_reference,
        )
        assert receipt.status_code in {200, 201}, receipt.text
    elif kind == "conflict_wrong_receipt":
        receipt = settle_release(
            client,
            graph,
            operation_id=operation_id,
            recipient_id=pending.poster_id,
            source_reference=pending.source_reference,
        )
        assert receipt.status_code in {200, 201}, receipt.text
        _finalize(client, operation_id, 409)
    elif kind == "conflict_competing_winner":
        receipt = settle_release(
            client,
            graph,
            operation_id=f"op-{uuid4()}",
            recipient_id=pending.poster_id,
            source_reference=f"task:{pending.task_id}:cancelled",
        )
        assert receipt.status_code in {200, 201}, receipt.text
        _finalize(client, operation_id, 409)
    elif kind == "conflict_resolved_without_receipt":
        writer.connection.execute(
            "UPDATE bank_escrow SET status='released',resolved_at=? WHERE escrow_id=?",
            ("2099-01-01T12:00:02Z", pending.escrow_id),
        )
        writer.connection.commit()
        _finalize(client, operation_id, 409)
    elif kind == "conflict_confirmed_projection":
        receipt = settle_release(
            client,
            graph,
            operation_id=operation_id,
            recipient_id=pending.worker_id,
            source_reference=pending.source_reference,
        )
        assert receipt.status_code in {200, 201}, receipt.text
        writer.connection.execute(
            "UPDATE board_tasks SET status='cancelled' WHERE task_id=?",
            (pending.task_id,),
        )
        writer.connection.commit()
        _finalize(client, operation_id, 409)
    else:
        assert kind == "native_pending"
    return str(pending.task_id), operation_id


def _native(root: Path, kind: HistoryKind) -> dict[str, object]:
    client, writer, database = _native_client(root)
    try:
        if kind in {"source_free_approved", "source_free_ruled"}:
            task_id, operation_id = _source_free(client, writer, kind)
        elif kind == "completed_refund":
            task_id, operation_id = _completed_refund(client)
        elif kind == "completed_split":
            task_id, operation_id = _completed_split(client)
        else:
            task_id, operation_id = _approval_case(client, writer, kind)
        record = DbReader(writer.connection).get_task(task_id)
        assert record is not None
        _write_board_sidecar(database, _board_projection(record))
        return {
            "database": str(database),
            "task_id": task_id,
            "operation_id": operation_id,
        }
    finally:
        client.close()
        writer.close()
        os.environ.pop("CONFIG_PATH", None)
        clear_settings_cache()


def build(root: Path, kind: HistoryKind) -> dict[str, object]:
    if kind in {"legacy_pending", "legacy_succeeded", "historical_paid", "court_only"}:
        return _legacy(root, kind)
    return _native(root, kind)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: mp08_financial_history.py ROOT KIND")
    requested = sys.argv[2]
    allowed = HistoryKind.__args__
    if requested not in allowed:
        raise SystemExit(f"unknown history kind: {requested}")
    print(json.dumps(build(Path(sys.argv[1]), cast("HistoryKind", requested))))
