"""GAP-E6: Task Board's Pydantic response models must be wired as response_model,
not just declared and left dead — the hand-built dicts had no enforcement.

Two things are proven per endpoint:
1. The model's fields match the real wire shape (e.g. BidResponse.amount, not
   the stale .proposal — the real bidding model is undercutting on amount).
2. The FastAPI route actually declares response_model=<that model>, which is
   the enforcement itself (a route returning a Response subclass directly, as
   these previously did via JSONResponse(...), bypasses response_model
   validation entirely — wiring means returning the plain dict and letting
   FastAPI validate + serialize it).
"""

from __future__ import annotations

import os

import pytest
from fastapi.routing import APIRoute

from task_board_service.app import create_app
from task_board_service.config import clear_settings_cache
from task_board_service.schemas import (
    AssetListResponse,
    AssetResponse,
    BidListResponse,
    BidResponse,
    TaskListResponse,
    TaskResponse,
)

# create_app() only reads settings.service/.request to build the FastAPI
# instance and register routers — it never runs lifespan() — so a minimal
# config is enough here. db_gateway is included only because it is now a
# required Settings field (WP-11), not because create_app() reads it.
_MINIMAL_CONFIG = """\
service:
  name: "task-board"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8003
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
database:
  path: "data/task-board.db"
central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/release"
  escrow_split_path: "/escrow/split"
  timeout_seconds: 10
platform:
  agent_id: "a-platform-test-id"
request:
  max_body_size: 1048576
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
"""


@pytest.fixture
def app(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MINIMAL_CONFIG)
    old_config = os.environ.get("CONFIG_PATH")
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()

    yield create_app()

    if old_config is None:
        os.environ.pop("CONFIG_PATH", None)
    else:
        os.environ["CONFIG_PATH"] = old_config
    clear_settings_cache()


def _route_response_model(app, path: str, method: str) -> object:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route.response_model
    msg = f"No route found for {method} {path}"
    raise AssertionError(msg)


@pytest.mark.unit
def test_bid_response_uses_amount_not_the_stale_proposal_field() -> None:
    """BidResponse must match the real undercutting-bid wire shape."""
    fields = BidResponse.model_fields
    assert "amount" in fields
    assert fields["amount"].annotation is int
    assert "proposal" not in fields


@pytest.mark.unit
def test_asset_response_includes_content_hash() -> None:
    """AssetResponse must match the real upload-response wire shape."""
    assert "content_hash" in AssetResponse.model_fields


@pytest.mark.unit
def test_task_endpoints_wire_response_model(app) -> None:
    assert _route_response_model(app, "/tasks", "POST") is TaskResponse
    assert _route_response_model(app, "/tasks", "GET") is TaskListResponse
    assert _route_response_model(app, "/tasks/{task_id}", "GET") is TaskResponse
    assert _route_response_model(app, "/tasks/{task_id}/cancel", "POST") is TaskResponse
    assert _route_response_model(app, "/tasks/{task_id}/submit", "POST") is TaskResponse
    assert _route_response_model(app, "/tasks/{task_id}/approve", "POST") is TaskResponse
    assert _route_response_model(app, "/tasks/{task_id}/dispute", "POST") is TaskResponse
    assert _route_response_model(app, "/tasks/{task_id}/ruling", "POST") is TaskResponse


@pytest.mark.unit
def test_bid_endpoints_wire_response_model(app) -> None:
    assert _route_response_model(app, "/tasks/{task_id}/bids", "POST") is BidResponse
    assert _route_response_model(app, "/tasks/{task_id}/bids", "GET") is BidListResponse
    assert (
        _route_response_model(app, "/tasks/{task_id}/bids/{bid_id}/accept", "POST") is TaskResponse
    )


@pytest.mark.unit
def test_asset_endpoints_wire_response_model(app) -> None:
    assert _route_response_model(app, "/tasks/{task_id}/assets", "POST") is AssetResponse
    assert _route_response_model(app, "/tasks/{task_id}/assets", "GET") is AssetListResponse
