"""READ-02 gap: no INSERT/UPDATE/DELETE SQL literal may appear anywhere
under src/ui_service/. The Q-4 sanctioned read-only exception
(tests/architecture/test_readonly_connection.py, READ-01) only guards the
connection URI (``?mode=ro`` + ``uri=True``); nothing in this suite scans
for write-statement literals themselves.

Verified before writing this test that it passes against the current
source tree (a plain grep for INSERT/UPDATE/DELETE SQL literals under
src/ui_service/ found none), so this closes the gap without masking any
existing violation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

_SRC = Path(__file__).resolve().parents[2] / "src" / "ui_service"
_WRITE_STATEMENT_RE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", re.IGNORECASE
)


def _iter_python_files() -> list[Path]:
    return [path for path in _SRC.rglob("*.py") if "__pycache__" not in path.parts]


def test_no_write_sql_literals_under_src_ui_service() -> None:
    offenders: list[str] = []
    for path in _iter_python_files():
        text = path.read_text(encoding="utf-8")
        for match in _WRITE_STATEMENT_RE.finditer(text):
            offenders.append(f"{path}: {match.group(0)!r}")
    assert offenders == [], f"write-statement SQL literals found under src/ui_service/: {offenders}"
