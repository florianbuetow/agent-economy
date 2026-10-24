"""MP-08 fairness and fencing through the real shared Gateway queue."""

from __future__ import annotations

import asyncio
import importlib
import sqlite3
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest
from db_gateway_service import task_time
from db_gateway_service.task_time import utc_now_us
from service_commons.exceptions import ServiceError

from tests.fakes.board_mp08_http_history import (
    RecoveryHistory,
    make_task_store,
    persist_effect,
    prepare_recovery_history,
)

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_WORKER_A = "recovery-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_WORKER_B = "recovery-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class _Recovery(Protocol):
    async def run_cycle(self) -> int: ...


class _Gateway(Protocol):
    base_url: str


def _worker(store: object, bank: object, worker_id: str, *, limit: int) -> _Recovery:
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
            worker_id=worker_id,
            max_delivery_attempts_per_cycle=limit,
        ),
    )


class _SelectiveBank:
    def __init__(
        self,
        client: httpx.AsyncClient,
        histories: dict[str, RecoveryHistory],
        failing_operation_id: str,
    ) -> None:
        self.client = client
        self.histories = histories
        self.failing_operation_id = failing_operation_id
        self.calls: list[str] = []

    async def escrow_release(self, **terms: object) -> dict[str, object]:
        operation_id = terms["operation_id"]
        assert isinstance(operation_id, str)
        self.calls.append(operation_id)
        if operation_id == self.failing_operation_id:
            raise RuntimeError("persistent first-item failure")
        await persist_effect(self.client, self.histories[operation_id])
        return {"untrusted_ack": True}

    async def escrow_split(self, **_terms: object) -> dict[str, object]:
        raise AssertionError("fairness histories are releases")


class _NoBank:
    async def escrow_release(self, **_terms: object) -> dict[str, object]:
        raise AssertionError("receipt-first recovery dispatched Bank")

    async def escrow_split(self, **_terms: object) -> dict[str, object]:
        raise AssertionError("receipt-first recovery dispatched Bank")


async def test_real_persistent_first_failure_does_not_starve_later_due_row(
    _gateway: _Gateway,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> None:
    histories = [
        await prepare_recovery_history(gateway_client, "approve_task"),
        await prepare_recovery_history(gateway_client, "approve_task"),
    ]
    connection = sqlite3.connect(f"file:{gateway_db_path}?mode=ro", uri=True)
    try:
        ordered_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT effect_operation_id FROM board_task_effect_deliveries "
                "WHERE effect_operation_id IN (?,?) "
                "ORDER BY next_attempt_at_us,effect_operation_id",
                (histories[0].operation_id, histories[1].operation_id),
            ).fetchall()
        ]
    finally:
        connection.close()
    by_operation = {item.operation_id: item for item in histories}
    ordered = [by_operation[operation_id] for operation_id in ordered_ids]
    bank = _SelectiveBank(gateway_client, by_operation, ordered[0].operation_id)
    store = make_task_store(_gateway.base_url, gateway_db_path)
    try:
        assert await _worker(store, bank, _WORKER_A, limit=2).run_cycle() == 2
    finally:
        store.close()

    connection = sqlite3.connect(f"file:{gateway_db_path}?mode=ro", uri=True)
    try:
        rows = dict(
            connection.execute(
                "SELECT effect_operation_id,delivery_state "
                "FROM board_task_effect_deliveries WHERE effect_operation_id IN (?,?)",
                (ordered[0].operation_id, ordered[1].operation_id),
            ).fetchall()
        )
        assert rows[ordered[0].operation_id] in {"pending", "dead_letter"}
        assert rows[ordered[1].operation_id] == "completed"
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
            (ordered[0].operation_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
            (ordered[1].operation_id,),
        ).fetchone() == (1,)
    finally:
        connection.close()
    assert bank.calls == [ordered[0].operation_id, ordered[1].operation_id]


async def test_two_real_workers_contend_for_one_generation_and_stale_report_cannot_regress(
    _gateway: _Gateway,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> None:
    history = await prepare_recovery_history(gateway_client, "approve_task")
    entered = asyncio.Event()
    release = asyncio.Event()

    class GatedBank:
        def __init__(self) -> None:
            self.calls = 0

        async def escrow_release(self, **terms: object) -> dict[str, object]:
            assert terms["operation_id"] == history.operation_id
            self.calls += 1
            entered.set()
            await release.wait()
            await persist_effect(gateway_client, history)
            return {"untrusted_ack": True}

        async def escrow_split(self, **_terms: object) -> dict[str, object]:
            raise AssertionError("contended history is a release")

    bank = GatedBank()
    store_a = make_task_store(_gateway.base_url, gateway_db_path)
    store_b = make_task_store(_gateway.base_url, gateway_db_path)
    task_a = asyncio.create_task(_worker(store_a, bank, _WORKER_A, limit=1).run_cycle())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert await _worker(store_b, bank, _WORKER_B, limit=1).run_cycle() == 0
        connection = sqlite3.connect(f"file:{gateway_db_path}?mode=ro", uri=True)
        try:
            generation = int(
                connection.execute(
                    "SELECT lease_generation FROM board_task_effect_deliveries "
                    "WHERE effect_operation_id=?",
                    (history.operation_id,),
                ).fetchone()[0]
            )
        finally:
            connection.close()
        release.set()
        assert await asyncio.wait_for(task_a, timeout=2) == 1
        with pytest.raises(ServiceError) as raised:
            cast("Any", store_a).fail_task_effect_delivery(
                history.operation_id,
                generation,
                _WORKER_A,
                "central_bank_unavailable",
            )
        assert raised.value.status_code == 409
        assert raised.value.error == "effect_delivery_attempt_stale"
    finally:
        release.set()
        if not task_a.done():
            task_a.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task_a
        store_a.close()
        store_b.close()
    assert bank.calls == 1


async def test_expired_attempt_n_dead_letters_at_expiry_and_continues_to_later_row(
    _gateway: _Gateway,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    histories = [
        await prepare_recovery_history(gateway_client, "approve_task"),
        await prepare_recovery_history(gateway_client, "approve_task"),
    ]
    ordered = sorted(histories, key=lambda item: item.operation_id.encode())
    fixed_now = utc_now_us()
    monkeypatch.setattr(task_time, "utc_now_us", lambda: fixed_now)
    connection = sqlite3.connect(gateway_db_path)
    try:
        enqueued = int(
            connection.execute(
                "SELECT enqueued_at_us FROM board_task_effect_deliveries "
                "WHERE effect_operation_id=?",
                (ordered[0].operation_id,),
            ).fetchone()[0]
        )
        expiry = fixed_now
        connection.execute(
            "UPDATE board_task_effect_deliveries SET delivery_state='leased',"
            "total_attempt_count=8,cycle_attempt_count=8,lease_generation=8,"
            "next_attempt_at_us=NULL,lease_owner_id=?,lease_acquired_at_us=?,"
            "lease_expires_at_us=? WHERE effect_operation_id=?",
            (_WORKER_A, enqueued, expiry, ordered[0].operation_id),
        )
        connection.execute(
            "UPDATE board_task_effect_deliveries SET next_attempt_at_us=? "
            "WHERE effect_operation_id=?",
            (fixed_now, ordered[1].operation_id),
        )
        connection.commit()
    finally:
        connection.close()
    bank = _SelectiveBank(
        gateway_client,
        {item.operation_id: item for item in histories},
        failing_operation_id="op-ffffffff-ffff-4fff-8fff-ffffffffffff",
    )
    store = make_task_store(_gateway.base_url, gateway_db_path)
    try:
        assert await _worker(store, bank, _WORKER_B, limit=1).run_cycle() == 1
    finally:
        store.close()
    connection = sqlite3.connect(f"file:{gateway_db_path}?mode=ro", uri=True)
    try:
        assert connection.execute(
            "SELECT delivery_state,last_failure_code,last_failure_at_us "
            "FROM board_task_effect_deliveries WHERE effect_operation_id=?",
            (ordered[0].operation_id,),
        ).fetchone() == ("dead_letter", "lease_expired", expiry)
        assert connection.execute(
            "SELECT delivery_state FROM board_task_effect_deliveries WHERE effect_operation_id=?",
            (ordered[1].operation_id,),
        ).fetchone() == ("completed",)
    finally:
        connection.close()


async def test_due_dead_letter_adopts_late_receipt_without_bank_dispatch(
    _gateway: _Gateway,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
) -> None:
    history = await prepare_recovery_history(gateway_client, "approve_task")
    connection = sqlite3.connect(gateway_db_path)
    try:
        connection.execute(
            "UPDATE board_task_effect_deliveries SET delivery_state='dead_letter',"
            "next_attempt_at_us=NULL,next_evidence_check_at_us=?,"
            "last_failure_code='central_bank_unavailable',last_failure_at_us=? "
            "WHERE effect_operation_id=?",
            (utc_now_us() - 1, utc_now_us() - 2, history.operation_id),
        )
        connection.commit()
    finally:
        connection.close()
    await persist_effect(gateway_client, history)
    store = make_task_store(_gateway.base_url, gateway_db_path)
    try:
        assert await _worker(store, _NoBank(), _WORKER_A, limit=1).run_cycle() == 0
    finally:
        store.close()
    connection = sqlite3.connect(f"file:{gateway_db_path}?mode=ro", uri=True)
    try:
        assert connection.execute(
            "SELECT d.delivery_state,r.operation_id "
            "FROM board_task_effect_deliveries AS d "
            "JOIN bank_operation_receipts AS r ON r.operation_id=d.effect_operation_id "
            "WHERE d.effect_operation_id=?",
            (history.operation_id,),
        ).fetchone() == ("completed", history.operation_id)
    finally:
        connection.close()
