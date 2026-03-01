# Observatory Frontend Build & Serve Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add frontend build steps to the observatory justfile and serve the built static files from the Python backend.

**Architecture:** The observatory service has a React/Vite frontend in `services/observatory/frontend/` but no build step exists and the backend never mounts the built files. We add `just build-frontend` and `just init-frontend` commands using `npm`, integrate them into `just init`, then mount `frontend/dist` as static files in `app.py` with SPA fallback so non-API routes serve `index.html`.

**Tech Stack:** npm (Node.js package manager), Vite (frontend build), FastAPI StaticFiles, starlette

---

## Ticket: agent-economy-y65 — Add frontend build step to observatory init/build workflow

### Task 1: Add `build-frontend` and `init-frontend` commands to justfile

**Files:**
- Modify: `services/observatory/justfile`

**Step 1: Add the `init-frontend` and `build-frontend` recipes to the justfile**

Add these recipes after the existing `init` recipe (after line 109). Also update the `check` recipe to verify `node` and `npm` are available, and update `init` to call `init-frontend`.

In the `check` recipe, after the `check_tool "lsof" lsof "-v"` line (line 85), add:

```just
    check_tool "node"    node   "--version"
    check_tool "npm"     npm    "--version"
```

In the `init` recipe, before the final `printf` success line, add the npm install inline:

```just
    @echo "Installing frontend dependencies..."
    @cd frontend && npm install
```

Do NOT create a separate `init-frontend` recipe. The npm install belongs inline in `init`.

Add this new recipe right after the `init` recipe:

```just
# Build frontend for production
build-frontend:
    @echo ""
    @printf "\033[0;34m=== Building Frontend ===\033[0m\n"
    @cd frontend && npm run build
    @printf "\033[0;32m✓ Frontend built to frontend/dist/\033[0m\n"
    @echo ""
```

**Step 2: Run the new commands to verify they work**

Run from `services/observatory/`:
```bash
just init-frontend
just build-frontend
ls frontend/dist/index.html
```
Expected: `npm install` succeeds, `npm run build` succeeds, `frontend/dist/index.html` exists.

**Step 3: Commit**

```bash
git add services/observatory/justfile
git commit -m "feat(observatory): add frontend build steps to justfile

Add init-frontend and build-frontend commands. Integrate init-frontend
into the init recipe. Add node/npm to tool checks."
```

---

## Ticket: agent-economy-r63 — Serve built frontend static files from observatory Python backend

### Task 2: Mount frontend static files in app.py with SPA fallback

**Files:**
- Modify: `services/observatory/src/observatory_service/app.py`

**Step 1: Write the failing test**

Create a new test file. The test verifies that the app serves static files from `frontend/dist` and that non-API routes fall back to `index.html`.

Create file: `services/observatory/tests/unit/test_static_files.py`

```python
"""Tests for frontend static file serving."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.mark.unit
class TestStaticFileServing:
    """Tests for serving built frontend files."""

    def test_index_html_served_at_root(self, tmp_path: Path) -> None:
        """GET / returns index.html when frontend/dist exists."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<html><body>Observatory</body></html>")

        with patch(
            "observatory_service.app.get_settings"
        ) as mock_settings:
            settings = mock_settings.return_value
            settings.service.name = "observatory"
            settings.service.version = "0.1.0"
            settings.frontend.dist_path = str(dist_dir)

            from observatory_service.app import create_app

            app = create_app()
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get("/")
            assert response.status_code == 200
            assert "Observatory" in response.text

    def test_api_routes_take_priority(self, tmp_path: Path) -> None:
        """API routes respond even when static files are mounted."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<html></html>")

        with patch(
            "observatory_service.app.get_settings"
        ) as mock_settings:
            settings = mock_settings.return_value
            settings.service.name = "observatory"
            settings.service.version = "0.1.0"
            settings.frontend.dist_path = str(dist_dir)

            from observatory_service.app import create_app

            app = create_app()
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"

    def test_spa_fallback_serves_index(self, tmp_path: Path) -> None:
        """Non-API routes that don't match a file serve index.html (SPA routing)."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<html><body>SPA</body></html>")

        with patch(
            "observatory_service.app.get_settings"
        ) as mock_settings:
            settings = mock_settings.return_value
            settings.service.name = "observatory"
            settings.service.version = "0.1.0"
            settings.frontend.dist_path = str(dist_dir)

            from observatory_service.app import create_app

            app = create_app()
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get("/dashboard")
            assert response.status_code == 200
            assert "SPA" in response.text

    def test_no_static_mount_when_dist_missing(self) -> None:
        """App starts without error when frontend/dist doesn't exist."""
        with patch(
            "observatory_service.app.get_settings"
        ) as mock_settings:
            settings = mock_settings.return_value
            settings.service.name = "observatory"
            settings.service.version = "0.1.0"
            settings.frontend.dist_path = "/nonexistent/path"

            from observatory_service.app import create_app

            app = create_app()
            client = TestClient(app, raise_server_exceptions=False)

            response = client.get("/health")
            assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

Run from `services/observatory/`:
```bash
uv run pytest tests/unit/test_static_files.py -v
```
Expected: FAIL — the app doesn't serve static files yet, `GET /` will return 404 or a JSON error.

**Step 3: Implement static file serving in app.py**

Modify `services/observatory/src/observatory_service/app.py`:

```python
"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from observatory_service.config import get_settings
from observatory_service.core.exceptions import register_exception_handlers
from observatory_service.core.lifespan import lifespan
from observatory_service.routers import agents, events, health, metrics, quarterly, tasks


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
    app.include_router(metrics.router, prefix="/api", tags=["Metrics"])
    app.include_router(events.router, prefix="/api", tags=["Events"])
    app.include_router(agents.router, prefix="/api", tags=["Agents"])
    app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
    app.include_router(quarterly.router, prefix="/api", tags=["Quarterly"])

    _mount_frontend(app, settings.frontend.dist_path)

    return app


def _mount_frontend(app: FastAPI, dist_path: str) -> None:
    """Mount frontend static files with SPA fallback if dist directory exists."""
    dist_dir = Path(dist_path)
    if not dist_dir.is_dir():
        return

    index_html = dist_dir / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> HTMLResponse:
        """Serve index.html for SPA client-side routing."""
        file_path = dist_dir / full_path
        if full_path and file_path.is_file():
            return HTMLResponse(content=file_path.read_text())
        if index_html.is_file():
            return HTMLResponse(content=index_html.read_text())
        return HTMLResponse(content="Not Found", status_code=404)

    app.mount("/assets", StaticFiles(directory=str(dist_dir / "assets")), name="static-assets")
```

**Important:** The catch-all route is registered after all API routers so `/health`, `/api/*` routes take priority. The `/assets` mount handles Vite's hashed asset files efficiently via StaticFiles. The `spa_fallback` catch-all handles all other paths by returning `index.html` for client-side routing.

**Wait — this approach has a subtlety.** The `/assets` mount will fail if the `assets/` subdirectory doesn't exist (e.g., before a build). Let's make it conditional too. Here's the corrected `_mount_frontend`:

```python
def _mount_frontend(app: FastAPI, dist_path: str) -> None:
    """Mount frontend static files with SPA fallback if dist directory exists."""
    dist_dir = Path(dist_path)
    if not dist_dir.is_dir():
        return

    index_html = dist_dir / "index.html"
    assets_dir = dist_dir / "assets"

    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="static-assets",
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> HTMLResponse:
        """Serve index.html for SPA client-side routing."""
        file_path = dist_dir / full_path
        if full_path and file_path.is_file():
            return HTMLResponse(content=file_path.read_text())
        if index_html.is_file():
            return HTMLResponse(content=index_html.read_text())
        return HTMLResponse(content="Not Found", status_code=404)
```

**Step 4: Run tests to verify they pass**

Run from `services/observatory/`:
```bash
uv run pytest tests/unit/test_static_files.py -v
```
Expected: All 4 tests PASS.

**Step 5: Run full CI to verify nothing is broken**

Run from `services/observatory/`:
```bash
just ci-quiet
```
Expected: All CI checks pass.

**Step 6: Commit**

```bash
git add services/observatory/src/observatory_service/app.py services/observatory/tests/unit/test_static_files.py
git commit -m "feat(observatory): serve frontend static files with SPA fallback

Mount frontend/dist as static files in create_app(). API and health
routes take priority. Non-API routes fall back to index.html for
client-side routing. Gracefully skips when dist/ doesn't exist."
```

---

### Task 3: Update Dockerfile to include frontend build

**Files:**
- Modify: `services/observatory/Dockerfile`

**Step 1: Update the Dockerfile to build frontend during image creation**

Replace the Dockerfile content with:

```dockerfile
FROM node:22-slim AS frontend-build

WORKDIR /frontend
COPY services/observatory/frontend/package.json services/observatory/frontend/package-lock.json ./
RUN npm ci
COPY services/observatory/frontend/ .
RUN npm run build

FROM python:3.12-slim

WORKDIR /repo/services/observatory

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY libs/service-commons/ /repo/libs/service-commons/
COPY services/observatory/pyproject.toml services/observatory/uv.lock ./
RUN uv sync --frozen --no-dev

COPY services/observatory/config.yaml .
COPY services/observatory/src/ src/
COPY --from=frontend-build /frontend/dist frontend/dist/

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "observatory_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
```

This uses a multi-stage build: stage 1 builds the frontend with Node, stage 2 copies the built `dist/` into the Python image.

**Step 2: Verify Dockerfile syntax**

```bash
docker build --check -f services/observatory/Dockerfile . 2>&1 || echo "Syntax check not supported, skip"
```

**Step 3: Commit**

```bash
git add services/observatory/Dockerfile
git commit -m "feat(observatory): add multi-stage Docker build with frontend

Use node:22-slim stage to build React frontend, copy dist/ into
the final Python image."
```

---

### Task 4: Final verification

**Step 1: Run full CI from the observatory service directory**

```bash
cd services/observatory && just ci-quiet
```
Expected: All checks pass.

**Step 2: Run full project CI**

```bash
just ci-all-quiet
```
Expected: All checks pass.

**Step 3: Manually verify frontend serving works end-to-end**

```bash
cd services/observatory
just build-frontend
just run
# In another terminal:
curl -s http://localhost:8006/ | head -5
curl -s http://localhost:8006/health | jq .
curl -s http://localhost:8006/dashboard | head -5
```
Expected:
- `GET /` returns the built HTML page
- `GET /health` returns `{"status": "ok", ...}`
- `GET /dashboard` returns the same HTML (SPA fallback)
