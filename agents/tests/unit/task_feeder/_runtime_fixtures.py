"""Shared real-journal fixtures for Task Feeder unit tests."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import TaskBoardCommandResponse, public_key_to_b64

from base_agent.runtime_config import RuntimeStateConfig
from base_agent.runtime_intent_models import canonical_sha256
from base_agent.runtime_intents import FileTaskIntentStore, FrozenTaskIntent
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from base_agent.agent import BaseAgent

TASK_ID = "t-00000000-0000-4000-8000-000000000901"
SECOND_TASK_ID = "t-00000000-0000-4000-8000-000000000902"
POSTER_ID = "a-feeder-fixture-reconciliation"
WORKER_ID = "a-worker-fixture-reconciliation"
ESCROW_ID = "esc-feeder-fixture-reconciliation"

_URLS = MappingProxyType(
    {
        "identity_url": "https://identity.feeder-fixture.test/v1/",
        "bank_url": "https://bank.feeder-fixture.test/v1/",
        "task_board_url": "https://task-board.feeder-fixture.test/v1/",
        "reputation_url": "https://reputation.feeder-fixture.test/v1/",
        "court_url": "https://court.feeder-fixture.test/v1/",
    }
)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    identity_url: str
    bank_url: str
    task_board_url: str
    reputation_url: str
    court_url: str


def make_agent() -> MagicMock:
    """Return a public-SDK double with an explicit registered identity."""
    private_key = Ed25519PrivateKey.from_private_bytes(bytes([19]) * 32)
    agent = MagicMock()
    agent.agent_id = POSTER_ID
    agent.config = AgentConfig(**_URLS)
    agent.get_public_key_b64.return_value = public_key_to_b64(private_key.public_key())
    agent.list_tasks = AsyncMock(return_value=[])
    agent.list_bids = AsyncMock(return_value=[])
    agent.get_agent_feedback = AsyncMock(return_value=[])
    agent.get_task = AsyncMock(return_value=submitted_task("4"))
    agent.list_assets = AsyncMock(return_value=[])
    agent.download_asset = AsyncMock(return_value=b"")
    agent.accept_bid = AsyncMock()
    agent.approve_task = AsyncMock()
    agent.dispute_task = AsyncMock()
    agent.get_task_posting = AsyncMock(
        side_effect=AssertionError("Unexpected posting status request")
    )
    agent.submit_task_posting = AsyncMock(
        side_effect=AssertionError("Unexpected posting submission")
    )
    return agent


def open_task(*, task_id: str = TASK_ID) -> dict[str, object]:
    return {
        "task_id": task_id,
        "poster_id": POSTER_ID,
        "status": "open",
        "lifecycle_version": 7,
        "created_at": "2026-07-10T12:00:00.000000Z",
        "escrow_id": ESCROW_ID,
        "reward": 100,
        "accepted_reward": None,
        "accepted_bid_amount": None,
    }


def submitted_task(answer: str | None, *, task_id: str = TASK_ID) -> dict[str, object]:
    task: dict[str, object] = {
        "task_id": task_id,
        "poster_id": POSTER_ID,
        "worker_id": WORKER_ID,
        "status": "submitted",
        "lifecycle_version": 11,
        "escrow_id": ESCROW_ID,
        "reward": 100,
        "accepted_reward": 100,
        "accepted_bid_amount": 60,
    }
    if answer is not None:
        task.update(
            submitted_answer=answer,
            submission={"answer": answer},
            deliverable={"answer": answer},
        )
    return task


def completion_ack(intent: FrozenTaskIntent) -> dict[str, object]:
    """Build the exact successful public mutation acknowledgment for an intent."""
    serialized = intent.as_json()
    context = cast("dict[str, object]", serialized["acknowledgment_context"])
    frozen = cast("dict[str, object]", context["frozen_task_facts"])
    result_facts = cast("dict[str, object]", context["result_facts"])
    result = {
        "task_id": intent.task_id,
        "command": intent.action,
        "status": intent.target_status,
        "lifecycle_version": intent.expected_lifecycle_version + 1,
        "event_id": 901,
        **result_facts,
    }
    effect_kind = "none"
    effect_state = "none"
    effect_operation_id: str | None = None
    effect_result_reference: str | None = None
    effect_intent: dict[str, object] | None = None
    if intent.action == "approve_task":
        financial_request = {
            "request_version": 1,
            "operation_kind": "escrow_release",
            "escrow_id": frozen["escrow_id"],
            "source_reference": f"task:{intent.task_id}:approved",
            "recipient_account_id": frozen["worker_id"],
        }
        effect_kind = "bank_release"
        effect_state = "succeeded"
        effect_operation_id = intent.operation_id
        effect_result_reference = intent.operation_id
        effect_intent = {
            "financial_request": financial_request,
            "financial_request_sha256": canonical_sha256(financial_request),
            "expected_escrow": {
                "escrow_id": frozen["escrow_id"],
                "task_id": intent.task_id,
                "payer_account_id": frozen["poster_id"],
                "amount": frozen["accepted_reward"],
            },
        }
    elif intent.action == "dispute_task":
        dispute_id = "clm-feeder-fixture-reconciliation"
        reason = cast("dict[str, object]", serialized["public_arguments"])["reason"]
        result.update(dispute_id=dispute_id, effect_result_reference=dispute_id)
        effect_kind = "court_claim"
        effect_state = "succeeded"
        effect_result_reference = dispute_id
        effect_intent = {
            "court_claim": {
                "task_id": intent.task_id,
                "claimant_id": frozen["poster_id"],
                "respondent_id": frozen["worker_id"],
                "reason": reason,
            },
            "expected_escrow": {
                "escrow_id": frozen["escrow_id"],
                "task_id": intent.task_id,
                "payer_account_id": frozen["poster_id"],
                "amount": frozen["accepted_reward"],
            },
        }
    operation = {
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "command": intent.action,
        "request_sha256": context["request_sha256"],
        "operation_state": "completed",
        "source_status": intent.source_status,
        "source_lifecycle_version": intent.expected_lifecycle_version,
        "target_status": intent.target_status,
        "target_lifecycle_version": intent.expected_lifecycle_version + 1,
        "effect_kind": effect_kind,
        "effect_state": effect_state,
        "effect_operation_id": effect_operation_id,
        "effect_result_reference": effect_result_reference,
        "effect_intent": effect_intent,
        "event_id": 901,
        "result": result,
    }
    return {
        "task_id": intent.task_id,
        "status": intent.target_status,
        "lifecycle_version": intent.expected_lifecycle_version + 1,
        "worker_id": frozen["worker_id"],
        "operation": operation,
    }


@contextmanager
def runtime_commands(
    tmp_path: Path,
    agent: MagicMock,
    *,
    acknowledgment: str = "completed",
) -> Iterator[tuple[RuntimeTaskCommands, FileTaskIntentStore]]:
    """Open the real feeder journal and configure the SDK acknowledgment mode."""
    runtime_config = RuntimeStateConfig(
        environment_id="mp07-feeder-fixture-reconciliation",
        directory=tmp_path,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )
    owner = runtime_owner_from_agent(
        runtime_config=runtime_config,
        agent=cast("BaseAgent", agent),
        role="task-feeder",
        profile="task-feeder",
        handle="feeder-fixture-reconciliation",
    )
    with FileTaskIntentStore(
        root_directory=tmp_path,
        owner=owner,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    ) as store:

        async def respond(
            task_id: str,
            *_args: object,
            operation_id: str,
            expected_lifecycle_version: int,
        ) -> TaskBoardCommandResponse:
            slot = "acceptance" if agent.accept_bid.await_count > 0 else "review-decision"
            intent = store.load(task_id, slot)
            assert intent is not None
            assert operation_id == intent.operation_id
            assert expected_lifecycle_version == intent.expected_lifecycle_version
            if acknowledgment == "completed":
                return TaskBoardCommandResponse(200, completion_ack(intent))
            if acknowledgment == "generic_conflict":
                return TaskBoardCommandResponse(
                    409,
                    {"error": "conflict", "message": "conflict", "details": {}},
                )
            if acknowledgment == "definitive_rejection":
                return TaskBoardCommandResponse(
                    409,
                    {
                        "error": "invalid_status",
                        "message": "task is no longer in the required state",
                        "details": {
                            "expected_status": intent.source_status,
                            "current_status": "expired",
                        },
                    },
                )
            return TaskBoardCommandResponse(500, {})

        agent.accept_bid.side_effect = respond
        agent.approve_task.side_effect = respond
        agent.dispute_task.side_effect = respond
        yield RuntimeTaskCommands(cast("BaseAgent", agent), store), store
