"""Architecture test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestarch import EvaluableArchitecture, LayeredArchitecture, get_evaluable_architecture

# Resolve paths relative to this file:
#   tests/architecture/conftest.py -> tests/ -> services/identity/
_TESTS_DIR = Path(__file__).resolve().parent.parent
_SERVICE_ROOT = _TESTS_DIR.parent
_IDENTITY_PKG = _SERVICE_ROOT / "src" / "identity_service"
_REPORTS_DIR = _SERVICE_ROOT / "reports" / "architecture"


@pytest.fixture(scope="session")
def evaluable() -> EvaluableArchitecture:
    """Build the evaluable architecture graph for identity_service.

    Uses identity_service package as both root and module path
    so module names are clean (e.g. 'identity_service.routers.agents').
    """
    return get_evaluable_architecture(str(_IDENTITY_PKG), str(_IDENTITY_PKG))


@pytest.fixture(scope="session")
def layered_arch() -> LayeredArchitecture:
    """Define the service's layered architecture.

    Layers (top to bottom):
        routers   - HTTP endpoint handlers (thin wrappers)
        core      - App state, lifespan, middleware, exceptions
        services  - Business logic (no FastAPI imports)
    """
    return (
        LayeredArchitecture()
        .layer("routers")
        .containing_modules(["identity_service.routers"])
        .layer("core")
        .containing_modules(["identity_service.core"])
        .layer("services")
        .containing_modules(["identity_service.services"])
    )


@pytest.fixture(scope="session")
def reports_dir() -> Path:
    """Ensure the architecture reports directory exists and return its path."""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return _REPORTS_DIR
