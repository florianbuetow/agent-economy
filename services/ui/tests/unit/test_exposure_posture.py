"""Regression test for WP-08 item 5 (Q-3): 127.0.0.1-only is the security boundary.

``/api/proxy/*`` is unauthenticated (see
docs/plans/2026-07-10-q3-proxy-exposure-decision.md) and, combined with the
dedicated operator identity (Q-2), can spend the operator's funds. The only
thing standing between that and anyone who can reach the port is the bind
address. This test guards the real config.yaml so a change to a non-loopback
host does not ship silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REAL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


def test_real_config_binds_localhost_only() -> None:
    raw = yaml.safe_load(REAL_CONFIG_PATH.read_text())
    assert raw["server"]["host"] == "127.0.0.1"
