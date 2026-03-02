"""In-memory ledger storage used for local tests and fallback mode."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any, ClassVar

from service_commons.exceptions import ServiceError


@dataclass
class _DatabaseState:
    lock: RLock = field(default_factory=RLock)
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    transactions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    credit_refs: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    escrows: dict[str, dict[str, Any]] = field(default_factory=dict)
    locked_escrow_by_task: dict[tuple[str, str], str] = field(default_factory=dict)


class InMemoryLedgerStore:
    """Thread-safe in-memory ledger with deterministic idempotency checks."""

    _DATABASES: ClassVar[dict[str, _DatabaseState]] = {}
    _DATABASES_LOCK: ClassVar[RLock] = RLock()

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        with self._DATABASES_LOCK:
            if db_path not in self._DATABASES:
                self._DATABASES[db_path] = _DatabaseState()
            self._state = self._DATABASES[db_path]

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _new_tx_id(self) -> str:
        return f"tx-{uuid.uuid4()}"

    def _new_escrow_id(self) -> str:
        return f"esc-{uuid.uuid4()}"

    def _append_tx(self, account_id: str, tx: dict[str, Any]) -> None:
        self._state.transactions.setdefault(account_id, []).append(tx)

    def create_account(self, account_id: str, initial_balance: int) -> dict[str, object]:
        if initial_balance < 0:
            raise ServiceError(
                "invalid_amount",
                "Initial balance must be non-negative",
                400,
                {},
            )

        with self._state.lock:
            if account_id in self._state.accounts:
                raise ServiceError(
                    "account_exists",
                    "Account already exists for this agent",
                    409,
                    {},
                )

            created_at = self._now()
            self._state.accounts[account_id] = {
                "account_id": account_id,
                "balance": initial_balance,
                "created_at": created_at,
            }
            self._state.transactions.setdefault(account_id, [])

            if initial_balance > 0:
                tx = {
                    "tx_id": self._new_tx_id(),
                    "type": "credit",
                    "amount": initial_balance,
                    "balance_after": initial_balance,
                    "reference": "initial_balance",
                    "timestamp": created_at,
                }
                self._append_tx(account_id, tx)

            return {
                "account_id": account_id,
                "balance": initial_balance,
                "created_at": created_at,
            }

    def get_account(self, account_id: str) -> dict[str, object] | None:
        with self._state.lock:
            account = self._state.accounts.get(account_id)
            if account is None:
                return None
            return {
                "account_id": str(account["account_id"]),
                "balance": int(account["balance"]),
                "created_at": str(account["created_at"]),
            }

    def credit(self, account_id: str, amount: int, reference: str) -> dict[str, object]:
        if amount <= 0:
            raise ServiceError("invalid_amount", "Amount must be a positive integer", 400, {})

        with self._state.lock:
            account = self._state.accounts.get(account_id)
            if account is None:
                raise ServiceError("account_not_found", "Account not found", 404, {})

            key = (account_id, reference)
            existing = self._state.credit_refs.get(key)
            if existing is not None:
                if int(existing["amount"]) != amount:
                    raise ServiceError(
                        "payload_mismatch",
                        "Duplicate credit reference used with a different amount",
                        400,
                        {},
                    )
                return {
                    "tx_id": str(existing["tx_id"]),
                    "balance_after": int(existing["balance_after"]),
                }

            new_balance = int(account["balance"]) + amount
            account["balance"] = new_balance
            tx = {
                "tx_id": self._new_tx_id(),
                "type": "credit",
                "amount": amount,
                "balance_after": new_balance,
                "reference": reference,
                "timestamp": self._now(),
            }
            self._append_tx(account_id, tx)
            self._state.credit_refs[key] = tx
            return {"tx_id": str(tx["tx_id"]), "balance_after": new_balance}

    def get_transactions(self, account_id: str) -> list[dict[str, object]]:
        with self._state.lock:
            if account_id not in self._state.accounts:
                raise ServiceError("account_not_found", "Account not found", 404, {})
            items = self._state.transactions.get(account_id, [])
            return [dict(item) for item in items]

    def escrow_lock(self, payer_account_id: str, amount: int, task_id: str) -> dict[str, object]:
        if amount <= 0:
            raise ServiceError("invalid_amount", "Amount must be a positive integer", 400, {})

        with self._state.lock:
            payer = self._state.accounts.get(payer_account_id)
            if payer is None:
                raise ServiceError("account_not_found", "Account not found", 404, {})

            key = (payer_account_id, task_id)
            existing_escrow_id = self._state.locked_escrow_by_task.get(key)
            if existing_escrow_id is not None:
                existing = self._state.escrows[existing_escrow_id]
                if int(existing["amount"]) != amount:
                    raise ServiceError(
                        "escrow_already_locked",
                        "Escrow already locked for this task",
                        409,
                        {},
                    )
                return {
                    "escrow_id": str(existing["escrow_id"]),
                    "amount": int(existing["amount"]),
                    "task_id": str(existing["task_id"]),
                    "status": str(existing["status"]),
                }

            current_balance = int(payer["balance"])
            if current_balance < amount:
                raise ServiceError("insufficient_funds", "Insufficient funds", 402, {})

            payer["balance"] = current_balance - amount
            escrow_id = self._new_escrow_id()
            escrow = {
                "escrow_id": escrow_id,
                "payer_account_id": payer_account_id,
                "amount": amount,
                "task_id": task_id,
                "status": "locked",
                "created_at": self._now(),
                "resolved_at": None,
            }
            self._state.escrows[escrow_id] = escrow
            self._state.locked_escrow_by_task[key] = escrow_id

            debit_tx = {
                "tx_id": self._new_tx_id(),
                "type": "debit",
                "amount": amount,
                "balance_after": int(payer["balance"]),
                "reference": f"escrow_lock:{task_id}",
                "timestamp": self._now(),
            }
            self._append_tx(payer_account_id, debit_tx)

            return {
                "escrow_id": escrow_id,
                "amount": amount,
                "task_id": task_id,
                "status": "locked",
            }

    def escrow_release(self, escrow_id: str, recipient_account_id: str) -> dict[str, object]:
        with self._state.lock:
            escrow = self._state.escrows.get(escrow_id)
            if escrow is None:
                raise ServiceError("escrow_not_found", "Escrow not found", 404, {})

            recipient = self._state.accounts.get(recipient_account_id)
            if recipient is None:
                raise ServiceError("account_not_found", "Recipient account not found", 404, {})

            if str(escrow["status"]) != "locked":
                raise ServiceError(
                    "escrow_already_resolved",
                    "Escrow has already been resolved",
                    409,
                    {},
                )

            amount = int(escrow["amount"])
            new_balance = int(recipient["balance"]) + amount
            recipient["balance"] = new_balance

            tx = {
                "tx_id": self._new_tx_id(),
                "type": "credit",
                "amount": amount,
                "balance_after": new_balance,
                "reference": f"escrow_release:{escrow_id}",
                "timestamp": self._now(),
            }
            self._append_tx(recipient_account_id, tx)

            escrow["status"] = "released"
            escrow["resolved_at"] = self._now()
            self._state.locked_escrow_by_task.pop(
                (str(escrow["payer_account_id"]), str(escrow["task_id"])),
                None,
            )

            return {
                "escrow_id": escrow_id,
                "status": "released",
                "amount": amount,
                "recipient": recipient_account_id,
            }

    def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
    ) -> dict[str, object]:
        if worker_pct < 0 or worker_pct > 100:
            raise ServiceError("invalid_payload", "worker_pct must be 0-100", 400, {})

        with self._state.lock:
            escrow = self._state.escrows.get(escrow_id)
            if escrow is None:
                raise ServiceError("escrow_not_found", "Escrow not found", 404, {})

            if str(escrow["status"]) != "locked":
                raise ServiceError(
                    "escrow_already_resolved",
                    "Escrow has already been resolved",
                    409,
                    {},
                )

            worker = self._state.accounts.get(worker_account_id)
            poster = self._state.accounts.get(poster_account_id)
            if worker is None or poster is None:
                raise ServiceError("account_not_found", "Account not found", 404, {})

            amount = int(escrow["amount"])
            worker_amount = amount * worker_pct // 100
            poster_amount = amount - worker_amount

            worker_new_balance = int(worker["balance"]) + worker_amount
            poster_new_balance = int(poster["balance"]) + poster_amount
            worker["balance"] = worker_new_balance
            poster["balance"] = poster_new_balance

            worker_tx = {
                "tx_id": self._new_tx_id(),
                "type": "credit",
                "amount": worker_amount,
                "balance_after": worker_new_balance,
                "reference": f"escrow_split_worker:{escrow_id}",
                "timestamp": self._now(),
            }
            poster_tx = {
                "tx_id": self._new_tx_id(),
                "type": "credit",
                "amount": poster_amount,
                "balance_after": poster_new_balance,
                "reference": f"escrow_split_poster:{escrow_id}",
                "timestamp": self._now(),
            }
            self._append_tx(worker_account_id, worker_tx)
            self._append_tx(poster_account_id, poster_tx)

            escrow["status"] = "split"
            escrow["resolved_at"] = self._now()
            self._state.locked_escrow_by_task.pop(
                (str(escrow["payer_account_id"]), str(escrow["task_id"])),
                None,
            )

            return {
                "escrow_id": escrow_id,
                "status": "split",
                "worker_amount": worker_amount,
                "poster_amount": poster_amount,
                "worker_pct": worker_pct,
            }

    def count_accounts(self) -> int:
        with self._state.lock:
            return len(self._state.accounts)

    def total_escrowed(self) -> int:
        with self._state.lock:
            total = 0
            for escrow in self._state.escrows.values():
                if str(escrow["status"]) == "locked":
                    total += int(escrow["amount"])
            return total

    def close(self) -> None:
        """No-op close for compatibility with previous store API."""
