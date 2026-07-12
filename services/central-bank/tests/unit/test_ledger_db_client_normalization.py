"""LedgerDbClient response handling must not drop persisted bank columns.

GAP-E12 residue audit (see task-board's TaskDbClient._normalize_task /
commit 91b649b for the original finding). LedgerDbClient has no whitelist tuple
constant; each read method names its fields explicitly, which is the same risk
spelled differently — a schema column not named in the return dict is silently
unavailable to every caller with no failing test anywhere. This suite pins the
client's observable behavior against the canonical schema.

LedgerDbClient is async (GAP-E2 consolidation, WP-04 item 6), backed internally
by the shared GatewayClient; its underlying httpx.AsyncClient is reached via
GatewayClient's public `connection` property, never a private attribute.

Two documented, intentional exclusions in get_transactions:
- `account_id`: the caller already has it (it's the query parameter) — every
  existing call site would just be re-reading its own argument back.
- `event_id`: not a client-side drop at all. The gateway's own read query
  (DbReader.get_transactions) never SELECTs event_id, so it is unavailable in
  the HTTP response body before LedgerDbClient sees it. That is a gateway
  DbReader gap, not a *_db_client.py whitelist gap — out of this audit's scope
  (item 5 targets identity/central-bank/reputation/court's *_db_client.py
  files, not db-gateway's DbReader) and is called out separately in the WP-04
  report rather than fixed here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from central_bank_service.services.ledger_db_client import LedgerDbClient

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"

_NON_COLUMN_PREFIXES = ("FOREIGN KEY", "CHECK", "PRIMARY KEY", "UNIQUE")

# See module docstring.
_GET_TRANSACTIONS_OMITS = frozenset({"account_id", "event_id"})


def _schema_columns(table: str) -> set[str]:
    """Column names declared for `table` in the canonical schema."""
    schema = SCHEMA_PATH.read_text()
    match = re.search(rf"CREATE TABLE {table}\s*\((.*?)\n\);", schema, re.DOTALL)
    assert match is not None, f"{table} table not found in schema.sql"

    columns: set[str] = set()
    for raw_line in match.group(1).splitlines():
        line = raw_line.split("--")[0].strip()
        if not line or line.startswith(_NON_COLUMN_PREFIXES):
            continue
        name = line.split()[0].strip(",")
        if name.isidentifier():
            columns.add(name)
    return columns


def _mock_response(status_code: int, json_data: Any = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = ""
    if json_data is not None:
        response.json.return_value = json_data
    return response


@pytest.fixture
def client() -> LedgerDbClient:
    return LedgerDbClient(base_url="http://gateway.invalid", timeout_seconds=1)


@pytest.mark.unit
class TestBankAccountsSchemaShape:
    def test_bank_accounts_has_the_expected_columns(self) -> None:
        assert _schema_columns("bank_accounts") == {"account_id", "balance", "created_at"}


@pytest.mark.unit
class TestGetAccountCoversEveryColumn:
    """get_account is the full-record read; it must surface every bank_accounts column."""

    async def test_get_account_returns_every_schema_column(self, client: LedgerDbClient) -> None:
        row = {"account_id": "a-1", "balance": 100, "created_at": "2026-03-01T09:00:00Z"}
        client._gateway.connection.get = AsyncMock(return_value=_mock_response(200, row))

        result = await client.get_account("a-1")

        assert result is not None
        missing = _schema_columns("bank_accounts") - set(result)
        assert missing == set(), (
            f"bank_accounts columns absent from LedgerDbClient.get_account's "
            f"return dict: {sorted(missing)}."
        )

    async def test_get_account_drops_unknown_fields(self, client: LedgerDbClient) -> None:
        row = {
            "account_id": "a-1",
            "balance": 100,
            "created_at": "2026-03-01T09:00:00Z",
            "surprise_column": "leaked",
        }
        client._gateway.connection.get = AsyncMock(return_value=_mock_response(200, row))

        result = await client.get_account("a-1")

        assert result is not None
        assert "surprise_column" not in result


@pytest.mark.unit
class TestGetTransactionsCoversNonExcludedColumns:
    async def test_returns_every_non_excluded_schema_column(self, client: LedgerDbClient) -> None:
        row = {
            "tx_id": "tx-1",
            "account_id": "a-1",
            "type": "credit",
            "amount": 10,
            "balance_after": 10,
            "reference": "ref",
            "timestamp": "2026-03-01T09:00:00Z",
            # event_id intentionally absent — the gateway's DbReader.get_transactions
            # never SELECTs it (see module docstring); this is what the real
            # gateway response body looks like today.
        }
        account_row = {"account_id": "a-1", "balance": 10, "created_at": "2026-03-01T09:00:00Z"}

        def _get(path: str, **_kwargs: object) -> MagicMock:
            # get_transactions calls get_account first to check existence, so the
            # mock must answer both endpoints distinctly.
            if path.endswith("/transactions"):
                return _mock_response(200, {"transactions": [row]})
            return _mock_response(200, account_row)

        client._gateway.connection.get = AsyncMock(side_effect=_get)

        result = await client.get_transactions("a-1")

        assert len(result) == 1
        expected_fields = _schema_columns("bank_transactions") - _GET_TRANSACTIONS_OMITS
        missing = expected_fields - set(result[0])
        assert missing == set(), (
            f"bank_transactions columns absent from LedgerDbClient.get_transactions: "
            f"{sorted(missing)}."
        )
