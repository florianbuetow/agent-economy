"""Real-service telemetry acceptance coverage for OPS-04 and OPS-05."""

from __future__ import annotations

import asyncio
import base64
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import yaml
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from scripts.isolated_test_runtime import IsolatedRuntime
from service_auth import AgentFactory
from service_auth.financial_identity import operation_id_for

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_MARKER = "private-request-marker-never-export"


class _Collector(ThreadingHTTPServer):
    mode: str
    requests: list[tuple[str, bytes]]


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append((self.path, body))  # type: ignore[attr-defined]
        mode = self.server.mode  # type: ignore[attr-defined]
        if mode == "timeout":
            time.sleep(2)
        if mode == "disconnect":
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        self.send_response(404 if mode == "404" else 200)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _start_collector(mode: str) -> tuple[_Collector, threading.Thread]:
    server = _Collector(("127.0.0.1", 0), _Handler)
    server.mode = mode
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _enable_telemetry(runtime: IsolatedRuntime, endpoint: str) -> None:
    for service in runtime.services:
        path = runtime.config_paths[service]
        config = yaml.safe_load(path.read_text())
        config["telemetry"].update(
            {"enabled": True, "otlp_endpoint": endpoint, "sample_ratio": 1.0}
        )
        path.write_text(yaml.safe_dump(config, sort_keys=False))


def _register_and_read(runtime: IsolatedRuntime) -> str:
    agent_id: str
    key_material = uuid.uuid4().bytes + uuid.uuid4().bytes
    with httpx.Client(timeout=5.0) as client:
        response = client.post(
            f"{runtime.service_url('identity')}/agents/register",
            headers={"X-Private-Marker": PRIVATE_MARKER},
            json={
                "name": f"telemetry-agent-{uuid.uuid4()}",
                "public_key": "ed25519:" + base64.b64encode(key_material).decode(),
            },
        )
        assert response.status_code == 201, response.text
        agent_id = str(response.json()["agent_id"])
        stored = client.get(f"{runtime.service_url('identity')}/agents/{agent_id}")
        assert stored.status_code == 200
        assert stored.json()["agent_id"] == agent_id
    return agent_id


def _stop_for_flush(runtime: IsolatedRuntime) -> None:
    runtime.stop_service("identity")
    runtime.stop_service("db-gateway")


def _wait_for_export(server: _Collector) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if server.requests:
            return
        time.sleep(0.05)
    raise AssertionError("telemetry batch was not exported within eight seconds")


def _wait_for_trace_services(server: _Collector, expected: set[str]) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        observed = set().union(*_trace_services(server).values()) if server.requests else set()
        if expected <= observed:
            return
        time.sleep(0.05)
    raise AssertionError(
        f"telemetry services were not exported within eight seconds: {_trace_services(server)}"
    )


def _trace_services(server: _Collector) -> dict[int, set[str]]:
    traces: dict[int, set[str]] = {}
    for path, raw in server.requests:
        if path != "/v1/traces":
            continue
        request = ExportTraceServiceRequest.FromString(raw)
        for resource_spans in request.resource_spans:
            resource = {
                attribute.key: attribute.value.string_value
                for attribute in resource_spans.resource.attributes
            }
            service = resource.get("service.name", "")
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    traces.setdefault(int.from_bytes(span.trace_id), set()).add(service)
    return traces


def _wait_for_export_failure(runtime: IsolatedRuntime) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        combined = "\n".join(
            path.read_text(errors="replace")
            for service, path in runtime.log_paths.items()
            if service in {"db-gateway", "identity"} and path.exists()
        )
        if "Failed to export" in combined or "Exception while exporting" in combined:
            return
        time.sleep(0.05)
    raise AssertionError("OTLP exporter failure was not visible in service logs")


def _trace_attributes(server: _Collector) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path, raw in server.requests:
        if path != "/v1/traces":
            continue
        request = ExportTraceServiceRequest.FromString(raw)
        for resource_spans in request.resource_spans:
            resource = {
                attribute.key: attribute.value.string_value
                for attribute in resource_spans.resource.attributes
            }
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    attributes: dict[str, object] = {}
                    for attribute in span.attributes:
                        value = attribute.value
                        attributes[attribute.key] = (
                            value.string_value
                            or value.int_value
                            or value.bool_value
                            or value.double_value
                        )
                    records.append(
                        {
                            "trace_id": int.from_bytes(span.trace_id),
                            "service": resource.get("service.name", ""),
                            "name": span.name,
                            "attributes": attributes,
                        }
                    )
    return records


def _task_version(task: dict[str, object], status: str) -> int:
    assert task["status"] == status
    version = task["lifecycle_version"]
    assert type(version) is int
    return version


def _has_attribute(record: dict[str, object], key: str, value: str) -> bool:
    attributes = record["attributes"]
    return isinstance(attributes, dict) and attributes.get(key) == value


def _wait_for_dispute_correlation(
    server: _Collector, task_id: str, dispute_id: str
) -> list[dict[str, object]]:
    deadline = time.monotonic() + 8
    latest: tuple[set[int], dict[int, list[str]]] = (set(), {})
    while time.monotonic() < deadline:
        records = _trace_attributes(server)
        trace_services = _trace_services(server)
        task_records = [row for row in records if _has_attribute(row, "task_id", task_id)]
        dispute_records = [
            row for row in records if _has_attribute(row, "dispute_id", dispute_id)
        ]
        filing_trace_ids = {
            int(row["trace_id"])
            for row in task_records
            if _has_attribute(row, "action", "dispute_task")
        }
        ruling_trace_ids = {
            int(row["trace_id"])
            for row in dispute_records
            if _has_attribute(row, "action", "rule")
        }
        filing_ready = any(
            {"ui", "task-board", "court", "db-gateway"}
            <= trace_services.get(trace_id, set())
            for trace_id in filing_trace_ids
        )
        ruling_ready = any(
            {"court", "task-board", "central-bank", "reputation", "db-gateway"}
            <= trace_services.get(trace_id, set())
            for trace_id in ruling_trace_ids
        )
        callback_ready = any(
            int(row["trace_id"]) in ruling_trace_ids
            and _has_attribute(row, "task_id", task_id)
            and _has_attribute(row, "action", "record_ruling")
            for row in task_records
        )
        if filing_ready and ruling_ready and callback_ready:
            return records
        latest = (
            ruling_trace_ids,
            {
                trace_id: sorted(trace_services.get(trace_id, set()))
                for trace_id in ruling_trace_ids
            },
        )
        time.sleep(0.05)
    raise AssertionError(latest)


async def _dispute_workflow(  # noqa: PLR0915
    runtime: IsolatedRuntime,
) -> dict[str, object]:
    factory = AgentFactory(config_path=runtime.agent_config_path)
    poster = factory.user_agent("operator")
    worker = factory.create_agent("alice")
    private_claim = "private-claim-content-never-export"
    private_rebuttal = "private-rebuttal-content-never-export"
    private_file = b"private-upload-bytes-never-export"
    try:
        await poster.register()
        await worker.register()
        try:
            await worker.create_account()
        except httpx.HTTPStatusError as error:
            assert error.response.status_code == 409
        assert poster.agent_id is not None and worker.agent_id is not None
        posting = poster.prepare_task_posting(
            title="Telemetry dispute chain",
            spec="Submit result.txt containing the requested result.",
            reward=100,
            bidding_deadline_seconds=120,
            execution_deadline_seconds=120,
            review_deadline_seconds=120,
            acceptance_window_seconds=120,
        )
        publication = await poster.submit_task_posting(posting)
        assert publication.status_code == 201
        task = publication.body["result"]
        assert isinstance(task, dict)
        task_id = str(task["task_id"])
        open_version = _task_version(task, "open")
        bid = await worker.submit_bid(
            task_id=task_id,
            amount=60,
            operation_id=operation_id_for("telemetry-bid", (task_id, worker.agent_id)),
            expected_lifecycle_version=open_version,
        )
        assert bid.status_code == 201
        bid_id = str(bid.body["bid_id"])
        accepted = await poster.accept_bid(
            task_id=task_id,
            bid_id=bid_id,
            operation_id=operation_id_for("telemetry-accept", (task_id, bid_id)),
            expected_lifecycle_version=open_version,
        )
        accepted_version = _task_version(accepted.body, "accepted")
        asset = await worker.upload_asset(task_id, "result.txt", private_file)
        asset_id = str(asset["asset_id"])
        download_headers = worker._auth_header(
            {"action": "download_asset", "task_id": task_id, "asset_id": asset_id},
            audience="task-board",
            resource=f"GET /tasks/{task_id}/assets/{asset_id}",
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            downloaded = await client.get(
                f"{runtime.service_url('task-board')}/tasks/{task_id}/assets/{asset_id}",
                headers=download_headers,
            )
        assert downloaded.status_code == 200
        assert downloaded.content == private_file
        submitted = await worker.submit_deliverable(
            task_id,
            operation_id=operation_id_for("telemetry-submit", (task_id, worker.agent_id)),
            expected_lifecycle_version=accepted_version,
        )
        submitted_version = _task_version(submitted.body, "submitted")

        ui_url = runtime.service_url("ui")
        async with httpx.AsyncClient(timeout=10.0) as client:
            dispute = await client.post(
                f"{ui_url}/api/proxy/tasks/{task_id}/dispute",
                headers={"Origin": ui_url, "X-ATE-CSRF": "local-operator"},
                json={
                    "reason": private_claim,
                    "operation_id": f"op-{uuid.uuid4()}",
                    "expected_lifecycle_version": submitted_version,
                },
            )
            assert dispute.status_code in {200, 202}, dispute.text

        deadline = time.monotonic() + 20
        disputed: dict[str, object] | None = None
        while time.monotonic() < deadline:
            current = await poster.get_task(task_id)
            if current["status"] == "disputed":
                disputed = current
                break
            await asyncio.sleep(0.1)
        assert disputed is not None
        dispute_id = str(disputed["dispute_id"])
        await worker.submit_worker_rebuttal(task_id, dispute_id, private_rebuttal)

        deadline = time.monotonic() + 45
        ruled: dict[str, object] | None = None
        while time.monotonic() < deadline:
            current = await poster.get_task(task_id)
            if current["status"] == "ruled":
                ruled = current
                break
            await asyncio.sleep(0.2)
        assert ruled is not None
        assert ruled["worker_payment"] is not None
        assert ruled["poster_refund"] is not None
        return {
            "task_id": task_id,
            "dispute_id": dispute_id,
            "worker_pct": ruled["worker_pct"],
            "worker_payment": ruled["worker_payment"],
            "poster_refund": ruled["poster_refund"],
            "private_values": (
                private_claim.encode(),
                private_rebuttal.encode(),
                private_file,
                download_headers["Authorization"].encode(),
                (runtime.run_dir / "keys" / "alice.key").read_bytes(),
            ),
        }
    finally:
        await worker.close()
        await poster.close()


def test_identity_gateway_trace_propagates_without_private_request_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, thread = _start_collector("trace")
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway", "identity"),
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
    host, port = server.server_address
    _enable_telemetry(runtime, f"http://{host}:{port}")
    try:
        runtime.start()
        _register_and_read(runtime)
        _wait_for_trace_services(server, {"identity", "db-gateway"})
        _stop_for_flush(runtime)
        traces = _trace_services(server)
        assert any(
            {"identity", "db-gateway"} <= services for services in traces.values()
        ), traces
        assert PRIVATE_MARKER.encode() not in b"".join(body for _path, body in server.requests)
    finally:
        runtime.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_dispute_traces_correlate_authoritative_task_and_dispute_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, thread = _start_collector("trace")
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
    host, port = server.server_address
    _enable_telemetry(runtime, f"http://{host}:{port}")
    try:
        runtime.start()
        assert runtime.provision() == 0
        outcome = asyncio.run(_dispute_workflow(runtime))
        task_id = str(outcome["task_id"])
        dispute_id = str(outcome["dispute_id"])
        assert outcome["worker_pct"] == 50
        assert outcome["worker_payment"] == 30
        assert outcome["poster_refund"] == 70
        _wait_for_trace_services(
            server,
            {"ui", "task-board", "court", "central-bank", "reputation", "db-gateway"},
        )
        records = _wait_for_dispute_correlation(server, task_id, dispute_id)
        task_records = [row for row in records if _has_attribute(row, "task_id", task_id)]
        dispute_records = [
            row for row in records if _has_attribute(row, "dispute_id", dispute_id)
        ]
        assert task_records
        assert dispute_records
        exported = b"".join(body for _path, body in server.requests)
        private_values = outcome["private_values"]
        assert isinstance(private_values, tuple)
        if any(value in exported for value in private_values):
            raise AssertionError("OTLP payload contains private request, token, key, or file bytes")
    finally:
        runtime.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("mode", ["404", "timeout", "disconnect"])
def test_exporter_failure_keeps_real_registration_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    server, thread = _start_collector(mode)
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway", "identity"),
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
    host, port = server.server_address
    _enable_telemetry(runtime, f"http://{host}:{port}")
    try:
        runtime.start()
        agent_id = _register_and_read(runtime)
        _wait_for_export(server)
        _wait_for_export_failure(runtime)
        _stop_for_flush(runtime)
        with httpx.Client(timeout=2.0) as client:
            # Restarting persistence and Identity proves the committed result,
            # rather than trusting the pre-shutdown process's memory.
            runtime.start_service("db-gateway")
            runtime.start_service("identity")
            stored = client.get(f"{runtime.service_url('identity')}/agents/{agent_id}")
            assert stored.status_code == 200
            assert stored.json()["agent_id"] == agent_id
            second_agent_id = _register_and_read(runtime)
            assert second_agent_id != agent_id
            assert (
                client.get(f"{runtime.service_url('identity')}/agents/{second_agent_id}").status_code
                == 200
            )
        assert server.requests
    finally:
        runtime.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
