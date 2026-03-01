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
    assets_dir = dist_dir / "assets"

    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="static-assets",
        )

    async def spa_fallback(full_path: str) -> HTMLResponse:
        """Serve index.html for SPA client-side routing."""
        file_path = dist_dir / full_path
        if full_path and file_path.is_file():
            return HTMLResponse(content=file_path.read_text())
        if index_html.is_file():
            return HTMLResponse(content=index_html.read_text())
        return HTMLResponse(content="Not Found", status_code=404)

    app.add_api_route(
        "/{full_path:path}",
        spa_fallback,
        methods=["GET"],
        include_in_schema=False,
    )
