"""Prove router-test logging stays inside its owned temporary directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit.conftest import _create_test_config

pytestmark = pytest.mark.unit


def test_router_fixture_uses_owned_gateway_log_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _create_test_config(str(tmp_path / "gateway.db"), config_path)

    configuration: dict[str, Any] = yaml.safe_load(config_path.read_text())

    assert Path(configuration["logging"]["directory"]) == (tmp_path / "logs" / "db-gateway")
