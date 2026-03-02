# Architecture Tests: CI Wiring + Cross-Service Dependency Enforcement

## Overview

Two tasks:
1. Wire existing `tests/architecture/` into the CI pipeline for all 7 services
2. Add cross-service dependency rules that enforce the documented service dependency graph

## Important Rules

- Do NOT use git commands (no git add, commit, push, etc.) — this project has no git repo
- Use `uv run` for all Python execution — never use raw `python` or `pip install`
- Do NOT modify existing test files — add new test files only
- Run `just ci-quiet` from each service directory after making changes to verify

## Service List and Package Names

| Service Dir     | Package Name             | Port | Allowed Dependencies                                      |
|-----------------|--------------------------|------|------------------------------------------------------------|
| identity        | identity_service         | 8001 | none (leaf service)                                        |
| central-bank    | central_bank_service     | 8002 | identity_service                                           |
| task-board      | task_board_service       | 8003 | identity_service, central_bank_service                     |
| reputation      | reputation_service       | 8004 | identity_service                                           |
| court           | court_service            | 8005 | identity_service, task_board_service, reputation_service, central_bank_service |
| db-gateway      | db_gateway_service       | 8007 | none (infrastructure service)                              |
| observatory     | observatory_service      | 8006 | none (read-only infrastructure)                            |

## Tier 1: Wire Architecture Tests Into CI

### What to change

For each of the 7 services listed above, edit the `justfile` in `services/<service-dir>/justfile`.

### Step 1: Add a `test-architecture` recipe

Add this recipe right after the existing `test-unit` recipe (around line 334-338 in most justfiles). Find the line that says `# Run integration tests only` and insert the new recipe BEFORE it:

```just
# Run architecture tests only
test-architecture:
    @echo ""
    @printf "\033[0;34m=== Running Architecture Tests ===\033[0m\n"
    @uv run pytest tests/architecture -m architecture -v
    @echo ""
```

### Step 2: Add `test-architecture` to the `ci` recipe

In the `ci` recipe (search for `# Run ALL validation checks (verbose)`), add `just test-architecture` right after `just test-unit`. The CI recipe section should look like:

```
    just test-unit
    just test-architecture
    just code-lspchecks
```

### Step 3: Add `test-architecture` to the `ci-quiet` recipe

In the `ci-quiet` recipe (search for `# Run ALL validation checks silently`), add these two lines right after the `just test-unit` quiet block:

```bash
    just test-architecture > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Architecture tests failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Architecture tests passed\033[0m\n"
```

Place these two lines right after:
```bash
    just test-unit > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Unit tests failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Unit tests passed\033[0m\n"
```

### Step 4: Add `test-architecture` to the `test` recipe

Find the `test` recipe (the one that runs both unit and integration tests). It typically looks like:

```just
test:
    #!/usr/bin/env bash
    set -e
    ...
    just test-unit
    just test-integration
    ...
```

Add `just test-architecture` after `just test-unit`:

```
    just test-unit
    just test-architecture
    just test-integration
```

### Step 5: Add the `test-architecture` help text

In the help recipe at the top of the justfile, find the test section that lists `test-unit` and `test-integration`. Add a line for `test-architecture` between them:

```
    @printf "  \033[0;37mjust test-architecture\033[0;34m Run architecture tests only\033[0m\n"
```

### Verification for Tier 1

After editing ALL 7 justfiles, run from each service directory:

```bash
cd services/identity && just test-architecture
cd services/central-bank && just test-architecture
cd services/task-board && just test-architecture
cd services/reputation && just test-architecture
cd services/court && just test-architecture
cd services/db-gateway && just test-architecture
cd services/observatory && just test-architecture
```

ALL of these must pass. If any fail, investigate and fix before moving to Tier 2.

Then run the full CI for each:

```bash
cd services/identity && just ci-quiet
cd services/central-bank && just ci-quiet
cd services/task-board && just ci-quiet
cd services/reputation && just ci-quiet
cd services/court && just ci-quiet
cd services/db-gateway && just ci-quiet
cd services/observatory && just ci-quiet
```

## Tier 2: Add Cross-Service Dependency Rules

For each service, create a NEW test file at `tests/architecture/test_cross_service_deps.py`. Do NOT modify existing test files.

The approach uses AST-based import scanning (same pattern as the existing `test_gateway_constraint.py` files). This is more reliable than pytestarch for cross-package detection because pytestarch only evaluates imports within its configured package root.

### Template for the test file

Every service gets a file `tests/architecture/test_cross_service_deps.py` using this template. Adapt `SERVICE_PKG`, `SERVICE_DIR_NAME`, and `FORBIDDEN_PACKAGES` per the table above.

```python
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

# --- Configuration: adapt these per service ---

# All sibling service packages in the project
_ALL_SERVICE_PACKAGES = frozenset({
    "identity_service",
    "central_bank_service",
    "task_board_service",
    "reputation_service",
    "court_service",
    "db_gateway_service",
    "observatory_service",
})

# This service's own package name
_OWN_PACKAGE = "<SERVICE_PKG>"

# Packages this service is ALLOWED to import (empty for leaf services)
_ALLOWED_DEPENDENCIES: frozenset[str] = frozenset({
    # <fill per service, e.g. "identity_service", or leave empty>
})

# Packages this service must NOT import
_FORBIDDEN_PACKAGES = _ALL_SERVICE_PACKAGES - {_OWN_PACKAGE} - _ALLOWED_DEPENDENCIES

# Resolve src directory
_SERVICE_SRC = Path(__file__).resolve().parents[2] / "src" / _OWN_PACKAGE


def _iter_python_files() -> list[Path]:
    """Return all .py files under src/, excluding __pycache__."""
    return [p for p in _SERVICE_SRC.rglob("*.py") if "__pycache__" not in p.parts]


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
                for pkg in sorted(forbidden_hits):
                    violations.append(f"{rel_path} imports {pkg}")

        assert violations == [], (
            f"{_OWN_PACKAGE} must not import these service packages "
            f"(allowed: {sorted(_ALLOWED_DEPENDENCIES) or 'none'}). "
            f"Violations:\n" + "\n".join(f"  - {v}" for v in violations)
        )
```

### Per-service values

Create 7 files, one per service. For each, replace `_OWN_PACKAGE` and `_ALLOWED_DEPENDENCIES`:

**services/identity/tests/architecture/test_cross_service_deps.py**
```python
_OWN_PACKAGE = "identity_service"
_ALLOWED_DEPENDENCIES: frozenset[str] = frozenset()
# identity is a leaf service — no sibling imports allowed
```

**services/central-bank/tests/architecture/test_cross_service_deps.py**
```python
_OWN_PACKAGE = "central_bank_service"
_ALLOWED_DEPENDENCIES: frozenset[str] = frozenset()
# central-bank calls identity via HTTP, but never imports its Python package
```

**services/task-board/tests/architecture/test_cross_service_deps.py**
```python
_OWN_PACKAGE = "task_board_service"
_ALLOWED_DEPENDENCIES: frozenset[str] = frozenset()
# task-board calls identity and central-bank via HTTP, but never imports their Python packages
```

**services/reputation/tests/architecture/test_cross_service_deps.py**
```python
_OWN_PACKAGE = "reputation_service"
_ALLOWED_DEPENDENCIES: frozenset[str] = frozenset()
# reputation calls identity via HTTP, but never imports its Python package
```

**services/court/tests/architecture/test_cross_service_deps.py**
```python
_OWN_PACKAGE = "court_service"
_ALLOWED_DEPENDENCIES: frozenset[str] = frozenset()
# court calls identity, task-board, reputation, central-bank via HTTP only
```

**services/db-gateway/tests/architecture/test_cross_service_deps.py**
```python
_OWN_PACKAGE = "db_gateway_service"
_ALLOWED_DEPENDENCIES: frozenset[str] = frozenset()
# db-gateway is infrastructure — no sibling imports
```

**services/observatory/tests/architecture/test_cross_service_deps.py**
```python
_OWN_PACKAGE = "observatory_service"
_ALLOWED_DEPENDENCIES: frozenset[str] = frozenset()
# observatory is read-only infrastructure — no sibling imports
```

**IMPORTANT**: Notice that `_ALLOWED_DEPENDENCIES` is empty for ALL services. This is correct because services communicate via HTTP, not Python imports. Even though Court depends on Identity at the HTTP level, it should never `import identity_service`. The cross-service dependency rules enforce import isolation, not HTTP communication rules.

### Verification for Tier 2

After creating all 7 files, run architecture tests for each service:

```bash
cd services/identity && just test-architecture
cd services/central-bank && just test-architecture
cd services/task-board && just test-architecture
cd services/reputation && just test-architecture
cd services/court && just test-architecture
cd services/db-gateway && just test-architecture
cd services/observatory && just test-architecture
```

ALL must pass. Then run the full CI for each:

```bash
cd services/identity && just ci-quiet
cd services/central-bank && just ci-quiet
cd services/task-board && just ci-quiet
cd services/reputation && just ci-quiet
cd services/court && just ci-quiet
cd services/db-gateway && just ci-quiet
cd services/observatory && just ci-quiet
```

## Final Verification

After both tiers are complete, run the full project CI from the project root:

```bash
cd /Users/flo/Developer/github/agent-economy && just ci-quiet
```

This is the definitive gate. Everything must pass.

## File Summary

Files to EDIT (not create):
- `services/identity/justfile`
- `services/central-bank/justfile`
- `services/task-board/justfile`
- `services/reputation/justfile`
- `services/court/justfile`
- `services/db-gateway/justfile`
- `services/observatory/justfile`

Files to CREATE:
- `services/identity/tests/architecture/test_cross_service_deps.py`
- `services/central-bank/tests/architecture/test_cross_service_deps.py`
- `services/task-board/tests/architecture/test_cross_service_deps.py`
- `services/reputation/tests/architecture/test_cross_service_deps.py`
- `services/court/tests/architecture/test_cross_service_deps.py`
- `services/db-gateway/tests/architecture/test_cross_service_deps.py`
- `services/observatory/tests/architecture/test_cross_service_deps.py`
