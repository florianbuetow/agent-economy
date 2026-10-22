"""Exact MP-05 posting-ledger values shared by tests-first caller slices."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import httpx
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import (
    TaskPostingIntent,
    TaskPostingResponse,
    parse_task_posting_status_response,
    parse_task_posting_submit_response,
)

from base_agent.runtime_intents import RuntimeOwner

if TYPE_CHECKING:
    from pathlib import Path

ACTOR_ID = "a-mp05-poster"
OPERATION_ID = "op-00000000-0000-4000-8000-000000000501"
TASK_ID = "t-00000000-0000-4000-8000-000000000502"
ESCROW_ID = "esc-00000000-0000-4000-8000-000000000503"
SOURCE_BYTES = (
    b'{"level":2,"problem_type":"proof","solution_note":null,'
    b'"solutions":["one","two"],"spec":"Publish this exact task once.",'
    b'"title":"Durable publication"}\n'
)


def canonical_bytes(value: object) -> bytes:
    """Render the exact canonical JSON representation used by the contract."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Hash one canonical JSON value."""

    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def runtime_owner(**overrides: object) -> RuntimeOwner:
    """Return one exact feeder runtime owner."""

    values: dict[str, object] = {
        "schema_version": 1,
        "environment_id": "mp05-posting-tests",
        "role": "task-feeder",
        "profile": "task-feeder",
        "handle": "poster-mp05",
        "registered_agent_id": ACTOR_ID,
        "key_fingerprint": "5" * 64,
        "service_urls": {
            "identity_url": "https://identity.example",
            "bank_url": "https://bank.example",
            "task_board_url": "https://task-board.example",
            "reputation_url": "https://reputation.example",
            "court_url": "https://court.example",
        },
    }
    values.update(overrides)
    return RuntimeOwner(**values)  # type: ignore[arg-type]


def raw_task(**overrides: object) -> dict[str, object]:
    """Return the complete immutable source evidence for one selected task."""

    values: dict[str, object] = {
        "title": "Durable publication",
        "spec": "Publish this exact task once.",
        "solutions": ["one", "two"],
        "level": 2,
        "problem_type": "proof",
        "solution_note": None,
    }
    values.update(overrides)
    return values


def source_binding(source_path: Path, *, shuffle: bool = False) -> dict[str, object]:
    """Return one complete source and posting-terms binding."""

    return {
        "source_path": str(source_path.resolve()),
        "source_snapshot_sha256": hashlib.sha256(SOURCE_BYTES).hexdigest(),
        "source_size_bytes": len(SOURCE_BYTES),
        "source_entry_count": 1,
        "posting_terms": {
            "base_reward": 7,
            "reward_per_level": 3,
            "bidding_deadline_seconds": 60,
            "acceptance_window_seconds": 30,
            "execution_deadline_seconds": 120,
            "review_deadline_seconds": 90,
            "shuffle": shuffle,
        },
    }


def selection(
    *,
    source_entry_index: int = 0,
    source_line_number: int = 1,
    selected_task: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return the exact caller-supplied selection evidence."""

    return {
        "source_entry_index": source_entry_index,
        "source_line_number": source_line_number,
        "source_line_sha256": hashlib.sha256(SOURCE_BYTES).hexdigest(),
        "raw_task": raw_task() if selected_task is None else selected_task,
    }


def posting_intent(
    *,
    operation_id: str = OPERATION_ID,
    task_id: str = TASK_ID,
    actor_id: str = ACTOR_ID,
    title: str = "Durable publication",
    spec: str = "Publish this exact task once.",
    reward: int = 13,
    bidding_deadline_seconds: int = 60,
    acceptance_window_seconds: int = 30,
    execution_deadline_seconds: int = 120,
    review_deadline_seconds: int = 90,
) -> TaskPostingIntent:
    """Return one strictly parsed native publication intent."""

    return TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": actor_id,
            "lock_operation_id": operation_id_for("task-lock", (task_id, actor_id)),
            "title": title,
            "spec": spec,
            "reward": reward,
            "bidding_deadline_seconds": bidding_deadline_seconds,
            "acceptance_window_seconds": acceptance_window_seconds,
            "execution_deadline_seconds": execution_deadline_seconds,
            "review_deadline_seconds": review_deadline_seconds,
        }
    )


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _envelope(intent: TaskPostingIntent, state: str) -> dict[str, object]:
    admitted = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    published = admitted + timedelta(seconds=1)
    bidding = published + timedelta(seconds=intent.bidding_deadline_seconds)
    acceptance = bidding + timedelta(seconds=intent.acceptance_window_seconds)
    published_text = _timestamp(published)
    is_published = state == "published"
    is_blocked = state == "blocked_integrity"
    result: dict[str, object] | None = None
    if is_published:
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        bidding_us = int((bidding - epoch).total_seconds() * 1_000_000)
        acceptance_us = int((acceptance - epoch).total_seconds() * 1_000_000)
        result = {
            "task_id": intent.task_id,
            "poster_id": intent.actor_id,
            "title": intent.title,
            "spec": intent.spec,
            "reward": intent.reward,
            "bidding_deadline_seconds": intent.bidding_deadline_seconds,
            "acceptance_window_seconds": intent.acceptance_window_seconds,
            "execution_deadline_seconds": intent.execution_deadline_seconds,
            "review_deadline_seconds": intent.review_deadline_seconds,
            "status": "open",
            "escrow_id": ESCROW_ID,
            "created_at": published_text,
            "bidding_deadline": _timestamp(bidding),
            "acceptance_deadline": _timestamp(acceptance),
            "bidding_cutoff_us": bidding_us,
            "acceptance_cutoff_us": acceptance_us,
            "lifecycle_version": 0,
            "timing_contract_version": 2,
            "event_id": 501,
        }
    return {
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "request_sha256": intent.request_sha256,
        "publication_state": state,
        "lock_operation_id": intent.lock_operation_id,
        "escrow_id": ESCROW_ID if is_published else None,
        "admitted_at": _timestamp(admitted),
        "published_at": published_text if is_published else None,
        "result": result,
        "error": (
            {
                "error": "publication_integrity_blocked",
                "message": "Publication evidence requires reconciliation",
                "details": {"reason": "receipt_invalid"},
            }
            if is_blocked
            else None
        ),
    }


def posting_response(
    intent: TaskPostingIntent,
    state: Literal["awaiting_lock", "published", "blocked_integrity"],
    *,
    status_observation: bool = False,
) -> TaskPostingResponse:
    """Build and validate a real SDK response for submit or owner status."""

    envelope = _envelope(intent, state)
    request = httpx.Request("GET" if status_observation else "POST", "https://task-board.example")
    if status_observation:
        response = httpx.Response(200, request=request, json=envelope)
        return parse_task_posting_status_response(response, intent.operation_id)
    if state == "blocked_integrity":
        wrapper = {
            "error": "publication_integrity_blocked",
            "message": "Publication evidence requires reconciliation",
            "details": {"publication": envelope},
        }
        response = httpx.Response(409, request=request, json=wrapper)
    else:
        response = httpx.Response(
            201 if state == "published" else 202,
            request=request,
            json=envelope,
        )
    return parse_task_posting_submit_response(response, intent)
