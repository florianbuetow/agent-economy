"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ui_service.config import get_settings
from ui_service.core.exceptions import register_exception_handlers
from ui_service.core.lifespan import lifespan
from ui_service.routers import agents, events, health, metrics, proxy, quarterly, tasks


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
    app.include_router(agents.router, prefix="/api", tags=["Agents"])
    app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
    app.include_router(events.router, prefix="/api", tags=["Events"])
    app.include_router(quarterly.router, prefix="/api", tags=["Quarterly"])
    app.include_router(proxy.router, prefix="/api", tags=["Proxy"])

    # Keep static frontend mount last, because SPA fallback captures all unmatched paths.
    _mount_frontend(app, settings.frontend.web_root)

    return app


def _mount_frontend(app: FastAPI, web_root: str) -> None:
    """Mount frontend static files with SPA fallback if web root directory exists."""
    web_dir = Path(web_root)
    if not web_dir.is_dir():
        return

    index_html = web_dir / "index.html"
    assets_dir = web_dir / "assets"

    if assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="static-assets",
        )

    async def spa_fallback(full_path: str) -> HTMLResponse:
        """Serve index.html for SPA client-side routing."""
        file_path = web_dir / full_path
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
