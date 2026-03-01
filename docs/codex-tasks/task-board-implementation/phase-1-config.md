# Phase 1 — Dependencies and Configuration

## Working Directory

All commands run from `services/task-board/`.

## Step 1.1: Add dependencies to pyproject.toml

Edit `pyproject.toml`. Change the `dependencies` list to:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "pydantic>=2.10.0",
    "service-commons",
    "cryptography>=44.0.0",
    "httpx>=0.28.0",
    "joserfc>=1.0.0",
    "aiofiles>=24.0.0",
    "python-multipart>=0.0.20",
]
```

Also add `"types-aiofiles>=24.0.0"` to the dev dependencies in `[project.optional-dependencies]`:

```toml
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=6.0.0",
    "pytest-asyncio>=0.24.0",
    "pytestarch[visualization]>=2.0.0",
    "httpx>=0.28.0",
    "ruff>=0.8.0",
    "mypy>=1.13.0",
    "pyright>=1.1.390",
    "bandit>=1.7.0",
    "deptry>=0.21.0",
    "codespell>=2.3.0",
    "semgrep>=1.99.0",
    "pip-audit>=2.7.0",
    "pygount>=1.8.0",
    "types-PyYAML>=6.0.0",
    "types-aiofiles>=24.0.0",
]
```

Update the `[tool.deptry.per_rule_ignores]` `DEP002` list to include the new runtime dependencies:

```toml
[tool.deptry.per_rule_ignores]
DEP002 = [
    "fastapi",
    "uvicorn",
    "pydantic",
    "service-commons",
    "cryptography",
    "httpx",
    "joserfc",
    "aiofiles",
    "python-multipart",
    "pytest",
    "pytest-cov",
    "pytest-asyncio",
    "pytestarch",
    "ruff",
    "mypy",
    "pyright",
    "bandit",
    "deptry",
    "codespell",
    "semgrep",
    "pip-audit",
    "pygount",
    "types-PyYAML",
    "types-aiofiles",
]
```

Do not change anything else in `pyproject.toml`.

## Step 1.2: Extend config.yaml

Replace the **entire** contents of `config.yaml` with:

```yaml
# Task Board Service Configuration
# Environment variable overrides use prefix: TASK_BOARD__

service:
  name: "task-board"
  version: "0.1.0"

server:
  host: "0.0.0.0"
  port: 8003
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

database:
  path: "data/task-board.db"

identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10

central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/{escrow_id}/release"
  timeout_seconds: 10

platform:
  agent_id: ""
  private_key_path: ""

assets:
  storage_path: "data/assets"
  max_file_size: 10485760
  max_files_per_task: 10

request:
  max_body_size: 10485760
```

All fields are required. The service must fail to start if any is missing. No default values.

`platform.agent_id` is the agent ID of the platform agent registered with the Identity service. `platform.private_key_path` points to the Ed25519 private key file used for signing platform operations (escrow release).

## Step 1.3: Install dependencies

```bash
cd services/task-board && just init
```

## Verification

```bash
cd services/task-board && uv run python -c "import httpx; import joserfc; import aiofiles; from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; print('OK')"
```

Must print `OK`.
