"""MP-05 immutable caller-owned task-posting intent contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth import TaskPostingIntent
from service_auth.agent import BaseAgent
from service_auth.config import AgentConfig
from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import SQLITE_MAX_INTEGER
from service_auth.jws import TokenPolicy

pytestmark = pytest.mark.unit

_ACTOR_ID = "a-alice"
_OPERATION_ID = "op-00000000-0000-4000-8000-000000000051"
_TASK_ID = "t-00000000-0000-4000-8000-000000000052"
_MAX_DURATION_SECONDS = SQLITE_MAX_INTEGER // 1_000_000
_SEMANTIC_KEYS = {
    "protocol_version",
    "operation_id",
    "task_id",
    "actor_kind",
    "actor_id",
    "lock_operation_id",
    "title",
    "spec",
    "reward",
    "bidding_deadline_seconds",
    "acceptance_window_seconds",
    "execution_deadline_seconds",
    "review_deadline_seconds",
}


def _config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    return AgentConfig(
        name="Alice",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url="https://identity",
        bank_url="https://bank",
        task_board_url="https://board",
        reputation_url="https://reputation",
        court_url="https://court",
        token_ttl_seconds=300,
        token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
    )


def _agent() -> BaseAgent:
    agent = BaseAgent(_config())
    agent.agent_id = _ACTOR_ID
    return agent


def _intent_json() -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": _OPERATION_ID,
        "task_id": _TASK_ID,
        "actor_kind": "agent",
        "actor_id": _ACTOR_ID,
        "lock_operation_id": operation_id_for("task-lock", (_TASK_ID, _ACTOR_ID)),
        "title": "Publish exactly once",
        "spec": "Retain one caller-owned identity across an ambiguous response.",
        "reward": 25,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 30,
        "execution_deadline_seconds": 120,
        "review_deadline_seconds": 90,
    }


def _request_sha256(value: dict[str, object]) -> str:
    semantic = {key: item for key, item in value.items() if key != "operation_id"}
    encoded = json.dumps(
        semantic,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def test_prepare_allocates_one_immutable_exact_semantic_intent() -> None:
    agent = _agent()
    try:
        intent = agent.prepare_task_posting(
            title="Publish exactly once",
            spec="Retain one caller-owned identity across an ambiguous response.",
            reward=25,
            bidding_deadline_seconds=60,
            acceptance_window_seconds=30,
            execution_deadline_seconds=120,
            review_deadline_seconds=90,
        )
    finally:
        await agent.close()

    assert isinstance(intent, TaskPostingIntent)
    value = intent.as_json()
    assert set(value) == _SEMANTIC_KEYS
    assert value == {
        **value,
        "protocol_version": 1,
        "actor_kind": "agent",
        "actor_id": _ACTOR_ID,
        "title": "Publish exactly once",
        "spec": "Retain one caller-owned identity across an ambiguous response.",
        "reward": 25,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 30,
        "execution_deadline_seconds": 120,
        "review_deadline_seconds": 90,
    }
    assert isinstance(value["operation_id"], str)
    assert isinstance(value["task_id"], str)
    assert value["operation_id"].startswith("op-")
    assert value["task_id"].startswith("t-")
    assert value["lock_operation_id"] == operation_id_for(
        "task-lock", (str(value["task_id"]), _ACTOR_ID)
    )
    assert value["lock_operation_id"] != value["operation_id"]
    assert intent.request_sha256 == _request_sha256(value)
    with pytest.raises((AttributeError, FrozenInstanceError)):
        intent.title = "Changed"


def test_exact_canonical_round_trip_returns_independent_mutable_json_copies() -> None:
    source = _intent_json()
    intent = TaskPostingIntent.from_json(source)
    first = intent.as_json()
    second = intent.as_json()

    assert first == source
    assert second == source
    assert first is not second
    first["title"] = "Mutated copy"
    source["spec"] = "Mutated source"
    assert intent.as_json()["title"] == "Publish exactly once"
    assert intent.as_json()["spec"] == (
        "Retain one caller-owned identity across an ambiguous response."
    )
    assert TaskPostingIntent.from_json(intent.as_json()) == intent
    assert intent.request_sha256 == _request_sha256(intent.as_json())


def test_request_hash_excludes_only_publication_operation_id() -> None:
    first = _intent_json()
    second = {
        **first,
        "operation_id": "op-00000000-0000-4000-8000-000000000099",
    }

    first_intent = TaskPostingIntent.from_json(first)
    second_intent = TaskPostingIntent.from_json(second)

    assert first_intent.operation_id != second_intent.operation_id
    assert first_intent.request_sha256 == second_intent.request_sha256
    changed_title = {**second, "title": "Changed title"}
    assert TaskPostingIntent.from_json(changed_title).request_sha256 != first_intent.request_sha256


def test_request_hash_uses_unescaped_unicode_canonical_json() -> None:
    value = {
        **_intent_json(),
        "title": "Résumé ✨",
        "spec": "数学を解く — naïve escaping is forbidden.",
    }
    intent = TaskPostingIntent.from_json(value)
    canonical = json.dumps(
        {key: item for key, item in value.items() if key != "operation_id"},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert "Résumé".encode() in canonical
    assert "数学".encode() in canonical
    assert b"\\u" not in canonical
    assert intent.request_sha256 == hashlib.sha256(canonical).hexdigest()


async def test_two_preparations_with_identical_content_are_distinct_intents() -> None:
    agent = _agent()
    arguments = {
        "title": "Same terms",
        "spec": "These are two deliberate postings, not a retry.",
        "reward": 25,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 30,
        "execution_deadline_seconds": 120,
        "review_deadline_seconds": 90,
    }
    try:
        first = agent.prepare_task_posting(**arguments)
        second = agent.prepare_task_posting(**arguments)
    finally:
        await agent.close()

    assert first.operation_id != second.operation_id
    assert first.task_id != second.task_id
    assert first.lock_operation_id != second.lock_operation_id
    assert first.request_sha256 != second.request_sha256
    assert {
        key: value
        for key, value in first.as_json().items()
        if key not in {"operation_id", "task_id", "lock_operation_id"}
    } == {
        key: value
        for key, value in second.as_json().items()
        if key not in {"operation_id", "task_id", "lock_operation_id"}
    }


def _invalid_intent_cases() -> list[tuple[str, dict[str, object] | object]]:
    valid = _intent_json()
    cases: list[tuple[str, dict[str, object] | object]] = [
        ("not_object", []),
        ("missing_field", {key: value for key, value in valid.items() if key != "title"}),
        ("unknown_field", {**valid, "extra": "no"}),
    ]
    invalid_values: dict[str, tuple[object, ...]] = {
        "protocol_version": (True, 1.0, 0, 2),
        "operation_id": (True, "op-invalid", valid["lock_operation_id"]),
        "task_id": (True, "t-invalid", "t-AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        "actor_kind": (True, "platform", ""),
        "actor_id": (True, ""),
        "lock_operation_id": (True, "op-invalid", _OPERATION_ID),
        "title": (True, "", "x" * 201),
        "spec": (True, "", "x" * 10_001),
        "reward": (None, True, 25.0, "25", 0, -1, SQLITE_MAX_INTEGER + 1),
        "bidding_deadline_seconds": (None, True, 60.0, "60", 0, _MAX_DURATION_SECONDS + 1),
        "acceptance_window_seconds": (None, True, 30.0, "30", 0, _MAX_DURATION_SECONDS + 1),
        "execution_deadline_seconds": (None, True, 120.0, "120", 0, _MAX_DURATION_SECONDS + 1),
        "review_deadline_seconds": (None, True, 90.0, "90", 0, _MAX_DURATION_SECONDS + 1),
    }
    for field, values in invalid_values.items():
        for value in values:
            cases.append((f"{field}_{value!r}", {**valid, field: value}))
    return cases


@pytest.mark.parametrize(("_case", "value"), _invalid_intent_cases())
def test_from_json_rejects_noncanonical_mismatched_or_out_of_range_semantics(
    _case: str,
    value: dict[str, object] | object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        TaskPostingIntent.from_json(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", ""),
        ("title", "x" * 201),
        ("spec", ""),
        ("spec", "x" * 10_001),
        ("reward", True),
        ("reward", 25.0),
        ("reward", 0),
        ("bidding_deadline_seconds", True),
        ("bidding_deadline_seconds", 60.0),
        ("bidding_deadline_seconds", 0),
        ("acceptance_window_seconds", True),
        ("acceptance_window_seconds", 30.0),
        ("acceptance_window_seconds", 0),
        ("execution_deadline_seconds", True),
        ("execution_deadline_seconds", 120.0),
        ("execution_deadline_seconds", 0),
        ("review_deadline_seconds", True),
        ("review_deadline_seconds", 90.0),
        ("review_deadline_seconds", 0),
    ],
)
async def test_prepare_rejects_invalid_terms_without_transport(field: str, value: object) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("prepare_task_posting must not use transport")

    agent = _agent()
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    arguments: dict[str, Any] = {
        "title": "Title",
        "spec": "Specification",
        "reward": 25,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 30,
        "execution_deadline_seconds": 120,
        "review_deadline_seconds": 90,
    }
    arguments[field] = value
    try:
        with pytest.raises((TypeError, ValueError)):
            agent.prepare_task_posting(**arguments)
    finally:
        await agent.close()
    assert requests == []


async def test_unregistered_prepare_rejects_before_transport() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("unregistered preparation must not use transport")

    agent = BaseAgent(_config())
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="registered"):
            agent.prepare_task_posting(
                title="Title",
                spec="Specification",
                reward=25,
                bidding_deadline_seconds=60,
                acceptance_window_seconds=30,
                execution_deadline_seconds=120,
                review_deadline_seconds=90,
            )
    finally:
        await agent.close()
    assert requests == []
