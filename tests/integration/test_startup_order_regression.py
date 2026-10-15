"""GAP-F6: startup-order regression test.

`justfile`'s `start-all` boots DB Gateway (tier 1), then Identity (tier 2,
gated on a "fully-online" health probe), before starting Central Bank/Task
Board/Reputation/Court (tier 3) — because (a) every service's own `/health`
reads through its DbReader/DbClient, so it can't report healthy without a
live gateway, and (b) the tier-3 services register a platform agent against
Identity during their own FastAPI lifespan startup (their real `config.yaml`
sets `platform.agent_config_path`), and launching them concurrently with
Identity previously raced: a dependent could POST /agents/register before
Identity had bound its socket, aborting that dependent's lifespan with an
unhandled `httpx.ConnectError` ("Application startup failed. Exiting.").

This test proves that documented failure mode is real and recoverable,
using Central Bank as the one representative tier-3 dependent (cheap: one
extra process beyond the two-tier prerequisite, short bounded waits) rather
than every dependent — the registration-at-startup code path is identical
across all four (central_bank/task_board/reputation/court's lifespans all
gate `platform_agent.register()` on the same `agent_config_path` config
knob), so one is representative of the class this regression protects
against. DB Gateway runs against an isolated temp SQLite file (not the
shared dev `data/economy.db`) so this test never touches real data.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "docs" / "specifications" / "schema.sql"
IDENTITY_DIR = REPO_ROOT / "services" / "identity"
CENTRAL_BANK_DIR = REPO_ROOT / "services" / "central-bank"
DB_GATEWAY_DIR = REPO_ROOT / "services" / "db-gateway"
DB_GATEWAY_PORT = 8007
IDENTITY_PORT = 8001
CENTRAL_BANK_PORT = 8002


def _is_listening(port: int) -> bool:
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


def _start(
    service_dir: Path, module: str, port: int, env: dict[str, str] | None = None
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            module,
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(service_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


def _wait_until(predicate: object, timeout: float, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(interval)
    return predicate()  # type: ignore[operator]


def _write_scratch_gateway_config(db_path: Path, log_dir: Path) -> Path:
    content = f"""
service:
  name: "db-gateway-startup-order-test"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: {DB_GATEWAY_PORT}
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


@pytest.fixture(autouse=True)
def _ensure_ports_free() -> None:
    """Guard: skip (don't corrupt results) if these ports are already
    occupied by something else — this test manages those processes' full
    lifecycle itself and must own them exclusively."""
    if any(_is_listening(p) for p in (DB_GATEWAY_PORT, IDENTITY_PORT, CENTRAL_BANK_PORT)):
        pytest.skip("port 8001/8002/8007 already in use — not this test's process to manage")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal handling assumed")
def test_dependent_fails_cleanly_without_identity_then_recovers_once_identity_is_up(
    tmp_path: Path,
) -> None:
    """Start Central Bank while Identity is down (DB Gateway up, matching
    real tier-1-then-tier-2-then-tier-3 ordering): Central Bank must not
    silently come up healthy — its lifespan must abort (process exits)
    within a bounded window. Then start Identity, retry Central Bank, and
    it must become healthy — proving the documented tiered-startup ordering
    is both necessary and sufficient, not just a superstition baked into
    the justfile."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    db_path = tmp_path / "economy.db"
    config_path = _write_scratch_gateway_config(db_path, log_dir)

    gateway_proc = _start(
        DB_GATEWAY_DIR,
        "db_gateway_service.app:create_app",
        DB_GATEWAY_PORT,
        env={**os.environ, "CONFIG_PATH": str(config_path)},
    )
    try:
        gateway_ready = _wait_until(lambda: _is_listening(DB_GATEWAY_PORT), timeout=15.0)
        assert gateway_ready, "scratch DB Gateway did not become healthy — cannot run this test"

        cb_without_identity = _start(
            CENTRAL_BANK_DIR, "central_bank_service.app:create_app", CENTRAL_BANK_PORT
        )
        try:
            became_healthy = _wait_until(lambda: _is_listening(CENTRAL_BANK_PORT), timeout=8.0)
            assert not became_healthy, (
                "Central Bank came up healthy without Identity running — "
                "the documented startup-order dependency no longer holds"
            )
            exited_cleanly = _wait_until(
                lambda: cb_without_identity.poll() is not None, timeout=5.0
            )
            assert exited_cleanly, "Central Bank neither became healthy nor exited — it hung"
            assert cb_without_identity.returncode != 0
        finally:
            _stop(cb_without_identity)

        identity_proc = _start(IDENTITY_DIR, "identity_service.app:create_app", IDENTITY_PORT)
        try:
            identity_ready = _wait_until(lambda: _is_listening(IDENTITY_PORT), timeout=15.0)
            assert identity_ready, "Identity did not become healthy — cannot test the retry path"

            cb_with_identity = _start(
                CENTRAL_BANK_DIR, "central_bank_service.app:create_app", CENTRAL_BANK_PORT
            )
            try:
                recovered = _wait_until(lambda: _is_listening(CENTRAL_BANK_PORT), timeout=15.0)
                assert recovered, (
                    "Central Bank still failed to start with Identity now running — "
                    "the failure above was not actually about startup order"
                )
            finally:
                _stop(cb_with_identity)
        finally:
            _stop(identity_proc)
    finally:
        _stop(gateway_proc)
