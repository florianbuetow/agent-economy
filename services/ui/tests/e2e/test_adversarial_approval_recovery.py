"""Two browser tabs recover one committed approval after response loss."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from playwright.async_api import Route, async_playwright, expect
from service_auth.factory import AgentFactory

if TYPE_CHECKING:
    from collections.abc import Generator

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.isolated_test_runtime import IsolatedRuntime  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.asyncio]


@pytest.fixture
def live_runtime(tmp_path: Path) -> Generator[IsolatedRuntime, None, None]:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path / "approval-recovery-runtime",
    )
    try:
        runtime.start()
        assert runtime.provision() == 0
        yield runtime
    finally:
        runtime.stop()


async def _ensure_account(agent: Any) -> None:
    try:
        await agent.create_account()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise


def _version(task: dict[str, Any]) -> int:
    value = task["lifecycle_version"]
    assert type(value) is int
    return value


def _receipt_count(database_path: Path, operation_id: str) -> int:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        assert row is not None
        return int(row[0])
    finally:
        connection.close()


def _approved_event_count(database_path: Path, task_id: str) -> int:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND event_type='task.approved'",
            (task_id,),
        ).fetchone()
        assert row is not None
        return int(row[0])
    finally:
        connection.close()


async def test_two_tabs_replay_one_approval_after_committed_response_is_lost(  # noqa: PLR0915
    live_runtime: IsolatedRuntime,
) -> None:
    """REV-05: both tabs retain one immutable approval intent and settlement."""
    factory = AgentFactory(config_path=live_runtime.agent_config_path)
    operator = factory.create_agent("operator")
    worker = factory.create_agent("alice")
    ui_url = live_runtime.service_url("ui")
    try:
        operator_id = (await operator.register())["agent_id"]
        worker_id = (await worker.register())["agent_id"]
        await _ensure_account(operator)
        await _ensure_account(worker)
        operator_initial = int((await operator.get_balance())["balance"])
        worker_initial = int((await worker.get_balance())["balance"])

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            first = await context.new_page()
            await first.goto(f"{ui_url}/task.html")
            await first.locator("#f-title").fill("Approval response-loss recovery")
            await first.locator("#f-spec").fill("Return answer.txt containing approved output.")
            await first.locator("#f-reward").fill("100")
            await first.locator("#f-bid-dl").fill("3600")
            await first.locator("#f-accept-window").fill("3600")
            await first.locator("#f-exec-dl").fill("7200")
            await first.locator("#f-rev-dl").fill("3600")
            await first.locator("#btn-post-task").click()
            posting_link = first.locator("[data-task-posting-link]")
            await posting_link.wait_for(state="visible")
            task_id = (await posting_link.get_attribute("href")).split("task_id=", maxsplit=1)[1]

            open_task = await worker.get_task(task_id)
            bid = await worker.submit_bid(
                task_id=task_id,
                amount=60,
                operation_id=f"op-{uuid.uuid4()}",
                expected_lifecycle_version=_version(open_task),
            )
            assert bid.status_code == 201
            await posting_link.click()
            await first.locator(".btn-accept-bid").click()
            await expect(first.locator("#task-status")).to_have_text("ACTIVE")

            accepted = await worker.get_task(task_id)
            assert accepted["worker_id"] == worker_id
            await worker.upload_asset(
                task_id,
                "answer.txt",
                b"approved output\n",
                idempotency_key="approval-recovery-output-v1",
            )
            submitted = await worker.submit_deliverable(
                task_id,
                operation_id=f"op-{uuid.uuid4()}",
                expected_lifecycle_version=_version(accepted),
            )
            assert submitted.status_code == 200

            await first.reload()
            await expect(first.locator("#task-status")).to_have_text("SUBMITTED")
            second = await context.new_page()
            await second.goto(f"{ui_url}/task.html?task_id={task_id}")
            await expect(second.locator("#task-status")).to_have_text("SUBMITTED")
            detail_url = f"{ui_url}/api/tasks/{task_id}"
            stale_detail_response = await second.request.get(detail_url)
            assert stale_detail_response.status == 200
            stale_detail = await stale_detail_response.json()
            approve_pattern = f"**/api/proxy/tasks/{task_id}/approve"
            committed_responses = 0
            approval_requests: list[str | None] = []

            def capture_approval(request: Any) -> None:
                if request.url.endswith(f"/api/proxy/tasks/{task_id}/approve"):
                    approval_requests.append(request.post_data)

            context.on("request", capture_approval)

            async def lose_committed_response(route: Route) -> None:
                nonlocal committed_responses
                response = await route.fetch()
                assert response.status == 200
                committed_responses += 1
                await route.abort("failed")

            await first.route(approve_pattern, lose_committed_response)
            await first.locator("#btn-approve").click()
            await expect(first.get_by_role("button", name="Try again")).to_be_visible()
            assert committed_responses == 1
            await first.unroute(approve_pattern, lose_committed_response)
            saved_session = await first.evaluate(
                "Object.fromEntries(Object.entries(sessionStorage))"
            )
            assert saved_session

            async def keep_second_tab_stale(route: Route) -> None:
                await route.fulfill(status=200, json=stale_detail)

            await second.route(f"**/api/tasks/{task_id}", keep_second_tab_stale)
            await second.evaluate(
                "entries => { sessionStorage.clear(); "
                "Object.entries(entries).forEach(([key, value]) => "
                "sessionStorage.setItem(key, value)); }",
                saved_session,
            )
            await second.reload()
            await expect(second.get_by_role("button", name="Try again")).to_be_visible()
            await second.unroute(f"**/api/tasks/{task_id}", keep_second_tab_stale)
            await asyncio.gather(
                first.get_by_role("button", name="Try again").click(),
                second.get_by_role("button", name="Try again").click(),
            )
            await expect(first.locator("#task-status")).to_have_text("APPROVED")
            await expect(second.locator("#task-status")).to_have_text("APPROVED")
            assert len(approval_requests) == 3
            approval_bodies = [json.loads(body) for body in approval_requests if body is not None]
            assert len(approval_bodies) == 3
            assert approval_bodies == [approval_bodies[0], approval_bodies[0], approval_bodies[0]]
            await browser.close()

        final_task = await worker.get_task(task_id)
        operator_final = int((await operator.get_balance())["balance"])
        worker_final = int((await worker.get_balance())["balance"])
        assert final_task["status"] == "approved"
        assert final_task["poster_id"] == operator_id
        assert final_task["accepted_bid_amount"] == 60
        assert operator_final - operator_initial == -60
        assert worker_final - worker_initial == 60
        assert operator_final + worker_final == operator_initial + worker_initial
        operation_id = final_task["financial_effect"]["operation_id"]
        assert isinstance(operation_id, str)
        database_path = live_runtime.run_dir / "data" / "economy.db"
        assert _receipt_count(database_path, operation_id) == 1
        assert _approved_event_count(database_path, task_id) == 1
    finally:
        await worker.close()
        await operator.close()


async def test_three_sealed_bids_and_two_tab_acceptance_have_one_explicit_winner(  # noqa: PLR0915
    live_runtime: IsolatedRuntime,
) -> None:
    """AUC-01/AUC-02: three sealed bids persist and two acceptances serialize."""
    factory = AgentFactory(config_path=live_runtime.agent_config_path)
    operator = factory.create_agent("operator")
    workers = [factory.create_agent(profile) for profile in ("alice", "bob", "mathbot")]
    ui_url = live_runtime.service_url("ui")
    try:
        await operator.register()
        worker_ids = [(await worker.register())["agent_id"] for worker in workers]
        for worker in workers:
            await _ensure_account(worker)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            first = await context.new_page()
            await first.goto(f"{ui_url}/task.html")
            await first.locator("#f-title").fill("Three sealed bids")
            await first.locator("#f-spec").fill("Choose exactly one explicit worker.")
            await first.locator("#f-reward").fill("100")
            await first.locator("#f-bid-dl").fill("3600")
            await first.locator("#f-accept-window").fill("3600")
            await first.locator("#f-exec-dl").fill("7200")
            await first.locator("#f-rev-dl").fill("3600")
            await first.locator("#btn-post-task").click()
            posting_link = first.locator("[data-task-posting-link]")
            await posting_link.wait_for(state="visible")
            task_id = (await posting_link.get_attribute("href")).split("task_id=", maxsplit=1)[1]
            open_task = await workers[0].get_task(task_id)
            bids = await asyncio.gather(
                *(
                    worker.submit_bid(
                        task_id=task_id,
                        amount=amount,
                        operation_id=f"op-{uuid.uuid4()}",
                        expected_lifecycle_version=_version(open_task),
                    )
                    for worker, amount in zip(workers, (60, 70, 60), strict=True)
                )
            )
            assert [response.status_code for response in bids] == [201, 201, 201]
            with pytest.raises(httpx.HTTPStatusError) as sealed:
                await workers[0].list_bids(task_id)
            assert sealed.value.response.status_code == 403

            await posting_link.click()
            await expect(first.locator(".bid-row")).to_have_count(3)
            second = await context.new_page()
            await second.goto(f"{ui_url}/task.html?task_id={task_id}")
            await expect(second.locator(".bid-row")).to_have_count(3)

            async def suppress_live_refresh(route: Route) -> None:
                await route.abort("failed")

            await first.route("**/api/events/stream*", suppress_live_refresh)
            await second.route("**/api/events/stream*", suppress_live_refresh)
            await first.reload()
            await second.reload()
            await expect(first.locator(".bid-row")).to_have_count(3)
            await expect(second.locator(".bid-row")).to_have_count(3)
            first_button = first.locator(f'.btn-accept-bid[data-bid-id="{bids[0].body["bid_id"]}"]')
            second_button = second.locator(
                f'.btn-accept-bid[data-bid-id="{bids[2].body["bid_id"]}"]'
            )
            await expect(first_button).to_be_visible()
            await expect(second_button).to_be_visible()
            accept_requests: list[tuple[str, str | None]] = []
            accept_statuses: list[int] = []

            def capture_request(request: Any) -> None:
                if "/api/proxy/tasks/" in request.url and request.url.endswith("/accept"):
                    accept_requests.append((request.url, request.post_data))

            def capture_response(response: Any) -> None:
                if "/api/proxy/tasks/" in response.url and response.url.endswith("/accept"):
                    accept_statuses.append(response.status)

            context.on("request", capture_request)
            context.on("response", capture_response)
            await asyncio.gather(first_button.click(), second_button.click())
            for _attempt in range(200):
                if len(accept_statuses) == 2:
                    break
                await first.wait_for_timeout(25)
            assert len(accept_requests) == len(accept_statuses) == 2
            assert sorted(accept_statuses) == [200, 409]
            bodies = [json.loads(body) for _url, body in accept_requests if body is not None]
            assert len(bodies) == 2
            assert {body["expected_lifecycle_version"] for body in bodies} == {0}
            assert len({body["operation_id"] for body in bodies}) == 2
            assert {
                url.split("/bids/", maxsplit=1)[1].split("/accept", maxsplit=1)[0]
                for url, _body in accept_requests
            } == {bids[0].body["bid_id"], bids[2].body["bid_id"]}
            await first.unroute("**/api/events/stream*", suppress_live_refresh)
            await second.unroute("**/api/events/stream*", suppress_live_refresh)
            await first.reload()
            await second.reload()
            await expect(first.locator("#task-status")).to_have_text("ACTIVE")
            await expect(second.locator("#task-status")).to_have_text("ACTIVE")
            await browser.close()

        final_task = await workers[0].get_task(task_id)
        assert final_task["status"] == "accepted"
        assert final_task["accepted_bid_amount"] == 60
        accepted_pairs = {
            bids[0].body["bid_id"]: worker_ids[0],
            bids[2].body["bid_id"]: worker_ids[2],
        }
        assert final_task["accepted_bid_id"] in accepted_pairs
        assert final_task["worker_id"] == accepted_pairs[final_task["accepted_bid_id"]]
    finally:
        for worker in workers:
            await worker.close()
        await operator.close()


async def test_two_tabs_freeze_complete_input_generations_while_sources_change(  # noqa: PLR0915
    live_runtime: IsolatedRuntime,
    tmp_path: Path,
) -> None:
    """PUB-06: selected files are immutable browser inputs across path replacement."""
    factory = AgentFactory(config_path=live_runtime.agent_config_path)
    operator = factory.create_agent("operator")
    reader = factory.create_agent("alice")
    ui_url = live_runtime.service_url("ui")
    first_generation = {"request.txt": b"generation one request\n", "data.bin": b"\x01" * 257}
    second_generation = {"request.txt": b"generation two request\n", "data.bin": b"\x02" * 257}
    paths = {name: tmp_path / name for name in first_generation}

    async def fill_post(page: Any, title: str) -> None:
        await page.goto(f"{ui_url}/task.html")
        await page.locator("#f-title").fill(title)
        await page.locator("#f-spec").fill("Preserve both selected input files exactly.")
        await page.locator("#f-reward").fill("100")
        await page.locator("#f-bid-dl").fill("3600")
        await page.locator("#f-accept-window").fill("3600")
        await page.locator("#f-exec-dl").fill("7200")
        await page.locator("#f-rev-dl").fill("3600")

    async def posting_outcome(page: Any) -> tuple[str, str]:
        await page.wait_for_function(
            "() => document.querySelector('[data-task-posting-link]') || "
            "document.getElementById('post-error').textContent"
        )
        link = page.locator("[data-task-posting-link]")
        if await link.count() == 1:
            href = await link.get_attribute("href")
            assert href is not None
            return "published", href.split("task_id=", maxsplit=1)[1]
        error = await page.locator("#post-error").text_content()
        assert error
        return "failed", error

    try:
        await operator.register()
        await reader.register()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            first = await context.new_page()
            second = await context.new_page()
            for name, content in first_generation.items():
                paths[name].write_bytes(content)
            await fill_post(first, "Generation one inputs")
            await first.locator("#f-inputs").set_input_files(list(paths.values()))

            for name, content in second_generation.items():
                paths[name].write_bytes(content)
            await fill_post(second, "Generation two inputs")
            await second.locator("#f-inputs").set_input_files(list(paths.values()))
            for path in paths.values():
                path.unlink()

            await asyncio.gather(
                first.locator("#btn-post-task").click(),
                second.locator("#btn-post-task").click(),
            )
            outcomes = await asyncio.gather(posting_outcome(first), posting_outcome(second))
            await browser.close()

        if {kind for kind, _value in outcomes} == {"failed"}:
            database_path = live_runtime.run_dir / "data" / "economy.db"
            connection = sqlite3.connect(database_path)
            try:
                task_count = connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()
                escrow_count = connection.execute("SELECT COUNT(*) FROM bank_escrow").fetchone()
                publications = connection.execute(
                    "SELECT state,COUNT(*) FROM board_task_publications GROUP BY state"
                ).fetchall()
                ready_uploads = connection.execute(
                    "SELECT COUNT(*) FROM board_artifact_uploads WHERE state='ready'"
                ).fetchone()
            finally:
                connection.close()
            assert task_count == (0,)
            assert escrow_count == (0,)
            assert publications == [("awaiting_inputs", 2)]
            assert ready_uploads == (0,)
            return

        assert {kind for kind, _value in outcomes} == {"published"}
        first_task, second_task = (outcomes[0][1], outcomes[1][1])
        assert first_task != second_task

        async def downloaded(task: str) -> dict[str, bytes]:
            manifest = (await reader.get_task_inputs(task))["input_manifest"]
            assert isinstance(manifest, dict)
            assert len(manifest["files"]) == 2
            return {
                item["filename"]: await reader.download_asset(task, item["asset_id"])
                for item in manifest["files"]
            }

        first_inputs, second_inputs = await asyncio.gather(
            downloaded(first_task), downloaded(second_task)
        )
        assert first_inputs == first_generation
        assert second_inputs == second_generation
    finally:
        await reader.close()
        await operator.close()
