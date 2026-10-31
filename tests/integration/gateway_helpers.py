"""Helpers for in-process DB Gateway test clients."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import TracebackType

    from db_gateway_service.core.state import AppState
    from db_gateway_service.services.db_writer import DbWriter


_DOMAIN_HANDLES = {
    "identity": "gateway-identity",
    "bank": "gateway-central-bank",
    "board": "gateway-task-board",
    "reputation": "gateway-reputation",
    "court": "gateway-court",
}


class _GatewayTestAuth(httpx.Auth):
    """Sign each request with its domain's distinct temporary capability."""

    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        domain = request.url.path.split("/", maxsplit=2)[1]
        if domain == "health" and request.method == "GET" and not request.url.query:
            yield request
            return
        signer = self._signers[domain]
        access = "read" if request.method == "GET" else "write"
        yield signer.sign_request(request, action=f"gateway.{domain}.{access}")


class _GatewayAsyncClient(httpx.AsyncClient):
    """Async client that always releases its owned Gateway test resources."""

    def __init__(self, *, cleanup: Callable[[], None], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._cleanup = cleanup
        self._cleanup_complete = False

    async def aclose(self) -> None:
        close_error: BaseException | None = None
        try:
            await super().aclose()
        except BaseException as exc:
            close_error = exc
        try:
            self._cleanup_once()
        except BaseException as cleanup_error:
            if close_error is not None:
                close_error.add_note(f"Gateway test cleanup also failed: {cleanup_error}")
            else:
                raise
        if close_error is not None:
            raise close_error

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        exit_error: BaseException | None = None
        try:
            await super().__aexit__(exc_type, exc_value, traceback)
        except BaseException as exc:
            exit_error = exc
        try:
            self._cleanup_once()
        except BaseException as cleanup_error:
            primary_error = exit_error or exc_value
            if primary_error is not None:
                primary_error.add_note(f"Gateway test cleanup also failed: {cleanup_error}")
            else:
                raise
        if exit_error is not None:
            raise exit_error

    def _cleanup_once(self) -> None:
        if self._cleanup_complete:
            return
        self._cleanup_complete = True
        self._cleanup()


def _write_test_config(db_path: str, schema_path: str, config_dir: Path) -> Path:
    config_content = f"""
service:
  name: "db-gateway-test"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 18007
  log_level: "warning"

telemetry:
  enabled: false
  otlp_endpoint: "http://127.0.0.1:4318"
  sample_ratio: 1.0
  excluded_urls: "health"
logging:
  level: "WARNING"
  directory: "data/logs"
  format: "json"

database:
  path: "{db_path}"
  schema_path: "{schema_path}"
  busy_timeout_ms: 5000
  journal_mode: "wal"

recovery:
  maintenance_lock_path: "{config_dir / "maintenance.lock"}"
  backup_root: "{config_dir / "backups"}"

board_authority:
  platform_public_key_path: "{config_dir / "platform.pub"}"
  deadline_actor_id: "system-task-board-deadline"

board_effect_recovery:
  current_task_board_caller_kid: "gateway-task-board"
  max_attempts_per_delivery: 8
  initial_backoff_seconds: 2
  max_backoff_seconds: 60
  lease_duration_seconds: 45
  evidence_interval_seconds: 60
  gateway_timeout_seconds: 10
  bank_timeout_seconds: 10

request:
  max_body_size: 1048576

auth:
  audience: "db-gateway"
  max_lifetime_seconds: 30
  clock_skew_seconds: 0
  trusted_callers:
    gateway-identity:
      public_key_path: "{config_dir / "gateway-identity.pub"}"
      capabilities: ["gateway.identity.read", "gateway.identity.write"]
    gateway-central-bank:
      public_key_path: "{config_dir / "gateway-central-bank.pub"}"
      capabilities: ["gateway.bank.read", "gateway.bank.write"]
    gateway-task-board:
      public_key_path: "{config_dir / "gateway-task-board.pub"}"
      capabilities: ["gateway.board.read", "gateway.board.write"]
    gateway-reputation:
      public_key_path: "{config_dir / "gateway-reputation.pub"}"
      capabilities: ["gateway.reputation.read", "gateway.reputation.write"]
    gateway-court:
      public_key_path: "{config_dir / "gateway-court.pub"}"
      capabilities: ["gateway.court.read", "gateway.court.write", "gateway.board.task.read"]
"""
    config_path = config_dir / "config.yaml"
    config_path.write_text(config_content)
    return config_path


def _raise_cleanup_errors(cleanup_errors: list[BaseException]) -> None:
    if not cleanup_errors:
        return
    if len(cleanup_errors) == 1:
        raise cleanup_errors[0]
    raise BaseExceptionGroup("Gateway test resource cleanup failed", cleanup_errors)


def _cleanup_gateway_state(
    temporary_directory: tempfile.TemporaryDirectory[str],
    owned_state: AppState | None,
    owned_writer: DbWriter | None,
) -> None:
    from db_gateway_service.config import clear_settings_cache
    from db_gateway_service.core.state import get_app_state, reset_app_state

    cleanup_errors: list[BaseException] = []
    if owned_writer is not None:
        try:
            owned_writer.close()
        except BaseException as exc:
            cleanup_errors.append(exc)

    try:
        current_state = get_app_state()
    except RuntimeError:
        current_state = None
    if current_state is owned_state:
        try:
            reset_app_state()
        except BaseException as exc:
            cleanup_errors.append(exc)
    try:
        clear_settings_cache()
    except BaseException as exc:
        cleanup_errors.append(exc)
    try:
        temporary_directory.cleanup()
    except BaseException as exc:
        cleanup_errors.append(exc)
    _raise_cleanup_errors(cleanup_errors)


def create_gateway_client(db_path: str) -> httpx.AsyncClient:
    """Create an async client that talks to an in-process DB Gateway app."""
    schema_path = str(
        Path(__file__).parent.parent.parent / "docs" / "specifications" / "schema.sql"
    )
    temporary_directory = tempfile.TemporaryDirectory(prefix="gateway-integration-")
    config_dir = Path(temporary_directory.name)
    state = None
    try:
        generate_keypair("platform", config_dir)
        signers: dict[str, GatewayRequestSigner] = {}
        for domain, handle in _DOMAIN_HANDLES.items():
            generate_keypair(handle, config_dir)
            signers[domain] = GatewayRequestSigner(
                GatewaySignerConfig(
                    kid=handle,
                    private_key_path=config_dir / f"{handle}.key",
                    audience="db-gateway",
                    token_ttl_seconds=30,
                ),
                config_dir=config_dir,
            )
        config_path = _write_test_config(db_path, schema_path, config_dir)

        with patch.dict(os.environ, {"CONFIG_PATH": str(config_path)}):
            from db_gateway_service.config import clear_settings_cache, get_settings
            from db_gateway_service.core.state import get_app_state, init_app_state, reset_app_state
            from db_gateway_service.services.db_reader import DbReader
            from db_gateway_service.services.db_writer import DbWriter

            clear_settings_cache()
            settings = get_settings()

            try:
                existing_state = get_app_state()
            except RuntimeError:
                existing_state = None
            if existing_state is not None and existing_state.db_writer is not None:
                existing_state.db_writer.close()

            reset_app_state()
            state = init_app_state()
            schema_sql = Path(settings.database.schema_path).read_text()
            state.db_writer = DbWriter(
                db_path=settings.database.path,
                busy_timeout_ms=settings.database.busy_timeout_ms,
                journal_mode=settings.database.journal_mode,
                schema_sql=schema_sql,
                current_task_board_caller_kid=(
                    settings.board_effect_recovery.current_task_board_caller_kid
                ),
            )
            state.db_reader = DbReader(db=state.db_writer._db)

            from db_gateway_service.app import create_app

            app = create_app()
            clear_settings_cache()

        transport = httpx.ASGITransport(app=app)
        owned_writer = state.db_writer
        return _GatewayAsyncClient(
            transport=transport,
            base_url="http://gateway-test",
            auth=_GatewayTestAuth(signers),
            cleanup=lambda: _cleanup_gateway_state(temporary_directory, state, owned_writer),
        )
    except BaseException as setup_error:
        try:
            owned_writer = state.db_writer if state is not None else None
            _cleanup_gateway_state(temporary_directory, state, owned_writer)
        except BaseException as cleanup_error:
            setup_error.add_note(f"Gateway test setup cleanup also failed: {cleanup_error}")
        raise
