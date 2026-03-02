"""DB Gateway-backed ledger storage."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from service_commons.exceptions import ServiceError


class LedgerDbClient:
    """Ledger storage backed by the DB Gateway HTTP API."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _new_tx_id(self) -> str:
        return f"tx-{uuid.uuid4()}"

    def _new_escrow_id(self) -> str:
        return f"esc-{uuid.uuid4()}"

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

    def create_account(self, account_id: str, initial_balance: int) -> dict[str, object]:
        if initial_balance < 0:
            raise ServiceError("invalid_amount", "Initial balance must be non-negative", 400, {})

        now = self._now()
        payload: dict[str, Any] = {
            "account_id": account_id,
            "balance": initial_balance,
            "created_at": now,
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": now,
                "agent_id": account_id,
                "summary": f"Account created for {account_id}",
                "payload": json.dumps({"agent_name": account_id}),
            },
        }
        if initial_balance > 0:
            payload["initial_credit"] = {
                "tx_id": self._new_tx_id(),
                "amount": initial_balance,
                "reference": "initial_balance",
                "timestamp": now,
            }

        response = self._client.post("/bank/accounts", json=payload)
        if response.status_code == 409:
            raise ServiceError("account_exists", "Account already exists for this agent", 409, {})
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        return {
            "account_id": account_id,
            "balance": initial_balance,
            "created_at": now,
        }

    def get_account(self, account_id: str) -> dict[str, object] | None:
        response = self._client.get(f"/bank/accounts/{account_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data = self._json(response)
        return {
            "account_id": str(data["account_id"]),
            "balance": int(data["balance"]),
            "created_at": str(data["created_at"]),
        }

    def credit(self, account_id: str, amount: int, reference: str) -> dict[str, object]:
        if amount <= 0:
            raise ServiceError("invalid_amount", "Amount must be a positive integer", 400, {})

        now = self._now()
        tx_id = self._new_tx_id()
        payload: dict[str, Any] = {
            "tx_id": tx_id,
            "account_id": account_id,
            "amount": amount,
            "reference": reference,
            "timestamp": now,
            "event": {
                "event_source": "bank",
                "event_type": "salary.paid",
                "timestamp": now,
                "agent_id": account_id,
                "summary": f"Credited {amount} to {account_id}",
                "payload": json.dumps({"amount": amount}),
            },
        }
        response = self._client.post("/bank/credit", json=payload)

        if response.status_code == 404:
            raise ServiceError("account_not_found", "Account not found", 404, {})
        if response.status_code == 409:
            error_code = str(self._json(response).get("error", ""))
            if error_code in {"reference_conflict", "constraint_violation"}:
                raise ServiceError(
                    "payload_mismatch",
                    "Duplicate credit reference used with a different amount",
                    400,
                    {},
                )
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data = self._json(response)
        return {
            "tx_id": str(data.get("tx_id", tx_id)),
            "balance_after": int(data.get("balance_after", 0)),
        }

    def get_transactions(self, account_id: str) -> list[dict[str, object]]:
        account = self.get_account(account_id)
        if account is None:
            raise ServiceError("account_not_found", "Account not found", 404, {})

        response = self._client.get(f"/bank/accounts/{account_id}/transactions")
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        items_raw = self._json(response).get("transactions", [])
        items = items_raw if isinstance(items_raw, list) else []
        transactions: list[dict[str, object]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            transactions.append(
                {
                    "tx_id": str(item["tx_id"]),
                    "type": str(item["type"]),
                    "amount": int(item["amount"]),
                    "balance_after": int(item["balance_after"]),
                    "reference": str(item["reference"]),
                    "timestamp": str(item["timestamp"]),
                }
            )
        return transactions

    def escrow_lock(self, payer_account_id: str, amount: int, task_id: str) -> dict[str, object]:
        if amount <= 0:
            raise ServiceError("invalid_amount", "Amount must be a positive integer", 400, {})

        now = self._now()
        escrow_id = self._new_escrow_id()
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "payer_account_id": payer_account_id,
            "amount": amount,
            "task_id": task_id,
            "created_at": now,
            "tx_id": self._new_tx_id(),
            "event": {
                "event_source": "bank",
                "event_type": "escrow.locked",
                "timestamp": now,
                "agent_id": payer_account_id,
                "task_id": task_id,
                "summary": f"Escrow locked: {amount} for task {task_id}",
                "payload": json.dumps({"escrow_id": escrow_id, "amount": amount, "title": task_id}),
            },
        }
        response = self._client.post("/bank/escrow/lock", json=payload)

        if response.status_code == 404:
            raise ServiceError("account_not_found", "Account not found", 404, {})
        if response.status_code == 402:
            raise ServiceError(
                "insufficient_funds",
                "Account balance is less than the escrow amount",
                402,
                {},
            )
        if response.status_code == 409:
            msg = self._json(response).get("message", "Escrow already locked")
            raise ServiceError("escrow_already_locked", str(msg), 409, {})
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data = self._json(response)
        return {
            "escrow_id": str(data.get("escrow_id", escrow_id)),
            "amount": amount,
            "task_id": task_id,
            "status": "locked",
        }

    def escrow_release(self, escrow_id: str, recipient_account_id: str) -> dict[str, object]:
        now = self._now()
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "recipient_account_id": recipient_account_id,
            "tx_id": self._new_tx_id(),
            "resolved_at": now,
            "constraints": {"status": "locked"},
            "event": {
                "event_source": "bank",
                "event_type": "escrow.released",
                "timestamp": now,
                "summary": f"Escrow {escrow_id} released to {recipient_account_id}",
                "payload": json.dumps(
                    {
                        "escrow_id": escrow_id,
                        "recipient_id": recipient_account_id,
                    }
                ),
            },
        }
        response = self._client.post("/bank/escrow/release", json=payload)

        if response.status_code == 404:
            error_code = str(self._json(response).get("error", ""))
            if error_code == "account_not_found":
                raise ServiceError("account_not_found", "Recipient account not found", 404, {})
            raise ServiceError("escrow_not_found", "Escrow not found", 404, {})
        if response.status_code == 409:
            raise ServiceError(
                "escrow_already_resolved",
                "Escrow has already been resolved",
                409,
                {},
            )
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data = self._json(response)
        return {
            "escrow_id": escrow_id,
            "status": "released",
            "recipient": recipient_account_id,
            "amount": int(data.get("amount", 0)),
        }

    def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
    ) -> dict[str, object]:
        if not (0 <= worker_pct <= 100):
            raise ServiceError("invalid_amount", "worker_pct must be between 0 and 100", 400, {})

        escrow_response = self._client.get(f"/bank/escrow/{escrow_id}")
        if escrow_response.status_code == 404:
            raise ServiceError("escrow_not_found", "Escrow not found", 404, {})
        if escrow_response.status_code != 200:
            msg = f"Gateway error: {escrow_response.status_code} {escrow_response.text}"
            raise RuntimeError(msg)
        escrow_data = self._json(escrow_response)

        if str(escrow_data["status"]) != "locked":
            raise ServiceError(
                "escrow_already_resolved",
                "Escrow has already been resolved",
                409,
                {},
            )

        if str(escrow_data["payer_account_id"]) != poster_account_id:
            raise ServiceError(
                "payload_mismatch",
                "poster_account_id must match the escrow payer_account_id",
                400,
                {},
            )

        total_amount = int(escrow_data["amount"])
        worker_amount = total_amount * worker_pct // 100
        poster_amount = total_amount - worker_amount

        now = self._now()
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "worker_account_id": worker_account_id,
            "poster_account_id": poster_account_id,
            "worker_amount": worker_amount,
            "poster_amount": poster_amount,
            "worker_tx_id": self._new_tx_id(),
            "poster_tx_id": self._new_tx_id(),
            "resolved_at": now,
            "constraints": {"status": "locked"},
            "event": {
                "event_source": "bank",
                "event_type": "escrow.split",
                "timestamp": now,
                "summary": (
                    f"Escrow {escrow_id} split: worker={worker_amount}, poster={poster_amount}"
                ),
                "payload": json.dumps(
                    {
                        "escrow_id": escrow_id,
                        "worker_amount": worker_amount,
                        "poster_amount": poster_amount,
                    }
                ),
            },
        }
        response = self._client.post("/bank/escrow/split", json=payload)

        if response.status_code == 404:
            error_code = str(self._json(response).get("error", ""))
            if error_code == "account_not_found":
                raise ServiceError("account_not_found", "Account not found", 404, {})
            raise ServiceError("escrow_not_found", "Escrow not found", 404, {})
        if response.status_code == 409:
            raise ServiceError(
                "escrow_already_resolved",
                "Escrow has already been resolved",
                409,
                {},
            )
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        return {
            "escrow_id": escrow_id,
            "status": "split",
            "worker_amount": worker_amount,
            "poster_amount": poster_amount,
        }

    def count_accounts(self) -> int:
        response = self._client.get("/bank/accounts/count")
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return int(self._json(response)["count"])

    def total_escrowed(self) -> int:
        response = self._client.get("/bank/escrow/total-locked")
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return int(self._json(response)["total"])

    def close(self) -> None:
        self._client.close()


__all__ = ["LedgerDbClient"]
