"""Adversarial recovery coverage for a live DB Gateway outage."""

from __future__ import annotations

import asyncio
import base64
import sqlite3
import uuid
from pathlib import Path

import httpx
import pytest
import yaml
from scripts.isolated_test_runtime import IsolatedRuntime
from service_auth import AgentFactory

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
COUNT_TABLES = (
    "identity_agents",
    "bank_accounts",
    "bank_transactions",
    "board_tasks",
    "reputation_feedback",
    "court_claims",
    "events",
)


def _counts(database: Path) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in COUNT_TABLES
        }


def _shorten_gateway_timeouts(runtime: IsolatedRuntime) -> None:
    for service in ("identity", "central-bank", "task-board", "reputation", "court"):
        path = runtime.config_paths[service]
        config = yaml.safe_load(path.read_text())
        config["db_gateway"]["timeout_seconds"] = 1
        path.write_text(yaml.safe_dump(config, sort_keys=False))


async def _exercise(runtime: IsolatedRuntime) -> None:
    factory = AgentFactory(config_path=runtime.agent_config_path)
    platform = factory.platform_agent()
    operator = factory.user_agent("operator")
    try:
        await platform.register()
        operator_registration = await operator.register()
        operator_id = str(operator_registration["agent_id"])
        before_balance = int((await operator.get_balance())["balance"])
        before = _counts(runtime.run_dir / "data" / "economy.db")

        runtime.stop_service("db-gateway")

        registration_name = "gateway-outage-agent"
        registration_key = "ed25519:" + base64.b64encode(b"x" * 32).decode()
        async with httpx.AsyncClient(timeout=3.0) as client:
            ui_tasks = await client.get(
                f"{runtime.service_url('ui')}/api/tasks/-/competitive"
            )
            assert ui_tasks.status_code == 200
            assert isinstance(ui_tasks.json().get("tasks"), list)

            registration = await client.post(
                f"{runtime.service_url('identity')}/agents/register",
                json={
                    "name": registration_name,
                    "public_key": registration_key,
                },
            )
            assert registration.status_code >= 500

        credit_operation_id = f"op-{uuid.uuid4()}"
        with pytest.raises(httpx.HTTPStatusError) as credit_error:
            await platform.credit_account(
                account_id=operator_id,
                amount=1,
                reference="gateway-outage-credit",
                operation_id=credit_operation_id,
            )
        assert credit_error.value.response.status_code >= 500

        publication = operator.new_task_publication(
            task_id=f"t-{uuid.uuid4()}",
            title="Gateway outage must not publish",
            spec="This publication must remain absent while persistence is unavailable.",
            reward=1,
            bidding_deadline_seconds=60,
            acceptance_window_seconds=60,
            execution_deadline_seconds=60,
            review_deadline_seconds=60,
            expected_input_count=0,
        )
        with pytest.raises(httpx.HTTPStatusError) as posting_error:
            await operator.admit_task_publication(publication)
        assert posting_error.value.response.status_code >= 500

        assert _counts(runtime.run_dir / "data" / "economy.db") == before

        runtime.start_service("db-gateway")
        async with httpx.AsyncClient(timeout=3.0) as client:
            retry_registration = await client.post(
                f"{runtime.service_url('identity')}/agents/register",
                json={"name": registration_name, "public_key": registration_key},
            )
            assert retry_registration.status_code in {201, 409}
            agents = (await client.get(f"{runtime.service_url('identity')}/agents")).json()[
                "agents"
            ]
            matching_agents = [row for row in agents if row["name"] == registration_name]
            assert len(matching_agents) == 1
            registered = await client.get(
                f"{runtime.service_url('identity')}/agents/"
                f"{matching_agents[0]['agent_id']}"
            )
            assert registered.status_code == 200
            assert registered.json()["public_key"] == registration_key

        await platform.credit_account(
            account_id=operator_id,
            amount=1,
            reference="gateway-outage-credit",
            operation_id=credit_operation_id,
        )
        admitted = await operator.admit_task_publication(publication)
        assert admitted["publication_operation_id"] == publication["publication_operation_id"]
        assert int((await operator.get_balance())["balance"]) == before_balance + 1
        after = _counts(runtime.run_dir / "data" / "economy.db")
        assert after["identity_agents"] == before["identity_agents"] + 1
        assert after["bank_accounts"] == before["bank_accounts"]
        assert after["bank_transactions"] == before["bank_transactions"] + 1
        assert after["board_tasks"] == before["board_tasks"]
    finally:
        await operator.close()
        await platform.close()


def test_gateway_outage_keeps_reads_available_and_never_invents_write_success(
    tmp_path: Path,
) -> None:
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    try:
        _shorten_gateway_timeouts(runtime)
        runtime.start()
        assert runtime.provision() == 0
        asyncio.run(_exercise(runtime))
    finally:
        runtime.stop()
