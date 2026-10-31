"""Owned Central Bank server with authenticated pre-effect MP-05 request gates."""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from central_bank_service.app import create_app
from central_bank_service.routers import escrow
from central_bank_service.services.ledger_db_client import LedgerDbClient
from service_auth import parse_jws

if TYPE_CHECKING:
    from service_auth.jws import VerifiedJws


_verified_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mp05_verified_escrow_token",
    default=None,
)


def _write_durable_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


async def _wait_for_release(path: Path) -> None:
    while not path.exists():
        await asyncio.sleep(0.02)


def _install_authenticated_lock_gates(gate_root: Path) -> None:
    original_verify = escrow.verify_jws_token
    original_lock = LedgerDbClient.escrow_lock
    call_count = 0
    count_lock = asyncio.Lock()

    async def capture_verified_token(
        token: str,
        *,
        audience: str,
        resource: str,
        allowed_action: str | tuple[str, ...],
    ) -> VerifiedJws:
        verified = await original_verify(
            token,
            audience=audience,
            resource=resource,
            allowed_action=allowed_action,
        )
        _verified_token.set(token)
        return verified

    async def gated_lock(
        self: LedgerDbClient,
        payer_account_id: str,
        amount: int,
        task_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        nonlocal call_count
        token = _verified_token.get()
        if token is None:
            raise AssertionError("Escrow effect reached the gate without authenticated token proof")
        parsed = parse_jws(token)
        async with count_lock:
            call_count += 1
            call_number = call_count
        if call_number not in {1, 2}:
            raise AssertionError(f"Unexpected authenticated escrow effect attempt {call_number}")

        common = {
            "call_number": call_number,
            "pid": os.getpid(),
            "kid": parsed.header.kid,
            "iat": parsed.header.iat,
            "exp": parsed.header.exp,
            "token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
            "operation_id": operation_id,
            "task_id": task_id,
            "payer_account_id": payer_account_id,
            "amount": amount,
        }
        _write_durable_json(gate_root / f"entered-{call_number}.json", common)
        await _wait_for_release(gate_root / f"release-{call_number}")
        receipt = await original_lock(
            self,
            payer_account_id,
            amount,
            task_id,
            operation_id,
        )
        native_receipt = receipt["receipt"]
        if not isinstance(native_receipt, dict):
            raise AssertionError("Real Central Bank returned a non-native receipt")
        completed = {
            **common,
            "outcome": receipt["outcome"],
            "financial_envelope": receipt,
            "receipt_operation_id": native_receipt["operation_id"],
            "receipt_event_id": native_receipt["event_id"],
            "escrow_id": native_receipt["escrow_id"],
            "leg_transaction_ids": [leg["transaction_id"] for leg in native_receipt["legs"]],
            "escrow_status": receipt["current_escrow"]["status"],
        }
        _write_durable_json(gate_root / f"completed-{call_number}.json", completed)
        return receipt

    escrow.verify_jws_token = capture_verified_token
    LedgerDbClient.escrow_lock = gated_lock  # type: ignore[method-assign]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fd", required=True, type=int)
    parser.add_argument("--gate-root", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.fd < 0:
        raise ValueError("Inherited server file descriptor must be nonnegative")
    _install_authenticated_lock_gates(arguments.gate_root)
    uvicorn.run(create_app(), fd=arguments.fd, log_level="warning")


if __name__ == "__main__":
    main()
