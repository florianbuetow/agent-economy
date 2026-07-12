"""DB Gateway-backed ledger storage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from service_clients.gateway import GatewayClient
from service_commons.exceptions import ServiceError

from central_bank_service.logging import get_logger

_logger = get_logger(__name__)


class LedgerDbClient:
    """Ledger storage backed by the DB Gateway HTTP API (via the shared GatewayClient).

    GatewayClient raises ServiceError for every non-2xx gateway response, carrying
    the gateway's own error code/message/status code. This facade inspects those
    fields to reproduce LedgerDbClient's pre-consolidation contract exactly (its
    own error codes for account_exists/insufficient_funds/etc., RuntimeError on
    anything genuinely unexpected), so callers see no behavior change.
    """

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self._gateway = GatewayClient(base_url=base_url, timeout_seconds=timeout_seconds)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _new_tx_id() -> str:
        return f"tx-{uuid.uuid4()}"

    @staticmethod
    def _new_escrow_id() -> str:
        return f"esc-{uuid.uuid4()}"

    @staticmethod
    def _as_gateway_runtime_error(exc: ServiceError) -> RuntimeError:
        return RuntimeError(f"Gateway error: {exc.status_code} {exc.message}")

    async def create_account(self, account_id: str, initial_balance: int) -> dict[str, object]:
        if initial_balance < 0:
            raise ServiceError("invalid_amount", "Initial balance must be non-negative", 400, {})

        now = self._now()
        tx_id: str | None = None
        initial_credit_data: dict[str, Any] | None = None
        if initial_balance > 0:
            tx_id = self._new_tx_id()
            initial_credit_data = {
                "tx_id": tx_id,
                "amount": initial_balance,
                "reference": "initial_balance",
                "timestamp": now,
            }

        try:
            await self._gateway.create_account(
                account_id=account_id,
                created_at=now,
                balance=initial_balance,
                initial_credit_data=initial_credit_data,
                agent_name=account_id,
            )
        except ServiceError as exc:
            if exc.status_code == 409:
                raise ServiceError(
                    "account_exists", "Account already exists for this agent", 409, {}
                ) from exc
            raise self._as_gateway_runtime_error(exc) from exc

        result: dict[str, object] = {
            "account_id": account_id,
            "balance": initial_balance,
            "created_at": now,
        }
        audit_extra: dict[str, object] = {
            "operation": "create_account",
            "account_id": account_id,
            "initial_balance": initial_balance,
            "reference": "initial_balance",
        }
        if tx_id is not None:
            audit_extra["tx_id"] = tx_id
        _logger.info("audit", extra=audit_extra)
        return result

    async def get_account(self, account_id: str) -> dict[str, object] | None:
        try:
            data = await self._gateway.get_account(account_id)
        except ServiceError as exc:
            raise self._as_gateway_runtime_error(exc) from exc
        if data is None:
            return None
        return {
            "account_id": str(data["account_id"]),
            "balance": int(data["balance"]),
            "created_at": str(data["created_at"]),
        }

    async def credit(self, account_id: str, amount: int, reference: str) -> dict[str, object]:
        if amount <= 0:
            raise ServiceError("invalid_amount", "Amount must be a positive integer", 400, {})

        now = self._now()
        tx_id = self._new_tx_id()

        try:
            data = await self._gateway.credit_account(
                tx_id=tx_id,
                account_id=account_id,
                amount=amount,
                reference=reference,
                timestamp=now,
            )
        except ServiceError as exc:
            if exc.status_code == 404:
                raise ServiceError("account_not_found", "Account not found", 404, {}) from exc
            conflict_codes = {"reference_conflict", "constraint_violation"}
            if exc.status_code == 409 and exc.error in conflict_codes:
                raise ServiceError(
                    "payload_mismatch",
                    "Duplicate credit reference used with a different amount",
                    400,
                    {},
                ) from exc
            raise self._as_gateway_runtime_error(exc) from exc

        result: dict[str, object] = {
            "tx_id": str(data.get("tx_id", tx_id)),
            "balance_after": int(data.get("balance_after", 0)),
        }
        _logger.info(
            "audit",
            extra={
                "operation": "credit",
                "account_id": account_id,
                "amount": amount,
                "reference": reference,
                "tx_id": str(result["tx_id"]),
            },
        )
        return result

    async def get_transactions(self, account_id: str) -> list[dict[str, object]]:
        account = await self.get_account(account_id)
        if account is None:
            raise ServiceError("account_not_found", "Account not found", 404, {})

        try:
            items = await self._gateway.get_transactions(account_id)
        except ServiceError as exc:
            raise self._as_gateway_runtime_error(exc) from exc

        transactions: list[dict[str, object]] = []
        for item in items:
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

    async def escrow_lock(
        self, payer_account_id: str, amount: int, task_id: str
    ) -> dict[str, object]:
        if amount <= 0:
            raise ServiceError("invalid_amount", "Amount must be a positive integer", 400, {})

        now = self._now()
        escrow_id = self._new_escrow_id()
        tx_id = self._new_tx_id()

        try:
            data = await self._gateway.escrow_lock(
                escrow_id=escrow_id,
                payer_account_id=payer_account_id,
                amount=amount,
                task_id=task_id,
                created_at=now,
                tx_id=tx_id,
            )
        except ServiceError as exc:
            if exc.status_code == 404:
                raise ServiceError("account_not_found", "Account not found", 404, {}) from exc
            if exc.status_code == 402:
                raise ServiceError(
                    "insufficient_funds",
                    "Account balance is less than the escrow amount",
                    402,
                    {},
                ) from exc
            if exc.status_code == 409:
                raise ServiceError("escrow_already_locked", exc.message, 409, {}) from exc
            raise self._as_gateway_runtime_error(exc) from exc

        result: dict[str, object] = {
            "escrow_id": str(data.get("escrow_id", escrow_id)),
            "amount": amount,
            "task_id": task_id,
            "status": "locked",
        }
        _logger.info(
            "audit",
            extra={
                "operation": "escrow_lock",
                "payer_account_id": payer_account_id,
                "amount": amount,
                "task_id": task_id,
                "escrow_id": str(result["escrow_id"]),
                "reference": task_id,
                "tx_id": tx_id,
            },
        )
        return result

    async def escrow_release(self, escrow_id: str, recipient_account_id: str) -> dict[str, object]:
        # A pre-fetch (mirrors escrow_split) so the release event's summary/payload
        # carries the real amount instead of an unknown placeholder.
        try:
            escrow_data = await self._gateway.get_escrow(escrow_id)
        except ServiceError as exc:
            raise self._as_gateway_runtime_error(exc) from exc
        if escrow_data is None:
            raise ServiceError("escrow_not_found", "Escrow not found", 404, {})
        known_amount = int(escrow_data.get("amount", 0))

        now = self._now()
        tx_id = self._new_tx_id()

        try:
            data = await self._gateway.escrow_release(
                escrow_id=escrow_id,
                recipient_account_id=recipient_account_id,
                tx_id=tx_id,
                resolved_at=now,
                amount=known_amount,
                constraints={"status": "locked"},
            )
        except ServiceError as exc:
            if exc.status_code == 404:
                if exc.error == "account_not_found":
                    raise ServiceError(
                        "account_not_found", "Recipient account not found", 404, {}
                    ) from exc
                raise ServiceError("escrow_not_found", "Escrow not found", 404, {}) from exc
            if exc.status_code == 409:
                raise ServiceError(
                    "escrow_already_resolved", "Escrow has already been resolved", 409, {}
                ) from exc
            raise self._as_gateway_runtime_error(exc) from exc

        amount = int(data.get("amount", known_amount))
        result: dict[str, object] = {
            "escrow_id": escrow_id,
            "status": "released",
            "recipient": recipient_account_id,
            "amount": amount,
        }
        _logger.info(
            "audit",
            extra={
                "operation": "escrow_release",
                "escrow_id": escrow_id,
                "recipient": recipient_account_id,
                "amount": amount,
                "reference": escrow_id,
                "tx_id": tx_id,
            },
        )
        return result

    async def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
    ) -> dict[str, object]:
        if not (0 <= worker_pct <= 100):
            raise ServiceError("invalid_amount", "worker_pct must be between 0 and 100", 400, {})

        try:
            escrow_data = await self._gateway.get_escrow(escrow_id)
        except ServiceError as exc:
            raise self._as_gateway_runtime_error(exc) from exc
        if escrow_data is None:
            raise ServiceError("escrow_not_found", "Escrow not found", 404, {})

        if str(escrow_data["status"]) != "locked":
            raise ServiceError(
                "escrow_already_resolved", "Escrow has already been resolved", 409, {}
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
        worker_tx_id = self._new_tx_id()
        poster_tx_id = self._new_tx_id()

        try:
            await self._gateway.escrow_split(
                escrow_id=escrow_id,
                worker_account_id=worker_account_id,
                poster_account_id=poster_account_id,
                worker_tx_id=worker_tx_id,
                poster_tx_id=poster_tx_id,
                resolved_at=now,
                worker_amount=worker_amount,
                poster_amount=poster_amount,
                constraints={"status": "locked"},
            )
        except ServiceError as exc:
            if exc.status_code == 404:
                if exc.error == "account_not_found":
                    raise ServiceError("account_not_found", "Account not found", 404, {}) from exc
                raise ServiceError("escrow_not_found", "Escrow not found", 404, {}) from exc
            if exc.status_code == 409:
                raise ServiceError(
                    "escrow_already_resolved", "Escrow has already been resolved", 409, {}
                ) from exc
            raise self._as_gateway_runtime_error(exc) from exc

        result: dict[str, object] = {
            "escrow_id": escrow_id,
            "status": "split",
            "worker_amount": worker_amount,
            "poster_amount": poster_amount,
        }
        _logger.info(
            "audit",
            extra={
                "operation": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_amount": worker_amount,
                "poster_amount": poster_amount,
                "reference": escrow_id,
                "worker_tx_id": worker_tx_id,
                "poster_tx_id": poster_tx_id,
            },
        )
        return result

    async def count_accounts(self) -> int:
        try:
            return await self._gateway.count_accounts()
        except ServiceError as exc:
            raise self._as_gateway_runtime_error(exc) from exc

    async def total_escrowed(self) -> int:
        try:
            return await self._gateway.total_escrowed()
        except ServiceError as exc:
            raise self._as_gateway_runtime_error(exc) from exc

    async def close(self) -> None:
        await self._gateway.close()


__all__ = ["LedgerDbClient"]
