"""Unit tests for demo_replay.clients (GAP-A13 — real Reputation API contract).

The real Reputation API (services/reputation/src/reputation_service/routers/
feedback.py) requires ``from_agent_id`` in the JWS payload and verifies it
against the token signer; it has no separate "reveal" endpoint. These tests
pin the fixed demo client against that contract and against GAP-E11 (no
hardcoded URL defaults on any client function).
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from demo_replay import clients


def _make_agent(agent_id: str = "a-1") -> MagicMock:
    agent = MagicMock()
    agent.agent_id = agent_id
    agent.sign_jws = MagicMock(return_value="token.sig.part")
    return agent


def _make_http_client(json_response: dict[str, object], status_code: int = 200) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_response
    response.raise_for_status = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.get = AsyncMock(return_value=response)
    return client


@pytest.mark.unit
class TestSubmitFeedbackMatchesRealApiContract:
    @pytest.mark.asyncio
    async def test_signs_from_agent_id_not_role(self) -> None:
        agent = _make_agent(agent_id="a-worker")
        http = _make_http_client({"feedback_id": "f-1"}, status_code=201)

        await clients.submit_feedback(
            http,
            agent,
            task_id="t-1",
            to_agent_id="a-poster",
            category="spec_quality",
            rating="satisfied",
            comment="Great task",
            reputation_url="http://localhost:8004",
        )

        payload = agent.sign_jws.call_args[0][0]
        assert payload["from_agent_id"] == "a-worker"
        assert "role" not in payload

    @pytest.mark.asyncio
    async def test_posts_to_feedback_endpoint_with_given_url(self) -> None:
        agent = _make_agent()
        http = _make_http_client({"feedback_id": "f-1"}, status_code=201)

        await clients.submit_feedback(
            http,
            agent,
            task_id="t-1",
            to_agent_id="a-poster",
            category="spec_quality",
            rating="satisfied",
            comment="",
            reputation_url="http://localhost:8004",
        )

        url = http.post.call_args[0][0]
        assert url == "http://localhost:8004/feedback"

    def test_submit_feedback_has_no_role_parameter(self) -> None:
        sig = inspect.signature(clients.submit_feedback)
        assert "role" not in sig.parameters


@pytest.mark.unit
class TestRevealFeedbackRemoved:
    """No such endpoint exists on the real Reputation API — feedback becomes
    visible automatically once both parties have submitted, or after the
    reveal timeout elapses. There is nothing for an agent to trigger.
    """

    def test_reveal_feedback_function_does_not_exist(self) -> None:
        assert not hasattr(clients, "reveal_feedback")


@pytest.mark.unit
class TestClientUrlsAreExplicitNoDefaults:
    """GAP-E11: no hardcoded URL module constants or defaulted URL params."""

    def test_no_url_module_constants(self) -> None:
        for name in ("IDENTITY_URL", "BANK_URL", "TASK_BOARD_URL", "COURT_URL"):
            assert not hasattr(clients, name)

    @pytest.mark.parametrize(
        ("func_name", "url_param"),
        [
            ("register_agent", "identity_url"),
            ("create_account", "bank_url"),
            ("credit_account", "bank_url"),
            ("post_task", "task_board_url"),
            ("submit_bid", "task_board_url"),
            ("list_bids", "task_board_url"),
            ("accept_bid", "task_board_url"),
            ("get_task", "task_board_url"),
            ("upload_asset", "task_board_url"),
            ("submit_deliverable", "task_board_url"),
            ("approve_task", "task_board_url"),
            ("dispute_task", "task_board_url"),
            ("list_disputes", "court_url"),
            ("file_claim", "court_url"),
            ("submit_rebuttal", "task_board_url"),
            ("trigger_ruling", "court_url"),
            ("submit_feedback", "reputation_url"),
        ],
    )
    def test_url_param_has_no_default(self, func_name: str, url_param: str) -> None:
        func = getattr(clients, func_name)
        sig = inspect.signature(func)
        param = sig.parameters[url_param]
        assert param.default is inspect.Parameter.empty


@pytest.mark.unit
class TestRegisterAgentUsesGivenIdentityUrl:
    @pytest.mark.asyncio
    async def test_register_posts_to_given_identity_url(self) -> None:
        agent = _make_agent()
        agent.public_key_string = MagicMock(return_value="ed25519:abc")
        http = _make_http_client({"agent_id": "a-1"}, status_code=201)

        await clients.register_agent(http, agent, "http://custom-identity:9001")

        url = http.post.call_args[0][0]
        assert url == "http://custom-identity:9001/agents/register"
