"""The stats recipe counts every advertised file extension."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def test_stats_counts_yaml_and_yml_files_with_safe_filenames(tmp_path: Path) -> None:
    script = tmp_path / "stats.sh"
    shutil.copy2(_ROOT / "scripts" / "stats.sh", script)
    script.chmod(0o700)
    tmp_path.joinpath("plain.yml").write_text("one\n", encoding="utf-8")
    nested = tmp_path / "nested directory"
    nested.mkdir()
    nested.joinpath("with space.yaml").write_text("two\nthree\n", encoding="utf-8")

    result = subprocess.run(
        (str(script),),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = _ANSI_ESCAPE.sub("", result.stdout + result.stderr)
    assert result.returncode == 0, output
    match = re.search(r"^\s*YAML\s+([0-9]+)\s*$", output, flags=re.MULTILINE)
    assert match is not None, output
    assert int(match.group(1)) == 3
