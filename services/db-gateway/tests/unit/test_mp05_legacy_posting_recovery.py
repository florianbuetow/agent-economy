"""MP-05 acceptance coverage for private legacy-posting refund recovery."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair
from service_commons.exceptions import ServiceError

from db_gateway_service.config import clear_settings_cache
from db_gateway_service.core.state import init_app_state
from db_gateway_service.migration import migrate_v3_to_v4
from db_gateway_service.services.db_writer import DbWriter
from tests.fakes.mp05_publication_history import publication_intent
from tests.fakes.mp05_publication_migration_history import (
    RichCutoverHistory,
    V3Source,
    add_rich_cutover_history,
    create_funded_payer,
    open_v3_writer,
    prepare_v3_source,
    prepare_v3_source_with_legacy_refund,
    publish_verified_backup,
)
from tests.unit.conftest import _create_test_config
from tests.unit.test_mp05_publication_contract import PublicationGateway, _signed_request

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_CAPABILITY_GAP = "Missing legacy posting observation reconciliation API"
_LEGACY_PATH = "/board/legacy-posting-observations"


@dataclass(frozen=True, slots=True)
class _PendingTemplate:
    database: Path
    caller_kid: str
    winner_task_id: str
    winner_escrow_id: str
    pending_observation_ids: tuple[str, ...]
    refunded_observation_id: str


@pytest.fixture(scope="module")
def pending_template(tmp_path_factory: pytest.TempPathFactory) -> _PendingTemplate:
    source, history = _migrated_pending_source(tmp_path_factory.mktemp("mp05-legacy-pending"))
    connection = _open_immutable(source.database)
    try:
        pending = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT observation_id FROM board_legacy_posting_observations "
                "WHERE state='refund_pending' ORDER BY observation_id"
            ).fetchall()
        )
        refunded = connection.execute(
            "SELECT observation_id FROM board_legacy_posting_observations WHERE state='refunded'"
        ).fetchone()
        assert len(pending) == 2 and refunded is not None
    finally:
        connection.close()
    _assert_standalone(source.database)
    return _PendingTemplate(
        source.database,
        source.caller_kid,
        history.task_id,
        history.winner.escrow_id,
        pending,
        str(refunded[0]),
    )


@pytest.fixture(scope="module")
def legacy_refunded_template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, str]:
    source = _migrate(
        prepare_v3_source_with_legacy_refund(tmp_path_factory.mktemp("mp05-legacy-refunded")),
        backup_id="legacy-refunded-before-mp05",
    )
    connection = _open_immutable(source.database)
    try:
        row = connection.execute(
            "SELECT observation_id FROM board_legacy_posting_observations WHERE state='refunded'"
        ).fetchone()
        assert row is not None
    finally:
        connection.close()
    _assert_standalone(source.database)
    return source.database, source.caller_kid, str(row[0])


@pytest.fixture
def legacy_gateway(
    pending_template: _PendingTemplate,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[PublicationGateway, _PendingTemplate]]:
    database = _copy_database(pending_template.database, tmp_path / "gateway.db")
    writer = DbWriter(str(database), 5000, "wal", None)
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
    config_path = tmp_path / "legacy-recovery-config.yaml"
    _create_test_config(str(database), config_path)
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

    with TestClient(create_app()) as client:
        init_app_state().db_writer = writer
        yield PublicationGateway(client, signers), pending_template
    writer.close()
    os.environ.pop("CONFIG_PATH", None)
    clear_settings_cache()


def test_legacy_reconciliation_api_exists(
    pending_template: _PendingTemplate, tmp_path: Path
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "capability.db")
    try:
        assert callable(getattr(writer, "reconcile_legacy_posting_observation", None)), (
            _CAPABILITY_GAP
        )
    finally:
        writer.close()


def test_pending_replay_returns_frozen_intent_without_money_effect(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "pending.db")
    observation_id = pending_template.pending_observation_ids[0]
    before = _money_snapshot(writer.connection)
    try:
        first = _reconcile(writer, observation_id, pending_template.caller_kid)
        second = _reconcile(writer, observation_id, pending_template.caller_kid)
        assert first == second
        _assert_legacy_envelope(first, writer.connection, expected_state="refund_pending")
        assert _money_snapshot(writer.connection) == before
    finally:
        writer.close()


def test_exact_release_finalizes_once_and_replays_after_reopen(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    database = _copy_database(pending_template.database, tmp_path / "release.db")
    observation_id = pending_template.pending_observation_ids[0]
    writer = DbWriter(str(database), 5000, "wal", None)
    try:
        pending = _reconcile(writer, observation_id, pending_template.caller_kid)
        receipt = _persist_exact_refund(writer, observation_id)
        completed = _reconcile(writer, observation_id, pending_template.caller_kid)
        _assert_legacy_envelope(completed, writer.connection, expected_state="refunded")
        assert completed["refund_receipt_event_id"] == receipt["event_id"]
        assert completed["result"] == receipt["result"]
        assert completed != pending
        assert _refund_receipt_count(writer.connection, observation_id) == 1
    finally:
        writer.close()

    reopened = DbWriter(str(database), 5000, "wal", None)
    try:
        assert _reconcile(reopened, observation_id, pending_template.caller_kid) == completed
        assert _refund_receipt_count(reopened.connection, observation_id) == 1
    finally:
        reopened.close()


def test_two_same_task_orphans_refund_each_payer_and_leave_winner_untouched(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "two-orphans.db")
    try:
        before_winner = writer.connection.execute(
            "SELECT escrow_id,status,poster_id,reward FROM board_tasks WHERE task_id=?",
            (pending_template.winner_task_id,),
        ).fetchone()
        assert before_winner is not None
        for observation_id in pending_template.pending_observation_ids:
            _persist_exact_refund(writer, observation_id)
            response = _reconcile(writer, observation_id, pending_template.caller_kid)
            _assert_legacy_envelope(response, writer.connection, expected_state="refunded")
        after_winner = writer.connection.execute(
            "SELECT escrow_id,status,poster_id,reward FROM board_tasks WHERE task_id=?",
            (pending_template.winner_task_id,),
        ).fetchone()
        assert tuple(after_winner) == tuple(before_winner)
        assert after_winner[0] == pending_template.winner_escrow_id
        recipients = writer.connection.execute(
            "SELECT json_extract(refund_request_json,'$.recipient_account_id') "
            "FROM board_legacy_posting_observations WHERE state='refunded' "
            "AND task_id=? ORDER BY escrow_id",
            (pending_template.winner_task_id,),
        ).fetchall()
        payers = writer.connection.execute(
            "SELECT poster_id FROM board_legacy_posting_observations WHERE task_id=? "
            "ORDER BY escrow_id",
            (pending_template.winner_task_id,),
        ).fetchall()
        assert [tuple(row) for row in recipients] == [tuple(row) for row in payers]
    finally:
        writer.close()


@pytest.mark.parametrize("provenance", ["native", "legacy"])
def test_historical_refund_replay_preserves_honest_identity_without_new_effect(
    pending_template: _PendingTemplate,
    legacy_refunded_template: tuple[Path, str, str],
    tmp_path: Path,
    provenance: str,
) -> None:
    if provenance == "native":
        database = _copy_database(pending_template.database, tmp_path / "native-refunded.db")
        caller_kid = pending_template.caller_kid
        observation_id = pending_template.refunded_observation_id
    else:
        template, caller_kid, observation_id = legacy_refunded_template
        database = _copy_database(template, tmp_path / "legacy-refunded.db")
    writer = DbWriter(str(database), 5000, "wal", None)
    before = _money_snapshot(writer.connection)
    try:
        response = _reconcile(writer, observation_id, caller_kid)
        _assert_legacy_envelope(response, writer.connection, expected_state="refunded")
        if provenance == "legacy":
            assert response["refund_operation_id"] is None
            assert response["refund_request"] is None
            assert response["result"]["settlement_provenance_kind"] == "legacy_observation"
        else:
            assert isinstance(response["refund_operation_id"], str)
            assert response["refund_request"]["operation_kind"] == "escrow_release"
        assert _money_snapshot(writer.connection) == before
    finally:
        writer.close()


def test_direct_reconciliation_hides_foreign_and_missing_observations(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "visibility.db")
    try:
        with pytest.raises(ServiceError) as foreign:
            _reconcile(writer, pending_template.pending_observation_ids[0], "foreign-kid")
        assert foreign.value.error == "operation_caller_mismatch"
        assert foreign.value.status_code == 403
        with pytest.raises(ServiceError) as missing:
            _reconcile(writer, f"obs-{uuid4()}", pending_template.caller_kid)
        assert missing.value.error == "posting_observation_not_found"
        assert missing.value.status_code == 404
        assert missing.value.message == "Posting observation not found"
    finally:
        writer.close()


def test_route_enforces_write_owner_empty_body_and_missing_shape(
    legacy_gateway: tuple[PublicationGateway, _PendingTemplate],
) -> None:
    gateway, template = legacy_gateway
    observation_id = template.pending_observation_ids[0]
    path = f"{_LEGACY_PATH}/{observation_id}/reconcile"
    foreign = _signed_request(gateway, "board_foreign", "POST", path, payload={})
    assert foreign.status_code == 403
    assert foreign.json()["error"] == "operation_caller_mismatch"
    bad_body = _signed_request(gateway, "board", "POST", path, payload={"receipt": {}})
    assert bad_body.status_code == 400
    assert bad_body.json()["error"] == "invalid_payload"
    missing = _signed_request(
        gateway,
        "board",
        "POST",
        f"{_LEGACY_PATH}/obs-{uuid4()}/reconcile",
        payload={},
    )
    assert missing.status_code == 404
    assert missing.json() == {
        "error": "posting_observation_not_found",
        "message": "Posting observation not found",
        "details": {},
    }


def test_reconciliation_accepts_no_forwarded_receipt_authority(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "no-forward.db")
    method = getattr(writer, "reconcile_legacy_posting_observation", None)
    assert callable(method), _CAPABILITY_GAP
    try:
        with pytest.raises(TypeError):
            method(
                pending_template.pending_observation_ids[0],
                pending_template.caller_kid,
                {"receipt": "caller supplied"},
            )
    finally:
        writer.close()


def test_recovery_pagination_merges_owned_unfinished_items_in_tagged_order(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "pagination.db")
    native_ids: list[str] = []
    foreign_id = ""
    try:
        for index in range(2):
            poster = create_funded_payer(writer, name=f"Pending native {index}")
            intent = publication_intent(poster)
            response = writer.admit_task_publication(
                cast("Any", _posting_command(intent)), pending_template.caller_kid
            )
            assert response["publication_state"] == "awaiting_lock"
            native_ids.append(intent.operation_id)
        foreign_poster = create_funded_payer(writer, name="Foreign pending native")
        foreign_intent = publication_intent(foreign_poster)
        writer.admit_task_publication(cast("Any", _posting_command(foreign_intent)), "foreign-kid")
        foreign_id = foreign_intent.operation_id

        expected = sorted(
            [
                *(f"legacy:{value}" for value in pending_template.pending_observation_ids),
                *(f"native:{value}" for value in native_ids),
            ],
            key=lambda value: value.encode("utf-8"),
        )
        observed: list[str] = []
        after = ""
        while True:
            page = writer.list_posting_recovery(after, 2, pending_template.caller_kid)
            assert set(page) == {"items", "next_after"}
            observed.extend(str(item["cursor"]) for item in page["items"])
            if page["next_after"] is None:
                break
            assert page["next_after"] == page["items"][-1]["cursor"]
            after = str(page["next_after"])
        assert observed == expected
        assert f"native:{foreign_id}" not in observed
        assert all(pending_template.refunded_observation_id not in cursor for cursor in observed)
    finally:
        writer.close()


@pytest.mark.parametrize(
    "defect",
    ["foreign_party", "amount", "event", "reference", "receipt_hash"],
)
def test_conflicting_refund_evidence_blocks_durably_without_second_effect(
    pending_template: _PendingTemplate,
    tmp_path: Path,
    defect: str,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / f"blocked-{defect}.db")
    observation_id = pending_template.pending_observation_ids[0]
    try:
        _persist_conflicting_refund(writer, observation_id, defect)
        before = _money_snapshot(writer.connection)
        with pytest.raises(ServiceError) as first:
            _reconcile(writer, observation_id, pending_template.caller_kid)
        assert first.value.error == "posting_refund_integrity_blocked"
        assert first.value.status_code == 409
        envelope = first.value.details["observation"]
        assert envelope["observation_state"] == "blocked_integrity"
        assert envelope["error"]["details"]["reason"] in {
            "receipt_invalid",
            "escrow_invalid",
            "settlement_conflict",
            "task_escrow_reference_conflict",
        }
        with pytest.raises(ServiceError) as replay:
            _reconcile(writer, observation_id, pending_template.caller_kid)
        assert replay.value.details == first.value.details
        assert _money_snapshot(writer.connection) == before
        assert _refund_receipt_count(writer.connection, observation_id) == 1
    finally:
        writer.close()


def _migrated_pending_source(root: Path) -> tuple[V3Source, RichCutoverHistory]:
    source = prepare_v3_source(root)
    writer = open_v3_writer(source)
    try:
        history = add_rich_cutover_history(writer)
    finally:
        writer.close()
    return _migrate(source, backup_id="legacy-pending-before-mp05"), history


def _migrate(source: V3Source, *, backup_id: str) -> V3Source:
    publish_verified_backup(source, backup_id=backup_id)
    result = migrate_v3_to_v4(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5000,
    )
    assert result.observation == "committed_v4_verified"
    return source


def _reconcile(writer: DbWriter, observation_id: str, caller_kid: str) -> dict[str, Any]:
    method = getattr(writer, "reconcile_legacy_posting_observation", None)
    if not callable(method):
        pytest.fail(_CAPABILITY_GAP)
    return cast("dict[str, Any]", method(observation_id, caller_kid))


def _persist_exact_refund(writer: DbWriter, observation_id: str) -> dict[str, Any]:
    row = _observation_row(writer.connection, observation_id)
    request = json.loads(str(row["refund_request_json"]))
    result = writer.escrow_release(
        {
            "operation_id": row["refund_operation_id"],
            "escrow_id": row["escrow_id"],
            "recipient_account_id": row["poster_id"],
            "source_reference": request["source_reference"],
            "tx_id": f"tx-{uuid4()}",
            "resolved_at": "2099-01-01T12:00:02.000000Z",
            "event": _release_event(row, agent_id=str(row["poster_id"])),
        }
    )
    return cast("dict[str, Any]", result["receipt"])


def _persist_conflicting_refund(writer: DbWriter, observation_id: str, defect: str) -> None:
    row = _observation_row(writer.connection, observation_id)
    request = json.loads(str(row["refund_request_json"]))
    recipient = str(row["poster_id"])
    source_reference = str(request["source_reference"])
    if defect == "foreign_party":
        recipient = create_funded_payer(writer, name="Foreign refund recipient", balance=1)
    if defect == "reference":
        source_reference += ":wrong"
    result = writer.escrow_release(
        {
            "operation_id": row["refund_operation_id"],
            "escrow_id": row["escrow_id"],
            "recipient_account_id": recipient,
            "source_reference": source_reference,
            "tx_id": f"tx-{uuid4()}",
            "resolved_at": "2099-01-01T12:00:02.000000Z",
            "event": _release_event(row, agent_id=recipient),
        }
    )
    receipt = result["receipt"]
    if defect == "amount":
        writer.connection.execute(
            "UPDATE bank_operation_legs SET amount=amount-1 WHERE operation_id=?",
            (row["refund_operation_id"],),
        )
    elif defect == "event":
        writer.connection.execute(
            "UPDATE events SET event_source='foreign-bank' WHERE event_id=?",
            (receipt["event_id"],),
        )
    elif defect == "receipt_hash":
        writer.connection.execute(
            "UPDATE bank_operation_receipts SET request_sha256=? WHERE operation_id=?",
            ("f" * 64, row["refund_operation_id"]),
        )
    writer.connection.commit()


def _release_event(row: sqlite3.Row, *, agent_id: str) -> dict[str, object]:
    return {
        "event_source": "central-bank",
        "event_type": "escrow.released",
        "timestamp": "2099-01-01T12:00:02.000000Z",
        "task_id": row["task_id"],
        "agent_id": agent_id,
        "summary": "MP-05 legacy posting refund",
        "payload": "{}",
    }


def _observation_row(connection: sqlite3.Connection, observation_id: str) -> sqlite3.Row:
    prior = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM board_legacy_posting_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        assert row is not None
        return row
    finally:
        connection.row_factory = prior


def _assert_legacy_envelope(
    response: dict[str, Any],
    connection: sqlite3.Connection,
    *,
    expected_state: str,
) -> None:
    assert set(response) == {
        "protocol_version",
        "observation_id",
        "task_id",
        "escrow_id",
        "poster_id",
        "amount",
        "observed_fact_sha256",
        "observation_state",
        "refund_operation_id",
        "refund_request",
        "refund_request_sha256",
        "refund_receipt_event_id",
        "refunded_at",
        "result",
        "error",
    }
    assert response["protocol_version"] == 1
    assert response["observation_state"] == expected_state
    stored = _observation_row(connection, str(response["observation_id"]))
    assert response["task_id"] == stored["task_id"]
    assert response["escrow_id"] == stored["escrow_id"]
    assert response["poster_id"] == stored["poster_id"]
    assert response["amount"] == stored["amount"]
    assert response["observed_fact_sha256"] == stored["observed_fact_sha256"]
    assert response["refund_operation_id"] == stored["refund_operation_id"]
    assert response["refund_request_sha256"] == stored["refund_request_sha256"]
    if expected_state == "refund_pending":
        assert response["refund_request"] == json.loads(stored["refund_request_json"])
        assert response["refund_receipt_event_id"] is None
        assert response["refunded_at"] is None
        assert response["result"] is None
        assert response["error"] is None
    else:
        assert response["result"] == json.loads(stored["result_json"])
        assert isinstance(response["refunded_at"], str)
        assert response["error"] is None


def _money_snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in (
            "bank_accounts",
            "bank_transactions",
            "bank_escrow",
            "bank_operation_receipts",
            "bank_operation_legs",
        )
    }


def _refund_receipt_count(connection: sqlite3.Connection, observation_id: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM bank_operation_receipts r "
        "JOIN board_legacy_posting_observations o ON o.refund_operation_id=r.operation_id "
        "WHERE o.observation_id=? AND r.operation_kind='escrow_release'",
        (observation_id,),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _posting_command(intent: Any) -> Any:
    from db_gateway_service.task_publication_requests import parse_task_publication_request

    return parse_task_publication_request(intent.body())


def _open_copy(source: Path, target: Path) -> DbWriter:
    return DbWriter(str(_copy_database(source, target)), 5000, "wal", None)


def _copy_database(source: Path, target: Path) -> Path:
    _assert_standalone(source)
    shutil.copy2(source, target)
    return target


def _assert_standalone(database: Path) -> None:
    wal = Path(f"{database}-wal")
    assert not wal.exists() or wal.stat().st_size == 0


def _open_immutable(database: Path) -> sqlite3.Connection:
    _assert_standalone(database)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection
