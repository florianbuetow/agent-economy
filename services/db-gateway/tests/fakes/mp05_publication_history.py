"""Real Gateway histories for the MP-05 native publication contract."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.financial_identity import operation_id_for

from db_gateway_service.financial_requests import canonical_request
from tests.fakes.native_board_history import create_account, register_agent

if TYPE_CHECKING:
    import httpx
    from fastapi.testclient import TestClient


@dataclass(frozen=True, slots=True)
class PublicationIntent:
    """One immutable protocol-v1 task publication intent."""

    operation_id: str
    task_id: str
    poster_id: str
    title: str
    spec: str
    reward: int
    bidding_deadline_seconds: int
    acceptance_window_seconds: int
    execution_deadline_seconds: int
    review_deadline_seconds: int

    @property
    def lock_operation_id(self) -> str:
        """Return the deterministic Bank lock identity."""
        return operation_id_for("task-lock", (self.task_id, self.poster_id))

    def body(self) -> dict[str, object]:
        """Return the exact wire object accepted by native admission."""
        return {
            "protocol_version": 1,
            "operation_id": self.operation_id,
            "task_id": self.task_id,
            "actor_kind": "agent",
            "actor_id": self.poster_id,
            "lock_operation_id": self.lock_operation_id,
            "title": self.title,
            "spec": self.spec,
            "reward": self.reward,
            "bidding_deadline_seconds": self.bidding_deadline_seconds,
            "acceptance_window_seconds": self.acceptance_window_seconds,
            "execution_deadline_seconds": self.execution_deadline_seconds,
            "review_deadline_seconds": self.review_deadline_seconds,
        }

    def canonical_request(self) -> str:
        """Return the sorted compact semantic request without operation identity."""
        fields = self.body()
        del fields["operation_id"]
        return json.dumps(
            fields,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def request_sha256(self) -> str:
        """Hash the complete immutable request except its replay identity."""
        return hashlib.sha256(self.canonical_request().encode("utf-8")).hexdigest()


def create_funded_poster(
    client: TestClient,
    *,
    agent_id: str | None = None,
    balance: int = 1_000,
) -> str:
    """Create a real Identity row and receipt-backed Bank account."""
    public_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    poster_id = register_agent(
        client,
        agent_id=agent_id,
        name="MP-05 publication poster",
        public_key="ed25519:" + base64.b64encode(public_key).decode("ascii"),
    )
    create_account(client, poster_id, balance=balance)
    return poster_id


def publication_intent(
    poster_id: str,
    *,
    operation_id: str | None = None,
    task_id: str | None = None,
    title: str = "Durable native publication",
    spec: str = "Publish exactly once from a durable caller-owned intent.",
    reward: int = 100,
) -> PublicationIntent:
    """Build one valid intent with independently allocated public identities."""
    return PublicationIntent(
        operation_id=operation_id or f"op-{uuid4()}",
        task_id=task_id or f"t-{uuid4()}",
        poster_id=poster_id,
        title=title,
        spec=spec,
        reward=reward,
        bidding_deadline_seconds=3_600,
        acceptance_window_seconds=1_800,
        execution_deadline_seconds=7_200,
        review_deadline_seconds=900,
    )


def lock_intent(
    client: TestClient,
    intent: PublicationIntent,
    *,
    payer_id: str | None = None,
    task_id: str | None = None,
    amount: int | None = None,
    operation_id: str | None = None,
    escrow_id: str | None = None,
) -> tuple[str, httpx.Response]:
    """Persist a genuine version-1 Bank lock receipt through its signed route."""
    resolved_payer = payer_id or intent.poster_id
    resolved_task = task_id or intent.task_id
    resolved_amount = amount if amount is not None else intent.reward
    resolved_operation = operation_id or intent.lock_operation_id
    resolved_escrow = escrow_id or f"esc-{uuid4()}"
    effect_time = _utc_now_text()
    response = client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": resolved_operation,
            "escrow_id": resolved_escrow,
            "payer_account_id": resolved_payer,
            "amount": resolved_amount,
            "task_id": resolved_task,
            "created_at": effect_time,
            "tx_id": f"tx-{uuid4()}",
            "event": _event(
                event_type="escrow.locked",
                timestamp=effect_time,
                task_id=resolved_task,
                agent_id=resolved_payer,
            ),
        },
    )
    assert response.status_code == 201, response.text
    receipt = response.json()["receipt"]
    expected = canonical_request(
        "escrow_lock",
        {
            "payer_account_id": resolved_payer,
            "task_id": resolved_task,
            "amount": resolved_amount,
        },
    )
    assert receipt["operation_id"] == resolved_operation
    assert receipt["request_sha256"] == expected.request_sha256
    assert receipt["committed_at"] == effect_time
    assert response.json()["current_escrow"]["status"] == "locked"
    assert response.json()["current_escrow"]["created_at"] == effect_time
    return resolved_escrow, response


def release_escrow(
    client: TestClient,
    *,
    escrow_id: str,
    recipient_id: str,
    task_id: str,
) -> httpx.Response:
    """Resolve one real lock through the receipt-backed Bank release route."""
    effect_time = _utc_now_text()
    response = client.post(
        "/bank/escrow/release",
        json={
            "operation_id": f"op-{uuid4()}",
            "escrow_id": escrow_id,
            "recipient_account_id": recipient_id,
            "source_reference": f"task:{task_id}:historical_release",
            "tx_id": f"tx-{uuid4()}",
            "resolved_at": effect_time,
            "event": _event(
                event_type="escrow.released",
                timestamp=effect_time,
                task_id=task_id,
                agent_id=recipient_id,
            ),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["receipt"]["committed_at"] == effect_time
    assert response.json()["current_escrow"]["status"] == "released"
    assert response.json()["current_escrow"]["resolved_at"] == effect_time
    return response


def _utc_now_text() -> str:
    """Sample one explicit UTC timestamp for every row in one effect."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _event(
    *,
    event_type: str,
    timestamp: str,
    task_id: str,
    agent_id: str,
) -> dict[str, object]:
    """Build an exact Central Bank event with the effect's sampled timestamp."""
    return {
        "event_source": "central-bank",
        "event_type": event_type,
        "timestamp": timestamp,
        "task_id": task_id,
        "agent_id": agent_id,
        "summary": "MP-05 publication financial effect",
        "payload": "{}",
    }


def assert_uniform_error(response: httpx.Response, status: int, error: str) -> dict[str, Any]:
    """Require the common exact error object shape and return its payload."""
    assert response.status_code == status, response.text
    payload = response.json()
    assert set(payload) == {"error", "message", "details"}
    assert payload["error"] == error
    assert isinstance(payload["message"], str) and payload["message"]
    assert isinstance(payload["details"], dict)
    return payload
