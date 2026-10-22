"""Bounded recovery makes progress without obtaining authority for poster debits."""

from __future__ import annotations

import asyncio
import importlib
import threading
from typing import Any
from unittest.mock import AsyncMock

import pytest
from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse
from service_commons.exceptions import ServiceError

pytestmark = pytest.mark.unit


class RecoveryStore:
    """Controlled pending work with separate read, reconciliation, and money effects."""

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {"": {"items": [], "next_after": None}}
        self.visited: list[str] = []
        self.cursors: list[tuple[str, int]] = []
        self.failed: set[str] = set()
        self.refunded = False

    def list_posting_recovery(self, after: str, limit: int) -> dict[str, Any]:
        self.cursors.append((after, limit))
        return self.pages[after]

    def reconcile_task_publication(
        self,
        operation_id: str,
        intent: TaskPostingIntent | None,
    ) -> TaskPostingResponse:
        del intent
        self.visited.append(operation_id)
        if operation_id in self.failed:
            raise ServiceError("publication_integrity_blocked", "Blocked", 409, {})
        return TaskPostingResponse(202, {"publication_state": "awaiting_lock"})

    def reconcile_legacy_posting_observation(self, observation_id: str) -> dict[str, Any]:
        self.visited.append(observation_id)
        return {
            "observation_state": "refunded" if self.refunded else "refund_pending",
            "refund_operation_id": "op-refund",
            "refund_request": {
                "request_version": 1,
                "operation_kind": "escrow_release",
                "escrow_id": "esc-orphan",
                "recipient_account_id": "a-original-payer",
                "source_reference": "task:t-original:posting_refund:esc-orphan",
            },
        }


def item(kind: str, identity: str) -> dict[str, str]:
    return {"cursor": f"{kind}:{identity}", "kind": kind, "identity": identity}


def recovery(store: RecoveryStore, bank: AsyncMock) -> Any:
    try:
        module = importlib.import_module("task_board_service.services.posting_recovery")
    except ModuleNotFoundError as exc:
        if exc.name != "task_board_service.services.posting_recovery":
            raise
        pytest.fail("Missing startup and periodic posting recovery implementation")
    return module.PostingRecovery(store=store, bank=bank, batch_size=2, diagnostic_limit=2)


async def test_failed_item_does_not_starve_later_pages_or_new_sweeps() -> None:
    store = RecoveryStore()
    store.pages = {
        "": {"items": [item("native", "one"), item("native", "two")], "next_after": "native:two"},
        "native:two": {"items": [item("native", "three")], "next_after": None},
    }
    store.failed = {"one"}
    bank = AsyncMock()
    worker = recovery(store, bank)
    await worker.run_batch()
    assert store.visited == ["one", "two"]
    await worker.run_batch()
    assert store.visited == ["one", "two", "three"]
    await worker.run_batch()
    assert store.cursors == [("", 2), ("native:two", 2), ("", 2)]
    assert store.visited == ["one", "two", "three", "one", "two"]
    assert bank.mock_calls == []


async def test_native_pending_work_never_dispatches_poster_debit() -> None:
    store = RecoveryStore()
    store.pages[""] = {"items": [item("native", "pending")], "next_after": None}
    bank = AsyncMock()
    worker = recovery(store, bank)
    await worker.run_batch()
    await worker.run_batch()
    assert store.visited == ["pending", "pending"]
    assert bank.mock_calls == []


@pytest.mark.parametrize("lost_response", [False, True])
async def test_refund_uses_saved_terms_and_reconciles_after_ambiguous_delivery(
    lost_response: bool,
) -> None:
    store = RecoveryStore()
    store.pages[""] = {"items": [item("legacy", "orphan")], "next_after": None}
    bank = AsyncMock()

    async def release(**_terms: object) -> dict[str, object]:
        store.refunded = True
        if lost_response:
            raise ServiceError("central_bank_unavailable", "Response lost", 502, {})
        return {"untrusted_transport_result": True}

    bank.escrow_release.side_effect = release
    worker = recovery(store, bank)
    await worker.run_batch()
    assert store.visited == ["orphan", "orphan"]
    bank.escrow_release.assert_awaited_once_with(
        escrow_id="esc-orphan",
        recipient_account_id="a-original-payer",
        operation_id="op-refund",
        source_reference="task:t-original:posting_refund:esc-orphan",
    )
    await worker.run_batch()
    assert bank.escrow_release.await_count == 1
    bank.escrow_lock.assert_not_called()


async def test_diagnostics_are_bounded_and_do_not_expose_exception_text() -> None:
    store = RecoveryStore()
    bank = AsyncMock()
    worker = recovery(store, bank)
    for identity in ("one", "two", "three"):
        store.pages[""] = {"items": [item("native", identity)], "next_after": None}
        store.failed.add(identity)
        await worker.run_batch()
    assert worker.diagnostics() == [
        {"cursor": "native:two", "error": "publication_integrity_blocked"},
        {"cursor": "native:three", "error": "publication_integrity_blocked"},
    ]


async def test_periodic_loop_runs_startup_batch_before_first_interval() -> None:
    store = RecoveryStore()
    worker = recovery(store, AsyncMock())
    first_batch = asyncio.Event()

    async def batch() -> None:
        first_batch.set()

    worker.run_batch = batch
    task = asyncio.create_task(worker.run_periodic(interval_seconds=3600))
    try:
        await asyncio.wait_for(first_batch.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_shutdown_drains_current_storage_call_before_returning() -> None:
    store = RecoveryStore()
    worker = recovery(store, AsyncMock())
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocked_page(after: str, limit: int) -> dict[str, Any]:
        assert (after, limit) == ("", 2)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=3)
        finished.set()
        return {"items": [], "next_after": None}

    store.list_posting_recovery = blocked_page  # type: ignore[method-assign]
    task = asyncio.create_task(worker.run_batch())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
