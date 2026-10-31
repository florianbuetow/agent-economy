"""Architecture test: IdentityClient must come from shared library."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SERVICE_PACKAGE = "reputation_service"
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

    def test_platform_verifier_uses_composition_not_inheritance(self) -> None:
        """The platform JWS verifier implements the JwsVerifier protocol via composition,
        never by subclassing the shared IdentityClient (WP-03.2; ratified exception #5).

        The old inheritance never called ``super().__init__`` and left the HTTP-client
        fields unset — a latent AttributeError. Composition removes that class of bug.
        """
        platform_file = _SERVICE_SRC / "services" / "platform_identity_client.py"
        tree = ast.parse(platform_file.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                base_names = {base.id for base in node.bases if isinstance(base, ast.Name)} | {
                    base.attr for base in node.bases if isinstance(base, ast.Attribute)
                }
                assert "IdentityClient" not in base_names, (
                    f"{node.name} must not subclass IdentityClient; use JwsVerifier composition"
                )

        imports = _extract_imports(platform_file)
        assert not any(name == "IdentityClient" for _module, name in imports), (
            "platform_identity_client.py must not import IdentityClient (composition, not "
            "inheritance)"
        )
