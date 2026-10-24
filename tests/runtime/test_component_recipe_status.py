"""Non-root component recipes remain automation-safe and truthful."""

from __future__ import annotations

import os
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_SERVICE_DIRS = tuple(
    sorted(
        path
        for path in (_ROOT / "services").iterdir()
        if path.is_dir() and path.joinpath("justfile").is_file()
    )
)
assert len(_SERVICE_DIRS) == 7
_COMPONENT_DIRS = (
    *_SERVICE_DIRS,
    _ROOT / "agents",
    _ROOT / "libs" / "service-auth",
    _ROOT / "libs" / "service-clients",
    _ROOT / "libs" / "service-commons",
    _ROOT / "tools",
)


def _run_just(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("TERM", None)
    return subprocess.run(
        ["just", *arguments],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize("component_dir", _COMPONENT_DIRS, ids=lambda path: path.name)
@pytest.mark.parametrize("arguments", [("help",), ()], ids=["help", "default"])
def test_help_and_default_work_without_term(
    component_dir: Path, arguments: tuple[str, ...]
) -> None:
    result = _run_just(component_dir, *arguments)

    assert result.returncode == 0, result.stderr
    assert "TERM environment variable not set" not in result.stderr


def test_service_justfiles_remain_identical() -> None:
    contents = {directory.joinpath("justfile").read_bytes() for directory in _SERVICE_DIRS}

    assert len(contents) == 1


@pytest.mark.parametrize("service_dir", _SERVICE_DIRS, ids=lambda path: path.name)
def test_service_does_not_expose_unsupported_performance_recipe(service_dir: Path) -> None:
    recipe_list = _run_just(service_dir, "--list")
    help_result = _run_just(service_dir, "help")

    assert recipe_list.returncode == 0, recipe_list.stderr
    assert help_result.returncode == 0, help_result.stderr
    assert "test-performance" not in recipe_list.stdout
    assert "test-performance" not in help_result.stdout


def test_service_check_does_not_require_system_python() -> None:
    justfile = (_SERVICE_DIRS[0] / "justfile").read_text(encoding="utf-8")

    assert 'check_tool "python"' not in justfile
    assert "python3" not in justfile


def test_service_status_uses_one_explicit_request_bound() -> None:
    justfile = (_SERVICE_DIRS[0] / "justfile").read_text(encoding="utf-8")

    assert "timeout_seconds=2" in justfile
    assert '--connect-timeout "$timeout_seconds"' in justfile
    assert '--max-time "$timeout_seconds"' in justfile


def test_generate_tasks_recipe_describes_replacement() -> None:
    justfile = (_ROOT / "tools" / "justfile").read_text(encoding="utf-8")

    assert "Replaces data/math_tasks.jsonl" in justfile
    assert "Tasks written to data/math_tasks.jsonl" in justfile
    assert "append" not in justfile.lower()


def test_tools_simulate_starts_without_artificial_run_id() -> None:
    result = _run_just(_ROOT / "tools", "--dry-run", "simulate", "scenarios/quick.yaml")

    assert result.returncode == 0, result.stderr
    command = result.stdout + result.stderr
    assert "python -m demo_replay" in command
    assert "--run-id" not in command


def test_tools_simulate_resume_passes_explicit_run_id() -> None:
    run_id = "run-00000000-0000-4000-8000-000000000591"
    result = _run_just(
        _ROOT / "tools",
        "--dry-run",
        "simulate-resume",
        "scenarios/quick.yaml",
        run_id,
    )

    assert result.returncode == 0, result.stderr
    command = result.stdout + result.stderr
    assert "python -m demo_replay" in command
    assert "--run-id" in command
    assert run_id in command


class _HealthHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_body = b'{"status":"ok","uptime_seconds":1}'

    def do_GET(self) -> None:
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def _health_server(status: int, body: bytes) -> Iterator[int]:
    handler = type(
        "ConfiguredHealthHandler",
        (_HealthHandler,),
        {"response_status": status, "response_body": body},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _status_result(tmp_path: Path, port: int) -> subprocess.CompletedProcess[str]:
    tmp_path.joinpath("justfile").write_bytes((_SERVICE_DIRS[0] / "justfile").read_bytes())
    tmp_path.joinpath("config.yaml").write_text(
        f"server:\n  host: 127.0.0.1\n  port: {port}\n", encoding="utf-8"
    )
    return _run_just(tmp_path, "status")


@pytest.mark.parametrize(
    ("http_status", "body", "expected_returncode", "expected_text"),
    [
        (200, b'{"status":"ok","uptime_seconds":1}', 0, "- ok"),
        (503, b'{"status":"ok"}', 1, "HTTP 503"),
        (200, b"not-json", 1, "malformed health response"),
        (200, b'{"status":"degraded"}', 1, "degraded"),
    ],
)
def test_service_status_reflects_health_response(
    tmp_path: Path,
    http_status: int,
    body: bytes,
    expected_returncode: int,
    expected_text: str,
) -> None:
    with _health_server(http_status, body) as port:
        result = _status_result(tmp_path, port)

    assert result.returncode == expected_returncode
    assert expected_text in result.stdout


def test_service_status_fails_when_unreachable(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = server.server_port
    server.server_close()

    result = _status_result(tmp_path, port)

    assert result.returncode == 1
    assert "not responding" in result.stdout
