"""SDK parity with the shipped service APIs: signed asset reads, idempotent uploads,
protocol-2 posting, quality reads and typed errors."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth.agent import BaseAgent
from service_auth.config import AgentConfig
from service_auth.errors import EconomyApiError
from service_auth.financial_identity import operation_id_for
from service_auth.jws import TokenPolicy, parse_jws

pytestmark = pytest.mark.unit


def _config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    return AgentConfig(
        name="parity",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url="http://identity.test",
        bank_url="http://bank.test",
        task_board_url="http://board.test",
        reputation_url="http://reputation.test",
        court_url="http://court.test",
        token_ttl_seconds=60,
        token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
    )


class _Recorder:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = responses

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses.pop(0)


def _agent(recorder: _Recorder) -> BaseAgent:
    agent = BaseAgent(_config())
    agent.agent_id = "a-poster"
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handler))
    return agent


def _bearer_payload(request: httpx.Request) -> dict[str, Any]:
    token = request.headers["Authorization"].removeprefix("Bearer ")
    return parse_jws(token).payload


async def test_list_and_download_assets_are_signed_reads() -> None:
    recorder = _Recorder(
        [
            httpx.Response(200, json={"assets": [{"asset_id": "asset-1"}]}),
            httpx.Response(200, content=b"bytes"),
        ]
    )
    agent = _agent(recorder)
    assert await agent.list_assets("t-1") == [{"asset_id": "asset-1"}]
    assert await agent.download_asset("t-1", "asset-1") == b"bytes"
    listing, download = recorder.requests
    assert str(listing.url) == "http://board.test/tasks/t-1/assets"
    assert _bearer_payload(listing)["action"] == "list_assets"
    assert _bearer_payload(listing)["task_id"] == "t-1"
    assert str(download.url) == "http://board.test/tasks/t-1/assets/asset-1"
    download_payload = _bearer_payload(download)
    assert download_payload["action"] == "download_asset"
    assert download_payload["asset_id"] == "asset-1"
    await agent.close()


async def test_upload_asset_forwards_the_idempotency_key() -> None:
    recorder = _Recorder([httpx.Response(201, json={"asset_id": "asset-1"})])
    agent = _agent(recorder)
    await agent.upload_asset("t-1", "out.txt", b"x", idempotency_key="retry-1")
    assert recorder.requests[0].headers["Idempotency-Key"] == "retry-1"
    await agent.close()


async def test_protocol_two_posting_signs_admit_upload_and_publish() -> None:
    recorder = _Recorder(
        [
            httpx.Response(202, json={"input_manifest_id": "manifest-input-x"}),
            httpx.Response(201, json={"asset_id": "asset-in-1", "role": "input"}),
            httpx.Response(201, json={"task_id": "t-9", "status": "open"}),
        ]
    )
    agent = _agent(recorder)
    intent = agent.new_task_publication(
        task_id="t-9",
        title="Label these",
        spec="Classify",
        reward=50,
        bidding_deadline_seconds=60,
        acceptance_window_seconds=60,
        execution_deadline_seconds=600,
        review_deadline_seconds=600,
        expected_input_count=1,
    )
    assert intent["lock_operation_id"] == operation_id_for("task-lock", ("t-9", "a-poster"))

    await agent.admit_task_publication(intent)
    await agent.upload_task_input(
        intent["publication_operation_id"],
        "input.csv",
        b"a,b",
        content_type="text/csv",
        idempotency_key="in-1",
    )
    status, body = await agent.publish_task_publication(intent)
    assert (status, body["task_id"]) == (201, "t-9")

    admit, upload, publish = recorder.requests
    admit_payload = parse_jws(json.loads(admit.content)["token"]).payload
    assert admit_payload["action"] == "task_publication_admit"
    assert admit_payload["expected_input_count"] == 1
    assert str(admit.url) == "http://board.test/task-publications"
    assert upload.headers["Idempotency-Key"] == "in-1"
    assert _bearer_payload(upload)["action"] == "task_publication_upload_input"
    publish_body = json.loads(publish.content)
    assert parse_jws(publish_body["task_token"]).payload["action"] == "task_publication_publish"
    escrow = parse_jws(publish_body["escrow_token"]).payload
    assert escrow["operation_id"] == intent["lock_operation_id"]
    assert escrow["amount"] == 50
    await agent.close()


async def test_quality_reads_public_and_signed() -> None:
    report = {"agent_id": "a-worker", "formula_version": "quality-v1"}
    recorder = _Recorder([httpx.Response(200, json=report), httpx.Response(200, json=report)])
    agent = _agent(recorder)
    assert await agent.get_agent_quality("a-worker") == report
    assert await agent.get_agent_quality_report("a-worker") == report
    public, signed = recorder.requests
    assert "Authorization" not in public.headers
    assert _bearer_payload(signed)["action"] == "get_quality"
    await agent.close()


async def test_structured_errors_surface_code_and_message() -> None:
    recorder = _Recorder(
        [
            httpx.Response(
                409,
                json={"error": "invalid_status", "message": "Task is not open", "details": {}},
            )
        ]
    )
    agent = _agent(recorder)
    with pytest.raises(EconomyApiError) as info:
        await agent.get_task("t-1")
    assert (info.value.status_code, info.value.error) == (409, "invalid_status")
    assert isinstance(info.value, httpx.HTTPStatusError)
    await agent.close()
