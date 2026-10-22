"""Restart admission for MP-07 Board-first ruling delivery."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from service_auth.signing import generate_keypair, public_key_to_b64
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_contract_audit import audit_task_contracts
from tests.conftest import make_event
from tests.unit.test_mp07_court_operation_contract import (
    _BOARD_KID,
    _Authority,
    _Graph,
    _native,
    _prepare_disputed_fixture,
    _request,
    _ruling_payload,
    _seed_submitted,
    _settle_ruling_effect,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _CompletedRuling:
    graph: _Graph
    claim_id: str
    worker_pct: int
    summary: str


def _platform_authority(tmp_path: Path) -> _Authority:
    _private_key, public_key = generate_keypair("ruling-startup-platform", tmp_path)
    return _Authority(
        agent_id=f"a-platform-{uuid4()}",
        public_key=f"ed25519:{public_key_to_b64(public_key)}",
    )


def _writer(database_path: Path, *, bootstrap: bool) -> DbWriter:
    return DbWriter(
        db_path=str(database_path),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V4) if bootstrap else None,
    )


def _complete_board_first_ruling(
    writer: DbWriter,
    authority: _Authority,
    worker_pct: int,
) -> _CompletedRuling:
    graph = _seed_submitted(writer, authority=authority)
    claim_id = f"clm-{uuid4()}"
    _prepare_disputed_fixture(writer, graph, claim_id)
    operation_id = f"op-{uuid4()}"
    payload = _ruling_payload(
        graph,
        operation_id,
        authority.agent_id,
        claim_id,
        worker_pct,
    )
    request = _request(payload)
    native = _native(writer, authority)

    pending = native.claim_operation(request, _BOARD_KID)
    assert pending["operation_state"] == "claimed"
    assert (
        writer.connection.execute(
            "SELECT COUNT(*) FROM court_rulings WHERE task_id=?", (graph.task_id,)
        ).fetchone()[0]
        == 0
    )
    _settle_ruling_effect(writer, graph, operation_id, claim_id, worker_pct)
    completed = native.finalize_operation(operation_id, _BOARD_KID)
    result = completed["result"]

    assert completed["operation_state"] == "completed"
    assert isinstance(result, dict)
    assert result["status"] == "ruled"
    assert result["ruling_id"] == claim_id
    assert result["worker_pct"] == worker_pct
    assert (
        writer.connection.execute(
            "SELECT COUNT(*) FROM court_rulings WHERE task_id=?", (graph.task_id,)
        ).fetchone()[0]
        == 0
    )
    arguments = payload["arguments"]
    assert isinstance(arguments, dict)
    summary = arguments["summary"]
    assert isinstance(summary, str)
    return _CompletedRuling(graph, claim_id, worker_pct, summary)


def _reopen_outcome(database_path: Path) -> dict[str, object]:
    try:
        reopened = _writer(database_path, bootstrap=False)
    except ServiceError as exc:
        return {
            "kind": "service_error",
            "status": exc.status_code,
            "error": exc.error,
            "details": exc.details,
        }
    try:
        audit = audit_task_contracts(reopened.connection)
        return {
            "kind": "success",
            "contracts_clean": audit.contracts_clean,
            "findings": tuple((finding.code, finding.task_id) for finding in audit.findings),
        }
    finally:
        reopened.close()


def _audit_facts(writer: DbWriter) -> dict[str, object]:
    audit = audit_task_contracts(writer.connection)
    return {
        "contracts_clean": audit.contracts_clean,
        "findings": tuple((finding.code, finding.task_id) for finding in audit.findings),
    }


@pytest.mark.parametrize("worker_pct", [0, 50, 100])
def test_board_first_ruling_without_court_row_survives_audit_and_restart(
    tmp_path: Path,
    worker_pct: int,
) -> None:
    """Court persists its ruling only after this Board delivery returns."""
    database_path = tmp_path / f"board-first-ruling-{worker_pct}.db"
    authority = _platform_authority(tmp_path)
    with closing(_writer(database_path, bootstrap=True)) as writer:
        completed = _complete_board_first_ruling(writer, authority, worker_pct)
        audit = _audit_facts(writer)
        assert (
            writer.connection.execute(
                "SELECT COUNT(*) FROM court_rulings WHERE task_id=?", (completed.graph.task_id,)
            ).fetchone()[0]
            == 0
        )

    reopened = _reopen_outcome(database_path)

    assert {
        "audit": audit,
        "reopened": reopened,
    } == {
        "audit": {"contracts_clean": True, "findings": ()},
        "reopened": {"kind": "success", "contracts_clean": True, "findings": ()},
    }


def _persist_matching_court_ruling(
    writer: DbWriter,
    completed: _CompletedRuling,
    *,
    worker_pct: int,
) -> None:
    result = writer.record_ruling(
        {
            "ruling_id": completed.claim_id,
            "claim_id": completed.claim_id,
            "task_id": completed.graph.task_id,
            "worker_pct": worker_pct,
            "summary": completed.summary,
            "judge_votes": '[{"judge_id":"judge-0","worker_pct":50}]',
            "ruled_at": "2099-01-01T00:00:07Z",
            "claim_status_update": "ruled",
            "event": make_event(
                source="court",
                event_type="ruling.delivered",
                task_id=completed.graph.task_id,
            ),
        }
    )
    assert result["ruling_id"] == completed.claim_id


def test_matching_later_court_row_remains_restart_clean(tmp_path: Path) -> None:
    database_path = tmp_path / "matching-later-court-row.db"
    authority = _platform_authority(tmp_path)
    with closing(_writer(database_path, bootstrap=True)) as writer:
        completed = _complete_board_first_ruling(writer, authority, 50)
        _persist_matching_court_ruling(writer, completed, worker_pct=50)
        audit = _audit_facts(writer)

    reopened = _reopen_outcome(database_path)

    assert audit == {"contracts_clean": True, "findings": ()}
    assert reopened == {"kind": "success", "contracts_clean": True, "findings": ()}


def test_mismatching_present_court_row_still_fails_audit_and_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "mismatching-court-row.db"
    authority = _platform_authority(tmp_path)
    with closing(_writer(database_path, bootstrap=True)) as writer:
        completed = _complete_board_first_ruling(writer, authority, 50)
        _persist_matching_court_ruling(writer, completed, worker_pct=49)
        audit = _audit_facts(writer)

    reopened = _reopen_outcome(database_path)

    finding = ("dispute_claim_linkage_invalid", completed.graph.task_id)
    assert audit == {"contracts_clean": False, "findings": (finding,)}
    assert reopened == {
        "kind": "service_error",
        "status": 500,
        "error": "database_task_contract_failed",
        "details": {"finding_codes": ["dispute_claim_linkage_invalid"]},
    }


def test_missing_court_row_requires_a_completed_native_ruling_proof(tmp_path: Path) -> None:
    database_path = tmp_path / "missing-ruling-proofs.db"
    authority = _platform_authority(tmp_path)
    with closing(_writer(database_path, bootstrap=True)) as writer:
        completed = _complete_board_first_ruling(writer, authority, 50)
        writer.connection.execute(
            "DELETE FROM board_task_operations WHERE task_id=? AND command='record_ruling'",
            (completed.graph.task_id,),
        )
        writer.connection.commit()
        assert not audit_task_contracts(writer.connection).contracts_clean
    assert _reopen_outcome(database_path)["kind"] == "service_error"
