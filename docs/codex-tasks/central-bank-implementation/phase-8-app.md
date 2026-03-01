# Phase 8 — Application Factory and Lifespan

## Working Directory

All paths relative to `services/central-bank/`.

---

## Task B8: Implement lifespan and app factory

### Step 8.1: Write core/lifespan.py

Create `services/central-bank/src/central_bank_service/core/lifespan.py`:

```python
"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from central_bank_service.config import get_settings
from central_bank_service.core.state import init_app_state
from central_bank_service.logging import get_logger, setup_logging
from central_bank_service.services.identity_client import IdentityClient
from central_bank_service.services.ledger import Ledger

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

    # Initialize ledger
    state.ledger = Ledger(db_path=db_path)

    # Initialize identity client
    state.identity_client = IdentityClient(
        base_url=settings.identity.base_url,
        verify_jws_path=settings.identity.verify_jws_path,
        get_agent_path=settings.identity.get_agent_path,
    )

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
        },
    )

    yield  # Application runs here

    # === SHUTDOWN ===
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})
    if state.identity_client is not None:
        await state.identity_client.close()
    if state.ledger is not None:
        state.ledger.close()
```

### Step 8.2: Write app.py

Create `services/central-bank/src/central_bank_service/app.py`:

```python
"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from central_bank_service.config import get_settings
from central_bank_service.core.exceptions import register_exception_handlers
from central_bank_service.core.lifespan import lifespan
from central_bank_service.core.middleware import RequestValidationMiddleware
from central_bank_service.routers import accounts, escrow, health


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI instance with all routers registered
    """
    settings = get_settings()

    app = FastAPI(
        title=f"{settings.service.name} Service",
        version=settings.service.version,
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    app.include_router(health.router, tags=["Operations"])
    app.include_router(accounts.router, tags=["Accounts"])
    app.include_router(escrow.router, tags=["Escrow"])

    app.add_middleware(
        RequestValidationMiddleware,
        max_body_size=settings.request.max_body_size,
    )

    return app
```

### Step 8.3: Verify service starts

```bash
cd services/central-bank && just run
# In another terminal: curl http://localhost:8002/health
# Then: Ctrl+C to stop
```

Expected: Service starts on port 8002, health check returns `{"status":"ok",...}`.

### Step 8.4: Commit

```bash
git add services/central-bank/src/central_bank_service/core/lifespan.py services/central-bank/src/central_bank_service/app.py
git commit -m "feat(central-bank): add lifespan and app factory"
```

---

## Verification

```bash
cd services/central-bank && uv run ruff check src/ && uv run ruff format --check src/
```
