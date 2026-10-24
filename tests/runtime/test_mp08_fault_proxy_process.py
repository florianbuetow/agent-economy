"""Real HTTP and owned-process control for the MP-08 fault proxy fixture."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from scripts.isolated_test_runtime import (
    _drain_owned_process_group,
    _release_owned_launch,
    _start_owned_launch,
)

pytestmark = pytest.mark.integration


def test_real_proxy_withholds_complete_upstream_response_and_drains(tmp_path: Path) -> None:
    body = b"{}"
    target = "/board/task-operations/op-process-control/finalize"
    result = b'{"operation_id":"op-process-control","operation_state":"completed"}'
    observed: list[tuple[str, bytes, str | None]] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received = self.rfile.read(int(self.headers["Content-Length"]))
            observed.append((self.path, received, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    with contextlib.ExitStack() as ownership:
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        ownership.callback(upstream.server_close)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        ownership.callback(thread.join, 5)
        ownership.callback(upstream.shutdown)
        listener = ownership.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        proxy_port = listener.getsockname()[1]
        gate = tmp_path / "committed.json"
        release = tmp_path / "release"
        config = tmp_path / "proxy.json"
        config.write_text(
            json.dumps(
                {
                    "upstream_url": f"http://127.0.0.1:{upstream.server_port}",
                    "timeout_seconds": 5,
                    "release_poll_seconds": 0.01,
                    "release_path": str(release),
                    "gate": {
                        "method": "POST",
                        "path": target,
                        "request_sha256": hashlib.sha256(body).hexdigest(),
                        "occurrence": 1,
                        "phase": "after_response",
                        "expected_status": 200,
                        "expected_response": json.loads(result),
                        "artifact_path": str(gate),
                    },
                }
            )
        )
        root = Path(__file__).resolve().parents[2]
        log_path = tmp_path / "proxy.log"
        log = ownership.enter_context(log_path.open("wb"))
        child, pgid, sid, descriptor = _start_owned_launch(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                str(Path(__file__).with_name("mp08_financial_effect_fault_server.py")),
                "--config",
                str(config),
                "--fd",
                str(listener.fileno()),
            ],
            cwd=str(root / "services" / "db-gateway"),
            env=dict(os.environ),
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=(listener.fileno(),),
        )
        ownership.callback(_drain_owned_process_group, child, pgid=pgid, sid=sid)
        remaining_descriptor: list[int] = [descriptor]

        def close_launch_gate() -> None:
            if remaining_descriptor:
                os.close(remaining_descriptor.pop())

        ownership.callback(close_launch_gate)
        released_descriptor = remaining_descriptor.pop()
        _release_owned_launch(released_descriptor)
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=10) as downstream:
            downstream.sendall(
                f"POST {target} HTTP/1.1\r\nHost: 127.0.0.1:{proxy_port}\r\n"
                f"Authorization: Bearer process-control\r\nContent-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n".encode()
                + body
            )
            try:
                deadline = time.monotonic() + 8
                while not gate.exists():
                    assert child.poll() is None, log_path.read_text()
                    assert time.monotonic() < deadline, log_path.read_text()
                    time.sleep(0.01)
                artifact = json.loads(gate.read_text())
                assert type(artifact["pid"]) is int and artifact["pid"] > 0
                assert os.getpgid(artifact["pid"]) == pgid
                assert os.getsid(artifact["pid"]) == sid
                assert artifact["pgid"] == pgid
                assert artifact["sid"] == sid
                assert artifact["upstream_status"] == 200
                assert artifact["response_sha256"] == hashlib.sha256(result).hexdigest()
                assert observed == [(target, body, "Bearer process-control")]
                downstream.settimeout(0.1)
                with pytest.raises(TimeoutError):
                    escaped = downstream.recv(1)
                    pytest.fail(
                        f"A response byte or EOF escaped the committed-response gate: {escaped!r}"
                    )
            finally:
                release.touch()
            downstream.settimeout(5)
            received = bytearray()
            while chunk := downstream.recv(8192):
                received.extend(chunk)
            headers, response_body = bytes(received).split(b"\r\n\r\n", 1)
            assert headers.startswith(b"HTTP/1.1 200 ")
            assert response_body == result
    assert child.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)
    assert not thread.is_alive()
