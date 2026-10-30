"""FastAPI application factory."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ui_service.config import get_settings
from ui_service.core.exceptions import register_exception_handlers
from ui_service.core.lifespan import lifespan
from ui_service.core.middleware import BrowserSecurityMiddleware, RequestValidationMiddleware
from ui_service.routers import agents, events, health, metrics, proxy, quarterly, tasks

_ASSET_REFERENCE = re.compile(
    r"(?P<prefix>\b(?:src|href)\s*=\s*(?P<quote>[\"']))"
    r"(?P<url>/assets/[^\"']+)"
    r"(?P=quote)",
    re.IGNORECASE,
)
_HTML_CACHE_HEADERS = {"Cache-Control": "no-cache"}
# Interactive API documentation is disabled; the SPA fallback must not answer for it.
_DISABLED_DOC_ROUTES = frozenset({"docs", "redoc", "openapi.json"})


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
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    register_exception_handlers(app)

    app.include_router(health.router, tags=["Operations"])
    app.include_router(metrics.router, prefix="/api", tags=["Metrics"])
    app.include_router(agents.router, prefix="/api", tags=["Agents"])
    app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
    app.include_router(events.router, prefix="/api", tags=["Events"])
    app.include_router(quarterly.router, prefix="/api", tags=["Quarterly"])
    app.include_router(proxy.router, prefix="/api", tags=["Proxy"])

    app.add_middleware(
        RequestValidationMiddleware,
        max_body_size=settings.request.max_body_size,
    )
    app.add_middleware(
        BrowserSecurityMiddleware,
        allowed_hosts=settings.browser_security.allowed_hosts,
        allowed_origins=settings.browser_security.allowed_origins,
        csrf_header_name=settings.browser_security.csrf_header_name,
        csrf_header_value=settings.browser_security.csrf_header_value,
        content_security_policy=settings.browser_security.content_security_policy,
    )

    # Keep static frontend mount last, because SPA fallback captures all unmatched paths.
    _mount_frontend(app, settings.frontend.web_root)

    return app


def _mount_frontend(app: FastAPI, web_root: str) -> None:
    """Mount frontend static files with SPA fallback if web root directory exists."""
    web_dir = Path(web_root).resolve()
    if not web_dir.is_dir():
        return

    assets_dir = _contained_frontend_path(web_dir, "assets")

    if assets_dir is not None and assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="static-assets",
        )

    async def spa_fallback(full_path: str) -> HTMLResponse:
        """Serve index.html for SPA client-side routing."""
        if full_path.strip("/").split("/")[0] in _DISABLED_DOC_ROUTES:
            return HTMLResponse(content="Not Found", status_code=404)
        file_path = _contained_frontend_path(web_dir, full_path)
        if file_path is None:
            return HTMLResponse(content="Not Found", status_code=404)
        if full_path and file_path.is_file():
            if file_path.suffix.lower() == ".html":
                return _versioned_html_response(web_dir, file_path)
            return HTMLResponse(content=file_path.read_text())
        index_html = _contained_frontend_path(web_dir, "index.html")
        if index_html is not None and index_html.is_file():
            return _versioned_html_response(web_dir, index_html)
        return HTMLResponse(content="Not Found", status_code=404)

    app.add_api_route(
        "/{full_path:path}",
        spa_fallback,
        methods=["GET"],
        include_in_schema=False,
    )


def _contained_frontend_path(web_dir: Path, requested_path: str) -> Path | None:
    """Keep decoded requests and symlinks within the configured static root."""
    try:
        resolved = (web_dir / requested_path).resolve()
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_relative_to(web_dir) else None


def _versioned_html_response(web_dir: Path, html_path: Path) -> HTMLResponse:
    """Serve owned HTML with content-addressed references to owned assets."""

    def version_asset(match: re.Match[str]) -> str:
        asset_url = match.group("url")
        parsed = urlsplit(asset_url)
        decoded_path = unquote(parsed.path)
        if ".." in Path(decoded_path).parts:
            return match.group(0)
        asset_path = _contained_frontend_path(web_dir, decoded_path.lstrip("/"))
        if (
            asset_path is None
            or not asset_path.is_file()
            or asset_path.suffix.lower() not in {".js", ".css"}
        ):
            return match.group(0)
        try:
            digest = hashlib.sha256(asset_path.read_bytes()).hexdigest()[:12]
        except OSError:
            return match.group(0)
        query = f"{parsed.query}&v={digest}" if parsed.query else f"v={digest}"
        versioned_url = urlunsplit(("", "", parsed.path, query, parsed.fragment))
        quote = match.group("quote")
        return f"{match.group('prefix')}{versioned_url}{quote}"

    content = _ASSET_REFERENCE.sub(version_asset, html_path.read_text())
    return HTMLResponse(content=content, headers=_HTML_CACHE_HEADERS)
