# Phase 1 — Scaffolding, Dependencies, Configuration

## Working Directory

Start from the project root, then work from `services/db-gateway/`.

## Step 1.1: Create Directory Structure

```bash
mkdir -p services/db-gateway/src/db_gateway_service/core
mkdir -p services/db-gateway/src/db_gateway_service/services
mkdir -p services/db-gateway/src/db_gateway_service/routers
mkdir -p services/db-gateway/tests/unit/routers
mkdir -p services/db-gateway/tests/integration
mkdir -p services/db-gateway/tests/performance
mkdir -p services/db-gateway/reports/coverage
mkdir -p services/db-gateway/reports/security
mkdir -p services/db-gateway/reports/pyright
mkdir -p services/db-gateway/reports/deptry
```

Create empty `__init__.py` files for packages:

```bash
touch services/db-gateway/src/db_gateway_service/__init__.py
touch services/db-gateway/src/db_gateway_service/core/__init__.py
touch services/db-gateway/src/db_gateway_service/services/__init__.py
touch services/db-gateway/src/db_gateway_service/routers/__init__.py
touch services/db-gateway/tests/__init__.py
touch services/db-gateway/tests/unit/__init__.py
touch services/db-gateway/tests/unit/routers/__init__.py
touch services/db-gateway/tests/integration/__init__.py
touch services/db-gateway/tests/performance/__init__.py
```

---

## Step 1.2: Create `pyproject.toml`

Create `services/db-gateway/pyproject.toml`:

```toml
[project]
name = "db-gateway-service"
version = "0.1.0"
description = "Database Gateway — write serialization layer for the shared economy.db"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "starlette>=0.47.2",
    "pydantic>=2.10.0",
    "service-commons",
]

[project.optional-dependencies]
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
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv.sources]
service-commons = { path = "../../libs/service-commons", editable = true }

[tool.hatch.build.targets.wheel]
packages = ["src/db_gateway_service"]

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
known-first-party = ["db_gateway_service"]

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
    "slow: Tests that take more than 1 second",
    "performance: Performance benchmark tests",
    "architecture: Architecture import rule tests",
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
    "uvicorn",
    "pytest",
    "pytest-cov",
    "pytest-asyncio",
    "pytestarch",
    "httpx",
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
]
```

**Key difference from other services**: No `cryptography`, `joserfc`, `httpx`, `aiofiles`, or `python-multipart` dependencies. The gateway is a pure write executor with no crypto, no outbound HTTP, and no file handling.

---

## Step 1.3: Create `config.yaml`

Create `services/db-gateway/config.yaml`:

```yaml
# Database Gateway Service Configuration
# Environment variable overrides use prefix: DB_GATEWAY__

service:
  name: "db-gateway"
  version: "0.1.0"

server:
  host: "0.0.0.0"
  port: 8006
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

database:
  path: "data/economy.db"
  schema_path: "../../docs/specifications/schema.sql"
  busy_timeout_ms: 5000
  journal_mode: "wal"

request:
  max_body_size: 1048576
```

The `schema_path` points to the shared schema file. The service initializes the database from this schema at startup (idempotent via `CREATE TABLE IF NOT EXISTS` — but note the actual schema.sql uses `CREATE TABLE` without `IF NOT EXISTS`, so the service must handle the case where tables already exist).

---

## Step 1.4: Create `pyrightconfig.json`

Create `services/db-gateway/pyrightconfig.json`:

```json
{
  "include": ["src"],
  "exclude": ["tests", ".venv", "**/__pycache__"],
  "venvPath": ".",
  "venv": ".venv",
  "pythonVersion": "3.12",
  "typeCheckingMode": "strict",
  "reportMissingImports": true,
  "reportMissingTypeStubs": false,
  "reportUnknownMemberType": false,
  "reportUnknownArgumentType": false,
  "reportUnknownVariableType": false,
  "reportUnknownParameterType": false,
  "reportMissingTypeArgument": false,
  "reportUnnecessaryIsInstance": false,
  "reportPrivateUsage": false
}
```

---

## Step 1.5: Create `justfile`

Create `services/db-gateway/justfile`:

```just
# Default recipe: show available commands
_default:
    @just help

# === Service Configuration ===
SERVICE_NAME := "db_gateway_service"
PORT := "8006"

# Show help information
help:
    @clear
    @echo ""
    @printf "\033[0;34m=== Database Gateway Service (port 8006) ===\033[0m\n"
    @echo ""
    @printf "\033[1;33mSetup\033[0m\n"
    @printf "  \033[0;37mjust check            \033[0;34m Check if all required tools are installed\033[0m\n"
    @printf "  \033[0;37mjust init             \033[0;34m Initialize development environment\033[0m\n"
    @printf "  \033[0;37mjust destroy          \033[0;34m Remove virtual environment\033[0m\n"
    @echo ""
    @printf "\033[1;33mLocal Development\033[0m\n"
    @printf "  \033[0;37mjust run              \033[0;34m Run locally with hot reload\033[0m\n"
    @printf "  \033[0;37mjust kill             \033[0;34m Stop the locally running service\033[0m\n"
    @printf "  \033[0;37mjust status           \033[0;34m Check health status\033[0m\n"
    @echo ""
    @printf "\033[1;33mDocker\033[0m\n"
    @printf "  \033[0;37mjust docker-up        \033[0;34m Start in Docker (production)\033[0m\n"
    @printf "  \033[0;37mjust docker-up-dev    \033[0;34m Start in Docker (dev with hot reload)\033[0m\n"
    @printf "  \033[0;37mjust docker-down      \033[0;34m Stop Docker container\033[0m\n"
    @printf "  \033[0;37mjust docker-logs      \033[0;34m View Docker logs\033[0m\n"
    @printf "  \033[0;37mjust docker-build     \033[0;34m Build Docker image\033[0m\n"
    @echo ""
    @printf "\033[1;33mTesting\033[0m\n"
    @printf "  \033[0;37mjust test             \033[0;34m Run all tests (unit + integration)\033[0m\n"
    @printf "  \033[0;37mjust test-unit        \033[0;34m Run unit tests only\033[0m\n"
    @printf "  \033[0;37mjust test-integration \033[0;34m Run integration tests only\033[0m\n"
    @printf "  \033[0;37mjust test-coverage    \033[0;34m Run tests with coverage report\033[0m\n"
    @printf "  \033[0;37mjust test-performance \033[0;34m Run performance tests\033[0m\n"
    @echo ""
    @printf "\033[1;33mCode Quality\033[0m\n"
    @printf "  \033[0;37mjust code-format      \033[0;34m Auto-fix formatting\033[0m\n"
    @printf "  \033[0;37mjust code-style       \033[0;34m Check style (read-only)\033[0m\n"
    @printf "  \033[0;37mjust code-typecheck   \033[0;34m Run mypy type checking\033[0m\n"
    @printf "  \033[0;37mjust code-lspchecks   \033[0;34m Run pyright (strict)\033[0m\n"
    @printf "  \033[0;37mjust code-security    \033[0;34m Run bandit security scan\033[0m\n"
    @printf "  \033[0;37mjust code-deptry      \033[0;34m Check dependency hygiene\033[0m\n"
    @printf "  \033[0;37mjust code-spell       \033[0;34m Check spelling\033[0m\n"
    @printf "  \033[0;37mjust code-semgrep     \033[0;34m Run static analysis\033[0m\n"
    @printf "  \033[0;37mjust code-audit       \033[0;34m Scan for vulnerabilities\033[0m\n"
    @printf "  \033[0;37mjust code-stats       \033[0;34m Generate code statistics\033[0m\n"
    @echo ""
    @printf "\033[1;33mCI\033[0m\n"
    @printf "  \033[0;37mjust ci               \033[0;34m Run all CI checks (verbose)\033[0m\n"
    @printf "  \033[0;37mjust ci-all           \033[0;34m Run CI + integration tests\033[0m\n"
    @printf "  \033[0;37mjust ci-quiet         \033[0;34m Run all CI checks (quiet)\033[0m\n"
    @echo ""

# === Setup ===

# Check if all required tools are installed
check:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Checking Required Tools ===\033[0m\n"
    printf "\n"
    missing=0

    check_tool() {
        local name=$1
        local cmd=$2
        local version_flag=${3:---version}
        if command -v "$cmd" >/dev/null 2>&1; then
            version=$("$cmd" $version_flag 2>&1 | head -1)
            printf "\033[0;32m✓ %s\033[0m - %s\n" "$name" "$version"
        else
            printf "\033[0;31m✗ %s\033[0m - not found\n" "$name"
            missing=$((missing + 1))
        fi
    }

    check_tool "uv"     uv     "--version"
    check_tool "python"  python3 "--version"
    check_tool "docker"  docker  "--version"
    check_tool "curl"    curl    "--version"
    check_tool "jq"      jq      "--version"
    check_tool "lsof"    lsof    "-v"

    printf "\n"
    if [ "$missing" -gt 0 ]; then
        printf "\033[0;31m✗ %d tool(s) missing\033[0m\n" "$missing"
        exit 1
    else
        printf "\033[0;32m✓ All required tools are installed\033[0m\n"
    fi
    printf "\n"

# === Environment Management ===

# Initialize the development environment
init:
    @echo ""
    @printf "\033[0;34m=== Initializing Development Environment ===\033[0m\n"
    @mkdir -p reports/coverage
    @mkdir -p reports/security
    @mkdir -p reports/pyright
    @mkdir -p reports/deptry
    @echo "Installing Python dependencies..."
    @uv sync --all-extras
    @printf "\033[0;32m✓ Development environment ready\033[0m\n"
    @echo ""

# Destroy the virtual environment
destroy:
    @echo ""
    @printf "\033[0;34m=== Destroying Virtual Environment ===\033[0m\n"
    @rm -rf .venv
    @rm -rf reports
    @printf "\033[0;32m✓ Virtual environment removed\033[0m\n"
    @echo ""

# === Run Service (Local) ===

# Run the service locally (no Docker) with hot reload
run:
    @echo ""
    @printf "\033[0;34m=== Running Database Gateway Service Locally on port 8006 ===\033[0m\n"
    @uv run uvicorn db_gateway_service.app:create_app --factory --reload --host 0.0.0.0 --port 8006

# Stop the locally running service
kill:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Stopping Database Gateway Service (Local) ===\033[0m\n"
    printf "\n"

    pid=$(lsof -ti :8006 -sTCP:LISTEN 2>/dev/null)

    if [ -n "$pid" ]; then
        printf "Service is running (PID: %s). Stopping...\n" "$pid"
        kill $pid 2>/dev/null
        sleep 1

        if lsof -ti :8006 -sTCP:LISTEN > /dev/null 2>&1; then
            printf "\033[0;31m✗ Service still running. Forcing kill...\033[0m\n"
            kill -9 $(lsof -ti :8006 -sTCP:LISTEN) 2>/dev/null
            sleep 1
        fi

        if lsof -ti :8006 -sTCP:LISTEN > /dev/null 2>&1; then
            printf "\033[0;31m✗ Failed to stop service\033[0m\n"
            exit 1
        else
            printf "\033[0;32m✓ Service stopped\033[0m\n"
        fi
    else
        printf "Service is not running\n"
    fi
    printf "\n"

# === Docker ===

# Run service in Docker (production mode, detached)
docker-up:
    @echo ""
    @printf "\033[0;34m=== Starting Database Gateway Service in Docker ===\033[0m\n"
    @cd ../.. && docker compose up -d db-gateway
    @echo ""
    @printf "\033[0;32m✓ Service running at http://localhost:8006\033[0m\n"
    @echo "  View logs: just docker-logs"
    @echo ""

# Run service in Docker (development mode with hot reload)
docker-up-dev:
    @echo ""
    @printf "\033[0;34m=== Starting Database Gateway Service in Docker (Dev Mode) ===\033[0m\n"
    @cd ../.. && docker compose -f docker-compose.yml -f docker-compose.dev.yml up db-gateway

# Stop service in Docker
docker-down:
    @echo ""
    @printf "\033[0;34m=== Stopping Database Gateway Service ===\033[0m\n"
    @cd ../.. && docker compose stop db-gateway
    @printf "\033[0;32m✓ Service stopped\033[0m\n"
    @echo ""

# View Docker logs for this service
docker-logs:
    @cd ../.. && docker compose logs -f db-gateway

# Build Docker image for this service
docker-build:
    @echo ""
    @printf "\033[0;34m=== Building Database Gateway Docker Image ===\033[0m\n"
    @cd ../.. && docker compose build db-gateway
    @printf "\033[0;32m✓ Image built\033[0m\n"
    @echo ""

# Check health status of this service
status:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Database Gateway Service Status ===\033[0m\n"
    printf "\n"
    if health_response=$(curl -s --connect-timeout 2 "http://localhost:8006/health" 2>/dev/null); then
        status=$(echo "$health_response" | jq -r '.status // empty' 2>/dev/null)
        if [ "$status" = "ok" ]; then
            uptime=$(echo "$health_response" | jq -r '.uptime_seconds // empty' 2>/dev/null)
            printf "\033[0;32m✓ Database Gateway\033[0m (port 8006) - ok"
            [ -n "$uptime" ] && printf " (uptime: %ss)" "$uptime"
            printf "\n"
        else
            printf "\033[0;33m⚠ Database Gateway\033[0m (port 8006) - %s\n" "${status:-unknown}"
        fi
    else
        printf "\033[0;31m✗ Database Gateway\033[0m (port 8006) - not responding\n"
    fi
    printf "\n"

# === Code Quality ===

# Check code style and formatting (read-only)
code-style:
    @echo ""
    @printf "\033[0;34m=== Checking Code Style ===\033[0m\n"
    @uv run ruff check .
    @echo ""
    @uv run ruff format --check .
    @echo ""
    @printf "\033[0;32m✓ Style checks passed\033[0m\n"
    @echo ""

# Auto-fix code style and formatting
code-format:
    @echo ""
    @printf "\033[0;34m=== Formatting Code ===\033[0m\n"
    @uv run ruff check . --fix
    @echo ""
    @uv run ruff format .
    @echo ""
    @printf "\033[0;32m✓ Code formatted\033[0m\n"
    @echo ""

# Run static type checking with mypy
code-typecheck:
    @echo ""
    @printf "\033[0;34m=== Running Type Checks ===\033[0m\n"
    @uv run mypy src/
    @echo ""
    @printf "\033[0;32m✓ Type checks passed\033[0m\n"
    @echo ""

# Run strict type checking with Pyright (LSP-based)
code-lspchecks:
    @echo ""
    @printf "\033[0;34m=== Running Pyright Type Checks ===\033[0m\n"
    @mkdir -p reports/pyright
    @uv run pyright --project pyrightconfig.json > reports/pyright/pyright.txt 2>&1 || true
    @uv run pyright --project pyrightconfig.json
    @echo ""
    @printf "\033[0;32m✓ Pyright checks passed\033[0m\n"
    @echo "  Report: reports/pyright/pyright.txt"
    @echo ""

# Run security checks with bandit
code-security:
    @echo ""
    @printf "\033[0;34m=== Running Security Checks ===\033[0m\n"
    @mkdir -p reports/security
    @uv run bandit -c pyproject.toml -r src -f txt -o reports/security/bandit.txt || true
    @uv run bandit -c pyproject.toml -r src
    @echo ""
    @printf "\033[0;32m✓ Security checks passed\033[0m\n"
    @echo ""

# Check dependency hygiene with deptry
code-deptry:
    @echo ""
    @printf "\033[0;34m=== Checking Dependencies ===\033[0m\n"
    @mkdir -p reports/deptry
    @uv run deptry src
    @echo ""
    @printf "\033[0;32m✓ Dependency checks passed\033[0m\n"
    @echo ""

# Generate code statistics with pygount
code-stats:
    @echo ""
    @printf "\033[0;34m=== Code Statistics ===\033[0m\n"
    @mkdir -p reports
    @uv run pygount src/ tests/ --suffix=py,md,txt,toml,yaml,yml --format=summary
    @echo ""
    @uv run pygount src/ tests/ --suffix=py,md,txt,toml,yaml,yml --format=summary > reports/code-stats.txt
    @printf "\033[0;32m✓ Report saved to reports/code-stats.txt\033[0m\n"
    @echo ""

# Check spelling in code and documentation
code-spell:
    @echo ""
    @printf "\033[0;34m=== Checking Spelling ===\033[0m\n"
    @uv run codespell src tests *.md *.toml --ignore-words=../../config/codespell/ignore.txt
    @echo ""
    @printf "\033[0;32m✓ Spelling checks passed\033[0m\n"
    @echo ""

# Scan dependencies for known vulnerabilities
code-audit:
    @echo ""
    @printf "\033[0;34m=== Scanning Dependencies for Vulnerabilities ===\033[0m\n"
    @uv run pip-audit
    @echo ""
    @printf "\033[0;32m✓ No known vulnerabilities found\033[0m\n"
    @echo ""

# Run Semgrep static analysis (uses root config)
code-semgrep:
    @echo ""
    @printf "\033[0;34m=== Running Semgrep Static Analysis ===\033[0m\n"
    @uv run semgrep --config ../../config/semgrep/ --error src
    @echo ""
    @printf "\033[0;32m✓ Semgrep checks passed\033[0m\n"
    @echo ""

# === Testing ===

# Run all tests (unit + integration in separate processes for isolation)
test:
    @echo ""
    @printf "\033[0;34m=== Running All Tests ===\033[0m\n"
    @just test-unit
    @just test-integration
    @printf "\033[0;32m✓ All tests passed\033[0m\n"
    @echo ""

# Run unit tests only (fast, isolated)
test-unit:
    @echo ""
    @printf "\033[0;34m=== Running Unit Tests ===\033[0m\n"
    @uv run pytest tests/unit -m unit -v
    @echo ""

# Run integration tests only
test-integration:
    @echo ""
    @printf "\033[0;34m=== Running Integration Tests ===\033[0m\n"
    @uv run pytest tests/integration -m integration -v
    @echo ""

# Run unit tests with coverage report and threshold check
test-coverage: init
    @echo ""
    @printf "\033[0;34m=== Running Unit Tests with Coverage ===\033[0m\n"
    @uv run pytest tests/ -v \
        --cov=src \
        --cov-report=html:reports/coverage/html \
        --cov-report=term \
        --cov-report=xml:reports/coverage/coverage.xml \
        --cov-fail-under=80
    @echo ""
    @printf "\033[0;32m✓ Coverage threshold met\033[0m\n"
    @echo "  HTML: reports/coverage/html/index.html"
    @echo ""

# Run performance tests (slow)
test-performance:
    @echo ""
    @printf "\033[0;34m=== Running Performance Tests ===\033[0m\n"
    @uv run pytest tests/performance -m performance -v -s
    @echo ""

# === CI Pipelines ===

# Run ALL validation checks (verbose) - uses unit tests for speed
ci:
    #!/usr/bin/env bash
    set -e
    printf "\n"
    printf "\033[0;34m=== Running CI Checks for Database Gateway Service ===\033[0m\n"
    printf "\n"
    just init
    just code-format
    just code-style
    just code-typecheck
    just code-security
    just code-deptry
    just code-spell
    just code-semgrep
    just code-audit
    just test-unit
    just code-lspchecks
    printf "\n"
    printf "\033[0;32m✓ All CI checks passed\033[0m\n"
    printf "\n"

# Run full CI including integration tests
ci-all:
    #!/usr/bin/env bash
    set -e
    printf "\n"
    printf "\033[0;34m=== Running All CI Checks (with Integration Tests) ===\033[0m\n"
    printf "\n"
    just ci
    just test-integration
    printf "\n"
    printf "\033[0;32m✓ All CI checks (including integration tests) passed\033[0m\n"
    printf "\n"

# Run ALL validation checks silently (only show output on errors) - uses unit tests for speed
ci-quiet:
    #!/usr/bin/env bash
    set -e
    printf "\033[0;34m=== Running CI Checks (Quiet Mode) ===\033[0m\n"
    TMPFILE=$(mktemp)
    trap "rm -f $TMPFILE" EXIT

    just init > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Init failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Init passed\033[0m\n"

    just code-format > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Code-format failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Code-format passed\033[0m\n"

    just code-style > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Code-style failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Code-style passed\033[0m\n"

    just code-typecheck > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Code-typecheck failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Code-typecheck passed\033[0m\n"

    just code-security > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Code-security failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Code-security passed\033[0m\n"

    just code-deptry > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Code-deptry failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Code-deptry passed\033[0m\n"

    just code-spell > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Code-spell failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Code-spell passed\033[0m\n"

    just code-semgrep > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Code-semgrep failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Code-semgrep passed\033[0m\n"

    just code-audit > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Code-audit failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Code-audit passed\033[0m\n"

    just test-unit > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Unit tests failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Unit tests passed\033[0m\n"

    just code-lspchecks > $TMPFILE 2>&1 || { printf "\033[0;31m✗ Code-lspchecks failed\033[0m\n"; cat $TMPFILE; exit 1; }
    printf "\033[0;32m✓ Code-lspchecks passed\033[0m\n"

    printf "\n"
    printf "\033[0;32m✓ All CI checks passed\033[0m\n"
    printf "\n"
```

---

## Step 1.6: Create `Dockerfile`

Create `services/db-gateway/Dockerfile`:

```dockerfile
FROM python:3.12-slim AS base

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependency files
COPY pyproject.toml ./
COPY ../../libs/service-commons /libs/service-commons

# Install dependencies
RUN uv sync --no-dev --frozen

# Copy application code
COPY src/ ./src/
COPY config.yaml ./

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "db_gateway_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
```

---

## Step 1.7: Install dependencies

```bash
cd services/db-gateway && just init
```

## Verification

```bash
cd services/db-gateway && uv run python -c "import fastapi; print('OK')"
```

Must print `OK`.
