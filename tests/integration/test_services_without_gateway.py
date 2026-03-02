"""Integration tests: DB Gateway endpoints fail clearly when gateway is offline."""

from __future__ import annotations

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

DEAD_GATEWAY_URL = "http://localhost:19999"


async def test_identity_endpoint_unreachable() -> None:
    async with httpx.AsyncClient(base_url=DEAD_GATEWAY_URL, timeout=2.0) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post(
                "/identity/agents",
                json={
                    "agent_id": "a-test",
                    "name": "Test Agent",
                    "public_key": "ed25519:AAAA",
                    "registered_at": "2026-03-02T06:30:00Z",
                    "event": {
                        "event_source": "identity",
                        "event_type": "agent.registered",
                        "timestamp": "2026-03-02T06:30:00Z",
                        "summary": "Test agent registered",
                        "payload": "{}",
                    },
                },
            )


async def test_bank_endpoint_unreachable() -> None:
    async with httpx.AsyncClient(base_url=DEAD_GATEWAY_URL, timeout=2.0) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post(
                "/bank/accounts",
                json={
                    "account_id": "a-test",
                    "balance": 0,
                    "created_at": "2026-03-02T06:30:00Z",
                    "event": {
                        "event_source": "bank",
                        "event_type": "account.created",
                        "timestamp": "2026-03-02T06:30:00Z",
                        "summary": "Account created",
                        "payload": "{}",
                    },
                },
            )


async def test_board_endpoint_unreachable() -> None:
    async with httpx.AsyncClient(base_url=DEAD_GATEWAY_URL, timeout=2.0) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("/board/tasks/count")


async def test_reputation_endpoint_unreachable() -> None:
    async with httpx.AsyncClient(base_url=DEAD_GATEWAY_URL, timeout=2.0) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("/reputation/feedback/count")


async def test_court_endpoint_unreachable() -> None:
    async with httpx.AsyncClient(base_url=DEAD_GATEWAY_URL, timeout=2.0) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("/court/claims/count")
