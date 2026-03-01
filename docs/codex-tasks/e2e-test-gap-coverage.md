# E2E Test Gap Coverage — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement 16 new e2e tests covering gaps identified in the system sequence diagram gap analysis. Five new test files in `agents/tests/e2e/`.

**Architecture:** Each test file follows the exact pattern of existing e2e tests — `@pytest.mark.e2e` async functions using `make_funded_agent` and `platform_agent` fixtures, with try/finally cleanup via `_close_agents`. Tests use `_request_raw` for asserting specific HTTP status codes and `_sign_jws` for crafting raw JWS tokens.

**Tech Stack:** pytest, pytest-asyncio, httpx, base_agent library (BaseAgent, PlatformAgent)

**Priority:** Happy path / confirming tests first (Tasks 1-8), adversarial tests second (Tasks 9-16).

**Key Rules:**
- All Python execution via `uv run` — never raw `python` or `pip install`
- Do NOT modify existing test files — only create new files
- All tests marked `@pytest.mark.e2e`
- Run tests from `agents/` directory: `cd agents && uv run pytest tests/e2e/<file> -v -m e2e`
- Agent names use unique 3-char suffixes to avoid collisions across test runs

---

## Phase 1: Happy Path / Confirming Tests

### Task 1: Create `test_asset_store.py` with download test

**Files:**
- Create: `agents/tests/e2e/test_asset_store.py`

**Step 1: Create the test file with the download test**

```python
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_download_uploaded_asset(make_funded_agent) -> None:
    """Confirm uploaded asset content can be downloaded and matches original."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster AS1", balance=5000)
        worker = await make_funded_agent(name="Worker AS1", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Asset download task",
            spec="Upload a file",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])

        original_content = b"Hello World from asset store test"
        asset = await worker.upload_asset(task["task_id"], "result.txt", original_content)
        assert isinstance(asset.get("asset_id"), str)

        # List assets to get the asset_id
        assets_response = await worker._request(
            "GET",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/assets",
        )
        assets = assets_response["assets"]
        assert len(assets) == 1
        asset_id = assets[0]["asset_id"]

        # Download the asset
        download_response = await worker._request_raw(
            "GET",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/assets/{asset_id}",
        )
        assert download_response.status_code == 200
        assert download_response.content == original_content
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run test to verify**

```bash
cd agents && uv run pytest tests/e2e/test_asset_store.py::test_download_uploaded_asset -v -m e2e
```

Expected: PASS (asset store download should already work).

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_asset_store.py
git commit -m "test(e2e): add asset download content verification test"
```

---

### Task 2: Add multiple asset uploads test

**Files:**
- Modify: `agents/tests/e2e/test_asset_store.py`

**Step 1: Append the multi-upload test to the file**

```python
@pytest.mark.e2e
async def test_multiple_asset_uploads(make_funded_agent) -> None:
    """Confirm multiple files can be uploaded to a single task."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster AS2", balance=5000)
        worker = await make_funded_agent(name="Worker AS2", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Multi asset task",
            spec="Upload multiple files",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])

        await worker.upload_asset(task["task_id"], "code.py", b"print(1)")
        await worker.upload_asset(task["task_id"], "readme.md", b"# Hello")
        await worker.upload_asset(task["task_id"], "data.json", b'{"key": 1}')

        assets_response = await worker._request(
            "GET",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/assets",
        )
        assets = assets_response["assets"]
        assert len(assets) == 3

        filenames = {a["filename"] for a in assets}
        assert filenames == {"code.py", "readme.md", "data.json"}

        for asset in assets:
            assert isinstance(asset["asset_id"], str)
            assert asset["size_bytes"] > 0
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run both asset store tests**

```bash
cd agents && uv run pytest tests/e2e/test_asset_store.py -v -m e2e
```

Expected: PASS.

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_asset_store.py
git commit -m "test(e2e): add multiple asset uploads verification test"
```

---

### Task 3: Create `test_reputation_sealed.py` with sealed feedback test

**Files:**
- Create: `agents/tests/e2e/test_reputation_sealed.py`

**Step 1: Create the test file with the sealed feedback test**

```python
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_sealed_feedback_invisible_until_mutual(make_funded_agent) -> None:
    """Confirm one-sided feedback is sealed; becomes visible when both sides submit."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster RS1", balance=5000)
        worker = await make_funded_agent(name="Worker RS1", balance=0)
        agents_to_close.extend([poster, worker])

        # Complete a task through the happy path
        task = await poster.post_task(
            title="Sealed feedback task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
        await worker.upload_asset(task["task_id"], "result.txt", b"Hello World")
        await worker.submit_deliverable(task["task_id"])
        await poster.approve_task(task["task_id"])

        # Poster submits feedback (one-sided — should be sealed)
        poster_fb = await poster.submit_feedback(
            task_id=task["task_id"],
            to_agent_id=str(worker.agent_id),
            category="delivery_quality",
            rating="satisfied",
            comment="Good work",
        )
        poster_fb_id = poster_fb["feedback_id"]

        # Sealed: task feedback query should return empty (no visible records)
        task_feedback_before = await poster.get_task_feedback(task["task_id"])
        visible_before = [fb for fb in task_feedback_before if fb.get("visible", True)]
        assert len(visible_before) == 0, "One-sided feedback should be sealed (invisible)"

        # Sealed: direct lookup should return 404
        sealed_response = await poster._request_raw(
            "GET",
            f"{poster.config.reputation_url}/feedback/{poster_fb_id}",
        )
        assert sealed_response.status_code == 404, "Sealed feedback should be indistinguishable from non-existent"

        # Worker submits feedback (mutual — both should become visible)
        await worker.submit_feedback(
            task_id=task["task_id"],
            to_agent_id=str(poster.agent_id),
            category="spec_quality",
            rating="satisfied",
            comment="Clear spec",
        )

        # Now both should be visible
        task_feedback_after = await poster.get_task_feedback(task["task_id"])
        assert len(task_feedback_after) == 2, "Both feedbacks should be visible after mutual submission"

        # Direct lookup should now succeed
        revealed_response = await poster._request_raw(
            "GET",
            f"{poster.config.reputation_url}/feedback/{poster_fb_id}",
        )
        assert revealed_response.status_code == 200, "Feedback should be revealed after mutual submission"
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run test**

```bash
cd agents && uv run pytest tests/e2e/test_reputation_sealed.py::test_sealed_feedback_invisible_until_mutual -v -m e2e
```

Expected: PASS (sealed mechanism is implemented in Reputation service).

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_reputation_sealed.py
git commit -m "test(e2e): add sealed feedback visibility verification test"
```

---

### Task 4: Create `test_court_rulings.py` with shared helpers and escrow split test

This is the most complex file. It reuses the helper pattern from `test_disputes.py` but tests post-ruling side-effects.

**Files:**
- Create: `agents/tests/e2e/test_court_rulings.py`

**Step 1: Create the file with helpers and the escrow split test**

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


async def _create_disputed_task(
    poster: BaseAgent, worker: BaseAgent, reward: int = 1000
) -> tuple[dict[str, Any], str]:
    """Drive a task through the full lifecycle to disputed state."""
    task = await poster.post_task(
        title="Court ruling task",
        spec="Implement the feature as specified",
        reward=reward,
        bidding_deadline_seconds=3600,
        execution_deadline_seconds=7200,
        review_deadline_seconds=3600,
    )
    bid = await worker.submit_bid(task_id=task["task_id"], amount=reward)
    await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
    await worker.upload_asset(task["task_id"], "deliverable.txt", b"Implementation complete")
    await worker.submit_deliverable(task["task_id"])

    dispute_reason = "Deliverable does not meet specification requirements"
    await poster.dispute_task(task_id=task["task_id"], reason=dispute_reason)

    disputed_task = await poster.get_task(task["task_id"])
    assert disputed_task["status"] == "disputed"
    return disputed_task, dispute_reason


async def _file_dispute_with_court(
    poster: BaseAgent,
    worker: BaseAgent,
    platform_agent: PlatformAgent,
    disputed_task: dict[str, Any],
    dispute_reason: str,
) -> str | None:
    """File the dispute with the Court service via the platform agent.

    Returns the dispute_id, or None if filing is unavailable.
    """
    task_id = str(disputed_task["task_id"])

    # Check if Court already has a dispute for this task
    listed_disputes = await poster._request(
        "GET",
        f"{poster.config.court_url}/disputes",
        params={"task_id": task_id},
    )
    disputes = listed_disputes["disputes"]
    if len(disputes) > 0:
        return str(disputes[0]["dispute_id"])

    # File a new dispute via platform-signed JWS
    file_token = platform_agent._sign_jws(
        {
            "action": "file_dispute",
            "task_id": task_id,
            "claimant_id": poster.agent_id,
            "respondent_id": worker.agent_id,
            "claim": dispute_reason,
            "escrow_id": disputed_task["escrow_id"],
        }
    )
    file_response = await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/file",
        json={"token": file_token},
    )
    if file_response.status_code == 201:
        return str(file_response.json()["dispute_id"])

    # Handle 409 (already filed, perhaps by Task Board auto-filing)
    if file_response.status_code == 409:
        refreshed = await poster._request(
            "GET",
            f"{poster.config.court_url}/disputes",
            params={"task_id": task_id},
        )
        refreshed_disputes = refreshed["disputes"]
        if len(refreshed_disputes) > 0:
            return str(refreshed_disputes[0]["dispute_id"])

    return None


async def _submit_rebuttal(platform_agent: PlatformAgent, dispute_id: str) -> int:
    """Submit a rebuttal on behalf of the worker. Returns HTTP status code."""
    rebuttal_token = platform_agent._sign_jws(
        {
            "action": "submit_rebuttal",
            "dispute_id": dispute_id,
            "rebuttal": "The deliverable meets all specification requirements.",
        }
    )
    rebuttal_response = await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/{dispute_id}/rebuttal",
        json={"token": rebuttal_token},
    )
    return rebuttal_response.status_code


async def _trigger_ruling(
    platform_agent: PlatformAgent, dispute_id: str
) -> tuple[int, dict[str, Any]]:
    """Trigger the judge panel ruling. Returns (status_code, response_json)."""
    ruling_token = platform_agent._sign_jws(
        {
            "action": "trigger_ruling",
            "dispute_id": dispute_id,
        }
    )
    ruling_response = await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/{dispute_id}/rule",
        json={"token": ruling_token},
    )
    payload: dict[str, Any] = {}
    if ruling_response.headers.get("content-type", "").startswith("application/json"):
        payload = ruling_response.json()
    return ruling_response.status_code, payload


async def _drive_to_ruling(
    poster: BaseAgent,
    worker: BaseAgent,
    platform_agent: PlatformAgent,
    reward: int = 1000,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Full lifecycle: post -> bid -> accept -> deliver -> dispute -> court ruling.

    Returns (disputed_task, dispute_id, ruling_payload).
    Skips the test if court is unavailable or ruling fails.
    """
    disputed_task, dispute_reason = await _create_disputed_task(poster, worker, reward=reward)

    dispute_id = await _file_dispute_with_court(
        poster, worker, platform_agent, disputed_task, dispute_reason
    )
    if dispute_id is None:
        pytest.skip("Court dispute filing is not available in the current environment")

    rebuttal_status = await _submit_rebuttal(platform_agent, dispute_id)
    assert rebuttal_status in {200, 409}, f"Unexpected rebuttal status: {rebuttal_status}"

    ruling_status, ruling_payload = await _trigger_ruling(platform_agent, dispute_id)
    if ruling_status != 200:
        pytest.skip(f"Court ruling unavailable (status {ruling_status})")

    assert ruling_payload["status"] == "ruled"
    return disputed_task, dispute_id, ruling_payload


@pytest.mark.e2e
async def test_escrow_split_proportional_payout(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Confirm escrow is split proportionally after court ruling — not just sum check."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CR1", balance=5000)
        worker = await make_funded_agent(name="Worker CR1", balance=0)
        agents_to_close.extend([poster, worker])

        reward = 1000
        disputed_task, dispute_id, ruling_payload = await _drive_to_ruling(
            poster, worker, platform_agent, reward=reward
        )

        worker_pct = ruling_payload["worker_pct"]
        assert isinstance(worker_pct, int)
        assert 0 <= worker_pct <= 100

        # Verify proportional split — not just that sum is correct
        expected_worker_amount = (reward * worker_pct) // 100
        expected_poster_amount = reward - expected_worker_amount

        worker_balance = await worker.get_balance()
        poster_balance = await poster.get_balance()

        assert worker_balance["balance"] == expected_worker_amount, (
            f"Worker should get {expected_worker_amount} ({worker_pct}% of {reward}), "
            f"got {worker_balance['balance']}"
        )
        assert poster_balance["balance"] == 5000 - reward + expected_poster_amount, (
            f"Poster should get {expected_poster_amount} back from escrow, "
            f"got {poster_balance['balance']} (expected {5000 - reward + expected_poster_amount})"
        )

        # Conservation check
        assert poster_balance["balance"] + worker_balance["balance"] == 5000
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run test**

```bash
cd agents && uv run pytest tests/e2e/test_court_rulings.py::test_escrow_split_proportional_payout -v -m e2e
```

Expected: May PASS or SKIP (depends on LLM availability). If Court side-effects are not wired, will FAIL.

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_court_rulings.py
git commit -m "test(e2e): add court escrow split proportional payout test"
```

---

### Task 5: Add ruling recorded on TaskBoard test

**Files:**
- Modify: `agents/tests/e2e/test_court_rulings.py`

**Step 1: Append the test**

```python
@pytest.mark.e2e
async def test_ruling_recorded_on_task_board(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Confirm task record is updated with ruling details after court decision."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CR2", balance=5000)
        worker = await make_funded_agent(name="Worker CR2", balance=0)
        agents_to_close.extend([poster, worker])

        disputed_task, dispute_id, ruling_payload = await _drive_to_ruling(
            poster, worker, platform_agent, reward=1000
        )

        # Check the task on TaskBoard has ruling details
        task_after = await poster.get_task(str(disputed_task["task_id"]))
        assert task_after["status"] == "ruled", f"Task status should be 'ruled', got '{task_after['status']}'"
        assert isinstance(task_after.get("ruling_id"), str), "ruling_id should be set"
        assert task_after["ruling_id"] != "", "ruling_id should not be empty"
        assert isinstance(task_after.get("worker_pct"), int), "worker_pct should be set"
        assert task_after["worker_pct"] == ruling_payload["worker_pct"], "worker_pct should match Court ruling"
        assert isinstance(task_after.get("ruling_summary"), str), "ruling_summary should be set"
        assert task_after["ruling_summary"] != "", "ruling_summary should not be empty"
        assert task_after.get("ruled_at") is not None, "ruled_at timestamp should be set"
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run test**

```bash
cd agents && uv run pytest tests/e2e/test_court_rulings.py::test_ruling_recorded_on_task_board -v -m e2e
```

Expected: May FAIL if Court does not POST ruling to TaskBoard.

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_court_rulings.py
git commit -m "test(e2e): add ruling recorded on task board verification test"
```

---

### Task 6: Add court reputation feedback test

**Files:**
- Modify: `agents/tests/e2e/test_court_rulings.py`

**Step 1: Append the test**

```python
@pytest.mark.e2e
async def test_court_posts_reputation_feedback(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Confirm Court posts feedback to Reputation service for both parties after ruling."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CR3", balance=5000)
        worker = await make_funded_agent(name="Worker CR3", balance=0)
        agents_to_close.extend([poster, worker])

        disputed_task, dispute_id, ruling_payload = await _drive_to_ruling(
            poster, worker, platform_agent, reward=1000
        )

        # Court should have posted two feedback records to Reputation
        task_feedback = await poster.get_task_feedback(str(disputed_task["task_id"]))
        assert len(task_feedback) >= 2, (
            f"Expected at least 2 feedback records from Court, got {len(task_feedback)}"
        )

        # Check for spec_quality feedback targeting the poster
        spec_feedback = [
            fb for fb in task_feedback
            if fb["category"] == "spec_quality" and fb["to_agent_id"] == poster.agent_id
        ]
        assert len(spec_feedback) >= 1, "Court should post spec_quality feedback for poster"

        # Check for delivery_quality feedback targeting the worker
        delivery_feedback = [
            fb for fb in task_feedback
            if fb["category"] == "delivery_quality" and fb["to_agent_id"] == worker.agent_id
        ]
        assert len(delivery_feedback) >= 1, "Court should post delivery_quality feedback for worker"
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run test**

```bash
cd agents && uv run pytest tests/e2e/test_court_rulings.py::test_court_posts_reputation_feedback -v -m e2e
```

Expected: May FAIL if Court does not POST feedback to Reputation.

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_court_rulings.py
git commit -m "test(e2e): add court reputation feedback verification test"
```

---

### Task 7: Add dispute without rebuttal test

**Files:**
- Modify: `agents/tests/e2e/test_court_rulings.py`

**Step 1: Append the test**

```python
@pytest.mark.e2e
async def test_dispute_proceeds_without_rebuttal(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Edge case: ruling should proceed even without worker rebuttal."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CR4", balance=5000)
        worker = await make_funded_agent(name="Worker CR4", balance=0)
        agents_to_close.extend([poster, worker])

        disputed_task, dispute_reason = await _create_disputed_task(
            poster, worker, reward=1000
        )

        dispute_id = await _file_dispute_with_court(
            poster, worker, platform_agent, disputed_task, dispute_reason
        )
        if dispute_id is None:
            pytest.skip("Court dispute filing is not available")

        # Skip rebuttal entirely — go straight to ruling
        ruling_status, ruling_payload = await _trigger_ruling(platform_agent, dispute_id)

        if ruling_status == 200:
            # Ruling succeeded without rebuttal
            assert ruling_payload["status"] == "ruled"
            assert isinstance(ruling_payload.get("worker_pct"), int)
        else:
            # Court may require rebuttal or have specific error handling
            # Check the dispute is still in a valid state
            dispute_snapshot = await poster._request(
                "GET",
                f"{poster.config.court_url}/disputes/{dispute_id}",
            )
            assert dispute_snapshot["status"] in {
                "rebuttal_pending",
                "judging",
                "ruled",
            }, f"Unexpected dispute status: {dispute_snapshot['status']}"
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run test**

```bash
cd agents && uv run pytest tests/e2e/test_court_rulings.py::test_dispute_proceeds_without_rebuttal -v -m e2e
```

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_court_rulings.py
git commit -m "test(e2e): add dispute without rebuttal edge case test"
```

---

### Task 8: Create `test_economic_invariants.py` with dispute cycle test

**Files:**
- Create: `agents/tests/e2e/test_economic_invariants.py`

**Step 1: Create the file**

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


async def _drive_dispute_to_ruling(
    poster: BaseAgent,
    worker: BaseAgent,
    platform_agent: PlatformAgent,
    reward: int,
) -> dict[str, Any] | None:
    """Drive a task through dispute to ruling. Returns ruling payload or None."""
    task = await poster.post_task(
        title="Economic dispute task",
        spec="Implement the feature",
        reward=reward,
        bidding_deadline_seconds=3600,
        execution_deadline_seconds=7200,
        review_deadline_seconds=3600,
    )
    bid = await worker.submit_bid(task_id=task["task_id"], amount=reward)
    await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
    await worker.upload_asset(task["task_id"], "work.txt", b"Completed work")
    await worker.submit_deliverable(task["task_id"])

    await poster.dispute_task(task_id=task["task_id"], reason="Does not meet spec")

    disputed_task = await poster.get_task(task["task_id"])
    assert disputed_task["status"] == "disputed"

    # File with Court
    task_id = str(disputed_task["task_id"])
    listed = await poster._request(
        "GET",
        f"{poster.config.court_url}/disputes",
        params={"task_id": task_id},
    )
    disputes = listed["disputes"]
    if len(disputes) > 0:
        dispute_id = str(disputes[0]["dispute_id"])
    else:
        file_token = platform_agent._sign_jws(
            {
                "action": "file_dispute",
                "task_id": task_id,
                "claimant_id": poster.agent_id,
                "respondent_id": worker.agent_id,
                "claim": "Does not meet spec",
                "escrow_id": disputed_task["escrow_id"],
            }
        )
        file_resp = await platform_agent._request_raw(
            "POST",
            f"{platform_agent.config.court_url}/disputes/file",
            json={"token": file_token},
        )
        if file_resp.status_code != 201:
            return None
        dispute_id = str(file_resp.json()["dispute_id"])

    # Rebuttal
    rebuttal_token = platform_agent._sign_jws(
        {
            "action": "submit_rebuttal",
            "dispute_id": dispute_id,
            "rebuttal": "Work meets specification.",
        }
    )
    await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/{dispute_id}/rebuttal",
        json={"token": rebuttal_token},
    )

    # Ruling
    ruling_token = platform_agent._sign_jws(
        {"action": "trigger_ruling", "dispute_id": dispute_id}
    )
    ruling_resp = await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/{dispute_id}/rule",
        json={"token": ruling_token},
    )
    if ruling_resp.status_code != 200:
        return None

    payload: dict[str, Any] = ruling_resp.json()
    return payload


@pytest.mark.e2e
async def test_economic_cycle_with_dispute_partial_payout(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Confirm partial payouts from disputes flow into subsequent economic activity."""
    agents_to_close: list[BaseAgent] = []

    try:
        agent_a = await make_funded_agent(name="Agent EI1 A", balance=5000)
        agent_b = await make_funded_agent(name="Agent EI1 B", balance=0)
        agents_to_close.extend([agent_a, agent_b])

        # Round 1: A posts, B works, A disputes, Court rules
        reward_1 = 1000
        ruling = await _drive_dispute_to_ruling(
            poster=agent_a,
            worker=agent_b,
            platform_agent=platform_agent,
            reward=reward_1,
        )
        if ruling is None:
            pytest.skip("Court ruling unavailable in this environment")

        worker_pct = ruling["worker_pct"]
        worker_earned = (reward_1 * worker_pct) // 100
        poster_refund = reward_1 - worker_earned

        balance_a_after_r1 = await agent_a.get_balance()
        balance_b_after_r1 = await agent_b.get_balance()
        assert balance_a_after_r1["balance"] == 5000 - reward_1 + poster_refund
        assert balance_b_after_r1["balance"] == worker_earned

        # Conservation check after round 1
        assert balance_a_after_r1["balance"] + balance_b_after_r1["balance"] == 5000

        # Round 2: B posts task using partial earnings (if B has enough)
        if worker_earned < 200:
            pytest.skip(f"Worker earned only {worker_earned}, not enough to post a task")

        reward_2 = min(200, worker_earned)
        task_2 = await agent_b.post_task(
            title="Round 2 task",
            spec="Do more work",
            reward=reward_2,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid_2 = await agent_a.submit_bid(task_id=task_2["task_id"], amount=reward_2)
        await agent_b.accept_bid(task_id=task_2["task_id"], bid_id=bid_2["bid_id"])
        await agent_a.upload_asset(task_2["task_id"], "work2.txt", b"Round 2 work")
        await agent_a.submit_deliverable(task_2["task_id"])
        await agent_b.approve_task(task_2["task_id"])

        # Final balance check
        final_a = await agent_a.get_balance()
        final_b = await agent_b.get_balance()
        assert final_a["balance"] + final_b["balance"] == 5000, "Money conservation violated"
        assert final_a["balance"] == balance_a_after_r1["balance"] + reward_2
        assert final_b["balance"] == balance_b_after_r1["balance"] - reward_2
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run test**

```bash
cd agents && uv run pytest tests/e2e/test_economic_invariants.py::test_economic_cycle_with_dispute_partial_payout -v -m e2e
```

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_economic_invariants.py
git commit -m "test(e2e): add economic cycle with dispute partial payout test"
```

---

## Phase 2: Adversarial Tests

### Task 9: Add submit without assets rejected test

**Files:**
- Modify: `agents/tests/e2e/test_asset_store.py`

**Step 1: Append the test**

```python
@pytest.mark.e2e
async def test_submit_without_assets_rejected(make_funded_agent) -> None:
    """Adversarial: worker cannot submit deliverable with no uploaded assets."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster AS3", balance=5000)
        worker = await make_funded_agent(name="Worker AS3", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="No assets task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])

        # Try to submit without uploading any assets
        response = await worker._request_raw(
            "POST",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/submit",
            json={
                "token": worker._sign_jws(
                    {
                        "action": "submit_deliverable",
                        "task_id": task["task_id"],
                        "worker_id": worker.agent_id,
                    }
                )
            },
        )

        # Spec says "requires at least 1 asset" — expect rejection
        assert response.status_code in {400, 409}, (
            f"Expected 400 or 409 for submit without assets, got {response.status_code}"
        )

        # Task should remain in accepted state
        task_after = await poster.get_task(task["task_id"])
        assert task_after["status"] == "accepted", "Task should remain accepted after failed submit"
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run test**

```bash
cd agents && uv run pytest tests/e2e/test_asset_store.py::test_submit_without_assets_rejected -v -m e2e
```

Expected: May FAIL if validation is not enforced.

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_asset_store.py
git commit -m "test(e2e): add submit without assets rejection test"
```

---

### Task 10: Add self-feedback and duplicate feedback tests

**Files:**
- Modify: `agents/tests/e2e/test_reputation_sealed.py`

**Step 1: Append both tests**

```python
@pytest.mark.e2e
async def test_self_feedback_rejected(make_funded_agent) -> None:
    """Adversarial: agent cannot submit feedback about themselves."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster RS2", balance=5000)
        worker = await make_funded_agent(name="Worker RS2", balance=0)
        agents_to_close.extend([poster, worker])

        # Complete a task
        task = await poster.post_task(
            title="Self feedback task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
        await worker.upload_asset(task["task_id"], "result.txt", b"Hello")
        await worker.submit_deliverable(task["task_id"])
        await poster.approve_task(task["task_id"])

        # Poster tries to rate themselves
        self_fb_token = poster._sign_jws(
            {
                "action": "submit_feedback",
                "from_agent_id": poster.agent_id,
                "to_agent_id": poster.agent_id,
                "task_id": task["task_id"],
                "category": "spec_quality",
                "rating": "extremely_satisfied",
            }
        )
        response = await poster._request_raw(
            "POST",
            f"{poster.config.reputation_url}/feedback",
            json={"token": self_fb_token},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "SELF_FEEDBACK"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_duplicate_feedback_rejected(make_funded_agent) -> None:
    """Adversarial: same (task, from, to) feedback pair cannot be submitted twice."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster RS3", balance=5000)
        worker = await make_funded_agent(name="Worker RS3", balance=0)
        agents_to_close.extend([poster, worker])

        # Complete a task
        task = await poster.post_task(
            title="Dup feedback task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
        await worker.upload_asset(task["task_id"], "result.txt", b"Hello")
        await worker.submit_deliverable(task["task_id"])
        await poster.approve_task(task["task_id"])

        # First feedback succeeds
        await poster.submit_feedback(
            task_id=task["task_id"],
            to_agent_id=str(worker.agent_id),
            category="delivery_quality",
            rating="satisfied",
            comment="Good",
        )

        # Second identical feedback should fail
        dup_token = poster._sign_jws(
            {
                "action": "submit_feedback",
                "from_agent_id": poster.agent_id,
                "to_agent_id": worker.agent_id,
                "task_id": task["task_id"],
                "category": "delivery_quality",
                "rating": "extremely_satisfied",
                "comment": "Even better",
            }
        )
        response = await poster._request_raw(
            "POST",
            f"{poster.config.reputation_url}/feedback",
            json={"token": dup_token},
        )

        assert response.status_code == 409
        assert response.json()["error"] == "FEEDBACK_EXISTS"
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run tests**

```bash
cd agents && uv run pytest tests/e2e/test_reputation_sealed.py -v -m e2e
```

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_reputation_sealed.py
git commit -m "test(e2e): add self-feedback and duplicate feedback adversarial tests"
```

---

### Task 11: Add duplicate dispute rejected test

**Files:**
- Modify: `agents/tests/e2e/test_court_rulings.py`

**Step 1: Append the test**

```python
@pytest.mark.e2e
async def test_duplicate_dispute_rejected(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Adversarial: filing a second dispute on an already-disputed task is rejected."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CR5", balance=5000)
        worker = await make_funded_agent(name="Worker CR5", balance=0)
        agents_to_close.extend([poster, worker])

        disputed_task, dispute_reason = await _create_disputed_task(
            poster, worker, reward=1000
        )

        # First filing should succeed (or already exist)
        dispute_id = await _file_dispute_with_court(
            poster, worker, platform_agent, disputed_task, dispute_reason
        )
        if dispute_id is None:
            pytest.skip("Court dispute filing is not available")

        # Second filing should be rejected
        duplicate_token = platform_agent._sign_jws(
            {
                "action": "file_dispute",
                "task_id": str(disputed_task["task_id"]),
                "claimant_id": poster.agent_id,
                "respondent_id": worker.agent_id,
                "claim": "Filing again",
                "escrow_id": disputed_task["escrow_id"],
            }
        )
        duplicate_response = await platform_agent._request_raw(
            "POST",
            f"{platform_agent.config.court_url}/disputes/file",
            json={"token": duplicate_token},
        )

        assert duplicate_response.status_code == 409
        assert duplicate_response.json()["error"] == "DISPUTE_ALREADY_EXISTS"
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run test**

```bash
cd agents && uv run pytest tests/e2e/test_court_rulings.py::test_duplicate_dispute_rejected -v -m e2e
```

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_court_rulings.py
git commit -m "test(e2e): add duplicate dispute rejection test"
```

---

### Task 12: Create `test_platform_auth.py` with escrow auth tests

**Files:**
- Create: `agents/tests/e2e/test_platform_auth.py`

**Step 1: Create the file with all 3 platform auth tests**

```python
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_non_platform_cannot_release_escrow(make_funded_agent) -> None:
    """Adversarial: non-platform agent cannot release escrow."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster PA1", balance=5000)
        worker = await make_funded_agent(name="Worker PA1", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Platform auth test",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        escrow_id = task["escrow_id"]

        # Worker (non-platform) tries to release escrow to themselves
        release_token = worker._sign_jws(
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": worker.agent_id,
            }
        )
        response = await worker._request_raw(
            "POST",
            f"{worker.config.bank_url}/escrow/{escrow_id}/release",
            json={"token": release_token},
        )

        assert response.status_code == 403, (
            f"Non-platform escrow release should return 403, got {response.status_code}"
        )

        # Verify escrow is still locked — poster balance unchanged
        poster_balance = await poster.get_balance()
        assert poster_balance["balance"] == 4500, "Escrow should still be locked"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_non_platform_cannot_split_escrow(make_funded_agent) -> None:
    """Adversarial: non-platform agent cannot split escrow."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster PA2", balance=5000)
        worker = await make_funded_agent(name="Worker PA2", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Platform split auth test",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
        await worker.upload_asset(task["task_id"], "result.txt", b"Work")
        await worker.submit_deliverable(task["task_id"])
        await poster.dispute_task(task_id=task["task_id"], reason="Bad work")

        escrow_id = task["escrow_id"]

        # Worker (non-platform) tries to split escrow 100% to themselves
        split_token = worker._sign_jws(
            {
                "action": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": worker.agent_id,
                "poster_account_id": poster.agent_id,
                "worker_pct": 100,
            }
        )
        response = await worker._request_raw(
            "POST",
            f"{worker.config.bank_url}/escrow/{escrow_id}/split",
            json={"token": split_token},
        )

        assert response.status_code == 403, (
            f"Non-platform escrow split should return 403, got {response.status_code}"
        )

        # Verify balances unchanged
        worker_balance = await worker.get_balance()
        assert worker_balance["balance"] == 0, "Worker should not have received funds"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_non_platform_cannot_credit_account(make_funded_agent) -> None:
    """Adversarial: non-platform agent cannot credit another agent's account."""
    agents_to_close: list[BaseAgent] = []

    try:
        agent_a = await make_funded_agent(name="Agent PA3 A", balance=100)
        agent_b = await make_funded_agent(name="Agent PA3 B", balance=0)
        agents_to_close.extend([agent_a, agent_b])

        # Agent A (non-platform) tries to credit Agent B
        credit_token = agent_a._sign_jws(
            {
                "action": "credit",
                "account_id": agent_b.agent_id,
                "amount": 500,
                "reference": "fake_salary",
            }
        )
        response = await agent_a._request_raw(
            "POST",
            f"{agent_a.config.bank_url}/accounts/{agent_b.agent_id}/credit",
            json={"token": credit_token},
        )

        assert response.status_code == 403, (
            f"Non-platform credit should return 403, got {response.status_code}"
        )

        # Verify B's balance unchanged
        b_balance = await agent_b.get_balance()
        assert b_balance["balance"] == 0, "Agent B should not have received fraudulent credit"

        # Verify A's balance unchanged
        a_balance = await agent_a.get_balance()
        assert a_balance["balance"] == 100, "Agent A balance should be unchanged"
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run tests**

```bash
cd agents && uv run pytest tests/e2e/test_platform_auth.py -v -m e2e
```

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_platform_auth.py
git commit -m "test(e2e): add platform signature enforcement adversarial tests"
```

---

### Task 13: Add insufficient funds task posting test

**Files:**
- Modify: `agents/tests/e2e/test_economic_invariants.py`

**Step 1: Append the test**

```python
@pytest.mark.e2e
async def test_insufficient_funds_cannot_post_task(make_funded_agent) -> None:
    """Adversarial: agent with insufficient balance cannot post a task."""
    agents_to_close: list[BaseAgent] = []

    try:
        agent = await make_funded_agent(name="Agent EI2", balance=100)
        agents_to_close.append(agent)

        # Try to post a task that costs more than the balance
        response = await agent._request_raw(
            "POST",
            f"{agent.config.task_board_url}/tasks",
            json={
                "task_token": agent._sign_jws(
                    {
                        "action": "create_task",
                        "task_id": "t-insufficient-funds-test",
                        "poster_id": agent.agent_id,
                        "title": "Expensive task",
                        "spec": "Do something expensive",
                        "reward": 500,
                        "bidding_deadline_seconds": 3600,
                        "execution_deadline_seconds": 7200,
                        "review_deadline_seconds": 3600,
                    }
                ),
                "escrow_token": agent._sign_jws(
                    {
                        "action": "escrow_lock",
                        "agent_id": agent.agent_id,
                        "amount": 500,
                        "task_id": "t-insufficient-funds-test",
                    }
                ),
            },
        )

        # Escrow lock should fail — task should not be created
        assert response.status_code in {400, 402, 502}, (
            f"Expected failure for insufficient funds, got {response.status_code}"
        )

        # Balance should remain unchanged
        balance = await agent.get_balance()
        assert balance["balance"] == 100, "Balance should be unchanged after failed task posting"
    finally:
        await _close_agents(agents_to_close)
```

**Step 2: Run tests**

```bash
cd agents && uv run pytest tests/e2e/test_economic_invariants.py -v -m e2e
```

**Step 3: Commit**

```bash
git add agents/tests/e2e/test_economic_invariants.py
git commit -m "test(e2e): add insufficient funds task posting adversarial test"
```

---

## Phase 3: Verification

### Task 14: Run full e2e test suite

**Step 1: Run all new tests together**

```bash
cd agents && uv run pytest tests/e2e/test_asset_store.py tests/e2e/test_reputation_sealed.py tests/e2e/test_court_rulings.py tests/e2e/test_platform_auth.py tests/e2e/test_economic_invariants.py -v -m e2e
```

**Step 2: Run CI checks on the agents package**

```bash
cd agents && uv run ruff check tests/e2e/ && uv run ruff format --check tests/e2e/
```

If ruff reports issues, fix formatting:

```bash
cd agents && uv run ruff format tests/e2e/ && uv run ruff check tests/e2e/ --fix
```

**Step 3: Final commit if formatting was needed**

```bash
git add agents/tests/e2e/
git commit -m "style: format new e2e test files"
```

---

## Summary of Files Created

| File | Tests | Phase |
|---|---|---|
| `agents/tests/e2e/test_asset_store.py` | 3 (download, multi-upload, no-assets submit) | Tasks 1-2, 9 |
| `agents/tests/e2e/test_reputation_sealed.py` | 3 (sealed visibility, self-feedback, duplicate) | Tasks 3, 10 |
| `agents/tests/e2e/test_court_rulings.py` | 5 (escrow split, ruling on TaskBoard, reputation, no rebuttal, duplicate dispute) | Tasks 4-7, 11 |
| `agents/tests/e2e/test_platform_auth.py` | 3 (release, split, credit auth) | Task 12 |
| `agents/tests/e2e/test_economic_invariants.py` | 2 (dispute cycle, insufficient funds) | Tasks 8, 13 |

**Total: 16 tests across 5 files, 14 implementation tasks.**
