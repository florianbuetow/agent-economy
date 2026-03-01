# Phase 2 — Configuration and Dependencies

## Working Directory

All commands run from `services/central-bank/`.

---

## Task B1: Update config.yaml and pyproject.toml

### Step 1.1: Update config.yaml

Replace the entire `services/central-bank/config.yaml` with:

```yaml
# Central Bank Service Configuration
# Environment variable overrides use prefix: CENTRAL_BANK__

service:
  name: "central-bank"
  version: "0.1.0"

server:
  host: "0.0.0.0"
  port: 8002
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

database:
  path: "data/central-bank.db"

identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"

platform:
  agent_id: ""

request:
  max_body_size: 1048576
```

### Step 1.2: Add httpx dependency to pyproject.toml

Add `"httpx>=0.28.0"` to the `dependencies` list in `services/central-bank/pyproject.toml` (the Bank needs httpx to call the Identity service at runtime, not just for tests).

### Step 1.3: Install

```bash
cd services/central-bank && just init
```

Expected: Dependencies install successfully.

### Step 1.4: Commit

```bash
git add services/central-bank/config.yaml services/central-bank/pyproject.toml services/central-bank/uv.lock
git commit -m "feat(central-bank): update config and add httpx dependency"
```

---

## Task B2: Implement config.py

### Step 2.1: Write config.py

Create `services/central-bank/src/central_bank_service/config.py`:

```python
"""
Configuration management for the central-bank service.

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


class ServiceConfig(BaseModel):
    """Service identity configuration."""

    model_config = ConfigDict(extra="forbid")
    name: str
    version: str


class ServerConfig(BaseModel):
    """HTTP server configuration."""

    model_config = ConfigDict(extra="forbid")
    host: str
    port: int
    log_level: str


class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(extra="forbid")
    level: str
    format: str


class DatabaseConfig(BaseModel):
    """Database configuration."""

    model_config = ConfigDict(extra="forbid")
    path: str


class IdentityConfig(BaseModel):
    """Identity service connection configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    verify_jws_path: str
    get_agent_path: str


class PlatformConfig(BaseModel):
    """Platform agent configuration."""

    model_config = ConfigDict(extra="forbid")
    agent_id: str


class RequestConfig(BaseModel):
    """Request validation configuration."""

    model_config = ConfigDict(extra="forbid")
    max_body_size: int


class Settings(BaseModel):
    """
    Root configuration container.

    All fields are REQUIRED. No defaults exist.
    Missing fields cause immediate startup failure.
    """

    model_config = ConfigDict(extra="forbid")
    service: ServiceConfig
    server: ServerConfig
    logging: LoggingConfig
    database: DatabaseConfig
    identity: IdentityConfig
    platform: PlatformConfig
    request: RequestConfig


def get_config_path() -> Path:
    """Determine configuration file path."""
    return resolve_config_path(
        env_var_name="CONFIG_PATH",
        default_filename="config.yaml",
    )


get_settings, clear_settings_cache = create_settings_loader(Settings, get_config_path)  # nosemgrep


def get_safe_config() -> dict[str, Any]:
    """Get configuration with sensitive values redacted."""
    return get_safe_model_config(get_settings(), REDACTION_MARKER)
```

### Step 2.2: Commit

```bash
git add services/central-bank/src/central_bank_service/config.py
git commit -m "feat(central-bank): add config.py with all config sections"
```

---

## Verification

```bash
cd services/central-bank && uv run ruff check src/ && uv run ruff format --check src/
```
