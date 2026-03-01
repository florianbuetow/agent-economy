"""Architecture test fixtures for court service."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestarch import EvaluableArchitecture, LayeredArchitecture, get_evaluable_architecture

_TESTS_DIR = Path(__file__).resolve().parent.parent
_SERVICE_ROOT = _TESTS_DIR.parent
_PKG_DIR = _SERVICE_ROOT / "src" / "court_service"
_REPORTS_DIR = _SERVICE_ROOT / "reports" / "architecture"


@pytest.fixture(scope="session")
def evaluable() -> EvaluableArchitecture:
    """Build the evaluable architecture graph for court_service."""
    return get_evaluable_architecture(str(_PKG_DIR), str(_PKG_DIR))


@pytest.fixture(scope="session")
def layered_arch() -> LayeredArchitecture:
    """Define the service's layered architecture.

    Layers will be populated once implementation exists.
    """
    return (
        LayeredArchitecture()
        .layer("routers")
        .containing_modules(["court_service.routers"])
        .layer("core")
        .containing_modules(["court_service.core"])
        .layer("services")
        .containing_modules(["court_service.services"])
    )


@pytest.fixture(scope="session")
def reports_dir() -> Path:
    """Ensure the architecture reports directory exists and return its path."""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return _REPORTS_DIR
