"""Architecture test: this service must not import the ``base_agent`` package.

PKI and platform agents live in ``libs/service-auth`` (package ``service_auth``)
after WP-02. Services depend on the shared library, never on the ``agents/``
application package, so this test forbids any ``base_agent`` import under ``src/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"


def _iter_python_files() -> list[Path]:
    return [path for path in _SRC.rglob("*.py") if "__pycache__" not in path.parts]


def _imports_base_agent(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "base_agent" or alias.name.startswith("base_agent."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "base_agent" or module.startswith("base_agent."):
                return True
    return False


@pytest.mark.unit
def test_no_base_agent_imports() -> None:
    offenders = [str(path) for path in _iter_python_files() if _imports_base_agent(path)]
    assert offenders == [], f"base_agent imports found (use service_auth instead): {offenders}"
