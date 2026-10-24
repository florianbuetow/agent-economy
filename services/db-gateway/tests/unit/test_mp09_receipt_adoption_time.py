"""Exact MP-09 V2 publication-time feasibility and chronology."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_receipt_adoption_fixture import (
    ADMITTED_US,
    AUTHORIZED_US,
    FROZEN_US,
    MAX_UTC_US,
    PUBLICATION_ID,
    TASK_ID,
    V2_ENVELOPE_KEYS,
    BankReadGuard,
    ReceiptAdoptionFixture,
    admit_publication,
    authorize_publication,
    bank_snapshot,
    freeze_publication,
    full_snapshot,
    fund_poster,
    lock_publication,
    open_receipt_fixture,
    prepare_authorized_publication,
    publication_row,
    reconcile_publication,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_ONE_SECOND_TERMS = {
    "bidding_deadline_seconds": 1,
    "acceptance_window_seconds": 1,
    "execution_deadline_seconds": 1,
    "review_deadline_seconds": 1,
}


def _prepare_one_second_terms(tmp_path: Path, name: str) -> ReceiptAdoptionFixture:
    fixture = open_receipt_fixture(tmp_path / f"{name}.sqlite3")
    fund_poster(fixture)
    admit_publication(fixture, overrides=_ONE_SECOND_TERMS)
    freeze_publication(fixture)
    return fixture


def _assert_clock_block(exc: ServiceError) -> None:
    assert (exc.status_code, exc.error, exc.message) == (
        409,
        "publication_integrity_blocked",
        "Publication evidence requires reconciliation",
    )
    assert set(exc.details) == {"publication"}
    publication = exc.details["publication"]
    assert isinstance(publication, dict)
    assert set(publication) == V2_ENVELOPE_KEYS
    assert publication["publication_state"] == "blocked_integrity"
    assert publication["error"] == {
        "error": "publication_integrity_blocked",
        "message": "Publication evidence requires reconciliation",
        "details": {"reason": "clock_out_of_range"},
    }


def test_publish_authorization_accepts_chain_ending_exactly_at_max_utc(tmp_path: Path) -> None:
    fixture = _prepare_one_second_terms(tmp_path, "exact-max")
    try:
        exact_base = MAX_UTC_US - 3_999_998
        with BankReadGuard(fixture.connection, deny=True) as guard:
            authorized = authorize_publication(fixture, at_us=exact_base)
        assert guard.reads == []
        assert authorized["publication_state"] == "awaiting_lock"
        assert authorized["publish_authorized_at_us"] == exact_base
        assert fixture.connection.execute("SELECT COUNT(*) FROM bank_escrow").fetchone()[0] == 0
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("authorization_us", "expected_field"),
    [
        (MAX_UTC_US - 999_999, "bidding_deadline_seconds"),
        (MAX_UTC_US - 1_999_999, "acceptance_window_seconds"),
        (MAX_UTC_US - 2_999_998, "execution_deadline_seconds"),
        (MAX_UTC_US - 3_999_997, "review_deadline_seconds"),
        (MAX_UTC_US - 1_000_000, "acceptance_window_seconds"),
        (MAX_UTC_US - 2_000_000, "execution_deadline_seconds"),
        (MAX_UTC_US - 2_999_999, "review_deadline_seconds"),
    ],
    ids=(
        "bidding-plus-one-us",
        "acceptance-plus-one-us",
        "execution-plus-one-us",
        "review-plus-one-us",
        "bidding-equals-max",
        "acceptance-equals-max",
        "execution-equals-max",
    ),
)
def test_publish_authorization_rejects_the_first_unrepresentable_signed_duration(
    tmp_path: Path,
    authorization_us: int,
    expected_field: str,
) -> None:
    fixture = _prepare_one_second_terms(tmp_path, expected_field + str(authorization_us))
    try:
        before = full_snapshot(fixture.connection)
        with (
            BankReadGuard(fixture.connection, deny=True) as guard,
            pytest.raises(ServiceError) as raised,
        ):
            authorize_publication(fixture, at_us=authorization_us)
        assert (raised.value.status_code, raised.value.error, raised.value.details) == (
            400,
            "invalid_deadline",
            {"field": expected_field},
        )
        assert guard.reads == []
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_delayed_adoption_overflow_durably_blocks_without_public_projection(
    tmp_path: Path,
) -> None:
    fixture = prepare_authorized_publication(tmp_path / "delayed-overflow.sqlite3")
    try:
        lock_publication(fixture)
        bank_before = bank_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            reconcile_publication(fixture, at_us=MAX_UTC_US - 1_000_000)
        _assert_clock_block(raised.value)
        assert bank_snapshot(fixture.connection) == bank_before
        assert fixture.connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
        assert (
            fixture.connection.execute("SELECT COUNT(*) FROM board_task_contracts").fetchone()[0]
            == 0
        )
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_source='task-board' "
                "AND event_type='task.created' AND task_id=?",
                (TASK_ID,),
            ).fetchone()[0]
            == 0
        )
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM board_task_artifact_contracts"
            ).fetchone()[0]
            == 0
        )
        assert publication_row(fixture.connection)[8] == "blocked_integrity"
    finally:
        fixture.close()


def test_corrupted_stored_phase_order_fails_before_any_bank_read(tmp_path: Path) -> None:
    fixture = prepare_authorized_publication(tmp_path / "phase-order.sqlite3")
    try:
        fixture.connection.execute(
            "UPDATE board_task_publications SET publish_authorized_at_us=? WHERE operation_id=?",
            (FROZEN_US - 1, PUBLICATION_ID),
        )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with (
            BankReadGuard(fixture.connection, deny=True) as guard,
            pytest.raises(ServiceError) as raised,
        ):
            reconcile_publication(fixture)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "publication_state_corrupt",
        )
        assert guard.reads == []
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_receipt_one_microsecond_before_authorization_durably_blocks(tmp_path: Path) -> None:
    fixture = prepare_authorized_publication(tmp_path / "receipt-regression.sqlite3")
    try:
        lock_publication(fixture, committed_at_us=AUTHORIZED_US - 1)
        bank_before = bank_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            reconcile_publication(fixture)
        _assert_clock_block(raised.value)
        assert bank_snapshot(fixture.connection) == bank_before
        assert fixture.connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("receipt_us", "publication_us"),
    [
        (AUTHORIZED_US, AUTHORIZED_US - 1),
        (AUTHORIZED_US + 1, AUTHORIZED_US),
    ],
    ids=("publication-before-authorization", "publication-before-receipt"),
)
def test_publication_clock_regression_durably_blocks(
    tmp_path: Path,
    receipt_us: int,
    publication_us: int,
) -> None:
    fixture = prepare_authorized_publication(tmp_path / f"clock-{receipt_us}.sqlite3")
    try:
        lock_publication(fixture, committed_at_us=receipt_us)
        bank_before = bank_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            reconcile_publication(fixture, at_us=publication_us)
        _assert_clock_block(raised.value)
        assert bank_snapshot(fixture.connection) == bank_before
        assert fixture.connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
    finally:
        fixture.close()


def test_equal_adjacent_phase_times_are_accepted(tmp_path: Path) -> None:
    fixture = open_receipt_fixture(tmp_path / "equal-times.sqlite3")
    try:
        fund_poster(fixture)
        admit_publication(fixture, at_us=ADMITTED_US)
        freeze_publication(fixture, at_us=ADMITTED_US)
        authorize_publication(fixture, at_us=ADMITTED_US)
        lock_publication(fixture, committed_at_us=ADMITTED_US)
        published = reconcile_publication(fixture, at_us=ADMITTED_US)
        assert published["publication_state"] == "published"
        assert published["inputs_frozen_at_us"] == ADMITTED_US
        assert published["publish_authorized_at_us"] == ADMITTED_US
        assert published["published_at"] is not None
    finally:
        fixture.close()
