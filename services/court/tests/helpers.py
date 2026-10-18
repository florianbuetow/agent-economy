"""Shared test helpers for Court service tests."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from cryptography.exceptions import InvalidSignature
from service_auth.jws import InvalidPayloadError, JwsProtectedHeader, VerifiedJws


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def make_ruling_acknowledgment(
    payload: dict[str, object],
    *,
    poster_id: str,
    worker_id: str,
    escrow_id: str,
    reward: int,
    event_id: int,
) -> dict[str, Any]:
    """Build the native completed acknowledgment consumed by Court tests."""
    operation_id = str(payload["operation_id"])
    task_id = str(payload["task_id"])
    ruling_id = str(payload["ruling_id"])
    worker_pct = int(payload["worker_pct"])
    summary = str(payload["ruling_summary"])
    source_version = int(payload["expected_lifecycle_version"])
    financial: dict[str, Any] = {
        "request_version": 1,
        "escrow_id": escrow_id,
        "source_reference": f"task:{task_id}:ruling:{ruling_id}",
    }
    if worker_pct in {0, 100}:
        effect_kind = "bank_release"
        financial.update(
            operation_kind="escrow_release",
            recipient_account_id=worker_id if worker_pct == 100 else poster_id,
        )
    else:
        effect_kind = "bank_split"
        financial.update(
            operation_kind="escrow_split",
            worker_account_id=worker_id,
            poster_account_id=poster_id,
            worker_pct=worker_pct,
        )
    return {
        "task_id": task_id,
        "operation": {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": task_id,
            "command": "record_ruling",
            "request_sha256": "a" * 64,
            "operation_state": "completed",
            "source_status": "disputed",
            "source_lifecycle_version": source_version,
            "target_status": "ruled",
            "target_lifecycle_version": source_version + 1,
            "effect_kind": effect_kind,
            "effect_state": "succeeded",
            "effect_operation_id": operation_id,
            "effect_result_reference": operation_id,
            "effect_intent": {
                "financial_request": financial,
                "financial_request_sha256": _canonical_hash(financial),
                "expected_escrow": {
                    "escrow_id": escrow_id,
                    "task_id": task_id,
                    "payer_account_id": poster_id,
                    "amount": reward,
                },
            },
            "event_id": event_id,
            "result": {
                "task_id": task_id,
                "command": "record_ruling",
                "status": "ruled",
                "lifecycle_version": source_version + 1,
                "event_id": event_id,
                "effect_result_reference": operation_id,
                "worker_id": worker_id,
                "ruling_id": ruling_id,
                "worker_pct": worker_pct,
                "summary": summary,
                "reward": reward,
            },
        },
    }


def _resource_for_payload(payload: dict[str, Any]) -> str:
    action = payload.get("action")
    dispute_id = payload.get("dispute_id")
    if action == "submit_rebuttal":
        return f"POST /disputes/{dispute_id}/rebuttal"
    if action == "trigger_ruling":
        return f"POST /disputes/{dispute_id}/rule"
    return "POST /disputes/file"


def make_jws_token(
    payload: dict[str, Any],
    kid: str = "a-platform-test",
    *,
    resource: str | None = None,
) -> str:
    """Build a fake but structurally valid JWS compact serialization."""
    issued_at = int(time.time())
    header = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "alg": "EdDSA",
                    "typ": "JWT",
                    "kid": kid,
                    "iat": issued_at,
                    "exp": issued_at + 60,
                    "aud": "court",
                    "resource": resource or _resource_for_payload(payload),
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(bytes(64)).rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


def make_tampered_jws(
    payload: dict[str, Any],
    kid: str = "a-platform-test",
    *,
    resource: str | None = None,
) -> str:
    """Build a JWS with a modified payload (signature will not match)."""
    issued_at = int(time.time())
    header = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "alg": "EdDSA",
                    "typ": "JWT",
                    "kid": kid,
                    "iat": issued_at,
                    "exp": issued_at + 60,
                    "aud": "court",
                    "resource": resource or _resource_for_payload(payload),
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    tampered = json.dumps({**payload, "_tampered": True}).encode()
    body = base64.urlsafe_b64encode(tampered).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(b"\x01" * 64).rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


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
    else:
        mock_client.get_task.side_effect = AssertionError(
            "Task Board fixture requires an explicit task projection"
        )
    if record_ruling_side_effect is not None:
        mock_client.record_ruling.side_effect = record_ruling_side_effect
    else:
        mock_client.record_ruling.side_effect = AssertionError(
            "Task Board fixture requires an explicit ruling acknowledgment"
        )
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


def make_mock_platform_agent(
    agent_id: str = "a-platform-test-id",
    task_response: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock PlatformAgent with all required methods.

    validate_certificate is sync (MagicMock). By default it decodes
    the base64 body of a JWS compact token. Override via side_effect
    or return_value for specific test scenarios.

    get_task, split_escrow, record_ruling, submit_platform_feedback
    are async (AsyncMock).
    """
    mock = MagicMock()
    mock.agent_id = agent_id

    def _decode_jws_body(token: str) -> VerifiedJws:
        """Decode and verify a fake JWS token.

        Checks that the signature matches the known fake-signature constant
        used by make_jws_token. Tokens from make_tampered_jws have a different
        signature and will be rejected with InvalidSignature.
        """
        parts = token.split(".")
        sig_part = parts[2]
        sig_part += "=" * (4 - len(sig_part) % 4)
        sig_bytes = base64.urlsafe_b64decode(sig_part)
        if sig_bytes != bytes(64):
            raise InvalidSignature()
        header = parts[0]
        header += "=" * (-len(header) % 4)
        decoded_header = json.loads(base64.urlsafe_b64decode(header))
        if decoded_header["kid"] != agent_id:
            raise InvalidSignature()
        body = parts[1]
        body += "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body))
        action = payload.get("action")
        if not isinstance(action, str) or not action.strip():
            raise InvalidPayloadError("Invalid JWS payload action: expected a non-blank string")
        return VerifiedJws(
            header=JwsProtectedHeader(**decoded_header),
            payload=payload,
        )

    mock.validate_certificate = MagicMock(side_effect=_decode_jws_body)
    mock.close = AsyncMock()

    mock.get_task = AsyncMock()
    if task_response is None:
        mock.get_task.side_effect = AssertionError(
            "Platform fixture requires an explicit task projection"
        )
    else:
        mock.get_task.return_value = task_response
    mock.split_escrow = AsyncMock(return_value={"status": "ok"})
    mock.record_ruling = AsyncMock(
        side_effect=AssertionError("Platform fixture requires an explicit ruling acknowledgment")
    )
    mock.submit_platform_feedback = AsyncMock(return_value={"status": "ok"})
    mock.record_feedback = mock.submit_platform_feedback

    return mock


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
        "lifecycle_version": 3,
    }
