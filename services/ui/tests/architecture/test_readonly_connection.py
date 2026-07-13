"""Architecture guard for WP-08 item 6 (Q-4): the UI's direct SQLite connection

must stay strictly read-only.

The Q-4 decision (docs/plans/2026-07-10-q4-ui-read-path-decision.md) blesses
a direct aiosqlite connection to the shared economy.db as the sanctioned
"observatory pattern" exception to the no-direct-sql semgrep rule — on the
condition that the connection can never write. This test asserts the
connection URI ``lifespan.py`` opens carries ``mode=ro`` by construction, so
a future edit that drops the read-only flag (turning the UI into a second
writer of the gateway's database) fails CI instead of shipping silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

LIFESPAN_PATH = Path(__file__).resolve().parents[2] / "src" / "ui_service" / "core" / "lifespan.py"


def _find_db_uri_assignment(tree: ast.Module) -> ast.Assign | None:
    """Find the ``db_uri = ...`` assignment in the lifespan module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "db_uri" for target in node.targets
        ):
            return node
    return None


def test_db_uri_assignment_is_an_fstring_with_mode_ro_literal() -> None:
    """The db_uri f-string must contain the literal ``?mode=ro`` suffix."""
    tree = ast.parse(LIFESPAN_PATH.read_text())
    assignment = _find_db_uri_assignment(tree)
    assert assignment is not None, "lifespan.py must assign a db_uri variable"

    value = assignment.value
    assert isinstance(value, ast.JoinedStr), "db_uri must be built from an f-string"

    literal_parts = "".join(part.value for part in value.values if isinstance(part, ast.Constant))
    assert "?mode=ro" in literal_parts, (
        f"db_uri f-string must contain the literal '?mode=ro' suffix, "
        f"got literal parts: {literal_parts!r}"
    )


def test_connect_call_passes_uri_true() -> None:
    """aiosqlite.connect must be called with uri=True so mode=ro is honored."""
    tree = ast.parse(LIFESPAN_PATH.read_text())
    connect_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "connect"
    ]
    assert connect_calls, "lifespan.py must call aiosqlite.connect(...)"

    call = connect_calls[0]
    uri_kwargs = [kw for kw in call.keywords if kw.arg == "uri"]
    assert uri_kwargs, "aiosqlite.connect(...) must pass uri=True explicitly"
    assert isinstance(uri_kwargs[0].value, ast.Constant)
    assert uri_kwargs[0].value.value is True
