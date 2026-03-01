"""Shared test helpers for Court service tests."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock


def make_jws_token(payload: dict[str, Any], kid: str = "a-platform-test") -> str:
    """Build a fake but structurally valid JWS compact serialization."""
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "EdDSA", "kid": kid}).encode())
        .rstrip(b"=")
        .decode()
    )
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(b"fake-signature").rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


def make_tampered_jws(payload: dict[str, Any], kid: str = "a-platform-test") -> str:
    """Build a JWS with a modified payload (signature will not match)."""
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "EdDSA", "kid": kid}).encode())
        .rstrip(b"=")
        .decode()
    )
    original = json.dumps(payload).encode()
    tampered = json.dumps({**payload, "_tampered": True}).encode()
    body = base64.urlsafe_b64encode(tampered).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(b"sig-over-" + original[:20]).rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


def make_mock_identity_client(
    verify_response: dict[str, Any] | None = None,
    verify_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock IdentityClient returning predictable responses."""
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    if verify_side_effect is not None:
        mock_client.verify_jws.side_effect = verify_side_effect
    elif verify_response is not None:
        mock_client.verify_jws.return_value = verify_response
    return mock_client


def make_mock_task_board_client(
    task_response: dict[str, Any] | None = None,
    task_side_effect: Exception | None = None,
    record_ruling_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock TaskBoardClient."""
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    if task_side_effect is not None:
        mock_client.get_task.side_effect = task_side_effect
    elif task_response is not None:
        mock_client.get_task.return_value = task_response
    if record_ruling_side_effect is not None:
        mock_client.record_ruling.side_effect = record_ruling_side_effect
    else:
        mock_client.record_ruling.return_value = {"status": "ok"}
    return mock_client


def make_mock_central_bank_client(
    split_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock CentralBankClient."""
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    if split_side_effect is not None:
        mock_client.split_escrow.side_effect = split_side_effect
    else:
        mock_client.split_escrow.return_value = {"status": "ok"}
    return mock_client


def make_mock_reputation_client(
    feedback_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock ReputationClient."""
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    if feedback_side_effect is not None:
        mock_client.record_feedback.side_effect = feedback_side_effect
    else:
        mock_client.record_feedback.return_value = {"status": "ok"}
    return mock_client


def make_mock_judge(
    worker_pct: int = 70,
    reasoning: str = "Test reasoning for the ruling.",
    side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock Judge that returns a predictable vote."""
    mock_judge = AsyncMock()
    if side_effect is not None:
        mock_judge.evaluate.side_effect = side_effect
    else:
        mock_judge.evaluate.return_value = {
            "worker_pct": worker_pct,
            "reasoning": reasoning,
        }
    return mock_judge


def new_task_id() -> str:
    """Generate a random task ID."""
    return f"t-{uuid.uuid4()}"


def new_agent_id() -> str:
    """Generate a random agent ID."""
    return f"a-{uuid.uuid4()}"


def new_escrow_id() -> str:
    """Generate a random escrow ID."""
    return f"esc-{uuid.uuid4()}"


def make_task_data(task_id: str | None = None) -> dict[str, Any]:
    """Create a valid task data response from the Task Board mock."""
    return {
        "task_id": task_id or new_task_id(),
        "title": "Implement email validation",
        "spec": "Build a login page with email validation.",
        "deliverables": "Login page with email field.",
        "reward": 1000,
        "status": "disputed",
    }
