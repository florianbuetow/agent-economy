"""DB Gateway-backed ledger storage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from service_auth.financial_responses import (
    LegacyFinancialEnvelope,
    NativeFinancialEnvelope,
    parse_account_projection,
    parse_financial_envelope,
    parse_native_financial_envelope,
    validate_operation_id,
)
from service_commons.exceptions import ServiceError

from central_bank_service.logging import get_logger
from central_bank_service.monetary import (
    require_nonnegative_money,
    require_positive_money,
    require_worker_percentage,
)
from central_bank_service.services.settlement import (
    SettlementGatewayClient,
    parse_settlement_envelope,
)

if TYPE_CHECKING:
    from service_auth.gateway import GatewayRequestSigner

_logger = get_logger(__name__)


def _log_credit_or_lock(result: dict[str, object]) -> None:
    """Audit the authoritative outcome without assigning new ownership to history."""
    if result["outcome"] == "legacy_observation":
        historical = LegacyFinancialEnvelope.model_validate(result)
        transaction = historical.observation.transaction
        _logger.info(
            "audit",
            extra={
                "operation": "legacy_observation",
                "operation_kind": historical.attempt.operation_kind,
                "operation_id": None,
                "attempt_operation_id": historical.attempt.operation_id,
                "account_id": transaction.account_id,
                "amount": transaction.amount,
                "reference": transaction.reference,
                "tx_id": transaction.tx_id,
            },
        )
        return
    receipt = NativeFinancialEnvelope.model_validate(result).receipt
    leg = receipt.legs[0]
    extra: dict[str, object] = {
        "operation": receipt.operation_kind,
        "operation_id": receipt.operation_id,
        "account_id": leg.account_id,
        "amount": leg.amount,
        "reference": receipt.source_reference,
        "tx_id": leg.transaction_id,
    }
    if receipt.operation_kind == "escrow_lock":
        extra.update(payer_account_id=leg.account_id, task_id=receipt.source_reference)
    _logger.info("audit", extra=extra)


def _require_utf8(value: str, field: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ServiceError(
            "invalid_financial_request", f"{field} must contain valid UTF-8 text", 400, {}
        ) from exc
    return value


class LedgerDbClient:
    """Ledger storage backed by the DB Gateway HTTP API (via the shared GatewayClient).

    GatewayClient raises ServiceError for every non-2xx gateway response, carrying
    the gateway's own error code/message/status code. This facade inspects those
    fields to reproduce LedgerDbClient's pre-consolidation contract exactly (its
    own error codes for account_exists/insufficient_funds/etc., RuntimeError on
    anything genuinely unexpected), so callers see no behavior change.
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: int,
        signer: GatewayRequestSigner,
    ) -> None:
        self._gateway = SettlementGatewayClient(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            signer=signer,
        )

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

    @staticmethod
    def _raise_preserved_money_error(exc: ServiceError) -> None:
        if exc.error in {
            "amount_out_of_range",
            "financial_operation_already_committed",
            "historical_settlement_unreplayable",
            "invalid_amount",
            "invalid_financial_request",
            "invalid_operation_id",
            "legacy_operation_conflict",
            "operation_id_conflict",
            "payload_mismatch",
            "receipt_ownership_failed",
            "receipt_ownership_missing",
        }:
            raise ServiceError(exc.error, exc.message, exc.status_code, exc.details) from exc

    async def create_account(
        self, account_id: str, initial_balance: int, operation_id: str | None
    ) -> dict[str, object]:
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
            try:
                operation_id = validate_operation_id(operation_id)
            except ValueError as exc:
                raise ServiceError("invalid_operation_id", str(exc), 400, {}) from exc

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
            data = await self._gateway.create_account(
                account_id=account_id,
                created_at=now,
                balance=initial_balance,
                initial_credit_data=initial_credit_data,
                agent_name=account_id,
                operation_id=operation_id,
            )
        except ServiceError as exc:
            self._raise_preserved_money_error(exc)
            if exc.status_code == 409:
                raise ServiceError(
                    "account_exists", "Account already exists for this agent", 409, {}
                ) from exc
            raise self._as_gateway_runtime_error(exc) from exc

        if initial_balance > 0:
            if operation_id is None:
                raise RuntimeError("Validated positive issuance lost its operation identity")
            return parse_native_financial_envelope(
                data,
                operation_id,
                "account_issuance",
                {
                    "account_id": account_id,
                    "amount": initial_balance,
                    "reference": "initial_balance",
                },
            )
        account = await self.get_account(account_id)
        if account is None:
            raise RuntimeError("Gateway account creation succeeded without an account row")
        result: dict[str, object] = {
            "account_id": account_id,
            "balance": account["balance"],
            "created_at": account["created_at"],
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
        return parse_account_projection(data, account_id)

    async def credit(
        self, account_id: str, amount: int, reference: str, operation_id: str
    ) -> dict[str, object]:
        amount = require_positive_money(amount, "amount")
        reference = _require_utf8(reference, "reference")

        now = self._now()
        tx_id = self._new_tx_id()

        try:
            data = await self._gateway.credit_account(
                tx_id=tx_id,
                account_id=account_id,
                amount=amount,
                reference=reference,
                timestamp=now,
                operation_id=operation_id,
            )
        except ServiceError as exc:
            self._raise_preserved_money_error(exc)
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

        result = parse_financial_envelope(
            data,
            operation_id,
            "credit",
            {"account_id": account_id, "amount": amount, "reference": reference},
        )
        _log_credit_or_lock(result)
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
        self, payer_account_id: str, amount: int, task_id: str, operation_id: str
    ) -> dict[str, object]:
        amount = require_positive_money(amount, "amount")

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
                operation_id=operation_id,
            )
        except ServiceError as exc:
            self._raise_preserved_money_error(exc)
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

        result = parse_financial_envelope(
            data,
            operation_id,
            "escrow_lock",
            {
                "payer_account_id": payer_account_id,
                "task_id": task_id,
                "amount": amount,
            },
        )
        _log_credit_or_lock(result)
        return result

    async def escrow_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, object]:
        source_reference = _require_utf8(source_reference, "source_reference")
        now = self._now()
        tx_id = self._new_tx_id()

        try:
            data = await self._gateway.escrow_release(
                escrow_id=escrow_id,
                recipient_account_id=recipient_account_id,
                tx_id=tx_id,
                resolved_at=now,
                source_reference=source_reference,
                operation_id=operation_id,
            )
        except ServiceError as exc:
            self._raise_preserved_money_error(exc)
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

        result = parse_native_financial_envelope(
            data,
            operation_id,
            "escrow_release",
            {
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
                "source_reference": source_reference,
            },
        )
        _logger.info(
            "audit",
            extra={
                "operation": "escrow_release",
                "escrow_id": escrow_id,
                "recipient": recipient_account_id,
                "source_reference": source_reference,
                "operation_id": operation_id,
            },
        )
        return result

    async def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, object]:
        worker_pct = require_worker_percentage(worker_pct)
        source_reference = _require_utf8(source_reference, "source_reference")

        now = self._now()
        worker_tx_id = self._new_tx_id()
        poster_tx_id = self._new_tx_id()

        try:
            data = await self._gateway.escrow_split(
                escrow_id=escrow_id,
                worker_account_id=worker_account_id,
                poster_account_id=poster_account_id,
                worker_tx_id=worker_tx_id,
                poster_tx_id=poster_tx_id,
                resolved_at=now,
                worker_pct=worker_pct,
                source_reference=source_reference,
                operation_id=operation_id,
            )
        except ServiceError as exc:
            self._raise_preserved_money_error(exc)
            if exc.status_code == 404:
                if exc.error == "account_not_found":
                    raise ServiceError("account_not_found", "Account not found", 404, {}) from exc
                raise ServiceError("escrow_not_found", "Escrow not found", 404, {}) from exc
            if exc.status_code == 409:
                raise ServiceError(
                    "escrow_already_resolved", "Escrow has already been resolved", 409, {}
                ) from exc
            raise self._as_gateway_runtime_error(exc) from exc

        result = parse_native_financial_envelope(
            data,
            operation_id,
            "escrow_split",
            {
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_pct": worker_pct,
                "source_reference": source_reference,
            },
        )
        _logger.info(
            "audit",
            extra={
                "operation": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_pct": worker_pct,
                "source_reference": source_reference,
                "operation_id": operation_id,
            },
        )
        return result

    async def escrow_settle(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_amount: int,
        poster_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, object]:
        worker_amount = require_nonnegative_money(worker_amount, "worker_amount")
        source_reference = _require_utf8(source_reference, "source_reference")

        try:
            data = await self._gateway.escrow_settle(
                escrow_id=escrow_id,
                worker_account_id=worker_account_id,
                poster_account_id=poster_account_id,
                worker_tx_id=self._new_tx_id(),
                poster_tx_id=self._new_tx_id(),
                resolved_at=self._now(),
                worker_amount=worker_amount,
                source_reference=source_reference,
                operation_id=operation_id,
            )
        except ServiceError as exc:
            self._raise_preserved_money_error(exc)
            if exc.status_code == 404:
                if exc.error == "account_not_found":
                    raise ServiceError("account_not_found", "Account not found", 404, {}) from exc
                raise ServiceError("escrow_not_found", "Escrow not found", 404, {}) from exc
            if exc.status_code == 409:
                raise ServiceError(
                    "escrow_already_resolved", "Escrow has already been resolved", 409, {}
                ) from exc
            raise self._as_gateway_runtime_error(exc) from exc

        result = parse_settlement_envelope(
            data,
            operation_id,
            {
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_amount": worker_amount,
                "source_reference": source_reference,
            },
        )
        _logger.info(
            "audit",
            extra={
                "operation": "escrow_settle",
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_amount": worker_amount,
                "source_reference": source_reference,
                "operation_id": operation_id,
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
