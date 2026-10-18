"""MP-07 TaskDbClient transport and strict response-contract proofs."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

import httpx
import pytest
from service_auth.gateway import (
    GatewayRequestSigner,
    GatewaySignerConfig,
    verify_gateway_request,
)
from service_auth.jws import TokenPolicy
from service_auth.signing import generate_keypair, load_public_key
from service_commons.exceptions import ServiceError

from task_board_service.services.task_db_client import TaskDbClient

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

pytestmark = pytest.mark.unit

_BASE_URL = "http://gateway.test"
_TASK_ID = "t-11111111-1111-4111-8111-111111111111"
_POSTER_ID = "a-poster"
_WORKER_ID = "a-worker"
_ESCROW_ID = "esc-11111111-1111-4111-8111-111111111111"
_BID_ID = "bid-11111111-1111-4111-8111-111111111111"
_OPERATION_ID = "op-11111111-1111-4111-8111-111111111111"

_ENVELOPE_FIELDS = frozenset(
    {
        "protocol_version",
        "operation_id",
        "task_id",
        "command",
        "request_sha256",
        "operation_state",
        "source_status",
        "source_lifecycle_version",
        "target_status",
        "target_lifecycle_version",
        "effect_kind",
        "effect_state",
        "effect_operation_id",
        "effect_result_reference",
        "effect_intent",
        "event_id",
        "result",
    }
)

_V3_TASK_FIELDS = (
    "lifecycle_version",
    "timing_contract_version",
    "acceptance_window_seconds",
    "bidding_cutoff_us",
    "acceptance_cutoff_us",
    "execution_cutoff_us",
    "review_cutoff_us",
    "accepted_reward",
    "accepted_bid_amount",
    "pending_transition",
    "legacy_reconciliations",
)


@dataclass(frozen=True)
class _ClientGraph:
    client: TaskDbClient
    requests: list[httpx.Request]


class _NativeOperationClient(Protocol):
    def claim_task_operation(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def finalize_task_operation(self, operation_id: str) -> dict[str, Any]: ...


class _MockTransportTaskDbClient(TaskDbClient):
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


@pytest.fixture
def signer(tmp_path: Path) -> GatewayRequestSigner:
    generate_keypair("gateway-task-board", tmp_path)
    return GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-task-board",
            private_key_path=Path("gateway-task-board.key"),
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )


def _client(
    signer: GatewayRequestSigner,
    handler: Callable[[httpx.Request], httpx.Response],
) -> TaskDbClient:
    return _MockTransportTaskDbClient(signer, handler)


def _recording_client(
    signer: GatewayRequestSigner,
    responses: list[httpx.Response],
) -> _ClientGraph:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert responses, "Mock transport received an unexpected request"
        return responses.pop(0)

    return _ClientGraph(_client(signer, handler), requests)


def _claim_payload(
    *,
    command: str = "approve_task",
    operation_id: str = _OPERATION_ID,
) -> dict[str, Any]:
    if command == "accept_bid":
        return {
            "protocol_version": 1,
            "operation_id": operation_id,
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
            "operation_id": operation_id,
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
        "operation_id": operation_id,
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


def _canonical_request(payload: Mapping[str, object]) -> tuple[str, str]:
    semantic = {str(key): value for key, value in payload.items() if key != "operation_id"}
    canonical = json.dumps(
        semantic,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return canonical, hashlib.sha256(canonical.encode()).hexdigest()


def _pending_bank_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    _canonical, request_hash = _canonical_request(payload)
    operation_id = str(payload["operation_id"])
    financial_request = {
        "escrow_id": _ESCROW_ID,
        "operation_kind": "escrow_release",
        "recipient_account_id": _WORKER_ID,
        "request_version": 1,
        "source_reference": f"task:{_TASK_ID}:approved",
    }
    financial_json = json.dumps(financial_request, sort_keys=True, separators=(",", ":"))
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
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
        "effect_operation_id": operation_id,
        "effect_result_reference": None,
        "effect_intent": {
            "financial_request": financial_request,
            "financial_request_sha256": hashlib.sha256(financial_json.encode()).hexdigest(),
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
        "operation_id": payload["operation_id"],
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


def _pending_court_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    _canonical, request_hash = _canonical_request(payload)
    return {
        "protocol_version": 1,
        "operation_id": payload["operation_id"],
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


def _assert_signed_request(request: httpx.Request, tmp_path: Path) -> None:
    verify_gateway_request(
        request.headers["Authorization"].removeprefix("Bearer "),
        load_public_key(tmp_path / "gateway-task-board.pub"),
        policy=TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0),
        audience="db-gateway",
        method="POST",
        path=request.url.path,
        body=request.content,
        query=request.url.query,
        allowed_actions="gateway.board.write",
    )


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


def _assert_native_transport_contract(
    signer: GatewayRequestSigner,
    tmp_path: Path,
) -> None:
    accept_payload = _claim_payload(command="accept_bid")
    approve_payload = _claim_payload()
    dispute_payload = _claim_payload(command="dispute_task")
    responses = [
        httpx.Response(200, json=_completed_envelope(accept_payload)),
        httpx.Response(202, json=_pending_bank_envelope(approve_payload)),
        httpx.Response(202, json=_pending_court_envelope(dispute_payload)),
        httpx.Response(202, json=_pending_bank_envelope(approve_payload)),
    ]
    graph = _recording_client(signer, responses)
    try:
        native = cast("_NativeOperationClient", graph.client)
        assert native.claim_task_operation(accept_payload) == _completed_envelope(accept_payload)
        assert native.claim_task_operation(approve_payload) == _pending_bank_envelope(
            approve_payload
        )
        assert native.claim_task_operation(dispute_payload) == _pending_court_envelope(
            dispute_payload
        )
        assert native.finalize_task_operation(_OPERATION_ID) == _pending_bank_envelope(
            approve_payload
        )
    finally:
        graph.client.close()
    assert len(graph.requests) == 4
    for request in graph.requests:
        _assert_signed_request(request, tmp_path)
    assert graph.requests[0].url.path == "/board/task-operations"
    assert json.loads(graph.requests[0].content) == accept_payload
    assert graph.requests[1].url.path == "/board/task-operations"
    assert json.loads(graph.requests[1].content) == approve_payload
    assert graph.requests[2].url.path == "/board/task-operations"
    assert json.loads(graph.requests[2].content) == dispute_payload
    assert graph.requests[3].url.path == f"/board/task-operations/{_OPERATION_ID}/finalize"
    assert graph.requests[3].content == b"{}"


def _assert_input_validation(
    signer: GatewayRequestSigner,
) -> None:
    requests: list[httpx.Request] = []
    graph = _client(
        signer,
        lambda request: requests.append(request) or httpx.Response(500),
    )
    malformed: list[dict[str, Any]] = []
    for field, value in (
        ("protocol_version", True),
        ("protocol_version", "1"),
        ("expected_lifecycle_version", True),
        ("expected_lifecycle_version", "2"),
    ):
        payload = copy.deepcopy(_claim_payload())
        payload[field] = value
        malformed.append(payload)
    for value in (True, "100"):
        payload = copy.deepcopy(_claim_payload())
        payload["arguments"]["reward"] = value
        malformed.append(payload)
    unknown = copy.deepcopy(_claim_payload())
    unknown["unknown"] = "field"
    malformed.append(unknown)
    missing = copy.deepcopy(_claim_payload())
    del missing["actor_id"]
    malformed.append(missing)
    try:
        native = cast("_NativeOperationClient", graph)
        outcomes = [
            _service_error(lambda payload=payload: native.claim_task_operation(payload))
            for payload in malformed
        ]
    finally:
        graph.close()
    assert [
        {"status": outcome.get("status"), "error": outcome.get("error")} for outcome in outcomes
    ] == [{"status": 400, "error": "invalid_payload"}] * len(malformed)
    assert requests == []


def _malformed_envelopes(
    payload: dict[str, Any],
) -> list[tuple[str, httpx.Response, dict[str, Any]]]:
    valid = _pending_bank_envelope(payload)
    cases: list[tuple[str, httpx.Response, dict[str, Any]]] = []

    def changed(name: str, mutate: Callable[[dict[str, Any]], None]) -> None:
        envelope = copy.deepcopy(valid)
        mutate(envelope)
        cases.append((name, httpx.Response(202, json=envelope), payload))

    changed("missing-key", lambda body: body.pop("protocol_version"))
    changed("unknown-key", lambda body: body.__setitem__("unknown", None))
    changed("bool-version", lambda body: body.__setitem__("source_lifecycle_version", True))
    changed("string-version", lambda body: body.__setitem__("target_lifecycle_version", "3"))
    changed("operation-id", lambda body: body.__setitem__("operation_id", f"op-{uuid4()}"))
    changed("task-id", lambda body: body.__setitem__("task_id", "t-other"))
    changed("command", lambda body: body.__setitem__("command", "cancel_task"))
    changed("request-hash", lambda body: body.__setitem__("request_sha256", "0" * 64))
    changed("pending-event", lambda body: body.__setitem__("event_id", 9))
    changed("pending-result", lambda body: body.__setitem__("result", {}))
    changed("bank-effect-id", lambda body: body.__setitem__("effect_operation_id", None))
    dispute_payload = _claim_payload(command="dispute_task")
    court = _pending_court_envelope(dispute_payload)
    court["effect_operation_id"] = str(court["operation_id"])
    cases.append(("court-effect-id", httpx.Response(202, json=court), dispute_payload))
    accept_payload = _claim_payload(command="accept_bid")
    completed = _completed_envelope(accept_payload)
    completed["result"]["lifecycle_version"] = 2
    cases.append(("result-version", httpx.Response(200, json=completed), accept_payload))
    cases.append(("invalid-json", httpx.Response(202, content=b"{"), payload))
    cases.append(("non-object", httpx.Response(202, json=[]), payload))
    return cases


def _assert_response_validation(
    signer: GatewayRequestSigner,
) -> None:
    payload = _claim_payload()
    observed: list[tuple[str, dict[str, object]]] = []
    cases = _malformed_envelopes(payload)
    for name, response, originating_payload in cases:
        graph = _recording_client(signer, [response])
        try:
            native = cast("_NativeOperationClient", graph.client)
            observed.append(
                (
                    name,
                    _service_error(
                        lambda native=native, originating_payload=originating_payload: (
                            native.claim_task_operation(originating_payload)
                        )
                    ),
                )
            )
        finally:
            graph.client.close()
    simplified = [(name, outcome.get("status"), outcome.get("error")) for name, outcome in observed]
    assert simplified == [
        (name, 502, "invalid_gateway_response") for name, _response, _payload in cases
    ]


def _assert_gateway_errors_preserved(signer: GatewayRequestSigner) -> None:
    payload = _claim_payload()
    expected: list[dict[str, object]] = []
    responses: list[httpx.Response] = []
    for status, error, details in (
        (400, "invalid_payload", {"field": "protocol_version"}),
        (403, "forbidden", {}),
        (404, "task_not_found", {}),
        (409, "transition_pending", {"winning_operation_id": f"op-{uuid4()}"}),
        (503, "platform_identity_not_ready", {}),
    ):
        message = f"typed {error}"
        body = {"error": error, "message": message, "details": details}
        responses.append(httpx.Response(status, json=body))
        expected.append({"status": status, **body})
    graph = _recording_client(signer, responses)
    try:
        native = cast("_NativeOperationClient", graph.client)
        observed = [
            _service_error(lambda: native.claim_task_operation(payload)) for _item in expected
        ]
    finally:
        graph.client.close()
    assert observed == expected


def test_native_operation_methods_sign_validate_and_fail_closed(
    signer: GatewayRequestSigner,
    tmp_path: Path,
) -> None:
    probe = TaskDbClient(_BASE_URL, 5, signer)
    try:
        method_names = ("claim_task_operation", "finalize_task_operation")
        methods = {name: getattr(probe, name, None) for name in method_names}
    finally:
        probe.close()
    if not all(callable(method) for method in methods.values()):
        pytest.fail(
            "Missing TaskDbClient native-operation capability: claim_task_operation(payload) "
            "and finalize_task_operation(operation_id) are required; signed transport, strict "
            "envelopes, typed errors, and malformed-response cases remain prepared behind this gate"
        )
    _assert_native_transport_contract(signer, tmp_path)
    _assert_input_validation(signer)
    _assert_response_validation(signer)
    _assert_gateway_errors_preserved(signer)


def _task_projection(*, legacy: bool) -> dict[str, Any]:
    pending_transition: dict[str, object] | None = {
        "operation_id": _OPERATION_ID,
        "command": "approve_task",
        "target_status": "approved",
        "target_lifecycle_version": 3,
        "operation_state": "claimed",
        "effect_state": "pending",
    }
    legacy_reconciliations: list[dict[str, object]] = []
    if legacy:
        pending_transition = None
        legacy_reconciliations = [
            {
                "observation_id": "obs-11111111-1111-4111-8111-111111111111",
                "observation_kind": "worker_settlement_pending",
                "effect_state": "pending",
                "target_outcome_hint": "approved",
            }
        ]
    return {
        "task_id": _TASK_ID,
        "poster_id": _POSTER_ID,
        "title": "Projected task",
        "spec": "Preserve every v3 contract field.",
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
        "bidding_cutoff_us": 4070908860000000,
        "acceptance_cutoff_us": 4070908890000000,
        "execution_cutoff_us": 4070908921000000,
        "review_cutoff_us": 4070908982000000,
        "accepted_reward": 100,
        "accepted_bid_amount": 90,
        "pending_transition": pending_transition,
        "legacy_reconciliations": legacy_reconciliations,
    }


def test_task_reads_preserve_native_and_legacy_v3_projections(
    signer: GatewayRequestSigner,
) -> None:
    native = _task_projection(legacy=False)
    legacy = _task_projection(legacy=True)
    graph = _recording_client(
        signer,
        [
            httpx.Response(200, json=native),
            httpx.Response(200, json={"tasks": [legacy]}),
        ],
    )
    try:
        observed_native = graph.client.get_task(_TASK_ID)
        observed_legacy = graph.client.list_tasks(None, None, None, None, None)[0]
    finally:
        graph.client.close()

    assert observed_native is not None
    assert {field: observed_native.get(field) for field in _V3_TASK_FIELDS} == {
        field: native[field] for field in _V3_TASK_FIELDS
    }
    assert {field: observed_legacy.get(field) for field in _V3_TASK_FIELDS} == {
        field: legacy[field] for field in _V3_TASK_FIELDS
    }
    assert observed_native["pending_transition"] is not None
    assert observed_native["legacy_reconciliations"] == []
    assert observed_legacy["pending_transition"] is None
    assert observed_legacy["legacy_reconciliations"] == legacy["legacy_reconciliations"]


def test_task_reads_fail_closed_when_any_v3_contract_field_is_missing(
    signer: GatewayRequestSigner,
) -> None:
    observed: list[tuple[str, str, dict[str, object]]] = []
    for endpoint in ("get", "list"):
        for field in _V3_TASK_FIELDS:
            task = _task_projection(legacy=False)
            del task[field]
            response_body: object = task if endpoint == "get" else {"tasks": [task]}
            graph = _recording_client(signer, [httpx.Response(200, json=response_body)])
            try:
                if endpoint == "get":
                    outcome = _service_error(lambda client=graph.client: client.get_task(_TASK_ID))
                else:
                    outcome = _service_error(
                        lambda client=graph.client: client.list_tasks(None, None, None, None, None)
                    )
            finally:
                graph.client.close()
            observed.append((endpoint, field, outcome))
    simplified = [
        (endpoint, field, outcome.get("status"), outcome.get("error"))
        for endpoint, field, outcome in observed
    ]
    assert simplified == [
        (endpoint, field, 502, "invalid_gateway_response")
        for endpoint in ("get", "list")
        for field in _V3_TASK_FIELDS
    ]


def test_task_publication_forwards_explicit_acceptance_window_unchanged(
    signer: GatewayRequestSigner,
) -> None:
    graph = _recording_client(signer, [httpx.Response(201, json={"task_id": _TASK_ID})])
    task = _task_projection(legacy=False)
    task["status"] = "open"
    task["bid_count"] = 0
    task["worker_id"] = None
    task["accepted_bid_id"] = None
    task["acceptance_window_seconds"] = 37
    try:
        graph.client.insert_task(task)
    finally:
        graph.client.close()

    assert len(graph.requests) == 1
    payload = json.loads(graph.requests[0].content)
    assert payload.get("acceptance_window_seconds") == 37
