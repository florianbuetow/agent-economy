from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.config import AgentConfig
from service_auth.financial_identity import operation_id_for
from service_auth.jws import TokenPolicy
from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse

OPERATOR_ID = "a-ui-native"
OPERATION_ID = "op-00000000-0000-4000-8000-000000000501"
SECOND_OPERATION_ID = "op-00000000-0000-4000-8000-000000000601"
TASK_ID = "t-00000000-0000-4000-8000-000000000502"
ESCROW_ID = "esc-00000000-0000-4000-8000-000000000503"
DURATION_MAX = 9_223_372_036_854
SAFE_INTEGER_MAX = 9_007_199_254_740_991

POSTING_TERMS: dict[str, Any] = {
    "title": "Native UI posting",
    "spec": "Persist the exact prepared task before transport.",
    "reward": 75,
    "bidding_deadline_seconds": 120,
    "acceptance_window_seconds": 45,
    "execution_deadline_seconds": 300,
    "review_deadline_seconds": 90,
}


def agent_config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    return AgentConfig(
        name="UI operator",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url="https://identity.test",
        bank_url="https://bank.test",
        task_board_url="https://board.test",
        reputation_url="https://reputation.test",
        court_url="https://court.test",
        token_ttl_seconds=30,
        token_policy=TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0),
    )


def scope(config: AgentConfig) -> dict[str, object]:
    raw_public = config.public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "schema_version": 1,
        "operator_agent_id": OPERATOR_ID,
        "operator_public_fingerprint_sha256": hashlib.sha256(raw_public).hexdigest(),
        "service_urls": {
            "identity_url": config.identity_url,
            "bank_url": config.bank_url,
            "task_board_url": config.task_board_url,
            "reputation_url": config.reputation_url,
            "court_url": config.court_url,
        },
    }


def intent(*, operation_id: str = OPERATION_ID, **updates: object) -> TaskPostingIntent:
    value: dict[str, object] = {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": TASK_ID,
        "actor_kind": "agent",
        "actor_id": OPERATOR_ID,
        "lock_operation_id": operation_id_for("task-lock", (TASK_ID, OPERATOR_ID)),
        **POSTING_TERMS,
    }
    value.update(updates)
    return TaskPostingIntent.from_json(value)


def prepared_record(
    config: AgentConfig, posting_intent: TaskPostingIntent | None = None
) -> dict[str, object]:
    selected = intent() if posting_intent is None else posting_intent
    return {
        "record_version": 1,
        "scope": scope(config),
        "intent": selected.as_json(),
        "request_sha256": selected.request_sha256,
    }


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def publication(
    posting_intent: TaskPostingIntent,
    state: Literal["awaiting_lock", "published", "blocked_integrity"],
) -> dict[str, object]:
    admitted = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
    published = admitted + timedelta(seconds=1)
    bidding = published + timedelta(seconds=posting_intent.bidding_deadline_seconds)
    acceptance = bidding + timedelta(seconds=posting_intent.acceptance_window_seconds)
    is_published = state == "published"
    result: dict[str, object] | None = None
    if is_published:
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        result = {
            "task_id": posting_intent.task_id,
            "poster_id": posting_intent.actor_id,
            "title": posting_intent.title,
            "spec": posting_intent.spec,
            "reward": posting_intent.reward,
            "bidding_deadline_seconds": posting_intent.bidding_deadline_seconds,
            "acceptance_window_seconds": posting_intent.acceptance_window_seconds,
            "execution_deadline_seconds": posting_intent.execution_deadline_seconds,
            "review_deadline_seconds": posting_intent.review_deadline_seconds,
            "status": "open",
            "escrow_id": ESCROW_ID,
            "created_at": _timestamp(published),
            "bidding_deadline": _timestamp(bidding),
            "acceptance_deadline": _timestamp(acceptance),
            "bidding_cutoff_us": int((bidding - epoch).total_seconds() * 1_000_000),
            "acceptance_cutoff_us": int((acceptance - epoch).total_seconds() * 1_000_000),
            "lifecycle_version": 0,
            "timing_contract_version": 2,
            "event_id": 501,
        }
    return {
        "protocol_version": 1,
        "operation_id": posting_intent.operation_id,
        "task_id": posting_intent.task_id,
        "request_sha256": posting_intent.request_sha256,
        "publication_state": state,
        "lock_operation_id": posting_intent.lock_operation_id,
        "escrow_id": ESCROW_ID if is_published else None,
        "admitted_at": _timestamp(admitted),
        "published_at": _timestamp(published) if is_published else None,
        "result": result,
        "error": (
            {
                "error": "publication_integrity_blocked",
                "message": "Publication evidence requires reconciliation",
                "details": {"reason": "receipt_invalid"},
            }
            if state == "blocked_integrity"
            else None
        ),
    }


def response(
    posting_intent: TaskPostingIntent,
    state: Literal["awaiting_lock", "published", "blocked_integrity"],
    *,
    observation: Literal["submit", "status"],
) -> TaskPostingResponse:
    status = (
        200
        if observation == "status"
        else {
            "awaiting_lock": 202,
            "published": 201,
            "blocked_integrity": 409,
        }[state]
    )
    body = publication(posting_intent, state)
    if observation == "submit" and state == "blocked_integrity":
        body = {
            "error": "publication_integrity_blocked",
            "message": "Publication evidence requires reconciliation",
            "details": {"publication": body},
        }
    return TaskPostingResponse(status_code=status, body=body)


@dataclass(slots=True)
class PostingAgent:
    config: AgentConfig = field(default_factory=agent_config)
    agent_id: str = OPERATOR_ID
    prepare_calls: list[dict[str, object]] = field(default_factory=list)
    submit_calls: list[TaskPostingIntent] = field(default_factory=list)
    status_calls: list[str] = field(default_factory=list)
    submit_response: TaskPostingResponse | Exception | None = None
    status_response: TaskPostingResponse | Exception | None = None

    def prepare_task_posting(self, **terms: object) -> TaskPostingIntent:
        self.prepare_calls.append(copy.deepcopy(terms))
        return intent(**terms)

    async def submit_task_posting(self, posting_intent: TaskPostingIntent) -> TaskPostingResponse:
        self.submit_calls.append(posting_intent)
        if isinstance(self.submit_response, Exception):
            raise self.submit_response
        return self.submit_response or response(posting_intent, "published", observation="submit")

    async def get_task_posting(self, operation_id: str) -> TaskPostingResponse:
        self.status_calls.append(operation_id)
        if isinstance(self.status_response, Exception):
            raise self.status_response
        return self.status_response or response(intent(), "published", observation="status")


def mutated_record(record: dict[str, object], case: str) -> dict[str, object]:
    changed = copy.deepcopy(record)
    record_scope = changed["scope"]
    record_intent = changed["intent"]
    assert isinstance(record_scope, dict) and isinstance(record_intent, dict)
    if case == "scope-agent":
        record_scope["operator_agent_id"] = "a-foreign"
    elif case == "scope-key":
        record_scope["operator_public_fingerprint_sha256"] = "0" * 64
    elif case == "scope-url":
        urls = record_scope["service_urls"]
        assert isinstance(urls, dict)
        urls["task_board_url"] = "https://foreign.test"
    elif case == "actor":
        record_intent["actor_id"] = "a-foreign"
    elif case == "hash":
        changed["request_sha256"] = "0" * 64
    elif case == "terms":
        record_intent["reward"] = 76
    elif case == "extra":
        changed["unexpected"] = True
    else:
        raise ValueError(case)
    return changed
