"""Failing-first UI producer contract for MP-07 native task operations."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from service_auth.config import AgentConfig
from service_auth.financial_identity import operation_id_for
from service_auth.jws import TokenPolicy
from service_auth.task_board_responses import TaskBoardCommandResponse
from service_auth.task_postings import TaskPostingIntent
from service_auth.user_agent import UserAgent

from ui_service.app import create_app
from ui_service.config import clear_settings_cache
from ui_service.core import lifespan as lifespan_module
from ui_service.core.lifespan import lifespan
from ui_service.core.state import get_app_state, reset_app_state

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI

pytestmark = pytest.mark.unit

OPERATION_ID = "op-00000000-0000-4000-8000-000000000701"

VALID_CREATE_BODY: dict[str, Any] = {
    "title": "Native UI task",
    "spec": "Keep the caller intent explicit.",
    "reward": 75,
    "bidding_deadline_seconds": 120,
    "acceptance_window_seconds": 45,
    "execution_deadline_seconds": 300,
    "review_deadline_seconds": 90,
}


def _agent_config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    return AgentConfig(
        name="UI operator",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url="https://identity.test",
        bank_url="https://bank.test",
        task_board_url="https://board.test",
        reputation_url="https://reputation.test",
        court_url="https://court.test",
        token_ttl_seconds=30,
        token_policy=TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0),
    )


async def _real_user_agent(handler: Any) -> UserAgent:
    agent = UserAgent(_agent_config())
    await agent._http.aclose()
    agent.agent_id = "a-ui-native"
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return agent


class _StartupUserAgent:
    agent_id = "a-ui-startup"
    name = "ui-startup"

    async def register(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _StartupAgentFactory:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def user_agent(self, handle: str) -> _StartupUserAgent:
        assert handle == "ui-native-test"
        return _StartupUserAgent()


@pytest.fixture
async def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>native proxy test</body></html>")
    (tmp_path / "economy.db").touch()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""\
service:
  name: "ui"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8008
  log_level: "info"
telemetry:
  enabled: false
  otlp_endpoint: "http://127.0.0.1:4318"
  sample_ratio: 1.0
  excluded_urls: "health"
logging:
  level: "WARNING"
  directory: "{tmp_path / "logs"}"
database:
  path: "{tmp_path / "economy.db"}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "{web_dir}"
request:
  max_body_size: 1572864
recovery:
  maintenance_lock_path: "{tmp_path / "maintenance.lock"}"
browser_security:
  allowed_hosts: ["localhost"]
  allowed_origins: ["http://localhost"]
  csrf_header_name: "X-ATE-CSRF"
  csrf_header_value: "local-operator"
  content_security_policy: "default-src 'self'; script-src 'self'; object-src 'none'"
user_agent:
  agent_config_path: "{tmp_path / "agent-config.yaml"}"
  handle: "ui-native-test"
"""
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setattr(lifespan_module, "AgentFactory", _StartupAgentFactory)
    clear_settings_cache()
    reset_app_state()
    try:
        app: FastAPI = create_app()
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://localhost",
                headers={
                    "Origin": "http://localhost",
                    "X-ATE-CSRF": "local-operator",
                },
            ) as test_client:
                yield test_client
    finally:
        reset_app_state()
        clear_settings_cache()


def _operation(command: str, state: str) -> dict[str, Any]:
    contracts = {
        "accept_bid": ("open", "accepted", "none"),
        "approve_task": ("submitted", "approved", "bank_release"),
        "dispute_task": ("submitted", "disputed", "court_claim"),
    }
    source_status, target_status, effect_kind = contracts[command]
    effect_state = (
        "none" if effect_kind == "none" else ("pending" if state == "claimed" else "succeeded")
    )
    effect_operation_id: str | None = None
    effect_result_reference: str | None = None
    effect_intent: dict[str, Any] | None = None
    if command == "approve_task":
        financial_request = {
            "escrow_id": "esc-native",
            "operation_kind": "escrow_release",
            "recipient_account_id": "a-worker",
            "request_version": 1,
            "source_reference": "task:t-native-ui:approved",
        }
        encoded = json.dumps(financial_request, sort_keys=True, separators=(",", ":"))
        effect_operation_id = OPERATION_ID
        effect_result_reference = OPERATION_ID if state == "completed" else None
        effect_intent = {
            "financial_request": financial_request,
            "financial_request_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "expected_escrow": {
                "escrow_id": "esc-native",
                "task_id": "t-native-ui",
                "payer_account_id": "a-ui-native",
                "amount": 75,
            },
        }
    elif command == "dispute_task":
        effect_result_reference = "clm-native" if state == "completed" else None
        effect_intent = {
            "court_claim": {
                "task_id": "t-native-ui",
                "claimant_id": "a-ui-native",
                "respondent_id": "a-worker",
                "reason": "Terms are incomplete.",
            },
            "expected_escrow": {
                "escrow_id": "esc-native",
                "task_id": "t-native-ui",
                "payer_account_id": "a-ui-native",
                "amount": 75,
            },
        }
    event_id = None if state == "claimed" else 41
    result: dict[str, Any] | None = None
    if state == "completed":
        result = {
            "task_id": "t-native-ui",
            "command": command,
            "status": target_status,
            "lifecycle_version": 8,
            "event_id": event_id,
        }
        if command == "accept_bid":
            result.update(
                {
                    "bid_id": "bid-native",
                    "worker_id": "a-worker",
                    "reward": 75,
                    "bid_amount": 60,
                }
            )
        elif command == "approve_task":
            result.update(
                {
                    "effect_result_reference": OPERATION_ID,
                    "worker_id": "a-worker",
                    "reward": 75,
                }
            )
        else:
            result.update(
                {
                    "effect_result_reference": "clm-native",
                    "dispute_id": "clm-native",
                    "reason": "Terms are incomplete.",
                    "worker_id": "a-worker",
                }
            )
    return {
        "protocol_version": 1,
        "operation_id": OPERATION_ID,
        "task_id": "t-native-ui",
        "command": command,
        "request_sha256": "a" * 64,
        "operation_state": state,
        "source_status": source_status,
        "source_lifecycle_version": 7,
        "target_status": target_status,
        "target_lifecycle_version": 8,
        "effect_kind": effect_kind,
        "effect_state": effect_state,
        "effect_operation_id": effect_operation_id,
        "effect_result_reference": effect_result_reference,
        "effect_intent": effect_intent,
        "event_id": event_id,
        "result": result,
    }


def _mutation_result(command: str, state: str) -> dict[str, Any]:
    source_status, target_status = {
        "accept_bid": ("open", "accepted"),
        "approve_task": ("submitted", "approved"),
        "dispute_task": ("submitted", "disputed"),
    }[command]
    status = source_status if state == "claimed" else target_status
    lifecycle_version = 7 if state == "claimed" else 8
    pending_transition = None
    if state == "claimed":
        pending_transition = {
            "operation_id": OPERATION_ID,
            "command": command,
            "target_status": target_status,
            "target_lifecycle_version": 8,
            "operation_state": "claimed",
            "effect_state": "pending",
        }
    return {
        "task_id": "t-native-ui",
        "poster_id": "a-ui-native",
        "title": "Native UI task",
        "spec": "Keep the caller intent explicit.",
        "reward": 75,
        "bidding_deadline_seconds": 120,
        "deadline_seconds": 300,
        "review_deadline_seconds": 90,
        "status": status,
        "escrow_id": "esc-native",
        "bid_count": 1,
        "worker_id": "a-worker",
        "accepted_bid_id": "bid-native",
        "created_at": "2026-10-18T11:58:00Z",
        "accepted_at": "2026-10-18T12:01:00Z",
        "submitted_at": None if command == "accept_bid" else "2026-10-18T12:03:00Z",
        "approved_at": "2026-10-18T12:04:00Z" if status == "approved" else None,
        "cancelled_at": None,
        "disputed_at": "2026-10-18T12:04:00Z" if status == "disputed" else None,
        "dispute_reason": "Terms are incomplete." if status == "disputed" else None,
        "dispute_id": "clm-native" if status == "disputed" else None,
        "rebuttal_deadline": None,
        "rebuttal_submitted_at": None,
        "ruling_id": None,
        "ruled_at": None,
        "worker_pct": None,
        "ruling_summary": None,
        "expired_at": None,
        "escrow_pending": False,
        "bidding_deadline": "2026-10-18T12:00:00Z",
        "acceptance_deadline": "2026-10-18T12:00:45Z",
        "execution_deadline": "2026-10-18T12:06:00Z",
        "review_deadline": None if command == "accept_bid" else "2026-10-18T12:04:30Z",
        "lifecycle_version": lifecycle_version,
        "timing_contract_version": 2,
        "acceptance_window_seconds": 45,
        "bidding_cutoff_us": 1_000_000,
        "acceptance_cutoff_us": 46_000_000,
        "execution_cutoff_us": 346_000_000,
        "review_cutoff_us": None if command == "accept_bid" else 436_000_000,
        "accepted_reward": 75,
        "accepted_bid_amount": 60,
        "pending_transition": pending_transition,
        "legacy_reconciliations": [],
        "operation": _operation(command, state),
    }


class _RecordingUserAgent:
    """Network-free recorder for the UI-owned SDK call boundary."""

    agent_id = "a-ui-native"

    def __init__(self, mutation_state: str = "completed") -> None:
        self.config = _agent_config()
        self.mutation_state = mutation_state
        self.mutation_result_override: dict[str, Any] | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def prepare_task_posting(self, **kwargs: Any) -> TaskPostingIntent:
        self.calls.append(("prepare", kwargs))
        task_id = "t-00000000-0000-4000-8000-000000000702"
        return TaskPostingIntent.from_json(
            {
                "protocol_version": 1,
                "operation_id": OPERATION_ID,
                "task_id": task_id,
                "actor_kind": "agent",
                "actor_id": self.agent_id,
                "lock_operation_id": operation_id_for("task-lock", (task_id, self.agent_id)),
                **kwargs,
            }
        )

    async def accept_bid(self, **kwargs: Any) -> TaskBoardCommandResponse:
        self.calls.append(("accept", kwargs))
        return TaskBoardCommandResponse(
            status_code=200,
            body=_mutation_result("accept_bid", "completed"),
        )

    async def approve_task(self, **kwargs: Any) -> TaskBoardCommandResponse:
        self.calls.append(("approve", kwargs))
        if self.mutation_result_override is not None:
            return TaskBoardCommandResponse(
                status_code=200,
                body=self.mutation_result_override,
            )
        return TaskBoardCommandResponse(
            status_code=202 if self.mutation_state == "claimed" else 200,
            body=_mutation_result("approve_task", self.mutation_state),
        )

    async def dispute_task(self, **kwargs: Any) -> TaskBoardCommandResponse:
        self.calls.append(("dispute", kwargs))
        return TaskBoardCommandResponse(
            status_code=202 if self.mutation_state == "claimed" else 200,
            body=_mutation_result("dispute_task", self.mutation_state),
        )

    async def close(self) -> None:
        return None


async def test_create_requires_and_forwards_poster_acceptance_window(client) -> None:
    agent = _RecordingUserAgent()
    get_app_state().user_agent = agent

    response = await client.post("/api/proxy/task-postings/prepare", json=VALID_CREATE_BODY)

    assert response.status_code == 200
    assert response.json()["intent"]["acceptance_window_seconds"] == 45
    assert agent.calls == [("prepare", VALID_CREATE_BODY)]


async def test_create_rejects_missing_acceptance_window_before_sdk(client) -> None:
    agent = _RecordingUserAgent()
    get_app_state().user_agent = agent
    body = dict(VALID_CREATE_BODY)
    del body["acceptance_window_seconds"]

    response = await client.post("/api/proxy/task-postings/prepare", json=body)

    assert response.status_code == 422
    assert agent.calls == []


@pytest.mark.parametrize("value", [True, "45", 0, 9_223_372_036_855])
async def test_create_rejects_non_strict_or_unrepresentable_acceptance_window(
    client,
    value: object,
) -> None:
    agent = _RecordingUserAgent()
    get_app_state().user_agent = agent

    response = await client.post(
        "/api/proxy/task-postings/prepare",
        json={**VALID_CREATE_BODY, "acceptance_window_seconds": value},
    )

    assert response.status_code == 422
    assert agent.calls == []


@pytest.mark.parametrize(
    ("path", "body", "expected_call"),
    [
        (
            "/api/proxy/tasks/t-native-ui/bids/bid-native/accept",
            {
                "operation_id": OPERATION_ID,
                "expected_lifecycle_version": 7,
            },
            (
                "accept",
                {
                    "task_id": "t-native-ui",
                    "bid_id": "bid-native",
                    "operation_id": OPERATION_ID,
                    "expected_lifecycle_version": 7,
                },
            ),
        ),
        (
            "/api/proxy/tasks/t-native-ui/approve",
            {
                "operation_id": OPERATION_ID,
                "expected_lifecycle_version": 7,
            },
            (
                "approve",
                {
                    "task_id": "t-native-ui",
                    "operation_id": OPERATION_ID,
                    "expected_lifecycle_version": 7,
                },
            ),
        ),
        (
            "/api/proxy/tasks/t-native-ui/dispute",
            {
                "reason": "The submitted artifact does not meet the specification.",
                "operation_id": OPERATION_ID,
                "expected_lifecycle_version": 7,
            },
            (
                "dispute",
                {
                    "task_id": "t-native-ui",
                    "reason": "The submitted artifact does not meet the specification.",
                    "operation_id": OPERATION_ID,
                    "expected_lifecycle_version": 7,
                },
            ),
        ),
    ],
)
async def test_native_mutation_forwards_exact_caller_identity(
    client,
    path: str,
    body: dict[str, Any],
    expected_call: tuple[str, dict[str, Any]],
) -> None:
    agent = _RecordingUserAgent()
    get_app_state().user_agent = agent

    response = await client.post(path, json=body)

    assert response.status_code == 200
    assert agent.calls == [expected_call]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/proxy/tasks/t-native-ui/bids/bid-native/accept", {}),
        ("/api/proxy/tasks/t-native-ui/approve", {}),
        (
            "/api/proxy/tasks/t-native-ui/dispute",
            {"reason": "Terms are incomplete."},
        ),
        (
            "/api/proxy/tasks/t-native-ui/approve",
            {
                "operation_id": OPERATION_ID,
                "expected_lifecycle_version": True,
            },
        ),
        (
            "/api/proxy/tasks/t-native-ui/approve",
            {
                "operation_id": OPERATION_ID,
                "expected_lifecycle_version": "7",
            },
        ),
        (
            "/api/proxy/tasks/t-native-ui/approve",
            {
                "operation_id": OPERATION_ID,
                "expected_lifecycle_version": -1,
            },
        ),
        (
            "/api/proxy/tasks/t-native-ui/approve",
            {
                "operation_id": OPERATION_ID,
                "expected_lifecycle_version": 9_223_372_036_854_775_808,
            },
        ),
        (
            "/api/proxy/tasks/t-native-ui/approve",
            {
                "operation_id": "generated-later",
                "expected_lifecycle_version": 7,
            },
        ),
        (
            "/api/proxy/tasks/t-native-ui/approve",
            {
                "operation_id": OPERATION_ID,
                "expected_lifecycle_version": 7,
                "unexpected": "field",
            },
        ),
    ],
)
async def test_native_mutation_rejects_missing_or_invalid_identity_before_sdk(
    client,
    path: str,
    body: dict[str, Any],
) -> None:
    agent = _RecordingUserAgent()
    get_app_state().user_agent = agent

    response = await client.post(path, json=body)

    assert response.status_code == 422
    assert agent.calls == []


@pytest.mark.parametrize(("state", "status_code"), [("claimed", 202), ("completed", 200)])
async def test_proxy_preserves_flat_operation_response_and_http_state(
    client,
    state: str,
    status_code: int,
) -> None:
    agent = _RecordingUserAgent(mutation_state=state)
    get_app_state().user_agent = agent

    response = await client.post(
        "/api/proxy/tasks/t-native-ui/approve",
        json={
            "operation_id": OPERATION_ID,
            "expected_lifecycle_version": 7,
        },
    )

    assert response.status_code == status_code
    assert response.json() == _mutation_result("approve_task", state)
    assert "task" not in response.json()


async def test_pure_acceptance_cannot_be_projected_as_pending(client) -> None:
    agent = _RecordingUserAgent(mutation_state="claimed")
    get_app_state().user_agent = agent

    response = await client.post(
        "/api/proxy/tasks/t-native-ui/bids/bid-native/accept",
        json={
            "operation_id": OPERATION_ID,
            "expected_lifecycle_version": 7,
        },
    )

    assert response.status_code == 200
    assert response.json() == _mutation_result("accept_bid", "completed")
    assert response.json()["operation"]["effect_kind"] == "none"
    assert response.json()["operation"]["operation_state"] == "completed"


@pytest.mark.parametrize("case", ["source_version", "source_status", "effect_kind"])
async def test_proxy_rejects_semantically_mismatched_success_ack(
    client,
    case: str,
) -> None:
    agent = _RecordingUserAgent()
    result = _mutation_result("approve_task", "completed")
    if case == "source_version":
        result["operation"]["source_lifecycle_version"] = 6
    elif case == "source_status":
        result["operation"]["source_status"] = "open"
    else:
        result["operation"]["effect_kind"] = "court_claim"
        result["operation"]["effect_operation_id"] = None
    agent.mutation_result_override = result
    get_app_state().user_agent = agent

    response = await client.post(
        "/api/proxy/tasks/t-native-ui/approve",
        json={"operation_id": OPERATION_ID, "expected_lifecycle_version": 7},
    )

    assert response.status_code == 502
    assert response.json() == {
        "error": "task_approval_failed",
        "message": "Task Board returned a malformed success response",
        "details": {},
    }


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/proxy/tasks/t-native-ui/bids/bid-native/accept",
            {"operation_id": OPERATION_ID, "expected_lifecycle_version": 7},
        ),
        (
            "/api/proxy/tasks/t-native-ui/approve",
            {"operation_id": OPERATION_ID, "expected_lifecycle_version": 7},
        ),
        (
            "/api/proxy/tasks/t-native-ui/dispute",
            {
                "reason": "Terms are incomplete.",
                "operation_id": OPERATION_ID,
                "expected_lifecycle_version": 7,
            },
        ),
    ],
)
async def test_typed_task_board_conflict_is_preserved_through_real_user_agent(
    client,
    path: str,
    body: dict[str, Any],
) -> None:
    upstream = {
        "error": "task_version_conflict",
        "message": "Task lifecycle version differs from the command precondition",
        "details": {
            "expected_lifecycle_version": 7,
            "actual_lifecycle_version": 8,
            "current_status": "approved",
        },
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(409, json=upstream, request=request)

    get_app_state().user_agent = await _real_user_agent(handler)

    response = await client.post(path, json=body)

    assert len(requests) == 1
    assert response.status_code == 409
    assert response.json() == upstream


@pytest.mark.parametrize(
    "upstream",
    [
        {"error": "task_version_conflict", "message": "missing details"},
        {"error": 17, "message": "wrong error type", "details": {}},
        ["not", "an", "error", "object"],
    ],
)
async def test_malformed_task_board_conflict_becomes_typed_502(
    client,
    upstream: object,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(409, json=upstream, request=request)

    get_app_state().user_agent = await _real_user_agent(handler)

    response = await client.post(
        "/api/proxy/tasks/t-native-ui/approve",
        json={"operation_id": OPERATION_ID, "expected_lifecycle_version": 7},
    )

    assert len(requests) == 1
    assert response.status_code == 502
    assert response.json() == {
        "error": "task_approval_failed",
        "message": "Task Board returned a malformed error response",
        "details": {},
    }
