"""Native publication admission requires a registered economic actor."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.publication_audit import PublicationAuditFinding, audit_publications
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from tests.fakes.mp05_publication_history import publication_intent
from tests.fakes.native_board_history import register_agent
from tests.unit.test_mp05_publication_contract import (
    PublicationGateway,
    _signed_request,
)

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.unit.test_mp05_publication_contract",)


def _post(gateway: PublicationGateway, payload: dict[str, object]) -> httpx.Response:
    return _signed_request(
        gateway,
        "board",
        "POST",
        "/board/task-publications",
        payload=payload,
    )


def test_unknown_actor_is_rejected_before_any_durable_write(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
) -> None:
    unknown_actor = f"a-unregistered-{uuid4()}"
    intent = publication_intent(unknown_actor)
    before = tuple(db_writer.connection.iterdump())

    response = _post(publication_gateway, intent.body())

    assert response.status_code == 409
    assert response.json()["error"] == "foreign_key_violation"
    assert tuple(db_writer.connection.iterdump()) == before
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM board_task_publications WHERE operation_id=?",
            (intent.operation_id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM board_tasks WHERE task_id=?", (intent.task_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
            (intent.lock_operation_id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM bank_escrow WHERE task_id=?", (intent.task_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=?", (intent.task_id,)
        ).fetchone()[0]
        == 0
    )


def test_registered_actor_without_bank_account_can_admit_pending_publication(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
) -> None:
    actor_id = register_agent(publication_gateway.client, name="Registered unfunded poster")
    intent = publication_intent(actor_id)
    account_count = db_writer.connection.execute(
        "SELECT COUNT(*) FROM bank_accounts WHERE account_id=?", (actor_id,)
    ).fetchone()[0]
    assert account_count == 0

    response = _post(publication_gateway, intent.body())

    assert response.status_code == 202
    body = response.json()
    assert body["operation_id"] == intent.operation_id
    assert body["task_id"] == intent.task_id
    assert body["publication_state"] == "awaiting_lock"
    stored = db_writer.connection.execute(
        "SELECT actor_id,state FROM board_task_publications WHERE operation_id=?",
        (intent.operation_id,),
    ).fetchone()
    assert stored is not None and tuple(stored) == (actor_id, "awaiting_lock")
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM board_tasks WHERE task_id=?", (intent.task_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
            (intent.lock_operation_id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM bank_escrow WHERE task_id=?", (intent.task_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=?", (intent.task_id,)
        ).fetchone()[0]
        == 0
    )


def _corrupted_pending_database(
    gateway: PublicationGateway,
    writer: DbWriter,
    path: Path,
) -> tuple[str, tuple[str, ...]]:
    actor_id = register_agent(gateway.client, name="Historical pending poster")
    admitted_intent = publication_intent(actor_id)
    admitted = _post(gateway, admitted_intent.body())
    assert admitted.status_code == 202

    with sqlite3.connect(path) as target:
        writer.connection.backup(target)
        unknown_actor = f"a-historical-missing-{uuid4()}"
        corrupted_intent = publication_intent(
            unknown_actor,
            operation_id=admitted_intent.operation_id,
            task_id=admitted_intent.task_id,
            title=admitted_intent.title,
            spec=admitted_intent.spec,
            reward=admitted_intent.reward,
        )
        # Intentional isolated historical corruption: retain a fully self-consistent
        # pending publication while changing only its actor-bound request identity.
        target.execute(
            "UPDATE board_task_publications SET canonical_request_json=?,request_sha256=?,"
            "actor_id=?,lock_operation_id=? WHERE operation_id=?",
            (
                json.dumps(
                    corrupted_intent.body(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
                corrupted_intent.request_sha256(),
                unknown_actor,
                corrupted_intent.lock_operation_id,
                admitted_intent.operation_id,
            ),
        )
        target.commit()
        before = tuple(target.iterdump())
    return admitted_intent.operation_id, before


def test_audit_rejects_historical_pending_publication_with_missing_actor(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-publication-actor-audit.db"
    operation_id, before = _corrupted_pending_database(publication_gateway, db_writer, path)

    with sqlite3.connect(path) as connection:
        result = audit_publications(connection)
        after = tuple(connection.iterdump())

    assert result.publications_clean is False
    assert result.findings == (PublicationAuditFinding("publication_actor_missing", operation_id),)
    assert after == before


def test_startup_rejects_historical_pending_publication_with_missing_actor_without_mutation(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-publication-actor-startup.db"
    _operation_id, before = _corrupted_pending_database(publication_gateway, db_writer, path)

    with pytest.raises(ServiceError) as raised:
        DbWriter(str(path), 5000, "wal", None)

    assert raised.value.error == "database_publication_contract_failed"
    assert raised.value.details["finding_codes"] == ["publication_actor_missing"]
    with sqlite3.connect(path) as connection:
        assert tuple(connection.iterdump()) == before


def test_audit_reports_missing_identity_table_without_querying_it() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V4))
        connection.execute("DROP TABLE identity_agents")
        before = tuple(connection.iterdump())

        result = audit_publications(connection)
        after = tuple(connection.iterdump())

    assert result.publications_clean is False
    assert result.findings == (
        PublicationAuditFinding("missing_publication_table", "identity_agents"),
    )
    assert after == before
