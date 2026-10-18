"""Native ruling replay preserves its frozen terms and actual settlement evidence."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.services.task_manager import TaskManager
from task_board_service.services.task_ruling import TaskRulingCoordinator
from tests.fakes.native_task_fixture import native_task_context
from tests.unit.test_escrow_coordinator import prepare_submitted

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from tests.fakes.native_task_fixture import NativeTaskFixture

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
RULING_OPERATION_ID = "op-00000000-0000-4000-8000-000000000903"


async def persist_native_split(
    scenario: NativeTaskFixture,
    escrow_id: str,
    worker_account_id: str,
    poster_account_id: str,
    worker_pct: int,
    operation_id: str,
    source_reference: str,
) -> dict[str, Any]:
    escrow = scenario.store.reader.get_escrow(escrow_id)
    assert escrow is not None
    return scenario.store.writer.escrow_split(
        {
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "worker_account_id": worker_account_id,
            "poster_account_id": poster_account_id,
            "worker_pct": worker_pct,
            "source_reference": source_reference,
            "resolved_at": scenario.clock.iso(),
            "worker_tx_id": f"tx-worker-{operation_id}",
            "poster_tx_id": f"tx-poster-{operation_id}",
            "event": scenario.event("central-bank", "escrow.split", str(escrow["task_id"])),
        }
    )


def ruling_snapshot(scenario: NativeTaskFixture) -> tuple[Any, ...]:
    connection = scenario.store.writer.connection
    return (
        scenario.task("t-one"),
        tuple(
            tuple(row)
            for row in connection.execute("SELECT * FROM bank_accounts ORDER BY account_id")
        ),
        tuple(
            tuple(row)
            for row in connection.execute("SELECT * FROM bank_transactions ORDER BY tx_id")
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM bank_operation_receipts ORDER BY operation_id"
            )
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM board_task_operations ORDER BY operation_id"
            )
        ),
        tuple(tuple(row) for row in connection.execute("SELECT * FROM events ORDER BY event_id")),
    )


@dataclass
class StoredRuling:
    scenario: NativeTaskFixture
    coordinator: TaskRulingCoordinator
    validator: AsyncMock
    payload: dict[str, Any]
    result: dict[str, Any]


@pytest.fixture
async def stored_ruling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[StoredRuling]:
    with native_task_context(tmp_path, monkeypatch) as scenario:
        prepare_submitted(scenario, "t-one")
        scenario.dispute("t-one", rebuttal_seconds=3600)
        scenario.bank.escrow_split = AsyncMock(side_effect=partial(persist_native_split, scenario))
        validator = AsyncMock()
        manager = Mock(spec=TaskManager)
        manager.store = scenario.store
        manager.platform_agent_id = "a-platform"
        manager.token_validator = validator
        manager.escrow_coordinator = scenario.escrow
        coordinator = TaskRulingCoordinator(cast("TaskManager", manager))
        payload = {
            "action": "record_ruling",
            "_signer_id": "a-platform",
            "task_id": "t-one",
            "operation_id": RULING_OPERATION_ID,
            "expected_lifecycle_version": 3,
            "ruling_id": "clm-t-one",
            "worker_pct": 50,
            "ruling_summary": "Split ruling",
        }
        validator.validate_platform_jws_token.return_value = payload
        result = await coordinator.record_ruling("t-one", "verified-platform-token")
        assert result["status"] == "ruled"
        assert result["operation"]["operation_state"] == "completed"
        assert (
            scenario.store.writer.connection.execute(
                "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
                (RULING_OPERATION_ID,),
            ).fetchone()[0]
            == 1
        )
        scenario.bank.escrow_split.reset_mock()
        scenario.bank.escrow_release.reset_mock()
        yield StoredRuling(scenario, coordinator, validator, payload, result)


def _assert_no_effect(fixture: StoredRuling, original: tuple[Any, ...]) -> None:
    assert ruling_snapshot(fixture.scenario) == original
    fixture.scenario.bank.escrow_release.assert_not_awaited()
    fixture.scenario.bank.escrow_split.assert_not_awaited()


async def test_identical_stored_ruling_replay_returns_original_without_settlement(
    stored_ruling: StoredRuling,
) -> None:
    original = ruling_snapshot(stored_ruling.scenario)
    result = await stored_ruling.coordinator.record_ruling("t-one", "verified-platform-token")
    assert result["status"] == "ruled"
    assert result["ruling_id"] == "clm-t-one"
    assert result["worker_pct"] == 50
    assert result["ruling_summary"] == "Split ruling"
    assert result["ruled_at"] == stored_ruling.result["ruled_at"]
    assert result["operation"] == stored_ruling.result["operation"]
    _assert_no_effect(stored_ruling, original)


@pytest.mark.parametrize(
    ("worker_pct", "summary"),
    [(0, "Split ruling"), (100, "Split ruling"), (49, "Split ruling"), (50, "Changed verdict")],
)
async def test_changed_stored_ruling_terms_conflict_without_effect(
    stored_ruling: StoredRuling,
    worker_pct: int,
    summary: str,
) -> None:
    original = ruling_snapshot(stored_ruling.scenario)
    stored_ruling.validator.validate_platform_jws_token.return_value = {
        **stored_ruling.payload,
        "worker_pct": worker_pct,
        "ruling_summary": summary,
    }
    with pytest.raises(ServiceError) as error:
        await stored_ruling.coordinator.record_ruling("t-one", "verified-platform-token")
    assert (error.value.status_code, error.value.error) == (409, "operation_id_conflict")
    _assert_no_effect(stored_ruling, original)


@pytest.mark.parametrize("worker_pct", [True, False, None, "50", 50.0, -1, 101])
async def test_invalid_stored_ruling_percentage_is_not_an_exact_retry(
    stored_ruling: StoredRuling,
    worker_pct: object,
) -> None:
    original = ruling_snapshot(stored_ruling.scenario)
    stored_ruling.validator.validate_platform_jws_token.return_value = {
        **stored_ruling.payload,
        "worker_pct": worker_pct,
    }
    with pytest.raises(ServiceError) as error:
        await stored_ruling.coordinator.record_ruling("t-one", "verified-platform-token")
    assert (error.value.status_code, error.value.error) == (400, "invalid_worker_pct")
    _assert_no_effect(stored_ruling, original)


async def test_malformed_other_ruling_fails_shape_validation_without_effect(
    stored_ruling: StoredRuling,
) -> None:
    original = ruling_snapshot(stored_ruling.scenario)
    stored_ruling.validator.validate_platform_jws_token.return_value = {
        **stored_ruling.payload,
        "operation_id": operation_id_for("ruling-fixture", ("fresh-malformed",)),
        "expected_lifecycle_version": 4,
        "ruling_id": "clm-other",
        "worker_pct": "invalid",
    }
    with pytest.raises(ServiceError) as error:
        await stored_ruling.coordinator.record_ruling("t-one", "verified-platform-token")
    assert (error.value.status_code, error.value.error) == (400, "invalid_worker_pct")
    _assert_no_effect(stored_ruling, original)


async def test_other_ruling_preserves_status_precedence_and_stored_terms(
    stored_ruling: StoredRuling,
) -> None:
    original = ruling_snapshot(stored_ruling.scenario)
    stored_ruling.validator.validate_platform_jws_token.return_value = {
        **stored_ruling.payload,
        "operation_id": operation_id_for("ruling-fixture", ("fresh-valid",)),
        "expected_lifecycle_version": 4,
        "ruling_id": "clm-other",
        "worker_pct": 50,
    }
    with pytest.raises(ServiceError) as error:
        await stored_ruling.coordinator.record_ruling("t-one", "verified-platform-token")
    assert (error.value.status_code, error.value.error) == (409, "invalid_status")
    _assert_no_effect(stored_ruling, original)
