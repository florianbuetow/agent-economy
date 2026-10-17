"""A stored ruling accepts only an exact retry of its financial and textual terms."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.task_manager import TaskManager
from task_board_service.services.task_ruling import TaskRulingCoordinator
from tests.fakes.in_memory_task_store import InMemoryTaskStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def stored_ruling(tmp_path: Path) -> Iterator[tuple[TaskRulingCoordinator, Mock]]:
    store = InMemoryTaskStore(str(tmp_path / "ruling.db"))
    store.insert_task(
        {
            "task_id": "t-one",
            "poster_id": "a-poster",
            "title": "Task",
            "spec": "Accepted specification",
            "reward": 100,
            "bidding_deadline_seconds": 60,
            "deadline_seconds": 120,
            "review_deadline_seconds": 180,
            "status": "ruled",
            "escrow_id": "esc-one",
            "bid_count": 1,
            "worker_id": "a-worker",
            "accepted_bid_id": "bid-one",
            "created_at": "2026-10-17T10:00:00Z",
            "accepted_at": "2026-10-17T10:01:00Z",
            "submitted_at": "2026-10-17T10:02:00Z",
            "approved_at": None,
            "cancelled_at": None,
            "disputed_at": "2026-10-17T10:03:00Z",
            "dispute_reason": "Incomplete work",
            "dispute_id": "dispute-one",
            "ruling_id": "ruling-one",
            "ruled_at": "2026-10-17T10:04:00Z",
            "worker_pct": 50,
            "ruling_summary": "Split ruling",
            "expired_at": None,
            "escrow_pending": 0,
        }
    )
    manager = Mock(spec=TaskManager)
    manager.store = store
    manager.platform_agent_id = "a-platform"
    manager.token_validator = AsyncMock()
    manager.escrow_coordinator = AsyncMock()
    try:
        yield TaskRulingCoordinator(manager), manager
    finally:
        store.close()


def _payload(worker_pct: object, summary: str, ruling_id: str) -> dict[str, Any]:
    return {
        "action": "record_ruling",
        "_signer_id": "a-platform",
        "task_id": "t-one",
        "ruling_id": ruling_id,
        "worker_pct": worker_pct,
        "ruling_summary": summary,
    }


def _assert_no_effect(manager: Mock, original: dict[str, Any]) -> None:
    assert manager.store.get_task("t-one") == original
    manager.escrow_coordinator.release_ruling_escrow.assert_not_awaited()
    manager.escrow_coordinator.split_ruling_escrow.assert_not_awaited()


async def test_identical_stored_ruling_replay_returns_original_without_settlement(
    stored_ruling: tuple[TaskRulingCoordinator, Mock],
) -> None:
    coordinator, manager = stored_ruling
    original = manager.store.get_task("t-one")
    manager.token_validator.validate_platform_jws_token.return_value = _payload(
        50, "Split ruling", "ruling-one"
    )
    result = await coordinator.record_ruling("t-one", "verified-platform-token")
    assert result["status"] == "ruled"
    assert result["ruling_id"] == "ruling-one"
    assert result["worker_pct"] == 50
    assert result["ruling_summary"] == "Split ruling"
    assert result["ruled_at"] == original["ruled_at"]
    _assert_no_effect(manager, original)


@pytest.mark.parametrize(
    ("worker_pct", "summary"),
    [(0, "Split ruling"), (100, "Split ruling"), (49, "Split ruling"), (50, "Changed verdict")],
)
async def test_changed_stored_ruling_terms_conflict_without_effect(
    stored_ruling: tuple[TaskRulingCoordinator, Mock], worker_pct: int, summary: str
) -> None:
    coordinator, manager = stored_ruling
    original = manager.store.get_task("t-one")
    manager.token_validator.validate_platform_jws_token.return_value = _payload(
        worker_pct, summary, "ruling-one"
    )
    with pytest.raises(ServiceError) as error:
        await coordinator.record_ruling("t-one", "verified-platform-token")
    assert (error.value.status_code, error.value.error) == (409, "ruling_conflict")
    _assert_no_effect(manager, original)


@pytest.mark.parametrize("worker_pct", [True, False, None, "50", 50.0, -1, 101])
async def test_invalid_stored_ruling_percentage_is_not_an_exact_retry(
    stored_ruling: tuple[TaskRulingCoordinator, Mock], worker_pct: object
) -> None:
    coordinator, manager = stored_ruling
    original = manager.store.get_task("t-one")
    manager.token_validator.validate_platform_jws_token.return_value = _payload(
        worker_pct, "Split ruling", "ruling-one"
    )
    with pytest.raises(ServiceError) as error:
        await coordinator.record_ruling("t-one", "verified-platform-token")
    assert (error.value.status_code, error.value.error) == (400, "invalid_worker_pct")
    _assert_no_effect(manager, original)


async def test_other_ruling_preserves_status_precedence_and_stored_terms(
    stored_ruling: tuple[TaskRulingCoordinator, Mock],
) -> None:
    coordinator, manager = stored_ruling
    original = manager.store.get_task("t-one")
    manager.token_validator.validate_platform_jws_token.return_value = _payload(
        "invalid", "Different ruling", "ruling-two"
    )
    with pytest.raises(ServiceError) as error:
        await coordinator.record_ruling("t-one", "verified-platform-token")
    assert (error.value.status_code, error.value.error) == (409, "invalid_status")
    _assert_no_effect(manager, original)
