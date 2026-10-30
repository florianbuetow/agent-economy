"""Root operational recipes report unhealthy and failed startup states."""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import threading
from contextlib import ExitStack, contextmanager
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_SERVICE_RECIPES = {
    "db-gateway": "_launch-db-gateway",
    "identity": "_launch-identity",
    "central-bank": "_launch-bank",
    "task-board": "_launch-taskboard",
    "reputation": "_launch-reputation",
    "court": "_launch-court",
    "ui": "_launch-ui",
}
_PORT_VARIABLES = {
    "identity": "port_identity",
    "central-bank": "port_central_bank",
    "task-board": "port_task_board",
    "reputation": "port_reputation",
    "court": "port_court",
    "db-gateway": "port_db_gateway",
    "ui": "port_ui",
}
_ORIGINAL_PORTS = {
    "identity": 8001,
    "central-bank": 8002,
    "task-board": 8003,
    "reputation": 8004,
    "court": 8005,
    "db-gateway": 8007,
    "ui": 8008,
}


def _unused_ports(count: int) -> list[int]:
    sockets = [socket.socket(socket.AF_INET, socket.SOCK_STREAM) for _ in range(count)]
    try:
        for listener in sockets:
            listener.bind(("127.0.0.1", 0))
        return [int(listener.getsockname()[1]) for listener in sockets]
    finally:
        for listener in sockets:
            listener.close()


def _copy_justfile(tmp_path: Path, ports: dict[str, int]) -> Path:
    source = (_ROOT / "justfile").read_text(encoding="utf-8")
    for service, variable in _PORT_VARIABLES.items():
        original = f'{variable} := "{_ORIGINAL_PORTS[service]}"'
        replacement = f'{variable} := "{ports[service]}"'
        assert source.count(original) == 1
        source = source.replace(original, replacement)
        assert source.count(replacement) == 1
    assert source.count("local max_attempts=60") == 1
    source = source.replace("local max_attempts=60", "local max_attempts=100")
    assert source.count("local max_attempts=100") == 1
    assert source.count("sleep 0.3") == 1
    source = source.replace("sleep 0.3", "sleep 0.02")
    assert source.count("sleep 0.02") == 1
    justfile = tmp_path / "justfile"
    justfile.write_text(source, encoding="utf-8")
    return justfile


def _run_just(justfile: Path, recipe: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("just", "--justfile", str(justfile), recipe),
        cwd=justfile.parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


@contextmanager
def _running_health_server(payload: bytes, http_status: int) -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            response = b'{"agents":[]}' if self.path == "/agents" else payload
            self.send_response(http_status)
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        assert not thread.is_alive()


def test_help_succeeds_without_term() -> None:
    env = os.environ.copy()
    env.pop("TERM", None)

    result = subprocess.run(
        ("just", "help"),
        cwd=_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Agent Task Economy" in result.stdout


def test_logs_uses_uv_project_python() -> None:
    result = subprocess.run(
        ("just", "--dry-run", "logs"),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    command = result.stdout + result.stderr
    assert "uv run --project services/db-gateway python scripts/tail_logs.py" in command
    assert "python3" not in command


@pytest.mark.parametrize("recipe", ["init-all", "destroy-all"])
def test_all_environment_recipes_include_shared_libraries(recipe: str) -> None:
    result = subprocess.run(
        ("just", "--dry-run", recipe),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    command = result.stdout + result.stderr
    for library in ("service-commons", "service-clients", "service-auth"):
        assert f"cd libs/{library} && just" in command


@pytest.mark.parametrize(
    ("payload", "http_status", "expected_detail"),
    [
        ("not-json", 200, "malformed"),
        ('{"status":"degraded"}', 200, "degraded"),
        ('{"status":"error"}', 503, "not responding"),
    ],
)
def test_status_reports_every_service_and_fails_for_unhealthy_response(
    tmp_path: Path,
    payload: str,
    http_status: int,
    expected_detail: str,
) -> None:
    with (
        _running_health_server(b'{"status":"ok"}', 200) as healthy_port,
        _running_health_server(payload.encode(), http_status) as unhealthy_port,
    ):
        ports = {service: healthy_port for service in _SERVICE_RECIPES}
        ports["ui"] = unhealthy_port
        justfile = _copy_justfile(tmp_path, ports)
        result = _run_just(justfile, "status")

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    for display_name in (
        "Identity",
        "Central Bank",
        "Task Board",
        "Reputation",
        "Court",
        "DB Gateway",
        "UI",
    ):
        assert display_name in output
    assert "UI" in output and expected_detail in output


def test_start_all_key_failure_prevents_every_service_launch(tmp_path: Path) -> None:
    ports = dict(zip(_SERVICE_RECIPES, _unused_ports(7), strict=True))
    justfile = _copy_justfile(tmp_path, ports)
    marker = tmp_path / "starts.log"
    marker_arg = shlex.quote(str(marker))
    overrides = [
        "",
        "set allow-duplicate-recipes",
        "",
        "check-keys:",
        "    @false",
    ]
    for service, recipe in _SERVICE_RECIPES.items():
        overrides.extend(
            ("", f"{recipe}:", f"    @printf '%s\\n' {service} >> {marker_arg}")
        )
    with justfile.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(overrides) + "\n")

    result = _run_just(justfile, "start-all")

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert not marker.exists()
    assert "Key preflight failed before service startup" in output
    assert "just provision-keys" in output
    assert "Starting tier 1" not in output


@pytest.mark.parametrize("failed_service", ["db-gateway", "central-bank", "ui"])
def test_start_all_stops_after_failed_health_gate(
    tmp_path: Path,
    failed_service: str,
) -> None:
    port_values = _unused_ports(len(_SERVICE_RECIPES))
    ports = dict(zip(_SERVICE_RECIPES, port_values, strict=True))
    marker = tmp_path / "starts.log"
    marker_arg = shlex.quote(str(marker))
    with ExitStack() as stack:
        for service in _SERVICE_RECIPES:
            if service != failed_service:
                ports[service] = stack.enter_context(
                    _running_health_server(b'{"status":"ok"}', 200)
                )
        justfile = _copy_justfile(tmp_path, ports)
        overrides = ["", "set allow-duplicate-recipes", "", "check-keys:", "    @:"]
        for service, recipe in _SERVICE_RECIPES.items():
            overrides.extend(
                ("", f"{recipe}:", f"    @printf '%s\\n' {service} >> {marker_arg}")
            )
            overrides.append("    @false")
        with justfile.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(overrides) + "\n")
        result = _run_just(justfile, "start-all")

    output = result.stdout + result.stderr
    started = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
    assert result.returncode != 0, output
    assert failed_service in started
    if failed_service == "db-gateway":
        assert "identity" not in started
    if failed_service == "central-bank":
        assert "ui" not in started
    assert "All services started" not in output


def test_stop_all_reports_failure_after_attempting_every_stop(tmp_path: Path) -> None:
    source = (_ROOT / "justfile").read_text(encoding="utf-8")
    stop_all = source[source.index("stop-all:") : source.index("# Start task feeder")]
    for forbidden in (
        "pkill",
        "lsof",
        "uvicorn",
        "_launch-",
        "stop-feeder",
        "stop-mathbot",
    ):
        assert forbidden not in stop_all

    justfile = _copy_justfile(
        tmp_path, dict(zip(_SERVICE_RECIPES, _unused_ports(7), strict=True))
    )
    marker = tmp_path / "stops.log"
    marker_arg = shlex.quote(str(marker))
    services = (
        "identity",
        "central-bank",
        "task-board",
        "reputation",
        "court",
        "db-gateway",
        "ui",
    )
    for service in services:
        service_dir = tmp_path / "services" / service
        service_dir.mkdir(parents=True)
        failure = "    @false\n" if service == "court" else ""
        service_justfile = (
            f"kill:\n    @printf '%s\\n' {service} >> {marker_arg}\n{failure}"
        )
        assert all(
            token not in service_justfile
            for token in ("pkill", "lsof", "uvicorn", "kill -")
        )
        (service_dir / "justfile").write_text(service_justfile, encoding="utf-8")
        (service_dir / "config.yaml").write_text("{}\n", encoding="utf-8")

    agent_marker = tmp_path / "agent-stops.log"
    agent_marker_arg = shlex.quote(str(agent_marker))
    overrides = [
        "",
        "set allow-duplicate-recipes",
        "",
        "stop-feeder:",
        f"    @printf '%s\\n' feeder >> {agent_marker_arg}",
        "",
        "stop-mathbot:",
        f"    @printf '%s\\n' mathbot >> {agent_marker_arg}",
    ]
    with justfile.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(overrides) + "\n")

    rendered = subprocess.run(
        ("just", "--justfile", str(justfile), "--dry-run", "stop-all"),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    rendered_command = rendered.stdout + rendered.stderr
    assert rendered.returncode == 0, rendered_command
    for forbidden in (
        "pkill",
        "lsof",
        "uvicorn",
        "stop-feeder",
        "stop-mathbot",
        "kill -",
    ):
        assert forbidden not in rendered_command

    result = _run_just(justfile, "stop-all")

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert marker.read_text(encoding="utf-8").splitlines() == list(services)
    assert not agent_marker.exists()
    assert "1 service(s) could not be stopped" in output


def test_start_all_failure_stops_only_services_it_launched_and_logs_output(
    tmp_path: Path,
) -> None:
    failed_service = "court"
    ports = dict(zip(_SERVICE_RECIPES, _unused_ports(len(_SERVICE_RECIPES)), strict=True))
    kills = tmp_path / "kills.log"
    kills_arg = shlex.quote(str(kills))
    with ExitStack() as stack:
        for service in _SERVICE_RECIPES:
            if service not in (failed_service, "identity"):
                ports[service] = stack.enter_context(
                    _running_health_server(b'{"status":"ok"}', 200)
                )
        ports["identity"] = stack.enter_context(
            _running_health_server(b'{"status":"ok"}', 200)
        )
        justfile = _copy_justfile(tmp_path, ports)
        overrides = ["", "set allow-duplicate-recipes", "", "check-keys:", "    @:"]
        for service, recipe in _SERVICE_RECIPES.items():
            overrides.extend(
                ("", f"{recipe}:", f"    @printf 'output-of-{service}\\n'")
            )
            overrides.append("    @false")
            service_dir = tmp_path / "services" / service
            service_dir.mkdir(parents=True)
            (service_dir / "justfile").write_text(
                f"kill:\n    @printf '%s\\n' {service} >> {kills_arg}\n",
                encoding="utf-8",
            )
        with justfile.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(overrides) + "\n")
        result = _run_just(justfile, "start-all")

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert kills.read_text(encoding="utf-8").splitlines() == [failed_service]
    log_file = tmp_path / "data" / "logs" / f"{failed_service}.out"
    assert "output-of-court" in log_file.read_text(encoding="utf-8")
    assert "output-of-court" not in output


def test_start_all_refuses_group_readable_court_env(tmp_path: Path) -> None:
    ports = dict(zip(_SERVICE_RECIPES, _unused_ports(len(_SERVICE_RECIPES)), strict=True))
    justfile = _copy_justfile(tmp_path, ports)
    marker = tmp_path / "starts.log"
    marker_arg = shlex.quote(str(marker))
    court_env = tmp_path / "services" / "court" / ".env"
    court_env.parent.mkdir(parents=True)
    court_env.write_text("SECRET=1\n", encoding="utf-8")
    court_env.chmod(0o644)
    overrides = ["", "set allow-duplicate-recipes", "", "check-keys:", "    @:"]
    for service, recipe in _SERVICE_RECIPES.items():
        overrides.extend(
            ("", f"{recipe}:", f"    @printf '%s\\n' {service} >> {marker_arg}")
        )
    with justfile.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(overrides) + "\n")

    result = _run_just(justfile, "start-all")

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert not marker.exists()
    assert "chmod 600 services/court/.env" in output
