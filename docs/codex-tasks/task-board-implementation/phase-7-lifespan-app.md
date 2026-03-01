# Phase 7 — Lifespan and App Assembly

## Working Directory

All paths relative to `services/task-board/`.

---

## File 1: `src/task_board_service/core/lifespan.py`

Create this file:

```python
"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from task_board_service.clients.central_bank_client import CentralBankClient
from task_board_service.clients.identity_client import IdentityClient
from task_board_service.clients.platform_signer import PlatformSigner
from task_board_service.config import get_settings
from task_board_service.core.state import init_app_state
from task_board_service.logging import get_logger, setup_logging
from task_board_service.services.task_manager import TaskManager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    # === STARTUP ===
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name)
    logger = get_logger(__name__)

    state = init_app_state()

    # Ensure database directory exists
    db_path = settings.database.path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Ensure asset storage directory exists
    asset_storage_path = settings.assets.storage_path
    Path(asset_storage_path).mkdir(parents=True, exist_ok=True)

    # Initialize PlatformSigner (loads Ed25519 private key from disk)
    platform_signer = PlatformSigner(
        private_key_path=settings.platform.private_key_path,
        platform_agent_id=settings.platform.agent_id,
    )
    state.platform_signer = platform_signer

    # Initialize IdentityClient (HTTP client for JWS verification)
    identity_client = IdentityClient(
        base_url=settings.identity.base_url,
        verify_jws_path=settings.identity.verify_jws_path,
        timeout_seconds=settings.identity.timeout_seconds,
    )
    state.identity_client = identity_client

    # Initialize CentralBankClient (HTTP client for escrow operations)
    central_bank_client = CentralBankClient(
        base_url=settings.central_bank.base_url,
        escrow_lock_path=settings.central_bank.escrow_lock_path,
        escrow_release_path=settings.central_bank.escrow_release_path,
        timeout_seconds=settings.central_bank.timeout_seconds,
        platform_signer=platform_signer,
    )
    state.central_bank_client = central_bank_client

    # Initialize TaskManager (all business logic)
    task_manager = TaskManager(
        db_path=db_path,
        identity_client=identity_client,
        central_bank_client=central_bank_client,
        platform_signer=platform_signer,
        asset_storage_path=asset_storage_path,
        max_file_size=settings.assets.max_file_size,
        max_files_per_task=settings.assets.max_files_per_task,
        platform_agent_id=settings.platform.agent_id,
    )
    state.task_manager = task_manager

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
            "db_path": db_path,
            "asset_storage_path": asset_storage_path,
            "identity_base_url": settings.identity.base_url,
            "central_bank_base_url": settings.central_bank.base_url,
            "platform_agent_id": settings.platform.agent_id,
        },
    )

    yield  # Application runs here

    # === SHUTDOWN ===
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})

    # Close task manager (closes SQLite database)
    task_manager.close()

    # Close HTTP clients (closes httpx async clients)
    await identity_client.close()
    await central_bank_client.close()
```

**Startup sequence explained:**

1. **Load settings** — `get_settings()` reads `config.yaml` and validates all fields. Fails fast on missing config.
2. **Setup logging** — configures structured JSON logging with the configured level and service name.
3. **Initialize app state** — creates the global `AppState` singleton that stores all runtime components.
4. **Ensure database directory** — `Path(db_path).parent.mkdir(parents=True, exist_ok=True)` creates `data/` if it does not exist. SQLite cannot create parent directories.
5. **Ensure asset storage directory** — `Path(asset_storage_path).mkdir(parents=True, exist_ok=True)` creates `data/assets/` if it does not exist.
6. **Initialize PlatformSigner** — loads the Ed25519 private key from `platform.private_key_path`. This key is used to sign outgoing JWS tokens for escrow release operations. Fails fast if the key file is missing or invalid.
7. **Initialize IdentityClient** — creates an `httpx.AsyncClient` for calling `POST /agents/verify-jws` on the Identity service. Configured with `base_url`, `verify_jws_path`, and `timeout_seconds` from settings.
8. **Initialize CentralBankClient** — creates an `httpx.AsyncClient` for calling `POST /escrow/lock` and `POST /escrow/{escrow_id}/release` on the Central Bank. Receives the `platform_signer` because escrow release calls require a platform-signed JWS token.
9. **Initialize TaskManager** — the central business logic component. Receives all dependencies: database path, HTTP clients, platform signer, asset config, and platform agent ID. Creates the SQLite database and tables on first use.
10. **Store all in app state** — each component is assigned to the corresponding `AppState` field. Routers access these via `get_app_state()`.
11. **Log startup info** — structured log entry with service metadata, paths, and external service URLs.

**Shutdown sequence explained:**

1. **Log shutdown** — records uptime for operational monitoring.
2. **Close task manager** — closes the SQLite database connection. This is synchronous (`task_manager.close()`), not async.
3. **Close identity client** — closes the `httpx.AsyncClient`. This is async (`await identity_client.close()`).
4. **Close central bank client** — closes the `httpx.AsyncClient`. This is async (`await central_bank_client.close()`).

**Key differences from Identity service lifespan:**

- **More initialization steps** — the Identity service only initializes one component (`AgentRegistry`). The Task Board initializes four: `PlatformSigner`, `IdentityClient`, `CentralBankClient`, and `TaskManager`.
- **Async shutdown** — the Identity service shutdown is synchronous (`state.registry.close()`). The Task Board has both synchronous (`task_manager.close()`) and async (`await client.close()`) shutdown steps because HTTP clients require async cleanup.
- **Directory creation** — the Task Board creates two directories (database and asset storage). The Identity service creates only one (database).
- **Initialization order matters** — `PlatformSigner` must be created before `CentralBankClient` (which depends on it). `IdentityClient` and `CentralBankClient` must be created before `TaskManager` (which depends on both).

---

## File 2: `src/task_board_service/app.py`

Create this file:

```python
"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from task_board_service.config import get_settings
from task_board_service.core.exceptions import register_exception_handlers
from task_board_service.core.lifespan import lifespan
from task_board_service.core.middleware import RequestValidationMiddleware
from task_board_service.routers import assets, bids, health, tasks


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI instance with all routers registered.
    """
    settings = get_settings()

    app = FastAPI(
        title=f"{settings.service.name} Service",
        version=settings.service.version,
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    app.include_router(health.router, tags=["Operations"])
    app.include_router(tasks.router, tags=["Tasks"])
    app.include_router(bids.router, tags=["Bids"])
    app.include_router(assets.router, tags=["Assets"])

    app.add_middleware(
        RequestValidationMiddleware,
        max_body_size=settings.request.max_body_size,
    )

    return app
```

**Design decisions explained:**

1. **Router registration order**: `health` first (lowest priority, least likely to conflict), then `tasks`, `bids`, `assets`. This order does not affect routing behavior — FastAPI merges all routes — but it structures the OpenAPI documentation logically.

2. **Tags**: Each router gets a tag for OpenAPI grouping. `"Operations"` for health (matching the Identity service convention), then domain-specific tags.

3. **Middleware registration**: `RequestValidationMiddleware` is added last via `app.add_middleware()`. Despite being added last in code, ASGI middleware executes in reverse order — this middleware runs FIRST, before any route handler. It validates Content-Type (415) and body size (413) for all POST/PUT/PATCH requests, with special handling for multipart/form-data on asset upload paths.

4. **`create_app()` factory pattern**: Returns a configured `FastAPI` instance. uvicorn calls this with `--factory`: `uvicorn task_board_service.app:create_app --factory --reload`.

5. **Settings are loaded once**: `get_settings()` is called to read `max_body_size` for the middleware. The settings loader caches the result, so subsequent calls during lifespan startup return the same instance.

---

## Verification: Start the Service

```bash
cd services/task-board && just run
```

In another terminal:

```bash
curl -s http://localhost:8003/health | jq .
```

Expected output:

```json
{
  "status": "ok",
  "uptime_seconds": ...,
  "started_at": "...",
  "total_tasks": 0,
  "tasks_by_status": {
    "open": 0,
    "accepted": 0,
    "submitted": 0,
    "approved": 0,
    "cancelled": 0,
    "disputed": 0,
    "ruled": 0,
    "expired": 0
  }
}
```

**Note:** The service will fail to start if `platform.agent_id` or `platform.private_key_path` in `config.yaml` are empty strings and the `PlatformSigner` validates them during initialization. For local development, you can either:
- Generate a test Ed25519 key pair and set the config values
- Or temporarily skip the signer validation (not recommended for production)

Then stop the service:

```bash
cd services/task-board && just kill
```

---

## Verification: Lint

```bash
cd services/task-board && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'task_board_service.clients'`**

Phase 4 (clients) has not been completed. The lifespan imports `IdentityClient`, `CentralBankClient`, and `PlatformSigner` from `task_board_service.clients.*`.

**`ModuleNotFoundError: No module named 'task_board_service.services.task_manager'`**

Phase 5 (service layer) has not been completed. The lifespan imports `TaskManager` from `task_board_service.services.task_manager`.

**`FileNotFoundError` on platform private key path**

The `platform.private_key_path` in `config.yaml` points to a file that does not exist. Generate a test key pair:

```bash
cd services/task-board && uv run python -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
key = Ed25519PrivateKey.generate()
pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
import pathlib
pathlib.Path('data').mkdir(exist_ok=True)
pathlib.Path('data/platform.pem').write_bytes(pem)
print('Key written to data/platform.pem')
"
```

Then update `config.yaml`:

```yaml
platform:
  agent_id: "a-platform-test"
  private_key_path: "data/platform.pem"
```

**Port 8003 already in use**

```bash
cd services/task-board && just kill
```

Or find and kill the process:

```bash
lsof -ti:8003 | xargs kill -9
```
