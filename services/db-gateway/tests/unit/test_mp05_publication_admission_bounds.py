"""MP-05 admission rejects publication cutoffs that cannot be persisted."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from db_gateway_service import task_time
from db_gateway_service.schema import SQLITE_MAX_INTEGER
from tests.fakes.mp05_publication_history import (
    assert_uniform_error,
    create_funded_poster,
    publication_intent,
)
from tests.unit import test_mp05_publication_contract as publication_contract
from tests.unit.test_mp05_publication_contract import (
    PublicationGateway,
    _assert_awaiting,
    _post,
)

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
publication_gateway = publication_contract.publication_gateway

_ADMISSION_TEXT = "2099-01-01T00:00:00.999999Z"
_ADMISSION_US = task_time.parse_utc_text_to_epoch_us(_ADMISSION_TEXT)
_ADMISSION = datetime.fromisoformat(_ADMISSION_TEXT.replace("Z", "+00:00"))
_MAX_UTC = datetime.max.replace(tzinfo=UTC)
_MAX_DELTA = _MAX_UTC - _ADMISSION
_MAX_COMPOSED_SECONDS = _MAX_DELTA.days * 86_400 + _MAX_DELTA.seconds
_MAX_DURATION_SECONDS = SQLITE_MAX_INTEGER // 1_000_000


def _financial_snapshot(writer: DbWriter) -> dict[str, int]:
    return {
        table: int(writer.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in (
            "bank_accounts",
            "bank_transactions",
            "bank_escrow",
            "bank_operation_receipts",
            "bank_operation_legs",
        )
    }


@pytest.mark.parametrize(
    ("bidding_seconds", "acceptance_seconds", "field"),
    [
        (_MAX_DURATION_SECONDS, 1, "bidding_deadline_seconds"),
        (60, _MAX_COMPOSED_SECONDS - 59, "acceptance_window_seconds"),
        (_MAX_COMPOSED_SECONDS - 1, 2, "acceptance_window_seconds"),
    ],
    ids=["sqlite-overflow", "year-10000", "composed-bid-accept-overflow"],
)
def test_unrepresentable_cutoff_rejected_before_reservation_or_money_effect(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
    bidding_seconds: int,
    acceptance_seconds: int,
    field: str,
) -> None:
    poster = create_funded_poster(publication_gateway.client)
    intent = replace(
        publication_intent(poster),
        bidding_deadline_seconds=bidding_seconds,
        acceptance_window_seconds=acceptance_seconds,
    )
    before_money = _financial_snapshot(db_writer)
    monkeypatch.setattr(task_time, "utc_now_us", lambda: _ADMISSION_US)

    response = _post(publication_gateway, intent)

    error = assert_uniform_error(response, 400, "invalid_deadline")
    assert field in error["message"]
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
    assert _financial_snapshot(db_writer) == before_money


def test_largest_representable_composed_cutoff_is_admitted(
    publication_gateway: PublicationGateway,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poster = create_funded_poster(publication_gateway.client)
    bidding_seconds = 60
    intent = replace(
        publication_intent(poster),
        bidding_deadline_seconds=bidding_seconds,
        acceptance_window_seconds=_MAX_COMPOSED_SECONDS - bidding_seconds,
    )
    monkeypatch.setattr(task_time, "utc_now_us", lambda: _ADMISSION_US)

    response = _post(publication_gateway, intent)

    assert response.status_code == 202, response.text
    _assert_awaiting(response.json(), intent)
    assert response.json()["admitted_at"] == _ADMISSION_TEXT
    assert (
        db_writer.connection.execute(
            "SELECT COUNT(*) FROM board_task_publications WHERE operation_id=?",
            (intent.operation_id,),
        ).fetchone()[0]
        == 1
    )


def test_fresh_normal_intent_remains_admissible(
    publication_gateway: PublicationGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poster = create_funded_poster(publication_gateway.client)
    intent = publication_intent(poster)
    monkeypatch.setattr(task_time, "utc_now_us", lambda: _ADMISSION_US)

    response = _post(publication_gateway, intent)

    assert response.status_code == 202, response.text
    _assert_awaiting(response.json(), intent)


def test_existing_operation_replay_and_conflict_precede_fresh_clock_validation(
    publication_gateway: PublicationGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poster = create_funded_poster(publication_gateway.client)
    intent = publication_intent(poster)
    monkeypatch.setattr(task_time, "utc_now_us", lambda: _ADMISSION_US)
    admitted = _post(publication_gateway, intent)
    assert admitted.status_code == 202, admitted.text

    monkeypatch.setattr(task_time, "utc_now_us", lambda: SQLITE_MAX_INTEGER)
    replay = _post(publication_gateway, intent)
    conflict = _post(publication_gateway, replace(intent, title="Different terms"))

    assert replay.status_code == 202, replay.text
    assert replay.json() == admitted.json()
    assert_uniform_error(conflict, 409, "operation_id_conflict")
