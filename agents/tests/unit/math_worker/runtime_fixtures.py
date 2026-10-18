"""Contract-faithful runtime fixtures for MathWorkerLoop unit tests."""

from __future__ import annotations

import base64
import hashlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import TaskBoardCommandResponse

from base_agent.agent import BaseAgent
from base_agent.config import AgentConfig, TokenPolicy
from base_agent.runtime_config import RuntimeStateConfig
from base_agent.runtime_intents import FileTaskIntentStore, FrozenTaskIntent, RuntimeOwner
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

TASK_ID = "t-10000000-0000-4000-8000-000000000001"
WORKER_ID = "a-worker-runtime-fixture"
POSTER_ID = "a-poster-runtime-fixture"
ESCROW_ID = "esc-runtime-fixture"
SERVICE_URLS = {
    "identity_url": "https://identity.worker-fixture.example/v1",
    "bank_url": "https://bank.worker-fixture.example/v1",
    "task_board_url": "https://task-board.worker-fixture.example/v1",
    "reputation_url": "https://reputation.worker-fixture.example/v1",
    "court_url": "https://court.worker-fixture.example/v1",
}


def task_snapshot(status: str, lifecycle_version: int) -> dict[str, object]:
    """Return the public task facts required by native worker intents."""
    accepted = status != "open"
    return {
        "task_id": TASK_ID,
        "title": "Solve X",
        "spec": "Return the exact integer answer",
        "reward": 50,
        "status": status,
        "lifecycle_version": lifecycle_version,
        "poster_id": POSTER_ID,
        "worker_id": WORKER_ID if accepted else None,
        "escrow_id": ESCROW_ID,
        "accepted_reward": 50 if accepted else None,
        "accepted_bid_amount": 40 if accepted else None,
    }


def _agent_config() -> AgentConfig:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes([11]) * 32)
    return AgentConfig(
        name="Runtime Fixture Math Worker",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url=SERVICE_URLS["identity_url"],
        bank_url=SERVICE_URLS["bank_url"],
        task_board_url=SERVICE_URLS["task_board_url"],
        reputation_url=SERVICE_URLS["reputation_url"],
        court_url=SERVICE_URLS["court_url"],
        token_ttl_seconds=300,
        token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
    )


def _runtime_config(root: Path) -> RuntimeStateConfig:
    return RuntimeStateConfig(
        environment_id="worker-fixture-tests",
        directory=root,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )


def _owner(root: Path, agent: BaseAgent) -> RuntimeOwner:
    return runtime_owner_from_agent(
        runtime_config=_runtime_config(root),
        agent=agent,
        role="math-worker",
        profile="mathbot_runtime_fixture",
        handle="mathbot",
    )


@asynccontextmanager
async def runtime_worker(
    root: Path,
) -> AsyncIterator[tuple[BaseAgent, FileTaskIntentStore, RuntimeTaskCommands]]:
    """Yield a real owner-bound store/coordinator around a public agent fixture."""
    agent = BaseAgent(_agent_config())
    agent.agent_id = WORKER_ID
    mocked = cast("Any", agent)
    mocked._request = AsyncMock(side_effect=AssertionError("private decoded request used"))
    mocked._request_raw = AsyncMock(side_effect=AssertionError("private raw request used"))
    runtime_config = _runtime_config(root)
    store = FileTaskIntentStore(
        root_directory=runtime_config.directory,
        owner=_owner(root, agent),
        diagnostic_limit=runtime_config.diagnostic_limit,
        max_record_bytes=runtime_config.max_record_bytes,
    )
    try:
        with store:
            yield agent, store, RuntimeTaskCommands(agent, store)
    finally:
        await agent.close()


def install_successful_native_transports(
    agent: BaseAgent,
    store: FileTaskIntentStore,
) -> None:
    """Install typed public SDK responses matching the stored native intents."""
    mocked = cast("Any", agent)

    async def submit_bid(
        task_id: str,
        amount: int,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        intent = store.load(task_id, "bid")
        assert intent is not None
        assert intent.operation_id == operation_id
        assert intent.expected_lifecycle_version == expected_lifecycle_version
        return TaskBoardCommandResponse(
            201,
            {
                "bid_id": f"bid-{operation_id[3:]}",
                "task_id": task_id,
                "bidder_id": WORKER_ID,
                "amount": amount,
                "submitted_at": "2026-10-18T12:00:00Z",
            },
        )

    async def submit_deliverable(
        task_id: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        intent = store.load(task_id, "submission")
        assert intent is not None
        assert intent.operation_id == operation_id
        assert intent.expected_lifecycle_version == expected_lifecycle_version
        return TaskBoardCommandResponse(200, _submission_ack(intent))

    mocked.submit_bid = AsyncMock(side_effect=submit_bid)
    mocked.submit_deliverable = AsyncMock(side_effect=submit_deliverable)


def _submission_ack(intent: FrozenTaskIntent) -> dict[str, object]:
    target_version = intent.expected_lifecycle_version + 1
    event_id = 41
    result = {
        "task_id": intent.task_id,
        "command": "submit_deliverable",
        "status": "submitted",
        "lifecycle_version": target_version,
        "event_id": event_id,
        "worker_id": WORKER_ID,
    }
    operation = {
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "command": "submit_deliverable",
        "request_sha256": intent.acknowledgment_context["request_sha256"],
        "operation_state": "completed",
        "source_status": "accepted",
        "source_lifecycle_version": intent.expected_lifecycle_version,
        "target_status": "submitted",
        "target_lifecycle_version": target_version,
        "effect_kind": "none",
        "effect_state": "none",
        "effect_operation_id": None,
        "effect_result_reference": None,
        "effect_intent": None,
        "event_id": event_id,
        "result": result,
    }
    return {
        "task_id": intent.task_id,
        "status": "submitted",
        "lifecycle_version": target_version,
        "worker_id": WORKER_ID,
        "operation": operation,
    }


def assert_owner_binding(agent: BaseAgent, store: FileTaskIntentStore) -> None:
    """Assert the fixture exposes an explicit public identity and service binding."""
    raw_key = base64.b64decode(agent.get_public_key_b64(), validate=True)
    assert store.owner.registered_agent_id == WORKER_ID
    assert store.owner.key_fingerprint == hashlib.sha256(raw_key).hexdigest()
    assert dict(store.owner.service_urls) == SERVICE_URLS
