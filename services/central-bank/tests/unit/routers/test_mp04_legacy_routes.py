"""Public Bank routes return and log historical facts without claiming new effects."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from central_bank_service.core.state import get_app_state
from tests.unit.test_mp04_legacy_responses import _observation

from .conftest import PLATFORM_AGENT_ID, make_jws_token

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("kind", ["credit", "escrow_lock"])
async def test_historical_route_preserves_observation_and_logs_no_new_ownership(
    client: Any,
    platform_keypair: Any,
    agent_keypair: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    kind: str,
) -> None:
    response_body = _observation(kind)
    operation_id = response_body["attempt"]["operation_id"]
    state = get_app_state()
    assert state.ledger is not None
    method = AsyncMock(return_value=response_body)
    monkeypatch.setattr(state.ledger, kind, method)
    if kind == "credit":
        path = "/accounts/a-poster/credit"
        payload = {
            "action": "credit",
            "account_id": "a-poster",
            "amount": 50,
            "reference": "old-funding",
            "operation_id": operation_id,
        }
        token = make_jws_token(platform_keypair[0], PLATFORM_AGENT_ID, payload)
        expected_status = 200
        expected_message = "Historical credit observed"
    else:
        path = "/escrow/lock"
        payload = {
            "action": "escrow_lock",
            "agent_id": "a-poster",
            "amount": 50,
            "task_id": "t-old",
            "operation_id": operation_id,
        }
        token = make_jws_token(agent_keypair[0], "a-poster", payload)
        expected_status = 201
        expected_message = "Historical escrow lock observed"
    logger = logging.getLogger("central-bank")
    caplog.set_level(logging.INFO, logger=logger.name)
    logger.addHandler(caplog.handler)
    try:
        response = await client.post(path, json={"token": token})
        assert response.status_code == expected_status, response.text
        assert response.json() == response_body
        method.assert_awaited_once()
        records = [record for record in caplog.records if record.getMessage() == expected_message]
        assert len(records) == 1
        record = records[0]
        assert record.operation_id is None
        assert record.attempt_operation_id == operation_id
        assert record.tx_id == "tx-old"
        assert record.amount == 50
        assert not any(
            item.getMessage() in {"Account credited", "Escrow locked"} for item in caplog.records
        )
    finally:
        logger.removeHandler(caplog.handler)
