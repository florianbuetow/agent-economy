"""Ruling-trigger eligibility/retry with real disputed task and rebuttal history.

The trigger itself is mocked: these tests do not claim live judging or a ruling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from service_auth.platform import PlatformAgent

    from tests.fakes.native_task_fixture import NativeTaskFixture

pytestmark = pytest.mark.unit
_TERMS = TaskTerms(100, 3600, 600, 3600, 3600)


@pytest.fixture(name="native_tasks")
def native_tasks_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[NativeTaskFixture]:
    with native_task_context(tmp_path, monkeypatch) as scenario:
        yield scenario


def _disputed_task(native_tasks: NativeTaskFixture) -> None:
    native_tasks.open_task("t-wp06", _TERMS)
    native_tasks.bid("t-wp06", 90)
    native_tasks.accept("t-wp06", 90)
    native_tasks.submit("t-wp06")
    native_tasks.dispute("t-wp06", rebuttal_seconds=3600)


async def test_trigger_fires_when_rebuttal_submitted(native_tasks: NativeTaskFixture) -> None:
    _disputed_task(native_tasks)
    native_tasks.rebut("t-wp06")
    platform = AsyncMock()
    platform.trigger_ruling.return_value = {"status": "ruled"}
    result = await native_tasks.evaluator(cast("PlatformAgent", platform)).evaluate_deadline(
        native_tasks.task("t-wp06")
    )
    platform.trigger_ruling.assert_awaited_once_with("clm-t-wp06")
    assert result["status"] == "disputed"
    assert result["ruling_id"] is None


async def test_trigger_fires_when_rebuttal_deadline_passed(native_tasks: NativeTaskFixture) -> None:
    _disputed_task(native_tasks)
    native_tasks.clock.at_seconds(3600)
    platform = AsyncMock()
    platform.trigger_ruling.return_value = {"status": "ruled"}
    result = await native_tasks.evaluator(cast("PlatformAgent", platform)).evaluate_deadline(
        native_tasks.task("t-wp06")
    )
    platform.trigger_ruling.assert_awaited_once_with("clm-t-wp06")
    assert result["status"] == "disputed"
    assert result["ruling_id"] is None


async def test_trigger_not_fired_inside_open_window(native_tasks: NativeTaskFixture) -> None:
    _disputed_task(native_tasks)
    native_tasks.clock.at_seconds(1800)
    platform = AsyncMock()
    before = native_tasks.task("t-wp06")
    result = await native_tasks.evaluator(cast("PlatformAgent", platform)).evaluate_deadline(before)
    assert result == before
    platform.trigger_ruling.assert_not_awaited()


async def test_failed_trigger_is_retryable(native_tasks: NativeTaskFixture) -> None:
    _disputed_task(native_tasks)
    native_tasks.rebut("t-wp06")
    platform = AsyncMock()
    request = httpx.Request("POST", "http://court.invalid/disputes/clm-t-wp06/rule")
    response = httpx.Response(502, request=request)
    platform.trigger_ruling.side_effect = [
        httpx.HTTPStatusError("502", request=request, response=response),
        {"status": "ruled"},
    ]
    evaluator = native_tasks.evaluator(cast("PlatformAgent", platform))
    task = native_tasks.task("t-wp06")
    before_events = native_tasks.store.writer.get_total_events()
    result = await evaluator.evaluate_deadline(task)
    assert result == task
    assert result["status"] == "disputed"
    platform.trigger_ruling.assert_awaited_once_with("clm-t-wp06")
    retried = await evaluator.evaluate_deadline(result)
    assert retried == task
    assert platform.trigger_ruling.await_count == 2
    assert all(call.args == ("clm-t-wp06",) for call in platform.trigger_ruling.await_args_list)
    assert native_tasks.store.writer.get_total_events() == before_events


async def test_no_trigger_without_platform_agent(native_tasks: NativeTaskFixture) -> None:
    _disputed_task(native_tasks)
    native_tasks.rebut("t-wp06")
    task = native_tasks.task("t-wp06")
    before_events = native_tasks.store.writer.get_total_events()
    result = await native_tasks.evaluator(None).evaluate_deadline(task)
    assert result == task
    assert result["status"] == "disputed"
    assert native_tasks.store.writer.get_total_events() == before_events
    native_tasks.bank.escrow_release.assert_not_awaited()
