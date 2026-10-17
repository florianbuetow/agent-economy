"""Router logs retain authoritative facts from native financial receipts."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from .conftest import PLATFORM_AGENT_ID, make_jws_token, make_operation_id

pytestmark = pytest.mark.unit


def _capture_service_logs(caplog: pytest.LogCaptureFixture) -> logging.Logger:
    caplog.set_level(logging.INFO, logger="central-bank")
    logger = logging.getLogger("central-bank")
    logger.addHandler(caplog.handler)
    return logger


def _record(caplog: pytest.LogCaptureFixture, message: str) -> logging.LogRecord:
    matches = [record for record in caplog.records if record.getMessage() == message]
    assert len(matches) == 1
    return matches[0]


async def _create_account(
    client: Any,
    private_key: Any,
    account_id: str,
    balance: int,
    operation_id: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": "create_account",
        "agent_id": account_id,
        "initial_balance": balance,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    token = make_jws_token(private_key, PLATFORM_AGENT_ID, payload)
    response = await client.post("/accounts", json={"token": token})
    assert response.status_code == 201, response.text
    result = response.json()
    assert isinstance(result, dict)
    return result


async def _lock(
    client: Any,
    private_key: Any,
    payer_id: str,
    amount: int,
    task_id: str,
    operation_id: str,
) -> dict[str, object]:
    token = make_jws_token(
        private_key,
        payer_id,
        {
            "action": "escrow_lock",
            "agent_id": payer_id,
            "amount": amount,
            "task_id": task_id,
            "operation_id": operation_id,
        },
    )
    response = await client.post("/escrow/lock", json={"token": token})
    assert response.status_code == 201, response.text
    result = response.json()
    assert isinstance(result, dict)
    return result


def _escrow_id(envelope: dict[str, object]) -> str:
    receipt = envelope["receipt"]
    assert isinstance(receipt, dict)
    escrow_id = receipt["escrow_id"]
    assert isinstance(escrow_id, str)
    return escrow_id


@pytest.mark.asyncio
async def test_positive_issuance_log_uses_receipt_identity(
    client: Any,
    platform_keypair: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    operation_id = make_operation_id()
    logger = _capture_service_logs(caplog)
    try:
        await _create_account(client, platform_keypair[0], "a-issued-log", 25, operation_id)
        record = _record(caplog, "Account created")
        assert record.account_id == "a-issued-log"
        assert record.initial_balance == 25
        assert record.operation_id == operation_id
    finally:
        logger.removeHandler(caplog.handler)


@pytest.mark.asyncio
async def test_zero_account_log_has_no_financial_identity(
    client: Any,
    platform_keypair: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = _capture_service_logs(caplog)
    try:
        await _create_account(client, platform_keypair[0], "a-zero-log", 0, None)
        record = _record(caplog, "Account created")
        assert record.account_id == "a-zero-log"
        assert record.initial_balance == 0
        assert not hasattr(record, "operation_id")
    finally:
        logger.removeHandler(caplog.handler)


@pytest.mark.asyncio
async def test_credit_log_keeps_the_validated_command_facts(
    client: Any,
    platform_keypair: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    platform_key = platform_keypair[0]
    await _create_account(client, platform_key, "a-credit-log", 0, None)
    operation_id = make_operation_id()
    logger = _capture_service_logs(caplog)
    try:
        token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "credit",
                "account_id": "a-credit-log",
                "amount": 9,
                "reference": "logging-credit",
                "operation_id": operation_id,
            },
        )
        response = await client.post(
            "/accounts/a-credit-log/credit",
            json={"token": token},
        )
        assert response.status_code == 200, response.text
        record = _record(caplog, "Account credited")
        assert record.account_id == "a-credit-log"
        assert record.amount == 9
        assert record.reference == "logging-credit"
        assert record.operation_id == operation_id
    finally:
        logger.removeHandler(caplog.handler)


@pytest.mark.asyncio
async def test_lock_log_reads_escrow_identity_from_the_receipt(
    client: Any,
    platform_keypair: Any,
    agent_keypair: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _create_account(
        client,
        platform_keypair[0],
        "a-lock-log",
        40,
        make_operation_id(),
    )
    operation_id = make_operation_id()
    logger = _capture_service_logs(caplog)
    try:
        envelope = await _lock(
            client,
            agent_keypair[0],
            "a-lock-log",
            15,
            "t-lock-log",
            operation_id,
        )
        record = _record(caplog, "Escrow locked")
        assert record.operation_id == operation_id
        assert record.escrow_id == _escrow_id(envelope)
        assert record.payer_account_id == "a-lock-log"
        assert record.amount == 15
        assert record.task_id == "t-lock-log"
    finally:
        logger.removeHandler(caplog.handler)


@pytest.mark.asyncio
async def test_release_log_reads_amount_from_the_receipt_leg(
    client: Any,
    platform_keypair: Any,
    agent_keypair: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    platform_key = platform_keypair[0]
    await _create_account(client, platform_key, "a-release-payer", 40, make_operation_id())
    await _create_account(client, platform_key, "a-release-worker", 0, None)
    locked = await _lock(
        client,
        agent_keypair[0],
        "a-release-payer",
        17,
        "t-release-log",
        make_operation_id(),
    )
    escrow_id = _escrow_id(locked)
    operation_id = make_operation_id()
    source_reference = "task:t-release-log:approved"
    logger = _capture_service_logs(caplog)
    try:
        token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": "a-release-worker",
                "operation_id": operation_id,
                "source_reference": source_reference,
            },
        )
        response = await client.post(
            f"/escrow/{escrow_id}/release",
            json={"token": token},
        )
        assert response.status_code == 200, response.text
        record = _record(caplog, "Escrow released")
        assert record.operation_id == operation_id
        assert record.source_reference == source_reference
        assert record.escrow_id == escrow_id
        assert record.recipient_account_id == "a-release-worker"
        assert record.amount == 17
    finally:
        logger.removeHandler(caplog.handler)


@pytest.mark.asyncio
async def test_split_log_reads_both_ordered_receipt_legs(
    client: Any,
    platform_keypair: Any,
    agent_keypair: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    platform_key = platform_keypair[0]
    await _create_account(client, platform_key, "a-split-poster", 100, make_operation_id())
    await _create_account(client, platform_key, "a-split-worker", 0, None)
    locked = await _lock(
        client,
        agent_keypair[0],
        "a-split-poster",
        100,
        "t-split-log",
        make_operation_id(),
    )
    escrow_id = _escrow_id(locked)
    operation_id = make_operation_id()
    source_reference = "task:t-split-log:ruling:r-one"
    logger = _capture_service_logs(caplog)
    try:
        token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": "a-split-worker",
                "worker_pct": 35,
                "poster_account_id": "a-split-poster",
                "operation_id": operation_id,
                "source_reference": source_reference,
            },
        )
        response = await client.post(
            f"/escrow/{escrow_id}/split",
            json={"token": token},
        )
        assert response.status_code == 200, response.text
        record = _record(caplog, "Escrow split")
        assert record.operation_id == operation_id
        assert record.source_reference == source_reference
        assert record.escrow_id == escrow_id
        assert record.worker_account_id == "a-split-worker"
        assert record.poster_account_id == "a-split-poster"
        assert record.worker_pct == response.json()["receipt"]["result"]["worker_pct"] == 35
        assert record.worker_amount == 35
        assert record.poster_amount == 65
    finally:
        logger.removeHandler(caplog.handler)
