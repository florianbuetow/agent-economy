"""Demo funding rejects historical observations as proof of new funding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import LegacyFinancialEnvelope

from demo_replay import clients
from demo_replay import engine as engine_module
from demo_replay.config import PlatformConfig
from demo_replay.engine import ReplayEngine
from demo_replay.wallet import DemoAgent

pytestmark = pytest.mark.unit

_REFERENCE = "demo:historical:fund:alice"
_HISTORICAL_TX_ID = "tx-demo-historical-credit"


def _sha256(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _legacy_credit(operation_id: str) -> dict[str, object]:
    request = {
        "request_version": 1,
        "operation_kind": "credit",
        "account_id": "a-alice",
        "amount": 100,
        "reference": _REFERENCE,
    }
    transaction = {
        "tx_id": _HISTORICAL_TX_ID,
        "account_id": "a-alice",
        "type": "credit",
        "amount": 100,
        "balance_after": 100,
        "reference": _REFERENCE,
        "timestamp": "",
        "event_id": -1,
    }
    raw = {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": operation_id,
            "operation_kind": "credit",
            "request_sha256": _sha256(request),
        },
        "observation": {
            "operation_id": None,
            "observed_kind": "observed_credit",
            "transaction": transaction,
            "observed_fact_sha256": _sha256(transaction),
        },
        "current_escrow": None,
    }
    return LegacyFinancialEnvelope.model_validate(raw).model_dump(mode="json")


def _config() -> PlatformConfig:
    return PlatformConfig(
        identity_url="https://identity",
        bank_url="https://bank",
        task_board_url="https://board",
        reputation_url="https://reputation",
        court_url="https://court",
        platform_key_path=Path("keys/platform.key"),
        token_ttl_seconds=60,
    )


@pytest.mark.asyncio
async def test_demo_fund_rejects_historical_credit_before_success_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ReplayEngine({"name": "test", "agents": [], "steps": []}, _config())
    agent = DemoAgent.create("alice", "Alice", 60)
    agent.agent_id = "a-alice"
    engine.agents["alice"] = agent
    engine.platform = DemoAgent.create("platform", "Platform", 60)
    engine.platform.agent_id = "a-platform"
    operation_id = operation_id_for("demo-funding", ("a-alice", _REFERENCE))
    credit = AsyncMock(return_value=_legacy_credit(operation_id))
    console = MagicMock()
    monkeypatch.setattr(clients, "credit_account", credit)
    monkeypatch.setattr(engine_module, "console", console)

    with pytest.raises(
        RuntimeError,
        match=r"historical observation.*no native operation confirmation",
    ) as error:
        await engine._do_fund(
            MagicMock(),
            {"action": "fund", "agent": "alice", "amount": 100, "reference": _REFERENCE},
        )

    assert _HISTORICAL_TX_ID in str(error.value)
    credit.assert_awaited_once()
    console.print.assert_not_called()
