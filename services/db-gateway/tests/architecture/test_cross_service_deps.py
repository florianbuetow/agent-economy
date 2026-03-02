"""Cross-service dependency enforcement tests.

These tests ensure this service does not import Python packages from
services it is not allowed to depend on, enforcing the architecture:

    Identity (8001)       <- no dependencies (leaf service)
    Central Bank (8002)   <- Identity
    Task Board (8003)     <- Identity, Central Bank
    Reputation (8004)     <- Identity
    Court (8005)          <- Identity, Task Board, Reputation, Central Bank
    DB Gateway (8007)     <- no upstream dependencies (infrastructure)
    Observatory (8006)    <- no upstream dependencies (infrastructure)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# All sibling service packages in the project
_ALL_SERVICE_PACKAGES = frozenset(
    {
        "identity_service",
        "central_bank_service",
        "task_board_service",
        "reputation_service",
        "court_service",
        "db_gateway_service",
        "observatory_service",
    }
)

# This service's own package name
_OWN_PACKAGE = "db_gateway_service"

# Packages this service is ALLOWED to import (empty for all services)
_ALLOWED_DEPENDENCIES: frozenset[str] = frozenset()

# Packages this service must NOT import
_FORBIDDEN_PACKAGES = _ALL_SERVICE_PACKAGES - {_OWN_PACKAGE} - _ALLOWED_DEPENDENCIES

# Resolve src directory
_SERVICE_SRC = Path(__file__).resolve().parents[2] / "src" / _OWN_PACKAGE


def _iter_python_files() -> list[Path]:
    """Return all .py files under src/, excluding __pycache__."""
    return [path for path in _SERVICE_SRC.rglob("*.py") if "__pycache__" not in path.parts]


def _extract_imported_packages(filepath: Path) -> set[str]:
    """Extract top-level package names from all imports in a Python file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()

    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                packages.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            packages.add(node.module.split(".")[0])
    return packages


@pytest.mark.architecture
class TestCrossServiceDependencies:
    """Enforce that this service does not import forbidden sibling services."""

    def test_no_forbidden_service_imports(self) -> None:
        """Source code must not import packages from services outside the allowed set."""
        violations: list[str] = []

        for py_file in _iter_python_files():
            imported = _extract_imported_packages(py_file)
            forbidden_hits = imported & _FORBIDDEN_PACKAGES
            if forbidden_hits:
                rel_path = py_file.relative_to(_SERVICE_SRC)
                for package in sorted(forbidden_hits):
                    violations.append(f"{rel_path} imports {package}")

        assert violations == [], (
            f"{_OWN_PACKAGE} must not import these service packages "
            f"(allowed: {sorted(_ALLOWED_DEPENDENCIES) or 'none'}). "
            f"Violations:\n" + "\n".join(f"  - {violation}" for violation in violations)
        )
