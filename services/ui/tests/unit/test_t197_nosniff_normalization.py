"""T-197 regression for authoritative browser MIME-sniffing protection."""

from __future__ import annotations

import httpx
import pytest

from ui_service.config import BrowserSecurityConfig
from ui_service.core.middleware import BrowserSecurityMiddleware

pytestmark = pytest.mark.unit


async def test_browser_security_replaces_all_upstream_content_type_options() -> None:
    async def application(_scope, _receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"x-content-type-options", b"unsafe"),
                    (b"X-Content-Type-Options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    policy = BrowserSecurityConfig(
        allowed_hosts=["localhost"],
        allowed_origins=["http://localhost"],
        csrf_header_name="X-ATE-CSRF",
        csrf_header_value="local-operator",
        content_security_policy="default-src 'self'; object-src 'none'",
    )
    app = BrowserSecurityMiddleware(application, **policy.model_dump())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
    ) as client:
        response = await client.get("/api/proxy/tasks/t-one/assets/asset-one")

    assert response.headers.get_list("x-content-type-options") == ["nosniff"]
