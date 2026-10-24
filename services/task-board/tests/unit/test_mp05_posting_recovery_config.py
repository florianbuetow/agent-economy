"""MP-05 configuration contract for bounded durable posting recovery."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from task_board_service.config import Settings

pytestmark = pytest.mark.unit


def _valid_config() -> dict[str, Any]:
    """Return a complete explicit Task Board configuration object."""
    return {
        "service": {"name": "task-board", "version": "0.1.0"},
        "server": {"host": "127.0.0.1", "port": 8003, "log_level": "info"},
        "logging": {"level": "INFO", "directory": "data/logs"},
        "database": {"path": "data/task-board.db"},
        "identity": {
            "base_url": "http://127.0.0.1:8001",
            "get_agent_path": "/agents",
            "verify_jws_path": "/agents/verify-jws",
            "timeout_seconds": 10,
        },
        "central_bank": {
            "base_url": "http://127.0.0.1:8002",
            "escrow_lock_path": "/escrow/lock",
            "escrow_release_path": "/escrow/{escrow_id}/release",
            "escrow_split_path": "/escrow/{escrow_id}/split",
            "timeout_seconds": 10,
        },
        "platform": {
            "agent_id": "a-platform",
            "private_key_path": "platform.key",
            "agent_config_path": "agents.yaml",
        },
        "request": {"max_body_size": 1_048_576},
        "asset_upload": {
            "stream_chunk_bytes": 262_144,
            "max_multipart_overhead_bytes": 1_048_576,
            "max_header_count": 8,
            "max_header_size": 4_224,
            "max_boundary_bytes": 70,
            "max_display_filename_utf8_bytes": 255,
        },
        "db_gateway": {
            "url": "http://127.0.0.1:8007",
            "timeout_seconds": 10,
            "signing": {
                "kid": "gateway-task-board",
                "private_key_path": "gateway-task-board.key",
                "audience": "db-gateway",
                "token_ttl_seconds": 30,
            },
        },
        "assets": {
            "storage_path": "data/assets",
            "max_file_size": 10_485_760,
            "max_files_per_task": 10,
        },
        "deadline_evaluation": {
            "actor_id": "system-task-board-deadline",
            "evaluation_interval_seconds": 10,
        },
        "recovery": {"maintenance_lock_path": "maintenance.lock"},
        "posting_recovery": {
            "interval_seconds": 0.25,
            "batch_size": 100,
            "diagnostic_limit": 200,
        },
        "financial_effect_recovery": {
            "interval_seconds": 2.0,
            "max_delivery_attempts_per_cycle": 10,
        },
    }


def _assert_single_nested_error(
    value: dict[str, Any],
    field: str,
    error_type: str,
) -> None:
    """Require the posting_recovery field itself to cause validation failure."""
    with pytest.raises(ValidationError) as caught:
        Settings.model_validate(value)
    assert [(issue["loc"], issue["type"]) for issue in caught.value.errors()] == [
        (("posting_recovery", field), error_type)
    ]


def test_explicit_posting_recovery_config_is_preserved() -> None:
    settings = Settings.model_validate(_valid_config())

    assert settings.model_dump()["posting_recovery"] == {
        "interval_seconds": 0.25,
        "batch_size": 100,
        "diagnostic_limit": 200,
    }


def test_posting_recovery_section_is_required() -> None:
    value = _valid_config()
    del value["posting_recovery"]

    with pytest.raises(ValidationError) as caught:
        Settings.model_validate(value)
    assert [(issue["loc"], issue["type"]) for issue in caught.value.errors()] == [
        (("posting_recovery",), "missing")
    ]


@pytest.mark.parametrize("field", ["interval_seconds", "batch_size", "diagnostic_limit"])
def test_every_posting_recovery_field_is_required(field: str) -> None:
    value = _valid_config()
    del value["posting_recovery"][field]

    _assert_single_nested_error(value, field, "missing")


@pytest.mark.parametrize(
    ("value", "error_type"),
    [
        (True, "float_type"),
        (False, "float_type"),
        ("1", "float_type"),
        (None, "float_type"),
        (0, "greater_than"),
        (-1, "greater_than"),
        (float("inf"), "finite_number"),
        (float("-inf"), "finite_number"),
        (float("nan"), "finite_number"),
    ],
)
def test_interval_seconds_rejects_bool_coercion_nonfinite_and_nonpositive_values(
    value: object,
    error_type: str,
) -> None:
    config = _valid_config()
    config["posting_recovery"]["interval_seconds"] = value

    _assert_single_nested_error(config, "interval_seconds", error_type)


@pytest.mark.parametrize("field", ["batch_size", "diagnostic_limit"])
@pytest.mark.parametrize(
    ("value", "error_type"),
    [
        (True, "int_type"),
        (False, "int_type"),
        (1.0, "int_type"),
        ("1", "int_type"),
        (None, "int_type"),
        (0, "greater_than_equal"),
        (-1, "greater_than_equal"),
        (1001, "less_than_equal"),
    ],
)
def test_integer_limits_are_strict_and_bounded(
    field: str,
    value: object,
    error_type: str,
) -> None:
    config = deepcopy(_valid_config())
    config["posting_recovery"][field] = value

    _assert_single_nested_error(config, field, error_type)


def test_posting_recovery_rejects_unknown_nested_fields() -> None:
    value = _valid_config()
    value["posting_recovery"]["retry_count"] = 3

    _assert_single_nested_error(value, "retry_count", "extra_forbidden")
