"""MP-05 strict task-publication SDK transport and response contracts."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth import TaskPostingIntent, TaskPostingResponse, jws
from service_auth.agent import BaseAgent
from service_auth.config import AgentConfig
from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import SQLITE_MAX_INTEGER
from service_auth.jws import TokenPolicy, parse_jws

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_ACTOR_ID = "a-alice"
_OPERATION_ID = "op-00000000-0000-4000-8000-000000000051"
_TASK_ID = "t-00000000-0000-4000-8000-000000000052"
_ESCROW_ID = "esc-00000000-0000-4000-8000-000000000053"
_ADMITTED_AT = "2026-10-18T09:59:59.000000Z"
_PUBLISHED_AT = "2026-10-18T10:00:00.000000Z"
_BIDDING_DEADLINE = "2026-10-18T10:01:00.000000Z"
_ACCEPTANCE_DEADLINE = "2026-10-18T10:01:30.000000Z"
_BIDDING_CUTOFF_US = 1_792_317_660_000_000
_ACCEPTANCE_CUTOFF_US = 1_792_317_690_000_000
_RESULT_KEYS = {
    "task_id",
    "poster_id",
    "title",
    "spec",
    "reward",
    "bidding_deadline_seconds",
    "acceptance_window_seconds",
    "execution_deadline_seconds",
    "review_deadline_seconds",
    "status",
    "escrow_id",
    "created_at",
    "bidding_deadline",
    "acceptance_deadline",
    "bidding_cutoff_us",
    "acceptance_cutoff_us",
    "lifecycle_version",
    "timing_contract_version",
    "event_id",
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


def _intent() -> TaskPostingIntent:
    return TaskPostingIntent.from_json(
        {
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
    )


def _creation_result(intent: TaskPostingIntent) -> dict[str, object]:
    return {
        "task_id": intent.task_id,
        "poster_id": intent.actor_id,
        "title": intent.title,
        "spec": intent.spec,
        "reward": intent.reward,
        "bidding_deadline_seconds": intent.bidding_deadline_seconds,
        "acceptance_window_seconds": intent.acceptance_window_seconds,
        "execution_deadline_seconds": intent.execution_deadline_seconds,
        "review_deadline_seconds": intent.review_deadline_seconds,
        "status": "open",
        "escrow_id": _ESCROW_ID,
        "created_at": _PUBLISHED_AT,
        "bidding_deadline": _BIDDING_DEADLINE,
        "acceptance_deadline": _ACCEPTANCE_DEADLINE,
        "bidding_cutoff_us": _BIDDING_CUTOFF_US,
        "acceptance_cutoff_us": _ACCEPTANCE_CUTOFF_US,
        "lifecycle_version": 0,
        "timing_contract_version": 2,
        "event_id": 91,
    }


def _envelope(state: str) -> dict[str, object]:
    intent = _intent()
    published = state == "published"
    blocked = state == "blocked_integrity"
    return {
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "request_sha256": intent.request_sha256,
        "publication_state": state,
        "lock_operation_id": intent.lock_operation_id,
        "escrow_id": _ESCROW_ID if published else None,
        "admitted_at": _ADMITTED_AT,
        "published_at": _PUBLISHED_AT if published else None,
        "result": _creation_result(intent) if published else None,
        "error": (
            {
                "error": "publication_integrity_blocked",
                "message": "Publication evidence requires reconciliation",
                "details": {"reason": "receipt_invalid"},
            }
            if blocked
            else None
        ),
    }


def _blocked_wire() -> dict[str, object]:
    return {
        "error": "publication_integrity_blocked",
        "message": "Publication evidence requires reconciliation",
        "details": {"publication": _envelope("blocked_integrity")},
    }


async def _agent(handler: Callable[[httpx.Request], httpx.Response]) -> BaseAgent:
    agent = BaseAgent(_config())
    await agent._http.aclose()
    agent.agent_id = _ACTOR_ID
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return agent


def _json_body(request: httpx.Request) -> dict[str, Any]:
    value = json.loads(request.content)
    assert isinstance(value, dict)
    return value


def _task_payload(intent: TaskPostingIntent) -> dict[str, object]:
    return {
        "action": "create_task",
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "poster_id": intent.actor_id,
        "lock_operation_id": intent.lock_operation_id,
        "title": intent.title,
        "spec": intent.spec,
        "reward": intent.reward,
        "bidding_deadline_seconds": intent.bidding_deadline_seconds,
        "acceptance_window_seconds": intent.acceptance_window_seconds,
        "execution_deadline_seconds": intent.execution_deadline_seconds,
        "review_deadline_seconds": intent.review_deadline_seconds,
    }


def _escrow_payload(intent: TaskPostingIntent) -> dict[str, object]:
    return {
        "action": "escrow_lock",
        "operation_id": intent.lock_operation_id,
        "task_id": intent.task_id,
        "amount": intent.reward,
        "agent_id": intent.actor_id,
    }


def _clock_values() -> Iterator[datetime]:
    first = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    yield first
    yield first
    yield first + timedelta(seconds=1)
    yield first + timedelta(seconds=1)


async def test_submit_reuses_one_intent_but_signs_fresh_exact_tokens(
    reset_clock: None,
) -> None:
    del reset_clock
    intent = _intent()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json=_envelope("published"), request=request)

    clocks = _clock_values()
    jws._clock = lambda: next(clocks)
    agent = await _agent(handler)
    try:
        first = await agent.submit_task_posting(intent)
        second = await agent.submit_task_posting(intent)
    finally:
        await agent.close()

    assert isinstance(first, TaskPostingResponse)
    assert first.status_code == second.status_code == 201
    assert first.body == second.body == _envelope("published")
    assert len(requests) == 2
    first_body = _json_body(requests[0])
    second_body = _json_body(requests[1])
    assert set(first_body) == {"task_token", "escrow_token"}
    for request, body in zip(requests, (first_body, second_body), strict=True):
        assert request.method == "POST"
        assert request.url == httpx.URL("https://board/tasks")
        task = parse_jws(body["task_token"])
        escrow = parse_jws(body["escrow_token"])
        assert task.header.aud == "task-board"
        assert task.header.resource == "POST /tasks"
        assert task.payload == _task_payload(intent)
        assert "actor_id" not in task.payload
        assert escrow.header.aud == "central-bank"
        assert escrow.header.resource == "POST /escrow/lock"
        assert escrow.payload == _escrow_payload(intent)
    assert first_body["task_token"] != second_body["task_token"]
    assert first_body["escrow_token"] != second_body["escrow_token"]


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (202, _envelope("awaiting_lock")),
        (409, _blocked_wire()),
    ],
)
async def test_submit_accepts_exact_pending_and_blocked_contracts(
    status_code: int,
    body: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body, request=request)

    agent = await _agent(handler)
    try:
        result = await agent.submit_task_posting(_intent())
    finally:
        await agent.close()
    assert isinstance(result, TaskPostingResponse)
    assert result.status_code == status_code
    assert result.body == body


@pytest.mark.parametrize(
    "reason",
    [
        "receipt_invalid",
        "escrow_invalid",
        "settlement_conflict",
        "publication_conflict",
        "clock_out_of_range",
    ],
)
async def test_submit_accepts_every_exact_integrity_block_reason(reason: str) -> None:
    body = _blocked_wire()
    details = body["details"]
    assert isinstance(details, dict)
    envelope = details["publication"]
    assert isinstance(envelope, dict)
    error = envelope["error"]
    assert isinstance(error, dict)
    error_details = error["details"]
    assert isinstance(error_details, dict)
    error_details["reason"] = reason

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json=body, request=request)

    agent = await _agent(handler)
    try:
        response = await agent.submit_task_posting(_intent())
    finally:
        await agent.close()
    assert response.status_code == 409
    assert response.body == body


@pytest.mark.parametrize("state", ["awaiting_lock", "published", "blocked_integrity"])
async def test_owner_status_get_accepts_all_states_and_signs_exact_authority(state: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_envelope(state), request=request)

    agent = await _agent(handler)
    try:
        result = await agent.get_task_posting(_OPERATION_ID)
    finally:
        await agent.close()

    assert result.status_code == 200
    assert result.body == _envelope(state)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url == httpx.URL(f"https://board/task-publications/{_OPERATION_ID}")
    token = parse_jws(request.headers["authorization"].removeprefix("Bearer "))
    assert token.header.aud == "task-board"
    assert token.header.resource == f"GET /task-publications/{_OPERATION_ID}"
    assert token.payload == {
        "action": "get_task_publication",
        "operation_id": _OPERATION_ID,
        "poster_id": _ACTOR_ID,
    }


async def test_submit_rejects_foreign_registered_owner_before_signing_or_transport() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("foreign intent must be rejected before transport")

    agent = await _agent(handler)
    agent.agent_id = "a-foreign"
    try:
        with patch.object(agent, "_sign_jws", wraps=agent._sign_jws) as signer:
            with pytest.raises(ValueError, match=r"owner|actor"):
                await agent.submit_task_posting(_intent())
            signer.assert_not_called()
    finally:
        await agent.close()
    assert requests == []


@pytest.mark.parametrize("method", ["submit", "get"])
async def test_unregistered_publication_calls_reject_before_transport(method: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("unregistered publication call must not use transport")

    agent = BaseAgent(_config())
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="registered"):
            if method == "submit":
                await agent.submit_task_posting(_intent())
            else:
                await agent.get_task_posting(_OPERATION_ID)
    finally:
        await agent.close()
    assert requests == []


@pytest.mark.parametrize(
    "operation_id", [None, True, "op-invalid", "OP-00000000-0000-4000-8000-000000000051"]
)
async def test_status_get_rejects_invalid_operation_id_before_transport(
    operation_id: object,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("invalid operation ID must not use transport")

    agent = await _agent(handler)
    try:
        with pytest.raises(ValueError, match="operation_id"):
            await agent.get_task_posting(operation_id)
    finally:
        await agent.close()
    assert requests == []


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (
            409,
            {
                "error": "operation_id_conflict",
                "message": "Operation identity is already bound to different terms",
                "details": {},
            },
        ),
    ],
)
async def test_non_acknowledgment_http_errors_are_preserved(
    status_code: int,
    body: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body, request=request)

    agent = await _agent(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await agent.submit_task_posting(_intent())
    finally:
        await agent.close()
    assert exc_info.value.response.status_code == status_code
    assert exc_info.value.response.json() == body


async def test_status_get_preserves_publication_not_found_error() -> None:
    body = {
        "error": "publication_not_found",
        "message": "Publication not found",
        "details": {},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json=body, request=request)

    agent = await _agent(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await agent.get_task_posting(_OPERATION_ID)
    finally:
        await agent.close()
    assert exc_info.value.response.status_code == 404
    assert exc_info.value.response.json() == body


@pytest.mark.parametrize(
    ("content", "json_body"),
    [
        (b"{", None),
        (None, []),
    ],
)
async def test_success_response_rejects_invalid_json_or_non_object_body(
    content: bytes | None,
    json_body: object,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if content is not None:
            return httpx.Response(
                201,
                content=content,
                headers={"content-type": "application/json"},
                request=request,
            )
        return httpx.Response(201, json=json_body, request=request)

    agent = await _agent(handler)
    try:
        with pytest.raises(RuntimeError):
            await agent.submit_task_posting(_intent())
    finally:
        await agent.close()


def _replace(
    value: dict[str, object], path: tuple[str, ...], replacement: object
) -> dict[str, object]:
    changed = copy.deepcopy(value)
    target: dict[str, object] = changed
    for key in path[:-1]:
        child = target[key]
        assert isinstance(child, dict)
        target = child
    target[path[-1]] = replacement
    return changed


def _invalid_published_bodies() -> list[tuple[str, int, dict[str, object]]]:
    valid = _envelope("published")
    result = _creation_result(_intent())
    cases = [
        ("unexpected_status", 200, valid),
        ("status_state_mismatch", 202, valid),
        ("unknown_envelope_field", 201, {**valid, "extra": True}),
        (
            "missing_envelope_field",
            201,
            {key: value for key, value in valid.items() if key != "admitted_at"},
        ),
        ("bool_protocol", 201, _replace(valid, ("protocol_version",), True)),
        ("float_protocol", 201, _replace(valid, ("protocol_version",), 1.0)),
        (
            "mismatched_operation",
            201,
            _replace(valid, ("operation_id",), "op-00000000-0000-4000-8000-000000000099"),
        ),
        (
            "mismatched_task",
            201,
            _replace(valid, ("task_id",), "t-00000000-0000-4000-8000-000000000099"),
        ),
        ("mismatched_hash", 201, _replace(valid, ("request_sha256",), "0" * 64)),
        ("uppercase_hash", 201, _replace(valid, ("request_sha256",), "A" * 64)),
        ("mismatched_lock", 201, _replace(valid, ("lock_operation_id",), _OPERATION_ID)),
        ("unknown_state", 201, _replace(valid, ("publication_state",), "complete")),
        ("null_escrow", 201, _replace(valid, ("escrow_id",), None)),
        ("empty_escrow", 201, _replace(valid, ("escrow_id",), "")),
        ("null_published_at", 201, _replace(valid, ("published_at",), None)),
        ("null_result", 201, _replace(valid, ("result",), None)),
        ("published_error", 201, _replace(valid, ("error",), {})),
        ("result_unknown_field", 201, _replace(valid, ("result",), {**result, "extra": True})),
        (
            "result_missing_field",
            201,
            _replace(
                valid,
                ("result",),
                {key: value for key, value in result.items() if key != "event_id"},
            ),
        ),
        (
            "result_task_mismatch",
            201,
            _replace(valid, ("result", "task_id"), "t-00000000-0000-4000-8000-000000000099"),
        ),
        ("result_poster_mismatch", 201, _replace(valid, ("result", "poster_id"), "a-foreign")),
        ("result_terms_mismatch", 201, _replace(valid, ("result", "reward"), 26)),
        ("result_reward_float", 201, _replace(valid, ("result", "reward"), 25.0)),
        (
            "result_bidding_duration_float",
            201,
            _replace(valid, ("result", "bidding_deadline_seconds"), 60.0),
        ),
        (
            "result_acceptance_duration_float",
            201,
            _replace(valid, ("result", "acceptance_window_seconds"), 30.0),
        ),
        (
            "result_execution_duration_float",
            201,
            _replace(valid, ("result", "execution_deadline_seconds"), 120.0),
        ),
        (
            "result_review_duration_float",
            201,
            _replace(valid, ("result", "review_deadline_seconds"), 90.0),
        ),
        ("result_status", 201, _replace(valid, ("result", "status"), "accepted")),
        ("result_escrow_mismatch", 201, _replace(valid, ("result", "escrow_id"), "esc-other")),
        ("created_mismatch", 201, _replace(valid, ("result", "created_at"), _ADMITTED_AT)),
        (
            "deadline_arithmetic",
            201,
            _replace(valid, ("result", "bidding_deadline"), _ACCEPTANCE_DEADLINE),
        ),
        ("invalid_admitted_timestamp", 201, _replace(valid, ("admitted_at",), "not-a-timestamp")),
        ("invalid_published_timestamp", 201, _replace(valid, ("published_at",), "not-a-timestamp")),
        (
            "admitted_timestamp_missing_microseconds",
            201,
            _replace(valid, ("admitted_at",), "2026-10-18T09:59:59Z"),
        ),
        (
            "published_timestamp_offset_spelling",
            201,
            _replace(valid, ("published_at",), "2026-10-18T10:00:00.000000+00:00"),
        ),
        (
            "result_timestamp_wrong_precision",
            201,
            _replace(valid, ("result", "created_at"), "2026-10-18T10:00:00.00000Z"),
        ),
        (
            "bidding_deadline_missing_microseconds",
            201,
            _replace(valid, ("result", "bidding_deadline"), "2026-10-18T10:01:00Z"),
        ),
        (
            "acceptance_deadline_wrong_precision",
            201,
            _replace(valid, ("result", "acceptance_deadline"), "2026-10-18T10:01:30.0000000Z"),
        ),
        ("cutoff_bool", 201, _replace(valid, ("result", "bidding_cutoff_us"), True)),
        (
            "cutoff_float",
            201,
            _replace(valid, ("result", "bidding_cutoff_us"), float(_BIDDING_CUTOFF_US)),
        ),
        (
            "acceptance_cutoff_float",
            201,
            _replace(valid, ("result", "acceptance_cutoff_us"), float(_ACCEPTANCE_CUTOFF_US)),
        ),
        ("cutoff_nonpositive", 201, _replace(valid, ("result", "bidding_cutoff_us"), 0)),
        (
            "cutoff_out_of_range",
            201,
            _replace(valid, ("result", "bidding_cutoff_us"), SQLITE_MAX_INTEGER + 1),
        ),
        (
            "cutoff_mismatch",
            201,
            _replace(valid, ("result", "acceptance_cutoff_us"), _BIDDING_CUTOFF_US),
        ),
        ("lifecycle_bool", 201, _replace(valid, ("result", "lifecycle_version"), False)),
        ("lifecycle_float", 201, _replace(valid, ("result", "lifecycle_version"), 0.0)),
        ("lifecycle_nonzero", 201, _replace(valid, ("result", "lifecycle_version"), 1)),
        ("timing_version", 201, _replace(valid, ("result", "timing_contract_version"), 1)),
        ("timing_float", 201, _replace(valid, ("result", "timing_contract_version"), 2.0)),
        ("event_bool", 201, _replace(valid, ("result", "event_id"), True)),
        ("event_float", 201, _replace(valid, ("result", "event_id"), 91.0)),
        ("event_nonpositive", 201, _replace(valid, ("result", "event_id"), 0)),
    ]
    return cases


@pytest.mark.parametrize(("_case", "status_code", "body"), _invalid_published_bodies())
async def test_submit_rejects_malformed_mismatched_or_contradictory_publication(
    _case: str,
    status_code: int,
    body: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body, request=request)

    agent = await _agent(handler)
    try:
        with pytest.raises(RuntimeError):
            await agent.submit_task_posting(_intent())
    finally:
        await agent.close()


@pytest.mark.parametrize(
    ("_case", "body"),
    [
        ("pending_escrow", _replace(_envelope("awaiting_lock"), ("escrow_id",), _ESCROW_ID)),
        ("pending_result", _replace(_envelope("awaiting_lock"), ("result",), {})),
        ("pending_error", _replace(_envelope("awaiting_lock"), ("error",), {})),
        (
            "pending_published_at",
            _replace(_envelope("awaiting_lock"), ("published_at",), _PUBLISHED_AT),
        ),
        ("blocked_result", _replace(_envelope("blocked_integrity"), ("result",), {})),
        ("blocked_escrow", _replace(_envelope("blocked_integrity"), ("escrow_id",), _ESCROW_ID)),
        (
            "blocked_published_at",
            _replace(_envelope("blocked_integrity"), ("published_at",), _PUBLISHED_AT),
        ),
        ("blocked_missing_error", _replace(_envelope("blocked_integrity"), ("error",), None)),
        (
            "blocked_unknown_reason",
            _replace(
                _envelope("blocked_integrity"),
                ("error", "details", "reason"),
                "unknown",
            ),
        ),
        (
            "blocked_recursive_error",
            _replace(
                _envelope("blocked_integrity"),
                ("error", "details"),
                {"reason": "receipt_invalid", "publication": {}},
            ),
        ),
    ],
)
async def test_status_get_rejects_invalid_state_nullability_or_blocked_error(
    _case: str,
    body: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, request=request)

    agent = await _agent(handler)
    try:
        with pytest.raises(RuntimeError):
            await agent.get_task_posting(_OPERATION_ID)
    finally:
        await agent.close()


@pytest.mark.parametrize(
    ("_case", "body"),
    [
        ("wrapper_extra", {**_blocked_wire(), "extra": True}),
        ("wrong_message", {**_blocked_wire(), "message": "Blocked"}),
        ("missing_publication", {**_blocked_wire(), "details": {}}),
        (
            "published_in_blocked_wrapper",
            {**_blocked_wire(), "details": {"publication": _envelope("published")}},
        ),
    ],
)
async def test_submit_rejects_malformed_blocked_409_wrapper(
    _case: str,
    body: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json=body, request=request)

    agent = await _agent(handler)
    try:
        with pytest.raises(RuntimeError):
            await agent.submit_task_posting(_intent())
    finally:
        await agent.close()


def test_creation_fixture_has_exact_nineteen_keys_and_consistent_cutoffs() -> None:
    result = _creation_result(_intent())
    assert set(result) == _RESULT_KEYS
    assert len(result) == 19
    published = datetime.fromisoformat(_PUBLISHED_AT.replace("Z", "+00:00"))
    bidding = datetime.fromisoformat(_BIDDING_DEADLINE.replace("Z", "+00:00"))
    acceptance = datetime.fromisoformat(_ACCEPTANCE_DEADLINE.replace("Z", "+00:00"))
    assert bidding == published + timedelta(seconds=60)
    assert acceptance == bidding + timedelta(seconds=30)
    assert int(bidding.timestamp() * 1_000_000) == _BIDDING_CUTOFF_US
    assert int(acceptance.timestamp() * 1_000_000) == _ACCEPTANCE_CUTOFF_US
