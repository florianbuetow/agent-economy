"""Real Gateway -> Board -> UserAgent -> UI financial projection proof."""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import aiosqlite
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from service_auth.config import AgentConfig
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.jws import TokenPolicy
from service_auth.user_agent import UserAgent

from ui_service.core.exceptions import register_exception_handlers as register_ui_exceptions
from ui_service.core.state import init_app_state as init_ui_state
from ui_service.core.state import reset_app_state as reset_ui_state
from ui_service.routers import tasks as ui_tasks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[4]
_BUILD_HISTORY = Path(__file__).resolve().parents[1] / "fakes" / "mp08_financial_history.py"
_KINDS = ("receipt_projection_pending", "conflict_wrong_receipt")


def _service_module(service: str, module: str) -> Any:
    source = _ROOT / "services" / service / "src"
    source_text = str(source)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    clients = str(_ROOT / "libs" / "service-clients" / "src")
    if clients not in sys.path:
        sys.path.insert(0, clients)
    return importlib.import_module(module)


def _path_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_history(tmp_path: Path, kind: str) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.pop("CONFIG_PATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["UV_FROZEN"] = "1"
    built = subprocess.run(
        ["uv", "run", "--no-sync", "python", str(_BUILD_HISTORY), str(tmp_path), kind],
        cwd=_ROOT / "services" / "db-gateway",
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    return cast("dict[str, Any]", json.loads(built.stdout.strip().splitlines()[-1]))


class _NoDeadlineMutation:
    async def evaluate_deadline(self, task: dict[str, Any]) -> dict[str, Any]:
        return task

    async def evaluate_deadlines_batch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return tasks


@pytest.fixture(params=_KINDS)
async def real_boundary(  # noqa: PLR0915
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, str, str, dict[str, object]]]:
    history = _build_history(tmp_path / str(request.param), str(request.param))
    database = Path(history["database"])
    config_dir = tmp_path / f"gateway-{request.param}"
    config_dir.mkdir()

    gateway_config_fake = _path_module(
        "ui_mp08_gateway_config",
        _ROOT / "services" / "db-gateway" / "tests" / "fakes" / "gateway_mp08_config.py",
    )
    signing = importlib.import_module("service_auth.signing")
    for handle in (
        "gateway-identity",
        "gateway-central-bank",
        "gateway-task-board",
        "gateway-reputation",
        "gateway-court",
    ):
        signing.generate_keypair(handle, config_dir)
    config_path = config_dir / "config.yaml"
    gateway_config_fake.write_gateway_config(str(database), config_path)
    config_path.write_text(
        config_path.read_text().replace(
            "../../docs/specifications/schema.sql",
            str(_ROOT / "docs" / "specifications" / "schema.sql"),
        )
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))

    gateway_config = _service_module("db-gateway", "db_gateway_service.config")
    gateway_state = _service_module("db-gateway", "db_gateway_service.core.state")
    gateway_app_module = _service_module("db-gateway", "db_gateway_service.app")
    gateway_config.clear_settings_cache()
    gateway_state.reset_app_state()

    task_db_module = _service_module("task-board", "task_board_service.services.task_db_client")
    task_manager_module = _service_module("task-board", "task_board_service.services.task_manager")
    board_state = _service_module("task-board", "task_board_service.core.state")
    board_tasks = _service_module("task-board", "task_board_service.routers.tasks")
    board_exceptions = _service_module("task-board", "task_board_service.core.exceptions")

    gateway_app = gateway_app_module.create_app()
    reset_ui_state()
    board_state.reset_app_state()
    gateway_client = TestClient(gateway_app)
    gateway_client.__enter__()
    task_store = None
    user_agent = None
    ui_db = None
    try:
        signer = GatewayRequestSigner(
            GatewaySignerConfig(
                kid="gateway-task-board",
                private_key_path=config_dir / "gateway-task-board.key",
                audience="db-gateway",
                token_ttl_seconds=30,
            ),
            config_dir=config_dir,
        )
        task_store = task_db_module.TaskDbClient(
            base_url="http://gateway.test",
            timeout_seconds=10,
            signer=signer,
        )
        task_store._client.close()
        task_store._client = httpx.Client(
            transport=gateway_client._transport,
            base_url="http://gateway.test",
        )
        authoritative = task_store.get_task(str(history["task_id"]))
        assert authoritative is not None

        monkeypatch.setattr(task_manager_module, "get_logger", logging.getLogger)
        manager = task_manager_module.TaskManager(
            store=task_store,
            central_bank_client=cast("Any", object()),
            escrow_coordinator=cast("Any", object()),
            token_validator=cast("Any", object()),
            deadline_evaluator=cast("Any", _NoDeadlineMutation()),
            asset_manager=cast("Any", object()),
            platform_signer=cast("Any", object()),
            platform_agent_id="a-platform-test",
        )
        board_runtime = board_state.init_app_state()
        board_runtime.task_manager = manager
        board_runtime.store = task_store
        board_app = FastAPI()
        board_exceptions.register_exception_handlers(board_app)
        board_app.include_router(board_tasks.router)

        private_key = Ed25519PrivateKey.generate()
        user_agent = UserAgent(
            AgentConfig(
                name="UI read boundary",
                private_key=private_key,
                public_key=private_key.public_key(),
                identity_url="http://identity.test",
                bank_url="http://bank.test",
                task_board_url="http://task-board.test",
                reputation_url="http://reputation.test",
                court_url="http://court.test",
                token_ttl_seconds=30,
                token_policy=TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0),
            )
        )
        await user_agent._http.aclose()
        user_agent._http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=board_app),
            base_url="http://task-board.test",
        )

        ui_db = await aiosqlite.connect(f"file:{database}?mode=ro", uri=True)
        ui_db.row_factory = aiosqlite.Row
        ui_runtime = init_ui_state()
        ui_runtime.db = ui_db
        ui_runtime.task_board_reader = user_agent
        ui_app = FastAPI()
        register_ui_exceptions(ui_app)
        ui_app.include_router(ui_tasks.router, prefix="/api")
        ui_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ui_app),
            base_url="http://ui.test",
        )
        async with ui_client:
            yield (
                ui_client,
                str(history["task_id"]),
                str(request.param),
                cast("dict[str, object]", authoritative["financial_effect"]),
            )
    finally:
        if ui_db is not None:
            await ui_db.close()
        if user_agent is not None:
            await user_agent.close()
        if task_store is not None:
            task_store.close()
        gateway_client.__exit__(None, None, None)
        board_state.reset_app_state()
        reset_ui_state()
        gateway_state.reset_app_state()
        gateway_config.clear_settings_cache()


async def test_real_services_preserve_authoritative_financial_effect(
    real_boundary: tuple[httpx.AsyncClient, str, str, dict[str, object]],
) -> None:
    client, task_id, kind, gateway_effect = real_boundary

    detail = await client.get(f"/api/tasks/{task_id}")
    listing = await client.get("/api/tasks?limit=100&offset=0")

    assert detail.status_code == 200, detail.text
    assert listing.status_code == 200, listing.text
    listed = [task for task in listing.json()["tasks"] if task["task_id"] == task_id]
    assert len(listed) == 1
    assert detail.json()["financial_effect"] == gateway_effect
    assert listed[0]["financial_effect"] == gateway_effect
    expected = {
        "receipt_projection_pending": ("pending", "confirmed", "worker_paid"),
        "conflict_wrong_receipt": ("conflict", "absent", None),
    }[kind]
    assert (
        gateway_effect["settlement_state"],
        gateway_effect["receipt_state"],
        gateway_effect["outcome"],
    ) == expected
