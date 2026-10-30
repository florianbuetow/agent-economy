"""MP-07 Court ruling delivery binds one native Task Board attempt."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.config import AgentConfig
from service_auth.financial_identity import operation_id_for
from service_auth.jws import TokenPolicy, parse_jws
from service_auth.platform import PlatformAgent
from service_commons.exceptions import ServiceError

from court_service.services.ruling_orchestrator import RulingClaim, RulingOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_DISPUTE_ID = "clm-00000000-0000-0000-0000-000000000001"
_TASK_ID = "t-00000000-0000-0000-0000-000000000001"
_OPERATION_ID = operation_id_for("court-ruling", (_DISPUTE_ID,))
_SQLITE_MAX_INTEGER = (1 << 63) - 1
_SUMMARY = "The worker substantially completed the task."
_LEASE_ID = "lease-00000000-0000-0000-0000-000000000001"


def _dispute() -> dict[str, Any]:
    return {
        "dispute_id": _DISPUTE_ID,
        "task_id": _TASK_ID,
        "claimant_id": "a-poster",
        "respondent_id": "a-worker",
        "claim": "The submitted result is incomplete.",
        "rebuttal": "The result follows the specification.",
        "status": "judging",
    }


def _claim() -> RulingClaim:
    return RulingClaim(dispute=_dispute(), lease_id=_LEASE_ID, decided=False)


def _task(version: object = 3) -> dict[str, Any]:
    return {
        "task_id": _TASK_ID,
        "title": "Ruling producer",
        "spec": "Produce the requested artifact.",
        "reward": 100,
        "deliverables": [],
        "lifecycle_version": version,
    }


def _orchestrator() -> tuple[RulingOrchestrator, MagicMock]:
    store = MagicMock()
    store.get_dispute.return_value = {**_dispute(), "status": "ruled"}
    return (
        RulingOrchestrator(
            store=store,
            feedback_extremely_satisfied_cutoff=80,
            feedback_satisfied_cutoff=40,
            feedback_comment_max_length=256,
            judging_lease_seconds=60,
        ),
        store,
    )


def _judge(worker_pct: int = 70) -> MagicMock:
    judge = MagicMock()
    judge.evaluate = AsyncMock(
        return_value={
            "schema_version": 1,
            "judge_id": "judge-0",
            "worker_pct": worker_pct,
            "spec_quality": "satisfied",
            "spec_quality_explanation": _SUMMARY,
            "delivery_quality": "satisfied",
            "delivery_quality_explanation": _SUMMARY,
            "reasoning": _SUMMARY,
            "voted_at": "2026-10-18T12:00:00Z",
        }
    )
    return judge


def _completed_ack(worker_pct: int = 70) -> dict[str, Any]:
    """Valid public response: current task view plus immutable operation result."""
    financial_request: dict[str, Any] = {
        "request_version": 1,
        "escrow_id": "esc-1",
        "source_reference": f"task:{_TASK_ID}:ruling:{_DISPUTE_ID}",
    }
    if worker_pct in {0, 100}:
        financial_request.update(
            operation_kind="escrow_release",
            recipient_account_id="a-worker" if worker_pct == 100 else "a-poster",
        )
        effect_kind = "bank_release"
    else:
        financial_request.update(
            operation_kind="escrow_split",
            worker_account_id="a-worker",
            poster_account_id="a-poster",
            worker_pct=worker_pct,
        )
        effect_kind = "bank_split"
    command = _native_command(worker_pct)
    return {
        "task_id": _TASK_ID,
        "poster_id": "a-poster",
        "title": "Ruling producer",
        "spec": "Produce the requested artifact.",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "deadline_seconds": 60,
        "review_deadline_seconds": 60,
        "status": "ruled",
        "escrow_id": "esc-1",
        "bid_count": 1,
        "worker_id": "a-worker",
        "accepted_bid_id": "bid-1",
        "created_at": "2099-01-01T00:00:00Z",
        "accepted_at": "2099-01-01T00:01:01Z",
        "submitted_at": "2099-01-01T00:02:02Z",
        "approved_at": None,
        "cancelled_at": None,
        "disputed_at": "2099-01-01T00:02:30Z",
        "dispute_reason": "The submitted result is incomplete.",
        "dispute_id": _DISPUTE_ID,
        "rebuttal_deadline": "2099-01-01T00:03:00Z",
        "rebuttal_submitted_at": "2099-01-01T00:02:45Z",
        "ruling_id": _DISPUTE_ID,
        "ruled_at": "2099-01-01T00:02:50Z",
        "worker_pct": worker_pct,
        "ruling_summary": _SUMMARY,
        "expired_at": None,
        "escrow_pending": False,
        "bidding_deadline": "2099-01-01T00:01:00Z",
        "acceptance_deadline": "2099-01-01T00:02:00Z",
        "execution_deadline": "2099-01-01T00:02:01Z",
        "review_deadline": "2099-01-01T00:03:02Z",
        "lifecycle_version": 4,
        "timing_contract_version": 2,
        "acceptance_window_seconds": 60,
        "bidding_cutoff_us": 4_070_908_860_000_000,
        "acceptance_cutoff_us": 4_070_908_920_000_000,
        "execution_cutoff_us": 4_070_908_921_000_000,
        "review_cutoff_us": 4_070_908_982_000_000,
        "accepted_reward": 100,
        "accepted_bid_amount": 90,
        "pending_transition": None,
        "legacy_reconciliations": [],
        "operation": {
            "protocol_version": 1,
            "operation_id": _OPERATION_ID,
            "task_id": _TASK_ID,
            "command": "record_ruling",
            "request_sha256": _canonical_hash(
                {key: value for key, value in command.items() if key != "operation_id"}
            ),
            "operation_state": "completed",
            "source_status": "disputed",
            "source_lifecycle_version": 3,
            "target_status": "ruled",
            "target_lifecycle_version": 4,
            "effect_kind": effect_kind,
            "effect_state": "succeeded",
            "effect_operation_id": _OPERATION_ID,
            "effect_result_reference": _OPERATION_ID,
            "effect_intent": {
                "financial_request": financial_request,
                "financial_request_sha256": _canonical_hash(financial_request),
                "expected_escrow": {
                    "escrow_id": "esc-1",
                    "task_id": _TASK_ID,
                    "payer_account_id": "a-poster",
                    "amount": 100,
                },
            },
            "event_id": 17,
            "result": {
                "task_id": _TASK_ID,
                "command": "record_ruling",
                "status": "ruled",
                "lifecycle_version": 4,
                "event_id": 17,
                "effect_result_reference": _OPERATION_ID,
                "worker_id": "a-worker",
                "ruling_id": _DISPUTE_ID,
                "worker_pct": worker_pct,
                "summary": _SUMMARY,
                "reward": 100,
            },
        },
    }


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _native_command(worker_pct: int = 70) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "operation_id": _OPERATION_ID,
        "task_id": _TASK_ID,
        "command": "record_ruling",
        "expected_status": "disputed",
        "expected_lifecycle_version": 3,
        "actor_kind": "platform",
        "actor_id": "a-platform",
        "arguments": {
            "poster_id": "a-poster",
            "worker_id": "a-worker",
            "escrow_id": "esc-1",
            "reward": 100,
            "ruling_id": _DISPUTE_ID,
            "worker_pct": worker_pct,
            "summary": _SUMMARY,
            "source_reference": f"task:{_TASK_ID}:ruling:{_DISPUTE_ID}",
        },
    }


def _synthetic_later_projection_ack() -> dict[str, Any]:
    """Boundary control: immutable result wins over a synthetic newer projection."""
    ack = _completed_ack()
    ack["lifecycle_version"] = 9
    ack["ruling_summary"] = "Synthetic later projection"
    return ack


def _platform() -> MagicMock:
    platform = MagicMock()
    platform.agent_id = "a-platform"
    platform.record_ruling = AsyncMock(return_value=_completed_ack())
    platform.submit_platform_feedback = AsyncMock(return_value={"status": "recorded"})
    platform.get_task = AsyncMock(return_value={**_task(4)})
    return platform


def _real_platform() -> PlatformAgent:
    private_key = Ed25519PrivateKey.generate()
    agent = PlatformAgent(
        AgentConfig(
            name="Court platform agent",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url="https://identity",
            bank_url="https://bank",
            task_board_url="https://board",
            reputation_url="https://reputation",
            court_url="https://court",
            token_ttl_seconds=60,
            token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=5),
        )
    )
    agent.agent_id = "a-platform"
    return agent


def _ack_case(case: str) -> object:
    if case == "nonmapping_ack":
        return []
    ack = copy.deepcopy(_completed_ack())
    operation = ack["operation"]
    assert isinstance(operation, dict)
    if case == "foreign_current_task":
        ack["task_id"] = "t-foreign"
    elif case == "missing_operation":
        ack.pop("operation")
    elif case == "pending":
        operation.update(
            operation_state="claimed",
            effect_state="pending",
            effect_result_reference=None,
            event_id=None,
            result=None,
        )
    elif case == "missing_result":
        operation.pop("result")
    elif case == "null_result":
        operation["result"] = None
    elif case == "nonmapping_result":
        operation["result"] = []
    else:
        mutations: dict[str, tuple[tuple[str, ...], object]] = {
            "wrong_protocol": (("protocol_version",), 2),
            "foreign_operation": (
                ("operation_id",),
                "op-00000000-0000-0000-0000-000000000099",
            ),
            "foreign_task": (("task_id",), "t-foreign"),
            "wrong_command": (("command",), "approve_task"),
            "wrong_source_status": (("source_status",), "submitted"),
            "wrong_source_version": (("source_lifecycle_version",), 2),
            "wrong_target_status": (("target_status",), "approved"),
            "wrong_target_version": (("target_lifecycle_version",), 5),
            "wrong_effect_kind": (("effect_kind",), "bank_release"),
            "wrong_effect_state": (("effect_state",), "pending"),
            "wrong_effect_operation": (
                ("effect_operation_id",),
                "op-00000000-0000-0000-0000-000000000099",
            ),
            "wrong_receipt_reference": (
                ("effect_result_reference",),
                "op-00000000-0000-0000-0000-000000000099",
            ),
            "wrong_event": (("result", "event_id"), 18),
            "nonpositive_event": (("event_id",), 0),
            "bool_event": (("event_id",), True),
            "wrong_result_task": (("result", "task_id"), "t-foreign"),
            "wrong_result_command": (("result", "command"), "approve_task"),
            "wrong_result_status": (("result", "status"), "approved"),
            "wrong_result_version": (("result", "lifecycle_version"), 5),
            "wrong_result_effect_reference": (
                ("result", "effect_result_reference"),
                "op-00000000-0000-0000-0000-000000000099",
            ),
            "wrong_result_reward": (("result", "reward"), 99),
            "wrong_ruling_id": (("result", "ruling_id"), "clm-foreign"),
            "wrong_worker_pct": (("result", "worker_pct"), 69),
            "wrong_summary": (("result", "summary"), "Different immutable ruling"),
        }
        if case not in mutations:
            raise AssertionError(f"Unknown acknowledgment case: {case}")
        path, value = mutations[case]
        target = operation
        for key in path[:-1]:
            nested = target[key]
            assert isinstance(nested, dict)
            target = nested
        target[path[-1]] = value
        if case in {"nonpositive_event", "bool_event"}:
            result = operation["result"]
            assert isinstance(result, dict)
            result["event_id"] = value
    return ack


async def test_valid_completed_ack_allows_feedback_and_local_persist() -> None:
    orchestrator, store = _orchestrator()
    judge = _judge()
    platform = _platform()

    ruled = await orchestrator.finish_ruling(
        _DISPUTE_ID,
        _claim(),
        [judge],
        _task(3),
        platform,
    )

    assert ruled["status"] == "ruled"
    platform.record_ruling.assert_awaited_once()
    platform.submit_platform_feedback.assert_awaited()
    store.complete_ruling.assert_called_once_with(_DISPUTE_ID, _LEASE_ID)


@pytest.mark.parametrize(
    ("worker_pct", "recipient_account_id"),
    [(0, "a-poster"), (100, "a-worker")],
)
async def test_completed_release_ack_allows_feedback_and_local_persist(
    worker_pct: int,
    recipient_account_id: str,
) -> None:
    orchestrator, store = _orchestrator()
    judge = _judge(worker_pct)
    platform = _platform()
    acknowledgment = _completed_ack(worker_pct)
    platform.record_ruling = AsyncMock(return_value=acknowledgment)

    ruled = await orchestrator.finish_ruling(
        _DISPUTE_ID,
        _claim(),
        [judge],
        _task(3),
        platform,
    )

    operation = acknowledgment["operation"]
    assert ruled["status"] == "ruled"
    assert operation["effect_kind"] == "bank_release"
    assert operation["effect_intent"]["financial_request"]["recipient_account_id"] == (
        recipient_account_id
    )
    assert operation["effect_result_reference"] == _OPERATION_ID
    assert operation["result"]["effect_result_reference"] == _OPERATION_ID
    platform.submit_platform_feedback.assert_awaited()
    store.complete_ruling.assert_called_once_with(_DISPUTE_ID, _LEASE_ID)


@pytest.mark.parametrize(
    ("worker_pct", "wrong_recipient"),
    [(0, "a-worker"), (100, "a-poster")],
)
async def test_release_ack_rejects_wrong_settlement_recipient(
    worker_pct: int,
    wrong_recipient: str,
) -> None:
    orchestrator, store = _orchestrator()
    judge = _judge(worker_pct)
    platform = _platform()
    acknowledgment = _completed_ack(worker_pct)
    operation = acknowledgment["operation"]
    intent = operation["effect_intent"]
    financial_request = intent["financial_request"]
    financial_request["recipient_account_id"] = wrong_recipient
    intent["financial_request_sha256"] = _canonical_hash(financial_request)
    platform.record_ruling = AsyncMock(return_value=acknowledgment)

    with pytest.raises(ServiceError) as raised:
        await orchestrator.finish_ruling(
            _DISPUTE_ID,
            _claim(),
            [judge],
            _task(3),
            platform,
        )

    assert raised.value.status_code == 502
    assert raised.value.error == "invalid_task_board_ack"
    platform.submit_platform_feedback.assert_not_awaited()
    store.complete_ruling.assert_not_called()
    store.release_judging_lease.assert_called_once_with(_DISPUTE_ID, _LEASE_ID)


async def test_synthetic_later_projection_uses_immutable_operation_result() -> None:
    orchestrator, store = _orchestrator()
    judge = _judge()
    platform = _platform()
    platform.record_ruling = AsyncMock(return_value=_synthetic_later_projection_ack())

    ruled = await orchestrator.finish_ruling(
        _DISPUTE_ID,
        _claim(),
        [judge],
        _task(3),
        platform,
    )

    assert ruled["status"] == "ruled"
    platform.record_ruling.assert_awaited_once()
    platform.submit_platform_feedback.assert_awaited()
    store.complete_ruling.assert_called_once_with(_DISPUTE_ID, _LEASE_ID)
    ack = _synthetic_later_projection_ack()
    assert ack["lifecycle_version"] == 9
    assert ack["ruling_summary"] != ack["operation"]["result"]["summary"]


async def test_platform_agent_signs_exact_native_ruling_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_completed_ack())

    platform = _real_platform()
    await platform._http.aclose()
    platform._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    payload = {
        "action": "record_ruling",
        "operation_id": _OPERATION_ID,
        "expected_lifecycle_version": 3,
        "task_id": _TASK_ID,
        "ruling_id": _DISPUTE_ID,
        "worker_pct": 70,
        "ruling_summary": _SUMMARY,
    }
    try:
        response = await platform.record_ruling(_TASK_ID, payload)
    finally:
        await platform.close()

    assert response == _completed_ack()
    assert len(requests) == 1
    assert requests[0].url == ("https://board/tasks/t-00000000-0000-0000-0000-000000000001/ruling")
    token = parse_jws(json.loads(requests[0].content)["token"])
    assert token.header.kid == "a-platform"
    assert token.header.aud == "task-board"
    assert token.header.resource == f"POST /tasks/{_TASK_ID}/ruling"
    assert token.payload == payload


@pytest.mark.parametrize(
    "case",
    [
        "nonmapping_ack",
        "missing_operation",
        "pending",
        "wrong_protocol",
        "foreign_current_task",
        "foreign_operation",
        "foreign_task",
        "wrong_command",
        "wrong_source_status",
        "wrong_source_version",
        "wrong_target_status",
        "wrong_target_version",
        "wrong_effect_kind",
        "wrong_effect_state",
        "wrong_effect_operation",
        "wrong_receipt_reference",
        "wrong_event",
        "nonpositive_event",
        "bool_event",
        "missing_result",
        "null_result",
        "nonmapping_result",
        "wrong_result_task",
        "wrong_result_command",
        "wrong_result_status",
        "wrong_result_version",
        "wrong_result_effect_reference",
        "wrong_result_reward",
        "wrong_ruling_id",
        "wrong_worker_pct",
        "wrong_summary",
    ],
)
async def test_invalid_task_board_ack_stops_before_feedback_and_local_persist(case: str) -> None:
    orchestrator, store = _orchestrator()
    judge = _judge()
    platform = _platform()
    platform.record_ruling = AsyncMock(return_value=_ack_case(case))

    with pytest.raises(ServiceError) as raised:
        await orchestrator.finish_ruling(
            _DISPUTE_ID,
            _claim(),
            [judge],
            _task(3),
            platform,
        )

    assert raised.value.status_code == 502
    assert raised.value.error == "invalid_task_board_ack"
    judge.evaluate.assert_awaited_once()
    platform.record_ruling.assert_awaited_once()
    store.claim_decision.assert_called_once()
    platform.submit_platform_feedback.assert_not_awaited()
    store.complete_ruling.assert_not_called()
    store.release_judging_lease.assert_called_once_with(_DISPUTE_ID, _LEASE_ID)


async def test_same_snapshot_retry_preserves_native_ruling_identity() -> None:
    orchestrator, _store = _orchestrator()
    judge = _judge()
    platform = _platform()
    snapshot = _task(3)

    for _ in range(2):
        await orchestrator.finish_ruling(
            _DISPUTE_ID,
            _claim(),
            [judge],
            snapshot,
            platform,
        )

    assert platform.record_ruling.await_count == 2
    payloads = [call.args[1] for call in platform.record_ruling.await_args_list]
    assert payloads[0] == payloads[1]
    assert payloads[0] == {
        "action": "record_ruling",
        "operation_id": _OPERATION_ID,
        "expected_lifecycle_version": 3,
        "task_id": _TASK_ID,
        "ruling_id": _DISPUTE_ID,
        "worker_pct": 70,
        "ruling_summary": payloads[0]["ruling_summary"],
    }
    platform.get_task.assert_not_awaited()


@pytest.mark.parametrize(
    "version",
    [pytest.param(None, id="missing"), True, -1, 1.5, "3", _SQLITE_MAX_INTEGER + 1],
)
async def test_invalid_task_snapshot_version_stops_before_external_calls(version: object) -> None:
    orchestrator, store = _orchestrator()
    judge = _judge()
    platform = _platform()
    task = _task(version)
    if version is None:
        task.pop("lifecycle_version")

    with pytest.raises(ServiceError) as raised:
        await orchestrator.finish_ruling(
            _DISPUTE_ID,
            _claim(),
            [judge],
            task,
            platform,
        )

    assert raised.value.status_code == 502
    assert raised.value.error == "invalid_task_snapshot"
    judge.evaluate.assert_not_awaited()
    platform.record_ruling.assert_not_awaited()
    platform.submit_platform_feedback.assert_not_awaited()
    platform.get_task.assert_not_awaited()
    store.complete_ruling.assert_not_called()
    store.claim_decision.assert_not_called()
    store.release_judging_lease.assert_called_once_with(_DISPUTE_ID, _LEASE_ID)


async def test_stale_version_conflict_retries_same_attempt_without_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator, store = _orchestrator()
    judge = _judge()
    requests: list[httpx.Request] = []
    error_body = {
        "error": "task_version_conflict",
        "message": "Task lifecycle version differs from the ruling attempt",
        "details": {
            "expected_lifecycle_version": 3,
            "actual_lifecycle_version": 4,
            "current_status": "disputed",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(409, json=error_body)

    platform = _real_platform()
    await platform._http.aclose()
    platform._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    feedback = AsyncMock(return_value={"status": "recorded"})
    monkeypatch.setattr(platform, "submit_platform_feedback", feedback)
    snapshot = _task(3)

    try:
        for _ in range(2):
            with pytest.raises(ServiceError) as raised:
                await orchestrator.finish_ruling(
                    _DISPUTE_ID,
                    _claim(),
                    [judge],
                    snapshot,
                    platform,
                )
            assert raised.value.error == "task_version_conflict"
            assert raised.value.status_code == 409
            assert raised.value.details == error_body["details"]
    finally:
        await platform.close()

    assert len(requests) == 2
    payloads = [parse_jws(json.loads(request.content)["token"]).payload for request in requests]
    assert payloads[0] == payloads[1]
    assert [payload["operation_id"] for payload in payloads] == [_OPERATION_ID, _OPERATION_ID]
    assert [payload["expected_lifecycle_version"] for payload in payloads] == [3, 3]
    assert [payload["ruling_id"] for payload in payloads] == [_DISPUTE_ID, _DISPUTE_ID]
    feedback.assert_not_awaited()
    store.complete_ruling.assert_not_called()
    assert store.release_judging_lease.call_count == 2


@pytest.mark.parametrize(
    "error_body",
    [
        pytest.param(["not", "an", "error", "envelope"], id="malformed"),
        pytest.param(
            {
                "error": "task_not_open",
                "message": "Task is not open",
                "details": {"current_status": "ruled"},
            },
            id="unrelated-typed-error",
        ),
        pytest.param(
            {
                "error": "task_version_conflict",
                "details": {
                    "expected_lifecycle_version": 3,
                    "actual_lifecycle_version": 4,
                    "current_status": "disputed",
                },
            },
            id="recognized-missing-message",
        ),
        pytest.param(
            {
                "error": "task_version_conflict",
                "message": "Task lifecycle version differs from the ruling attempt",
                "details": "not-a-mapping",
            },
            id="recognized-nonmapping-details",
        ),
        pytest.param(
            {
                "error": "task_version_conflict",
                "message": "Task lifecycle version differs from the ruling attempt",
                "details": {
                    "expected_lifecycle_version": 3,
                    "current_status": "disputed",
                },
            },
            id="recognized-missing-actual-version",
        ),
        pytest.param(
            {
                "error": "task_version_conflict",
                "message": "Task lifecycle version differs from the ruling attempt",
                "details": {
                    "expected_lifecycle_version": 4,
                    "actual_lifecycle_version": 4,
                    "current_status": "disputed",
                },
            },
            id="recognized-wrong-expected-version",
        ),
        pytest.param(
            {
                "error": "task_version_conflict",
                "message": "Task lifecycle version differs from the ruling attempt",
                "details": {
                    "expected_lifecycle_version": 3,
                    "actual_lifecycle_version": True,
                    "current_status": "disputed",
                },
            },
            id="recognized-bool-actual-version",
        ),
    ],
)
async def test_unrecognized_or_malformed_http_409_remains_dependency_error(
    error_body: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator, store = _orchestrator()
    judge = _judge()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(409, json=error_body)

    platform = _real_platform()
    await platform._http.aclose()
    platform._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    feedback = AsyncMock(return_value={"status": "recorded"})
    monkeypatch.setattr(platform, "submit_platform_feedback", feedback)

    try:
        with pytest.raises(ServiceError) as raised:
            await orchestrator.finish_ruling(
                _DISPUTE_ID,
                _claim(),
                [judge],
                _task(3),
                platform,
            )
    finally:
        await platform.close()

    assert raised.value.error == "task_board_unavailable"
    assert raised.value.status_code == 502
    assert raised.value.details == {}
    assert len(requests) == 1
    feedback.assert_not_awaited()
    store.complete_ruling.assert_not_called()
    store.release_judging_lease.assert_called_once_with(_DISPUTE_ID, _LEASE_ID)
