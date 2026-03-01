"""Router test fixtures with mocked external services."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient

from court_service.app import create_app
from court_service.config import clear_settings_cache
from court_service.core.state import get_app_state, reset_app_state
from tests.helpers import (
    make_jws_token,
    make_mock_central_bank_client,
    make_mock_identity_client,
    make_mock_judge,
    make_mock_reputation_client,
    make_mock_task_board_client,
    make_task_data,
    new_escrow_id,
    new_task_id,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

PLATFORM_AGENT_ID = "a-platform-test-id"
ROGUE_AGENT_ID = "a-rogue-test-id"
CLAIMANT_ID = "a-claimant-test-id"
RESPONDENT_ID = "a-respondent-test-id"


def _valid_config(tmp_path: Any, db_path: str | None = None) -> str:
    """Write a valid court config.yaml and return its path."""
    if db_path is None:
        db_path = str(tmp_path / "test.db")
    config_content = f"""\
service:
  name: "court"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8005
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
database:
  path: "{db_path}"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
platform:
  agent_id: "{PLATFORM_AGENT_ID}"
request:
  max_body_size: 1048576
disputes:
  rebuttal_deadline_seconds: 86400
  max_claim_length: 10000
  max_rebuttal_length: 10000
judges:
  panel_size: 1
  judges:
    - id: "judge-0"
      provider: "mock"
      model: "test-model"
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return str(config_path)


@pytest.fixture
async def app(tmp_path: Any) -> AsyncIterator[FastAPI]:
    """Create a test app with mocked external services."""
    config_path = _valid_config(tmp_path)
    os.environ["CONFIG_PATH"] = config_path

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with test_app.router.lifespan_context(test_app):
        state = get_app_state()
        state.identity_client = make_mock_identity_client(
            verify_response={
                "valid": True,
                "agent_id": PLATFORM_AGENT_ID,
                "payload": {},
            }
        )
        state.task_board_client = make_mock_task_board_client(task_response=make_task_data())
        state.central_bank_client = make_mock_central_bank_client()
        state.reputation_client = make_mock_reputation_client()
        state.judges = [make_mock_judge()]
        yield test_app

    reset_app_state()
    clear_settings_cache()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Create an async HTTP client for the test app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


def file_dispute_payload(
    task_id: str | None = None,
    claimant_id: str | None = None,
    respondent_id: str | None = None,
    claim: str = "The worker did not deliver as specified.",
    escrow_id: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Return a valid file_dispute JWS payload."""
    base: dict[str, Any] = {
        "action": "file_dispute",
        "task_id": task_id or new_task_id(),
        "claimant_id": claimant_id or CLAIMANT_ID,
        "respondent_id": respondent_id or RESPONDENT_ID,
        "claim": claim,
        "escrow_id": escrow_id or new_escrow_id(),
    }
    base.update(overrides)
    return base


def rebuttal_payload(
    dispute_id: str,
    rebuttal: str = "The specification was ambiguous.",
    **overrides: Any,
) -> dict[str, Any]:
    """Return a valid submit_rebuttal JWS payload."""
    base: dict[str, Any] = {
        "action": "submit_rebuttal",
        "dispute_id": dispute_id,
        "rebuttal": rebuttal,
    }
    base.update(overrides)
    return base


def ruling_payload(dispute_id: str, **overrides: Any) -> dict[str, Any]:
    """Return a valid trigger_ruling JWS payload."""
    base: dict[str, Any] = {
        "action": "trigger_ruling",
        "dispute_id": dispute_id,
    }
    base.update(overrides)
    return base


def token_body(payload: dict[str, Any], kid: str = PLATFORM_AGENT_ID) -> dict[str, str]:
    """Wrap a payload into a {token: ...} body."""
    return {"token": make_jws_token(payload, kid=kid)}


def inject_identity_verify(
    agent_id: str,
    payload: dict[str, Any],
    valid: bool = True,
) -> None:
    """Update the mock identity client to return a specific verify response."""
    state = get_app_state()
    state.identity_client = make_mock_identity_client(
        verify_response={"valid": valid, "agent_id": agent_id, "payload": payload}
    )


def inject_identity_error(error: Exception) -> None:
    """Update the mock identity client to raise an error."""
    state = get_app_state()
    state.identity_client = make_mock_identity_client(verify_side_effect=error)


def inject_task_board_response(task_data: dict[str, Any] | None = None) -> None:
    """Update the mock task board client response."""
    state = get_app_state()
    if task_data is None:
        task_data = make_task_data()
    state.task_board_client = make_mock_task_board_client(task_response=task_data)


def inject_task_board_error(error: Exception) -> None:
    """Update the mock task board client to raise an error."""
    state = get_app_state()
    state.task_board_client = make_mock_task_board_client(task_side_effect=error)


def inject_judge(
    worker_pct: int = 70,
    reasoning: str = "Test reasoning.",
    side_effect: Exception | None = None,
) -> None:
    """Update the mock judge."""
    state = get_app_state()
    state.judges = [
        make_mock_judge(worker_pct=worker_pct, reasoning=reasoning, side_effect=side_effect)
    ]


def inject_central_bank_error(error: Exception) -> None:
    """Update the mock central bank client to raise an error."""
    state = get_app_state()
    state.central_bank_client = make_mock_central_bank_client(split_side_effect=error)


def inject_reputation_error(error: Exception) -> None:
    """Update the mock reputation client to raise an error."""
    state = get_app_state()
    state.reputation_client = make_mock_reputation_client(feedback_side_effect=error)


async def file_dispute(
    client: AsyncClient,
    payload: dict[str, Any] | None = None,
    kid: str = PLATFORM_AGENT_ID,
) -> dict[str, Any]:
    """File a dispute and return the response JSON. Assumes mocks are configured."""
    if payload is None:
        payload = file_dispute_payload()
    inject_identity_verify(kid, payload)
    response = await client.post("/disputes/file", json=token_body(payload, kid=kid))
    assert response.status_code == 201, f"Failed to file dispute: {response.text}"
    return response.json()


async def file_and_rebut(
    client: AsyncClient,
    file_payload: dict[str, Any] | None = None,
    rebuttal_text: str = "The specification was ambiguous.",
    kid: str = PLATFORM_AGENT_ID,
) -> dict[str, Any]:
    """File a dispute, submit a rebuttal, return the file dispute response JSON."""
    dispute = await file_dispute(client, payload=file_payload, kid=kid)
    dispute_id = dispute["dispute_id"]
    reb_payload = rebuttal_payload(dispute_id, rebuttal=rebuttal_text)
    inject_identity_verify(kid, reb_payload)
    response = await client.post(
        f"/disputes/{dispute_id}/rebuttal",
        json=token_body(reb_payload, kid=kid),
    )
    assert response.status_code == 200, f"Failed to submit rebuttal: {response.text}"
    return dispute


async def file_rebut_and_rule(
    client: AsyncClient,
    file_payload: dict[str, Any] | None = None,
    rebuttal_text: str = "The specification was ambiguous.",
    worker_pct: int = 70,
    kid: str = PLATFORM_AGENT_ID,
) -> dict[str, Any]:
    """File, rebut, and rule a dispute. Returns the ruling response JSON."""
    dispute = await file_and_rebut(client, file_payload, rebuttal_text, kid)
    dispute_id = dispute["dispute_id"]
    inject_judge(worker_pct=worker_pct)
    rule_pay = ruling_payload(dispute_id)
    inject_identity_verify(kid, rule_pay)
    response = await client.post(
        f"/disputes/{dispute_id}/rule",
        json=token_body(rule_pay, kid=kid),
    )
    assert response.status_code == 200, f"Failed to trigger ruling: {response.text}"
    return response.json()
