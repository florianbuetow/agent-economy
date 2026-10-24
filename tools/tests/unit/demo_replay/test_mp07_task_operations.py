"""MP-07 demo producer contracts: explicit ownership and no false advancement."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
import yaml
from service_auth.financial_responses import SQLITE_MAX_INTEGER
from service_auth.jws import parse_jws
from service_auth.task_postings import TaskPostingResponse
from service_auth.task_postings import TaskPostingIntent
from service_auth.financial_identity import operation_id_for

from demo_replay import clients
from demo_replay import engine as engine_module
from demo_replay.config import PlatformConfig
from demo_replay.engine import ReplayEngine, load_scenario
from demo_replay.run_store import ActorDefinition, RunEnvironment, RunStore
from demo_replay.task_operations import TaskIntent, canonical_json
from demo_replay.wallet import DemoAgent

pytestmark = pytest.mark.unit
TASK = "t-00000000-0000-4000-8000-000000000001"
OPERATION = "op-00000000-0000-4000-8000-000000000002"
OTHER_OPERATION = "op-00000000-0000-4000-8000-000000000003"
BID = "bid-00000000-0000-4000-8000-000000000002"
AT = "2026-10-18T12:00:00Z"
REASON = "The delivered answer is incomplete"
COMMANDS = {
    "bid": ("submit_bid", "open", "open", 0),
    "accept_bid": ("accept_bid", "open", "accepted", 0),
    "submit_deliverable": ("submit_deliverable", "accepted", "submitted", 1),
    "approve": ("approve_task", "submitted", "approved", 2),
    "dispute": ("dispute_task", "submitted", "disputed", 2),
}


def _agent(handle: str) -> DemoAgent:
    agent = DemoAgent.create(handle, handle.title(), 60)
    agent.agent_id = f"a-{handle}"
    return agent


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _task(status: str, version: object) -> dict[str, Any]:
    accepted = status != "open"
    submitted = status in {"submitted", "approved", "disputed"}
    return {
        "task_id": TASK,
        "poster_id": "a-alice",
        "title": "Demo task",
        "spec": "Exact task",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "deadline_seconds": 120,
        "review_deadline_seconds": 180,
        "status": status,
        "escrow_id": "esc-demo",
        "bid_count": 1,
        "worker_id": "a-bob" if accepted else None,
        "accepted_bid_id": BID if accepted else None,
        "created_at": AT,
        "accepted_at": AT if accepted else None,
        "submitted_at": AT if submitted else None,
        "approved_at": AT if status == "approved" else None,
        "cancelled_at": None,
        "disputed_at": AT if status == "disputed" else None,
        "dispute_reason": REASON if status == "disputed" else None,
        "dispute_id": "clm-demo" if status == "disputed" else None,
        "rebuttal_deadline": "2026-10-18T13:00:00Z" if status == "disputed" else None,
        "rebuttal_submitted_at": None,
        "ruling_id": None,
        "ruled_at": None,
        "worker_pct": None,
        "ruling_summary": None,
        "expired_at": None,
        "escrow_pending": False,
        "bidding_deadline": "2026-10-18T12:01:00Z",
        "acceptance_deadline": "2026-10-18T12:02:00Z",
        "execution_deadline": "2026-10-18T12:02:00Z" if accepted else None,
        "review_deadline": "2026-10-18T12:03:00Z" if submitted else None,
        "lifecycle_version": version,
        "timing_contract_version": 2,
        "acceptance_window_seconds": 60,
        "bidding_cutoff_us": 1792324860000000,
        "acceptance_cutoff_us": 1792324920000000,
        "execution_cutoff_us": 1792324920000000 if accepted else None,
        "review_cutoff_us": 1792324980000000 if submitted else None,
        "accepted_reward": 100 if accepted else None,
        "accepted_bid_amount": 90 if accepted else None,
        "pending_transition": None,
        "legacy_reconciliations": [],
    }


def _flat_bid() -> dict[str, Any]:
    return {"bid_id": BID, "task_id": TASK, "bidder_id": "a-bob", "amount": 90, "submitted_at": AT}


def _response(action: str, operation_id: str, version: int, *, pending: bool) -> dict[str, Any]:
    command, source, target, _version = COMMANDS[action]
    if action == "bid":
        return {**_flat_bid(), "bid_id": f"bid-{operation_id[3:]}"}
    result: dict[str, Any] = {
        "task_id": TASK,
        "command": command,
        "status": target,
        "lifecycle_version": version + 1,
        "event_id": 10,
        "worker_id": "a-bob",
    }
    escrow = {
        "escrow_id": "esc-demo",
        "task_id": TASK,
        "payer_account_id": "a-alice",
        "amount": 100,
    }
    effect_kind = "none"
    intent: dict[str, Any] | None = None
    effect_id: str | None = None
    reference: str | None = None
    if action == "accept_bid":
        arguments = {
            "bid_id": BID,
            "worker_id": "a-bob",
            "escrow_id": "esc-demo",
            "reward": 100,
            "bid_amount": 90,
        }
        result.update({"bid_id": BID, "reward": 100, "bid_amount": 90})
    elif action == "submit_deliverable":
        arguments = {"worker_id": "a-bob"}
    else:
        arguments = {
            "poster_id": "a-alice",
            "worker_id": "a-bob",
            "escrow_id": "esc-demo",
            "reward": 100,
        }
        if action == "approve":
            effect_kind, effect_id, reference = "bank_release", operation_id, operation_id
            financial = {
                "request_version": 1,
                "operation_kind": "escrow_release",
                "escrow_id": "esc-demo",
                "recipient_account_id": "a-bob",
                "source_reference": f"task:{TASK}:approved",
            }
            intent = {
                "financial_request": financial,
                "financial_request_sha256": _hash(financial),
                "expected_escrow": escrow,
            }
            arguments["source_reference"] = f"task:{TASK}:approved"
            result["reward"] = 100
        else:
            effect_kind, reference = "court_claim", "clm-demo"
            arguments["reason"] = REASON
            intent = {
                "court_claim": {
                    "task_id": TASK,
                    "claimant_id": "a-alice",
                    "respondent_id": "a-bob",
                    "reason": REASON,
                },
                "expected_escrow": escrow,
            }
            result.update({"dispute_id": "clm-demo", "reason": REASON})
        result["effect_result_reference"] = reference
    canonical = {
        "protocol_version": 1,
        "task_id": TASK,
        "command": command,
        "expected_status": source,
        "expected_lifecycle_version": version,
        "actor_kind": "agent",
        "actor_id": "a-bob" if action == "submit_deliverable" else "a-alice",
        "arguments": arguments,
    }
    operation = {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": TASK,
        "command": command,
        "request_sha256": _hash(canonical),
        "operation_state": "claimed" if pending else "completed",
        "source_status": source,
        "source_lifecycle_version": version,
        "target_status": target,
        "target_lifecycle_version": version + 1,
        "effect_kind": effect_kind,
        "effect_state": "pending" if pending else "none" if effect_kind == "none" else "succeeded",
        "effect_operation_id": effect_id,
        "effect_result_reference": None if pending else reference,
        "effect_intent": intent,
        "event_id": None if pending else 10,
        "result": None if pending else result,
    }
    current = _task(source if pending else target, version if pending else version + 1)
    if pending:
        current["pending_transition"] = {
            key: operation[key]
            for key in (
                "operation_id",
                "command",
                "target_status",
                "target_lifecycle_version",
                "operation_state",
                "effect_state",
            )
        }
    return {**current, "operation": operation}


def _step(action: str) -> dict[str, Any]:
    result: dict[str, Any] = {"action": action, "task_ref": "task"}
    if action == "bid":
        result.update({"bidder": "bob", "amount": 90})
    elif action == "accept_bid":
        result.update({"poster": "alice", "bidder": "bob"})
    elif action == "submit_deliverable":
        result["worker"] = "bob"
    else:
        result["poster"] = "alice"
        if action == "dispute":
            result["reason"] = REASON
    return result


def _engine(steps: list[dict[str, Any]]) -> ReplayEngine:
    config = PlatformConfig(
        identity_url="https://identity.invalid",
        bank_url="https://bank.invalid",
        task_board_url="https://board.invalid",
        reputation_url="https://reputation.invalid",
        court_url="https://court.invalid",
        platform_key_path=Path("unused-platform.key"),
        token_ttl_seconds=60,
    )
    first_action = steps[0]["action"] if steps else "bid"
    order = ["bid", "accept_bid", "submit_deliverable", "approve"]
    needed = order[: order.index(first_action)] if first_action in order else order[:3]
    post_step = {
        "action": "post_task",
        "poster": "alice",
        "ref": "task",
        "title": "Demo task",
        "spec": "Exact task",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 60,
        "execution_deadline_seconds": 120,
        "review_deadline_seconds": 180,
    }
    producers = [_step(action) for action in needed]
    scenario = {
        "name": "Native demo",
        "default_delay": 0,
        "agents": [
            {"handle": "alice", "name": "Alice"},
            {"handle": "bob", "name": "Bob"},
        ],
        "steps": [post_step, *producers, *steps],
    }
    engine = ReplayEngine(scenario, config)
    state = Path(tempfile.mkdtemp(prefix="mp05-native-unit-")).resolve()
    store = RunStore.open(
        state_directory=state,
        run_id=f"run-{UUID(int=id(engine) % (1 << 128))}",
        environment=RunEnvironment(
            environment_id="native-unit",
            identity_url=config.identity_url,
            bank_url=config.bank_url,
            task_board_url=config.task_board_url,
            reputation_url=config.reputation_url,
            court_url=config.court_url,
            platform_public_fingerprint_sha256="c" * 64,
        ),
        scenario_bytes=yaml.safe_dump(scenario, sort_keys=False).encode(),
        scenario=scenario,
        actor_definitions=(
            ActorDefinition(handle="alice", name="Alice"),
            ActorDefinition(handle="bob", name="Bob"),
        ),
        effective_delay_seconds=0.0,
        max_record_bytes=1_048_576,
        diagnostic_limit=32,
    )
    for handle in ("alice", "bob"):
        actor = store.actors[handle]
        store.record_registration(
            actor_handle=handle,
            response={
                "agent_id": f"a-{handle}",
                "name": actor.name,
                "public_key": actor.public_key,
                "registered_at": AT,
            },
        )
    engine.agents = {}
    for handle, actor in store.actors.items():
        loaded = DemoAgent.from_pem(handle, actor.name, actor.private_key_path, 60)
        loaded.agent_id = actor.agent_id
        engine.agents[handle] = loaded
    engine._store = store
    _seed_posting(store, post_step)
    for offset, producer in enumerate(producers, start=1):
        _seed_native(store, offset, producer, engine.agents)
    engine._refs["task"] = TASK
    return engine


def _seed_posting(store: RunStore, step: dict[str, Any]) -> None:
    intent = TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": "op-00000000-0000-4000-8000-000000000090",
            "task_id": TASK,
            "actor_kind": "agent",
            "actor_id": "a-alice",
            "lock_operation_id": operation_id_for("task-lock", (TASK, "a-alice")),
            "title": step["title"],
            "spec": step["spec"],
            "reward": step["reward"],
            "bidding_deadline_seconds": step["bidding_deadline_seconds"],
            "acceptance_window_seconds": step["acceptance_window_seconds"],
            "execution_deadline_seconds": step["execution_deadline_seconds"],
            "review_deadline_seconds": step["review_deadline_seconds"],
        }
    )
    response = _published_response(intent)
    prepared = store.reserve_posting(index=0, step=step, actor_handle="alice", intent=intent)
    sent = store.mark_dispatching(index=0, expected_revision=prepared.revision)
    store.complete_posting(index=0, expected_revision=sent.revision, response=response)


def _published_response(intent: TaskPostingIntent) -> TaskPostingResponse:
    created = datetime(2026, 10, 18, 12, tzinfo=UTC)

    def stamp(value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def micros(value: datetime) -> int:
        return int(value.timestamp() * 1_000_000)

    result = {
        "task_id": intent.task_id,
        "poster_id": intent.actor_id,
        "title": intent.title,
        "spec": intent.spec,
        "reward": intent.reward,
        "bidding_deadline_seconds": intent.bidding_deadline_seconds,
        "acceptance_window_seconds": intent.acceptance_window_seconds,
        "execution_deadline_seconds": intent.execution_deadline_seconds,
        "review_deadline_seconds": intent.review_deadline_seconds,
        "status": "open",
        "escrow_id": "esc-demo",
        "created_at": stamp(created),
        "bidding_deadline": stamp(created + timedelta(seconds=intent.bidding_deadline_seconds)),
        "acceptance_deadline": stamp(
            created
            + timedelta(seconds=intent.bidding_deadline_seconds + intent.acceptance_window_seconds)
        ),
        "bidding_cutoff_us": micros(created + timedelta(seconds=intent.bidding_deadline_seconds)),
        "acceptance_cutoff_us": micros(
            created
            + timedelta(seconds=intent.bidding_deadline_seconds + intent.acceptance_window_seconds)
        ),
        "lifecycle_version": 0,
        "timing_contract_version": 2,
        "event_id": 1,
    }
    body = {
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "request_sha256": intent.request_sha256,
        "publication_state": "published",
        "lock_operation_id": intent.lock_operation_id,
        "escrow_id": "esc-demo",
        "admitted_at": stamp(created),
        "published_at": stamp(created),
        "result": result,
        "error": None,
    }
    return TaskPostingResponse(201, body)


def _seed_native(
    store: RunStore,
    index: int,
    step: dict[str, Any],
    agents: dict[str, DemoAgent],
) -> None:
    action = step["action"]
    command, source, _target, version = COMMANDS[action]
    del command
    actor_handle = "bob" if action in {"bid", "submit_deliverable"} else "alice"
    arguments: dict[str, Any] = {}
    if action == "bid":
        arguments = {"amount": 90}
    elif action == "accept_bid":
        arguments = {
            "bid_id": BID,
            "worker_id": "a-bob",
            "bidder_handle": "bob",
            "bid_amount": 90,
        }
    operation_id = (
        OPERATION if action == "bid" else f"op-00000000-0000-4000-8000-00000000009{index}"
    )
    intent = TaskIntent(
        action=action,
        step_json=canonical_json(step),
        operation_id=operation_id,
        expected_lifecycle_version=version,
        actor_handle=actor_handle,
        actor_id=f"a-{actor_handle}",
        actor_public_key=agents[actor_handle].public_key_string(),
        task_id=TASK,
        arguments_json=canonical_json(arguments),
        observed_task_json=canonical_json(_task(source, version)),
    )
    prepared = store.reserve_step(
        index=index,
        step=step,
        actor_handle=actor_handle,
        intent=intent.as_json(),
    )
    sent = store.mark_dispatching(index=index, expected_revision=prepared.revision)
    response = _response(action, intent.operation_id, version, pending=False)
    if action == "bid":
        result: dict[str, object] = {"task_id": TASK, "bid": response}
    elif action == "accept_bid":
        result = {"task_id": TASK, "bidder_handle": "bob", "response": response}
    else:
        result = {"task_id": TASK, "response": response}
    store.finish_step(
        index=index,
        expected_revision=sent.revision,
        state="completed",
        result=result,
        diagnostic=None,
    )


def _printed(console: MagicMock) -> str:
    return " ".join(str(value) for call in console.print.call_args_list for value in call.args)


async def _invoke_client(
    name: str, client: httpx.AsyncClient, operation_id: object, version: object
) -> dict[str, Any]:
    actor = _agent("bob" if name in {"submit_bid", "submit_deliverable"} else "alice")
    arguments: list[Any] = [client, actor, TASK]
    if name == "submit_bid":
        arguments.append(90)
    elif name == "accept_bid":
        arguments.append(BID)
    elif name == "dispute_task":
        arguments.append(REASON)
    arguments.append("https://board.invalid")
    method: Any = getattr(clients, name)
    response: dict[str, Any] = await method(
        *arguments, operation_id=operation_id, expected_lifecycle_version=version
    )
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["submit_bid", "accept_bid", "submit_deliverable", "approve_task", "dispute_task"]
)
async def test_clients_sign_explicit_native_identity_without_retry(name: str) -> None:
    requests: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"received": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_capture)) as client:
        await _invoke_client(name, client, OPERATION, 2)
    assert len(requests) == 1
    signed = parse_jws(json.loads(requests[0].content)["token"])
    assert signed.payload["operation_id"] == OPERATION
    assert signed.payload["expected_lifecycle_version"] == 2
    assert signed.payload["action"] == name
    assert signed.header.aud == "task-board"
    assert signed.header.resource == f"POST {requests[0].url.path}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_id", "version"),
    [
        ("invalid", 0),
        (OPERATION, True),
        (OPERATION, -1),
        (OPERATION, "2"),
        (OPERATION, SQLITE_MAX_INTEGER + 1),
    ],
)
async def test_invalid_native_identity_never_reaches_http(
    operation_id: object, version: object
) -> None:
    requests: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_capture)) as client:
        with pytest.raises(ValueError):
            await _invoke_client("submit_bid", client, operation_id, version)
    assert requests == []


@pytest.mark.asyncio
async def test_posting_signs_the_explicit_poster_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"task_id": TASK})

    monkeypatch.setattr(
        "service_auth.mixins.task_board.parse_task_posting_submit_response",
        lambda response, intent: TaskPostingResponse(response.status_code, {"accepted": True}),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(_capture)) as client:
        poster = _agent("alice")
        poster.configure_task_board(client, "https://board.invalid")
        intent = poster.prepare_task_posting(
            "Title",
            "Spec",
            100,
            120,
            45,
            900,
            240,
        )
        await poster.submit_task_posting(intent)
    signed = parse_jws(json.loads(requests[0].content)["task_token"])
    assert signed.payload["acceptance_window_seconds"] == 45
    assert signed.payload["bidding_deadline_seconds"] == 120
    assert signed.payload["execution_deadline_seconds"] == 900
    assert signed.payload["review_deadline_seconds"] == 240


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [0, True, 1.5, "45", SQLITE_MAX_INTEGER // 1_000_000 + 1])
async def test_invalid_window_never_reaches_http(window: object) -> None:
    client = AsyncMock()
    poster = _agent("alice")
    poster.configure_task_board(client, "https://board.invalid")
    with pytest.raises(ValueError):
        poster.prepare_task_posting(
            "Title",
            "Spec",
            100,
            120,
            window,
            900,
            240,
        )
    client.request.assert_not_awaited()


@pytest.mark.parametrize("window", [None, 0, True, "45"])
def test_scenario_rejects_missing_or_invalid_acceptance_window(
    tmp_path: Path, window: object
) -> None:
    step: dict[str, Any] = {
        "action": "post_task",
        "poster": "alice",
        "title": "Task",
        "spec": "Spec",
        "reward": 100,
        "bidding_deadline_seconds": 120,
        "execution_deadline_seconds": 900,
        "review_deadline_seconds": 240,
    }
    if window is not None:
        step["acceptance_window_seconds"] = window
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump({"name": "Bad window", "agents": [], "steps": [step]}))
    with pytest.raises(ValueError):
        load_scenario(path)


@pytest.mark.asyncio
async def test_engine_forwards_all_explicit_posting_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    step = {
        "action": "post_task",
        "poster": "alice",
        "title": "Task",
        "spec": "Spec",
        "reward": 100,
        "bidding_deadline_seconds": 120,
        "execution_deadline_seconds": 900,
        "review_deadline_seconds": 240,
        "acceptance_window_seconds": 45,
    }
    engine = _engine([step])
    poster = engine.agents["alice"]
    prepare = MagicMock(wraps=poster.prepare_task_posting)
    monkeypatch.setattr(poster, "prepare_task_posting", prepare)
    missing = httpx.Response(
        404,
        request=httpx.Request("GET", "https://board.invalid/task-publications/missing"),
    )
    monkeypatch.setattr(
        poster,
        "get_task_posting",
        AsyncMock(
            side_effect=httpx.HTTPStatusError("missing", request=missing.request, response=missing)
        ),
    )
    monkeypatch.setattr(
        poster,
        "submit_task_posting",
        AsyncMock(side_effect=lambda intent: _published_response(intent)),
    )

    await engine._execute_step(MagicMock(), step)

    prepare.assert_called_once_with("Task", "Spec", 100, 120, 45, 900, 240)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(COMMANDS))
async def test_new_engine_intent_observes_version_before_mutation(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    step = _step(action)
    engine = _engine([step])
    name, source, _target, version = COMMANDS[action]
    order: list[str] = []

    async def _read(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        order.append("read")
        return _task(source, version)

    async def _mutate(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        order.append("mutate")
        return _response(action, kwargs.get("operation_id", OPERATION), version, pending=False)

    read = AsyncMock(side_effect=_read)
    mutate = AsyncMock(side_effect=_mutate)
    monkeypatch.setattr(clients, "get_task", read)
    monkeypatch.setattr(clients, "list_bids", AsyncMock(return_value=[_flat_bid()]))
    monkeypatch.setattr(clients, name, mutate)
    await engine._execute_step(MagicMock(), step)
    assert order == ["read", "mutate"]
    assert mutate.call_args.kwargs["expected_lifecycle_version"] == version
    assert isinstance(mutate.call_args.kwargs["operation_id"], str)
    assert mutate.call_args.kwargs["operation_id"].startswith("op-")


@pytest.mark.asyncio
async def test_public_bid_stays_flat_in_engine_tracking(monkeypatch: pytest.MonkeyPatch) -> None:
    step = _step("bid")
    engine = _engine([step])
    monkeypatch.setattr("demo_replay.engine.uuid.uuid4", lambda: UUID(OPERATION[3:]))
    monkeypatch.setattr(clients, "get_task", AsyncMock(return_value=_task("open", 0)))
    monkeypatch.setattr(clients, "submit_bid", AsyncMock(return_value=_flat_bid()))
    await engine._execute_step(MagicMock(), step)
    assert engine._bids[TASK] == [_flat_bid()]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, True, -1, "2"])
async def test_bad_observed_version_stops_before_mutation(
    monkeypatch: pytest.MonkeyPatch, version: object
) -> None:
    step = _step("approve")
    engine = _engine([step])
    monkeypatch.setattr(clients, "get_task", AsyncMock(return_value=_task("submitted", version)))
    mutation = AsyncMock(return_value=_response("approve", OPERATION, 2, pending=False))
    monkeypatch.setattr(clients, "approve_task", mutation)
    with pytest.raises((ValueError, RuntimeError)):
        await engine._execute_step(MagicMock(), step)
    mutation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["accept_bid", "submit_deliverable", "approve", "dispute"])
@pytest.mark.parametrize(
    "defect", ["claimed", "missing_operation", "unknown_state", "wrong_identity", "missing_result"]
)
async def test_incomplete_or_malformed_mutation_cannot_advance(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    defect: str,
) -> None:
    step = _step(action)
    engine = _engine([step])
    name, source, _target, version = COMMANDS[action]
    console = MagicMock()
    monkeypatch.setattr(engine_module, "console", console)
    monkeypatch.setattr(clients, "get_task", AsyncMock(return_value=_task(source, version)))
    monkeypatch.setattr(clients, "list_bids", AsyncMock(return_value=[_flat_bid()]))

    async def _mutate(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        body = _response(
            action, kwargs.get("operation_id", OPERATION), version, pending=defect == "claimed"
        )
        if defect == "missing_operation":
            body.pop("operation")
        elif defect == "unknown_state":
            body["operation"]["operation_state"] = "unknown"
        elif defect == "wrong_identity":
            body["operation"]["operation_id"] = OTHER_OPERATION
        elif defect == "missing_result":
            body["operation"]["result"] = None
        return body

    mutation = AsyncMock(side_effect=_mutate)
    monkeypatch.setattr(clients, name, mutation)
    with pytest.raises((ValueError, RuntimeError)):
        await engine._execute_step(MagicMock(), step)
    mutation.assert_awaited_once()
    assert engine._worker_task == {}
    assert engine._disputes == {}
    output = _printed(console)
    assert "payout released" not in output
    assert "contract formed" not in output
    assert "submitted deliverables" not in output
    assert "disputed task" not in output


@pytest.mark.asyncio
async def test_response_loss_retry_retains_original_identity_and_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step("approve")
    engine = _engine([step])
    read = AsyncMock(side_effect=[_task("submitted", 2), _task("approved", 3)])
    attempts: list[dict[str, Any]] = []

    async def _mutate(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        attempts.append(copy.deepcopy(kwargs))
        if len(attempts) == 1:
            raise httpx.ReadError("Response lost after commit")
        return _response("approve", kwargs.get("operation_id", OPERATION), 2, pending=False)

    mutation = AsyncMock(side_effect=_mutate)
    monkeypatch.setattr(clients, "get_task", read)
    monkeypatch.setattr(clients, "approve_task", mutation)
    with pytest.raises(httpx.ReadError):
        await engine._execute_step(MagicMock(), step)
    await engine._execute_step(MagicMock(), step)
    assert read.await_count == 1
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert attempts[0]["expected_lifecycle_version"] == 2
    assert isinstance(attempts[0]["operation_id"], str)


@pytest.mark.asyncio
async def test_changed_terms_under_same_step_are_rejected_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step("dispute")
    engine = _engine([step])
    read = AsyncMock(return_value=_task("submitted", 2))
    mutation = AsyncMock(
        side_effect=[
            httpx.ReadError("Response lost"),
            _response("dispute", OPERATION, 2, pending=False),
        ]
    )
    monkeypatch.setattr(clients, "get_task", read)
    monkeypatch.setattr(clients, "dispute_task", mutation)
    with pytest.raises(httpx.ReadError):
        await engine._execute_step(MagicMock(), step)
    step["reason"] = "Changed terms for the same scenario step"
    with pytest.raises((ValueError, RuntimeError)):
        await engine._execute_step(MagicMock(), step)
    mutation.assert_awaited_once()
    read.assert_awaited_once()


@pytest.mark.asyncio
async def test_distinct_equal_steps_have_distinct_native_intents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _step("bid")
    second = copy.deepcopy(first)
    engine = _engine([first, second])
    read = AsyncMock(return_value=_task("open", 0))
    request = httpx.Request("POST", f"https://board.invalid/tasks/{TASK}/bids")
    conflict = httpx.HTTPStatusError(
        "A second new bid conflicts", request=request, response=httpx.Response(409, request=request)
    )
    deliveries = 0

    async def _submit(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal deliveries
        deliveries += 1
        if deliveries == 1:
            return {**_flat_bid(), "bid_id": f"bid-{kwargs['operation_id'][3:]}"}
        raise conflict

    mutation = AsyncMock(side_effect=_submit)
    monkeypatch.setattr(clients, "get_task", read)
    monkeypatch.setattr(clients, "submit_bid", mutation)
    await engine._execute_step(MagicMock(), first)
    with pytest.raises(httpx.HTTPStatusError):
        await engine._execute_step(MagicMock(), second)
    calls = mutation.await_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["operation_id"] != calls[1].kwargs["operation_id"]
    assert read.await_count == 2


@pytest.mark.asyncio
async def test_acceptance_retry_does_not_reselect_bid_or_refresh_live_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step("accept_bid")
    engine = _engine([step])
    read = AsyncMock(return_value=_task("open", 0))
    bids = AsyncMock(side_effect=[[_flat_bid()], [{**_flat_bid(), "bid_id": "bid-different"}]])
    attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def _accept(*args: Any, **kwargs: Any) -> dict[str, Any]:
        attempts.append((args, dict(kwargs)))
        if len(attempts) == 1:
            raise httpx.ReadError("Acceptance committed but response lost")
        return _response("accept_bid", kwargs["operation_id"], 0, pending=False)

    monkeypatch.setattr(clients, "get_task", read)
    monkeypatch.setattr(clients, "list_bids", bids)
    monkeypatch.setattr(clients, "accept_bid", AsyncMock(side_effect=_accept))
    http = MagicMock()
    with pytest.raises(httpx.ReadError):
        await engine._execute_step(http, step)
    engine._refs["task"] = "t-later-task"
    await engine._execute_step(http, step)
    assert attempts[0] == attempts[1]
    assert attempts[0][0][2:4] == (TASK, BID)
    assert attempts[0][1]["expected_lifecycle_version"] == 0
    read.assert_awaited_once()
    bids.assert_awaited_once()
    assert engine._worker_task == {"bob": TASK}


@pytest.mark.asyncio
async def test_boolean_amount_cannot_masquerade_as_frozen_numeric_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step("approve")
    engine = _engine([step])
    observed = _task("submitted", 2)
    observed.update({"reward": 1, "accepted_reward": 1, "accepted_bid_amount": 1})
    monkeypatch.setattr(clients, "get_task", AsyncMock(return_value=observed))
    console = MagicMock()
    monkeypatch.setattr(engine_module, "console", console)

    async def _approve(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        body = _response("approve", kwargs["operation_id"], 2, pending=False)
        body.update({"reward": 1, "accepted_reward": 1, "accepted_bid_amount": 1})
        operation = body["operation"]
        operation["result"]["reward"] = 1
        operation["effect_intent"]["expected_escrow"]["amount"] = True
        operation["request_sha256"] = _hash(
            {
                "protocol_version": 1,
                "task_id": TASK,
                "command": "approve_task",
                "expected_status": "submitted",
                "expected_lifecycle_version": 2,
                "actor_kind": "agent",
                "actor_id": "a-alice",
                "arguments": {
                    "poster_id": "a-alice",
                    "worker_id": "a-bob",
                    "escrow_id": "esc-demo",
                    "reward": 1,
                    "source_reference": f"task:{TASK}:approved",
                },
            }
        )
        return body

    monkeypatch.setattr(clients, "approve_task", AsyncMock(side_effect=_approve))
    with pytest.raises((ValueError, RuntimeError)):
        await engine._execute_step(MagicMock(), step)
    assert "payout released" not in _printed(console)


@pytest.mark.asyncio
async def test_changed_action_cannot_escape_an_ambiguous_step_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step("approve")
    engine = _engine([step])
    monkeypatch.setattr(clients, "get_task", AsyncMock(return_value=_task("submitted", 2)))
    approval = AsyncMock(side_effect=httpx.ReadError("Approval response lost"))
    feedback = AsyncMock(return_value={"feedback_id": "f-changed-step"})
    monkeypatch.setattr(clients, "approve_task", approval)
    monkeypatch.setattr(clients, "submit_feedback", feedback)
    with pytest.raises(httpx.ReadError):
        await engine._execute_step(MagicMock(), step)
    step.clear()
    step.update(
        {
            "action": "feedback",
            "agent": "alice",
            "task_ref": "task",
            "to_agent_id": "bob",
            "category": "spec_quality",
            "rating": "satisfied",
            "comment": "Changed action must not bypass the pending step",
        }
    )
    with pytest.raises(ValueError, match="intent changed"):
        await engine._execute_step(MagicMock(), step)
    approval.assert_awaited_once()
    feedback.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_bid_identity_cannot_complete_native_bid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step("bid")
    engine = _engine([step])
    console = MagicMock()
    monkeypatch.setattr(engine_module, "console", console)
    monkeypatch.setattr(clients, "get_task", AsyncMock(return_value=_task("open", 0)))

    async def _foreign_bid(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        response = _flat_bid()
        expected_id = f"bid-{kwargs['operation_id'][3:]}"
        response["bid_id"] = "bid-00000000-0000-4000-8000-000000000999"
        assert response["bid_id"] != expected_id
        return response

    monkeypatch.setattr(clients, "submit_bid", AsyncMock(side_effect=_foreign_bid))
    with pytest.raises((ValueError, RuntimeError)):
        await engine._execute_step(MagicMock(), step)
    assert engine._bids == {}
    assert "bid 90 coins" not in _printed(console)
