"""Architecture import rule tests for the Identity service.

These tests enforce the project's architectural boundaries:
- Business logic (services/) is independent of HTTP framework
- Routers are thin wrappers that don't contain business logic imports from wrong layers
- The service layer does not depend on routers or the app module
- Config and schemas remain leaf-like modules

See: docs/service-implementation-guide.md for the intended architecture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import matplotlib
import matplotlib.pyplot as plt
import pytest
from pytestarch import EvaluableArchitecture, LayeredArchitecture, LayerRule, Rule

if TYPE_CHECKING:
    from pathlib import Path

matplotlib.use("Agg")

# ---------------------------------------------------------------------------
# Visualization: generate dependency graph into reports/architecture/
# ---------------------------------------------------------------------------


@pytest.mark.architecture
class TestVisualization:
    """Generate architecture visualizations into the service reports directory."""

    def test_generate_module_dependency_graph(
        self,
        evaluable: EvaluableArchitecture,
        reports_dir: Path,
    ) -> None:
        """Generate a full module dependency graph as PNG."""
        fig, ax = plt.subplots(figsize=(16, 12))
        evaluable.visualize(
            ax=ax,
            spacing=2.5,
            node_size=2000,
            font_size=8,
            arrows=True,
            with_labels=True,
            aliases={"identity_service": "id_svc"},
        )
        ax.set_title("Identity Service — Module Dependency Graph")
        fig.tight_layout()

        output_path = reports_dir / "module_dependencies.png"
        fig.savefig(str(output_path), dpi=150)
        plt.close(fig)

        assert output_path.exists(), f"Visualization not saved to {output_path}"


# ---------------------------------------------------------------------------
# Module-level rules: services layer independence
# ---------------------------------------------------------------------------


@pytest.mark.architecture
class TestServicesLayerIndependence:
    """The services/ layer contains pure business logic with no framework imports."""

    def test_services_must_not_import_routers(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Business logic must not depend on HTTP routing."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("identity_service.services")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("identity_service.routers")
            .assert_applies(evaluable)
        )

    def test_services_must_not_import_app(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Business logic must not depend on the application factory."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("identity_service.services")
            .should_not()
            .import_modules_that()
            .are_named("identity_service.app")
            .assert_applies(evaluable)
        )

    def test_services_must_not_import_middleware(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Business logic must not depend on ASGI middleware."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("identity_service.services")
            .should_not()
            .import_modules_that()
            .are_named("identity_service.core.middleware")
            .assert_applies(evaluable)
        )

    def test_services_must_not_import_schemas(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Business logic must not depend on Pydantic HTTP schemas."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("identity_service.services")
            .should_not()
            .import_modules_that()
            .are_named("identity_service.schemas")
            .assert_applies(evaluable)
        )


# ---------------------------------------------------------------------------
# Module-level rules: config and schemas are leaf modules
# ---------------------------------------------------------------------------


@pytest.mark.architecture
class TestLeafModules:
    """Config and schemas should not depend on service internals."""

    def test_config_must_not_import_routers(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Configuration loading must not depend on routers."""
        (
            Rule()
            .modules_that()
            .are_named("identity_service.config")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("identity_service.routers")
            .assert_applies(evaluable)
        )

    def test_config_must_not_import_services(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Configuration loading must not depend on business logic."""
        (
            Rule()
            .modules_that()
            .are_named("identity_service.config")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("identity_service.services")
            .assert_applies(evaluable)
        )

    def test_config_must_not_import_core(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Configuration loading must not depend on core infrastructure."""
        (
            Rule()
            .modules_that()
            .are_named("identity_service.config")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("identity_service.core")
            .assert_applies(evaluable)
        )

    def test_schemas_must_not_import_services(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """HTTP schemas must not depend on business logic."""
        (
            Rule()
            .modules_that()
            .are_named("identity_service.schemas")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("identity_service.services")
            .assert_applies(evaluable)
        )

    def test_schemas_must_not_import_routers(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """HTTP schemas must not depend on routers."""
        (
            Rule()
            .modules_that()
            .are_named("identity_service.schemas")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("identity_service.routers")
            .assert_applies(evaluable)
        )

    def test_schemas_must_not_import_core(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """HTTP schemas must not depend on core infrastructure."""
        (
            Rule()
            .modules_that()
            .are_named("identity_service.schemas")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("identity_service.core")
            .assert_applies(evaluable)
        )


# ---------------------------------------------------------------------------
# Module-level rules: routers are thin wrappers
# ---------------------------------------------------------------------------


@pytest.mark.architecture
class TestRouterConstraints:
    """Routers should only depend on state, schemas, and exceptions — not services directly."""

    def test_routers_must_not_import_config_directly(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Routers must not read configuration directly; config flows via AppState."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("identity_service.routers")
            .should_not()
            .import_modules_that()
            .are_named("identity_service.config")
            .assert_applies(evaluable)
        )

    def test_routers_must_not_import_app(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Routers must not import the application factory (circular dependency)."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("identity_service.routers")
            .should_not()
            .import_modules_that()
            .are_named("identity_service.app")
            .assert_applies(evaluable)
        )

    def test_routers_must_not_import_lifespan(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Routers must not depend on lifecycle management."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("identity_service.routers")
            .should_not()
            .import_modules_that()
            .are_named("identity_service.core.lifespan")
            .assert_applies(evaluable)
        )


# ---------------------------------------------------------------------------
# Layer-level rules
# ---------------------------------------------------------------------------


@pytest.mark.architecture
class TestLayeredArchitecture:
    """Layer-level dependency rules enforcing the service architecture."""

    def test_services_layer_must_not_access_routers_layer(
        self,
        evaluable: EvaluableArchitecture,
        layered_arch: LayeredArchitecture,
    ) -> None:
        """Business logic layer must not access the HTTP routing layer."""
        (
            LayerRule()
            .based_on(layered_arch)
            .layers_that()
            .are_named("services")
            .should_not()
            .access_layers_that()
            .are_named("routers")
            .assert_applies(evaluable)
        )

    def test_services_layer_must_not_access_core_layer(
        self,
        evaluable: EvaluableArchitecture,
        layered_arch: LayeredArchitecture,
    ) -> None:
        """Business logic layer must not depend on core infrastructure."""
        (
            LayerRule()
            .based_on(layered_arch)
            .layers_that()
            .are_named("services")
            .should_not()
            .access_layers_that()
            .are_named("core")
            .assert_applies(evaluable)
        )
