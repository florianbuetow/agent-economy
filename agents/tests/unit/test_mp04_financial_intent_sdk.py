"""SDK signing carries caller-owned financial intent across repeated requests.

These are outbound-token tests. Truthful transport fixtures satisfy the SDK's
success boundary but are not evidence of a real money effect; those are covered
by the Bank/Gateway suites.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from service_auth.financial_identity import operation_id_for

from base_agent.agent import BaseAgent
from base_agent.platform import PlatformAgent
from base_agent.signing import parse_jws

if TYPE_CHECKING:
    from base_agent.config import AgentConfig

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_OPERATION_ID = "op-00000000-0000-4000-8000-000000000001"
_SOURCE_REFERENCE = "task:t-1:approved"
_COMMITTED_AT = "2026-10-17T16:00:00Z"


def _request_hash(operation_kind: str, fields: dict[str, object]) -> str:
    canonical = {
        "request_version": 1,
        "operation_kind": operation_kind,
        **fields,
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _projection(status: str, amount: int) -> dict[str, object]:
    return {
        "escrow_id": "esc-1",
        "payer_account_id": "a-poster",
        "task_id": "t-1",
        "amount": amount,
        "status": status,
        "created_at": "2026-10-17T15:59:00Z",
        "resolved_at": None if status == "locked" else _COMMITTED_AT,
    }


def _native_response(
    operation_kind: str,
    source_reference: str,
    fields: dict[str, object],
    result: dict[str, object],
    legs: list[dict[str, object]],
    current_escrow: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": _OPERATION_ID,
            "operation_kind": operation_kind,
            "request_sha256": _request_hash(operation_kind, fields),
            "source_reference": source_reference,
            "committed_at": _COMMITTED_AT,
            "event_id": 1,
            "escrow_id": None if current_escrow is None else "esc-1",
            "result": result,
            "legs": legs,
        },
        "current_escrow": current_escrow,
    }


def _response_for(method_name: str) -> dict[str, object]:
    if method_name == "create_account":
        return _native_response(
            "account_issuance",
            "initial_balance",
            {"account_id": "a-worker", "amount": 100, "reference": "initial_balance"},
            {"account_id": "a-worker", "issued_amount": 100, "balance_after": 100},
            [
                {
                    "role": "account",
                    "account_id": "a-worker",
                    "direction": "credit",
                    "amount": 100,
                    "transaction_id": "tx-issuance",
                    "balance_after": 100,
                }
            ],
            None,
        )
    if method_name == "credit_account":
        return _native_response(
            "credit",
            "funding-1",
            {"account_id": "a-worker", "amount": 100, "reference": "funding-1"},
            {
                "account_id": "a-worker",
                "amount": 100,
                "balance_after": 200,
                "reference": "funding-1",
            },
            [
                {
                    "role": "account",
                    "account_id": "a-worker",
                    "direction": "credit",
                    "amount": 100,
                    "transaction_id": "tx-credit",
                    "balance_after": 200,
                }
            ],
            None,
        )
    if method_name == "release_escrow":
        return _native_response(
            "escrow_release",
            _SOURCE_REFERENCE,
            {
                "escrow_id": "esc-1",
                "recipient_account_id": "a-worker",
                "source_reference": _SOURCE_REFERENCE,
            },
            {"escrow_status_at_commit": "released"},
            [
                {
                    "role": "recipient",
                    "account_id": "a-worker",
                    "direction": "credit",
                    "amount": 100,
                    "transaction_id": "tx-release",
                    "balance_after": 100,
                }
            ],
            _projection("released", 100),
        )
    return _native_response(
        "escrow_split",
        _SOURCE_REFERENCE,
        {
            "escrow_id": "esc-1",
            "worker_account_id": "a-worker",
            "poster_account_id": "a-poster",
            "worker_pct": 70,
            "source_reference": _SOURCE_REFERENCE,
        },
        {"escrow_status_at_commit": "split", "worker_pct": 70},
        [
            {
                "role": "worker",
                "account_id": "a-worker",
                "direction": "credit",
                "amount": 70,
                "transaction_id": "tx-worker",
                "balance_after": 70,
            },
            {
                "role": "poster",
                "account_id": "a-poster",
                "direction": "credit",
                "amount": 30,
                "transaction_id": "tx-poster",
                "balance_after": 30,
            },
        ],
        _projection("split", 100),
    )


def _lock_response() -> dict[str, object]:
    return _native_response(
        "escrow_lock",
        "t-1",
        {"payer_account_id": "a-poster", "task_id": "t-1", "amount": 100},
        {"escrow_status_at_commit": "locked"},
        [
            {
                "role": "payer",
                "account_id": "a-poster",
                "direction": "debit",
                "amount": 100,
                "transaction_id": "tx-lock",
                "balance_after": 0,
            }
        ],
        _projection("locked", 100),
    )


@pytest.mark.parametrize(
    ("method_name", "parameters", "action", "resource"),
    [
        (
            "create_account",
            {"agent_id": "a-worker", "initial_balance": 100},
            "create_account",
            "POST /accounts",
        ),
        (
            "credit_account",
            {"account_id": "a-worker", "amount": 100, "reference": "funding-1"},
            "credit",
            "POST /accounts/a-worker/credit",
        ),
        (
            "release_escrow",
            {
                "escrow_id": "esc-1",
                "recipient_account_id": "a-worker",
                "source_reference": _SOURCE_REFERENCE,
            },
            "escrow_release",
            "POST /escrow/esc-1/release",
        ),
        (
            "split_escrow",
            {
                "escrow_id": "esc-1",
                "worker_account_id": "a-worker",
                "poster_account_id": "a-poster",
                "worker_pct": 70,
                "source_reference": _SOURCE_REFERENCE,
            },
            "escrow_split",
            "POST /escrow/esc-1/split",
        ),
    ],
)
async def test_platform_repeated_requests_preserve_the_exact_signed_intent(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    parameters: dict[str, Any],
    action: str,
    resource: str,
) -> None:
    agent = PlatformAgent(sample_config)
    agent.agent_id = "a-platform"
    transport = AsyncMock(return_value=_response_for(method_name))
    monkeypatch.setattr(agent, "_request", transport)
    try:
        method = getattr(agent, method_name)
        for _ in range(2):
            await method(**parameters, operation_id=_OPERATION_ID)
        assert transport.await_count == 2
        for call in transport.await_args_list:
            token = parse_jws(call.kwargs["json"]["token"])
            assert (token.header.kid, token.header.aud, token.header.resource) == (
                "a-platform",
                "central-bank",
                resource,
            )
            assert token.payload == {"action": action, **parameters, "operation_id": _OPERATION_ID}
    finally:
        await agent.close()


async def test_agent_lock_repeats_the_callers_operation_identity(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = BaseAgent(sample_config)
    agent.agent_id = "a-poster"
    transport = AsyncMock(return_value=_lock_response())
    monkeypatch.setattr(agent, "_request", transport)
    try:
        for _ in range(2):
            await agent.lock_escrow(100, "t-1", operation_id=_OPERATION_ID)
        assert transport.await_count == 2
        for call in transport.await_args_list:
            token = parse_jws(call.kwargs["json"]["token"])
            assert (token.header.aud, token.header.resource) == (
                "central-bank",
                "POST /escrow/lock",
            )
            assert token.payload == {
                "action": "escrow_lock",
                "agent_id": "a-poster",
                "amount": 100,
                "task_id": "t-1",
                "operation_id": _OPERATION_ID,
            }
    finally:
        await agent.close()


async def test_zero_account_explicit_none_is_not_serialized_as_an_operation(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = PlatformAgent(sample_config)
    agent.agent_id = "a-platform"
    transport = AsyncMock(
        return_value={
            "account_id": "a-worker",
            "balance": 7,
            "created_at": _COMMITTED_AT,
        }
    )
    monkeypatch.setattr(agent, "_request", transport)
    try:
        await agent.create_account("a-worker", 0, operation_id=None)
        transport.assert_awaited_once()
        assert transport.await_args is not None
        token = parse_jws(transport.await_args.kwargs["json"]["token"])
        assert token.payload == {
            "action": "create_account",
            "agent_id": "a-worker",
            "initial_balance": 0,
        }
    finally:
        await agent.close()


@pytest.mark.parametrize(("balance", "operation_id"), [(100, None), (0, _OPERATION_ID)])
async def test_account_intent_must_match_whether_money_is_issued(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    balance: int,
    operation_id: str | None,
) -> None:
    agent = PlatformAgent(sample_config)
    agent.agent_id = "a-platform"
    transport = AsyncMock()
    monkeypatch.setattr(agent, "_request", transport)
    try:
        with pytest.raises(ValueError, match="operation_id"):
            await agent.create_account("a-worker", balance, operation_id=operation_id)
        transport.assert_not_awaited()
    finally:
        await agent.close()


async def test_task_posting_derives_the_lock_identity_from_its_existing_intent(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = BaseAgent(sample_config)
    agent.agent_id = "a-poster"
    transport = AsyncMock(return_value={"task_id": "transport-task-probe"})
    monkeypatch.setattr(agent, "_request", transport)
    try:
        await agent.post_task("Title", "Spec", 100, 10, 20, 30, acceptance_window_seconds=40)
        transport.assert_awaited_once()
        assert transport.await_args is not None
        body = transport.await_args.kwargs["json"]
        task = parse_jws(body["task_token"])
        escrow = parse_jws(body["escrow_token"])
        task_id = task.payload["task_id"]
        assert isinstance(task_id, str)
        assert escrow.payload["operation_id"] == operation_id_for(
            "task-lock", (task_id, "a-poster")
        )
        assert escrow.payload["task_id"] == task.payload["task_id"]
        assert escrow.payload["agent_id"] == task.payload["poster_id"]
        assert escrow.payload["amount"] == task.payload["reward"]
    finally:
        await agent.close()
