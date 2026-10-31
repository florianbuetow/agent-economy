"""Support for the isolated UI browser-test server."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from service_auth.signing import generate_keypair

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    import pytest

E2E_OPERATOR_HANDLE = "e2e-operator"
E2E_OPERATOR_AGENT_ID = "a-e2e-operator"
_UNREACHABLE_SERVICE_URL = "http://127.0.0.1:0"
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_E2E_SUPPORT = Path(__file__).resolve().parent


def provision_e2e_operator(work_dir: Path) -> tuple[Path, str]:
    """Provision a disposable operator for the isolated browser server."""
    keys_dir = work_dir / "keys"
    generate_keypair(E2E_OPERATOR_HANDLE, keys_dir)

    roster_path = work_dir / "roster.yaml"
    roster_path.write_text(
        f'agents:\n  {E2E_OPERATOR_HANDLE}:\n    name: "E2E Operator"\n',
    )

    agent_config_path = work_dir / "agents.yaml"
    agent_config_path.write_text(
        f"""\
platform:
  identity_url: "{_UNREACHABLE_SERVICE_URL}"
  bank_url: "{_UNREACHABLE_SERVICE_URL}"
  task_board_url: "{_UNREACHABLE_SERVICE_URL}"
  reputation_url: "{_UNREACHABLE_SERVICE_URL}"
  court_url: "{_UNREACHABLE_SERVICE_URL}"
data:
  keys_dir: "{keys_dir}"
  roster_path: "{roster_path}"
signing:
  token_ttl_seconds: 60
  max_lifetime_seconds: 300
  clock_skew_seconds: 30
""",
    )
    return agent_config_path, E2E_OPERATOR_HANDLE


def _create_server_socket() -> socket.socket:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen()
    server_socket.set_inheritable(True)
    return server_socket


def _server_log_text(log_path: Path) -> str:
    if not log_path.exists():
        return "<server log was not created>"
    text = log_path.read_text()
    return text if text else "<server log is empty>"


def _wait_for_server(
    port: int,
    process: subprocess.Popen[str],
    log_path: Path,
    timeout: float = 15.0,
) -> None:
    import httpx  # noqa: PLC0415

    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            msg = (
                f"UI E2E server exited with code {return_code} before readiness.\n"
                f"Server log ({log_path}):\n{_server_log_text(log_path)}"
            )
            raise RuntimeError(msg)
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        time.sleep(0.3)
    msg = (
        f"Server on port {port} did not become healthy within {timeout}s.\n"
        f"Server log ({log_path}):\n{_server_log_text(log_path)}"
    )
    raise TimeoutError(msg)


def _stop_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        process.wait()
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _child_environment(
    config_path: Path,
    *,
    frozen_clock: bool,
    database_path: Path,
) -> dict[str, str]:
    env = {
        **os.environ,
        "CONFIG_PATH": str(config_path),
        "UI_E2E_DATABASE_PATH": str(database_path),
    }
    env.pop("UI_E2E_FROZEN_CLOCK", None)
    if frozen_clock:
        env["UI_E2E_FROZEN_CLOCK"] = "1"
    return env


def start_controlled_ui_server(
    db_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    name: str,
    *,
    frozen_clock: bool,
    socket_factory: Callable[[], socket.socket] = _create_server_socket,
    provision_operator: Callable[[Path], tuple[Path, str]] = provision_e2e_operator,
) -> Generator[str, None, None]:
    """Run an isolated UI child with disposable credentials and retained logs."""
    server_socket = socket_factory()
    process: subprocess.Popen[str] | None = None
    server_log = None
    try:
        work_dir = tmp_path_factory.mktemp(name)
        port = server_socket.getsockname()[1]

        web_dir = work_dir / "web"
        real_web = _PROJECT_ROOT / "services" / "ui" / "data" / "web"
        if real_web.is_dir():
            shutil.copytree(str(real_web), str(web_dir))
        else:
            web_dir.mkdir()
            (web_dir / "index.html").write_text("<html><body>Test</body></html>")

        log_dir = work_dir / "logs"
        log_dir.mkdir()
        agent_config_path, operator_handle = provision_operator(work_dir)

        config_path = work_dir / "config.yaml"
        config_path.write_text(
            f"""\
service:
  name: "ui"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: {port}
  log_level: "warning"
telemetry:
  enabled: false
  otlp_endpoint: "http://127.0.0.1:4318"
  sample_ratio: 1.0
  excluded_urls: "health"
logging:
  level: "WARNING"
  directory: "{log_dir}"
database:
  path: "{db_path}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "{web_dir}"
request:
  max_body_size: 1572864
recovery:
  maintenance_lock_path: "{work_dir / "maintenance.lock"}"
browser_security:
  allowed_hosts: ["127.0.0.1", "localhost"]
  allowed_origins: ["http://127.0.0.1:{port}", "http://localhost:{port}"]
  csrf_header_name: "X-ATE-CSRF"
  csrf_header_value: "local-operator"
  content_security_policy: >-
    default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
    img-src 'self' data:; connect-src 'self'; object-src 'none';
    base-uri 'none'; frame-ancestors 'none'; form-action 'self'
user_agent:
  agent_config_path: "{agent_config_path}"
  handle: "{operator_handle}"
""",
        )

        server_log_path = work_dir / "server.log"
        server_log = server_log_path.open("w")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "_server_app:create_app",
                "--factory",
                "--fd",
                str(server_socket.fileno()),
                "--app-dir",
                str(_E2E_SUPPORT),
            ],
            cwd=str(_PROJECT_ROOT / "services" / "ui"),
            env=_child_environment(
                config_path,
                frozen_clock=frozen_clock,
                database_path=db_path,
            ),
            pass_fds=(server_socket.fileno(),),
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        server_socket.close()
        _wait_for_server(port, process, server_log_path)
        yield f"http://127.0.0.1:{port}"
    finally:
        server_socket.close()
        if process is not None:
            _stop_server(process)
        if server_log is not None:
            server_log.close()
