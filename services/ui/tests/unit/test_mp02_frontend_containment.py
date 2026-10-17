"""The page fallback must not render files outside its configured frontend root."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from ui_service.app import create_app
from ui_service.config import FrontendConfig, get_settings

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.fixture
def frontend_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<p>local index</p>")
    (web / "task.html").write_text("<p>local task</p>")
    outside = tmp_path / "outside.html"
    outside.write_text("<p>outside-root marker</p>")
    (web / "linked.html").symlink_to(outside)
    settings = get_settings().model_copy(update={"frontend": FrontendConfig(web_root=str(web))})
    monkeypatch.setattr("ui_service.app.get_settings", lambda: settings)
    return web, outside


@pytest.mark.parametrize("path", ["/..%2foutside.html", "/%2e%2e/outside.html", "/linked.html"])
async def test_page_requests_cannot_read_outside_frontend(frontend_tree, path):
    del frontend_tree
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost:8008"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 404
    assert "outside-root marker" not in response.text


async def test_absolute_page_path_cannot_replace_frontend_root(frontend_tree):
    _web, outside = frontend_tree
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost:8008"
    ) as client:
        response = await client.get("/%2f" + str(outside).lstrip("/"))
    assert response.status_code == 404
    assert "outside-root marker" not in response.text


async def test_symlinked_index_cannot_escape_on_fallback(frontend_tree):
    web, outside = frontend_tree
    (web / "index.html").unlink()
    (web / "index.html").symlink_to(outside)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost:8008"
    ) as client:
        response = await client.get("/unknown-page")
    assert response.status_code == 404
    assert "outside-root marker" not in response.text


async def test_symlinked_asset_mount_cannot_escape_frontend(frontend_tree):
    web, outside = frontend_tree
    (web / "assets").symlink_to(outside.parent, target_is_directory=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost:8008"
    ) as client:
        response = await client.get("/assets/outside.html")
    assert response.status_code == 404
    assert "outside-root marker" not in response.text


@pytest.mark.parametrize(
    ("path", "content"), [("/task.html", "local task"), ("/unknown-page", "local index")]
)
async def test_regular_pages_and_index_fallback_still_work(frontend_tree, path, content):
    del frontend_tree
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost:8008"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert content in response.text
