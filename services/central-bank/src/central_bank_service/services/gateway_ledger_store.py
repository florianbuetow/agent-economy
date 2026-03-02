"""Backward-compatibility shim for legacy GatewayLedgerStore imports."""

from central_bank_service.services.ledger_db_client import LedgerDbClient as GatewayLedgerStore

__all__ = ["GatewayLedgerStore"]
