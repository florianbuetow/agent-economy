"""Demo credit client validates attested historical observations."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import pytest

from demo_replay.clients import credit_account
from demo_replay.wallet import DemoAgent

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_OPERATION_ID = "op-00000000-0000-4000-8000-000000000311"


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _legacy_credit(operation_id: str) -> dict[str, Any]:
    transaction = {
        "tx_id": "tx-historical-credit",
        "account_id": "a-demo",
        "type": "credit",
        "amount": 75,
        "balance_after": 75,
        "reference": "demo:historical:funding",
        "timestamp": "",
        "event_id": -1,
    }
    request = {
        "request_version": 1,
        "operation_kind": "credit",
        "account_id": "a-demo",
        "amount": 75,
        "reference": "demo:historical:funding",
    }
    return {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": operation_id,
            "operation_kind": "credit",
            "request_sha256": _hash(request),
        },
        "observation": {
            "operation_id": None,
            "observed_kind": "observed_credit",
            "transaction": transaction,
            "observed_fact_sha256": _hash(transaction),
        },
        "current_escrow": None,
    }


async def _call(body: dict[str, Any], status_code: int) -> dict[str, object]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, json=body)

    platform = DemoAgent.create("platform", "Platform", 60)
    platform.agent_id = "a-platform"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await credit_account(
            http,
            platform,
            "a-demo",
            75,
            "https://bank",
            operation_id=_OPERATION_ID,
            reference="demo:historical:funding",
        )
    token = json.loads(requests[0].content)["token"]
    assert isinstance(token, str)
    return result


async def test_claimed_valid_legacy_credit_returns_exact_observation() -> None:
    expected = _legacy_credit(_OPERATION_ID)

    assert await _call(expected, 200) == expected


@pytest.mark.parametrize("mutation", ["attempt_id", "hash", "account", "amount", "reference"])
async def test_mismatched_legacy_credit_is_rejected(mutation: str) -> None:
    body = _legacy_credit(_OPERATION_ID)
    attempt = body["attempt"]
    observation = body["observation"]
    assert isinstance(attempt, dict)
    assert isinstance(observation, dict)
    transaction = observation["transaction"]
    assert isinstance(transaction, dict)
    if mutation == "attempt_id":
        attempt["operation_id"] = "op-00000000-0000-4000-8000-000000000312"
    elif mutation == "hash":
        observation["observed_fact_sha256"] = "0" * 64
    else:
        value: object = {
            "account": "a-other",
            "amount": 76,
            "reference": "demo:other:funding",
        }[mutation]
        transaction[
            {"account": "account_id", "amount": "amount", "reference": "reference"}[mutation]
        ] = value
        observation["observed_fact_sha256"] = _hash(transaction)

    with pytest.raises(RuntimeError):
        await _call(body, 200)


async def test_non_success_http_response_precedes_financial_parsing() -> None:
    with pytest.raises(httpx.HTTPStatusError) as failure:
        await _call({"error": "legacy_operation_conflict"}, 409)
    assert failure.value.response.status_code == 409
