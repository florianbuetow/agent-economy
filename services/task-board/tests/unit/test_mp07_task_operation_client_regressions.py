"""Semantic response-binding regressions for the MP-07 TaskDbClient."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx
import pytest
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair
from service_commons.exceptions import ServiceError

from task_board_service.services.task_db_client import TaskDbClient
from task_board_service.task_operation_contract import canonical_hash

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit

_CLAIM_ID = "clm-11111111-1111-4111-8111-111111111111"
_OTHER_AGENT = "a-other"
_BASE_URL = "http://gateway.test"
_TASK_ID = "t-11111111-1111-4111-8111-111111111111"
_POSTER_ID = "a-poster"
_WORKER_ID = "a-worker"
_ESCROW_ID = "esc-11111111-1111-4111-8111-111111111111"
_BID_ID = "bid-11111111-1111-4111-8111-111111111111"
_OPERATION_ID = "op-11111111-1111-4111-8111-111111111111"


class _NativeClient(Protocol):
    def claim_task_operation(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def finalize_task_operation(self, operation_id: str) -> dict[str, Any]: ...


class _MockClient(TaskDbClient):
    def __init__(
        self,
        signer: GatewayRequestSigner,
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> None:
        super().__init__(_BASE_URL, 5, signer)
        self._client.close()
        self._client = httpx.Client(
            base_url=_BASE_URL,
            transport=httpx.MockTransport(handler),
        )


@dataclass(frozen=True)
class _ClientGraph:
    client: TaskDbClient
    requests: list[httpx.Request]


@pytest.fixture
def signer(tmp_path: Path) -> GatewayRequestSigner:
    generate_keypair("gateway-task-board-regressions", tmp_path)
    return GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-task-board-regressions",
            private_key_path=Path("gateway-task-board-regressions.key"),
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )


def _recording_client(
    signer: GatewayRequestSigner,
    responses: list[httpx.Response],
) -> _ClientGraph:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert responses
        return responses.pop(0)

    return _ClientGraph(_MockClient(signer, handler), requests)


def _service_error(call: Callable[[], object]) -> dict[str, object]:
    try:
        call()
    except ServiceError as exc:
        return {
            "status": exc.status_code,
            "error": exc.error,
            "message": exc.message,
            "details": exc.details,
        }
    return {"kind": "success"}


def _canonical_request(payload: dict[str, Any]) -> tuple[str, str]:
    semantic = {key: value for key, value in payload.items() if key != "operation_id"}
    canonical = json.dumps(
        semantic,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return canonical, hashlib.sha256(canonical.encode()).hexdigest()


def _claim_payload(command: str = "approve_task") -> dict[str, Any]:
    if command == "accept_bid":
        return {
            "protocol_version": 1,
            "operation_id": _OPERATION_ID,
            "task_id": _TASK_ID,
            "command": command,
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": _POSTER_ID,
            "arguments": {
                "bid_id": _BID_ID,
                "worker_id": _WORKER_ID,
                "escrow_id": _ESCROW_ID,
                "reward": 100,
                "bid_amount": 90,
            },
        }
    if command == "dispute_task":
        return {
            "protocol_version": 1,
            "operation_id": _OPERATION_ID,
            "task_id": _TASK_ID,
            "command": command,
            "expected_status": "submitted",
            "expected_lifecycle_version": 2,
            "actor_kind": "agent",
            "actor_id": _POSTER_ID,
            "arguments": {
                "poster_id": _POSTER_ID,
                "worker_id": _WORKER_ID,
                "escrow_id": _ESCROW_ID,
                "reward": 100,
                "reason": "The submitted work is incomplete.",
            },
        }
    return {
        "protocol_version": 1,
        "operation_id": _OPERATION_ID,
        "task_id": _TASK_ID,
        "command": command,
        "expected_status": "submitted",
        "expected_lifecycle_version": 2,
        "actor_kind": "agent",
        "actor_id": _POSTER_ID,
        "arguments": {
            "poster_id": _POSTER_ID,
            "worker_id": _WORKER_ID,
            "escrow_id": _ESCROW_ID,
            "reward": 100,
            "source_reference": f"task:{_TASK_ID}:approved",
        },
    }


def _pending_bank_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    _canonical, request_hash = _canonical_request(payload)
    financial = {
        "escrow_id": _ESCROW_ID,
        "operation_kind": "escrow_release",
        "recipient_account_id": _WORKER_ID,
        "request_version": 1,
        "source_reference": f"task:{_TASK_ID}:approved",
    }
    return {
        "protocol_version": 1,
        "operation_id": _OPERATION_ID,
        "task_id": _TASK_ID,
        "command": "approve_task",
        "request_sha256": request_hash,
        "operation_state": "claimed",
        "source_status": "submitted",
        "source_lifecycle_version": 2,
        "target_status": "approved",
        "target_lifecycle_version": 3,
        "effect_kind": "bank_release",
        "effect_state": "pending",
        "effect_operation_id": _OPERATION_ID,
        "effect_result_reference": None,
        "effect_intent": {
            "financial_request": financial,
            "financial_request_sha256": canonical_hash(financial),
            "expected_escrow": {
                "escrow_id": _ESCROW_ID,
                "task_id": _TASK_ID,
                "payer_account_id": _POSTER_ID,
                "amount": 100,
            },
        },
        "event_id": None,
        "result": None,
    }


def _pending_court_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    _canonical, request_hash = _canonical_request(payload)
    return {
        "protocol_version": 1,
        "operation_id": _OPERATION_ID,
        "task_id": _TASK_ID,
        "command": "dispute_task",
        "request_sha256": request_hash,
        "operation_state": "claimed",
        "source_status": "submitted",
        "source_lifecycle_version": 2,
        "target_status": "disputed",
        "target_lifecycle_version": 3,
        "effect_kind": "court_claim",
        "effect_state": "pending",
        "effect_operation_id": None,
        "effect_result_reference": None,
        "effect_intent": {
            "court_claim": {
                "task_id": _TASK_ID,
                "claimant_id": _POSTER_ID,
                "respondent_id": _WORKER_ID,
                "reason": "The submitted work is incomplete.",
            },
            "expected_escrow": {
                "escrow_id": _ESCROW_ID,
                "task_id": _TASK_ID,
                "payer_account_id": _POSTER_ID,
                "amount": 100,
            },
        },
        "event_id": None,
        "result": None,
    }


def _completed_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    _canonical, request_hash = _canonical_request(payload)
    return {
        "protocol_version": 1,
        "operation_id": _OPERATION_ID,
        "task_id": _TASK_ID,
        "command": "accept_bid",
        "request_sha256": request_hash,
        "operation_state": "completed",
        "source_status": "open",
        "source_lifecycle_version": 0,
        "target_status": "accepted",
        "target_lifecycle_version": 1,
        "effect_kind": "none",
        "effect_state": "none",
        "effect_operation_id": None,
        "effect_result_reference": None,
        "effect_intent": None,
        "event_id": 42,
        "result": {
            "task_id": _TASK_ID,
            "command": "accept_bid",
            "status": "accepted",
            "lifecycle_version": 1,
            "event_id": 42,
            "bid_id": _BID_ID,
            "worker_id": _WORKER_ID,
            "reward": 100,
            "bid_amount": 90,
        },
    }


def _ruling_payload() -> dict[str, Any]:
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
            "poster_id": _POSTER_ID,
            "worker_id": _WORKER_ID,
            "escrow_id": _ESCROW_ID,
            "reward": 100,
            "ruling_id": _CLAIM_ID,
            "worker_pct": 50,
            "summary": "The Court split the frozen reward equally.",
            "source_reference": f"task:{_TASK_ID}:ruling:{_CLAIM_ID}",
        },
    }


def _pending_ruling_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    _canonical, request_hash = _canonical_request(payload)
    financial = {
        "escrow_id": _ESCROW_ID,
        "operation_kind": "escrow_split",
        "poster_account_id": _POSTER_ID,
        "request_version": 1,
        "source_reference": f"task:{_TASK_ID}:ruling:{_CLAIM_ID}",
        "worker_account_id": _WORKER_ID,
        "worker_pct": 50,
    }
    return {
        "protocol_version": 1,
        "operation_id": _OPERATION_ID,
        "task_id": _TASK_ID,
        "command": "record_ruling",
        "request_sha256": request_hash,
        "operation_state": "claimed",
        "source_status": "disputed",
        "source_lifecycle_version": 3,
        "target_status": "ruled",
        "target_lifecycle_version": 4,
        "effect_kind": "bank_split",
        "effect_state": "pending",
        "effect_operation_id": _OPERATION_ID,
        "effect_result_reference": None,
        "effect_intent": {
            "financial_request": financial,
            "financial_request_sha256": canonical_hash(financial),
            "expected_escrow": {
                "escrow_id": _ESCROW_ID,
                "task_id": _TASK_ID,
                "payer_account_id": _POSTER_ID,
                "amount": 100,
            },
        },
        "event_id": None,
        "result": None,
    }


def _completed_external(
    pending: dict[str, Any],
    result_fields: dict[str, object],
    *,
    effect_reference: str,
) -> dict[str, Any]:
    envelope = copy.deepcopy(pending)
    envelope["operation_state"] = "completed"
    envelope["effect_state"] = "succeeded"
    envelope["effect_result_reference"] = effect_reference
    envelope["event_id"] = 42
    envelope["result"] = {
        "task_id": envelope["task_id"],
        "command": envelope["command"],
        "status": envelope["target_status"],
        "lifecycle_version": envelope["target_lifecycle_version"],
        "event_id": 42,
        "effect_result_reference": effect_reference,
        **result_fields,
    }
    return envelope


def _completed_approve() -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _claim_payload()
    envelope = _completed_external(
        _pending_bank_envelope(payload),
        {"worker_id": _WORKER_ID, "reward": 100},
        effect_reference=_OPERATION_ID,
    )
    return payload, envelope


def _completed_dispute() -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _claim_payload(command="dispute_task")
    envelope = _completed_external(
        _pending_court_envelope(payload),
        {
            "dispute_id": _CLAIM_ID,
            "reason": "The submitted work is incomplete.",
            "worker_id": _WORKER_ID,
        },
        effect_reference=_CLAIM_ID,
    )
    return payload, envelope


def _completed_ruling() -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _ruling_payload()
    envelope = _completed_external(
        _pending_ruling_envelope(payload),
        {
            "worker_id": _WORKER_ID,
            "ruling_id": _CLAIM_ID,
            "worker_pct": 50,
            "summary": "The Court split the frozen reward equally.",
            "reward": 100,
        },
        effect_reference=_OPERATION_ID,
    )
    return payload, envelope


def _claim_outcome(
    signer: GatewayRequestSigner,
    payload: dict[str, Any],
    envelope: dict[str, Any],
    *,
    status_code: int,
) -> dict[str, object]:
    graph = _recording_client(signer, [httpx.Response(status_code, json=envelope)])
    try:
        client = cast("_NativeClient", graph.client)
        return _service_error(lambda: client.claim_task_operation(payload))
    finally:
        graph.client.close()


def _finalize_outcome(
    signer: GatewayRequestSigner,
    envelope: dict[str, Any],
) -> dict[str, object]:
    graph = _recording_client(signer, [httpx.Response(200, json=envelope)])
    try:
        client = cast("_NativeClient", graph.client)
        return _service_error(lambda: client.finalize_task_operation(_OPERATION_ID))
    finally:
        graph.client.close()


def _rehash_financial_intent(envelope: dict[str, Any]) -> None:
    intent = envelope["effect_intent"]
    assert isinstance(intent, dict)
    financial = intent["financial_request"]
    assert isinstance(financial, dict)
    intent["financial_request_sha256"] = canonical_hash(financial)


def _error_code(outcome: dict[str, object]) -> tuple[object, object]:
    return outcome.get("status"), outcome.get("error")


def test_valid_producer_intents_and_command_results_are_accepted(
    signer: GatewayRequestSigner,
) -> None:
    accept_payload = _claim_payload(command="accept_bid")
    positive = [
        (accept_payload, _completed_envelope(accept_payload), 200),
        (_claim_payload(), _pending_bank_envelope(_claim_payload()), 202),
        (
            _claim_payload(command="dispute_task"),
            _pending_court_envelope(_claim_payload(command="dispute_task")),
            202,
        ),
        (_ruling_payload(), _pending_ruling_envelope(_ruling_payload()), 202),
        (*_completed_approve(), 200),
        (*_completed_dispute(), 200),
        (*_completed_ruling(), 200),
    ]

    outcomes = [
        _claim_outcome(signer, payload, envelope, status_code=status)
        for payload, envelope, status in positive
    ]

    assert outcomes == [{"kind": "success"}] * len(positive)


def _set_path(envelope: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    current: dict[str, Any] = envelope
    for key in path[:-1]:
        nested = current[key]
        assert isinstance(nested, dict)
        current = nested
    current[path[-1]] = value


def _intent_binding_cases() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    approve_payload = _claim_payload()
    approve_mutations: tuple[tuple[str, tuple[tuple[tuple[str, ...], object], ...]], ...] = (
        (
            "approve-recipient",
            ((("effect_intent", "financial_request", "recipient_account_id"), _POSTER_ID),),
        ),
        (
            "approve-escrow",
            (
                (("effect_intent", "financial_request", "escrow_id"), "esc-wrong"),
                (("effect_intent", "expected_escrow", "escrow_id"), "esc-wrong"),
            ),
        ),
        (
            "approve-payer",
            ((("effect_intent", "expected_escrow", "payer_account_id"), _OTHER_AGENT),),
        ),
        (
            "approve-amount",
            ((("effect_intent", "expected_escrow", "amount"), 99),),
        ),
        (
            "approve-source",
            (
                (
                    ("effect_intent", "financial_request", "source_reference"),
                    f"task:{_TASK_ID}:cancelled",
                ),
            ),
        ),
    )
    for name, mutations in approve_mutations:
        envelope = copy.deepcopy(_pending_bank_envelope(approve_payload))
        for path, value in mutations:
            _set_path(envelope, path, value)
        _rehash_financial_intent(envelope)
        cases.append((name, approve_payload, envelope))

    dispute_payload = _claim_payload(command="dispute_task")
    dispute_mutations: tuple[tuple[str, tuple[tuple[tuple[str, ...], object], ...]], ...] = (
        (
            "dispute-claimant",
            (
                (("effect_intent", "court_claim", "claimant_id"), _OTHER_AGENT),
                (("effect_intent", "expected_escrow", "payer_account_id"), _OTHER_AGENT),
            ),
        ),
        (
            "dispute-respondent",
            ((("effect_intent", "court_claim", "respondent_id"), _OTHER_AGENT),),
        ),
        (
            "dispute-reason",
            ((("effect_intent", "court_claim", "reason"), "Different claim reason"),),
        ),
        (
            "dispute-escrow",
            ((("effect_intent", "expected_escrow", "escrow_id"), "esc-wrong"),),
        ),
        (
            "dispute-amount",
            ((("effect_intent", "expected_escrow", "amount"), 99),),
        ),
    )
    for name, mutations in dispute_mutations:
        envelope = copy.deepcopy(_pending_court_envelope(dispute_payload))
        for path, value in mutations:
            _set_path(envelope, path, value)
        cases.append((name, dispute_payload, envelope))

    ruling_payload = _ruling_payload()
    for name, field, value in (
        ("ruling-worker", "worker_account_id", _OTHER_AGENT),
        ("ruling-poster", "poster_account_id", _OTHER_AGENT),
        ("ruling-percentage", "worker_pct", 49),
        ("ruling-source", "source_reference", f"task:{_TASK_ID}:ruling:wrong"),
    ):
        envelope = copy.deepcopy(_pending_ruling_envelope(ruling_payload))
        envelope["effect_intent"]["financial_request"][field] = value
        _rehash_financial_intent(envelope)
        cases.append((name, ruling_payload, envelope))
    for name, field, value in (
        ("ruling-escrow", "escrow_id", "esc-wrong"),
        ("ruling-amount", "amount", 99),
    ):
        envelope = copy.deepcopy(_pending_ruling_envelope(ruling_payload))
        envelope["effect_intent"]["expected_escrow"][field] = value
        if field == "escrow_id":
            envelope["effect_intent"]["financial_request"][field] = value
            _rehash_financial_intent(envelope)
        cases.append((name, ruling_payload, envelope))
    return cases


def test_claim_rejects_self_consistent_effect_intent_not_bound_to_request(
    signer: GatewayRequestSigner,
) -> None:
    cases = _intent_binding_cases()

    observed = [
        (name, _error_code(_claim_outcome(signer, payload, envelope, status_code=202)))
        for name, payload, envelope in cases
    ]

    assert observed == [(name, (502, "invalid_gateway_response")) for name, _p, _e in cases]


def _command_result_cases() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    cases: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    accept_payload = _claim_payload(command="accept_bid")
    for field, value in (
        ("bid_id", "bid-wrong"),
        ("worker_id", _OTHER_AGENT),
        ("reward", 99),
        ("bid_amount", 89),
    ):
        envelope = copy.deepcopy(_completed_envelope(accept_payload))
        envelope["result"][field] = value
        cases.append((f"accept-{field}", accept_payload, envelope))

    for command, completed, mutations in (
        (
            "approve",
            _completed_approve(),
            (("worker_id", _OTHER_AGENT), ("reward", 99)),
        ),
        (
            "dispute",
            _completed_dispute(),
            (
                ("dispute_id", "clm-wrong"),
                ("reason", "Different result reason"),
                ("worker_id", _OTHER_AGENT),
            ),
        ),
        (
            "ruling",
            _completed_ruling(),
            (
                ("worker_id", _OTHER_AGENT),
                ("ruling_id", "clm-wrong"),
                ("worker_pct", 49),
                ("summary", "Different result summary"),
                ("reward", 99),
            ),
        ),
    ):
        payload, valid = completed
        for field, value in mutations:
            envelope = copy.deepcopy(valid)
            envelope["result"][field] = value
            cases.append((f"{command}-{field}", payload, envelope))
    return cases


def test_claim_rejects_command_specific_result_mismatch(
    signer: GatewayRequestSigner,
) -> None:
    cases = _command_result_cases()

    observed = [
        (name, _error_code(_claim_outcome(signer, payload, envelope, status_code=200)))
        for name, payload, envelope in cases
    ]

    assert observed == [(name, (502, "invalid_gateway_response")) for name, _p, _e in cases]


def _finalize_semantic_cases() -> list[tuple[str, dict[str, Any]]]:
    cases: list[tuple[str, dict[str, Any]]] = []
    for command, completed, mutations in (
        (
            "approve",
            _completed_approve()[1],
            (("worker_id", _OTHER_AGENT), ("reward", 99)),
        ),
        (
            "dispute",
            _completed_dispute()[1],
            (
                ("dispute_id", "clm-wrong"),
                ("reason", "Different result reason"),
                ("worker_id", _OTHER_AGENT),
            ),
        ),
        (
            "ruling",
            _completed_ruling()[1],
            (
                ("worker_id", _OTHER_AGENT),
                ("ruling_id", "clm-wrong"),
                ("worker_pct", 49),
                ("reward", 99),
            ),
        ),
    ):
        for field, value in mutations:
            envelope = copy.deepcopy(completed)
            envelope["result"][field] = value
            cases.append((f"{command}-{field}", envelope))
    return cases


def test_finalize_rejects_internally_inconsistent_effect_and_result(
    signer: GatewayRequestSigner,
) -> None:
    cases = _finalize_semantic_cases()

    observed = [
        (name, _error_code(_finalize_outcome(signer, envelope))) for name, envelope in cases
    ]

    assert observed == [(name, (502, "invalid_gateway_response")) for name, _e in cases]


def _dual_recovery_owner_projection() -> dict[str, Any]:
    return {
        "task_id": _TASK_ID,
        "poster_id": _POSTER_ID,
        "title": "Ambiguous recovery ownership",
        "spec": "Only one recovery owner may be active.",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "deadline_seconds": 120,
        "review_deadline_seconds": 180,
        "status": "submitted",
        "escrow_id": _ESCROW_ID,
        "bid_count": 1,
        "worker_id": _WORKER_ID,
        "accepted_bid_id": _BID_ID,
        "created_at": "2099-01-01T00:00:00Z",
        "accepted_at": "2099-01-01T00:00:01Z",
        "submitted_at": "2099-01-01T00:00:02Z",
        "approved_at": None,
        "cancelled_at": None,
        "disputed_at": None,
        "dispute_reason": None,
        "dispute_id": None,
        "rebuttal_deadline": None,
        "rebuttal_submitted_at": None,
        "ruling_id": None,
        "ruled_at": None,
        "worker_pct": None,
        "ruling_summary": None,
        "expired_at": None,
        "escrow_pending": 0,
        "lifecycle_version": 2,
        "timing_contract_version": 2,
        "acceptance_window_seconds": 30,
        "bidding_cutoff_us": 4_070_908_860_000_000,
        "acceptance_cutoff_us": 4_070_908_890_000_000,
        "execution_cutoff_us": 4_070_908_921_000_000,
        "review_cutoff_us": 4_070_908_982_000_000,
        "accepted_reward": 100,
        "accepted_bid_amount": 90,
        "worker_payment": None,
        "poster_refund": None,
        "pending_transition": {
            "operation_id": _OPERATION_ID,
            "command": "approve_task",
            "target_status": "approved",
            "target_lifecycle_version": 3,
            "operation_state": "claimed",
            "effect_state": "pending",
        },
        "legacy_reconciliations": [
            {
                "observation_id": "obs-11111111-1111-4111-8111-111111111111",
                "observation_kind": "worker_settlement_pending",
                "effect_state": "pending",
                "target_outcome_hint": "approved",
            }
        ],
    }


def test_task_reads_reject_simultaneous_native_and_legacy_recovery_owners(
    signer: GatewayRequestSigner,
) -> None:
    task = _dual_recovery_owner_projection()
    observed: list[tuple[str, tuple[object, object]]] = []
    for endpoint, body in (
        ("get", task),
        ("list", {"tasks": [task]}),
    ):
        graph = _recording_client(signer, [httpx.Response(200, json=body)])
        try:
            if endpoint == "get":
                outcome = _service_error(lambda client=graph.client: client.get_task(_TASK_ID))
            else:
                outcome = _service_error(
                    lambda client=graph.client: client.list_tasks(None, None, None, None, None)
                )
        finally:
            graph.client.close()
        observed.append((endpoint, _error_code(outcome)))

    assert observed == [
        ("get", (502, "invalid_gateway_response")),
        ("list", (502, "invalid_gateway_response")),
    ]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(409, content=b"{"),
        httpx.Response(409, json=[]),
        httpx.Response(409, json={"error": "transition_pending"}),
        httpx.Response(
            409,
            json={
                "error": "transition_pending",
                "message": "Another operation owns this task",
                "details": "not-an-object",
            },
        ),
    ],
)
def test_malformed_typed_gateway_error_fails_closed(
    signer: GatewayRequestSigner,
    response: httpx.Response,
) -> None:
    graph = _recording_client(signer, [response])
    try:
        client = cast("_NativeClient", graph.client)
        outcome = _service_error(lambda: client.claim_task_operation(_claim_payload()))
    finally:
        graph.client.close()

    assert _error_code(outcome) == (502, "invalid_gateway_response")
