"""Backward-compatible Ledger import shim."""

from central_bank_service.services.in_memory_ledger_store import InMemoryLedgerStore

Ledger = InMemoryLedgerStore

__all__ = ["InMemoryLedgerStore", "Ledger"]
