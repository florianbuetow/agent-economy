"""ECO-05 wrapper for the owned disposable compound recovery harness."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def test_compound_recovery_sequence(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    result_path = tmp_path / "compound-result.json"
    env = {**os.environ, "PYTHONPATH": str(repo_root)}
    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "tests/adversarial/compound_recovery.py"),
            "--artifacts-root",
            str(tmp_path / "runtime"),
            "--result",
            str(result_path),
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(result_path.read_text())
    assert result["accepted_price"] == 60
    assert result["votes"] == 3
    assert result["worker_payment"] == 0
    assert result["poster_refund"] == 100
