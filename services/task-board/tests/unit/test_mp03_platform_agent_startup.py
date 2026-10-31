"""Task Board startup uses one factory-validated PlatformAgent signer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml
from fastapi import FastAPI
from service_auth import PlatformSigner
from service_auth.platform import PlatformAgent
from service_auth.signing import KeyMaterialError, generate_keypair
from service_commons.config import ConfigurationError

import task_board_service.core.lifespan as lifespan_module
from task_board_service.config import clear_settings_cache
from task_board_service.core.lifespan import lifespan
from task_board_service.core.state import get_app_state, reset_app_state

if TYPE_CHECKING:
    from pathlib import Path


def _write_agent_config(tmp_path: Path, *, corrupt_private_key: bool) -> Path:
    keys_dir = tmp_path / "agent-keys"
    generate_keypair("platform", keys_dir)
    generate_keypair("operator", keys_dir)
    if corrupt_private_key:
        (keys_dir / "platform.key").write_text("not a private key")
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "platform": {"name": "Platform", "type": "platform"},
                    "operator": {"name": "UI Operator", "type": "operator"},
                }
            }
        )
    )
    agent_config_path = tmp_path / "agents.yaml"
    agent_config_path.write_text(
        yaml.safe_dump(
            {
                "platform": {
                    "identity_url": "http://identity.test",
                    "bank_url": "http://bank.test",
                    "task_board_url": "http://task-board.test",
                    "reputation_url": "http://reputation.test",
                    "court_url": "http://court.test",
                },
                "data": {
                    "keys_dir": str(keys_dir),
                    "roster_path": str(roster_path),
                },
                "signing": {
                    "token_ttl_seconds": 300,
                    "max_lifetime_seconds": 300,
                    "clock_skew_seconds": 0,
                },
            }
        )
    )
    return agent_config_path


def _write_service_config(
    tmp_path: Path,
    *,
    agent_config_path: Path | None,
    legacy_private_key_path: Path,
) -> Path:
    generate_keypair("gateway-task-board", tmp_path)
    platform: dict[str, object] = {
        "agent_id": "a-configured-platform",
        "private_key_path": str(legacy_private_key_path),
    }
    if agent_config_path is not None:
        platform["agent_config_path"] = str(agent_config_path)
    service_config_path = tmp_path / "task-board.yaml"
    service_config_path.write_text(
        yaml.safe_dump(
            {
                "service": {"name": "task-board", "version": "0.1.0"},
                "server": {"host": "127.0.0.1", "port": 8003, "log_level": "info"},
                "logging": {"level": "WARNING", "directory": str(tmp_path / "logs")},
                "database": {"path": str(tmp_path / "task-board.db")},
                "identity": {
                    "base_url": "http://identity.test",
                    "get_agent_path": "/agents/{agent_id}",
                    "verify_jws_path": "/agents/verify-jws",
                    "timeout_seconds": 1,
                },
                "central_bank": {
                    "base_url": "http://bank.test",
                    "escrow_lock_path": "/escrow/lock",
                    "escrow_release_path": "/escrow/{escrow_id}/release",
                    "escrow_split_path": "/escrow/{escrow_id}/split",
                    "escrow_settle_path": "/escrow/{escrow_id}/settle",
                    "timeout_seconds": 1,
                },
                "platform": platform,
                "artifacts": {
                    "max_manifest_bytes": 104857600,
                    "reservation_ttl_seconds": 300,
                    "reservation_renew_before_expiry_seconds": 60,
                    "max_upload_duration_seconds": 900,
                    "recovery_interval_seconds": 60.0,
                    "stale_file_seconds": 900,
                    "archive_after_seconds": 259200,
                    "archive_batch_size": 50,
                },
                "asset_quota": {
                    "max_total_bytes_per_agent": 536_870_912,
                    "max_total_bytes": 4_294_967_296,
                },
                "asset_upload": {
                    "stream_chunk_bytes": 262_144,
                    "max_multipart_overhead_bytes": 1_048_576,
                    "max_header_count": 8,
                    "max_header_size": 4_224,
                    "max_boundary_bytes": 70,
                    "max_display_filename_utf8_bytes": 255,
                },
                "request": {"max_body_size": 1048576},
                "recovery": {"maintenance_lock_path": str(tmp_path / "maintenance.lock")},
                "posting_recovery": {
                    "interval_seconds": 2,
                    "batch_size": 100,
                    "diagnostic_limit": 100,
                },
                "financial_effect_recovery": {
                    "interval_seconds": 2.0,
                    "max_delivery_attempts_per_cycle": 10,
                },
                "db_gateway": {
                    "url": "http://db-gateway.test",
                    "timeout_seconds": 1,
                    "signing": {
                        "kid": "gateway-task-board",
                        "private_key_path": "gateway-task-board.key",
                        "audience": "db-gateway",
                        "token_ttl_seconds": 300,
                    },
                },
                "assets": {
                    "storage_path": str(tmp_path / "assets"),
                    "max_file_size": 1048576,
                    "max_files_per_task": 2,
                },
                "deadline_evaluation": {
                    "evaluation_interval_seconds": 3600,
                    "actor_id": "system-platform-startup-test-deadline",
                },
            }
        )
    )
    return service_config_path


async def _register_platform(agent: PlatformAgent) -> dict[str, Any]:
    agent.agent_id = "a-platform-registered"
    return {"agent_id": agent.agent_id}


@pytest.mark.unit
async def test_startup_reuses_registered_platform_agent_as_token_signer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_config_path = _write_agent_config(tmp_path, corrupt_private_key=False)
    legacy_private_key_path = tmp_path / "legacy-platform.pem"
    service_config_path = _write_service_config(
        tmp_path,
        agent_config_path=agent_config_path,
        legacy_private_key_path=legacy_private_key_path,
    )
    legacy_signer_calls = 0

    def _track_legacy_signer(*args: object, **kwargs: object) -> PlatformSigner:
        nonlocal legacy_signer_calls
        legacy_signer_calls += 1
        return PlatformSigner(*args, **kwargs)

    monkeypatch.setenv("CONFIG_PATH", str(service_config_path))
    monkeypatch.setattr(PlatformAgent, "register", _register_platform)
    monkeypatch.setattr(lifespan_module, "PlatformSigner", _track_legacy_signer, raising=False)
    clear_settings_cache()
    reset_app_state()
    try:
        async with lifespan(FastAPI()):
            state = get_app_state()
            assert state.platform_agent is not None
            assert state.platform_signer is state.platform_agent
            assert state.central_bank_client is not None
            assert state.central_bank_client._platform_signer is state.platform_agent
            assert state.task_manager is not None
            assert state.task_manager._platform_signer is state.platform_agent
            assert legacy_signer_calls == 0
            assert not legacy_private_key_path.exists()
    finally:
        reset_app_state()
        clear_settings_cache()


@pytest.mark.unit
async def test_missing_agent_config_fails_before_legacy_key_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_private_key_path = tmp_path / "legacy-platform.pem"
    service_config_path = _write_service_config(
        tmp_path,
        agent_config_path=None,
        legacy_private_key_path=legacy_private_key_path,
    )
    monkeypatch.setenv("CONFIG_PATH", str(service_config_path))
    clear_settings_cache()
    reset_app_state()
    try:
        with pytest.raises(ConfigurationError, match="agent_config_path"):
            async with lifespan(FastAPI()):
                pass
        assert not legacy_private_key_path.exists()
    finally:
        reset_app_state()
        clear_settings_cache()


@pytest.mark.unit
async def test_damaged_agent_key_fails_without_legacy_key_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_config_path = _write_agent_config(tmp_path, corrupt_private_key=True)
    legacy_private_key_path = tmp_path / "legacy-platform.pem"
    service_config_path = _write_service_config(
        tmp_path,
        agent_config_path=agent_config_path,
        legacy_private_key_path=legacy_private_key_path,
    )
    monkeypatch.setenv("CONFIG_PATH", str(service_config_path))
    clear_settings_cache()
    reset_app_state()
    try:
        with pytest.raises(KeyMaterialError, match="corrupt private key"):
            async with lifespan(FastAPI()):
                pass
        assert not legacy_private_key_path.exists()
    finally:
        reset_app_state()
        clear_settings_cache()
