"""Failing-first server contract for durable native UI task posting."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient
from mp05_posting_fixtures import (
    DURATION_MAX,
    OPERATION_ID,
    POSTING_TERMS,
    PostingAgent,
    mutated_record,
    prepared_record,
    publication,
    response,
)
from service_auth.task_postings import TaskPostingResponse
from service_commons.exceptions import ServiceError

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
    (web_dir / "index.html").write_text("<html><body>native posting proxy test</body></html>")
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
        async with (
            lifespan(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Origin": "http://localhost", "X-ATE-CSRF": "local-operator"},
            ) as test_client,
        ):
            yield test_client
    finally:
        reset_app_state()
        clear_settings_cache()


async def _prepare(test_client: AsyncClient, agent: PostingAgent) -> dict[str, Any]:
    get_app_state().user_agent = agent  # type: ignore[assignment]
    result = await test_client.post("/api/proxy/task-postings/prepare", json=POSTING_TERMS)
    assert result.status_code == 200
    value = result.json()
    assert isinstance(value, dict)
    return value


async def test_prepare_returns_exact_full_record_without_network_effect(
    client: AsyncClient,
) -> None:
    agent = PostingAgent()

    record = await _prepare(client, agent)

    assert record == prepared_record(agent.config)
    assert agent.prepare_calls == [POSTING_TERMS]
    assert agent.submit_calls == []
    assert agent.status_calls == []
    assert "private_key" not in repr(record)
    assert "token" not in repr(record)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reward", True),
        ("reward", "75"),
        ("reward", 0),
        ("bidding_deadline_seconds", 1.5),
        ("acceptance_window_seconds", 1e2),
        ("execution_deadline_seconds", DURATION_MAX + 1),
        ("review_deadline_seconds", -1),
    ],
)
async def test_prepare_rejects_non_strict_or_out_of_range_numbers_before_effect(
    client: AsyncClient, field: str, value: object
) -> None:
    agent = PostingAgent()
    get_app_state().user_agent = agent  # type: ignore[assignment]

    result = await client.post(
        "/api/proxy/task-postings/prepare",
        json={**POSTING_TERMS, field: value},
    )

    assert result.status_code == 422
    assert agent.prepare_calls == []
    assert agent.submit_calls == []


@pytest.mark.parametrize("case", ["missing", "extra"])
async def test_prepare_requires_exact_seven_posting_terms(client: AsyncClient, case: str) -> None:
    agent = PostingAgent()
    get_app_state().user_agent = agent  # type: ignore[assignment]
    body = dict(POSTING_TERMS)
    if case == "missing":
        del body["acceptance_window_seconds"]
    else:
        body["unexpected"] = "field"

    result = await client.post("/api/proxy/task-postings/prepare", json=body)

    assert result.status_code == 422
    assert agent.prepare_calls == []
    assert agent.submit_calls == []


@pytest.mark.parametrize(
    "case", ["scope-agent", "scope-key", "scope-url", "actor", "hash", "terms", "extra"]
)
@pytest.mark.parametrize("action", ["submit", "status"])
async def test_saved_record_mismatch_is_rejected_before_transport(
    client: AsyncClient, case: str, action: str
) -> None:
    agent = PostingAgent()
    get_app_state().user_agent = agent  # type: ignore[assignment]
    record = mutated_record(prepared_record(agent.config), case)

    result = await client.post(
        f"/api/proxy/task-postings/{OPERATION_ID}/{action}",
        json=record,
    )

    assert result.status_code == 422
    assert agent.submit_calls == []
    assert agent.status_calls == []


@pytest.mark.parametrize("action", ["submit", "status"])
async def test_path_operation_identity_mismatch_has_no_transport(
    client: AsyncClient, action: str
) -> None:
    agent = PostingAgent()
    get_app_state().user_agent = agent  # type: ignore[assignment]
    record = prepared_record(agent.config)
    foreign = "op-00000000-0000-4000-8000-000000000999"

    result = await client.post(f"/api/proxy/task-postings/{foreign}/{action}", json=record)

    assert result.status_code == 422
    assert agent.submit_calls == []
    assert agent.status_calls == []


@pytest.mark.parametrize(
    ("state", "expected_status"),
    [("published", 201), ("awaiting_lock", 202), ("blocked_integrity", 409)],
)
async def test_submit_returns_bound_normalized_publication(
    client: AsyncClient, state: str, expected_status: int
) -> None:
    agent = PostingAgent()
    posting_intent = agent.prepare_task_posting(**POSTING_TERMS)
    agent.prepare_calls.clear()
    agent.submit_response = response(posting_intent, state, observation="submit")  # type: ignore[arg-type]
    get_app_state().user_agent = agent  # type: ignore[assignment]
    record = prepared_record(agent.config, posting_intent)

    result = await client.post(
        f"/api/proxy/task-postings/{OPERATION_ID}/submit",
        json=record,
    )

    assert result.status_code == expected_status
    assert result.json() == {
        "observation": "submit",
        "status_code": expected_status,
        "publication": publication(posting_intent, state),
    }
    assert [item.as_json() for item in agent.submit_calls] == [posting_intent.as_json()]


@pytest.mark.parametrize("state", ["published", "awaiting_lock", "blocked_integrity"])
async def test_status_binds_sdk_observation_to_complete_saved_intent(
    client: AsyncClient, state: str
) -> None:
    agent = PostingAgent()
    posting_intent = agent.prepare_task_posting(**POSTING_TERMS)
    agent.prepare_calls.clear()
    agent.status_response = response(posting_intent, state, observation="status")  # type: ignore[arg-type]
    get_app_state().user_agent = agent  # type: ignore[assignment]
    record = prepared_record(agent.config, posting_intent)

    result = await client.post(
        f"/api/proxy/task-postings/{OPERATION_ID}/status",
        json=record,
    )

    assert result.status_code == 200
    assert result.json() == {
        "observation": "status",
        "status_code": 200,
        "publication": publication(posting_intent, state),
    }
    assert agent.status_calls == [OPERATION_ID]


@pytest.mark.parametrize("action", ["submit", "status"])
async def test_malformed_acknowledgment_becomes_safe_502(client: AsyncClient, action: str) -> None:
    agent = PostingAgent()
    malformed = TaskPostingResponse(status_code=201 if action == "submit" else 200, body={})
    if action == "submit":
        agent.submit_response = malformed
    else:
        agent.status_response = malformed
    get_app_state().user_agent = agent  # type: ignore[assignment]

    result = await client.post(
        f"/api/proxy/task-postings/{OPERATION_ID}/{action}",
        json=prepared_record(agent.config),
    )

    assert result.status_code == 502
    assert result.json() == {
        "error": "task_posting_failed",
        "message": "Task Board returned a malformed posting acknowledgment",
        "details": {},
    }


@pytest.mark.parametrize("field", ["operation_id", "request_sha256", "reward"])
async def test_semantically_wrong_acknowledgment_terms_become_safe_502(
    client: AsyncClient, field: str
) -> None:
    agent = PostingAgent()
    posting_intent = agent.prepare_task_posting(**POSTING_TERMS)
    agent.prepare_calls.clear()
    wrong = publication(posting_intent, "published")
    if field == "operation_id":
        wrong["operation_id"] = "op-00000000-0000-4000-8000-000000000999"
    elif field == "request_sha256":
        wrong["request_sha256"] = "0" * 64
    else:
        result_value = wrong["result"]
        assert isinstance(result_value, dict)
        result_value["reward"] = 76
    agent.submit_response = TaskPostingResponse(status_code=201, body=wrong)
    get_app_state().user_agent = agent  # type: ignore[assignment]

    result = await client.post(
        f"/api/proxy/task-postings/{OPERATION_ID}/submit",
        json=prepared_record(agent.config, posting_intent),
    )

    assert result.status_code == 502
    assert result.json()["error"] == "task_posting_failed"


@pytest.mark.parametrize("action", ["submit", "status"])
async def test_typed_upstream_error_shape_is_preserved(client: AsyncClient, action: str) -> None:
    agent = PostingAgent()
    error = ServiceError(
        "posting_unavailable",
        "Publication reconciliation is temporarily unavailable",
        503,
        {"retryable": True},
    )
    if action == "submit":
        agent.submit_response = error
    else:
        agent.status_response = error
    get_app_state().user_agent = agent  # type: ignore[assignment]

    result = await client.post(
        f"/api/proxy/task-postings/{OPERATION_ID}/{action}",
        json=prepared_record(agent.config),
    )

    assert result.status_code == 503
    assert result.json() == {
        "error": "posting_unavailable",
        "message": "Publication reconciliation is temporarily unavailable",
        "details": {"retryable": True},
    }


async def test_old_effectful_create_endpoint_is_removed(client: AsyncClient) -> None:
    agent = PostingAgent()
    get_app_state().user_agent = agent  # type: ignore[assignment]

    result = await client.post("/api/proxy/tasks", json=copy.deepcopy(POSTING_TERMS))

    assert result.status_code in {404, 405}
    assert agent.submit_calls == []
    assert agent.prepare_calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/proxy/task-postings/prepare",
        f"/api/proxy/task-postings/{OPERATION_ID}/submit",
        f"/api/proxy/task-postings/{OPERATION_ID}/status",
    ],
)
async def test_new_posting_routes_require_json_content_type(client: AsyncClient, path: str) -> None:
    agent = PostingAgent()
    get_app_state().user_agent = agent  # type: ignore[assignment]

    result = await client.post(path, content=b"{}", headers={"Content-Type": "text/plain"})

    assert result.status_code == 415
    assert result.json()["error"] == "unsupported_media_type"
    assert agent.prepare_calls == []
    assert agent.submit_calls == []
    assert agent.status_calls == []


async def test_new_routes_keep_existing_browser_security_boundary(client: AsyncClient) -> None:
    agent = PostingAgent()
    get_app_state().user_agent = agent  # type: ignore[assignment]

    result = await client.post(
        "/api/proxy/task-postings/prepare",
        json=POSTING_TERMS,
        headers={"Origin": "https://foreign.invalid", "X-ATE-CSRF": "local-operator"},
    )

    assert result.status_code == 403
    assert result.json()["error"] == "forbidden_origin"
    assert agent.prepare_calls == []
    assert agent.submit_calls == []
