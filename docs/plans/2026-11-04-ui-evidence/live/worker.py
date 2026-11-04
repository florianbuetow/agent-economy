from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import async_playwright, expect
from service_auth.factory import AgentFactory
from service_auth.financial_identity import operation_id_for


def operation_id() -> str:
    return f"op-{uuid.uuid4()}"


def lifecycle_version(task: dict[str, Any]) -> int:
    value = task.get("lifecycle_version")
    assert type(value) is int and value >= 0
    return value


async def ensure_account(agent: Any) -> None:
    try:
        await agent.create_account()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise


async def main() -> None:
    config_path = Path(os.environ["AGENT_CONFIG_PATH"])
    factory = AgentFactory(config_path=config_path)
    platform = factory.platform_agent()
    poster = factory.create_agent("feeder")
    operator = factory.create_agent("operator")
    ui_url = os.environ["UI_URL"]
    reward = 100
    accepted_price = 60
    input_bytes = b"classify: the service is reliable"
    output_bytes = b"positive\n"

    try:
        await platform.register()
        poster_registration = await poster.register()
        operator_registration = await operator.register()
        await ensure_account(poster)
        await ensure_account(operator)
        poster_id = poster_registration["agent_id"]
        operator_id = operator_registration["agent_id"]
        funding_reference = f"ui-worker-live-{uuid.uuid4()}"
        await platform.credit_account(
            account_id=poster_id,
            amount=200,
            reference=funding_reference,
            operation_id=operation_id_for(
                "ui-worker-live-credit",
                (poster_id, funding_reference),
            ),
        )
        poster_initial = int((await poster.get_balance())["balance"])
        operator_initial = int((await operator.get_balance())["balance"])

        task_id = f"t-{uuid.uuid4()}"
        intent = poster.new_task_publication(
            task_id=task_id,
            title="UI worker live input task",
            spec="Read request.txt and return the sentiment label.",
            reward=reward,
            bidding_deadline_seconds=3600,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
            expected_input_count=1,
        )
        admitted = await poster.admit_task_publication(intent)
        assert admitted["publication_state"] == "awaiting_inputs"
        uploaded_input = await poster.upload_task_input(
            intent["publication_operation_id"],
            "request.txt",
            input_bytes,
            content_type="text/plain",
            idempotency_key="ui-worker-live-input-v1",
        )
        assert uploaded_input["content_hash"]
        publish_status, published = await poster.publish_task_publication(intent)
        assert publish_status == 201, published
        assert published["publication_state"] == "published"
        task = await poster.get_task(task_id)
        assert task["status"] == "open"

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(f"{ui_url}/")
            await page.get_by_role("link", name="Jobs", exact=True).click()
            task_link = page.locator(f'a[href*="task_id={task_id}"]').first
            await task_link.wait_for(state="visible")
            await task_link.click()
            await page.locator("#f-bid-amount").fill(str(accepted_price))
            async with page.expect_response(lambda response: response.url.endswith(f"/tasks/{task_id}/bids") and response.request.method == "POST") as bid_info:
                await page.locator("#btn-submit-bid").click()
            bid_response = await bid_info.value
            assert bid_response.status == 201
            bid = await bid_response.json()
            assert bid["bidder_id"] == operator_id
            accepted = await poster.accept_bid(
                task_id=task_id, bid_id=bid["bid_id"], operation_id=operation_id(),
                expected_lifecycle_version=lifecycle_version(task),
            )
            assert accepted.status_code == 200
            await page.reload()
            await expect(page.locator("#task-status")).to_have_text("ACTIVE")
            async with page.expect_download() as download_info:
                await page.locator("[data-task-input] a[download]").click()
            download = await download_info.value
            download_path = await download.path()
            assert Path(download_path).read_bytes() == input_bytes
            await page.screenshot(path="/tmp/ate-ui-journey-validation/worker-accepted.png", full_page=True)
            await page.locator("#f-upload-file").set_input_files({"name": "answer.txt", "mimeType": "text/plain", "buffer": output_bytes})
            async with page.expect_response(lambda response: response.url.endswith(f"/tasks/{task_id}/assets") and response.request.method == "POST") as upload_info:
                await page.locator("#btn-upload-asset").click()
            upload_response = await upload_info.value
            assert upload_response.status == 201
            output_asset = await upload_response.json()
            preview = page.locator(f'[data-document-preview-button][data-asset-id="{output_asset["asset_id"]}"]')
            await preview.click()
            await expect(page.locator(f'[data-asset-preview="{output_asset["asset_id"]}"]')).to_have_text(output_bytes.decode())
            await page.locator("#btn-submit-deliverable").click()
            await expect(page.locator("#task-status")).to_have_text("SUBMITTED")
            await expect(page.locator("[data-operation-recovery]")).to_have_count(0)
            await page.screenshot(path="/tmp/ate-ui-journey-validation/worker-submitted.png", full_page=True)
            assert errors == [], errors
            await page.locator("#task-orientation").scroll_into_view_if_needed()
            await page.screenshot(path="/tmp/ate-ui-journey-validation/worker-submitted-orientation.png", full_page=True)
            await browser.close()
        submitted = await poster.get_task(task_id)
        assert submitted["status"] == "submitted"

        assets = await poster.list_assets(task_id)
        output_rows = [item for item in assets if item["asset_id"] == output_asset["asset_id"]]
        assert len(output_rows) == 1
        downloaded_output = await poster.download_asset(task_id, output_asset["asset_id"])
        assert downloaded_output == output_bytes
        approved = await poster.approve_task(
            task_id=task_id,
            operation_id=operation_id(),
            expected_lifecycle_version=lifecycle_version(submitted),
        )
        assert approved.status_code == 200
        final_task = approved.body
        assert final_task["status"] == "approved"
        assert final_task["accepted_bid_amount"] == accepted_price

        poster_final = int((await poster.get_balance())["balance"])
        operator_final = int((await operator.get_balance())["balance"])
        assert operator_final - operator_initial == accepted_price
        assert poster_final == poster_initial - accepted_price
        assert poster_final + operator_final == poster_initial + operator_initial

        print(
            json.dumps(
                {
                    "result": "passed",
                    "scope": "live isolated browser UI worker journey",
                    "task_id": task_id,
                    "poster_id": poster_id,
                    "operator_id": operator_id,
                    "input_bytes": len(input_bytes),
                    "output_bytes": len(output_bytes),
                    "accepted_price": accepted_price,
                    "operator_balance_delta": operator_final - operator_initial,
                    "poster_balance_delta": poster_final - poster_initial,
                    "final_status": final_task["status"],
                },
                sort_keys=True,
            )
        )
    finally:
        await poster.close()
        await operator.close()
        await platform.close()


if __name__ == "__main__":
    asyncio.run(main())
