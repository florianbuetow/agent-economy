"""Thread-safe in-memory ledger used by focused storage tests."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any, ClassVar

from service_auth.financial_responses import validate_operation_id
from service_commons.exceptions import ServiceError

from central_bank_service.logging import get_logger
from central_bank_service.monetary import (
    checked_balance_add,
    require_nonnegative_money,
    require_positive_money,
    require_worker_percentage,
)

_logger = get_logger(__name__)


@dataclass
class _StoredReceipt:
    operation_kind: str
    request_sha256: str
    receipt: dict[str, object]


@dataclass
class _DatabaseState:
    lock: RLock = field(default_factory=RLock)
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    transactions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    credit_owners: dict[tuple[str, str], str] = field(default_factory=dict)
    escrows: dict[str, dict[str, Any]] = field(default_factory=dict)
    escrow_owners_by_task: dict[tuple[str, str], str] = field(default_factory=dict)
    settlement_owners: dict[str, str] = field(default_factory=dict)
    receipts: dict[str, _StoredReceipt] = field(default_factory=dict)
    next_event_id: int = 1


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ServiceError(
            "invalid_financial_request",
            f"{field_name} must be a nonempty string",
            400,
            {},
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ServiceError(
            "invalid_financial_request",
            f"{field_name} must contain valid UTF-8 text",
            400,
            {},
        ) from exc
    return value


def _require_operation_id(value: object) -> str:
    try:
        return validate_operation_id(value)
    except ValueError as exc:
        raise ServiceError("invalid_operation_id", str(exc), 400, {}) from exc


def _request_hash(operation_kind: str, fields: dict[str, object]) -> str:
    payload = {"request_version": 1, "operation_kind": operation_kind, **fields}
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class InMemoryLedgerStore:
    """Keep the Gateway receipt contract available to isolated storage tests."""

    _DATABASES: ClassVar[dict[str, _DatabaseState]] = {}
    _DATABASES_LOCK: ClassVar[RLock] = RLock()

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        with self._DATABASES_LOCK:
            if db_path not in self._DATABASES:
                self._DATABASES[db_path] = _DatabaseState()
            self._state = self._DATABASES[db_path]

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _new_tx_id() -> str:
        return f"tx-{uuid.uuid4()}"

    @staticmethod
    def _new_escrow_id() -> str:
        return f"esc-{uuid.uuid4()}"

    def _append_tx(self, account_id: str, tx: dict[str, Any]) -> None:
        self._state.transactions.setdefault(account_id, []).append(tx)

    def _projection(self, escrow_id: str) -> dict[str, object]:
        escrow = self._state.escrows[escrow_id]
        return {
            "escrow_id": str(escrow["escrow_id"]),
            "payer_account_id": str(escrow["payer_account_id"]),
            "task_id": str(escrow["task_id"]),
            "amount": int(escrow["amount"]),
            "status": str(escrow["status"]),
            "created_at": str(escrow["created_at"]),
            "resolved_at": (None if escrow["resolved_at"] is None else str(escrow["resolved_at"])),
        }

    def _envelope(self, receipt: dict[str, object]) -> dict[str, object]:
        escrow_id = receipt["escrow_id"]
        current_escrow = None if escrow_id is None else self._projection(str(escrow_id))
        return copy.deepcopy(
            {
                "outcome": "native_receipt",
                "receipt": receipt,
                "current_escrow": current_escrow,
            }
        )

    def _replay(
        self,
        operation_id: str,
        operation_kind: str,
        request_sha256: str,
    ) -> dict[str, object] | None:
        stored = self._state.receipts.get(operation_id)
        if stored is None:
            return None
        if stored.operation_kind != operation_kind or stored.request_sha256 != request_sha256:
            raise ServiceError(
                "operation_id_conflict",
                "Operation ID is already bound to a different financial command",
                409,
                {"winning_operation_id": operation_id},
            )
        return self._envelope(stored.receipt)

    def _record_receipt(
        self,
        operation_id: str,
        operation_kind: str,
        request_sha256: str,
        source_reference: str,
        committed_at: str,
        escrow_id: str | None,
        result: dict[str, object],
        legs: list[dict[str, object]],
    ) -> dict[str, object]:
        event_id = self._state.next_event_id
        self._state.next_event_id += 1
        receipt: dict[str, object] = {
            "receipt_version": 1,
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            "request_sha256": request_sha256,
            "source_reference": source_reference,
            "committed_at": committed_at,
            "event_id": event_id,
            "escrow_id": escrow_id,
            "result": result,
            "legs": legs,
        }
        self._state.receipts[operation_id] = _StoredReceipt(
            operation_kind=operation_kind,
            request_sha256=request_sha256,
            receipt=copy.deepcopy(receipt),
        )
        return self._envelope(receipt)

    @staticmethod
    def _ownership_conflict(winning_operation_id: str) -> ServiceError:
        return ServiceError(
            "financial_operation_already_committed",
            "A different operation already owns this financial effect",
            409,
            {"winning_operation_id": winning_operation_id},
        )

    def create_account(
        self,
        account_id: str,
        initial_balance: int,
        operation_id: str | None,
    ) -> dict[str, object]:
        account_id = _require_text(account_id, "account_id")
        initial_balance = require_nonnegative_money(initial_balance, "initial_balance")
        if initial_balance == 0:
            if operation_id is not None:
                raise ServiceError(
                    "invalid_operation_id",
                    "Zero-balance account creation must not carry operation_id",
                    400,
                    {},
                )
        else:
            operation_id = _require_operation_id(operation_id)

        fields: dict[str, object] = {
            "account_id": account_id,
            "amount": initial_balance,
            "reference": "initial_balance",
        }
        request_sha256 = _request_hash("account_issuance", fields)
        with self._state.lock:
            if operation_id is not None:
                replay = self._replay(operation_id, "account_issuance", request_sha256)
                if replay is not None:
                    return replay
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
            if initial_balance == 0:
                _logger.info(
                    "audit",
                    extra={
                        "operation": "create_account",
                        "account_id": account_id,
                        "initial_balance": initial_balance,
                        "reference": "initial_balance",
                    },
                )
                return {
                    "account_id": account_id,
                    "balance": initial_balance,
                    "created_at": created_at,
                }

            if operation_id is None:
                raise RuntimeError("Validated positive issuance lost its operation identity")
            tx_id = self._new_tx_id()
            self._append_tx(
                account_id,
                {
                    "tx_id": tx_id,
                    "type": "credit",
                    "amount": initial_balance,
                    "balance_after": initial_balance,
                    "reference": "initial_balance",
                    "timestamp": created_at,
                },
            )
            envelope = self._record_receipt(
                operation_id,
                "account_issuance",
                request_sha256,
                "initial_balance",
                created_at,
                None,
                {
                    "account_id": account_id,
                    "issued_amount": initial_balance,
                    "balance_after": initial_balance,
                },
                [
                    {
                        "role": "account",
                        "account_id": account_id,
                        "direction": "credit",
                        "amount": initial_balance,
                        "transaction_id": tx_id,
                        "balance_after": initial_balance,
                    }
                ],
            )
            _logger.info(
                "audit",
                extra={
                    "operation": "create_account",
                    "account_id": account_id,
                    "initial_balance": initial_balance,
                    "reference": "initial_balance",
                    "operation_id": operation_id,
                    "tx_id": tx_id,
                },
            )
            return envelope

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

    def credit(
        self,
        account_id: str,
        amount: int,
        reference: str,
        operation_id: str,
    ) -> dict[str, object]:
        account_id = _require_text(account_id, "account_id")
        amount = require_positive_money(amount, "amount")
        reference = _require_text(reference, "reference")
        operation_id = _require_operation_id(operation_id)
        fields: dict[str, object] = {
            "account_id": account_id,
            "amount": amount,
            "reference": reference,
        }
        request_sha256 = _request_hash("credit", fields)

        with self._state.lock:
            replay = self._replay(operation_id, "credit", request_sha256)
            if replay is not None:
                return replay
            winning_operation_id = self._state.credit_owners.get((account_id, reference))
            if winning_operation_id is not None:
                raise self._ownership_conflict(winning_operation_id)
            account = self._state.accounts.get(account_id)
            if account is None:
                raise ServiceError("account_not_found", "Account not found", 404, {})

            new_balance = checked_balance_add(int(account["balance"]), amount)
            committed_at = self._now()
            tx_id = self._new_tx_id()
            account["balance"] = new_balance
            self._append_tx(
                account_id,
                {
                    "tx_id": tx_id,
                    "type": "credit",
                    "amount": amount,
                    "balance_after": new_balance,
                    "reference": reference,
                    "timestamp": committed_at,
                },
            )
            self._state.credit_owners[(account_id, reference)] = operation_id
            envelope = self._record_receipt(
                operation_id,
                "credit",
                request_sha256,
                reference,
                committed_at,
                None,
                {
                    "account_id": account_id,
                    "amount": amount,
                    "balance_after": new_balance,
                    "reference": reference,
                },
                [
                    {
                        "role": "account",
                        "account_id": account_id,
                        "direction": "credit",
                        "amount": amount,
                        "transaction_id": tx_id,
                        "balance_after": new_balance,
                    }
                ],
            )
            _logger.info(
                "audit",
                extra={
                    "operation": "credit",
                    "account_id": account_id,
                    "amount": amount,
                    "reference": reference,
                    "operation_id": operation_id,
                    "tx_id": tx_id,
                },
            )
            return envelope

    def get_transactions(self, account_id: str) -> list[dict[str, object]]:
        with self._state.lock:
            if account_id not in self._state.accounts:
                raise ServiceError("account_not_found", "Account not found", 404, {})
            return copy.deepcopy(self._state.transactions.get(account_id, []))

    def escrow_lock(
        self,
        payer_account_id: str,
        amount: int,
        task_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        payer_account_id = _require_text(payer_account_id, "payer_account_id")
        amount = require_positive_money(amount, "amount")
        task_id = _require_text(task_id, "task_id")
        operation_id = _require_operation_id(operation_id)
        fields: dict[str, object] = {
            "payer_account_id": payer_account_id,
            "task_id": task_id,
            "amount": amount,
        }
        request_sha256 = _request_hash("escrow_lock", fields)

        with self._state.lock:
            replay = self._replay(operation_id, "escrow_lock", request_sha256)
            if replay is not None:
                return replay
            key = (payer_account_id, task_id)
            existing_escrow_id = self._state.escrow_owners_by_task.get(key)
            if existing_escrow_id is not None:
                winner = str(self._state.escrows[existing_escrow_id]["operation_id"])
                raise self._ownership_conflict(winner)
            payer = self._state.accounts.get(payer_account_id)
            if payer is None:
                raise ServiceError("account_not_found", "Account not found", 404, {})
            current_balance = int(payer["balance"])
            if current_balance < amount:
                raise ServiceError("insufficient_funds", "Insufficient funds", 402, {})

            committed_at = self._now()
            escrow_id = self._new_escrow_id()
            tx_id = self._new_tx_id()
            new_balance = current_balance - amount
            payer["balance"] = new_balance
            self._state.escrows[escrow_id] = {
                "escrow_id": escrow_id,
                "payer_account_id": payer_account_id,
                "amount": amount,
                "task_id": task_id,
                "status": "locked",
                "created_at": committed_at,
                "resolved_at": None,
                "operation_id": operation_id,
            }
            self._state.escrow_owners_by_task[key] = escrow_id
            self._append_tx(
                payer_account_id,
                {
                    "tx_id": tx_id,
                    "type": "escrow_lock",
                    "amount": amount,
                    "balance_after": new_balance,
                    "reference": task_id,
                    "timestamp": committed_at,
                },
            )
            envelope = self._record_receipt(
                operation_id,
                "escrow_lock",
                request_sha256,
                task_id,
                committed_at,
                escrow_id,
                {"escrow_status_at_commit": "locked"},
                [
                    {
                        "role": "payer",
                        "account_id": payer_account_id,
                        "direction": "debit",
                        "amount": amount,
                        "transaction_id": tx_id,
                        "balance_after": new_balance,
                    }
                ],
            )
            _logger.info(
                "audit",
                extra={
                    "operation": "escrow_lock",
                    "payer_account_id": payer_account_id,
                    "amount": amount,
                    "task_id": task_id,
                    "escrow_id": escrow_id,
                    "operation_id": operation_id,
                    "reference": task_id,
                    "tx_id": tx_id,
                },
            )
            return envelope

    def escrow_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, object]:
        escrow_id = _require_text(escrow_id, "escrow_id")
        recipient_account_id = _require_text(recipient_account_id, "recipient_account_id")
        operation_id = _require_operation_id(operation_id)
        source_reference = _require_text(source_reference, "source_reference")
        fields: dict[str, object] = {
            "escrow_id": escrow_id,
            "recipient_account_id": recipient_account_id,
            "source_reference": source_reference,
        }
        request_sha256 = _request_hash("escrow_release", fields)

        with self._state.lock:
            replay = self._replay(operation_id, "escrow_release", request_sha256)
            if replay is not None:
                return replay
            winner = self._state.settlement_owners.get(escrow_id)
            if winner is not None:
                raise self._ownership_conflict(winner)
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
            new_balance = checked_balance_add(int(recipient["balance"]), amount)
            committed_at = self._now()
            tx_id = self._new_tx_id()
            recipient["balance"] = new_balance
            self._append_tx(
                recipient_account_id,
                {
                    "tx_id": tx_id,
                    "type": "escrow_release",
                    "amount": amount,
                    "balance_after": new_balance,
                    "reference": escrow_id,
                    "timestamp": committed_at,
                },
            )
            escrow["status"] = "released"
            escrow["resolved_at"] = committed_at
            self._state.settlement_owners[escrow_id] = operation_id
            envelope = self._record_receipt(
                operation_id,
                "escrow_release",
                request_sha256,
                source_reference,
                committed_at,
                escrow_id,
                {"escrow_status_at_commit": "released"},
                [
                    {
                        "role": "recipient",
                        "account_id": recipient_account_id,
                        "direction": "credit",
                        "amount": amount,
                        "transaction_id": tx_id,
                        "balance_after": new_balance,
                    }
                ],
            )
            _logger.info(
                "audit",
                extra={
                    "operation": "escrow_release",
                    "escrow_id": escrow_id,
                    "recipient": recipient_account_id,
                    "source_reference": source_reference,
                    "operation_id": operation_id,
                    "amount": amount,
                    "reference": escrow_id,
                    "tx_id": tx_id,
                },
            )
            return envelope

    def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, object]:
        return self._split(
            escrow_id,
            worker_account_id,
            poster_account_id,
            operation_id,
            source_reference,
            share_field="worker_pct",
            share=require_worker_percentage(worker_pct),
        )

    def escrow_settle(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_amount: int,
        poster_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, object]:
        """Pay the worker an exact amount and refund the rest of the escrow to the poster."""
        return self._split(
            escrow_id,
            worker_account_id,
            poster_account_id,
            operation_id,
            source_reference,
            share_field="worker_amount",
            share=require_nonnegative_money(worker_amount, "worker_amount"),
        )

    def _split(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        operation_id: str,
        source_reference: str,
        *,
        share_field: str,
        share: int,
    ) -> dict[str, object]:
        escrow_id = _require_text(escrow_id, "escrow_id")
        worker_account_id = _require_text(worker_account_id, "worker_account_id")
        poster_account_id = _require_text(poster_account_id, "poster_account_id")
        operation_id = _require_operation_id(operation_id)
        source_reference = _require_text(source_reference, "source_reference")
        fields: dict[str, object] = {
            "escrow_id": escrow_id,
            "worker_account_id": worker_account_id,
            "poster_account_id": poster_account_id,
            share_field: share,
            "source_reference": source_reference,
        }
        request_sha256 = _request_hash("escrow_split", fields)

        with self._state.lock:
            replay = self._replay(operation_id, "escrow_split", request_sha256)
            if replay is not None:
                return replay
            winner = self._state.settlement_owners.get(escrow_id)
            if winner is not None:
                raise self._ownership_conflict(winner)
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
            if str(escrow["payer_account_id"]) != poster_account_id:
                raise ServiceError(
                    "payload_mismatch",
                    "poster_account_id must match the escrow payer_account_id",
                    400,
                    {},
                )

            amount = int(escrow["amount"])
            if share_field == "worker_amount" and share > amount:
                raise ServiceError(
                    "invalid_amount", "Worker amount exceeds the escrow amount", 400, {}
                )
            worker_amount = share if share_field == "worker_amount" else amount * share // 100
            poster_amount = amount - worker_amount
            credit_amounts: dict[str, int] = {}
            if worker_amount > 0:
                credit_amounts[worker_account_id] = worker_amount
            if poster_amount > 0:
                credit_amounts[poster_account_id] = (
                    credit_amounts.get(poster_account_id, 0) + poster_amount
                )
            for account_id, credit_amount in credit_amounts.items():
                account = self._state.accounts.get(account_id)
                if account is None:
                    raise ServiceError("account_not_found", "Account not found", 404, {})
                checked_balance_add(int(account["balance"]), credit_amount)

            committed_at = self._now()
            worker_leg = self._credit_split_leg(
                "worker", worker_account_id, worker_amount, escrow_id, committed_at
            )
            poster_leg = self._credit_split_leg(
                "poster", poster_account_id, poster_amount, escrow_id, committed_at
            )
            escrow["status"] = "split"
            escrow["resolved_at"] = committed_at
            self._state.settlement_owners[escrow_id] = operation_id
            envelope = self._record_receipt(
                operation_id,
                "escrow_split",
                request_sha256,
                source_reference,
                committed_at,
                escrow_id,
                {"escrow_status_at_commit": "split", share_field: share},
                [worker_leg, poster_leg],
            )
            _logger.info(
                "audit",
                extra={
                    "operation": "escrow_split",
                    "escrow_id": escrow_id,
                    "worker_account_id": worker_account_id,
                    "poster_account_id": poster_account_id,
                    share_field: share,
                    "worker_amount": worker_amount,
                    "poster_amount": poster_amount,
                    "source_reference": source_reference,
                    "operation_id": operation_id,
                    "reference": escrow_id,
                    "worker_tx_id": worker_leg["transaction_id"],
                    "poster_tx_id": poster_leg["transaction_id"],
                },
            )
            return envelope

    def _credit_split_leg(
        self,
        role: str,
        account_id: str,
        amount: int,
        escrow_id: str,
        committed_at: str,
    ) -> dict[str, object]:
        if amount == 0:
            return {
                "role": role,
                "account_id": account_id,
                "direction": "credit",
                "amount": 0,
                "transaction_id": None,
                "balance_after": None,
            }
        account = self._state.accounts[account_id]
        new_balance = checked_balance_add(int(account["balance"]), amount)
        tx_id = self._new_tx_id()
        account["balance"] = new_balance
        self._append_tx(
            account_id,
            {
                "tx_id": tx_id,
                "type": "escrow_release",
                "amount": amount,
                "balance_after": new_balance,
                "reference": escrow_id,
                "timestamp": committed_at,
            },
        )
        return {
            "role": role,
            "account_id": account_id,
            "direction": "credit",
            "amount": amount,
            "transaction_id": tx_id,
            "balance_after": new_balance,
        }

    def count_accounts(self) -> int:
        with self._state.lock:
            return len(self._state.accounts)

    def total_escrowed(self) -> int:
        with self._state.lock:
            return sum(
                int(escrow["amount"])
                for escrow in self._state.escrows.values()
                if str(escrow["status"]) == "locked"
            )

    def close(self) -> None:
        """Keep API parity with persistent stores that own resources."""


__all__ = ["InMemoryLedgerStore"]
