"""Disposable runtimes preserve the required Task Board upload limits."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import cast

import pytest
import yaml
from scripts.isolated_test_runtime import IsolatedRuntime

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK_BOARD_ROOT = _REPO_ROOT / "services" / "task-board"
_EXPECTED_ASSET_UPLOAD = {
    "stream_chunk_bytes": 262144,
    "max_multipart_overhead_bytes": 1048576,
    "max_header_count": 8,
    "max_header_size": 4224,
    "max_boundary_bytes": 70,
    "max_display_filename_utf8_bytes": 255,
}
_VALIDATE_SETTINGS = """\
import json
import sys
from pathlib import Path

import yaml
from task_board_service.config import Settings

document = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
settings = Settings.model_validate(document)
print(json.dumps(settings.asset_upload.model_dump(), sort_keys=True))
"""


def _validated_asset_upload(config_path: Path) -> dict[str, int]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--directory",
            str(_TASK_BOARD_ROOT),
            "--frozen",
            "--no-sync",
            "python",
            "-c",
            _VALIDATE_SETTINGS,
            str(config_path),
        ],
        cwd=_REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert isinstance(result, dict)
    return cast("dict[str, int]", result)


def test_generated_task_board_config_preserves_asset_upload_limits(tmp_path: Path) -> None:
    """The root runtime emits a complete, valid Task Board config."""
    runtime = IsolatedRuntime.create(
        repo_root=_REPO_ROOT,
        artifacts_root=tmp_path,
        services=("task-board",),
    )
    try:
        config_path = runtime.config_paths["task-board"]
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        assert document["asset_upload"] == _EXPECTED_ASSET_UPLOAD
        assert _validated_asset_upload(config_path) == _EXPECTED_ASSET_UPLOAD
    finally:
        runtime.stop()
