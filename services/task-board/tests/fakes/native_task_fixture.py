"""Explicit native task histories and persisted external effects for unit fixtures."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from db_gateway_service.board_authority import BoardAuthority
from db_gateway_service.money_audit import audit_money
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.task_time import epoch_us_to_utc_text, parse_utc_text_to_epoch_us
from service_auth.financial_identity import operation_id_for
from service_auth.signing import public_key_to_b64
from service_auth.task_postings import TaskPostingIntent

from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.escrow_coordinator import EscrowCoordinator
from task_board_service.services.task_operations import complete_bank_operation
from tests.fakes.gateway_task_store import GatewayTaskStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest
    from service_auth.platform import PlatformAgent
    from service_clients.bank import BankClient

START_US = parse_utc_text_to_epoch_us("2026-10-18T12:00:00Z")
DEADLINE_ACTOR_ID = "system-native-deadline-fixture"


@dataclass(frozen=True)
class TaskTerms:
    reward: int
    bidding_seconds: int
    acceptance_window_seconds: int
    execution_seconds: int
    review_seconds: int


@dataclass
class FixtureClock:
    value_us: int

    def __call__(self) -> int:
        return self.value_us

    def at_seconds(self, seconds_after_start: int) -> None:
        target = START_US + seconds_after_start * 1_000_000
        if target < self.value_us:
            raise ValueError("Fixture clock must not move backwards")
        self.value_us = target

    def iso(self) -> str:
        return epoch_us_to_utc_text(self.value_us)


def publish_open_task(
    store: GatewayTaskStore,
    *,
    task_id: str,
    poster_id: str,
    title: str,
    spec: str,
    terms: TaskTerms,
    timestamp: str,
    event: dict[str, Any],
) -> TaskPostingIntent:
    """Persist one fresh task through native admission, Bank proof, and reconciliation."""

    intent = TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": operation_id_for("native-task-fixture", (task_id, "publication")),
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": poster_id,
            "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
            "title": title,
            "spec": spec,
            "reward": terms.reward,
            "bidding_deadline_seconds": terms.bidding_seconds,
            "acceptance_window_seconds": terms.acceptance_window_seconds,
            "execution_deadline_seconds": terms.execution_seconds,
            "review_deadline_seconds": terms.review_seconds,
        }
    )
    admitted = store.admit_task_publication(intent)
    assert admitted.status_code == 202
    assert admitted.body["publication_state"] == "awaiting_lock"
    store.writer.escrow_lock(
        {
            "operation_id": intent.lock_operation_id,
            "escrow_id": f"esc-{task_id}",
            "payer_account_id": poster_id,
            "amount": terms.reward,
            "task_id": task_id,
            "created_at": timestamp,
            "tx_id": f"tx-lock-{task_id}",
            "event": {**event, "agent_id": poster_id},
        }
    )
    published = store.reconcile_task_publication(intent.operation_id, intent)
    assert published.status_code == 201
    assert published.body["publication_state"] == "published"
    assert published.body["task_id"] == task_id
    return intent


class NativeTaskFixture:
    """Build real facts explicitly; the underlying adapter invents no history."""

    def __init__(
        self,
        *,
        store: GatewayTaskStore,
        clock: FixtureClock,
        authority: BoardAuthority,
        poster_id: str,
        worker_id: str,
    ) -> None:
        self.store = store
        self.clock = clock
        self.authority = authority
        self.poster_id = poster_id
        self.worker_id = worker_id
        self.bank = AsyncMock()
        self.bank.escrow_release = AsyncMock(side_effect=self.persist_release)
        self.escrow = EscrowCoordinator(central_bank_client=cast("BankClient", self.bank))

    def event(self, source: str, event_type: str, task_id: str | None) -> dict[str, Any]:
        return {
            "event_source": source,
            "event_type": event_type,
            "timestamp": self.clock.iso(),
            "task_id": task_id,
            "summary": event_type,
            "payload": "{}",
        }

    def task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        assert task is not None
        return task

    def open_task(self, task_id: str, terms: TaskTerms) -> None:
        publish_open_task(
            self.store,
            task_id=task_id,
            poster_id=self.poster_id,
            title=task_id,
            spec="Explicit deadline fixture terms",
            terms=terms,
            timestamp=self.clock.iso(),
            event=self.event("central-bank", "escrow.locked", task_id),
        )

    def command(
        self,
        task_id: str,
        name: str,
        status: str,
        version: int,
        actor_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "protocol_version": 1,
            "operation_id": operation_id_for("deadline-fixture", (task_id, name)),
            "task_id": task_id,
            "command": name,
            "expected_status": status,
            "expected_lifecycle_version": version,
            "actor_kind": "agent",
            "actor_id": actor_id,
            "arguments": arguments,
        }

    def bid(self, task_id: str, amount: int) -> None:
        result = self.store.claim_task_operation(
            self.command(
                task_id,
                "submit_bid",
                "open",
                0,
                self.worker_id,
                {"bid_id": f"bid-{task_id}", "bidder_id": self.worker_id, "amount": amount},
            )
        )
        assert result["operation_state"] == "completed"

    def accept(self, task_id: str, bid_amount: int) -> None:
        task = self.task(task_id)
        result = self.store.claim_task_operation(
            self.command(
                task_id,
                "accept_bid",
                "open",
                0,
                self.poster_id,
                {
                    "bid_id": f"bid-{task_id}",
                    "worker_id": self.worker_id,
                    "escrow_id": task["escrow_id"],
                    "reward": task["reward"],
                    "bid_amount": bid_amount,
                },
            )
        )
        assert result["operation_state"] == "completed"
        assert self.task(task_id)["status"] == "accepted"

    def submit(self, task_id: str) -> None:
        self.store.insert_asset(
            {
                "asset_id": f"asset-{task_id}",
                "task_id": task_id,
                "uploader_id": self.worker_id,
                "filename": "answer.txt",
                "content_type": "text/plain",
                "size_bytes": 2,
                "storage_path": f"{task_id}/asset-{task_id}/answer.txt",
                "content_hash": "a" * 64,
                "uploaded_at": self.clock.iso(),
            },
            expected_lifecycle_version=1,
            max_files_per_task=10,
        )
        result = self.store.claim_task_operation(
            self.command(
                task_id,
                "submit_deliverable",
                "accepted",
                1,
                self.worker_id,
                {"worker_id": self.worker_id},
            )
        )
        assert result["operation_state"] == "completed"
        assert self.task(task_id)["status"] == "submitted"

    def accepted_arguments(self, task_id: str) -> dict[str, Any]:
        task = self.task(task_id)
        return {
            "poster_id": self.poster_id,
            "worker_id": self.worker_id,
            "escrow_id": task["escrow_id"],
            "reward": task["accepted_reward"],
        }

    async def approve(self, task_id: str) -> None:
        arguments = self.accepted_arguments(task_id)
        arguments["source_reference"] = f"task:{task_id}:approved"
        claimed = self.store.claim_task_operation(
            self.command(task_id, "approve_task", "submitted", 2, self.poster_id, arguments)
        )
        result = await complete_bank_operation(self.store, self.escrow, claimed)
        assert result["operation_state"] == "completed"
        assert self.task(task_id)["status"] == "approved"

    def dispute(self, task_id: str, rebuttal_seconds: int) -> None:
        arguments = self.accepted_arguments(task_id)
        arguments["reason"] = "The submitted answer is incomplete"
        claimed = self.store.claim_task_operation(
            self.command(task_id, "dispute_task", "submitted", 2, self.poster_id, arguments)
        )
        assert claimed["operation_state"] == "claimed"
        self.store.writer.file_claim(
            {
                "claim_id": f"clm-{task_id}",
                "task_id": task_id,
                "claimant_id": self.poster_id,
                "respondent_id": self.worker_id,
                "reason": arguments["reason"],
                "status": "rebuttal_pending",
                "rebuttal_deadline": epoch_us_to_utc_text(
                    self.clock.value_us + rebuttal_seconds * 1_000_000
                ),
                "filed_at": self.clock.iso(),
                "event": self.event("court", "claim.filed", task_id),
            }
        )
        result = self.store.finalize_task_operation(claimed["operation_id"])
        assert result["operation_state"] == "completed"
        assert self.task(task_id)["status"] == "disputed"

    def rebut(self, task_id: str) -> None:
        self.store.writer.submit_rebuttal(
            {
                "rebuttal_id": f"reb-{task_id}",
                "claim_id": f"clm-{task_id}",
                "agent_id": self.worker_id,
                "content": "The answer meets the specification",
                "submitted_at": self.clock.iso(),
                "claim_status_update": "rebuttal_submitted",
                "event": self.event("court", "rebuttal.submitted", task_id),
            },
            {"status": "rebuttal_pending"},
        )
        assert self.task(task_id)["rebuttal_submitted_at"] is not None

    def seed_legacy_timing_contract(self, task_id: str) -> None:
        """Explicit historical sidecar fixture; this does not execute migration."""
        self.store.writer.connection.execute(
            "UPDATE board_task_contracts SET timing_contract_version=1, "
            "acceptance_window_seconds=NULL, acceptance_cutoff_us=bidding_cutoff_us "
            "WHERE task_id=?",
            (task_id,),
        )
        self.store.writer.connection.commit()
        task = self.task(task_id)
        assert task["timing_contract_version"] == 1
        assert task["acceptance_window_seconds"] is None
        assert task["acceptance_cutoff_us"] == task["bidding_cutoff_us"]

    async def persist_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        escrow = self.store.reader.get_escrow(escrow_id)
        assert escrow is not None
        return self.store.writer.escrow_release(
            {
                "operation_id": operation_id,
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
                "source_reference": source_reference,
                "tx_id": f"tx-{operation_id}",
                "resolved_at": self.clock.iso(),
                "event": self.event("central-bank", "escrow.released", str(escrow["task_id"])),
            }
        )

    def evaluator(self, platform_agent: PlatformAgent | None) -> DeadlineEvaluator:
        return DeadlineEvaluator(
            store=self.store,
            escrow_coordinator=self.escrow,
            platform_agent=platform_agent,
            deadline_actor_id=self.authority.deadline_actor_id,
        )

    def assert_settlement(
        self,
        task_id: str,
        *,
        command: str,
        status: str,
        recipient: str,
        event_type: str,
    ) -> str:
        task = self.task(task_id)
        assert task["status"] == status
        assert task["pending_transition"] is None
        operation = self.store.writer.connection.execute(
            "SELECT operation_id,operation_state FROM board_task_operations "
            "WHERE task_id=? AND command=?",
            (task_id, command),
        ).fetchall()
        assert len(operation) == 1
        operation_id, operation_state = operation[0]
        assert operation_state == "completed"
        receipts = self.store.writer.connection.execute(
            "SELECT operation_kind,escrow_id FROM bank_operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchall()
        assert [tuple(row) for row in receipts] == [("escrow_release", task["escrow_id"])]
        legs = self.store.writer.connection.execute(
            "SELECT account_id,direction,amount FROM bank_operation_legs WHERE operation_id=?",
            (operation_id,),
        ).fetchall()
        assert [tuple(row) for row in legs] == [(recipient, "credit", task["reward"])]
        transactions = self.store.writer.connection.execute(
            "SELECT account_id,amount FROM bank_transactions "
            "WHERE reference=? AND type='escrow_release'",
            (task["escrow_id"],),
        ).fetchall()
        assert [tuple(row) for row in transactions] == [(recipient, task["reward"])]
        assert (
            self.store.writer.connection.execute(
                "SELECT COUNT(*) FROM bank_operation_receipts "
                "WHERE escrow_id=? AND operation_kind='escrow_release'",
                (task["escrow_id"],),
            ).fetchone()[0]
            == 1
        )
        assert audit_money(self.store.writer.connection).financially_clean
        escrow = self.store.reader.get_escrow(str(task["escrow_id"]))
        assert escrow is not None and escrow["status"] == "released"
        assert (
            self.store.writer.connection.execute(
                "SELECT COUNT(*) FROM events WHERE task_id=? "
                "AND event_source='board' AND event_type=?",
                (task_id, event_type),
            ).fetchone()[0]
            == 1
        )
        return str(operation_id)


def _public_key() -> str:
    return f"ed25519:{public_key_to_b64(Ed25519PrivateKey.generate().public_key())}"


@contextmanager
def native_task_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[NativeTaskFixture]:
    """Provide an owned database, explicit business clock, and isolated captured logs."""
    clock = FixtureClock(START_US)

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> ClockDateTime:
            value = cls(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=clock.value_us)
            return value.replace(tzinfo=None) if tz is None else value.astimezone(tz)

    logger = logging.Logger("mp07-deadline-fixture")
    logger.propagate = False
    handler = logging.StreamHandler()
    logger.addHandler(handler)

    def _logger(_name: str) -> logging.Logger:
        return logger

    monkeypatch.setattr("task_board_service.services.deadline_evaluator.datetime", ClockDateTime)
    monkeypatch.setattr("task_board_service.services.deadline_evaluator.get_logger", _logger)
    authority = BoardAuthority(
        platform_public_key=_public_key(), deadline_actor_id=DEADLINE_ACTOR_ID
    )
    store = GatewayTaskStore(
        db_path=tmp_path / "native-deadlines.db",
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V6),
        caller_kid="gateway-task-board",
        authority=authority,
        clock_us=clock,
    )
    try:
        scenario = NativeTaskFixture(
            store=store,
            clock=clock,
            authority=authority,
            poster_id="a-poster",
            worker_id="a-worker",
        )
        for agent_id, public_key in (
            ("a-platform", authority.platform_public_key),
            (scenario.poster_id, _public_key()),
            (scenario.worker_id, _public_key()),
        ):
            store.writer.register_agent(
                {
                    "agent_id": agent_id,
                    "name": agent_id,
                    "public_key": public_key,
                    "registered_at": clock.iso(),
                    "event": scenario.event("identity", "agent.registered", None),
                }
            )
        store.writer.create_account(
            {
                "operation_id": operation_id_for("deadline-fixture", ("initial-funding",)),
                "account_id": scenario.poster_id,
                "balance": 1000,
                "created_at": clock.iso(),
                "initial_credit": {
                    "tx_id": "tx-initial-funding",
                    "amount": 1000,
                    "reference": "initial_balance",
                    "timestamp": clock.iso(),
                },
                "event": scenario.event("bank", "account.created", None),
            }
        )
        store.writer.create_account(
            {
                "account_id": scenario.worker_id,
                "balance": 0,
                "created_at": clock.iso(),
                "event": scenario.event("bank", "account.created", None),
            }
        )
        yield scenario
    finally:
        store.close()
        handler.close()
