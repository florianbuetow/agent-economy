"""MP-08 recovery through real signed Gateway histories and durable receipts."""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from service_commons.exceptions import ServiceError

from tests.fakes.board_mp08_http_history import (
    Command,
    RecoveryHistory,
    make_task_store,
    persist_effect,
    prepare_recovery_history,
    register_platform_identity,
)

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_RECOVERY_WORKER = "recovery-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class _Recovery(Protocol):
    async def run_cycle(self) -> int: ...


class _Gateway(Protocol):
    base_url: str


def _worker(store: object, bank: object) -> _Recovery:
    try:
        module = importlib.import_module("task_board_service.services.financial_effect_recovery")
    except ModuleNotFoundError as exc:
        if exc.name != "task_board_service.services.financial_effect_recovery":
            raise
        pytest.fail("Missing MP-08 FinancialEffectRecovery capability")
    return cast(
        "_Recovery",
        module.FinancialEffectRecovery(
            store=store,
            bank=bank,
            worker_id=_RECOVERY_WORKER,
            max_delivery_attempts_per_cycle=10,
        ),
    )


class _PersistingBank:
    def __init__(
        self,
        client: httpx.AsyncClient,
        history: RecoveryHistory,
        *,
        lose_ack: bool,
    ) -> None:
        self.client = client
        self.history = history
        self.lose_ack = lose_ack
        self.calls = 0

    async def escrow_release(self, **terms: object) -> dict[str, object]:
        assert self.history.effect_kind == "bank_release"
        assert terms == {
            "escrow_id": self.history.escrow_id,
            "recipient_account_id": self.history.worker_id or self.history.poster_id,
            "operation_id": self.history.operation_id,
            "source_reference": self.history.source_reference,
        }
        return await self._persist()

    async def escrow_split(self, **terms: object) -> dict[str, object]:
        assert self.history.effect_kind == "bank_split"
        assert terms == {
            "escrow_id": self.history.escrow_id,
            "worker_account_id": self.history.worker_id,
            "poster_account_id": self.history.poster_id,
            "worker_pct": self.history.worker_pct,
            "operation_id": self.history.operation_id,
            "source_reference": self.history.source_reference,
        }
        return await self._persist()

    async def _persist(self) -> dict[str, object]:
        self.calls += 1
        assert self.calls == 1, "immutable effect was dispatched more than once"
        await persist_effect(self.client, self.history)
        if self.lose_ack:
            raise ServiceError("central_bank_unavailable", "ack lost", 503, {})
        return {"untrusted_ack": True}


def _assert_conserved(db_path: str, history: RecoveryHistory) -> None:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
            (history.operation_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM board_task_effect_deliveries "
            "WHERE effect_operation_id=? AND delivery_state='completed'",
            (history.operation_id,),
        ).fetchone() == (1,)
        status = connection.execute(
            "SELECT status FROM board_tasks WHERE task_id=?", (history.task_id,)
        ).fetchone()
        expected = {
            "approve_task": "approved",
            "auto_approve": "approved",
            "cancel_task": "cancelled",
            "expire_bidding": "expired",
            "record_ruling": "ruled",
        }[history.command]
        assert status == (expected,)
        balances = int(
            connection.execute("SELECT COALESCE(SUM(balance),0) FROM bank_accounts").fetchone()[0]
        )
        locked = int(
            connection.execute(
                "SELECT COALESCE(SUM(amount),0) FROM bank_escrow WHERE status='locked'"
            ).fetchone()[0]
        )
        assert balances + locked == 100
    finally:
        connection.close()


@pytest.mark.parametrize(
    "command",
    ["approve_task", "auto_approve", "cancel_task", "expire_bidding", "record_ruling"],
)
async def test_real_terminal_histories_recover_exact_receipt_projection_and_conservation(
    command: Command,
    _gateway: _Gateway,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> None:
    platform_actor_id = None
    if command == "record_ruling":
        platform_actor_id = await register_platform_identity(
            gateway_client,
            Path(gateway_db_path).parent / "agent-keys" / "platform.pub",
        )
    history = await prepare_recovery_history(
        gateway_client,
        command,
        platform_actor_id=platform_actor_id,
    )
    store = make_task_store(_gateway.base_url, gateway_db_path)
    bank = _PersistingBank(gateway_client, history, lose_ack=False)
    try:
        assert await _worker(store, bank).run_cycle() == 1
    finally:
        store.close()
    assert bank.calls == 1
    _assert_conserved(gateway_db_path, history)


async def test_real_lost_ack_is_adopted_without_a_second_dispatch(
    _gateway: _Gateway,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> None:
    history = await prepare_recovery_history(gateway_client, "approve_task")
    store = make_task_store(_gateway.base_url, gateway_db_path)
    bank = _PersistingBank(gateway_client, history, lose_ack=True)
    try:
        assert await _worker(store, bank).run_cycle() == 1
    finally:
        store.close()
    assert bank.calls == 1
    _assert_conserved(gateway_db_path, history)


async def test_real_finalize_commit_with_lost_ack_is_monotonic_and_idempotent(
    _gateway: _Gateway,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> None:
    history = await prepare_recovery_history(gateway_client, "approve_task")
    delegate = make_task_store(_gateway.base_url, gateway_db_path)

    class AfterCommitLostAck:
        def __init__(self) -> None:
            self.finalize_calls = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(delegate, name)

        def finalize_task_effect_delivery(self, operation_id: str) -> dict[str, Any]:
            self.finalize_calls += 1
            result = cast("Any", delegate).finalize_task_effect_delivery(operation_id)
            if self.finalize_calls == 2:
                raise ServiceError("gateway_unavailable", "response lost", 503, {})
            return result

    store = AfterCommitLostAck()
    bank = _PersistingBank(gateway_client, history, lose_ack=False)
    try:
        assert await _worker(store, bank).run_cycle() == 1
        assert await _worker(store, bank).run_cycle() == 0
    finally:
        delegate.close()
    assert bank.calls == 1
    _assert_conserved(gateway_db_path, history)


async def test_receipt_persists_before_projection_then_next_cycle_completes_without_redispatch(
    _gateway: _Gateway,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> None:
    history = await prepare_recovery_history(gateway_client, "approve_task")
    delegate = make_task_store(_gateway.base_url, gateway_db_path)

    class RejectFirstPostBankFinalize:
        def __init__(self) -> None:
            self.finalize_calls = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(delegate, name)

        def finalize_task_effect_delivery(self, operation_id: str) -> dict[str, Any]:
            self.finalize_calls += 1
            if self.finalize_calls == 2:
                raise ServiceError(
                    "gateway_unavailable", "request rejected before forwarding", 503, {}
                )
            return cast("Any", delegate).finalize_task_effect_delivery(operation_id)

    store = RejectFirstPostBankFinalize()
    bank = _PersistingBank(gateway_client, history, lose_ack=False)
    try:
        assert await _worker(store, bank).run_cycle() == 1
        projection = await gateway_client.get(f"/board/tasks/{history.task_id}")
        assert projection.status_code == 200, projection.text
        task = projection.json()
        assert task["status"] == "submitted"
        assert task["financial_effect"] == {
            "schema_version": 1,
            "settlement_state": "pending",
            "receipt_state": "confirmed",
            "outcome": "worker_paid",
            "operation_id": history.operation_id,
            "receipt_reference": history.operation_id,
            "business_projection_pending": True,
            "pending_target_status": "approved",
        }
        connection = sqlite3.connect(gateway_db_path)
        try:
            connection.execute(
                "UPDATE board_task_effect_deliveries SET next_attempt_at_us=0 "
                "WHERE effect_operation_id=? AND delivery_state='pending'",
                (history.operation_id,),
            )
            connection.commit()
        finally:
            connection.close()
        assert await _worker(store, bank).run_cycle() == 1
    finally:
        delegate.close()
    assert bank.calls == 1
    _assert_conserved(gateway_db_path, history)
