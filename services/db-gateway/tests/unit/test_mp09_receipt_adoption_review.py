"""Review regressions for V2 receipt-adoption postwrite and replay integrity."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_receipt_adoption_fixture import (
    BOARD_KID,
    ESCROW_ID,
    POSTER_ID,
    PUBLICATION_ID,
    PUBLISHED_US,
    TASK_ID,
    BankReadGuard,
    bank_snapshot,
    fixed_utc_text,
    full_snapshot,
    lock_publication,
    prepare_authorized_publication,
    reconcile_publication,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

    from tests.fakes.mp09_receipt_adoption_fixture import ReceiptAdoptionFixture

pytestmark = pytest.mark.unit

_ENTRYPOINTS = ("get", "reconcile", "admission_replay", "command_replay")


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _service_error(call: Callable[[], object]) -> ServiceError:
    with pytest.raises(ServiceError) as raised:
        call()
    return raised.value


def _published_fixture(database_path: Path) -> ReceiptAdoptionFixture:
    fixture = prepare_authorized_publication(database_path)
    try:
        lock_publication(fixture)
        published = reconcile_publication(fixture)
        assert published["publication_state"] == "published"
        return fixture
    except BaseException:
        fixture.close()
        raise


def _blocked_fixture(database_path: Path) -> ReceiptAdoptionFixture:
    fixture = prepare_authorized_publication(database_path)
    try:
        lock_publication(fixture)
        assert fixture.admission_request is not None
        fixture.connection.execute(
            "UPDATE bank_operation_receipts SET result_json='{}' WHERE operation_id=?",
            (fixture.admission_request.lock_operation_id,),
        )
        fixture.connection.commit()
        blocked = _service_error(lambda: reconcile_publication(fixture))
        assert (blocked.status_code, blocked.error) == (
            409,
            "publication_integrity_blocked",
        )
        return fixture
    except BaseException:
        fixture.close()
        raise


def _entrypoint_call(
    fixture: ReceiptAdoptionFixture,
    entrypoint: str,
) -> Callable[[], object]:
    assert fixture.admission_request is not None
    assert fixture.publish_request is not None
    calls: dict[str, Callable[[], object]] = {
        "get": lambda: fixture.publication_writer.get(
            PUBLICATION_ID,
            verified_caller_kid=BOARD_KID,
            expected_actor_id=POSTER_ID,
        ),
        "reconcile": lambda: reconcile_publication(fixture),
        "admission_replay": lambda: fixture.publication_writer.admit(
            fixture.admission_request,
            verified_caller_kid=BOARD_KID,
        ),
        "command_replay": lambda: fixture.publication_writer.authorize_publish(
            fixture.publish_request,
            verified_caller_kid=BOARD_KID,
        ),
    }
    return calls[entrypoint]


def _assert_state_corrupt_without_bank_reads_or_mutation(
    fixture: ReceiptAdoptionFixture,
    entrypoint: str,
) -> None:
    before = full_snapshot(fixture.connection)
    bank_before = bank_snapshot(fixture.connection)
    with BankReadGuard(fixture.connection, deny=True) as bank_reads:
        error = _service_error(_entrypoint_call(fixture, entrypoint))
    assert (error.status_code, error.error) == (500, "publication_state_corrupt")
    assert bank_reads.reads == []
    assert full_snapshot(fixture.connection) == before
    assert bank_snapshot(fixture.connection) == bank_before


def _coherently_shift_public_deadlines(connection: sqlite3.Connection) -> None:
    timing = connection.execute(
        "SELECT bidding_cutoff_us,acceptance_cutoff_us FROM board_task_contracts WHERE task_id=?",
        (TASK_ID,),
    ).fetchone()
    publication = connection.execute(
        "SELECT creation_event_id,result_json FROM board_task_publications WHERE operation_id=?",
        (PUBLICATION_ID,),
    ).fetchone()
    assert timing is not None and publication is not None
    shifted_bidding_us = int(timing[0]) + 1_000_000
    shifted_acceptance_us = int(timing[1]) + 1_000_000
    shifted_bidding = fixed_utc_text(shifted_bidding_us)
    shifted_acceptance = fixed_utc_text(shifted_acceptance_us)
    result = json.loads(str(publication[1]))
    assert isinstance(result, dict)
    result.update(
        {
            "bidding_deadline": shifted_bidding,
            "acceptance_deadline": shifted_acceptance,
            "bidding_cutoff_us": shifted_bidding_us,
            "acceptance_cutoff_us": shifted_acceptance_us,
        }
    )
    event_payload = connection.execute(
        "SELECT payload FROM events WHERE event_id=?",
        (int(publication[0]),),
    ).fetchone()
    assert event_payload is not None
    payload = json.loads(str(event_payload[0]))
    assert isinstance(payload, dict)
    payload["bidding_deadline"] = shifted_bidding
    connection.execute(
        "UPDATE board_task_contracts SET bidding_cutoff_us=?,acceptance_cutoff_us=? "
        "WHERE task_id=?",
        (shifted_bidding_us, shifted_acceptance_us, TASK_ID),
    )
    connection.execute(
        "UPDATE board_tasks SET bidding_deadline=? WHERE task_id=?",
        (shifted_bidding, TASK_ID),
    )
    connection.execute(
        "UPDATE events SET payload=? WHERE event_id=?",
        (_canonical(payload), int(publication[0])),
    )
    connection.execute(
        "UPDATE board_task_publications SET result_json=? WHERE operation_id=?",
        (_canonical(result), PUBLICATION_ID),
    )
    connection.commit()


def test_unicode_publication_uses_exact_utf8_canonical_bytes_and_replays(
    tmp_path: Path,
) -> None:
    title = "Résumé 世界"
    spec = "Return Δ evidence without escaping the signed text."
    fixture = prepare_authorized_publication(
        tmp_path / "unicode-publication.sqlite3",
        admission_overrides={"title": title, "spec": spec},
    )
    try:
        lock_publication(fixture)
        published = reconcile_publication(fixture)
        assert published["publication_state"] == "published"
        result = published["result"]
        assert isinstance(result, dict)
        assert result["title"] == title
        assert result["spec"] == spec
        stored = fixture.connection.execute(
            "SELECT result_json,creation_event_id FROM board_task_publications "
            "WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert stored is not None
        event = fixture.connection.execute(
            "SELECT payload FROM events WHERE event_id=?",
            (int(stored[1]),),
        ).fetchone()
        assert event is not None
        expected_event_payload = _canonical(
            {
                "bidding_deadline": result["bidding_deadline"],
                "reward": result["reward"],
                "title": title,
            }
        )
        assert str(event[0]) == expected_event_payload
        assert str(stored[0]) == _canonical(result)
        before = full_snapshot(fixture.connection)
        bank_before = bank_snapshot(fixture.connection)
        with BankReadGuard(fixture.connection, deny=True) as bank_reads:
            fetched = fixture.publication_writer.get(
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
            replayed = reconcile_publication(fixture)
        assert bank_reads.reads == []
        assert fetched == published
        assert replayed == published
        assert full_snapshot(fixture.connection) == before
        assert bank_snapshot(fixture.connection) == bank_before
    finally:
        fixture.close()


@pytest.mark.parametrize("entrypoint", _ENTRYPOINTS)
def test_coherent_deadline_shift_cannot_replace_signed_publication_terms(
    tmp_path: Path,
    entrypoint: str,
) -> None:
    fixture = _published_fixture(tmp_path / f"coherent-deadline-{entrypoint}.sqlite3")
    try:
        _coherently_shift_public_deadlines(fixture.connection)
        _assert_state_corrupt_without_bank_reads_or_mutation(fixture, entrypoint)
    finally:
        fixture.close()


def test_after_trigger_public_graph_corruption_rolls_back_materialization(
    tmp_path: Path,
) -> None:
    fixture = prepare_authorized_publication(tmp_path / "after-published.sqlite3")
    try:
        lock_publication(fixture)
        fixture.connection.execute(
            "CREATE TRIGGER mp09_corrupt_after_publish "
            "AFTER UPDATE ON board_task_publications WHEN NEW.state='published' "
            "BEGIN UPDATE board_tasks SET title=title||' corrupt' WHERE task_id=NEW.task_id; END"
        )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        bank_before = bank_snapshot(fixture.connection)
        error = _service_error(lambda: reconcile_publication(fixture))
        assert (error.status_code, error.error) == (
            500,
            "publication_persistence_failed",
        )
        assert full_snapshot(fixture.connection) == before
        assert bank_snapshot(fixture.connection) == bank_before
    finally:
        fixture.close()


def test_after_trigger_corrupt_block_rolls_back_and_never_claims_409(tmp_path: Path) -> None:
    fixture = prepare_authorized_publication(tmp_path / "after-block.sqlite3")
    try:
        lock_publication(fixture)
        assert fixture.admission_request is not None
        fixture.connection.execute(
            "UPDATE bank_operation_receipts SET result_json='{}' WHERE operation_id=?",
            (fixture.admission_request.lock_operation_id,),
        )
        fixture.connection.execute(
            "CREATE TRIGGER mp09_corrupt_after_block "
            "AFTER UPDATE ON board_task_publications WHEN NEW.state='blocked_integrity' "
            "BEGIN UPDATE board_task_publications SET error_json='{}' "
            "WHERE operation_id=NEW.operation_id; END"
        )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        bank_before = bank_snapshot(fixture.connection)
        error = _service_error(lambda: reconcile_publication(fixture))
        assert (error.status_code, error.error) == (
            500,
            "publication_persistence_failed",
        )
        assert full_snapshot(fixture.connection) == before
        assert bank_snapshot(fixture.connection) == bank_before
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("corruption", "sql", "parameters"),
    [
        (
            "error_shape",
            "UPDATE board_task_publications SET error_json=? WHERE operation_id=?",
            (
                _canonical(
                    {
                        "error": "publication_integrity_blocked",
                        "message": "Wrong persisted message",
                        "details": {"reason": "not_an_allowed_reason"},
                        "unexpected": True,
                    }
                ),
                PUBLICATION_ID,
            ),
        ),
        (
            "completion_facts",
            "UPDATE board_task_publications SET escrow_id=?,published_at_us=? WHERE operation_id=?",
            (ESCROW_ID, PUBLISHED_US, PUBLICATION_ID),
        ),
    ],
)
def test_persisted_corrupt_block_fails_closed_without_bank_reads(
    tmp_path: Path,
    corruption: str,
    sql: str,
    parameters: tuple[object, ...],
) -> None:
    fixture = _blocked_fixture(tmp_path / f"corrupt-block-{corruption}.sqlite3")
    try:
        if corruption == "completion_facts":
            fixture.connection.execute("PRAGMA ignore_check_constraints=ON")
        fixture.connection.execute(sql, parameters)
        fixture.connection.commit()
        if corruption == "completion_facts":
            fixture.connection.execute("PRAGMA ignore_check_constraints=OFF")
        _assert_state_corrupt_without_bank_reads_or_mutation(fixture, "reconcile")
    finally:
        fixture.close()


@pytest.mark.parametrize("entrypoint", _ENTRYPOINTS)
def test_noncanonical_published_result_bytes_fail_closed(
    tmp_path: Path,
    entrypoint: str,
) -> None:
    fixture = _published_fixture(tmp_path / f"noncanonical-result-{entrypoint}.sqlite3")
    try:
        row = fixture.connection.execute(
            "SELECT result_json FROM board_task_publications WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert row is not None
        value = json.loads(str(row[0]))
        fixture.connection.execute(
            "UPDATE board_task_publications SET result_json=? WHERE operation_id=?",
            (json.dumps(value, ensure_ascii=False, indent=1), PUBLICATION_ID),
        )
        fixture.connection.commit()
        _assert_state_corrupt_without_bank_reads_or_mutation(fixture, entrypoint)
    finally:
        fixture.close()


@pytest.mark.parametrize("entrypoint", _ENTRYPOINTS)
def test_changed_stored_lock_request_hash_fails_closed_without_bank_reads(
    tmp_path: Path,
    entrypoint: str,
) -> None:
    fixture = _published_fixture(tmp_path / f"changed-lock-hash-{entrypoint}.sqlite3")
    try:
        row = fixture.connection.execute(
            "SELECT lock_request_sha256 FROM board_task_publications WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert row is not None and str(row[0]) != "f" * 64
        fixture.connection.execute(
            "UPDATE board_task_publications SET lock_request_sha256=? WHERE operation_id=?",
            ("f" * 64, PUBLICATION_ID),
        )
        fixture.connection.commit()
        _assert_state_corrupt_without_bank_reads_or_mutation(fixture, entrypoint)
    finally:
        fixture.close()


@pytest.mark.parametrize("entrypoint", _ENTRYPOINTS)
def test_noncanonical_block_error_bytes_fail_closed(
    tmp_path: Path,
    entrypoint: str,
) -> None:
    fixture = _blocked_fixture(tmp_path / f"noncanonical-error-{entrypoint}.sqlite3")
    try:
        row = fixture.connection.execute(
            "SELECT error_json FROM board_task_publications WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert row is not None
        value = json.loads(str(row[0]))
        fixture.connection.execute(
            "UPDATE board_task_publications SET error_json=? WHERE operation_id=?",
            (json.dumps(value, ensure_ascii=False, indent=1), PUBLICATION_ID),
        )
        fixture.connection.commit()
        _assert_state_corrupt_without_bank_reads_or_mutation(fixture, entrypoint)
    finally:
        fixture.close()
