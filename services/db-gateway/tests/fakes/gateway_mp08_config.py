"""Ephemeral Gateway configuration builder for MP-08 HTTP tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from service_auth.signing import generate_keypair

if TYPE_CHECKING:
    from pathlib import Path


def write_gateway_config(db_path: str, config_path: Path) -> None:
    generate_keypair("platform", config_path.parent)
    config_path.write_text(
        f"""
service:
  name: "db-gateway-test"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 18006
  log_level: "warning"
telemetry:
  enabled: false
  otlp_endpoint: "http://127.0.0.1:4318"
  sample_ratio: 1.0
  excluded_urls: "health"
logging:
  level: "WARNING"
  directory: "{config_path.parent / "logs" / "db-gateway"}"
  format: "json"
database:
  path: "{db_path}"
  schema_path: "../../docs/specifications/schema.sql"
  busy_timeout_ms: 5000
  journal_mode: "wal"
request:
  max_body_size: 1048576
recovery:
  maintenance_lock_path: "maintenance.lock"
  backup_root: "backups"
board_effect_recovery:
  current_task_board_caller_kid: "gateway-task-board"
  max_attempts_per_delivery: 8
  initial_backoff_seconds: 2
  max_backoff_seconds: 60
  lease_duration_seconds: 45
  evidence_interval_seconds: 60
  gateway_timeout_seconds: 10
  bank_timeout_seconds: 10
board_authority:
  platform_public_key_path: "platform.pub"
  deadline_actor_id: "system-task-board-deadline"
auth:
  audience: "db-gateway"
  max_lifetime_seconds: 30
  clock_skew_seconds: 0
  trusted_callers:
    gateway-identity:
      public_key_path: "gateway-identity.pub"
      capabilities: ["gateway.identity.read", "gateway.identity.write"]
    gateway-central-bank:
      public_key_path: "gateway-central-bank.pub"
      capabilities: ["gateway.bank.read", "gateway.bank.write"]
    gateway-task-board:
      public_key_path: "gateway-task-board.pub"
      capabilities: ["gateway.board.read", "gateway.board.write"]
    gateway-reputation:
      public_key_path: "gateway-reputation.pub"
      capabilities: ["gateway.reputation.read", "gateway.reputation.write"]
    gateway-court:
      public_key_path: "gateway-court.pub"
      capabilities: ["gateway.court.read", "gateway.court.write", "gateway.board.task.read"]
"""
    )


__all__ = ["write_gateway_config"]
