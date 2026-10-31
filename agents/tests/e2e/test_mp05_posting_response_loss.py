"""MP-05 caller retry after a committed task-posting response is lost."""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from base_agent.agent import BaseAgent

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_INITIAL_BALANCE = 500
_REWARD = 100
_TASK_TITLE = "MP-05 committed posting response-loss proof"
_TASK_SPEC = "Publish this one logical caller task exactly once after an ambiguous response."


@pytest.fixture(autouse=True)
def _require_disposable_runtime() -> None:
    """Refuse accidental execution against ordinary service URLs or state."""
    assert os.environ.get("ISOLATED_RUNTIME_ID"), "Run through isolated_test_runtime.py"
    assert os.environ.get("ISOLATED_RUNTIME_DIR"), "An isolated runtime directory is required"


class _DropFirstCommittedPostingResponse:
    """Return-seam fault that drops one real successful POST /tasks response."""

    def __init__(
        self,
        request: Callable[..., Awaitable[httpx.Response]],
        task_board_url: str,
    ) -> None:
        self._request = request
        self._posting_url = f"{task_board_url}/tasks"
        self.drop_count = 0
        self.committed_status_code: int | None = None
        self.committed_body: dict[str, Any] | None = None

    async def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = await self._request(method, url, **kwargs)
        if method == "POST" and url == self._posting_url and self.drop_count == 0:
            response.raise_for_status()
            body = response.json()
            assert isinstance(body, dict)
            self.drop_count += 1
            self.committed_status_code = response.status_code
            self.committed_body = body
            raise httpx.ReadTimeout(
                "MP-05 injected loss after the real posting response committed",
                request=response.request,
            )
        return response


def _posting_arguments() -> dict[str, object]:
    return {
        "title": _TASK_TITLE,
        "spec": _TASK_SPEC,
        "reward": _REWARD,
        "bidding_deadline_seconds": 3_600,
        "acceptance_window_seconds": 3_600,
        "execution_deadline_seconds": 7_200,
        "review_deadline_seconds": 3_600,
    }


def _escrow_locks(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [transaction for transaction in transactions if transaction["type"] == "escrow_lock"]


async def test_two_distinct_postings_with_identical_terms_keep_distinct_identities(
    make_funded_agent: Any,
) -> None:
    """Identical content does not collapse two deliberate logical postings."""
    async with AsyncExitStack() as resources:
        poster: BaseAgent = await make_funded_agent(
            name="MP05 distinct-postings control",
            balance=_INITIAL_BALANCE,
        )
        resources.push_async_callback(poster.close)

        first_intent = poster.prepare_task_posting(**_posting_arguments())
        second_intent = poster.prepare_task_posting(**_posting_arguments())
        first_response = await poster.submit_task_posting(first_intent)
        second_response = await poster.submit_task_posting(second_intent)
        assert first_response.status_code == second_response.status_code == 201
        first = first_response.body["result"]
        second = second_response.body["result"]

        expected_task_ids = {first["task_id"], second["task_id"]}
        public_tasks = await poster.list_tasks(poster_id=poster.agent_id)
        locks = _escrow_locks(await poster.get_transactions())
        balance = await poster.get_balance()
        assert len(expected_task_ids) == 2
        assert {task["task_id"] for task in public_tasks} == expected_task_ids
        assert {lock["reference"] for lock in locks} == expected_task_ids
        assert len(locks) == 2
        assert balance["balance"] == _INITIAL_BALANCE - (2 * _REWARD)


async def test_caller_retry_after_lost_committed_response_keeps_one_task_and_one_lock(
    make_funded_agent: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One logical posting remains one public task and one reward debit."""
    async with AsyncExitStack() as resources:
        poster: BaseAgent = await make_funded_agent(
            name="MP05 response-loss poster",
            balance=_INITIAL_BALANCE,
        )
        resources.push_async_callback(poster.close)
        fault = _DropFirstCommittedPostingResponse(
            poster._http.request,
            poster.config.task_board_url,
        )
        monkeypatch.setattr(poster._http, "request", fault)
        intent = poster.prepare_task_posting(**_posting_arguments())

        with pytest.raises(httpx.ReadTimeout, match="after the real posting response committed"):
            await poster.submit_task_posting(intent)

        assert fault.drop_count == 1
        assert fault.committed_status_code == 201
        assert fault.committed_body is not None
        committed_task_id = fault.committed_body["task_id"]
        first_public_tasks = await poster.list_tasks(poster_id=poster.agent_id)
        first_balance = await poster.get_balance()
        first_locks = _escrow_locks(await poster.get_transactions())
        assert [task["task_id"] for task in first_public_tasks] == [committed_task_id]
        assert [lock["reference"] for lock in first_locks] == [committed_task_id]
        assert first_balance["balance"] == _INITIAL_BALANCE - _REWARD

        retry_response = await poster.submit_task_posting(intent)
        assert retry_response.status_code == 201
        assert retry_response.body == fault.committed_body
        retry = retry_response.body["result"]

        public_tasks = await poster.list_tasks(poster_id=poster.agent_id)
        balance = await poster.get_balance()
        locks = _escrow_locks(await poster.get_transactions())
        observed = {
            "retry_task_id": retry["task_id"],
            "public_task_ids": [task["task_id"] for task in public_tasks],
            "escrow_task_ids": [lock["reference"] for lock in locks],
            "balance": balance["balance"],
        }
        assert observed == {
            "retry_task_id": committed_task_id,
            "public_task_ids": [committed_task_id],
            "escrow_task_ids": [committed_task_id],
            "balance": _INITIAL_BALANCE - _REWARD,
        }
