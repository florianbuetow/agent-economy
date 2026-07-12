"""Tests for GatewayClient — the async db-gateway HTTP client (GAP-E2/E3, T-082).

Every request goes through `httpx.MockTransport`, so no real socket is ever
opened. Coverage: connection-failure mapping (shared by every method, so tested
once), then each domain's happy path plus its distinguishing error path (a
409 conflict, a 404-returns-None contract, etc.).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from service_commons.exceptions import ServiceError

from service_clients.gateway import GatewayClient

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> GatewayClient:
    client = GatewayClient(base_url="http://gateway.invalid", timeout_seconds=1)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://gateway.invalid",
    )
    return client


def _json_response(status_code: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status_code, json=body)


# ---------------------------------------------------------------------------
# Connection failure mapping — shared by every method via BaseServiceClient
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConnectionFailureMapping:
    async def test_connect_error_maps_to_502_db_gateway_unavailable(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _client(handler)

        with pytest.raises(ServiceError) as excinfo:
            await client.get_agent("a-1")

        assert excinfo.value.error == "db_gateway_unavailable"
        assert excinfo.value.status_code == 502

    async def test_timeout_maps_to_502_db_gateway_unavailable(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        client = _client(handler)

        with pytest.raises(ServiceError) as excinfo:
            await client.get_account("a-1")

        assert excinfo.value.error == "db_gateway_unavailable"
        assert excinfo.value.status_code == 502


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdentity:
    async def test_register_agent_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/identity/agents"
            body = json.loads(request.content)
            assert body["agent_id"] == "a-1"
            assert body["event"]["event_type"] == "agent.registered"
            return _json_response(201, {"agent_id": "a-1", "event_id": 1})

        client = _client(handler)
        result = await client.register_agent(
            agent_id="a-1",
            name="Alice",
            public_key="ed25519:abc",
            registered_at="2026-03-01T09:00:00Z",
        )
        assert result["agent_id"] == "a-1"

    async def test_register_agent_conflict(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                409, {"error": "public_key_exists", "message": "already registered", "details": {}}
            )

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.register_agent(
                agent_id="a-1", name="Alice", public_key="ed25519:abc", registered_at="now"
            )
        assert excinfo.value.error == "public_key_exists"
        assert excinfo.value.status_code == 409

    async def test_get_agent_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            body = {"error": "not_found", "message": "no such agent", "details": {}}
            return _json_response(404, body)

        client = _client(handler)
        assert await client.get_agent("a-missing") is None

    async def test_list_agents(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"agents": [{"agent_id": "a-1"}, {"agent_id": "a-2"}]})

        client = _client(handler)
        agents = await client.list_agents()
        assert [a["agent_id"] for a in agents] == ["a-1", "a-2"]

    async def test_count_agents(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"count": 3})

        client = _client(handler)
        assert await client.count_agents() == 3


# ---------------------------------------------------------------------------
# Bank
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBank:
    async def test_create_account_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["account_id"] == "a-1"
            assert body["event"]["event_type"] == "account.created"
            return _json_response(201, {"account_id": "a-1", "event_id": 1})

        client = _client(handler)
        result = await client.create_account(
            account_id="a-1",
            created_at="2026-03-01T09:00:00Z",
            balance=0,
            initial_credit_data=None,
            agent_name="Alice",
        )
        assert result["account_id"] == "a-1"

    async def test_create_account_conflict(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                409, {"error": "account_exists", "message": "already exists", "details": {}}
            )

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.create_account(
                account_id="a-1",
                created_at="now",
                balance=0,
                initial_credit_data=None,
                agent_name="Alice",
            )
        assert excinfo.value.error == "account_exists"

    async def test_get_account_found(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"account_id": "a-1", "balance": 100, "created_at": "now"})

        client = _client(handler)
        result = await client.get_account("a-1")
        assert result is not None
        assert result["balance"] == 100

    async def test_get_account_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            body = {"error": "not_found", "message": "no such account", "details": {}}
            return _json_response(404, body)

        client = _client(handler)
        assert await client.get_account("a-missing") is None

    async def test_get_transactions(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/bank/accounts/a-1/transactions"
            return _json_response(200, {"transactions": [{"tx_id": "tx-1", "amount": 10}]})

        client = _client(handler)
        txs = await client.get_transactions("a-1")
        assert len(txs) == 1
        assert txs[0]["tx_id"] == "tx-1"

    async def test_count_accounts(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"count": 5})

        client = _client(handler)
        assert await client.count_accounts() == 5

    async def test_total_escrowed(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"total": 250})

        client = _client(handler)
        assert await client.total_escrowed() == 250

    async def test_get_escrow_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            body = {"error": "escrow_not_found", "message": "no", "details": {}}
            return _json_response(404, body)

        client = _client(handler)
        assert await client.get_escrow("esc-missing") is None

    async def test_credit_account(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/bank/credit"
            return _json_response(200, {"tx_id": "tx-1", "balance_after": 50})

        client = _client(handler)
        result = await client.credit_account(
            tx_id="tx-1", account_id="a-1", amount=50, reference="ref", timestamp="now"
        )
        assert result["balance_after"] == 50

    async def test_escrow_lock_insufficient_funds(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                402, {"error": "insufficient_funds", "message": "not enough", "details": {}}
            )

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.escrow_lock(
                escrow_id="esc-1",
                payer_account_id="a-1",
                amount=100,
                task_id="t-1",
                created_at="now",
                tx_id="tx-1",
            )
        assert excinfo.value.error == "insufficient_funds"
        assert excinfo.value.status_code == 402

    async def test_escrow_release_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/bank/escrow/release"
            return _json_response(200, {"escrow_id": "esc-1", "status": "released"})

        client = _client(handler)
        result = await client.escrow_release(
            escrow_id="esc-1",
            recipient_account_id="a-2",
            tx_id="tx-1",
            resolved_at="now",
            amount=100,
        )
        assert result["status"] == "released"

    async def test_escrow_split_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/bank/escrow/split"
            body = json.loads(request.content)
            assert body["worker_amount"] == 40
            assert body["poster_amount"] == 60
            return _json_response(200, {"escrow_id": "esc-1", "status": "split"})

        client = _client(handler)
        result = await client.escrow_split(
            escrow_id="esc-1",
            worker_account_id="a-worker",
            poster_account_id="a-poster",
            worker_tx_id="tx-w",
            poster_tx_id="tx-p",
            resolved_at="now",
            worker_amount=40,
            poster_amount=60,
        )
        assert result["status"] == "split"


# ---------------------------------------------------------------------------
# Reputation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReputation:
    async def test_submit_feedback_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/reputation/feedback"
            return _json_response(201, {"feedback_id": "fb-1", "visible": False})

        client = _client(handler)
        result = await client.submit_feedback({"feedback_id": "fb-1"})
        assert result["visible"] is False

    async def test_submit_feedback_conflict(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                409, {"error": "feedback_exists", "message": "dup", "details": {}}
            )

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.submit_feedback({"feedback_id": "fb-1"})
        assert excinfo.value.error == "feedback_exists"

    async def test_get_feedback_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(404, {"error": "not_found", "message": "no", "details": {}})

        client = _client(handler)
        assert await client.get_feedback("fb-missing") is None

    async def test_list_feedback_by_task(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["task_id"] == "t-1"
            return _json_response(200, {"feedback": [{"feedback_id": "fb-1"}]})

        client = _client(handler)
        results = await client.list_feedback(task_id="t-1")
        assert len(results) == 1

    async def test_count_feedback(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"count": 7})

        client = _client(handler)
        assert await client.count_feedback() == 7


# ---------------------------------------------------------------------------
# Court
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCourt:
    async def test_file_claim_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/court/claims"
            return _json_response(201, {"claim_id": "clm-1"})

        client = _client(handler)
        result = await client.file_claim({"claim_id": "clm-1"})
        assert result["claim_id"] == "clm-1"

    async def test_get_claim_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(404, {"error": "not_found", "message": "no", "details": {}})

        client = _client(handler)
        assert await client.get_claim("clm-missing") is None

    async def test_list_claims_with_status_filter(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["status"] == "ruled"
            return _json_response(200, {"claims": [{"claim_id": "clm-1"}]})

        client = _client(handler)
        results = await client.list_claims(status="ruled")
        assert len(results) == 1

    async def test_update_claim_status_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(404, {"error": "claim_not_found", "message": "no", "details": {}})

        client = _client(handler)
        assert await client.update_claim_status("clm-missing", {"status": "ruled"}) is None

    async def test_update_claim_status_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/court/claims/clm-1/status"
            return _json_response(200, {"claim_id": "clm-1", "status": "ruled"})

        client = _client(handler)
        result = await client.update_claim_status("clm-1", {"status": "ruled"})
        assert result is not None
        assert result["status"] == "ruled"

    async def test_submit_rebuttal_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/court/rebuttals"
            return _json_response(201, {"rebuttal_id": "reb-1"})

        client = _client(handler)
        result = await client.submit_rebuttal({"rebuttal_id": "reb-1"})
        assert result["rebuttal_id"] == "reb-1"

    async def test_get_rebuttal_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(404, {"error": "not_found", "message": "no", "details": {}})

        client = _client(handler)
        assert await client.get_rebuttal("clm-missing") is None

    async def test_record_ruling_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/court/rulings"
            return _json_response(201, {"ruling_id": "clm-1"})

        client = _client(handler)
        result = await client.record_ruling({"ruling_id": "clm-1"})
        assert result["ruling_id"] == "clm-1"

    async def test_get_ruling_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(404, {"error": "not_found", "message": "no", "details": {}})

        client = _client(handler)
        assert await client.get_ruling("clm-missing") is None

    async def test_delete_ruling_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            assert request.url.path == "/court/rulings/clm-1"
            return _json_response(200, {"deleted": True, "claim_id": "clm-1", "event_id": 1})

        client = _client(handler)
        result = await client.delete_ruling("clm-1", {"event": {}})
        assert result is not None
        assert result["deleted"] is True

    async def test_delete_ruling_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(404, {"error": "not_found", "message": "no", "details": {}})

        client = _client(handler)
        assert await client.delete_ruling("clm-missing", {"event": {}}) is None

    async def test_count_claims(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"count": 2})

        client = _client(handler)
        assert await client.count_claims() == 2

    async def test_count_active_claims(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"count": 1})

        client = _client(handler)
        assert await client.count_active_claims() == 1


# ---------------------------------------------------------------------------
# Board — read-only
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBoardRead:
    async def test_get_task_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/board/tasks/t-1"
            return _json_response(200, {"task_id": "t-1", "escrow_id": "esc-1"})

        client = _client(handler)
        result = await client.get_task("t-1")
        assert result is not None
        assert result["escrow_id"] == "esc-1"

    async def test_get_task_not_found_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(404, {"error": "task_not_found", "message": "no", "details": {}})

        client = _client(handler)
        assert await client.get_task("t-missing") is None
