"""Public SDK boundaries validate attested historical credit and lock observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth.agent import BaseAgent
from service_auth.config import AgentConfig
from service_auth.financial_responses import SQLITE_MAX_INTEGER, parse_native_financial_envelope
from service_auth.jws import TokenPolicy, parse_jws
from service_auth.platform import PlatformAgent

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_CREDIT_OPERATION_IDS = (
    "op-00000000-0000-4000-8000-000000000091",
    "op-00000000-0000-4000-8000-000000000092",
)
_LOCK_OPERATION_IDS = (
    "op-00000000-0000-4000-8000-000000000093",
    "op-00000000-0000-4000-8000-000000000094",
)


def _config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    return AgentConfig(
        name="Historical observer",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url="https://identity",
        bank_url="https://bank",
        task_board_url="https://board",
        reputation_url="https://reputation",
        court_url="https://court",
        token_ttl_seconds=60,
        token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=0),
    )


def _hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _attempt_hash(operation_kind: str, fields: dict[str, object]) -> str:
    return _hash(
        {
            "request_version": 1,
            "operation_kind": operation_kind,
            **fields,
        }
    )


def _credit_transaction() -> dict[str, object]:
    return {
        "tx_id": "tx-historical-credit",
        "account_id": "a-credit",
        "type": "credit",
        "amount": 50,
        "balance_after": 50,
        "reference": "historical-genesis",
        "timestamp": "",
        "event_id": -7,
    }


def _credit_observation(operation_id: str) -> dict[str, object]:
    transaction = _credit_transaction()
    fields = {
        "account_id": "a-credit",
        "amount": 50,
        "reference": "historical-genesis",
    }
    return {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": operation_id,
            "operation_kind": "credit",
            "request_sha256": _attempt_hash("credit", fields),
        },
        "observation": {
            "operation_id": None,
            "observed_kind": "observed_credit",
            "transaction": transaction,
            "observed_fact_sha256": _hash(transaction),
        },
        "current_escrow": None,
    }


def _lock_transaction() -> dict[str, object]:
    return {
        "tx_id": "tx-historical-lock",
        "account_id": "a-payer",
        "type": "escrow_lock",
        "amount": 40,
        "balance_after": 60,
        "reference": "t-historical",
        "timestamp": "",
        "event_id": 0,
    }


def _lock_fact() -> dict[str, object]:
    return {
        "escrow_id": "esc-historical",
        "payer_account_id": "a-payer",
        "amount": 40,
        "task_id": "t-historical",
        "created_at": "",
        "event_id": -11,
    }


def _lock_observation(operation_id: str) -> dict[str, object]:
    transaction = _lock_transaction()
    lock_fact = _lock_fact()
    fields = {
        "payer_account_id": "a-payer",
        "task_id": "t-historical",
        "amount": 40,
    }
    return {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": operation_id,
            "operation_kind": "escrow_lock",
            "request_sha256": _attempt_hash("escrow_lock", fields),
        },
        "observation": {
            "operation_id": None,
            "observed_kind": "observed_escrow_lock",
            "transaction": transaction,
            "observed_fact_sha256": _hash(transaction),
            "escrow_lock": lock_fact,
            "observed_status": "locked",
            "lock_fact_sha256": _hash(lock_fact),
            "resolution_fact": None,
            "resolution_fact_sha256": None,
        },
        "current_escrow": {
            "escrow_id": "esc-historical",
            "payer_account_id": "a-payer",
            "task_id": "t-historical",
            "amount": 40,
            "status": "released",
            "created_at": "",
            "resolved_at": "2026-10-17T15:00:00Z",
        },
    }


def _resolution_fact(
    observed_status: str,
    transaction_ids: list[str],
) -> dict[str, object]:
    return {
        "escrow_id": "esc-historical",
        "observed_status": observed_status,
        "resolved_at": "",
        "transaction_ids": transaction_ids,
    }


def _resolved_lock_observation(operation_id: str, observed_status: str) -> dict[str, object]:
    envelope = _lock_observation(operation_id)
    observation = envelope["observation"]
    current = envelope["current_escrow"]
    assert isinstance(observation, dict)
    assert isinstance(current, dict)
    transaction_ids = (
        ["tx-release-a"] if observed_status == "released" else ["tx-release-a", "tx-release-b"]
    )
    resolution = _resolution_fact(observed_status, transaction_ids)
    observation["observed_status"] = observed_status
    observation["resolution_fact"] = resolution
    observation["resolution_fact_sha256"] = _hash(resolution)
    current["status"] = observed_status
    current["resolved_at"] = ""
    return envelope


async def _credit_call(
    envelope: dict[str, object],
    operation_id: str,
    account_id: str,
    amount: int,
    reference: str,
) -> dict[str, Any]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=envelope)

    agent = PlatformAgent(_config())
    agent.agent_id = "a-platform"
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await agent.credit_account(
            account_id,
            amount,
            reference,
            operation_id=operation_id,
        )
        token = parse_jws(json.loads(requests[0].content)["token"])
        assert token.payload["operation_id"] == operation_id
        return result
    finally:
        await agent.close()


async def _lock_call(
    envelope: dict[str, object],
    operation_id: str,
    payer_id: str,
    amount: int,
    task_id: str,
) -> dict[str, Any]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json=envelope)

    agent = BaseAgent(_config())
    agent.agent_id = payer_id
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await agent.lock_escrow(amount, task_id, operation_id=operation_id)
        token = parse_jws(json.loads(requests[0].content)["token"])
        assert token.payload["operation_id"] == operation_id
        return result
    finally:
        await agent.close()


async def _issuance_call(envelope: dict[str, object], operation_id: str) -> dict[str, Any]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=envelope)

    agent = PlatformAgent(_config())
    agent.agent_id = "a-platform"
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return await agent.create_account("a-credit", 50, operation_id=operation_id)
    finally:
        await agent.close()


async def _release_call(envelope: dict[str, object], operation_id: str) -> dict[str, Any]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=envelope)

    agent = PlatformAgent(_config())
    agent.agent_id = "a-platform"
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return await agent.release_escrow(
            "esc-historical",
            "a-recipient",
            operation_id=operation_id,
            source_reference="task:t-historical:approved",
        )
    finally:
        await agent.close()


async def _split_call(envelope: dict[str, object], operation_id: str) -> dict[str, Any]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=envelope)

    agent = PlatformAgent(_config())
    agent.agent_id = "a-platform"
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return await agent.split_escrow(
            "esc-historical",
            "a-worker",
            "a-payer",
            50,
            operation_id=operation_id,
            source_reference="task:t-historical:ruling:r-one",
        )
    finally:
        await agent.close()


def _assert_credit_base_is_admissible(envelope: dict[str, object]) -> None:
    assert set(envelope) == {"outcome", "attempt", "observation", "current_escrow"}
    assert envelope["outcome"] == "legacy_observation"
    assert envelope["current_escrow"] is None
    attempt = envelope["attempt"]
    observation = envelope["observation"]
    assert isinstance(attempt, dict)
    assert isinstance(observation, dict)
    assert set(attempt) == {"operation_id", "operation_kind", "request_sha256"}
    assert set(observation) == {
        "operation_id",
        "observed_kind",
        "transaction",
        "observed_fact_sha256",
    }
    transaction = observation["transaction"]
    assert isinstance(transaction, dict)
    assert observation["operation_id"] is None
    assert observation["observed_kind"] == "observed_credit"
    assert observation["observed_fact_sha256"] == _hash(transaction)
    assert type(transaction["amount"]) is int and 0 < transaction["amount"] <= SQLITE_MAX_INTEGER
    assert type(transaction["balance_after"]) is int
    assert 0 <= transaction["balance_after"] <= SQLITE_MAX_INTEGER
    assert transaction["timestamp"] == ""
    assert transaction["event_id"] == -7


def _assert_lock_base_is_admissible(
    envelope: dict[str, object],
    expected_observed_status: str,
) -> None:
    assert set(envelope) == {"outcome", "attempt", "observation", "current_escrow"}
    attempt, observation, current, transaction, lock_fact = _lock_parts(envelope)
    assert set(attempt) == {"operation_id", "operation_kind", "request_sha256"}
    assert set(observation) == {
        "operation_id",
        "observed_kind",
        "transaction",
        "observed_fact_sha256",
        "escrow_lock",
        "observed_status",
        "lock_fact_sha256",
        "resolution_fact",
        "resolution_fact_sha256",
    }
    assert observation["operation_id"] is None
    assert observation["observed_kind"] == "observed_escrow_lock"
    assert observation["observed_status"] == expected_observed_status
    assert observation["observed_fact_sha256"] == _hash(transaction)
    assert observation["lock_fact_sha256"] == _hash(lock_fact)
    assert transaction["type"] == "escrow_lock"
    assert transaction["account_id"] == lock_fact["payer_account_id"]
    assert transaction["amount"] == lock_fact["amount"]
    assert transaction["reference"] == lock_fact["task_id"]
    assert current["escrow_id"] == lock_fact["escrow_id"]
    assert current["payer_account_id"] == lock_fact["payer_account_id"]
    assert current["task_id"] == lock_fact["task_id"]
    assert current["amount"] == lock_fact["amount"]
    resolution = observation["resolution_fact"]
    resolution_hash = observation["resolution_fact_sha256"]
    if expected_observed_status == "locked":
        assert resolution is None and resolution_hash is None
    else:
        assert isinstance(resolution, dict)
        assert resolution_hash == _hash(resolution)
        assert resolution["resolved_at"] == current["resolved_at"]
        transaction_ids = resolution["transaction_ids"]
        assert isinstance(transaction_ids, list)
        assert transaction_ids == sorted(transaction_ids)
        if expected_observed_status == "released":
            assert len(transaction_ids) == 1
        else:
            assert len(transaction_ids) in {1, 2}


async def test_control_fixtures_are_admissible_before_mutation() -> None:
    _assert_credit_base_is_admissible(_credit_observation(_CREDIT_OPERATION_IDS[0]))
    _assert_lock_base_is_admissible(_lock_observation(_LOCK_OPERATION_IDS[0]), "locked")
    _assert_lock_base_is_admissible(
        _resolved_lock_observation(_LOCK_OPERATION_IDS[0], "released"),
        "released",
    )
    _assert_lock_base_is_admissible(
        _resolved_lock_observation(_LOCK_OPERATION_IDS[0], "split"),
        "split",
    )


@pytest.mark.parametrize("boundary", ["issuance", "release", "split"])
async def test_other_financial_boundaries_reject_valid_legacy_outcomes_control(
    boundary: str,
) -> None:
    operation_id = _CREDIT_OPERATION_IDS[0]
    if boundary == "issuance":
        envelope = _credit_observation(operation_id)
        attempt = envelope["attempt"]
        assert isinstance(attempt, dict)
        attempt["operation_kind"] = "account_issuance"
        attempt["request_sha256"] = _attempt_hash(
            "account_issuance",
            {"account_id": "a-credit", "amount": 50, "reference": "initial_balance"},
        )
        with pytest.raises(RuntimeError):
            await _issuance_call(envelope, operation_id)
    elif boundary == "release":
        envelope = _resolved_lock_observation(operation_id, "released")
        attempt = envelope["attempt"]
        assert isinstance(attempt, dict)
        attempt["operation_kind"] = "escrow_release"
        attempt["request_sha256"] = _attempt_hash(
            "escrow_release",
            {
                "escrow_id": "esc-historical",
                "recipient_account_id": "a-recipient",
                "source_reference": "task:t-historical:approved",
            },
        )
        with pytest.raises(RuntimeError):
            await _release_call(envelope, operation_id)
    else:
        envelope = _resolved_lock_observation(operation_id, "split")
        attempt = envelope["attempt"]
        assert isinstance(attempt, dict)
        attempt["operation_kind"] = "escrow_split"
        attempt["request_sha256"] = _attempt_hash(
            "escrow_split",
            {
                "escrow_id": "esc-historical",
                "worker_account_id": "a-worker",
                "poster_account_id": "a-payer",
                "worker_pct": 50,
                "source_reference": "task:t-historical:ruling:r-one",
            },
        )
        with pytest.raises(RuntimeError):
            await _split_call(envelope, operation_id)


async def test_credit_attempts_share_one_unchanged_historical_observation() -> None:
    results = [
        await _credit_call(
            _credit_observation(operation_id),
            operation_id,
            "a-credit",
            50,
            "historical-genesis",
        )
        for operation_id in _CREDIT_OPERATION_IDS
    ]

    assert results[0]["attempt"]["operation_id"] != results[1]["attempt"]["operation_id"]
    assert results[0]["observation"] == results[1]["observation"]
    observation = results[0]["observation"]
    assert observation["operation_id"] is None
    assert observation["transaction"]["timestamp"] == ""
    assert observation["transaction"]["event_id"] == -7


async def test_lock_attempts_preserve_cutover_fact_and_project_native_settlement() -> None:
    results = [
        await _lock_call(
            _lock_observation(operation_id),
            operation_id,
            "a-payer",
            40,
            "t-historical",
        )
        for operation_id in _LOCK_OPERATION_IDS
    ]

    assert results[0]["attempt"]["operation_id"] != results[1]["attempt"]["operation_id"]
    assert results[0]["observation"] == results[1]["observation"]
    observation = results[0]["observation"]
    assert observation["operation_id"] is None
    assert observation["observed_status"] == "locked"
    assert observation["resolution_fact"] is None
    assert observation["escrow_lock"]["created_at"] == ""
    assert observation["escrow_lock"]["event_id"] == -11
    assert results[0]["current_escrow"]["status"] == "released"
    assert results[0]["current_escrow"]["resolved_at"] == "2026-10-17T15:00:00Z"


CreditMutation = Callable[[dict[str, object]], None]


def _credit_parts(
    envelope: dict[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    attempt = envelope["attempt"]
    observation = envelope["observation"]
    assert isinstance(attempt, dict)
    assert isinstance(observation, dict)
    transaction = observation["transaction"]
    assert isinstance(transaction, dict)
    return attempt, observation, transaction


def _mutate_credit_attempt(case: str, attempt: dict[str, object]) -> None:
    if case == "attempt_id":
        attempt["operation_id"] = _CREDIT_OPERATION_IDS[1]
    elif case == "attempt_kind":
        attempt["operation_kind"] = "escrow_lock"
    else:
        attempt["request_sha256"] = "0" * 64


def _mutate_credit_business(
    case: str,
    observation: dict[str, object],
    transaction: dict[str, object],
) -> None:
    field = {"account": "account_id", "amount": "amount", "reference": "reference"}[case]
    value: object = {
        "account": "a-other",
        "amount": 51,
        "reference": "other-reference",
    }[case]
    transaction[field] = value
    observation["observed_fact_sha256"] = _hash(transaction)


def _mutate_credit_metadata(
    case: str,
    observation: dict[str, object],
    transaction: dict[str, object],
) -> None:
    if case == "observed_kind_wrong":
        observation["observed_kind"] = "observed_escrow_lock"
    elif case == "observed_kind_missing":
        observation.pop("observed_kind")
    elif case == "historical_operation_id":
        observation["operation_id"] = _CREDIT_OPERATION_IDS[0]
    elif case == "forged_fact_hash":
        observation["observed_fact_sha256"] = "f" * 64
    elif case == "raw_timestamp":
        transaction["timestamp"] = 7
        observation["observed_fact_sha256"] = _hash(transaction)
    elif case == "raw_event_id":
        transaction["event_id"] = "0"
        observation["observed_fact_sha256"] = _hash(transaction)
    else:
        transaction["event_id"] = SQLITE_MAX_INTEGER + 1
        observation["observed_fact_sha256"] = _hash(transaction)


def _mutate_credit_shape(
    case: str,
    envelope: dict[str, object],
    attempt: dict[str, object],
    observation: dict[str, object],
    transaction: dict[str, object],
) -> None:
    if case == "transaction_type":
        transaction["type"] = "escrow_lock"
    elif case == "extra_transaction":
        transaction["unexpected"] = "field"
    elif case == "extra_observation":
        observation["unexpected"] = "field"
    elif case == "extra_top":
        envelope["unexpected"] = "field"
    elif case == "missing_transaction_key":
        transaction.pop("timestamp")
    elif case == "missing_observation_key":
        observation.pop("observed_fact_sha256")
    elif case == "missing_attempt_key":
        attempt.pop("request_sha256")
    elif case == "missing_top_key":
        envelope.pop("current_escrow")
    if "observed_fact_sha256" in observation:
        observation["observed_fact_sha256"] = _hash(transaction)


def _mutate_credit_money(
    case: str,
    observation: dict[str, object],
    transaction: dict[str, object],
) -> None:
    if case == "coerced_amount":
        transaction["amount"] = "50"
    elif case == "boolean_amount":
        transaction["amount"] = True
    elif case == "amount_out_of_range":
        transaction["amount"] = SQLITE_MAX_INTEGER + 1
    elif case == "balance_negative":
        transaction["balance_after"] = -1
    else:
        transaction["balance_after"] = False
    observation["observed_fact_sha256"] = _hash(transaction)


def _credit_mutation(case: str) -> CreditMutation:
    def mutate(envelope: dict[str, object]) -> None:
        attempt, observation, transaction = _credit_parts(envelope)
        if case in {"attempt_id", "attempt_kind", "attempt_hash"}:
            _mutate_credit_attempt(case, attempt)
        elif case in {"account", "amount", "reference"}:
            _mutate_credit_business(case, observation, transaction)
        elif case in {
            "observed_kind_wrong",
            "observed_kind_missing",
            "historical_operation_id",
            "forged_fact_hash",
            "raw_timestamp",
            "raw_event_id",
            "event_out_of_range",
        }:
            _mutate_credit_metadata(case, observation, transaction)
        elif case in {
            "transaction_type",
            "extra_transaction",
            "extra_observation",
            "extra_top",
            "missing_transaction_key",
            "missing_observation_key",
            "missing_attempt_key",
            "missing_top_key",
        }:
            _mutate_credit_shape(case, envelope, attempt, observation, transaction)
        else:
            _mutate_credit_money(case, observation, transaction)

    return mutate


@pytest.mark.parametrize(
    "case",
    [
        "attempt_id",
        "attempt_kind",
        "attempt_hash",
        "account",
        "amount",
        "reference",
        "observed_kind_wrong",
        "observed_kind_missing",
        "transaction_type",
        "extra_transaction",
        "extra_observation",
        "extra_top",
        "missing_transaction_key",
        "missing_observation_key",
        "missing_attempt_key",
        "missing_top_key",
        "coerced_amount",
        "boolean_amount",
        "amount_out_of_range",
        "balance_negative",
        "balance_boolean",
        "historical_operation_id",
        "forged_fact_hash",
        "raw_timestamp",
        "raw_event_id",
        "event_out_of_range",
    ],
)
async def test_invalid_credit_observations_remain_rejected_controls(case: str) -> None:
    envelope = _credit_observation(_CREDIT_OPERATION_IDS[0])
    _credit_mutation(case)(envelope)

    with pytest.raises(RuntimeError):
        await _credit_call(
            envelope,
            _CREDIT_OPERATION_IDS[0],
            "a-credit",
            50,
            "historical-genesis",
        )


def _lock_parts(
    envelope: dict[str, object],
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    attempt = envelope["attempt"]
    observation = envelope["observation"]
    current = envelope["current_escrow"]
    assert isinstance(attempt, dict)
    assert isinstance(observation, dict)
    assert isinstance(current, dict)
    transaction = observation["transaction"]
    lock_fact = observation["escrow_lock"]
    assert isinstance(transaction, dict)
    assert isinstance(lock_fact, dict)
    return attempt, observation, current, transaction, lock_fact


def _mutate_lock_attempt(case: str, attempt: dict[str, object]) -> None:
    if case == "attempt_id":
        attempt["operation_id"] = _LOCK_OPERATION_IDS[1]
    elif case == "attempt_kind":
        attempt["operation_kind"] = "credit"
    else:
        attempt["request_sha256"] = "0" * 64


def _mutate_lock_business(
    case: str,
    observation: dict[str, object],
    transaction: dict[str, object],
    lock_fact: dict[str, object],
) -> None:
    if case == "payer":
        transaction["account_id"] = "a-other"
        lock_fact["payer_account_id"] = "a-other"
        observation["observed_fact_sha256"] = _hash(transaction)
        observation["lock_fact_sha256"] = _hash(lock_fact)
    elif case == "task":
        transaction["reference"] = "t-other"
        lock_fact["task_id"] = "t-other"
        observation["observed_fact_sha256"] = _hash(transaction)
        observation["lock_fact_sha256"] = _hash(lock_fact)
    elif case == "amount":
        transaction["amount"] = 41
        lock_fact["amount"] = 41
        observation["observed_fact_sha256"] = _hash(transaction)
        observation["lock_fact_sha256"] = _hash(lock_fact)


def _mutate_lock_relationships(
    case: str,
    observation: dict[str, object],
    current: dict[str, object],
    transaction: dict[str, object],
    lock_fact: dict[str, object],
) -> None:
    if case == "observed_kind_wrong":
        observation["observed_kind"] = "observed_credit"
    elif case == "observed_kind_missing":
        observation.pop("observed_kind")
    elif case == "transaction_type":
        transaction["type"] = "credit"
        observation["observed_fact_sha256"] = _hash(transaction)
    elif case == "current_payer":
        current["payer_account_id"] = "a-other"
    elif case == "current_escrow":
        current["escrow_id"] = "esc-other"
    elif case == "current_task":
        current["task_id"] = "t-other"
    elif case == "current_amount":
        current["amount"] = 41
    elif case == "lock_escrow":
        lock_fact["escrow_id"] = "esc-other"
        observation["lock_fact_sha256"] = _hash(lock_fact)
    elif case == "transaction_payer_mismatch":
        transaction["account_id"] = "a-other"
        observation["observed_fact_sha256"] = _hash(transaction)
    elif case == "transaction_task_mismatch":
        transaction["reference"] = "t-other"
        observation["observed_fact_sha256"] = _hash(transaction)
    elif case == "transaction_amount_mismatch":
        transaction["amount"] = 41
        observation["observed_fact_sha256"] = _hash(transaction)


def _mutate_lock_hash_or_identity(case: str, observation: dict[str, object]) -> None:
    if case == "historical_operation_id":
        observation["operation_id"] = _LOCK_OPERATION_IDS[0]
    elif case == "forged_transaction_hash":
        observation["observed_fact_sha256"] = "f" * 64
    else:
        observation["lock_fact_sha256"] = "f" * 64


def _mutate_lock_exact_shape(
    case: str,
    envelope: dict[str, object],
    attempt: dict[str, object],
    observation: dict[str, object],
    current: dict[str, object],
    transaction: dict[str, object],
    lock_fact: dict[str, object],
) -> None:
    if case == "extra_top":
        envelope["unexpected"] = "field"
    elif case == "extra_observation":
        observation["unexpected"] = "field"
    elif case == "extra_transaction":
        transaction["unexpected"] = "field"
        observation["observed_fact_sha256"] = _hash(transaction)
    elif case == "extra_lock":
        lock_fact["unexpected"] = "field"
        observation["lock_fact_sha256"] = _hash(lock_fact)
    elif case == "extra_current":
        current["unexpected"] = "field"
    elif case == "missing_attempt_key":
        attempt.pop("request_sha256")
    elif case == "missing_top_key":
        envelope.pop("current_escrow")
    elif case == "missing_observation_key":
        observation.pop("lock_fact_sha256")
    elif case == "missing_transaction_key":
        transaction.pop("timestamp")
        observation["observed_fact_sha256"] = _hash(transaction)
    elif case == "missing_lock_key":
        lock_fact.pop("created_at")
        observation["lock_fact_sha256"] = _hash(lock_fact)
    else:
        current.pop("created_at")


def _mutate_lock_money(
    case: str,
    observation: dict[str, object],
    current: dict[str, object],
    transaction: dict[str, object],
    lock_fact: dict[str, object],
) -> None:
    if case == "coerced_event":
        lock_fact["event_id"] = "-11"
    elif case == "raw_timestamp":
        transaction["timestamp"] = 7
    elif case == "transaction_event_out_of_range":
        transaction["event_id"] = SQLITE_MAX_INTEGER + 1
    elif case == "lock_event_out_of_range":
        lock_fact["event_id"] = SQLITE_MAX_INTEGER + 1
    elif case == "transaction_balance_negative":
        transaction["balance_after"] = -1
    else:
        invalid_amount: object = {
            "money_boolean": True,
            "money_zero": 0,
            "money_out_of_range": SQLITE_MAX_INTEGER + 1,
        }[case]
        transaction["amount"] = invalid_amount
        lock_fact["amount"] = invalid_amount
        current["amount"] = invalid_amount
    observation["observed_fact_sha256"] = _hash(transaction)
    observation["lock_fact_sha256"] = _hash(lock_fact)


_RESOLUTION_ID_CASES = {
    "unsorted_resolution_ids",
    "released_cardinality",
    "split_cardinality_zero",
    "split_cardinality_three",
    "duplicate_resolution_ids",
    "resolution_empty_id",
}


def _mutate_resolution_ids(case: str, transaction_ids: list[str]) -> None:
    if case == "unsorted_resolution_ids":
        transaction_ids.reverse()
    elif case == "released_cardinality":
        transaction_ids.append("tx-release-b")
    elif case == "split_cardinality_zero":
        transaction_ids.clear()
    elif case == "split_cardinality_three":
        transaction_ids.append("tx-release-c")
    elif case == "duplicate_resolution_ids":
        transaction_ids[1] = transaction_ids[0]
    else:
        transaction_ids[0] = ""


def _mutate_lock_resolution(case: str, observation: dict[str, object]) -> None:
    resolution = observation["resolution_fact"]
    if case == "resolution_with_locked_status":
        resolution = _resolution_fact("released", ["tx-release-a"])
        observation["resolution_fact"] = resolution
    elif case == "resolution_hash_without_fact":
        observation["resolution_fact_sha256"] = "f" * 64
        return
    else:
        assert isinstance(resolution, dict)
        transaction_ids = resolution["transaction_ids"]
        assert isinstance(transaction_ids, list)
        if case in _RESOLUTION_ID_CASES:
            _mutate_resolution_ids(case, transaction_ids)
        elif case == "resolution_escrow_mismatch":
            resolution["escrow_id"] = "esc-other"
        elif case == "resolution_status_mismatch":
            resolution["observed_status"] = "split"
        elif case == "resolution_time_mismatch":
            resolution["resolved_at"] = "different"
        else:
            observation["resolution_fact_sha256"] = "f" * 64
            return
    observation["resolution_fact_sha256"] = _hash(resolution)


_LOCK_RELATIONSHIP_CASES = {
    "observed_kind_wrong",
    "observed_kind_missing",
    "transaction_type",
    "current_payer",
    "current_escrow",
    "current_task",
    "current_amount",
    "lock_escrow",
    "transaction_payer_mismatch",
    "transaction_task_mismatch",
    "transaction_amount_mismatch",
}

_LOCK_HASH_CASES = {
    "historical_operation_id",
    "forged_transaction_hash",
    "forged_lock_hash",
}

_LOCK_SHAPE_CASES = {
    "extra_top",
    "extra_observation",
    "extra_transaction",
    "extra_lock",
    "extra_current",
    "missing_attempt_key",
    "missing_top_key",
    "missing_observation_key",
    "missing_transaction_key",
    "missing_lock_key",
    "missing_current_key",
}

_LOCK_MONEY_CASES = {
    "coerced_event",
    "raw_timestamp",
    "transaction_event_out_of_range",
    "lock_event_out_of_range",
    "transaction_balance_negative",
    "money_boolean",
    "money_zero",
    "money_out_of_range",
}

_LOCK_RESOLUTION_CASES = {
    "resolution_with_locked_status",
    "resolution_hash_without_fact",
    "unsorted_resolution_ids",
    "resolution_escrow_mismatch",
    "resolution_status_mismatch",
    "released_cardinality",
    "split_cardinality_zero",
    "split_cardinality_three",
    "duplicate_resolution_ids",
    "resolution_empty_id",
    "resolution_time_mismatch",
    "forged_resolution_hash",
}


def _legacy_lock_base_for_case(case: str) -> dict[str, object]:
    if case in {
        "unsorted_resolution_ids",
        "split_cardinality_zero",
        "split_cardinality_three",
        "duplicate_resolution_ids",
    }:
        return _resolved_lock_observation(_LOCK_OPERATION_IDS[0], "split")
    if case in {
        "resolution_escrow_mismatch",
        "resolution_status_mismatch",
        "released_cardinality",
        "forged_resolution_hash",
        "resolution_empty_id",
        "resolution_time_mismatch",
    }:
        return _resolved_lock_observation(_LOCK_OPERATION_IDS[0], "released")
    return _lock_observation(_LOCK_OPERATION_IDS[0])


def _lock_mutation(case: str, envelope: dict[str, object]) -> None:
    attempt, observation, current, transaction, lock_fact = _lock_parts(envelope)
    if case in {"attempt_id", "attempt_kind", "attempt_hash"}:
        _mutate_lock_attempt(case, attempt)
    elif case in {"payer", "task", "amount"}:
        _mutate_lock_business(case, observation, transaction, lock_fact)
    elif case in _LOCK_RELATIONSHIP_CASES:
        _mutate_lock_relationships(case, observation, current, transaction, lock_fact)
    elif case in _LOCK_HASH_CASES:
        _mutate_lock_hash_or_identity(case, observation)
    elif case in _LOCK_SHAPE_CASES:
        _mutate_lock_exact_shape(
            case,
            envelope,
            attempt,
            observation,
            current,
            transaction,
            lock_fact,
        )
    elif case in _LOCK_MONEY_CASES:
        _mutate_lock_money(case, observation, current, transaction, lock_fact)
    else:
        assert case in _LOCK_RESOLUTION_CASES
        _mutate_lock_resolution(case, observation)


@pytest.mark.parametrize(
    "case",
    [
        "attempt_id",
        "attempt_kind",
        "attempt_hash",
        "payer",
        "task",
        "amount",
        "observed_kind_wrong",
        "observed_kind_missing",
        "transaction_type",
        "current_payer",
        "current_escrow",
        "current_task",
        "current_amount",
        "lock_escrow",
        "transaction_payer_mismatch",
        "transaction_task_mismatch",
        "transaction_amount_mismatch",
        "historical_operation_id",
        "forged_transaction_hash",
        "forged_lock_hash",
        "resolution_with_locked_status",
        "resolution_hash_without_fact",
        "unsorted_resolution_ids",
        "resolution_escrow_mismatch",
        "resolution_status_mismatch",
        "released_cardinality",
        "split_cardinality_zero",
        "split_cardinality_three",
        "duplicate_resolution_ids",
        "resolution_empty_id",
        "resolution_time_mismatch",
        "forged_resolution_hash",
        "extra_top",
        "extra_observation",
        "extra_transaction",
        "extra_lock",
        "extra_current",
        "missing_attempt_key",
        "missing_top_key",
        "missing_observation_key",
        "missing_transaction_key",
        "missing_lock_key",
        "missing_current_key",
        "coerced_event",
        "raw_timestamp",
        "transaction_event_out_of_range",
        "lock_event_out_of_range",
        "transaction_balance_negative",
        "money_boolean",
        "money_zero",
        "money_out_of_range",
    ],
)
async def test_invalid_lock_observations_remain_rejected_controls(case: str) -> None:
    envelope = _legacy_lock_base_for_case(case)
    _lock_mutation(case, envelope)

    with pytest.raises(RuntimeError):
        await _lock_call(
            envelope,
            _LOCK_OPERATION_IDS[0],
            "a-payer",
            40,
            "t-historical",
        )


async def test_self_consistent_terminal_resolution_fact_is_preserved() -> None:
    envelope = _resolved_lock_observation(_LOCK_OPERATION_IDS[0], "released")
    observation = envelope["observation"]
    assert isinstance(observation, dict)
    resolution = _resolution_fact("released", ["tx-release-a"])

    result = await _lock_call(
        envelope,
        _LOCK_OPERATION_IDS[0],
        "a-payer",
        40,
        "t-historical",
    )

    assert result["observation"]["resolution_fact"] == resolution
    assert result["observation"]["resolution_fact_sha256"] == _hash(resolution)


async def test_self_consistent_split_resolution_fact_is_preserved() -> None:
    envelope = _resolved_lock_observation(_LOCK_OPERATION_IDS[0], "split")
    resolution = _resolution_fact("split", ["tx-release-a", "tx-release-b"])

    result = await _lock_call(
        envelope,
        _LOCK_OPERATION_IDS[0],
        "a-payer",
        40,
        "t-historical",
    )

    assert result["observation"]["observed_status"] == "split"
    assert result["observation"]["resolution_fact"] == resolution
    assert result["observation"]["resolution_fact_sha256"] == _hash(resolution)


async def test_self_consistent_one_leg_split_resolution_is_preserved() -> None:
    envelope = _resolved_lock_observation(_LOCK_OPERATION_IDS[0], "split")
    observation = envelope["observation"]
    assert isinstance(observation, dict)
    resolution = _resolution_fact("split", ["tx-release-a"])
    observation["resolution_fact"] = resolution
    observation["resolution_fact_sha256"] = _hash(resolution)
    _assert_lock_base_is_admissible(envelope, "split")

    result = await _lock_call(
        envelope,
        _LOCK_OPERATION_IDS[0],
        "a-payer",
        40,
        "t-historical",
    )

    assert result["observation"]["resolution_fact"] == resolution
    assert result["observation"]["resolution_fact_sha256"] == _hash(resolution)


@pytest.mark.parametrize("observed_status", ["released", "split"])
async def test_terminal_historical_null_resolution_time_is_preserved(
    observed_status: str,
) -> None:
    envelope = _resolved_lock_observation(_LOCK_OPERATION_IDS[0], observed_status)
    observation = envelope["observation"]
    current = envelope["current_escrow"]
    assert isinstance(observation, dict)
    assert isinstance(current, dict)
    resolution = observation["resolution_fact"]
    assert isinstance(resolution, dict)
    resolution["resolved_at"] = None
    observation["resolution_fact_sha256"] = _hash(resolution)
    current["resolved_at"] = None
    _assert_lock_base_is_admissible(envelope, observed_status)

    result = await _lock_call(
        envelope,
        _LOCK_OPERATION_IDS[0],
        "a-payer",
        40,
        "t-historical",
    )

    assert result["observation"]["resolution_fact"]["resolved_at"] is None
    assert result["current_escrow"]["resolved_at"] is None


@pytest.mark.parametrize("resolved_at", ["", "legacy-nonnull-time"])
async def test_locked_historical_nonnull_resolution_time_is_preserved(
    resolved_at: str,
) -> None:
    envelope = _lock_observation(_LOCK_OPERATION_IDS[0])
    current = envelope["current_escrow"]
    assert isinstance(current, dict)
    current["status"] = "locked"
    current["resolved_at"] = resolved_at
    _assert_lock_base_is_admissible(envelope, "locked")

    result = await _lock_call(
        envelope,
        _LOCK_OPERATION_IDS[0],
        "a-payer",
        40,
        "t-historical",
    )

    assert result["current_escrow"]["status"] == "locked"
    assert result["current_escrow"]["resolved_at"] == resolved_at


def _native_escrow_envelope(operation_kind: str, created_at: object) -> dict[str, object]:
    operation_id = "op-00000000-0000-4000-8000-000000000095"
    if operation_kind == "escrow_lock":
        fields: dict[str, object] = {
            "payer_account_id": "a-payer",
            "task_id": "t-historical",
            "amount": 40,
        }
        result = {"escrow_status_at_commit": "locked"}
        legs = [
            {
                "role": "payer",
                "account_id": "a-payer",
                "direction": "debit",
                "amount": 40,
                "transaction_id": "tx-native-lock",
                "balance_after": 60,
            }
        ]
        source_reference = "t-historical"
        status = "locked"
        resolved_at = None
    elif operation_kind == "escrow_release":
        fields = {
            "escrow_id": "esc-historical",
            "recipient_account_id": "a-worker",
            "source_reference": "task:t-historical:approved",
        }
        result = {"escrow_status_at_commit": "released"}
        legs = [
            {
                "role": "recipient",
                "account_id": "a-worker",
                "direction": "credit",
                "amount": 40,
                "transaction_id": "tx-native-release",
                "balance_after": 40,
            }
        ]
        source_reference = "task:t-historical:approved"
        status = "released"
        resolved_at = "2026-10-17T20:00:00Z"
    else:
        fields = {
            "escrow_id": "esc-historical",
            "worker_account_id": "a-worker",
            "poster_account_id": "a-payer",
            "worker_pct": 50,
            "source_reference": "task:t-historical:ruling:r-one",
        }
        result = {"escrow_status_at_commit": "split", "worker_pct": 50}
        legs = [
            {
                "role": "worker",
                "account_id": "a-worker",
                "direction": "credit",
                "amount": 20,
                "transaction_id": "tx-native-worker",
                "balance_after": 20,
            },
            {
                "role": "poster",
                "account_id": "a-payer",
                "direction": "credit",
                "amount": 20,
                "transaction_id": "tx-native-poster",
                "balance_after": 20,
            },
        ]
        source_reference = "task:t-historical:ruling:r-one"
        status = "split"
        resolved_at = "2026-10-17T20:00:00Z"
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            "request_sha256": _attempt_hash(operation_kind, fields),
            "source_reference": source_reference,
            "committed_at": "2026-10-17T20:00:00Z",
            "event_id": 1,
            "escrow_id": "esc-historical",
            "result": result,
            "legs": legs,
        },
        "current_escrow": {
            "escrow_id": "esc-historical",
            "payer_account_id": "a-payer",
            "task_id": "t-historical",
            "amount": 40,
            "status": status,
            "created_at": created_at,
            "resolved_at": resolved_at,
        },
    }


def _native_escrow_fields(operation_kind: str) -> dict[str, object]:
    if operation_kind == "escrow_lock":
        return {"payer_account_id": "a-payer", "task_id": "t-historical", "amount": 40}
    if operation_kind == "escrow_release":
        return {
            "escrow_id": "esc-historical",
            "recipient_account_id": "a-worker",
            "source_reference": "task:t-historical:approved",
        }
    return {
        "escrow_id": "esc-historical",
        "worker_account_id": "a-worker",
        "poster_account_id": "a-payer",
        "worker_pct": 50,
        "source_reference": "task:t-historical:ruling:r-one",
    }


@pytest.mark.parametrize("operation_kind", ["escrow_release", "escrow_split"])
async def test_native_settlement_preserves_empty_historical_creation_time(
    operation_kind: str,
) -> None:
    result = parse_native_financial_envelope(
        _native_escrow_envelope(operation_kind, ""),
        "op-00000000-0000-4000-8000-000000000095",
        operation_kind,
        _native_escrow_fields(operation_kind),
    )

    assert result["current_escrow"]["created_at"] == ""


async def test_native_lock_still_rejects_empty_creation_time() -> None:
    with pytest.raises(RuntimeError):
        parse_native_financial_envelope(
            _native_escrow_envelope("escrow_lock", ""),
            "op-00000000-0000-4000-8000-000000000095",
            "escrow_lock",
            _native_escrow_fields("escrow_lock"),
        )


@pytest.mark.parametrize("created_at", [7, "\ud800"])
async def test_native_projection_rejects_non_string_or_non_utf8_creation_time(
    created_at: object,
) -> None:
    with pytest.raises(RuntimeError):
        parse_native_financial_envelope(
            _native_escrow_envelope("escrow_release", created_at),
            "op-00000000-0000-4000-8000-000000000095",
            "escrow_release",
            _native_escrow_fields("escrow_release"),
        )
