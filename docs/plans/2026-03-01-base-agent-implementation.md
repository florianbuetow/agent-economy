# Base Agent — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create the `agents/` package with a `BaseAgent` class that provides a programmable Python client for the Agent Task Economy platform, with Strands `@tool`-decorated methods for future LLM control.

**Architecture:** Mixin composition — one `BaseAgent` class composes service-specific mixins (`IdentityMixin`, `BankMixin`, `TaskBoardMixin`, `ReputationMixin`, `CourtMixin`). Cross-cutting concerns (signing, HTTP, config) live on the base class. Each mixin is a separate file.

**Tech Stack:** Python 3.12, httpx (async HTTP), cryptography (Ed25519), pydantic (config), strands-agents (@tool decorator), service-commons (config loader), PyYAML (roster)

**Design doc:** `docs/plans/2026-03-01-base-agent-design.md`

---

## Files to Read FIRST

Read these before doing anything:

1. `AGENTS.md` — project conventions, architecture, testing rules
2. `docs/plans/2026-03-01-base-agent-design.md` — the design document for this work
3. `libs/service-commons/src/service_commons/config.py` — shared config loading pattern
4. `services/identity/src/identity_service/config.py` — reference config.py implementation
5. `services/identity/config.yaml` — reference config.yaml structure
6. `services/identity/pyproject.toml` — reference pyproject.toml structure
7. `services/identity/justfile` — reference justfile structure

## Global Rules

- Use `uv run` for ALL Python execution — never raw `python`, `python3`, or `pip install`
- **Never use default parameter values** for configurable settings
- **All config comes from config.yaml** — no hardcoded values
- Every Pydantic model uses `ConfigDict(extra="forbid")`
- Do NOT modify files in `libs/service-commons/`
- Working directory for all commands: `agents/` (unless stated otherwise)
- All runtime data (keys, etc.) goes in the project-root `data/` directory
- After every file creation/modification, run: `cd agents && uv run ruff check src/ && uv run ruff format --check src/`

## Implementation Phases

Execute these in order.

| Phase | What It Does |
|-------|-------------|
| 1 | Project scaffolding: pyproject.toml, justfile, config.yaml, roster.yaml |
| 2 | Config and signing: config.py, signing.py, __init__.py |
| 3 | BaseAgent skeleton: agent.py with internals and mixin stubs |
| 4 | Unit tests for signing and config |
| 5 | Verification: CI passes, imports work |

---

## Phase 1 — Project Scaffolding

### Working Directory

All commands run from the project root initially, then `agents/` after creation.

### Step 1.1: Create directory structure

```bash
mkdir -p agents/src/base_agent/mixins
mkdir -p agents/tests/unit
mkdir -p data/keys
```

### Step 1.2: Create pyproject.toml

Create `agents/pyproject.toml` with this exact content:

```toml
[project]
name = "base-agent"
version = "0.1.0"
description = "Programmable Python client for the Agent Task Economy platform"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.28.0",
    "cryptography>=44.0.0",
    "pydantic>=2.10.0",
    "strands-agents>=0.1.0",
    "service-commons",
    "pyyaml>=6.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=6.0.0",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.8.0",
    "mypy>=1.13.0",
    "pyright>=1.1.390",
    "bandit>=1.7.0",
    "deptry>=0.21.0",
    "codespell>=2.3.0",
    "types-PyYAML>=6.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv.sources]
service-commons = { path = "../libs/service-commons", editable = true }

[tool.hatch.build.targets.wheel]
packages = ["src/base_agent"]

# === Ruff ===
[tool.ruff]
target-version = "py312"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = [
    "E",      # pycodestyle errors
    "W",      # pycodestyle warnings
    "F",      # Pyflakes
    "I",      # isort
    "B",      # flake8-bugbear
    "C4",     # flake8-comprehensions
    "UP",     # pyupgrade
    "ARG",    # flake8-unused-arguments
    "SIM",    # flake8-simplify
    "TCH",    # flake8-type-checking
    "PTH",    # flake8-use-pathlib
    "ERA",    # eradicate (commented out code)
    "PL",     # pylint
    "RUF",    # Ruff-specific rules
]
ignore = [
    "PLR0913",  # Too many arguments
    "PLR2004",  # Magic value comparison
]

[tool.ruff.lint.isort]
known-first-party = ["base_agent"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
skip-magic-trailing-comma = false
line-ending = "auto"

# === Mypy ===
[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_ignores = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
disallow_untyped_decorators = true
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_configs = true
show_error_codes = true
files = ["src"]

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false

[[tool.mypy.overrides]]
module = "strands.*"
ignore_missing_imports = true

# === Bandit ===
[tool.bandit]
exclude_dirs = ["tests", ".venv"]
skips = ["B101"]

# === Pytest ===
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
asyncio_mode = "auto"
addopts = "-v --tb=short --strict-markers"
markers = [
    "unit: Unit tests (fast, isolated, no external dependencies)",
    "integration: Integration tests (require running services)",
]

# === Coverage ===
[tool.coverage.run]
source = ["src"]
branch = true
omit = ["tests/*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
    "if __name__ == .__main__.:",
]
fail_under = 80
show_missing = true

# === Deptry ===
[tool.deptry]
extend_exclude = [".venv", "tests"]

[tool.deptry.per_rule_ignores]
DEP002 = [
    "pytest",
    "pytest-cov",
    "pytest-asyncio",
    "ruff",
    "mypy",
    "pyright",
    "bandit",
    "deptry",
    "codespell",
    "types-PyYAML",
]
```

**IMPORTANT:** If `strands-agents` is not available on PyPI or fails to install, remove it from `dependencies` and proceed without the `@tool` decorator. The methods will still work as regular Python methods. You can add the decorator later when the package is available. If this happens, also skip importing `tool` from `strands` in all files — just define plain `async def` methods.

### Step 1.3: Create pyrightconfig.json

Create `agents/pyrightconfig.json`:

```json
{
  "include": ["src"],
  "exclude": ["**/__pycache__", ".venv", "tests"],
  "pythonVersion": "3.12",
  "typeCheckingMode": "strict",
  "reportMissingImports": true,
  "reportMissingTypeStubs": false,
  "reportUnknownMemberType": false,
  "reportUnknownArgumentType": false,
  "reportUnknownVariableType": false,
  "reportUnknownParameterType": false,
  "reportUnusedImport": true,
  "reportPrivateUsage": false,
  "venvPath": ".",
  "venv": ".venv"
}
```

### Step 1.4: Create config.yaml

Create `agents/config.yaml`:

```yaml
# Base Agent Configuration
# Environment variable overrides use prefix: AGENT__

platform:
  identity_url: "http://localhost:8001"
  bank_url: "http://localhost:8002"
  task_board_url: "http://localhost:8003"
  reputation_url: "http://localhost:8004"
  court_url: "http://localhost:8005"

data:
  keys_dir: "../data/keys"
  roster_path: "roster.yaml"
```

### Step 1.5: Create roster.yaml

Create `agents/roster.yaml`:

```yaml
# Agent Roster — maps handles to names and types
# Keys are stored at: data/keys/{handle}.key and data/keys/{handle}.pub

agents:
  alice:
    name: "Alice"
    type: "worker"
  bob:
    name: "Bob"
    type: "worker"
```

### Step 1.6: Create justfile

Create `agents/justfile`:

```just
# Default recipe: show available commands
_default:
    @just help

# Show help information
help:
    @clear
    @echo ""
    @printf "\033[0;34m=== Base Agent ===\033[0m\n"
    @echo ""
    @printf "\033[1;33mSetup\033[0m\n"
    @printf "  \033[0;37mjust init             \033[0;34m Initialize development environment\033[0m\n"
    @printf "  \033[0;37mjust destroy          \033[0;34m Remove virtual environment\033[0m\n"
    @echo ""
    @printf "\033[1;33mCode Quality\033[0m\n"
    @printf "  \033[0;37mjust code-format      \033[0;34m Auto-fix formatting\033[0m\n"
    @printf "  \033[0;37mjust code-style       \033[0;34m Check style (read-only)\033[0m\n"
    @printf "  \033[0;37mjust code-typecheck   \033[0;34m Run mypy\033[0m\n"
    @printf "  \033[0;37mjust code-security    \033[0;34m Run bandit\033[0m\n"
    @printf "  \033[0;37mjust code-spell       \033[0;34m Check spelling\033[0m\n"
    @echo ""
    @printf "\033[1;33mTesting\033[0m\n"
    @printf "  \033[0;37mjust test             \033[0;34m Run all tests\033[0m\n"
    @printf "  \033[0;37mjust test-unit        \033[0;34m Run unit tests only\033[0m\n"
    @echo ""
    @printf "\033[1;33mCI\033[0m\n"
    @printf "  \033[0;37mjust ci               \033[0;34m Run all CI checks (verbose)\033[0m\n"
    @printf "  \033[0;37mjust ci-quiet         \033[0;34m Run all CI checks (quiet)\033[0m\n"
    @echo ""

# Initialize the development environment
init:
    @echo ""
    @printf "\033[0;34m=== Initializing Base Agent Environment ===\033[0m\n"
    @echo "Installing Python dependencies..."
    @uv sync --all-extras
    @printf "\033[0;32m✓ Base agent environment ready\033[0m\n"
    @echo ""

# Destroy the virtual environment
destroy:
    @echo ""
    @printf "\033[0;34m=== Destroying Virtual Environment ===\033[0m\n"
    @rm -rf .venv
    @printf "\033[0;32m✓ Virtual environment removed\033[0m\n"
    @echo ""

# Auto-fix formatting
code-format:
    @echo ""
    uv run ruff format src/ tests/
    uv run ruff check --fix src/ tests/
    @printf "\033[0;32m✓ Formatting applied\033[0m\n"
    @echo ""

# Check style (read-only)
code-style:
    @echo ""
    uv run ruff format --check src/ tests/
    uv run ruff check src/ tests/
    @printf "\033[0;32m✓ Style checks passed\033[0m\n"
    @echo ""

# Run mypy
code-typecheck:
    @echo ""
    uv run mypy src/
    @printf "\033[0;32m✓ Type checks passed\033[0m\n"
    @echo ""

# Run bandit
code-security:
    @echo ""
    uv run bandit -r src/ -c pyproject.toml
    @printf "\033[0;32m✓ Security checks passed\033[0m\n"
    @echo ""

# Check spelling
code-spell:
    @echo ""
    uv run codespell src/ tests/ --ignore-words-list="assertIn"
    @printf "\033[0;32m✓ Spelling checks passed\033[0m\n"
    @echo ""

# Run all tests
test:
    @echo ""
    uv run pytest tests/ -v
    @printf "\033[0;32m✓ All tests passed\033[0m\n"
    @echo ""

# Run unit tests only
test-unit:
    @echo ""
    uv run pytest tests/unit/ -v -m unit
    @printf "\033[0;32m✓ Unit tests passed\033[0m\n"
    @echo ""

# Run all CI checks (verbose)
ci:
    #!/usr/bin/env bash
    set -euo pipefail
    printf "\n"
    printf "\033[0;34m=== Running CI for Base Agent ===\033[0m\n"
    printf "\n"

    printf "\033[0;34m--- Formatting ---\033[0m\n"
    uv run ruff format --check src/ tests/

    printf "\033[0;34m--- Linting ---\033[0m\n"
    uv run ruff check src/ tests/

    printf "\033[0;34m--- Type Checking (mypy) ---\033[0m\n"
    uv run mypy src/

    printf "\033[0;34m--- Security (bandit) ---\033[0m\n"
    uv run bandit -r src/ -c pyproject.toml

    printf "\033[0;34m--- Spelling ---\033[0m\n"
    uv run codespell src/ tests/ --ignore-words-list="assertIn"

    printf "\033[0;34m--- Tests ---\033[0m\n"
    uv run pytest tests/ -v

    printf "\n"
    printf "\033[0;32m✓ All CI checks passed\033[0m\n"
    printf "\n"

# Run all CI checks (quiet)
ci-quiet:
    #!/usr/bin/env bash
    set -euo pipefail
    printf "Checking base-agent...\n"

    uv run ruff format --check src/ tests/ > /dev/null 2>&1
    uv run ruff check src/ tests/ > /dev/null 2>&1
    uv run mypy src/ > /dev/null 2>&1
    uv run bandit -r src/ -c pyproject.toml -q > /dev/null 2>&1
    uv run codespell src/ tests/ --ignore-words-list="assertIn" > /dev/null 2>&1
    uv run pytest tests/ -v --tb=short

    printf "\033[0;32m✓ base-agent CI passed\033[0m\n"
```

### Step 1.7: Install dependencies

```bash
cd agents && just init
```

If `strands-agents` fails to install: edit `pyproject.toml`, remove the `"strands-agents>=0.1.0"` line from `dependencies`, then run `just init` again. Make a note that Strands integration is deferred.

### Verification

```bash
cd agents && uv run python -c "import httpx; import cryptography; import pydantic; print('OK')"
```

Must print `OK`.

### Commit

```bash
git add agents/pyproject.toml agents/pyrightconfig.json agents/config.yaml agents/roster.yaml agents/justfile
git commit -m "feat(agents): add project scaffolding with config and justfile"
```

---

## Phase 2 — Config, Signing, and Package Init

### Working Directory

All commands run from `agents/`.

### Step 2.1: Create `__init__.py`

Create `agents/src/base_agent/__init__.py`:

```python
"""Base Agent — programmable client for the Agent Task Economy platform."""

__version__ = "0.1.0"
```

### Step 2.2: Create `mixins/__init__.py`

Create `agents/src/base_agent/mixins/__init__.py`:

```python
"""Service-specific mixin classes for BaseAgent."""
```

### Step 2.3: Create config.py

Create `agents/src/base_agent/config.py`:

```python
"""
Configuration management for the base agent.

Loads configuration from YAML with ZERO defaults.
Every value must be explicitly specified or startup fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict
from service_commons.config import (
    REDACTION_MARKER,
    create_settings_loader,
    get_safe_model_config,
)
from service_commons.config import (
    get_config_path as resolve_config_path,
)

if TYPE_CHECKING:
    from pathlib import Path


class PlatformConfig(BaseModel):
    """URLs for all platform services."""

    model_config = ConfigDict(extra="forbid")
    identity_url: str
    bank_url: str
    task_board_url: str
    reputation_url: str
    court_url: str


class DataConfig(BaseModel):
    """Data directory configuration."""

    model_config = ConfigDict(extra="forbid")
    keys_dir: str
    roster_path: str


class Settings(BaseModel):
    """
    Root configuration container.

    All fields are REQUIRED. No defaults exist.
    Missing fields cause immediate startup failure.
    """

    model_config = ConfigDict(extra="forbid")
    platform: PlatformConfig
    data: DataConfig


def get_config_path() -> Path:
    """Determine configuration file path."""
    return resolve_config_path(
        env_var_name="AGENT_CONFIG_PATH",
        default_filename="config.yaml",
    )


get_settings, clear_settings_cache = create_settings_loader(Settings, get_config_path)


def get_safe_config() -> dict[str, Any]:
    """Get configuration with sensitive values redacted."""
    return get_safe_model_config(get_settings(), REDACTION_MARKER)
```

### Step 2.4: Create signing.py

Create `agents/src/base_agent/signing.py`:

```python
"""
Ed25519 key management and JWS token creation.

Handles key generation, loading, and compact JWS (header.payload.signature)
token creation using EdDSA for authenticating with platform services.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def generate_keypair(handle: str, keys_dir: Path) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a new Ed25519 keypair and persist to disk.

    Creates {handle}.key (private) and {handle}.pub (public) in PEM format.

    Args:
        handle: Agent handle used as filename prefix.
        keys_dir: Directory to write key files into.

    Returns:
        Tuple of (private_key, public_key).
    """
    keys_dir.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_path = keys_dir / f"{handle}.key"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    public_path = keys_dir / f"{handle}.pub"
    public_path.write_bytes(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    return private_key, public_key


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from a PEM file.

    Args:
        path: Path to the PEM-encoded private key file.

    Returns:
        The loaded private key.

    Raises:
        FileNotFoundError: If the key file does not exist.
        ValueError: If the file does not contain a valid Ed25519 private key.
    """
    key_bytes = path.read_bytes()
    private_key = serialization.load_pem_private_key(key_bytes, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        msg = f"Expected Ed25519 private key, got {type(private_key).__name__}"
        raise ValueError(msg)
    return private_key


def load_public_key(path: Path) -> Ed25519PublicKey:
    """Load an Ed25519 public key from a PEM file.

    Args:
        path: Path to the PEM-encoded public key file.

    Returns:
        The loaded public key.

    Raises:
        FileNotFoundError: If the key file does not exist.
        ValueError: If the file does not contain a valid Ed25519 public key.
    """
    key_bytes = path.read_bytes()
    public_key = serialization.load_pem_public_key(key_bytes)
    if not isinstance(public_key, Ed25519PublicKey):
        msg = f"Expected Ed25519 public key, got {type(public_key).__name__}"
        raise ValueError(msg)
    return public_key


def public_key_to_b64(public_key: Ed25519PublicKey) -> str:
    """Export a public key as base64-encoded raw bytes.

    The Identity service expects keys in the format "ed25519:<base64>".
    This function returns only the base64 portion.

    Args:
        public_key: The Ed25519 public key to export.

    Returns:
        Base64-encoded string of the raw 32-byte public key.
    """
    raw_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw_bytes).decode("ascii")


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode bytes without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def create_jws(payload: dict[str, object], private_key: Ed25519PrivateKey) -> str:
    """Create a compact JWS token (header.payload.signature) using EdDSA.

    Produces a three-part dot-separated string: base64url(header).base64url(payload).base64url(signature).
    The header specifies alg=EdDSA. The signature covers the ASCII bytes of "header.payload".

    Args:
        payload: Dictionary to sign as the JWS payload.
        private_key: Ed25519 private key used for signing.

    Returns:
        Compact JWS string.
    """
    header = {"alg": "EdDSA", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input)
    signature_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"
```

### Verification

```bash
cd agents && uv run ruff check src/ && uv run ruff format --check src/
cd agents && uv run mypy src/
cd agents && uv run python -c "from base_agent.config import get_settings; print('config OK')"
cd agents && uv run python -c "from base_agent.signing import generate_keypair, create_jws; print('signing OK')"
```

All must succeed. The config import will raise a `ConfigurationError` if not run from the `agents/` directory (where `config.yaml` lives) — that's expected behavior.

### Commit

```bash
git add agents/src/
git commit -m "feat(agents): add config.py and signing.py with Ed25519 key management"
```

---

## Phase 3 — BaseAgent Skeleton and Mixin Stubs

### Working Directory

All commands run from `agents/`.

### Step 3.1: Create mixin stub files

Each mixin is a placeholder class with no methods yet. They will be implemented in separate tickets.

Create `agents/src/base_agent/mixins/identity.py`:

```python
"""Identity service mixin — agent registration and lookup."""


class IdentityMixin:
    """Methods for interacting with the Identity service (port 8001)."""
```

Create `agents/src/base_agent/mixins/bank.py`:

```python
"""Central Bank mixin — account balance, transactions, escrow."""


class BankMixin:
    """Methods for interacting with the Central Bank service (port 8002)."""
```

Create `agents/src/base_agent/mixins/task_board.py`:

```python
"""Task Board mixin — task lifecycle, bidding, contracts."""


class TaskBoardMixin:
    """Methods for interacting with the Task Board service (port 8003)."""
```

Create `agents/src/base_agent/mixins/reputation.py`:

```python
"""Reputation mixin — feedback submission and retrieval."""


class ReputationMixin:
    """Methods for interacting with the Reputation service (port 8004)."""
```

Create `agents/src/base_agent/mixins/court.py`:

```python
"""Court mixin — dispute filing."""


class CourtMixin:
    """Methods for interacting with the Court service (port 8005)."""
```

Update `agents/src/base_agent/mixins/__init__.py`:

```python
"""Service-specific mixin classes for BaseAgent."""

from base_agent.mixins.bank import BankMixin
from base_agent.mixins.court import CourtMixin
from base_agent.mixins.identity import IdentityMixin
from base_agent.mixins.reputation import ReputationMixin
from base_agent.mixins.task_board import TaskBoardMixin

__all__ = [
    "BankMixin",
    "CourtMixin",
    "IdentityMixin",
    "ReputationMixin",
    "TaskBoardMixin",
]
```

### Step 3.2: Create agent.py

Create `agents/src/base_agent/agent.py`:

```python
"""
BaseAgent — programmable client for the Agent Task Economy platform.

Composes service-specific mixins for Identity, Central Bank, Task Board,
Reputation, and Court services. All cross-cutting concerns (signing, HTTP,
config) live here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from base_agent.config import Settings
from base_agent.mixins import (
    BankMixin,
    CourtMixin,
    IdentityMixin,
    ReputationMixin,
    TaskBoardMixin,
)
from base_agent.signing import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
    create_jws,
    generate_keypair,
    load_private_key,
    load_public_key,
    public_key_to_b64,
)


class BaseAgent(IdentityMixin, BankMixin, TaskBoardMixin, ReputationMixin, CourtMixin):
    """Programmable client for the Agent Task Economy platform.

    Holds agent identity (keypair, handle, name) and provides HTTP + signing
    internals used by all service mixins. Methods on mixins are dual-use:
    callable directly from Python and usable as Strands @tool functions.

    Usage::

        config = get_settings()
        agent = BaseAgent(handle="alice", config=config)
        await agent.register()
        tasks = await agent.list_tasks(status="open")
    """

    def __init__(self, handle: str, config: Settings) -> None:
        """Initialize the agent with a handle and configuration.

        Loads the keypair from disk (or generates one if missing) and reads
        the agent's name from the roster file.

        Args:
            handle: Agent handle from roster (e.g. "alice").
                    Maps to key files: {keys_dir}/alice.key, {keys_dir}/alice.pub
            config: Loaded Settings object with platform URLs and data paths.
        """
        self.handle = handle
        self.config = config
        self.agent_id: str | None = None

        # Resolve paths relative to config file directory
        config_dir = Path(config.data.keys_dir)
        if not config_dir.is_absolute():
            config_dir = Path.cwd() / config_dir
        self._keys_dir = config_dir.resolve()

        # Load roster
        roster_path = Path(config.data.roster_path)
        if not roster_path.is_absolute():
            roster_path = Path.cwd() / roster_path
        roster = self._load_roster(roster_path.resolve())
        self.name: str = roster["agents"][handle]["name"]
        self.agent_type: str = roster["agents"][handle]["type"]

        # Load or generate keypair
        self._private_key, self._public_key = self._load_or_generate_keys()

        # HTTP client
        self._http = httpx.AsyncClient()

    @staticmethod
    def _load_roster(roster_path: Path) -> dict[str, Any]:
        """Load the agent roster from a YAML file.

        Args:
            roster_path: Absolute path to roster.yaml.

        Returns:
            Parsed roster dictionary.

        Raises:
            FileNotFoundError: If roster file does not exist.
            ValueError: If roster file is empty or invalid.
        """
        if not roster_path.exists():
            msg = f"Roster file not found: {roster_path}"
            raise FileNotFoundError(msg)

        with roster_path.open() as f:
            roster = yaml.safe_load(f)

        if not isinstance(roster, dict) or "agents" not in roster:
            msg = f"Invalid roster file: {roster_path} — must contain 'agents' key"
            raise ValueError(msg)

        return roster  # type: ignore[no-any-return]

    def _load_or_generate_keys(self) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
        """Load keypair from disk, or generate if missing.

        Returns:
            Tuple of (private_key, public_key).
        """
        private_path = self._keys_dir / f"{self.handle}.key"
        public_path = self._keys_dir / f"{self.handle}.pub"

        if private_path.exists() and public_path.exists():
            return load_private_key(private_path), load_public_key(public_path)

        return generate_keypair(self.handle, self._keys_dir)

    def get_public_key_b64(self) -> str:
        """Return the public key as a base64-encoded string.

        Returns:
            Base64 string of the raw 32-byte public key.
        """
        return public_key_to_b64(self._public_key)

    def _sign_jws(self, payload: dict[str, object]) -> str:
        """Create a JWS token signed with this agent's private key.

        Args:
            payload: Dictionary to encode and sign.

        Returns:
            Compact JWS string (header.payload.signature).
        """
        return create_jws(payload, self._private_key)

    def _auth_header(self, payload: dict[str, object]) -> dict[str, str]:
        """Create an Authorization header with a signed JWS token.

        Args:
            payload: Dictionary to encode and sign.

        Returns:
            Dictionary with 'Authorization' key containing 'Bearer <JWS>'.
        """
        token = self._sign_jws(payload)
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an HTTP request with consistent error handling.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Full URL to request.
            **kwargs: Additional arguments passed to httpx.AsyncClient.request().

        Returns:
            Parsed JSON response as a dictionary.

        Raises:
            httpx.HTTPStatusError: If the response status indicates an error.
        """
        response = await self._http.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def _request_raw(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request and return the raw response.

        Does NOT raise on error status codes — the caller decides how to handle.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Full URL to request.
            **kwargs: Additional arguments passed to httpx.AsyncClient.request().

        Returns:
            The raw httpx.Response object.
        """
        return await self._http.request(method, url, **kwargs)

    def get_tools(self) -> list[Any]:
        """Return all @tool-decorated methods for use with Strands Agent.

        Returns:
            List of tool-decorated methods. Empty list if Strands is not installed.
        """
        tools: list[Any] = []
        for attr_name in dir(self):
            attr = getattr(self, attr_name, None)
            if callable(attr) and hasattr(attr, "tool_definition"):
                tools.append(attr)
        return tools

    async def close(self) -> None:
        """Close the HTTP client. Call this when done using the agent."""
        await self._http.aclose()

    def __repr__(self) -> str:
        registered = f", agent_id={self.agent_id!r}" if self.agent_id else ""
        return f"BaseAgent(handle={self.handle!r}, name={self.name!r}{registered})"
```

### Verification

```bash
cd agents && uv run ruff check src/ && uv run ruff format --check src/
cd agents && uv run mypy src/
cd agents && uv run python -c "
from base_agent.config import get_settings
from base_agent.agent import BaseAgent
settings = get_settings()
print('BaseAgent importable:', BaseAgent.__mro__)
"
```

Must succeed. The import test will show the MRO including all mixins.

### Commit

```bash
git add agents/src/
git commit -m "feat(agents): add BaseAgent skeleton with mixin stubs and HTTP/signing internals"
```

---

## Phase 4 — Unit Tests

### Working Directory

All commands run from `agents/`.

### Step 4.1: Create test conftest

Create `agents/tests/__init__.py` (empty):

```python
```

Create `agents/tests/conftest.py`:

```python
"""Shared test fixtures for base_agent tests."""
```

Create `agents/tests/unit/__init__.py` (empty):

```python
```

Create `agents/tests/unit/conftest.py`:

```python
"""Unit test fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from base_agent.config import Settings, clear_settings_cache


@pytest.fixture()
def tmp_keys_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for key storage."""
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    return keys_dir


@pytest.fixture()
def sample_roster(tmp_path: Path) -> Path:
    """Create a temporary roster file."""
    roster: dict[str, Any] = {
        "agents": {
            "testbot": {
                "name": "Test Bot",
                "type": "worker",
            },
        },
    }
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(yaml.dump(roster))
    return roster_path


@pytest.fixture()
def sample_settings(tmp_keys_dir: Path, sample_roster: Path) -> Settings:
    """Create a Settings object pointing at temporary paths."""
    return Settings(
        platform={
            "identity_url": "http://localhost:8001",
            "bank_url": "http://localhost:8002",
            "task_board_url": "http://localhost:8003",
            "reputation_url": "http://localhost:8004",
            "court_url": "http://localhost:8005",
        },
        data={
            "keys_dir": str(tmp_keys_dir),
            "roster_path": str(sample_roster),
        },
    )


@pytest.fixture(autouse=True)
def _clear_config_cache() -> None:
    """Clear config cache between tests."""
    clear_settings_cache()
```

### Step 4.2: Create signing tests

Create `agents/tests/unit/test_signing.py`:

```python
"""Unit tests for Ed25519 signing utilities."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from base_agent.signing import (
    create_jws,
    generate_keypair,
    load_private_key,
    load_public_key,
    public_key_to_b64,
)


@pytest.mark.unit
class TestGenerateKeypair:
    """Tests for generate_keypair."""

    def test_creates_key_files(self, tmp_keys_dir: Path) -> None:
        private_key, public_key = generate_keypair("alice", tmp_keys_dir)
        assert (tmp_keys_dir / "alice.key").exists()
        assert (tmp_keys_dir / "alice.pub").exists()

    def test_keys_are_loadable(self, tmp_keys_dir: Path) -> None:
        generate_keypair("bob", tmp_keys_dir)
        private_key = load_private_key(tmp_keys_dir / "bob.key")
        public_key = load_public_key(tmp_keys_dir / "bob.pub")
        assert private_key is not None
        assert public_key is not None

    def test_creates_directory_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "keys"
        generate_keypair("carol", nested)
        assert (nested / "carol.key").exists()


@pytest.mark.unit
class TestLoadKeys:
    """Tests for load_private_key and load_public_key."""

    def test_load_missing_private_key_raises(self, tmp_keys_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_private_key(tmp_keys_dir / "nonexistent.key")

    def test_load_missing_public_key_raises(self, tmp_keys_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_public_key(tmp_keys_dir / "nonexistent.pub")

    def test_load_invalid_private_key_raises(self, tmp_keys_dir: Path) -> None:
        bad_file = tmp_keys_dir / "bad.key"
        bad_file.write_text("not a key")
        with pytest.raises((ValueError, Exception)):
            load_private_key(bad_file)

    def test_roundtrip(self, tmp_keys_dir: Path) -> None:
        original_private, original_public = generate_keypair("roundtrip", tmp_keys_dir)
        loaded_private = load_private_key(tmp_keys_dir / "roundtrip.key")
        loaded_public = load_public_key(tmp_keys_dir / "roundtrip.pub")
        assert public_key_to_b64(original_public) == public_key_to_b64(loaded_public)


@pytest.mark.unit
class TestPublicKeyToB64:
    """Tests for public_key_to_b64."""

    def test_returns_base64_string(self, tmp_keys_dir: Path) -> None:
        _, public_key = generate_keypair("b64test", tmp_keys_dir)
        result = public_key_to_b64(public_key)
        raw_bytes = base64.b64decode(result)
        assert len(raw_bytes) == 32

    def test_deterministic(self, tmp_keys_dir: Path) -> None:
        _, public_key = generate_keypair("det", tmp_keys_dir)
        assert public_key_to_b64(public_key) == public_key_to_b64(public_key)


@pytest.mark.unit
class TestCreateJws:
    """Tests for create_jws."""

    def test_produces_three_part_token(self, tmp_keys_dir: Path) -> None:
        private_key, _ = generate_keypair("jws", tmp_keys_dir)
        token = create_jws({"action": "test"}, private_key)
        parts = token.split(".")
        assert len(parts) == 3

    def test_header_contains_eddsa(self, tmp_keys_dir: Path) -> None:
        private_key, _ = generate_keypair("jwshdr", tmp_keys_dir)
        token = create_jws({"action": "test"}, private_key)
        header_b64 = token.split(".")[0]
        padding = "=" * (4 - len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64 + padding))
        assert header["alg"] == "EdDSA"

    def test_payload_is_encoded(self, tmp_keys_dir: Path) -> None:
        private_key, _ = generate_keypair("jwspld", tmp_keys_dir)
        payload = {"action": "register", "agent_id": "a-123"}
        token = create_jws(payload, private_key)
        payload_b64 = token.split(".")[1]
        padding = "=" * (4 - len(payload_b64) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        assert decoded == payload

    def test_signature_verifies(self, tmp_keys_dir: Path) -> None:
        private_key, public_key = generate_keypair("jwsver", tmp_keys_dir)
        token = create_jws({"action": "verify"}, private_key)
        parts = token.split(".")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        sig_padding = "=" * (4 - len(parts[2]) % 4)
        signature = base64.urlsafe_b64decode(parts[2] + sig_padding)
        # This will raise if signature is invalid
        public_key.verify(signature, signing_input)
```

### Step 4.3: Create config tests

Create `agents/tests/unit/test_config.py`:

```python
"""Unit tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from base_agent.config import Settings


@pytest.mark.unit
class TestSettings:
    """Tests for the Settings model."""

    def test_valid_settings(self, sample_settings: Settings) -> None:
        assert sample_settings.platform.identity_url == "http://localhost:8001"
        assert sample_settings.data.keys_dir is not None

    def test_missing_platform_raises(self) -> None:
        with pytest.raises(Exception):
            Settings(
                data={"keys_dir": "/tmp/keys", "roster_path": "roster.yaml"},
            )

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(Exception):
            Settings(
                platform={
                    "identity_url": "http://localhost:8001",
                    "bank_url": "http://localhost:8002",
                    "task_board_url": "http://localhost:8003",
                    "reputation_url": "http://localhost:8004",
                    "court_url": "http://localhost:8005",
                    "unknown_field": "bad",
                },
                data={"keys_dir": "/tmp/keys", "roster_path": "roster.yaml"},
            )
```

### Step 4.4: Create agent tests

Create `agents/tests/unit/test_agent.py`:

```python
"""Unit tests for BaseAgent initialization and internals."""

from __future__ import annotations

import pytest

from base_agent.agent import BaseAgent
from base_agent.config import Settings


@pytest.mark.unit
class TestBaseAgentInit:
    """Tests for BaseAgent construction."""

    def test_creates_agent(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        assert agent.handle == "testbot"
        assert agent.name == "Test Bot"
        assert agent.agent_type == "worker"
        assert agent.agent_id is None

    def test_generates_keys_if_missing(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        keys_dir = Path(sample_settings.data.keys_dir)
        assert (keys_dir / "testbot.key").exists()
        assert (keys_dir / "testbot.pub").exists()

    def test_loads_existing_keys(self, sample_settings: Settings) -> None:
        agent1 = BaseAgent(handle="testbot", config=sample_settings)
        pub1 = agent1.get_public_key_b64()
        agent2 = BaseAgent(handle="testbot", config=sample_settings)
        pub2 = agent2.get_public_key_b64()
        assert pub1 == pub2

    def test_unknown_handle_raises(self, sample_settings: Settings) -> None:
        with pytest.raises(KeyError):
            BaseAgent(handle="nonexistent", config=sample_settings)

    def test_repr(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        assert "testbot" in repr(agent)
        assert "Test Bot" in repr(agent)


@pytest.mark.unit
class TestBaseAgentSigning:
    """Tests for JWS signing internals."""

    def test_sign_jws_produces_token(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        token = agent._sign_jws({"action": "test"})
        assert token.count(".") == 2

    def test_auth_header_format(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        header = agent._auth_header({"action": "test"})
        assert "Authorization" in header
        assert header["Authorization"].startswith("Bearer ")

    def test_public_key_b64_is_stable(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        assert agent.get_public_key_b64() == agent.get_public_key_b64()
```

### Step 4.5: Run tests

```bash
cd agents && just test
```

All tests must pass.

### Step 4.6: Run full CI

```bash
cd agents && just ci
```

Must pass with zero failures. If there are formatting issues, run `just code-format` first, then re-run `just ci`.

### Commit

```bash
git add agents/tests/
git commit -m "test(agents): add unit tests for signing, config, and BaseAgent"
```

---

## Phase 5 — Final Verification and Integration

### Working Directory

All commands run from `agents/`.

### Step 5.1: Run full CI suite

```bash
cd agents && just ci-quiet
```

Must pass with zero failures. This is the **definitive validation**.

### Step 5.2: Verify import from project root

```bash
cd agents && uv run python -c "
from base_agent.agent import BaseAgent
from base_agent.config import get_settings, Settings
from base_agent.signing import generate_keypair, create_jws, load_private_key, load_public_key, public_key_to_b64
from base_agent.mixins import IdentityMixin, BankMixin, TaskBoardMixin, ReputationMixin, CourtMixin
print('All imports OK')
print('BaseAgent MRO:', [c.__name__ for c in BaseAgent.__mro__])
"
```

Expected output:
```
All imports OK
BaseAgent MRO: ['BaseAgent', 'IdentityMixin', 'BankMixin', 'TaskBoardMixin', 'ReputationMixin', 'CourtMixin', 'object']
```

### Step 5.3: Final commit

```bash
git add -A agents/
git commit -m "feat(agents): complete base agent scaffolding (agent-economy-mzd)"
```

---

## File List (All Files to Create)

| File | Phase |
|------|-------|
| `agents/pyproject.toml` | 1 |
| `agents/pyrightconfig.json` | 1 |
| `agents/config.yaml` | 1 |
| `agents/roster.yaml` | 1 |
| `agents/justfile` | 1 |
| `agents/src/base_agent/__init__.py` | 2 |
| `agents/src/base_agent/config.py` | 2 |
| `agents/src/base_agent/signing.py` | 2 |
| `agents/src/base_agent/mixins/__init__.py` | 3 |
| `agents/src/base_agent/mixins/identity.py` | 3 |
| `agents/src/base_agent/mixins/bank.py` | 3 |
| `agents/src/base_agent/mixins/task_board.py` | 3 |
| `agents/src/base_agent/mixins/reputation.py` | 3 |
| `agents/src/base_agent/mixins/court.py` | 3 |
| `agents/src/base_agent/agent.py` | 3 |
| `agents/tests/__init__.py` | 4 |
| `agents/tests/conftest.py` | 4 |
| `agents/tests/unit/__init__.py` | 4 |
| `agents/tests/unit/conftest.py` | 4 |
| `agents/tests/unit/test_signing.py` | 4 |
| `agents/tests/unit/test_config.py` | 4 |
| `agents/tests/unit/test_agent.py` | 4 |
