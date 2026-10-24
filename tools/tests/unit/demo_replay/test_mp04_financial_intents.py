"""Demo replay owns stable financial intents and validates native results."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
import yaml
from service_auth.financial_identity import operation_id_for
from service_auth.jws import parse_jws
from service_auth.task_postings import TaskPostingResponse

from demo_replay import clients
from demo_replay import engine as engine_module
from demo_replay.config import PlatformConfig
from demo_replay.engine import ReplayEngine
from demo_replay.run_store import ActorDefinition, RunEnvironment, RunStore
from demo_replay.wallet import DemoAgent

pytestmark = pytest.mark.unit

_OPERATION_ID = "op-00000000-0000-4000-8000-000000000081"
_REFERENCE = "demo:quick:fund:alice"


def _credit_envelope(
    account_id: str,
    amount: int,
    reference: str,
    operation_id: str,
) -> dict[str, object]:
    canonical_request = {
        "request_version": 1,
        "operation_kind": "credit",
        "account_id": account_id,
        "amount": amount,
        "reference": reference,
    }
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": operation_id,
            "operation_kind": "credit",
            "request_sha256": hashlib.sha256(
                json.dumps(
                    canonical_request,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
            "source_reference": reference,
            "committed_at": "2026-10-17T14:00:00Z",
            "event_id": 1,
            "escrow_id": None,
            "result": {
                "account_id": account_id,
                "amount": amount,
                "balance_after": amount,
                "reference": reference,
            },
            "legs": [
                {
                    "role": "account",
                    "account_id": account_id,
                    "direction": "credit",
                    "amount": amount,
                    "transaction_id": "tx-demo-funding",
                    "balance_after": amount,
                }
            ],
        },
        "current_escrow": None,
    }


def _agent(handle: str, agent_id: str) -> DemoAgent:
    agent = DemoAgent.create(handle, handle.title(), 60)
    agent.agent_id = agent_id
    return agent


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


def _durable_funding_engine(steps: list[dict[str, object]]) -> ReplayEngine:
    config = _config()
    scenario = {
        "name": "durable funding",
        "agents": [{"handle": "alice", "name": "Alice"}],
        "steps": steps,
    }
    engine = ReplayEngine(scenario, config)
    state = Path(tempfile.mkdtemp(prefix="mp05-funding-unit-")).resolve()
    store = RunStore.open(
        state_directory=state,
        run_id=f"run-{UUID(int=id(engine) % (1 << 128))}",
        environment=RunEnvironment(
            environment_id="funding-unit",
            identity_url=config.identity_url,
            bank_url=config.bank_url,
            task_board_url=config.task_board_url,
            reputation_url=config.reputation_url,
            court_url=config.court_url,
            platform_public_fingerprint_sha256="d" * 64,
        ),
        scenario_bytes=yaml.safe_dump(scenario, sort_keys=False).encode(),
        scenario=scenario,
        actor_definitions=(ActorDefinition(handle="alice", name="Alice"),),
        effective_delay_seconds=0.0,
        max_record_bytes=1_048_576,
        diagnostic_limit=32,
    )
    actor = store.actors["alice"]
    store.record_registration(
        actor_handle="alice",
        response={
            "agent_id": "a-alice",
            "name": "Alice",
            "public_key": actor.public_key,
            "registered_at": "2026-10-22T00:00:00Z",
        },
    )
    loaded = DemoAgent.from_pem("alice", "Alice", actor.private_key_path, 60)
    loaded.agent_id = "a-alice"
    engine.agents["alice"] = loaded
    engine.platform = _agent("platform", "a-platform")
    engine._store = store
    return engine


@pytest.mark.asyncio
async def test_credit_uses_explicit_intent_and_validates_native_receipt() -> None:
    requests: list[httpx.Request] = []
    envelope = _credit_envelope("a-alice", 100, _REFERENCE, _OPERATION_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=envelope)

    platform = _agent("platform", "a-platform")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await clients.credit_account(
            http,
            platform,
            "a-alice",
            100,
            "https://bank",
            operation_id=_OPERATION_ID,
            reference=_REFERENCE,
        )

    assert result == envelope
    token = parse_jws(json.loads(requests[0].content)["token"])
    assert token.payload == {
        "action": "credit",
        "operation_id": _OPERATION_ID,
        "account_id": "a-alice",
        "amount": 100,
        "reference": _REFERENCE,
    }


@pytest.mark.asyncio
async def test_credit_rejects_a_mismatched_native_receipt() -> None:
    envelope = _credit_envelope("a-alice", 100, _REFERENCE, _OPERATION_ID)
    receipt = envelope["receipt"]
    assert isinstance(receipt, dict)
    receipt["request_sha256"] = "0" * 64
    platform = _agent("platform", "a-platform")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=envelope)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(RuntimeError, match="mismatched native financial receipt"):
            await clients.credit_account(
                http,
                platform,
                "a-alice",
                100,
                "https://bank",
                operation_id=_OPERATION_ID,
                reference=_REFERENCE,
            )


@pytest.mark.asyncio
async def test_zero_account_strictly_parses_the_current_projection() -> None:
    platform = _agent("platform", "a-platform")

    def valid_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "account_id": "a-worker",
                "balance": 0,
                "created_at": "2026-10-17T14:00:00Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(valid_handler)) as http:
        assert await clients.create_account(http, platform, "a-worker", "https://bank") == {
            "account_id": "a-worker",
            "balance": 0,
            "created_at": "2026-10-17T14:00:00Z",
        }

    def invalid_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"account_id": "a-worker", "balance": False, "created_at": "now"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_handler)) as http:
        with pytest.raises(RuntimeError, match="invalid account projection"):
            await clients.create_account(http, platform, "a-worker", "https://bank")


@pytest.mark.asyncio
async def test_zero_account_retains_existing_account_conflict_behavior() -> None:
    platform = _agent("platform", "a-platform")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "account_exists"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await clients.create_account(http, platform, "a-worker", "https://bank") == {
            "status": "already_exists"
        }


@pytest.mark.asyncio
async def test_post_task_derives_lock_identity_from_allocated_task_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"task_id": "created"})

    poster = _agent("poster", "a-poster")
    monkeypatch.setattr(
        "service_auth.mixins.task_board.parse_task_posting_submit_response",
        lambda response, intent: TaskPostingResponse(response.status_code, {"accepted": True}),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        poster.configure_task_board(http, "https://board")
        intent = poster.prepare_task_posting(
            "Title",
            "Spec",
            10,
            3600,
            300,
            7200,
            3600,
        )
        await poster.submit_task_posting(intent)

    body = json.loads(requests[0].content)
    task = parse_jws(body["task_token"])
    escrow = parse_jws(body["escrow_token"])
    task_id = task.payload["task_id"]
    assert isinstance(task_id, str)
    assert escrow.payload["operation_id"] == operation_id_for(
        "task-lock",
        (task_id, "a-poster"),
    )
    assert escrow.payload["task_id"] == task_id
    assert escrow.payload["agent_id"] == "a-poster"
    assert escrow.payload["amount"] == 10


@pytest.mark.asyncio
async def test_fund_step_derives_stable_identity_and_prints_receipt_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = {"action": "fund", "agent": "alice", "amount": 100, "reference": _REFERENCE}
    engine = _durable_funding_engine([step])
    operation_id = operation_id_for("demo-funding", ("a-alice", _REFERENCE))
    credit = AsyncMock(return_value=_credit_envelope("a-alice", 100, _REFERENCE, operation_id))
    console = MagicMock()
    monkeypatch.setattr(clients, "credit_account", credit)
    monkeypatch.setattr(engine_module, "console", console)

    await engine._execute_step(MagicMock(), step)
    assert engine._restore_completed_step(0, step)

    assert credit.await_count == 1
    for call in credit.await_args_list:
        assert call.kwargs == {
            "operation_id": operation_id,
            "reference": _REFERENCE,
            "bank_url": "https://bank",
        }
        assert call.args[2:4] == ("a-alice", 100)
    for call in console.print.call_args_list:
        rendered = call.args[0]
        assert "balance: 100" in rendered
        assert f"operation_id: {operation_id}" in rendered


@pytest.mark.asyncio
async def test_fund_step_requires_reference_before_transport_or_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = {"action": "fund", "agent": "alice", "amount": 100}
    engine = _durable_funding_engine([step])
    credit = AsyncMock()
    console = MagicMock()
    monkeypatch.setattr(clients, "credit_account", credit)
    monkeypatch.setattr(engine_module, "console", console)

    with pytest.raises(ValueError, match="reference"):
        await engine._execute_step(MagicMock(), step)

    credit.assert_not_awaited()
    console.print.assert_not_called()


@pytest.mark.asyncio
async def test_changed_amount_under_one_reference_reuses_identity_without_false_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = {"action": "fund", "agent": "alice", "amount": 100, "reference": _REFERENCE}
    second = {"action": "fund", "agent": "alice", "amount": 101, "reference": _REFERENCE}
    engine = _durable_funding_engine([first, second])
    operation_id = operation_id_for("demo-funding", ("a-alice", _REFERENCE))
    credit = AsyncMock(
        side_effect=[
            _credit_envelope("a-alice", 100, _REFERENCE, operation_id),
            RuntimeError("financial operation conflict"),
        ]
    )
    console = MagicMock()
    monkeypatch.setattr(clients, "credit_account", credit)
    monkeypatch.setattr(engine_module, "console", console)

    await engine._execute_step(MagicMock(), first, step_index=0)
    with pytest.raises(RuntimeError, match="financial operation conflict"):
        await engine._execute_step(MagicMock(), second, step_index=1)

    assert [call.kwargs["operation_id"] for call in credit.await_args_list] == [
        operation_id,
        operation_id,
    ]
    assert console.print.call_count == 1


def test_shipped_funding_steps_have_unique_stable_references() -> None:
    scenario_root = Path(__file__).parents[3] / "scenarios"
    for path in sorted(scenario_root.glob("*.yaml")):
        scenario = yaml.safe_load(path.read_text())
        references = [
            step.get("reference") for step in scenario["steps"] if step.get("action") == "fund"
        ]
        assert references
        assert all(isinstance(reference, str) and reference for reference in references)
        assert len(references) == len(set(references))
