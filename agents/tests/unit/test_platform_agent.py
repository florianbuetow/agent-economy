"""Unit tests for PlatformAgent privileged operations."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.config import AgentConfig
from base_agent.platform import PlatformAgent
from base_agent.signing import TokenPolicy, create_jws

_ISSUANCE_OPERATION_ID = "op-00000000-0000-4000-8000-000000000042"
_CREDIT_OPERATION_ID = "op-00000000-0000-4000-8000-000000000043"
_RELEASE_OPERATION_ID = "op-00000000-0000-4000-8000-000000000044"
_SPLIT_OPERATION_ID = "op-00000000-0000-4000-8000-000000000045"
_RELEASE_SOURCE_REFERENCE = "task:t-1:approved"
_SPLIT_SOURCE_REFERENCE = "task:t-1:ruling:r-one"
_COMMITTED_AT = "2026-10-17T14:00:00Z"


def _native_response(
    operation_id: str,
    operation_kind: str,
    source_reference: str,
    business_fields: dict[str, object],
    result: dict[str, object],
    legs: list[dict[str, object]],
    current_escrow: dict[str, object] | None,
) -> dict[str, Any]:
    canonical_request = {
        "request_version": 1,
        "operation_kind": operation_kind,
        **business_fields,
    }
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            "request_sha256": hashlib.sha256(
                json.dumps(
                    canonical_request,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
            "source_reference": source_reference,
            "committed_at": _COMMITTED_AT,
            "event_id": 1,
            "escrow_id": None if current_escrow is None else current_escrow["escrow_id"],
            "result": result,
            "legs": legs,
        },
        "current_escrow": current_escrow,
    }


@pytest.fixture()
def platform_config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return AgentConfig(
        name="Platform",
        private_key=private_key,
        public_key=public_key,
        identity_url="http://localhost:8001",
        bank_url="http://localhost:8002",
        task_board_url="http://localhost:8003",
        reputation_url="http://localhost:8004",
        court_url="http://localhost:8005",
        token_ttl_seconds=300,
        token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
    )


@pytest.mark.unit
class TestPlatformAgentInit:
    """Tests for PlatformAgent construction."""

    def test_creates_platform_agent(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        assert agent.name == "Platform"
        assert agent.agent_id is None

    def test_repr(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        assert "PlatformAgent" in repr(agent)


@pytest.mark.unit
class TestCreateAccount:
    """Tests for create_account."""

    async def test_create_account_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        response = _native_response(
            _ISSUANCE_OPERATION_ID,
            "account_issuance",
            "initial_balance",
            {"account_id": "a-alice", "amount": 100, "reference": "initial_balance"},
            {"account_id": "a-alice", "issued_amount": 100, "balance_after": 100},
            [
                {
                    "role": "account",
                    "account_id": "a-alice",
                    "direction": "credit",
                    "amount": 100,
                    "transaction_id": "tx-issuance",
                    "balance_after": 100,
                }
            ],
            None,
        )
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.create_account(
            agent_id="a-alice",
            initial_balance=100,
            operation_id=_ISSUANCE_OPERATION_ID,
        )

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "create_account",
                "agent_id": "a-alice",
                "initial_balance": 100,
                "operation_id": _ISSUANCE_OPERATION_ID,
            },
            audience="central-bank",
            resource="POST /accounts",
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/accounts",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestCreditAccount:
    """Tests for credit_account."""

    async def test_credit_account_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        response = _native_response(
            _CREDIT_OPERATION_ID,
            "credit",
            "salary",
            {"account_id": "a-alice", "amount": 100, "reference": "salary"},
            {
                "account_id": "a-alice",
                "amount": 100,
                "balance_after": 200,
                "reference": "salary",
            },
            [
                {
                    "role": "account",
                    "account_id": "a-alice",
                    "direction": "credit",
                    "amount": 100,
                    "transaction_id": "tx-credit",
                    "balance_after": 200,
                }
            ],
            None,
        )
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.credit_account(
            account_id="a-alice",
            amount=100,
            reference="salary",
            operation_id=_CREDIT_OPERATION_ID,
        )

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "credit",
                "operation_id": _CREDIT_OPERATION_ID,
                "account_id": "a-alice",
                "amount": 100,
                "reference": "salary",
            },
            audience="central-bank",
            resource="POST /accounts/a-alice/credit",
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/accounts/a-alice/credit",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestReleaseEscrow:
    """Tests for release_escrow."""

    async def test_release_escrow_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        current_escrow = {
            "escrow_id": "esc-1",
            "payer_account_id": "a-bob",
            "task_id": "t-1",
            "amount": 50,
            "status": "released",
            "created_at": _COMMITTED_AT,
            "resolved_at": _COMMITTED_AT,
        }
        response = _native_response(
            _RELEASE_OPERATION_ID,
            "escrow_release",
            _RELEASE_SOURCE_REFERENCE,
            {
                "escrow_id": "esc-1",
                "recipient_account_id": "a-alice",
                "source_reference": _RELEASE_SOURCE_REFERENCE,
            },
            {"escrow_status_at_commit": "released"},
            [
                {
                    "role": "recipient",
                    "account_id": "a-alice",
                    "direction": "credit",
                    "amount": 50,
                    "transaction_id": "tx-release",
                    "balance_after": 50,
                }
            ],
            current_escrow,
        )
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.release_escrow(
            escrow_id="esc-1",
            recipient_account_id="a-alice",
            operation_id=_RELEASE_OPERATION_ID,
            source_reference=_RELEASE_SOURCE_REFERENCE,
        )

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "escrow_release",
                "operation_id": _RELEASE_OPERATION_ID,
                "source_reference": _RELEASE_SOURCE_REFERENCE,
                "escrow_id": "esc-1",
                "recipient_account_id": "a-alice",
            },
            audience="central-bank",
            resource="POST /escrow/esc-1/release",
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/escrow/esc-1/release",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestSplitEscrow:
    """Tests for split_escrow."""

    async def test_split_escrow_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        current_escrow = {
            "escrow_id": "esc-1",
            "payer_account_id": "a-bob",
            "task_id": "t-1",
            "amount": 100,
            "status": "split",
            "created_at": _COMMITTED_AT,
            "resolved_at": _COMMITTED_AT,
        }
        response = _native_response(
            _SPLIT_OPERATION_ID,
            "escrow_split",
            _SPLIT_SOURCE_REFERENCE,
            {
                "escrow_id": "esc-1",
                "worker_account_id": "a-alice",
                "poster_account_id": "a-bob",
                "worker_pct": 70,
                "source_reference": _SPLIT_SOURCE_REFERENCE,
            },
            {"escrow_status_at_commit": "split", "worker_pct": 70},
            [
                {
                    "role": "worker",
                    "account_id": "a-alice",
                    "direction": "credit",
                    "amount": 70,
                    "transaction_id": "tx-worker",
                    "balance_after": 70,
                },
                {
                    "role": "poster",
                    "account_id": "a-bob",
                    "direction": "credit",
                    "amount": 30,
                    "transaction_id": "tx-poster",
                    "balance_after": 30,
                },
            ],
            current_escrow,
        )
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.split_escrow(
            escrow_id="esc-1",
            worker_account_id="a-alice",
            poster_account_id="a-bob",
            worker_pct=70,
            operation_id=_SPLIT_OPERATION_ID,
            source_reference=_SPLIT_SOURCE_REFERENCE,
        )

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "escrow_split",
                "operation_id": _SPLIT_OPERATION_ID,
                "source_reference": _SPLIT_SOURCE_REFERENCE,
                "escrow_id": "esc-1",
                "worker_account_id": "a-alice",
                "poster_account_id": "a-bob",
                "worker_pct": 70,
            },
            audience="central-bank",
            resource="POST /escrow/esc-1/split",
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/escrow/esc-1/split",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestVerifyPlatformJws:
    """Tests for verify_platform_jws."""

    def test_verify_valid_platform_token(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"

        token = agent._sign_jws(
            {"action": "create_account", "agent_id": "a-alice"},
            audience="central-bank",
            resource="POST /accounts",
        )
        result = agent.verify_platform_jws(token)

        assert result.payload["action"] == "create_account"
        assert result.payload["agent_id"] == "a-alice"

    def test_verify_rejects_non_platform_token(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"

        other_key = Ed25519PrivateKey.generate()
        token = create_jws(
            {"action": "create_account"},
            other_key,
            kid="a-imposter",
            audience="central-bank",
            resource="POST /accounts",
            token_ttl_seconds=300,
        )

        with pytest.raises(InvalidSignature):
            agent.verify_platform_jws(token)
