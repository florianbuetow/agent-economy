"""Integration test fixtures."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import yaml
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig

from db_gateway_service.config import Settings, get_config_path, get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator


class GatewayIntegrationAuth(httpx.Auth):
    """Select the distinct provisioned service capability for each domain."""

    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        domain = request.url.path.split("/", maxsplit=2)[1]
        if domain == "health" and request.method == "GET" and not request.url.query:
            yield request
            return
        signer = self._signers[domain]
        access = "read" if request.method == "GET" else "write"
        yield signer.sign_request(request, action=f"gateway.{domain}.{access}")


@pytest.fixture
def gateway_url() -> str:
    """Base URL for the running Database Gateway service."""
    configured_url = os.environ.get("DB_GATEWAY_URL")
    if configured_url is not None:
        return configured_url
    settings = get_settings()
    return f"http://{settings.server.host}:{settings.server.port}"


@pytest.fixture
def gateway_auth() -> GatewayIntegrationAuth:
    """Load each caller's private pair from the live Gateway trust configuration."""
    runtime_dir = os.environ.get("ISOLATED_RUNTIME_DIR")
    if runtime_dir is None:
        settings = get_settings()
        config_dir = get_config_path().parent
    else:
        config_path = Path(runtime_dir) / "configs" / "db-gateway.yaml"
        settings = Settings.model_validate(yaml.safe_load(config_path.read_text()))
        config_dir = config_path.parent
    domains = {
        "identity": "gateway-identity",
        "bank": "gateway-central-bank",
        "board": "gateway-task-board",
        "reputation": "gateway-reputation",
        "court": "gateway-court",
    }
    signers: dict[str, GatewayRequestSigner] = {}
    for domain, kid in domains.items():
        trusted = settings.auth.trusted_callers[kid]
        private_key_path = trusted.public_key_path.with_suffix(".key")
        if not private_key_path.is_absolute():
            private_key_path = config_dir / private_key_path
        signers[domain] = GatewayRequestSigner(
            GatewaySignerConfig(
                kid=kid,
                private_key_path=Path(private_key_path),
                audience=settings.auth.audience,
                token_ttl_seconds=settings.auth.max_lifetime_seconds,
            ),
            config_dir=config_dir,
        )
    return GatewayIntegrationAuth(signers)


@pytest.fixture
def gateway_client(gateway_url: str, gateway_auth: GatewayIntegrationAuth) -> httpx.Client:
    """HTTP client for the gateway."""
    return httpx.Client(base_url=gateway_url, auth=gateway_auth)
