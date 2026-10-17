"""T-167/T-168: browser prerequisites fail before browser-dependent tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SERVICE_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("cache_state", ["empty", "stale"])
def test_missing_browser_has_actionable_prerequisite_error(
    tmp_path: Path, cache_state: str
) -> None:
    """An absent or obsolete browser cache must never produce a green preflight."""
    browser_cache = tmp_path / "browsers"
    browser_cache.mkdir()
    if cache_state == "stale":
        (browser_cache / "chromium_headless_shell-1").mkdir()

    environment = os.environ.copy()
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)
    environment["UV_NO_SYNC"] = "1"
    environment["UV_OFFLINE"] = "1"
    result = subprocess.run(
        ["just", "test-prerequisites"],
        cwd=SERVICE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode != 0
    assert "uv run playwright install chromium" in result.stdout + result.stderr
    assert "Running Unit Tests" not in result.stdout
