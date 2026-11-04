"""Simulated Bank replays cannot roll back a concurrent Gateway finalization."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import TYPE_CHECKING, Any

import pytest
from db_gateway_service.services.task_operation_writer import TaskOperationWriter

from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from db_gateway_service.task_operation_requests import TaskOperationRequest

pytestmark = pytest.mark.unit
_TASK_ID = "t-00000000-0000-4000-8000-000000000901"


def test_bank_replay_waits_for_gateway_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hold a real uncommitted event while another thread replays the Bank effect."""
    event_inserted = Event()
    allow_finalize = Event()
    replay_started = Event()
    replay_accessed_connection = Event()
    original_insert = TaskOperationWriter._insert_event

    def hold_event(cursor: sqlite3.Cursor, request: TaskOperationRequest, now_us: int) -> int:
        event_id = original_insert(cursor, request, now_us)
        event_inserted.set()
        assert allow_finalize.wait(5), "Finalization was not released by the test"
        return event_id

    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(_TASK_ID, TaskTerms(100, 1, 1, 1, 1))
        scenario.clock.at_seconds(5)
        evaluator = scenario.evaluator(None)
        task = scenario.task(_TASK_ID)
        original_read = scenario.store.reader.get_escrow

        def observe_bank_read(escrow_id: str) -> dict[str, Any] | None:
            if event_inserted.is_set():
                replay_accessed_connection.set()
            return original_read(escrow_id)

        def replay_bank(arguments: dict[str, Any]) -> dict[str, Any]:
            replay_started.set()
            return asyncio.run(scenario.persist_release(**arguments))

        monkeypatch.setattr(TaskOperationWriter, "_insert_event", staticmethod(hold_event))
        monkeypatch.setattr(scenario.store.reader, "get_escrow", observe_bank_read)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                evaluation = pool.submit(asyncio.run, evaluator.evaluate_deadline(dict(task)))
                try:
                    assert event_inserted.wait(5), "Finalization did not insert its event"
                    arguments = dict(scenario.bank.escrow_release.await_args.kwargs)
                    replay = pool.submit(replay_bank, arguments)
                    assert replay_started.wait(5), "Bank replay did not start"
                    # The transaction is deliberately held, so a second connection user
                    # must wait. This observes behavior rather than a particular lock API.
                    interleaved = replay_accessed_connection.wait(1)
                finally:
                    allow_finalize.set()
                assert evaluation.result(timeout=5)["status"] == "expired"
                replay.result(timeout=5)
                assert not interleaved, "Bank replay accessed an unfinished Gateway transaction"
        finally:
            evaluator.close()
        scenario.assert_settlement(
            _TASK_ID,
            command="expire_bidding",
            status="expired",
            recipient=scenario.poster_id,
            event_type="task.expired",
        )
