from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from playwright.async_api import async_playwright, expect
from service_auth.factory import AgentFactory


def operation_id() -> str:
    return f"op-{uuid.uuid4()}"


def lifecycle_version(task: dict[str, object]) -> int:
    value = task.get("lifecycle_version")
    assert type(value) is int and value >= 0
    return value


async def ensure_account(agent: object) -> None:
    try:
        await agent.create_account()  # type: ignore[attr-defined]
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise


async def main() -> None:
    factory = AgentFactory(config_path=Path(os.environ["AGENT_CONFIG_PATH"]))
    operator = factory.create_agent("operator")
    worker = factory.create_agent("alice")
    ui_url = os.environ["UI_URL"]
    input_path = Path("/tmp/agent-economy-goal-2026-11-04/browser-poster-input.txt")
    input_bytes = b"sentiment input: this product is dependable\n"
    output_bytes = b"positive\n"
    input_path.write_bytes(input_bytes)

    try:
        operator_registration = await operator.register()
        worker_registration = await worker.register()
        await ensure_account(operator)
        await ensure_account(worker)
        operator_id = operator_registration["agent_id"]
        worker_id = worker_registration["agent_id"]
        operator_initial = int((await operator.get_balance())["balance"])
        worker_initial = int((await worker.get_balance())["balance"])
        assert worker_initial == 0

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(f"{ui_url}/")
            await page.locator('a[href="/task.html"]').first.click()
            await page.locator("#f-title").fill("Browser poster input task")
            await page.locator("#f-spec").fill(
                "Read browser-poster-input.txt and return its sentiment label."
            )
            await page.locator("#f-reward").fill("100")
            await page.locator("#f-bid-dl").fill("3600")
            await page.locator("#f-accept-window").fill("3600")
            await page.locator("#f-exec-dl").fill("7200")
            await page.locator("#f-rev-dl").fill("3600")
            await page.locator("#f-inputs").set_input_files(str(input_path))
            await page.locator("#btn-post-task").click()
            posting_link = page.locator("[data-task-posting-link]")
            await posting_link.wait_for(state="visible")
            href = await posting_link.get_attribute("href")
            assert href is not None
            task_id = parse_qs(urlparse(href).query)["task_id"][0]

            published = await worker.get_task(task_id)
            assert published["poster_id"] == operator_id
            input_manifest = (await worker.get_task_inputs(task_id))["input_manifest"]
            assert isinstance(input_manifest, dict)
            input_asset = input_manifest["files"][0]
            downloaded_input = await worker.download_asset(task_id, input_asset["asset_id"])
            assert downloaded_input == input_bytes

            bid = await worker.submit_bid(
                task_id=task_id,
                amount=60,
                operation_id=operation_id(),
                expected_lifecycle_version=lifecycle_version(published),
            )
            assert bid.status_code == 201

            await posting_link.click()
            accept_button = page.locator(".btn-accept-bid")
            await accept_button.wait_for(state="visible")
            await accept_button.click()
            await expect(page.locator("#task-status")).to_have_text("ACTIVE")

            accepted = await worker.get_task(task_id)
            assert accepted["worker_id"] == worker_id
            uploaded = await worker.upload_asset(
                task_id,
                "browser-worker-output.txt",
                output_bytes,
                idempotency_key="browser-poster-output-v1",
            )
            submitted = await worker.submit_deliverable(
                task_id,
                operation_id=operation_id(),
                expected_lifecycle_version=lifecycle_version(accepted),
            )
            assert submitted.status_code == 200

            await page.reload()
            await expect(page.locator("#task-status")).to_have_text("SUBMITTED")
            preview_button = page.locator(
                f'[data-document-preview-button][data-asset-id="{uploaded["asset_id"]}"]'
            )
            await preview_button.wait_for(state="visible")
            await preview_button.click()
            await expect(
                page.locator(f'[data-asset-preview="{uploaded["asset_id"]}"]')
            ).to_have_text(output_bytes.decode())
            approve_button = page.locator("#btn-approve")
            await approve_button.wait_for(state="visible")
            await approve_button.click()
            await expect(page.locator("#task-status")).to_have_text("APPROVED")
            await expect(page.locator(".payment-status")).not_to_contain_text("court")
            await page.screenshot(path="/tmp/ate-ui-journey-validation/poster-approved.png", full_page=True)
            await page.locator("#task-orientation").scroll_into_view_if_needed()
            await page.screenshot(path="/tmp/ate-ui-journey-validation/poster-outcome-orientation.png", full_page=True)
            await browser.close()

        final_task = await worker.get_task(task_id)
        operator_final = int((await operator.get_balance())["balance"])
        worker_final = int((await worker.get_balance())["balance"])
        assert final_task["status"] == "approved"
        assert final_task["accepted_bid_amount"] == 60
        assert operator_final - operator_initial == -60
        assert worker_final - worker_initial == 60
        assert operator_final + worker_final == operator_initial + worker_initial
        print(
            json.dumps(
                {
                    "result": "passed",
                    "scope": "live isolated browser UI poster with input",
                    "task_id": task_id,
                    "operator_id": operator_id,
                    "worker_id": worker_id,
                    "input_bytes": len(input_bytes),
                    "output_bytes": len(output_bytes),
                    "accepted_price": 60,
                    "operator_balance_delta": operator_final - operator_initial,
                    "worker_balance_delta": worker_final - worker_initial,
                    "final_status": final_task["status"],
                },
                sort_keys=True,
            )
        )
    finally:
        await worker.close()
        await operator.close()


if __name__ == "__main__":
    asyncio.run(main())
