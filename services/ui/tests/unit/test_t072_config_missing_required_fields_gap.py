"""CFG-01 gap: config.yaml missing a required top-level section must fail
startup (fail-fast). tests/unit/test_config.py only tests a fully valid
config and one rejecting *extra* fields — it never tests *missing* required
fields.

``user_agent`` is included: WP-13 fixed ``ui_service.config.get_settings()``
to stop unconditionally backfilling a fully-missing ``user_agent`` block
from the service's own checked-in config.yaml — the backfill now only fires
to fill in missing *keys* within an already-present, partial ``user_agent``
block (the ratified exception #10 / WP-08 contract, still exercised by
``services/ui/tests/unit/test_treasury_funding.py``). A completely absent
section now fails fast like every other required section.
"""

from __future__ import annotations

import os

import pytest

from ui_service.config import clear_settings_cache, get_settings

pytestmark = pytest.mark.unit

_FULL_CONFIG_SECTIONS = {
    "service": 'service:\n  name: "ui"\n  version: "0.1.0"\n',
    "server": 'server:\n  host: "127.0.0.1"\n  port: 8008\n  log_level: "info"\n',
    "logging": 'logging:\n  level: "INFO"\n  directory: "data/logs"\n',
    "database": 'database:\n  path: "data/economy.db"\n',
    "sse": (
        "sse:\n  poll_interval_seconds: 1\n  keepalive_interval_seconds: 15\n  batch_size: 50\n"
    ),
    "frontend": 'frontend:\n  web_root: "data/web"\n',
    "request": "request:\n  max_body_size: 1572864\n",
    "user_agent": (
        "user_agent:\n"
        '  agent_config_path: "../../agents/config.yaml"\n'
        '  handle: "ui-operator-test"\n'
    ),
}


@pytest.mark.parametrize(
    "omitted_section",
    ["server", "database", "sse", "frontend", "request", "user_agent"],
)
def test_missing_required_section_fails_startup(tmp_path, omitted_section: str) -> None:
    """CFG-01: omitting server/database/sse/frontend/request/user_agent raises
    at settings-load time."""
    content = "".join(
        block for name, block in _FULL_CONFIG_SECTIONS.items() if name != omitted_section
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    try:
        with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError via load_settings
            get_settings()
    finally:
        clear_settings_cache()
        os.environ.pop("CONFIG_PATH", None)
