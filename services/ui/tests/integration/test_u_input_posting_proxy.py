"""Protocol-2 posting with input attachments through the operator proxy (lane U).

prepare -> admit -> upload each input -> publish, each signed as the operator. A
network-free fake UserAgent records the signed upstream calls and returns scripted
Task Board responses.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.config import AgentConfig
from service_auth.financial_identity import operation_id_for
from service_auth.jws import TokenPolicy

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration

AGENT_ID = "a-operator-inputs"
TERMS = {
    "title": "Sum the inputs",
    "spec": "Return the sum of the integers in input.txt.",
    "reward": 100,
    "bidding_deadline_seconds": 3600,
    "acceptance_window_seconds": 3600,
    "execution_deadline_seconds": 86400,
    "review_deadline_seconds": 86400,
}


class _FakeOperator:
    def __init__(self) -> None:
        self.agent_id = AGENT_ID
        private_key = Ed25519PrivateKey.generate()
        self.config = AgentConfig(
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
        self.signed: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.responses: list[httpx.Response] = []

    def _sign_jws(self, payload: dict[str, object], *, audience: str, resource: str) -> str:
        self.signed.append({"payload": payload, "audience": audience, "resource": resource})
        return f"jws-{len(self.signed)}"

    def _auth_header(
        self, payload: dict[str, object], *, audience: str, resource: str
    ) -> dict[str, str]:
        token = self._sign_jws(payload, audience=audience, resource=resource)
        return {"Authorization": f"Bearer {token}"}

    async def _request_raw(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        response.request = httpx.Request(method, url)
        return response

    async def close(self) -> None:
        return None


@pytest.fixture
def operator(client: Any) -> _FakeOperator:
    _ = client
    fake = _FakeOperator()
    get_app_state().user_agent = fake  # type: ignore[assignment]
    return fake


def _envelope(record: dict[str, Any], state: str) -> dict[str, Any]:
    intent = record["intent"]
    return {
        "protocol_version": 2,
        "publication_operation_id": intent["publication_operation_id"],
        "task_id": intent["task_id"],
        "actor_id": AGENT_ID,
        "publication_state": state,
        "request_sha256": record["request_sha256"],
        "input_manifest_id": "manifest-input-1",
        "result": {"task_id": intent["task_id"], "status": "open"}
        if state == "published"
        else None,
    }


async def _prepare(client: Any, count: int) -> dict[str, Any]:
    response = await client.post(
        "/api/proxy/task-publications/prepare", json={**TERMS, "expected_input_count": count}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_prepare_admit_upload_publish_succeeds(client: Any, operator: _FakeOperator) -> None:
    record = await _prepare(client, 1)
    intent = record["intent"]
    publication_id = intent["publication_operation_id"]
    assert record["record_version"] == 2
    assert intent["lock_operation_id"] == operation_id_for(
        "task-lock", (intent["task_id"], AGENT_ID)
    )
    rendered = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert record["request_sha256"] == hashlib.sha256(rendered.encode()).hexdigest()

    operator.responses.append(httpx.Response(202, json=_envelope(record, "awaiting_inputs")))
    admitted = await client.post(
        f"/api/proxy/task-publications/{publication_id}/admit", json=record
    )
    assert admitted.status_code == 202, admitted.text
    assert admitted.json()["publication_state"] == "awaiting_inputs"
    assert operator.signed[-1]["resource"] == "POST /task-publications"
    assert operator.signed[-1]["payload"]["expected_input_count"] == 1
    assert operator.requests[-1]["url"] == "https://board.test/task-publications"

    uploaded_body = {
        "artifact_id": "art-1",
        "filename": "input.txt",
        "size_bytes": 7,
        "content_hash": "c" * 64,
        "content_type": "text/plain; charset=utf-8",
        "role": "input",
    }
    operator.responses.append(httpx.Response(201, json=uploaded_body))
    uploaded = await client.post(
        f"/api/proxy/task-publications/{publication_id}/inputs",
        files={"file": ("input.txt", b"3 7 11\n", "text/plain")},
        headers={"Idempotency-Key": "input-key-1"},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json() == uploaded_body
    upload_call = operator.requests[-1]
    assert upload_call["url"] == f"https://board.test/task-publications/{publication_id}/inputs"
    assert upload_call["headers"]["Idempotency-Key"] == "input-key-1"
    assert upload_call["headers"]["Content-Type"].startswith("multipart/form-data")
    assert operator.signed[-1]["payload"] == {
        "action": "task_publication_upload_input",
        "publication_operation_id": publication_id,
        "poster_id": AGENT_ID,
    }

    operator.responses.append(httpx.Response(201, json=_envelope(record, "published")))
    published = await client.post(
        f"/api/proxy/task-publications/{publication_id}/publish", json=record
    )
    assert published.status_code == 201, published.text
    assert published.json()["publication_state"] == "published"
    escrow = operator.signed[-1]
    assert escrow["audience"] == "central-bank"
    assert escrow["payload"] == {
        "action": "escrow_lock",
        "operation_id": intent["lock_operation_id"],
        "task_id": intent["task_id"],
        "agent_id": AGENT_ID,
        "amount": 100,
    }
    assert operator.signed[-2]["resource"] == f"POST /task-publications/{publication_id}/publish"


async def test_input_upload_requires_multipart(client: Any, operator: _FakeOperator) -> None:
    record = await _prepare(client, 1)
    publication_id = record["intent"]["publication_operation_id"]
    response = await client.post(
        f"/api/proxy/task-publications/{publication_id}/inputs",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 415, response.text
    assert response.json()["error"] == "unsupported_media_type"
    assert operator.requests == []


async def test_publish_count_mismatch_surfaces_409(client: Any, operator: _FakeOperator) -> None:
    record = await _prepare(client, 2)
    publication_id = record["intent"]["publication_operation_id"]
    operator.responses.append(
        httpx.Response(
            409,
            json={
                "error": "input_count_mismatch",
                "message": "Input manifest count does not match",
                "details": {},
            },
        )
    )
    response = await client.post(
        f"/api/proxy/task-publications/{publication_id}/publish", json=record
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "input_count_mismatch"


async def test_tampered_record_is_rejected_before_transport(
    client: Any, operator: _FakeOperator
) -> None:
    record = await _prepare(client, 1)
    publication_id = record["intent"]["publication_operation_id"]
    record["intent"]["reward"] = 1
    response = await client.post(
        f"/api/proxy/task-publications/{publication_id}/admit", json=record
    )
    assert response.status_code == 422, response.text
    assert operator.requests == []
