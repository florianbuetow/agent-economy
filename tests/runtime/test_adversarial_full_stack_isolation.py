"""Full-stack isolation coverage for OPS-07."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import yaml
from scripts.isolated_test_runtime import SERVICE_ORDER, IsolatedRuntime
from service_auth import AgentFactory
from tests.runtime.test_recovery_live_restore import DELIVERABLE, _public_workload

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _runtime_paths(runtime: IsolatedRuntime) -> set[Path]:
    board = yaml.safe_load(runtime.config_paths["task-board"].read_text())
    ui = yaml.safe_load(runtime.config_paths["ui"].read_text())
    gateway = yaml.safe_load(runtime.config_paths["db-gateway"].read_text())
    return {
        runtime.run_dir,
        runtime.run_dir / "keys",
        runtime.run_dir / "data" / "economy.db",
        Path(board["assets"]["storage_path"]),
        Path(board["recovery"]["maintenance_lock_path"]),
        Path(ui["recovery"]["maintenance_lock_path"]),
        Path(gateway["recovery"]["maintenance_lock_path"]),
        runtime.agent_config_path,
    }


def _assert_healthy(runtime: IsolatedRuntime) -> None:
    for service in SERVICE_ORDER:
        response = httpx.get(f"{runtime.service_url(service)}/health", timeout=3.0)
        assert response.status_code == 200, (service, response.text)
        assert response.json()["status"] == "ok"


def _assert_runtime_receipt(runtime: IsolatedRuntime, receipt: dict[str, object]) -> None:
    database = runtime.run_dir / "data" / "economy.db"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM board_tasks WHERE task_id=?", (receipt["task_id"],)
        ).fetchone() == ("submitted",)
        object_key, size = connection.execute(
            "SELECT object_key,declared_size_bytes FROM board_artifact_uploads "
            "WHERE 'asset-' || artifact_id=?",
            (receipt["asset_id"],),
        ).fetchone()
    assert size == len(DELIVERABLE)
    assert (runtime.run_dir / "assets" / object_key).read_bytes() == DELIVERABLE


async def _assert_second_still_operable(
    runtime: IsolatedRuntime, receipt: dict[str, object]
) -> None:
    factory = AgentFactory(config_path=runtime.agent_config_path)
    poster = factory.user_agent("operator")
    try:
        await poster.register()
        task = await poster.get_task(str(receipt["task_id"]))
        assert task["status"] == "submitted"
        assert await poster.download_asset(
            str(receipt["task_id"]), str(receipt["asset_id"])
        ) == DELIVERABLE
    finally:
        await poster.close()


def test_two_complete_stacks_keep_ports_data_assets_keys_and_cleanup_disjoint(
    tmp_path: Path,
) -> None:
    first = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path / "first")
    second = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path / "second")
    assert set(first.ports.values()).isdisjoint(second.ports.values())
    assert _runtime_paths(first).isdisjoint(_runtime_paths(second))
    try:
        first.start()
        second.start()
        assert first.provision() == second.provision() == 0
        first_receipt = asyncio.run(_public_workload(first.agent_config_path))
        second_receipt = asyncio.run(_public_workload(second.agent_config_path))
        assert first_receipt["task_id"] != second_receipt["task_id"]
        _assert_runtime_receipt(first, first_receipt)
        _assert_runtime_receipt(second, second_receipt)
        _assert_healthy(first)
        _assert_healthy(second)

        first.stop()
        _assert_healthy(second)
        asyncio.run(_assert_second_still_operable(second, second_receipt))
        first_manifest = first.run_dir / "runtime.json"
        assert first_manifest.is_file()
        first_state = json.loads(first_manifest.read_text())
        assert all(
            not process["running"] and process["group_drained"]
            for process in first_state["processes"].values()
        )
        second_state = json.loads((second.run_dir / "runtime.json").read_text())
        assert all(process["running"] for process in second_state["processes"].values())
        assert all(process.poll() is None for process in second.processes.values())
    finally:
        first.stop()
        second.stop()
