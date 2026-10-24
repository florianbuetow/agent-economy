"""Acceptance controls for the MP-08 test-only response-loss proxy."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from tests.runtime.mp08_financial_effect_fault_server import (
    FaultGate,
    canonical_headers_sha256,
    create_proxy,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BODY = b'{"signed":"unaltered bytes"}'
PATH = "/board/task-operations/op-test/finalize"
RESPONSE = {"operation_id": "op-test", "operation_state": "completed"}


def _gate(tmp_path: Path, phase: str, occurrence: int) -> FaultGate:
    return FaultGate(
        method="POST",
        path=PATH,
        request_sha256=hashlib.sha256(BODY).hexdigest(),
        occurrence=occurrence,
        phase=phase,
        expected_status=200,
        expected_response=RESPONSE,
        artifact_path=tmp_path / "gate.json",
    )


async def test_response_gate_forwards_signed_bytes_and_withholds_ack(
    tmp_path: Path,
) -> None:
    recorded: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=RESPONSE)

    gate = _gate(tmp_path, "after_response", 1)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream)
    ) as upstream_client:
        app = create_proxy(upstream_client, "http://gateway.test", gate)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        ) as client:
            request = asyncio.create_task(
                client.post(
                    PATH,
                    content=BODY,
                    headers={"Authorization": "Bearer test-signature"},
                )
            )
            try:
                async with asyncio.timeout(2):
                    await gate.reached.wait()
                assert not request.done(), "A committed response escaped the crash gate"
                assert len(recorded) == 1
                assert recorded[0].content == BODY
                assert recorded[0].url.raw_path == PATH.encode()
                assert recorded[0].headers["authorization"] == "Bearer test-signature"
                artifact = json.loads(gate.artifact_path.read_text())
                assert artifact["request_sha256"] == hashlib.sha256(BODY).hexdigest()
                assert artifact["request_headers_sha256"] == canonical_headers_sha256(
                    recorded[0].headers.raw
                )
                assert artifact["upstream_status"] == 200
                expected_response = httpx.Response(200, json=RESPONSE)
                assert artifact["response_headers_sha256"] == canonical_headers_sha256(
                    expected_response.headers.raw
                )
                assert (
                    artifact["response_sha256"]
                    == hashlib.sha256(expected_response.content).hexdigest()
                )
                gate.release.set()
                response = await request
                assert response.status_code == 200
                assert response.json() == RESPONSE
            finally:
                gate.release.set()
                if not request.done():
                    request.cancel()
                await asyncio.gather(request, return_exceptions=True)


async def test_before_forward_gate_prevents_upstream_effect(tmp_path: Path) -> None:
    calls: list[bytes] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        return httpx.Response(200, json=RESPONSE)

    gate = _gate(tmp_path, "before_forward", 1)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream)
    ) as upstream_client:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_proxy(upstream_client, "http://gateway.test", gate)
            ),
            base_url="http://proxy.test",
        ) as client:
            request = asyncio.create_task(client.post(PATH, content=BODY))
            try:
                async with asyncio.timeout(2):
                    await gate.reached.wait()
                assert calls == []
                assert not request.done()
                artifact = json.loads(gate.artifact_path.read_text())
                assert type(artifact["request_headers_sha256"]) is str
                assert artifact["response_headers_sha256"] is None
                gate.release.set()
                assert (await request).status_code == 200
                assert calls == [BODY]
            finally:
                gate.release.set()
                if not request.done():
                    request.cancel()
                await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("mismatch", ["method", "path", "body", "occurrence"])
async def test_nonmatching_request_never_fires_gate(
    tmp_path: Path, mismatch: str
) -> None:
    gate = _gate(tmp_path, "after_response", 2 if mismatch == "occurrence" else 1)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=RESPONSE)
        )
    ) as upstream_client:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_proxy(upstream_client, "http://gateway.test", gate)
            ),
            base_url="http://proxy.test",
        ) as client:
            response = await client.request(
                "GET" if mismatch == "method" else "POST",
                "/unrelated" if mismatch == "path" else PATH,
                content=b"changed" if mismatch == "body" else BODY,
            )
            assert response.status_code == 200
            assert not gate.reached.is_set()
            assert not gate.artifact_path.exists()


@pytest.mark.parametrize(
    ("status", "response"),
    [
        (202, RESPONSE),
        (200, {"operation_id": "other", "operation_state": "completed"}),
        (200, {"operation_id": "op-test", "operation_state": "claimed"}),
    ],
)
async def test_unconfirmed_response_cannot_publish_commit_gate(
    tmp_path: Path, status: int, response: dict[str, str]
) -> None:
    gate = _gate(tmp_path, "after_response", 1)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json=response)
        )
    ) as upstream_client:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_proxy(upstream_client, "http://gateway.test", gate)
            ),
            base_url="http://proxy.test",
        ) as client:
            with pytest.raises(AssertionError, match="Unconfirmed upstream response"):
                await client.post(PATH, content=BODY)
    assert not gate.reached.is_set()
    assert not gate.artifact_path.exists()
