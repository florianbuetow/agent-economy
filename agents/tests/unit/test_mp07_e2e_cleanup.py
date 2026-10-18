"""Cleanup regressions for the MP-07 live E2E fixture wiring."""

from __future__ import annotations

from contextlib import ExitStack

import pytest

from tests.e2e.test_autonomous_acceptance import _close_agents_and_journals
from tests.e2e.test_unattended_economy import _close_clients_and_journals


class _ClosingClient:
    def __init__(self, name: str, events: list[str], failure: BaseException | None = None) -> None:
        self._name = name
        self._events = events
        self._failure = failure

    async def close(self) -> None:
        self._events.append(f"close:{self._name}")
        if self._failure is not None:
            raise self._failure


@pytest.mark.unit
async def test_autonomous_cleanup_awaits_every_client_before_releasing_lease() -> None:
    events: list[str] = []
    stack = ExitStack()
    stack.callback(events.append, "release:journal")
    clients = [
        _ClosingClient("first", events, RuntimeError("first close failed")),
        _ClosingClient("second", events),
    ]

    with pytest.raises(BaseExceptionGroup) as raised:
        await _close_agents_and_journals(clients, stack)  # type: ignore[arg-type]

    assert events == ["close:first", "close:second", "release:journal"]
    assert [str(error) for error in raised.value.exceptions] == ["first close failed"]


@pytest.mark.unit
async def test_unattended_cleanup_collects_all_failures_and_releases_lease_last() -> None:
    events: list[str] = []
    stack = ExitStack()
    stack.callback(events.append, "release:journal")
    llm_clients = [_ClosingClient("llm", events, RuntimeError("llm close failed"))]
    agents = [_ClosingClient("agent", events, RuntimeError("agent close failed"))]

    with pytest.raises(BaseExceptionGroup) as raised:
        await _close_clients_and_journals(
            llm_clients,  # type: ignore[arg-type]
            agents,  # type: ignore[arg-type]
            stack,
            prior_failures=[RuntimeError("background failed")],
        )

    assert events == ["close:llm", "close:agent", "release:journal"]
    assert [str(error) for error in raised.value.exceptions] == [
        "background failed",
        "llm close failed",
        "agent close failed",
    ]
