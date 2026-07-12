"""GAP-C5 — DbReader wiring and the single-writer/no-await-in-transaction invariant.

The reader and writer intentionally share one SQLite connection (WAL mode does not
make SQLite safe for concurrent writers, and the gateway is single-process). What
must never happen is:

1. Lifespan wiring reaching into ``DbWriter``'s private ``_db`` attribute instead of
   a public accessor — that hides the sharing as an implementation accident instead
   of a documented contract.
2. A write method becoming ``async def`` and awaiting something between
   ``BEGIN IMMEDIATE`` and ``COMMIT``/``ROLLBACK`` — since the gateway runs every
   route as an ``async def`` coroutine on one event loop, an ``await`` mid-transaction
   would let another coroutine's write interleave with an open transaction on the
   same connection (measured 2026-07-10: cross-thread use already raises
   ``sqlite3.OperationalError: cannot start a transaction within a transaction``;
   cross-coroutine interleaving on one thread is the async analogue of that hazard).

Today every ``DbWriter`` write method is a plain (non-async) ``def`` that runs BEGIN
IMMEDIATE through COMMIT/ROLLBACK to completion without yielding to the event loop,
which is what makes the current single-connection design safe. A full ADR for this
lands in WP-12; this test is the cheap regression guard in the meantime.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from db_gateway_service.services.db_writer import DbWriter

_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent
_LIFESPAN_PATH = _SERVICE_ROOT / "src" / "db_gateway_service" / "core" / "lifespan.py"
_DB_WRITER_PATH = _SERVICE_ROOT / "src" / "db_gateway_service" / "services" / "db_writer.py"


@pytest.mark.architecture
class TestDbReaderWiring:
    """DbReader must be constructed through DbWriter's public connection accessor."""

    def test_db_writer_exposes_a_public_connection_accessor(self) -> None:
        """DbWriter.connection is the sanctioned way to obtain the shared connection."""
        assert hasattr(DbWriter, "connection")
        assert isinstance(DbWriter.__dict__["connection"], property)

    def test_lifespan_does_not_reach_into_the_private_db_attribute(self) -> None:
        """Startup wiring must not read `db_writer._db` — only the public accessor."""
        source = _LIFESPAN_PATH.read_text()

        assert "db_writer._db" not in source, (
            "lifespan.py reaches into DbWriter's private _db attribute; "
            "use the public DbWriter.connection accessor instead"
        )
        assert "db_writer.connection" in source, (
            "lifespan.py must wire DbReader through DbWriter.connection"
        )

    def test_connection_property_returns_the_writers_live_connection(
        self, tmp_db_path: str, schema_sql: str
    ) -> None:
        """The public accessor is not a copy — it is the same connection object."""
        writer = DbWriter(
            db_path=tmp_db_path,
            busy_timeout_ms=5000,
            journal_mode="wal",
            schema_sql=schema_sql,
        )
        try:
            assert writer.connection is writer._db
        finally:
            writer.close()


@pytest.mark.architecture
class TestNoAwaitInsideATransaction:
    """Cheap static regression guard for the single-writer/no-await-in-txn invariant."""

    def test_no_db_writer_method_is_a_coroutine_function(self) -> None:
        """Every write method must stay a plain `def` — converting one to `async def`
        is exactly the change that would let an `await` land between BEGIN IMMEDIATE
        and COMMIT/ROLLBACK.
        """
        offenders = [
            name
            for name, member in inspect.getmembers(DbWriter)
            if inspect.iscoroutinefunction(member)
        ]
        assert offenders == [], (
            f"DbWriter methods must not be async: {offenders}. "
            "An async write method could await between BEGIN IMMEDIATE and COMMIT, "
            "corrupting the single shared-connection transaction."
        )

    def test_db_writer_source_contains_no_await_expression(self) -> None:
        """AST-level guard: no `await` keyword anywhere in db_writer.py.

        This catches the hazard even inside a helper that isn't itself a DbWriter
        method (e.g. a module-level async helper called from within a transaction).
        """
        tree = ast.parse(_DB_WRITER_PATH.read_text(), filename=str(_DB_WRITER_PATH))
        awaits = [node for node in ast.walk(tree) if isinstance(node, ast.Await)]
        assert awaits == [], (
            "db_writer.py contains an `await` expression — this can interleave "
            "another coroutine's write with an open BEGIN IMMEDIATE transaction "
            "on the shared connection."
        )
