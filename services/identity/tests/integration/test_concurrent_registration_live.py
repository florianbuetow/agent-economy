"""REG-04: concurrent duplicate key race is safe, proven end-to-end through
a real, live Identity service process — not the in-process ASGITransport
fixture the rest of this integration suite uses, and not the DB Gateway
directly (services/db-gateway/tests/integration/test_concurrent_registration.py
proves the gateway's own atomicity; this proves Identity's real HTTP surface
carries that guarantee through).

Two genuinely separate agent registrations (different names, same public_key),
each over its own TCP connection to a live `uvicorn` process, racing via a
real thread pool. Exactly one must win.
"""

from __future__ import annotations

import base64
import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = REPO_ROOT / "docs" / "specifications" / "schema.sql"
DB_GATEWAY_DIR = REPO_ROOT / "services" / "db-gateway"
IDENTITY_DIR = REPO_ROOT / "services" / "identity"
GATEWAY_PORT = 18040
IDENTITY_PORT = 18041


def _free_port_is(port: int) -> bool:
    try:
        httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.3)
    except httpx.HTTPError:
        return True
    return False


def _wait_until(predicate: object, timeout: float, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(interval)
    return predicate()  # type: ignore[operator]


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


def _write_gateway_config(db_path: Path, log_dir: Path) -> Path:
    content = f"""
service:
  name: "db-gateway-reg04-test"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: {GATEWAY_PORT}
  log_level: "warning"
logging:
  level: "WARNING"
  directory: "{log_dir}"
  format: "json"
database:
  path: "{db_path}"
  schema_path: "{SCHEMA_PATH}"
  busy_timeout_ms: 5000
  journal_mode: "wal"
request:
  max_body_size: 1048576
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, dir=str(log_dir)) as f:
        f.write(content)
        return Path(f.name)


def _write_identity_config(log_dir: Path) -> Path:
    content = f"""
service:
  name: "identity-reg04-test"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: {IDENTITY_PORT}
  log_level: "warning"
logging:
  level: "WARNING"
  directory: "{log_dir}"
crypto:
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64
request:
  max_body_size: 1572864
db_gateway:
  url: "http://127.0.0.1:{GATEWAY_PORT}"
  timeout_seconds: 10
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, dir=str(log_dir)) as f:
        f.write(content)
        return Path(f.name)


@pytest.fixture
def live_identity(tmp_path: Path) -> Iterator[str]:
    """Boot a real DB Gateway + a real Identity service, both live subprocesses,
    against an isolated temp SQLite file. Yields Identity's base_url.

    Ports 18040/18041 are dedicated scratch ports for this test only; if
    something else is already bound there, the health-wait below fails the
    test outright rather than skipping (tests must pass or fail)."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    db_path = tmp_path / "economy.db"

    gateway_config = _write_gateway_config(db_path, log_dir)
    gateway_proc = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "db_gateway_service.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(GATEWAY_PORT),
        ],
        cwd=str(DB_GATEWAY_DIR),
        env={**os.environ, "CONFIG_PATH": str(gateway_config)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_until(lambda: not _free_port_is(GATEWAY_PORT), timeout=15.0), (
            "scratch DB Gateway did not become healthy"
        )

        identity_config = _write_identity_config(log_dir)
        identity_proc = subprocess.Popen(
            [
                "uv",
                "run",
                "uvicorn",
                "identity_service.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(IDENTITY_PORT),
            ],
            cwd=str(IDENTITY_DIR),
            env={**os.environ, "CONFIG_PATH": str(identity_config)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert _wait_until(lambda: not _free_port_is(IDENTITY_PORT), timeout=15.0), (
                "scratch Identity service did not become healthy"
            )
            yield f"http://127.0.0.1:{IDENTITY_PORT}"
        finally:
            _stop(identity_proc)
    finally:
        _stop(gateway_proc)


def _keypair_public_key() -> str:
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return f"ed25519:{base64.b64encode(pub_bytes).decode()}"


def _register_over_own_connection(base_url: str, name: str, public_key: str) -> httpx.Response:
    """Open a brand-new httpx.Client (its own TCP connection) and register."""
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        return client.post(
            "/agents/register",
            json={"name": name, "public_key": public_key},
        )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal handling assumed")
class TestConcurrentDuplicateKeyRaceThroughLiveIdentity:
    def test_exactly_one_201_through_live_identity_process(self, live_identity: str) -> None:
        """REG-04: two distinct registrations racing on the same public_key,
        each over its own connection to a real, live Identity process."""
        shared_public_key = _keypair_public_key()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(_register_over_own_connection, live_identity, name, shared_public_key)
                for name in ("Racer One", "Racer Two")
            ]
            responses = [f.result() for f in as_completed(futures)]

        status_codes = sorted(r.status_code for r in responses)
        assert status_codes == [201, 409], f"expected [201, 409], got {status_codes}"

        conflict_bodies = [r.json() for r in responses if r.status_code == 409]
        assert conflict_bodies[0]["error"] == "public_key_exists"

        winner = next(r for r in responses if r.status_code == 201)
        winner_id = winner.json()["agent_id"]

        with httpx.Client(base_url=live_identity, timeout=10.0) as client:
            read_back = client.get(f"/agents/{winner_id}")
            assert read_back.status_code == 200
            assert read_back.json()["public_key"] == shared_public_key
