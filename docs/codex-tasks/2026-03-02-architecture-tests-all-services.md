# Architecture Tests for All Services — Complete Implementation Plan

> **Date**: 2026-03-02
> **Tickets**: agent-economy-xcmn (Tier 1), agent-economy-b4u6 (Tier 2), agent-economy-7m6v (Tier 3)
> **Reference implementation**: `services/identity/tests/architecture/test_architecture.py`

## Overview

The Identity service has a comprehensive architecture test suite (16 tests) using `pytestarch` that enforces import boundaries between layers. Six other services (central-bank, reputation, task-board, court, db-gateway, observatory) need the same treatment. UI is excluded because it lacks `pytestarch` in its dependencies.

The work is split into three tiers, executed sequentially. After each tier, run per-service CI to validate.

## CRITICAL RULES

1. **Do NOT use git** — there is no git remote configured. Do not run any `git` commands.
2. **Do NOT modify existing test files** — only create new files or populate empty stubs.
3. **Use `uv run` for all Python execution** — never `python`, `python3`, or `pip install`.
4. **Run `just ci-quiet` from the service directory** after completing each service to validate.
5. **All test functions must be decorated with `@pytest.mark.architecture`** (use a class decorator).
6. **Follow the identity service template exactly** — same class structure, same docstrings style, same Rule/LayerRule patterns.
7. **Read AGENTS.md first** — it contains project conventions you must follow.

## Reference: Identity Service Architecture Tests

The template file is at `services/identity/tests/architecture/test_architecture.py`. It contains:

### Class Structure
```
TestVisualization                    — 1 test  (generates module dependency graph PNG)
TestServicesLayerIndependence         — 4 tests (services must not import routers, app, middleware, schemas)
TestLeafModules                       — 6 tests (config/schemas must not import routers, services, core)
TestRouterConstraints                 — 3 tests (routers must not import config, app, lifespan)
TestLayeredArchitecture               — 2 tests (layer-level rules: services vs routers, services vs core)
```

### Pattern for Each Rule Test
```python
def test_<module>_must_not_import_<target>(
    self,
    evaluable: EvaluableArchitecture,
) -> None:
    """<Module> must not depend on <target>."""
    (
        Rule()
        .modules_that()
        .are_sub_modules_of("<pkg>.services")  # or .are_named("<pkg>.config")
        .should_not()
        .import_modules_that()
        .are_sub_modules_of("<pkg>.routers")  # or .are_named("<pkg>.app")
        .assert_applies(evaluable)
    )
```

### conftest.py Pattern
```python
from pytestarch import EvaluableArchitecture, LayeredArchitecture, get_evaluable_architecture

@pytest.fixture(scope="session")
def evaluable() -> EvaluableArchitecture:
    return get_evaluable_architecture(str(_PKG_DIR), str(_PKG_DIR))

@pytest.fixture(scope="session")
def layered_arch() -> LayeredArchitecture:
    return (
        LayeredArchitecture()
        .layer("routers").containing_modules(["<pkg>.routers"])
        .layer("core").containing_modules(["<pkg>.core"])
        .layer("services").containing_modules(["<pkg>.services"])
    )
```

## Module Structure Reference

Before writing tests, know what modules each service has:

### central-bank (`central_bank_service`)
- `app.py`, `config.py`, `schemas.py`, `logging.py`
- `core/`: `__init__.py`, `exceptions.py`, `lifespan.py`, `middleware.py`, `state.py`
- `routers/`: `__init__.py`, `accounts.py`, `escrow.py`, `health.py`, `helpers.py`
- `services/`: `__init__.py`, `identity_client.py`, `ledger.py`

### reputation (`reputation_service`)
- `app.py`, `config.py`, `schemas.py`, `logging.py`
- `core/`: `__init__.py`, `exceptions.py`, `lifespan.py`, `middleware.py`, `state.py`
- `routers/`: `__init__.py`, `feedback.py`, `health.py`
- `services/`: `__init__.py`, `feedback.py`, `feedback_store.py`, `identity_client.py`

### task-board (`task_board_service`)
- `app.py`, `config.py`, `schemas.py`, `logging.py`
- `core/`: `__init__.py`, `exceptions.py`, `lifespan.py`, `middleware.py`, `state.py`
- `routers/`: `__init__.py`, `assets.py`, `bids.py`, `health.py`, `tasks.py`, `validation.py`
- `services/`: `__init__.py`, `asset_manager.py`, `deadline_evaluator.py`, `escrow_coordinator.py`, `identity_client.py`, `task_manager.py`, `task_store.py`, `token_validator.py`
- `clients/`: `__init__.py`, `central_bank_client.py`, `platform_signer.py` ← **EXTRA LAYER**

### court (`court_service`)
- `app.py`, `config.py`, `schemas.py`, `logging.py`
- `core/`: `__init__.py`, `exceptions.py`, `lifespan.py`, `middleware.py`, `state.py`
- `routers/`: `__init__.py`, `disputes.py`, `health.py`, `validation.py`
- `services/`: `__init__.py`, `dispute_service.py`, `dispute_store.py`, `ruling_orchestrator.py`
- `judges/`: `__init__.py`, `base.py`, `llm_judge.py`, `prompts.py` ← **EXTRA LAYER**

### db-gateway (`db_gateway_service`)
- `app.py`, `config.py`, `schemas.py`, `logging.py`
- `core/`: `__init__.py`, `exceptions.py`, `lifespan.py`, `middleware.py`, `state.py`
- `routers/`: `__init__.py`, `bank.py`, `board.py`, `court.py`, `health.py`, `helpers.py`, `identity.py`, `reputation.py`
- `services/`: `__init__.py`, `db_writer.py`

### observatory (`observatory_service`)
- `app.py`, `config.py`, `schemas.py`, `logging.py`
- `core/`: `__init__.py`, `exceptions.py`, `lifespan.py`, `state.py` (no middleware.py)
- `routers/`: `__init__.py`, `agents.py`, `events.py`, `health.py`, `metrics.py`, `quarterly.py`, `tasks.py`
- `services/`: `__init__.py`, `agents.py`, `database.py`, `events.py`, `metrics.py`, `quarterly.py`, `tasks.py`

---

## TIER 1: central-bank and reputation (Ticket: agent-economy-xcmn)

These two services have the simplest structure (same as identity: routers, core, services — no extra layers). Their `tests/architecture/` directories already exist with `__init__.py`, `conftest.py` (correctly configured), and an empty `test_architecture.py` stub.

### Task 1A: Populate `services/central-bank/tests/architecture/test_architecture.py`

Replace the empty stub with a full test file. The package name is `central_bank_service`.

**File**: `services/central-bank/tests/architecture/test_architecture.py`

Write the following content (adapting identity's template):

```python
"""Architecture import rule tests for the Central Bank service.

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
            aliases={"central_bank_service": "bank_svc"},
        )
        ax.set_title("Central Bank Service — Module Dependency Graph")
        fig.tight_layout()

        output_path = reports_dir / "module_dependencies.png"
        fig.savefig(str(output_path), dpi=150)
        plt.close(fig)

        assert output_path.exists(), f"Visualization not saved to {output_path}"


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
            .are_sub_modules_of("central_bank_service.services")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("central_bank_service.routers")
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
            .are_sub_modules_of("central_bank_service.services")
            .should_not()
            .import_modules_that()
            .are_named("central_bank_service.app")
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
            .are_sub_modules_of("central_bank_service.services")
            .should_not()
            .import_modules_that()
            .are_named("central_bank_service.core.middleware")
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
            .are_sub_modules_of("central_bank_service.services")
            .should_not()
            .import_modules_that()
            .are_named("central_bank_service.schemas")
            .assert_applies(evaluable)
        )


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
            .are_named("central_bank_service.config")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("central_bank_service.routers")
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
            .are_named("central_bank_service.config")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("central_bank_service.services")
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
            .are_named("central_bank_service.config")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("central_bank_service.core")
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
            .are_named("central_bank_service.schemas")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("central_bank_service.services")
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
            .are_named("central_bank_service.schemas")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("central_bank_service.routers")
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
            .are_named("central_bank_service.schemas")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("central_bank_service.core")
            .assert_applies(evaluable)
        )


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
            .are_sub_modules_of("central_bank_service.routers")
            .should_not()
            .import_modules_that()
            .are_named("central_bank_service.config")
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
            .are_sub_modules_of("central_bank_service.routers")
            .should_not()
            .import_modules_that()
            .are_named("central_bank_service.app")
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
            .are_sub_modules_of("central_bank_service.routers")
            .should_not()
            .import_modules_that()
            .are_named("central_bank_service.core.lifespan")
            .assert_applies(evaluable)
        )


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
```

### Task 1B: Populate `services/reputation/tests/architecture/test_architecture.py`

Identical structure to central-bank but with package name `reputation_service` and alias `rep_svc`.

Replace every occurrence of `central_bank_service` with `reputation_service` and the visualization alias/title accordingly.

### Verification for Tier 1

After writing both files, run:
```bash
cd services/central-bank && just ci-quiet
cd ../../services/reputation && just ci-quiet
```

Both must pass with zero failures. If any architecture test fails, it means the service has an actual architecture violation — report the failure but do NOT modify the test. The test is correct; the violation needs to be investigated separately.

---

## TIER 2: task-board and court (Ticket: agent-economy-b4u6)

These services have extra layers beyond the standard routers/core/services:
- **task-board** has a `clients/` layer (HTTP clients for external services)
- **court** has a `judges/` layer (LLM judge implementations)

### Task 2A: Populate `services/task-board/tests/architecture/test_architecture.py`

Same base 16 tests as Tier 1, but with package name `task_board_service` and these **additional rules** for the `clients/` layer:

**Extra test class to add:**

```python
@pytest.mark.architecture
class TestClientsLayerIndependence:
    """The clients/ layer provides HTTP clients and must not depend on routers or app."""

    def test_clients_must_not_import_routers(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """HTTP clients must not depend on HTTP routing."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("task_board_service.clients")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("task_board_service.routers")
            .assert_applies(evaluable)
        )

    def test_clients_must_not_import_app(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """HTTP clients must not depend on the application factory."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("task_board_service.clients")
            .should_not()
            .import_modules_that()
            .are_named("task_board_service.app")
            .assert_applies(evaluable)
        )

    def test_clients_must_not_import_schemas(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """HTTP clients must not depend on Pydantic HTTP schemas."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("task_board_service.clients")
            .should_not()
            .import_modules_that()
            .are_named("task_board_service.schemas")
            .assert_applies(evaluable)
        )

    def test_routers_must_not_import_clients(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Routers must not directly use HTTP clients; services layer mediates."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("task_board_service.routers")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("task_board_service.clients")
            .assert_applies(evaluable)
        )
```

Also update the `conftest.py` layered_arch fixture to include the clients layer:
```python
# NOTE: Do NOT modify conftest.py — it already exists.
# BUT — if the layered_arch fixture does not include "clients", you need to add it.
# Check the existing conftest.py first. If it only has routers/core/services, add:
#   .layer("clients").containing_modules(["task_board_service.clients"])
# after the "services" layer.
```

**IMPORTANT**: Check `services/task-board/tests/architecture/conftest.py` first. If it already has a `layered_arch` fixture with only 3 layers (routers, core, services), you need to add the clients layer. If it does NOT have a layered_arch fixture at all, create one following the identity pattern plus the clients layer.

The visualization alias should be `{"task_board_service": "tb_svc"}` and title `"Task Board Service — Module Dependency Graph"`.

### Task 2B: Populate `services/court/tests/architecture/test_architecture.py`

Same base 16 tests as Tier 1, but with package name `court_service` and these **additional rules** for the `judges/` layer:

**Extra test class to add:**

```python
@pytest.mark.architecture
class TestJudgesLayerIndependence:
    """The judges/ layer provides LLM judge implementations and must not depend on routers or app."""

    def test_judges_must_not_import_routers(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Judge implementations must not depend on HTTP routing."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("court_service.judges")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("court_service.routers")
            .assert_applies(evaluable)
        )

    def test_judges_must_not_import_app(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Judge implementations must not depend on the application factory."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("court_service.judges")
            .should_not()
            .import_modules_that()
            .are_named("court_service.app")
            .assert_applies(evaluable)
        )

    def test_judges_must_not_import_schemas(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Judge implementations must not depend on Pydantic HTTP schemas."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("court_service.judges")
            .should_not()
            .import_modules_that()
            .are_named("court_service.schemas")
            .assert_applies(evaluable)
        )

    def test_routers_must_not_import_judges(
        self,
        evaluable: EvaluableArchitecture,
    ) -> None:
        """Routers must not directly use judge implementations; services layer mediates."""
        (
            Rule()
            .modules_that()
            .are_sub_modules_of("court_service.routers")
            .should_not()
            .import_modules_that()
            .are_sub_modules_of("court_service.judges")
            .assert_applies(evaluable)
        )
```

Also check/update the `conftest.py` layered_arch fixture to include judges layer if not already present.

The visualization alias should be `{"court_service": "court_svc"}` and title `"Court Service — Module Dependency Graph"`.

### Verification for Tier 2

After writing both files, run:
```bash
cd services/task-board && just ci-quiet
cd ../../services/court && just ci-quiet
```

Both must pass with zero failures.

---

## TIER 3: db-gateway and observatory (Ticket: agent-economy-7m6v)

These services do **NOT** have existing `tests/architecture/` directories. You must create the full directory structure.

### Task 3A: Create architecture tests for db-gateway

Create these files:

#### `services/db-gateway/tests/architecture/__init__.py`
```python
```
(empty file)

#### `services/db-gateway/tests/architecture/conftest.py`
```python
"""Architecture test fixtures for db-gateway service."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestarch import EvaluableArchitecture, LayeredArchitecture, get_evaluable_architecture

_TESTS_DIR = Path(__file__).resolve().parent.parent
_SERVICE_ROOT = _TESTS_DIR.parent
_PKG_DIR = _SERVICE_ROOT / "src" / "db_gateway_service"
_REPORTS_DIR = _SERVICE_ROOT / "reports" / "architecture"


@pytest.fixture(scope="session")
def evaluable() -> EvaluableArchitecture:
    """Build the evaluable architecture graph for db_gateway_service."""
    return get_evaluable_architecture(str(_PKG_DIR), str(_PKG_DIR))


@pytest.fixture(scope="session")
def layered_arch() -> LayeredArchitecture:
    """Define the service's layered architecture."""
    return (
        LayeredArchitecture()
        .layer("routers")
        .containing_modules(["db_gateway_service.routers"])
        .layer("core")
        .containing_modules(["db_gateway_service.core"])
        .layer("services")
        .containing_modules(["db_gateway_service.services"])
    )


@pytest.fixture(scope="session")
def reports_dir() -> Path:
    """Ensure the architecture reports directory exists and return its path."""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return _REPORTS_DIR
```

#### `services/db-gateway/tests/architecture/test_architecture.py`

Same 16-test template as Tier 1 with package name `db_gateway_service`, alias `{"db_gateway_service": "dbgw_svc"}`, title `"DB Gateway Service — Module Dependency Graph"`.

### Task 3B: Create architecture tests for observatory

Create these files:

#### `services/observatory/tests/architecture/__init__.py`
```python
```
(empty file)

#### `services/observatory/tests/architecture/conftest.py`
```python
"""Architecture test fixtures for observatory service."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestarch import EvaluableArchitecture, LayeredArchitecture, get_evaluable_architecture

_TESTS_DIR = Path(__file__).resolve().parent.parent
_SERVICE_ROOT = _TESTS_DIR.parent
_PKG_DIR = _SERVICE_ROOT / "src" / "observatory_service"
_REPORTS_DIR = _SERVICE_ROOT / "reports" / "architecture"


@pytest.fixture(scope="session")
def evaluable() -> EvaluableArchitecture:
    """Build the evaluable architecture graph for observatory_service."""
    return get_evaluable_architecture(str(_PKG_DIR), str(_PKG_DIR))


@pytest.fixture(scope="session")
def layered_arch() -> LayeredArchitecture:
    """Define the service's layered architecture."""
    return (
        LayeredArchitecture()
        .layer("routers")
        .containing_modules(["observatory_service.routers"])
        .layer("core")
        .containing_modules(["observatory_service.core"])
        .layer("services")
        .containing_modules(["observatory_service.services"])
    )


@pytest.fixture(scope="session")
def reports_dir() -> Path:
    """Ensure the architecture reports directory exists and return its path."""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return _REPORTS_DIR
```

#### `services/observatory/tests/architecture/test_architecture.py`

Same 16-test template as Tier 1 with package name `observatory_service`, alias `{"observatory_service": "obs_svc"}`, title `"Observatory Service — Module Dependency Graph"`.

**NOTE**: Observatory does NOT have `core/middleware.py`. The `test_services_must_not_import_middleware` test should still be included — it will simply pass (no middleware to import). This is correct behavior.

### Verification for Tier 3

After creating all files, run:
```bash
cd services/db-gateway && just ci-quiet
cd ../../services/observatory && just ci-quiet
```

Both must pass with zero failures.

---

## Final Verification

After all three tiers are complete, run the full project CI:
```bash
cd /Users/flo/Developer/github/agent-economy
just ci-all-quiet
```

This is the definitive validation. All services must pass.

---

## Execution Checklist

For each service, the agent must:
1. Read the existing `tests/architecture/conftest.py` (if it exists) to understand the fixtures
2. Read `services/identity/tests/architecture/test_architecture.py` as the reference template
3. Write the new `test_architecture.py` (or create the full directory for Tier 3)
4. Run `cd services/<name> && just ci-quiet` to validate
5. Report the result (pass/fail and any failures)

## Split Between Agents

- **codex session** (`codex:0.0`): Tier 1 (central-bank, reputation) + Tier 2 (task-board, court)
- **codingagent session** (`codingagent:0.0`): Tier 3 (db-gateway, observatory)

Both can work in parallel since the tiers don't actually have code dependencies (the ticket dependencies are just for tracking order). The services are independent.
