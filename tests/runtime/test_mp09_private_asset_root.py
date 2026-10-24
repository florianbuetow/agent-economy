"""Disposable runtimes satisfy the owned artifact-root admission contract."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from scripts.isolated_test_runtime import IsolatedRuntime

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_disposable_runtime_creates_private_owned_asset_root(tmp_path: Path) -> None:
    """A valid disposable asset root is private without later chmod repair."""
    runtime = IsolatedRuntime.create(
        repo_root=_REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    try:
        root = runtime.run_dir / "assets"
        identity = root.stat()
        assert identity.st_uid == os.geteuid()
        assert stat.S_IMODE(identity.st_mode) == 0o700
        assert list(root.iterdir()) == []
    finally:
        runtime.stop()
