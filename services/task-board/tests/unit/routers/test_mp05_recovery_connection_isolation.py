"""Posting recovery must not roll back a router-owned SQLite transaction."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from task_board_service.core.state import get_app_state
from task_board_service.services.posting_recovery import PostingRecovery
from tests.fakes.gateway_task_store import GatewayTaskStore
from tests.unit.routers.conftest import create_task

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = pytest.mark.unit


async def test_recovery_sqlite_error_does_not_rollback_router_owner_transaction(
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
) -> None:
    state = get_app_state()
    request_store = state.store
    assert isinstance(request_store, GatewayTaskStore)
    recovery = state.posting_recovery
    assert isinstance(recovery, PostingRecovery)
    recovery_task = state.posting_recovery_task
    assert recovery_task is not None
    recovery_task.cancel()
    with suppress(asyncio.CancelledError):
        await recovery_task

    central_bank = state.central_bank_client
    assert central_bank is not None
    central_bank.escrow_lock = AsyncMock(return_value={"unproven_bank_body": True})
    response = await create_task(
        client,
        alice_keypair,
        alice_agent_id,
        reward=100,
    )
    assert response.status_code == 202
    publication = response.json()
    assert publication["publication_state"] == "awaiting_lock"
    assert publication["result"] is None

    stored_before = request_store.get_task_publication(publication["operation_id"], alice_agent_id)
    assert stored_before.body == publication

    recovery_store = cast("GatewayTaskStore", recovery._store)
    recovery_connection = recovery_store.writer.connection
    request_connection = request_store.writer.connection
    recovery_connection.execute("PRAGMA busy_timeout=1")
    bank_rows_before = tuple(
        line for line in request_connection.iterdump() if line.startswith('INSERT INTO "bank_')
    )

    request_connection.execute("BEGIN IMMEDIATE")
    assert request_connection.in_transaction
    try:
        with pytest.raises(sqlite3.OperationalError):
            await recovery.run_batch()

        assert request_connection.in_transaction
        assert (
            tuple(
                line
                for line in request_connection.iterdump()
                if line.startswith('INSERT INTO "bank_')
            )
            == bank_rows_before
        )
    finally:
        if request_connection.in_transaction:
            request_connection.rollback()

    stored_after = request_store.get_task_publication(publication["operation_id"], alice_agent_id)
    assert stored_after.body == publication
