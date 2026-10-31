"""T-072 coverage-sweep gap fixes for task creation, query, and cancellation.

Each test below closes a spec ID from ``docs/specifications/service-tests/
task-board-service-tests.md`` that the existing ``test_tasks.py`` suite does not
actually exercise (its ``TC-nn``/``TQ-nn``/``CAN-nn`` docstring numbering drifted
from the spec after ``TC-14`` was split into ``TC-14a/b/c``, so several later
labels cite the wrong spec behavior). These are new tests only -- no existing
test file is modified.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from service_auth.financial_identity import operation_id_for

from task_board_service.core.state import get_app_state
from tests.fakes.gateway_task_store import GatewayTaskStore
from tests.helpers import make_fake_jws, make_jws_token
from tests.unit.routers.conftest import (
    accept_bid,
    create_task,
    make_task_id,
    new_operation_fields,
    submit_bid,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestTaskCreationGaps:
    """Gaps in Category 1: Task Creation (POST /tasks)."""

    @pytest.mark.unit
    async def test_tc15_empty_title_rejected(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
    ) -> None:
        """TC-15: Empty title returns 400 invalid_payload."""
        resp = await create_task(client, alice_keypair, alice_agent_id, title="")
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"

    @pytest.mark.unit
    async def test_tc17_empty_spec_rejected(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
    ) -> None:
        """TC-17: Empty spec returns 400 invalid_payload."""
        resp = await create_task(client, alice_keypair, alice_agent_id, spec="")
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"

    @pytest.mark.unit
    async def test_tc17_spec_over_max_length_rejected(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
    ) -> None:
        """TC-17: Spec of 10,001 characters (one over the limit) returns 400 invalid_payload."""
        resp = await create_task(client, alice_keypair, alice_agent_id, spec="S" * 10001)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"

    @pytest.mark.unit
    async def test_tc21_unregistered_agent_signer_forbidden(
        self,
        client: AsyncClient,
    ) -> None:
        """TC-21: task_token signed by an agent unknown to Identity returns 403 forbidden.

        ``make_fake_jws`` uses a ``kid`` that was never registered via
        ``make_jws_token`` -- the router-test identity double treats an unknown
        ``kid`` exactly like the real Identity service would treat an
        unregistered public key.
        """
        unregistered_agent_id = "a-never-registered-00000000"
        task_id = make_task_id()
        task_payload = {
            "acceptance_window_seconds": 3600,
            "action": "create_task",
            "task_id": task_id,
            "poster_id": unregistered_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_fake_jws(task_payload, kid=unregistered_agent_id)
        escrow_payload = {
            "action": "escrow_lock",
            "operation_id": operation_id_for("task-lock", (task_id, unregistered_agent_id)),
            "task_id": task_id,
            "agent_id": unregistered_agent_id,
            "amount": 100,
        }
        escrow_token = make_fake_jws(escrow_payload, kid=unregistered_agent_id)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    @pytest.mark.unit
    async def test_tc28_native_task_id_conflict_has_no_financial_side_effects(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TC-28: Native admission rejects an existing task before any Bank call."""
        state = get_app_state()
        task_id = make_task_id()

        existing = await create_task(
            client, bob_keypair, bob_agent_id, task_id=task_id, title="Existing"
        )
        assert existing.status_code == 201, existing.text
        store = state.store
        assert isinstance(store, GatewayTaskStore)
        existing_task = store.reader.get_task(task_id)
        assert existing_task is not None
        existing_task_bytes = json.dumps(
            existing_task,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        balance_before = store.writer.connection.execute(
            "SELECT balance FROM bank_accounts WHERE account_id=?", (alice_agent_id,)
        ).fetchone()[0]
        escrow_before = store.writer.connection.execute(
            "SELECT payer_account_id,status FROM bank_escrow WHERE task_id=? "
            "ORDER BY payer_account_id",
            (task_id,),
        ).fetchall()
        assert [tuple(row) for row in escrow_before] == [(bob_agent_id, "locked")]
        assert state.central_bank_client is not None
        lock = state.central_bank_client.escrow_lock
        release = state.central_bank_client.escrow_release
        assert isinstance(lock, AsyncMock) and isinstance(release, AsyncMock)
        lock.reset_mock()
        release.reset_mock()
        monkeypatch.setattr(store, "get_task", MagicMock(return_value=None))
        database_before = tuple(store.writer.connection.iterdump())

        resp = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)

        assert resp.status_code == 409
        assert resp.json()["error"] == "task_id_conflict"
        lock.assert_not_awaited()
        release.assert_not_awaited()
        assert (
            store.writer.connection.execute(
                "SELECT balance FROM bank_accounts WHERE account_id=?", (alice_agent_id,)
            ).fetchone()[0]
            == balance_before
        )
        task_after = store.reader.get_task(task_id)
        assert task_after is not None
        task_after_bytes = json.dumps(
            task_after,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        assert task_after_bytes == existing_task_bytes
        assert tuple(store.writer.connection.iterdump()) == database_before
        escrows = store.writer.connection.execute(
            "SELECT payer_account_id,status FROM bank_escrow WHERE task_id=? "
            "ORDER BY payer_account_id",
            (task_id,),
        ).fetchall()
        assert [tuple(row) for row in escrows] == [(bob_agent_id, "locked")]


class TestTaskQueryGaps:
    """Gaps in Category 2: Task Queries (GET /tasks, GET /tasks/{task_id})."""

    @pytest.mark.unit
    async def test_tq04_sql_injection_returns_task_not_found(
        self,
        client: AsyncClient,
    ) -> None:
        """TQ-04: SQL injection in task ID path returns 404 with error=task_not_found."""
        resp = await client.get("/tasks/' OR '1'='1")
        assert resp.status_code == 404
        assert resp.json()["error"] == "task_not_found"

    @pytest.mark.unit
    async def test_tq09_filter_by_worker_id(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """TQ-09: Filter tasks by worker_id returns only the accepted task."""
        # task_1: bid accepted by Bob (worker_id = bob)
        task_id_1 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_1)
        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id_1)
        bid_id = bid_resp.json()["bid_id"]
        await accept_bid(client, alice_keypair, alice_agent_id, task_id_1, bid_id)

        # task_2: no bid accepted (worker_id = None)
        task_id_2 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_2)

        resp = await client.get(f"/tasks?worker_id={bob_agent_id}")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert len(tasks) >= 1
        for task in tasks:
            assert task["worker_id"] == bob_agent_id
        assert any(t["task_id"] == task_id_1 for t in tasks)
        assert not any(t["task_id"] == task_id_2 for t in tasks)

    @pytest.mark.unit
    async def test_tq10_combined_filters_and_logic(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """TQ-10: Combined poster_id + status filters apply AND logic."""
        task_id_1 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_1)

        # Bob's task stays open too -- must NOT show up under poster_id=alice.
        task_id_2 = make_task_id()
        await create_task(client, bob_keypair, bob_agent_id, task_id=task_id_2)

        # A second Alice task, cancelled -- must NOT show up under status=open.
        task_id_3 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_3)
        cancel_payload = {
            **new_operation_fields(task_id_3),
            "action": "cancel_task",
            "task_id": task_id_3,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(alice_keypair[0], alice_agent_id, cancel_payload)
        cancel_resp = await client.post(f"/tasks/{task_id_3}/cancel", json={"token": cancel_token})
        assert cancel_resp.status_code == 200

        resp = await client.get(f"/tasks?poster_id={alice_agent_id}&status=open")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        task_ids = {t["task_id"] for t in tasks}
        assert task_id_1 in task_ids
        assert task_id_2 not in task_ids
        assert task_id_3 not in task_ids

    @pytest.mark.unit
    async def test_tq11_unknown_filter_value_returns_empty_list(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
    ) -> None:
        """TQ-11: Unknown filter value returns 200 with an empty array, not an error."""
        await create_task(client, alice_keypair, alice_agent_id)

        resp = await client.get("/tasks?status=nonexistent")
        assert resp.status_code == 200
        assert resp.json()["tasks"] == []


class TestTaskCancellationGaps:
    """Gaps in Category 3: Task Cancellation (POST /tasks/{task_id}/cancel)."""

    @pytest.mark.unit
    async def test_can03_impersonation_signer_not_payload_poster(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """CAN-03: Bob signs claiming poster_id=alice -- returns 403 forbidden.

        Distinct from the plain non-poster case: here the payload itself claims
        a poster_id the signer does not hold, exercising the signer-vs-payload
        check (step 9a) rather than the signer-vs-task check (step 9b).
        """
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)

        cancel_payload = {
            **new_operation_fields(task_id),
            "action": "cancel_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(bob_keypair[0], bob_agent_id, cancel_payload)
        resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token})
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    @pytest.mark.unit
    async def test_can07_central_bank_unavailable_on_cancel_release(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
    ) -> None:
        """CAN-07: Central Bank unavailable during escrow release returns 502."""
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)

        state = get_app_state()
        state.central_bank_client.escrow_release = AsyncMock(
            side_effect=ConnectionError("Central Bank unreachable")
        )

        cancel_payload = {
            **new_operation_fields(task_id),
            "action": "cancel_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(alice_keypair[0], alice_agent_id, cancel_payload)
        resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token})
        assert resp.status_code == 502
        assert resp.json()["error"] == "central_bank_unavailable"
        store = state.store
        assert isinstance(store, GatewayTaskStore)
        row = store.writer.connection.execute(
            "SELECT o.command,o.operation_state,o.effect_state,t.status,e.status "
            "FROM board_task_operations AS o JOIN board_tasks AS t ON t.task_id=o.task_id "
            "JOIN bank_escrow AS e ON e.escrow_id=t.escrow_id WHERE o.operation_id=?",
            (cancel_payload["operation_id"],),
        ).fetchone()
        assert row is not None
        assert tuple(row) == ("cancel_task", "claimed", "pending", "open", "locked")

    @pytest.mark.unit
    async def test_can09_task_id_payload_must_match_url(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
    ) -> None:
        """CAN-09: task_id in payload must match URL path -- returns 400 invalid_payload."""
        task_id_1 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_1)
        task_id_2 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_2)

        cancel_payload = {
            **new_operation_fields(task_id_2),
            "action": "cancel_task",
            "task_id": task_id_2,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(alice_keypair[0], alice_agent_id, cancel_payload)
        resp = await client.post(f"/tasks/{task_id_1}/cancel", json={"token": cancel_token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"
