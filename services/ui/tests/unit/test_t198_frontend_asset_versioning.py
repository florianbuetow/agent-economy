"""T-198: owned HTML references current local frontend asset bytes."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import httpx
import pytest

from ui_service.app import create_app
from ui_service.config import FrontendConfig, get_settings

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.fixture
def versioned_frontend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    web = tmp_path / "web"
    assets = web / "assets"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("window.version = 1;")
    (assets / "site.css").write_text("body { color: red; }")
    outside = tmp_path / "outside.js"
    outside.write_text("window.outside = true;")
    (assets / "linked.js").symlink_to(outside)
    (web / "index.html").write_text(
        """<!doctype html>
        <link rel="stylesheet" href="/assets/site.css">
        <script src="/assets/app.js"></script>
        <script src="/assets/../outside.js"></script>
        <script src="/assets/linked.js"></script>
        <script src="https://cdn.example/assets/external.js"></script>
        """
    )
    settings = get_settings().model_copy(update={"frontend": FrontendConfig(web_root=str(web))})
    monkeypatch.setattr("ui_service.app.get_settings", lambda: settings)
    return web


def _asset_url(html: str, asset_name: str) -> str:
    match = re.search(rf"(?P<url>/assets/{re.escape(asset_name)}\?v=[0-9a-f]+)", html)
    assert match is not None
    return match.group("url")


async def test_html_versions_contained_assets_and_revalidates_after_change(
    versioned_frontend: Path,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost:8008"
    ) as client:
        first = await client.get("/")
        first_js_url = _asset_url(first.text, "app.js")
        css_url = _asset_url(first.text, "site.css")

        assert first.headers["cache-control"] == "no-cache"
        assert (await client.get(first_js_url)).content == b"window.version = 1;"
        assert (await client.get(css_url)).content == b"body { color: red; }"

        (versioned_frontend / "assets/app.js").write_text("window.version = 2;")
        second = await client.get("/")
        second_js_url = _asset_url(second.text, "app.js")

        assert second_js_url != first_js_url
        assert (await client.get(second_js_url)).content == b"window.version = 2;"


async def test_html_does_not_version_traversal_symlink_or_external_references(
    versioned_frontend: Path,
) -> None:
    del versioned_frontend
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://localhost:8008"
    ) as client:
        response = await client.get("/")

    assert 'src="/assets/../outside.js"' in response.text
    assert 'src="/assets/linked.js"' in response.text
    assert 'src="https://cdn.example/assets/external.js"' in response.text
