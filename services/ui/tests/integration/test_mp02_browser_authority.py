"""MP-02 browser authority checks for operator proxy mutations."""

from __future__ import annotations

from typing import Any

import pytest

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration


class _RecordingOperator:
    agent_id = "a-mp02-operator"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def approve_task(self, **kwargs: str) -> dict[str, Any]:
        self.calls.append(("approve", kwargs))
        return {"status": "approved"}

    async def accept_bid(self, **kwargs: str) -> dict[str, Any]:
        self.calls.append(("accept", kwargs))
        return {"status": "accepted"}

    async def close(self) -> None:
        return None


@pytest.mark.parametrize(
    "path",
    [
        "/api/proxy/tasks/t-task8/approve",
        "/api/proxy/tasks/t-task2/bids/bid-3/accept",
    ],
)
async def test_off_origin_bodyless_write_is_rejected_before_operator(
    client,
    path: str,
) -> None:
    operator = _RecordingOperator()
    get_app_state().user_agent = operator

    response = await client.post(path, headers={"Origin": "https://attacker.invalid"})

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden_origin"
    assert operator.calls == []


async def test_malicious_host_is_rejected_before_operator(client) -> None:
    operator = _RecordingOperator()
    get_app_state().user_agent = operator

    response = await client.post(
        "/api/proxy/tasks/t-task8/approve",
        headers={
            "Host": "attacker.invalid",
            "Origin": "http://localhost",
            "X-ATE-CSRF": "local-operator",
        },
    )

    assert response.status_code == 400
    assert operator.calls == []


async def test_same_origin_write_requires_csrf_header(client) -> None:
    operator = _RecordingOperator()
    get_app_state().user_agent = operator
    client.headers.pop("X-ATE-CSRF")

    response = await client.post(
        "/api/proxy/tasks/t-task8/approve",
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 403
    assert response.json()["error"] == "csrf_failed"
    assert operator.calls == []
