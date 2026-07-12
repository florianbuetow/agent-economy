"""Bank-store parity contract suite (GAP-A10, T-030/031/032).

`InMemoryLedgerStore` and `LedgerDbClient` are two interchangeable implementations
of the same ledger-store interface. Historically they drifted: different transaction
`type` vocabulary, prefixed vs bare `reference` values, a missing poster==payer guard,
a different error code for an out-of-range `worker_pct`, and zero-amount split legs
written by one store but not the other.

This ONE suite runs every scenario against BOTH implementations via a single
parametrized `store` fixture, so a future change to either store's observable
behavior fails here first instead of drifting silently again.

`LedgerDbClient`'s side is backed by a real, in-process DB Gateway ASGI app (a fresh
temp SQLite database per test, no network socket, no live-stack dependency) — the
gateway is the source of truth for what "correct" looks like; `InMemoryLedgerStore`
is asserted to match it exactly.

`LedgerDbClient` is async (GAP-E2 consolidation, WP-04 item 6); `InMemoryLedgerStore`
stays sync (it is never wired into production — see
tests/architecture/test_db_client_isolation.py's test_lifespan_does_not_import_legacy_stores,
and test_ledger_audit_log.py calls it synchronously). `_await_maybe` lets every test
body call `store.<method>(...)` once and work against either.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from db_gateway_service.app import create_app
from db_gateway_service.config import clear_settings_cache
from db_gateway_service.core.state import init_app_state, reset_app_state
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from httpx import ASGITransport, AsyncClient
from service_commons.exceptions import ServiceError

from central_bank_service.services.in_memory_ledger_store import InMemoryLedgerStore
from central_bank_service.services.ledger_db_client import LedgerDbClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"

LedgerStore = InMemoryLedgerStore | LedgerDbClient

pytestmark = pytest.mark.asyncio


async def _await_maybe(value: Any) -> Any:
    """Await `value` if it is awaitable (LedgerDbClient), else return it as-is
    (InMemoryLedgerStore) — the one seam that lets both stores share test bodies.
    """
    if inspect.isawaitable(value):
        return await value
    return value


def _write_gateway_config(db_path: str) -> str:
    """Write a temp db-gateway config.yaml pointing at an isolated SQLite file."""
    content = f"""
service:
  name: "db-gateway-contract-test"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 18028
  log_level: "warning"

logging:
  level: "WARNING"
  directory: "data/logs"
  format: "json"

database:
  path: "{db_path}"
  schema_path: "{_SCHEMA_PATH}"
  busy_timeout_ms: 5000
  journal_mode: "wal"

request:
  max_body_size: 1048576
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(content)
        return f.name


async def _gateway_backed_client(db_path: str) -> AsyncIterator[LedgerDbClient]:
    """Yield a LedgerDbClient wired to a fresh in-process DB Gateway ASGI app.

    Mirrors tests/integration/gateway_helpers.py's create_gateway_client: build
    DbWriter/DbReader directly against a temp SQLite file, hand the app an
    httpx.AsyncClient(transport=ASGITransport(...)) — true in-process async I/O,
    no live-stack dependency — and briefly point CONFIG_PATH at a throwaway
    gateway config only while create_app() reads its own settings.
    """
    config_path = _write_gateway_config(db_path)
    reset_app_state()
    state = init_app_state()
    writer = DbWriter(
        db_path=db_path,
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=_SCHEMA_PATH.read_text(),
    )
    state.db_writer = writer
    state.db_reader = DbReader(db=writer.connection)

    with patch.dict("os.environ", {"CONFIG_PATH": config_path}):
        clear_settings_cache()
        app = create_app()
    clear_settings_cache()

    client = LedgerDbClient(base_url="http://gateway-test", timeout_seconds=5)
    await client._gateway.close()
    client._gateway._client = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://gateway-test"
    )
    try:
        yield client
    finally:
        await client.close()
        writer.close()
        reset_app_state()
        Path(config_path).unlink(missing_ok=True)


@pytest.fixture(params=["in_memory", "gateway"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[LedgerStore]:
    """A fresh ledger store, either backend, isolated per test."""
    if request.param == "in_memory":
        in_memory_store = InMemoryLedgerStore(db_path=f"contract-{uuid4()}")
        yield in_memory_store
        in_memory_store.close()
    else:
        db_path = str(tmp_path / f"contract-{uuid4()}.db")
        async for client in _gateway_backed_client(db_path):
            yield client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fund(store: LedgerStore, account_id: str, balance: int) -> None:
    """Create a funded account, registering the agent with Identity first when the
    store is gateway-backed — `bank_accounts.account_id` has a FOREIGN KEY on
    `identity_agents.agent_id` in the real schema, a constraint InMemoryLedgerStore
    (a bank-domain-only fake) has no concept of.
    """
    if isinstance(store, LedgerDbClient):
        await _register_agent(store, account_id)
    await _await_maybe(store.create_account(account_id, balance))


async def _register_agent(client: LedgerDbClient, agent_id: str) -> None:
    response = await client._gateway.connection.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-03-01T09:00:00Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-03-01T09:00:00Z",
                "agent_id": agent_id,
                "summary": f"{agent_id} registered",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text


def _tx_by_reference(transactions: list[dict[str, Any]], reference: str) -> dict[str, Any]:
    matches = [tx for tx in transactions if tx["reference"] == reference]
    assert len(matches) == 1, (
        f"expected exactly one transaction with reference={reference!r}, "
        f"found {len(matches)}: {transactions}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# escrow_lock — transaction type and reference vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEscrowLockTransactionShape:
    async def test_writes_escrow_lock_type_with_bare_task_id_reference(
        self, store: LedgerStore
    ) -> None:
        task_id = f"t-{uuid4()}"
        await _fund(store, "a-payer", 100)

        await _await_maybe(store.escrow_lock("a-payer", 30, task_id))

        txs = await _await_maybe(store.get_transactions("a-payer"))
        tx = _tx_by_reference(txs, task_id)
        assert tx["type"] == "escrow_lock"
        assert tx["amount"] == 30
        assert tx["balance_after"] == 70


# ---------------------------------------------------------------------------
# escrow_release — transaction type and reference vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEscrowReleaseTransactionShape:
    async def test_writes_escrow_release_type_with_bare_escrow_id_reference(
        self, store: LedgerStore
    ) -> None:
        task_id = f"t-{uuid4()}"
        await _fund(store, "a-payer", 100)
        await _fund(store, "a-worker", 0)
        locked = await _await_maybe(store.escrow_lock("a-payer", 30, task_id))
        escrow_id = str(locked["escrow_id"])

        await _await_maybe(store.escrow_release(escrow_id, "a-worker"))

        txs = await _await_maybe(store.get_transactions("a-worker"))
        tx = _tx_by_reference(txs, escrow_id)
        assert tx["type"] == "escrow_release"
        assert tx["amount"] == 30
        assert tx["balance_after"] == 30


# ---------------------------------------------------------------------------
# escrow_split — transaction type, reference, poster==payer guard, worker_pct
# error code, and zero-amount leg skipping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEscrowSplitTransactionShape:
    async def test_both_legs_write_escrow_release_type_with_bare_escrow_id_reference(
        self, store: LedgerStore
    ) -> None:
        task_id = f"t-{uuid4()}"
        await _fund(store, "a-poster", 100)
        await _fund(store, "a-worker", 0)
        locked = await _await_maybe(store.escrow_lock("a-poster", 100, task_id))
        escrow_id = str(locked["escrow_id"])

        await _await_maybe(store.escrow_split(escrow_id, "a-worker", 40, "a-poster"))

        worker_txs = await _await_maybe(store.get_transactions("a-worker"))
        worker_tx = _tx_by_reference(worker_txs, escrow_id)
        assert worker_tx["type"] == "escrow_release"
        assert worker_tx["amount"] == 40

        # a-poster's history already has an escrow_lock row with the same
        # reference (the escrow_id doubles as both legs' and the lock's
        # reference on the payer's own account), so filter to the credit leg.
        poster_txs = await _await_maybe(store.get_transactions("a-poster"))
        poster_release_txs = [
            tx
            for tx in poster_txs
            if tx["reference"] == escrow_id and tx["type"] == "escrow_release"
        ]
        assert len(poster_release_txs) == 1
        assert poster_release_txs[0]["amount"] == 60

    async def test_zero_amount_worker_leg_is_not_written(self, store: LedgerStore) -> None:
        """worker_pct=0 means the entire escrow goes to the poster — no worker tx row.

        Counts transactions before/after rather than filtering by reference value,
        so this stays a pure zero-amount-skip check independent of exactly how the
        (non-zero-amount) reference is formatted.
        """
        task_id = f"t-{uuid4()}"
        await _fund(store, "a-poster", 100)
        await _fund(store, "a-worker", 0)
        locked = await _await_maybe(store.escrow_lock("a-poster", 100, task_id))
        escrow_id = str(locked["escrow_id"])
        txs_before = len(await _await_maybe(store.get_transactions("a-worker")))

        result = await _await_maybe(store.escrow_split(escrow_id, "a-worker", 0, "a-poster"))

        assert result["worker_amount"] == 0
        assert result["poster_amount"] == 100
        txs_after = await _await_maybe(store.get_transactions("a-worker"))
        assert len(txs_after) == txs_before, (
            "a zero-amount split leg must not write a transaction row"
        )

    async def test_zero_amount_poster_leg_is_not_written(self, store: LedgerStore) -> None:
        """worker_pct=100 means the entire escrow goes to the worker — no poster credit row."""
        task_id = f"t-{uuid4()}"
        await _fund(store, "a-poster", 100)
        await _fund(store, "a-worker", 0)
        locked = await _await_maybe(store.escrow_lock("a-poster", 100, task_id))
        escrow_id = str(locked["escrow_id"])
        txs_before = len(await _await_maybe(store.get_transactions("a-poster")))

        result = await _await_maybe(store.escrow_split(escrow_id, "a-worker", 100, "a-poster"))

        assert result["worker_amount"] == 100
        assert result["poster_amount"] == 0
        txs_after = await _await_maybe(store.get_transactions("a-poster"))
        assert len(txs_after) == txs_before, (
            "a zero-amount split leg must not write a transaction row"
        )

    async def test_rejects_poster_that_does_not_match_the_escrow_payer(
        self, store: LedgerStore
    ) -> None:
        task_id = f"t-{uuid4()}"
        await _fund(store, "a-poster", 100)
        await _fund(store, "a-worker", 0)
        await _fund(store, "a-imposter", 0)
        locked = await _await_maybe(store.escrow_lock("a-poster", 100, task_id))
        escrow_id = str(locked["escrow_id"])

        with pytest.raises(ServiceError) as excinfo:
            await _await_maybe(store.escrow_split(escrow_id, "a-worker", 40, "a-imposter"))

        assert excinfo.value.error == "payload_mismatch"
        assert excinfo.value.status_code == 400

    async def test_rejects_out_of_range_worker_pct_with_invalid_amount(
        self, store: LedgerStore
    ) -> None:
        task_id = f"t-{uuid4()}"
        await _fund(store, "a-poster", 100)
        await _fund(store, "a-worker", 0)
        locked = await _await_maybe(store.escrow_lock("a-poster", 100, task_id))
        escrow_id = str(locked["escrow_id"])

        with pytest.raises(ServiceError) as excinfo:
            await _await_maybe(store.escrow_split(escrow_id, "a-worker", 150, "a-poster"))

        assert excinfo.value.error == "invalid_amount"
        assert excinfo.value.status_code == 400
