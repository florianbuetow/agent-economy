"""Explicitly configured Gateway app fixture for MP-08 unit tests."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient

from db_gateway_service.config import clear_settings_cache
from db_gateway_service.core.state import init_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.fakes.gateway_mp08_config import write_gateway_config

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from service_auth.gateway import GatewayRequestSigner

    from db_gateway_service.services.db_writer import DbWriter


class _DomainAuth(httpx.Auth):
    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        domain = request.url.path.split("/", maxsplit=2)[1]
        signer = self._signers[domain]
        protected_delivery = request.url.path.startswith(
            "/board/task-effect-deliveries"
        ) or request.url.path.startswith("/board/legacy-effect-observations/")
        access = "write" if protected_delivery or request.method != "GET" else "read"
        yield signer.sign_request(request, action=f"gateway.{domain}.{access}")


@pytest.fixture
def gateway_mp08_app(
    db_writer: DbWriter,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    tmp_path: Path,
) -> Iterator[TestClient]:
    config_path = tmp_path / "gateway-mp08-config.yaml"
    write_gateway_config(db_writer._db_path, config_path)
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    from db_gateway_service.app import create_app

    app = create_app()
    with TestClient(app) as client:
        client.auth = _DomainAuth(gateway_test_signers)
        state = init_app_state()
        state.db_writer = db_writer
        state.db_reader = DbReader(db_writer.connection)
        yield client
    os.environ.pop("CONFIG_PATH", None)
    clear_settings_cache()


__all__ = ["gateway_mp08_app"]
