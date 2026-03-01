"""Tests for frontend static file serving."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from observatory_service.app import create_app

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
class TestStaticFileServing:
    """Tests for serving built frontend files."""

    def test_index_html_served_at_root(self, tmp_path: Path) -> None:
        """GET / returns index.html when frontend/dist exists."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<html><body>Observatory</body></html>")

        with patch("observatory_service.app.get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.service.name = "observatory"
            settings.service.version = "0.1.0"
            settings.frontend.dist_path = str(dist_dir)

            app = create_app()
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/")
            assert response.status_code == 200
            assert "Observatory" in response.text

    def test_api_routes_take_priority(self, tmp_path: Path) -> None:
        """API routes respond even when static files are mounted."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<html></html>")

        with patch("observatory_service.app.get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.service.name = "observatory"
            settings.service.version = "0.1.0"
            settings.frontend.dist_path = str(dist_dir)

            app = create_app()
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"

    def test_spa_fallback_serves_index(self, tmp_path: Path) -> None:
        """Non-API routes that don't match a file serve index.html (SPA routing)."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<html><body>SPA</body></html>")

        with patch("observatory_service.app.get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.service.name = "observatory"
            settings.service.version = "0.1.0"
            settings.frontend.dist_path = str(dist_dir)

            app = create_app()
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/dashboard")
            assert response.status_code == 200
            assert "SPA" in response.text

    def test_no_static_mount_when_dist_missing(self) -> None:
        """App starts without error when frontend/dist doesn't exist."""
        with patch("observatory_service.app.get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.service.name = "observatory"
            settings.service.version = "0.1.0"
            settings.frontend.dist_path = "/nonexistent/path"

            app = create_app()
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/health")
            assert response.status_code == 200
