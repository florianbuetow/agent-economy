"""Strict configuration coverage for bounded streaming asset uploads."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from task_board_service.config import Settings

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
_EXPECTED_ASSET_UPLOAD = {
    "stream_chunk_bytes": 262144,
    "max_multipart_overhead_bytes": 1048576,
    "max_header_count": 8,
    "max_header_size": 4224,
    "max_boundary_bytes": 70,
    "max_display_filename_utf8_bytes": 255,
}


def _valid_config() -> dict[str, Any]:
    value = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.mark.unit
def test_shipped_asset_upload_config_has_exact_limits() -> None:
    """The checked-in service config carries every approved explicit limit."""
    settings = Settings.model_validate(_valid_config())

    assert settings.asset_upload.model_dump() == _EXPECTED_ASSET_UPLOAD


@pytest.mark.unit
def test_asset_upload_config_is_required() -> None:
    """Startup rejects a config that omits the streaming-upload limit block."""
    value = _valid_config()
    value.pop("asset_upload", None)

    with pytest.raises(ValidationError):
        Settings.model_validate(value)


@pytest.mark.unit
@pytest.mark.parametrize("field", tuple(_EXPECTED_ASSET_UPLOAD))
def test_asset_upload_config_requires_every_field(field: str) -> None:
    """Each parser bound is explicit rather than supplied by a code default."""
    value = _valid_config()
    asset_upload = deepcopy(_EXPECTED_ASSET_UPLOAD)
    asset_upload.pop(field)
    value["asset_upload"] = asset_upload

    with pytest.raises(ValidationError):
        Settings.model_validate(value)


@pytest.mark.unit
@pytest.mark.parametrize("invalid_value", [0, -1, 1.0, True, "1"])
@pytest.mark.parametrize("field", tuple(_EXPECTED_ASSET_UPLOAD))
def test_asset_upload_config_requires_strict_positive_integers(
    field: str,
    invalid_value: object,
) -> None:
    """Zero, negative, coerced, and boolean parser bounds are rejected."""
    value = _valid_config()
    asset_upload = deepcopy(_EXPECTED_ASSET_UPLOAD)
    asset_upload[field] = invalid_value
    value["asset_upload"] = asset_upload

    with pytest.raises(ValidationError):
        Settings.model_validate(value)


@pytest.mark.unit
def test_asset_upload_config_rejects_extra_fields() -> None:
    """Unknown parser controls cannot silently enter the runtime contract."""
    value = _valid_config()
    value["asset_upload"] = {
        **_EXPECTED_ASSET_UPLOAD,
        "unbounded_buffer_bytes": 1,
    }

    with pytest.raises(ValidationError):
        Settings.model_validate(value)
