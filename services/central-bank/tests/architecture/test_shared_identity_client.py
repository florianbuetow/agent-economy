"""Architecture test: IdentityClient must come from shared library."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SERVICE_PACKAGE = "central_bank_service"
_SERVICE_SRC = Path(__file__).resolve().parents[2] / "src" / _SERVICE_PACKAGE


def _extract_imports(filepath: Path) -> list[tuple[str, str]]:
    """Extract (module, name) pairs from 'from X import Y' statements."""
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    results: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                results.append((node.module, alias.name))
    return results


@pytest.mark.architecture
class TestSharedIdentityClient:
    """IdentityClient must be imported from service_clients, not from local service package."""

    def test_lifespan_imports_shared_identity_client(self) -> None:
        """lifespan.py must import IdentityClient from service_clients.identity."""
        lifespan = _SERVICE_SRC / "core" / "lifespan.py"
        imports = _extract_imports(lifespan)

        shared_import = any(
            module == "service_clients.identity" and name == "IdentityClient"
            for module, name in imports
        )
        local_import = any(
            _SERVICE_PACKAGE in module and name == "IdentityClient" for module, name in imports
        )

        assert shared_import, "lifespan.py must import IdentityClient from service_clients.identity"
        assert not local_import, (
            "lifespan.py must NOT import IdentityClient from local service package"
        )

    def test_no_local_identity_client_module(self) -> None:
        """The service must not have its own identity_client.py in src/services/."""
        local_file = _SERVICE_SRC / "services" / "identity_client.py"
        assert not local_file.exists(), (
            f"Local identity_client.py must be removed: {local_file}. "
            "Use service_clients.identity.IdentityClient instead."
        )
