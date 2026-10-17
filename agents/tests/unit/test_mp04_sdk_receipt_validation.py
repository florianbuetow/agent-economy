"""Financial SDK consumers reject success bodies that do not prove their command."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from service_auth.financial_identity import operation_id_for

from base_agent.agent import BaseAgent
from base_agent.platform import PlatformAgent
from fund_feeder_cli import __main__ as fund_feeder_main
from treasury_provision_cli import __main__ as treasury_main
from treasury_provision_cli.config import TreasuryConfig

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_ISSUANCE_ID = "op-00000000-0000-4000-8000-000000000101"
_CREDIT_ID = "op-00000000-0000-4000-8000-000000000102"
_LOCK_ID = "op-00000000-0000-4000-8000-000000000103"
_RELEASE_ID = "op-00000000-0000-4000-8000-000000000104"
_SPLIT_ID = "op-00000000-0000-4000-8000-000000000105"
_COMMITTED_AT = "2026-10-17T15:00:00Z"
_DELETE = object()

type PathPart = str | int


def _request_hash(operation_kind: str, fields: dict[str, object]) -> str:
    canonical = {
        "request_version": 1,
        "operation_kind": operation_kind,
        **fields,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _envelope(
    operation_id: str,
    operation_kind: str,
    source_reference: str,
    business_fields: dict[str, object],
    result: dict[str, object],
    legs: list[dict[str, object]],
    current_escrow: dict[str, object] | None,
) -> dict[str, Any]:
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            "request_sha256": _request_hash(operation_kind, business_fields),
            "source_reference": source_reference,
            "committed_at": _COMMITTED_AT,
            "event_id": 17,
            "escrow_id": None if current_escrow is None else current_escrow["escrow_id"],
            "result": result,
            "legs": legs,
        },
        "current_escrow": current_escrow,
    }


def _projection(
    status: str,
    amount: int,
    payer_account_id: str,
    task_id: str,
) -> dict[str, object]:
    return {
        "escrow_id": "esc-one",
        "payer_account_id": payer_account_id,
        "task_id": task_id,
        "amount": amount,
        "status": status,
        "created_at": "2026-10-17T14:59:00Z",
        "resolved_at": None if status == "locked" else _COMMITTED_AT,
    }


def _issuance_response() -> dict[str, Any]:
    return _envelope(
        _ISSUANCE_ID,
        "account_issuance",
        "initial_balance",
        {"account_id": "a-issued", "amount": 100, "reference": "initial_balance"},
        {"account_id": "a-issued", "issued_amount": 100, "balance_after": 100},
        [
            {
                "role": "account",
                "account_id": "a-issued",
                "direction": "credit",
                "amount": 100,
                "transaction_id": "tx-issuance",
                "balance_after": 100,
            }
        ],
        None,
    )


def _credit_response(
    operation_id: str,
    account_id: str,
    amount: int,
    reference: str,
) -> dict[str, Any]:
    return _envelope(
        operation_id,
        "credit",
        reference,
        {"account_id": account_id, "amount": amount, "reference": reference},
        {
            "account_id": account_id,
            "amount": amount,
            "balance_after": amount,
            "reference": reference,
        },
        [
            {
                "role": "account",
                "account_id": account_id,
                "direction": "credit",
                "amount": amount,
                "transaction_id": "tx-credit",
                "balance_after": amount,
            }
        ],
        None,
    )


def _lock_response(status: str) -> dict[str, Any]:
    projection = _projection(status, 80, "a-poster", "t-one")
    return _envelope(
        _LOCK_ID,
        "escrow_lock",
        "t-one",
        {"payer_account_id": "a-poster", "task_id": "t-one", "amount": 80},
        {"escrow_status_at_commit": "locked"},
        [
            {
                "role": "payer",
                "account_id": "a-poster",
                "direction": "debit",
                "amount": 80,
                "transaction_id": "tx-lock",
                "balance_after": 20,
            }
        ],
        projection,
    )


def _release_response() -> dict[str, Any]:
    projection = _projection("released", 50, "a-poster", "t-one")
    return _envelope(
        _RELEASE_ID,
        "escrow_release",
        "task:t-one:approved",
        {
            "escrow_id": "esc-one",
            "recipient_account_id": "a-worker",
            "source_reference": "task:t-one:approved",
        },
        {"escrow_status_at_commit": "released"},
        [
            {
                "role": "recipient",
                "account_id": "a-worker",
                "direction": "credit",
                "amount": 50,
                "transaction_id": "tx-release",
                "balance_after": 50,
            }
        ],
        projection,
    )


def _split_response(worker_pct: int) -> dict[str, Any]:
    worker_amount = worker_pct
    poster_amount = 100 - worker_pct
    projection = _projection("split", 100, "a-poster", "t-one")
    return _envelope(
        _SPLIT_ID,
        "escrow_split",
        "task:t-one:ruling:r-one",
        {
            "escrow_id": "esc-one",
            "worker_account_id": "a-worker",
            "poster_account_id": "a-poster",
            "worker_pct": worker_pct,
            "source_reference": "task:t-one:ruling:r-one",
        },
        {"escrow_status_at_commit": "split", "worker_pct": worker_pct},
        [
            {
                "role": "worker",
                "account_id": "a-worker",
                "direction": "credit",
                "amount": worker_amount,
                "transaction_id": None if worker_amount == 0 else "tx-worker",
                "balance_after": None if worker_amount == 0 else worker_amount,
            },
            {
                "role": "poster",
                "account_id": "a-poster",
                "direction": "credit",
                "amount": poster_amount,
                "transaction_id": None if poster_amount == 0 else "tx-poster",
                "balance_after": None if poster_amount == 0 else poster_amount,
            },
        ],
        projection,
    )


def _corrupt(
    response: dict[str, Any],
    path: tuple[PathPart, ...],
    replacement: object,
) -> dict[str, Any]:
    changed = copy.deepcopy(response)
    current: Any = changed
    for part in path[:-1]:
        current = current[part]
    final = path[-1]
    if replacement is _DELETE:
        del current[final]
    else:
        current[final] = replacement
    return changed


async def _platform_call(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    agent = PlatformAgent(config)
    agent.agent_id = "a-platform"
    monkeypatch.setattr(agent, "_request", AsyncMock(return_value=response))
    try:
        if operation == "issuance":
            return await agent.create_account(
                "a-issued",
                100,
                operation_id=_ISSUANCE_ID,
            )
        if operation == "credit":
            return await agent.credit_account(
                "a-worker",
                100,
                "funding-one",
                operation_id=_CREDIT_ID,
            )
        if operation == "release":
            return await agent.release_escrow(
                "esc-one",
                "a-worker",
                operation_id=_RELEASE_ID,
                source_reference="task:t-one:approved",
            )
        return await agent.split_escrow(
            "esc-one",
            "a-worker",
            "a-poster",
            40,
            operation_id=_SPLIT_ID,
            source_reference="task:t-one:ruling:r-one",
        )
    finally:
        await agent.close()


async def _zero_account_call(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    agent_kind: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    agent: BaseAgent = PlatformAgent(config) if agent_kind == "platform" else BaseAgent(config)
    agent.agent_id = "a-zero"
    monkeypatch.setattr(agent, "_request", AsyncMock(return_value=response))
    try:
        if isinstance(agent, PlatformAgent):
            return await agent.create_account("a-zero", 0, operation_id=None)
        return await agent.create_account()
    finally:
        await agent.close()


async def _base_lock_call(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any],
) -> dict[str, Any]:
    agent = BaseAgent(config)
    agent.agent_id = "a-poster"
    monkeypatch.setattr(agent, "_request", AsyncMock(return_value=response))
    try:
        return await agent.lock_escrow(80, "t-one", operation_id=_LOCK_ID)
    finally:
        await agent.close()


@pytest.mark.parametrize("agent_kind", ["base", "platform"])
async def test_zero_account_accepts_positive_current_balance_after_concurrent_credit(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    agent_kind: str,
) -> None:
    response = {
        "account_id": "a-zero",
        "balance": 7,
        "created_at": _COMMITTED_AT,
    }
    assert await _zero_account_call(sample_config, monkeypatch, agent_kind, response) == response


@pytest.mark.parametrize("agent_kind", ["base", "platform"])
@pytest.mark.parametrize(
    "response",
    [
        {"account_id": "a-other", "balance": 0, "created_at": _COMMITTED_AT},
        {"account_id": "a-zero", "balance": True, "created_at": _COMMITTED_AT},
        {
            "account_id": "a-zero",
            "balance": 0,
            "created_at": _COMMITTED_AT,
            "unexpected": "field",
        },
        {"account_id": "a-zero", "balance": 0},
    ],
)
async def test_zero_account_rejects_corrupt_projection(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    agent_kind: str,
    response: dict[str, Any],
) -> None:
    with pytest.raises(RuntimeError):
        await _zero_account_call(sample_config, monkeypatch, agent_kind, response)


@pytest.mark.parametrize(
    ("operation", "response"),
    [
        ("issuance", _issuance_response()),
        ("credit", _credit_response(_CREDIT_ID, "a-worker", 100, "funding-one")),
        ("release", _release_response()),
        ("split", _split_response(40)),
    ],
)
async def test_platform_financial_operations_accept_truthful_native_receipts(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    response: dict[str, Any],
) -> None:
    assert await _platform_call(sample_config, monkeypatch, operation, response) == response


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("receipt", "request_sha256"), "f" * 64),
        (("receipt", "source_reference"), "changed-source"),
        (("receipt", "result", "account_id"), "a-other"),
        (("receipt", "result", "issued_amount"), 101),
        (("receipt", "legs", 0, "account_id"), "a-other"),
        (("receipt", "legs", 0, "amount"), 101),
        (("receipt", "committed_at"), _DELETE),
        (("receipt", "receipt_version"), True),
        (("receipt", "event_id"), True),
        (("receipt", "event_id"), 0),
    ],
)
async def test_platform_issuance_rejects_corrupt_native_receipts(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[PathPart, ...],
    replacement: object,
) -> None:
    response = _corrupt(_issuance_response(), path, replacement)
    with pytest.raises(RuntimeError):
        await _platform_call(sample_config, monkeypatch, "issuance", response)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("receipt", "request_sha256"), "f" * 64),
        (("receipt", "source_reference"), "changed-source"),
        (("receipt", "result", "account_id"), "a-other"),
        (("receipt", "result", "amount"), 99),
        (("receipt", "result", "reference"), "changed-reference"),
        (("receipt", "legs", 0, "account_id"), "a-other"),
        (("receipt", "legs", 0, "amount"), 99),
        (("receipt", "legs", 0, "balance_after"), 101),
        (("current_escrow",), _projection("locked", 100, "a-worker", "t-other")),
        (("receipt", "result"), _DELETE),
    ],
)
async def test_platform_credit_rejects_corrupt_native_receipts(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[PathPart, ...],
    replacement: object,
) -> None:
    response = _corrupt(
        _credit_response(_CREDIT_ID, "a-worker", 100, "funding-one"),
        path,
        replacement,
    )
    with pytest.raises(RuntimeError):
        await _platform_call(sample_config, monkeypatch, "credit", response)


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("receipt", "request_sha256"), "f" * 64),
        (("receipt", "source_reference"), "changed-source"),
        (("receipt", "escrow_id"), "esc-other"),
        (("receipt", "result", "escrow_status_at_commit"), "locked"),
        (("receipt", "legs", 0, "account_id"), "a-other"),
        (("receipt", "legs", 0, "amount"), 49),
        (("current_escrow", "amount"), 49),
        (("current_escrow", "status"), "locked"),
        (("current_escrow", "resolved_at"), None),
        (("current_escrow",), _DELETE),
    ],
)
async def test_platform_release_rejects_corrupt_native_receipts(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[PathPart, ...],
    replacement: object,
) -> None:
    response = _corrupt(_release_response(), path, replacement)
    with pytest.raises(RuntimeError):
        await _platform_call(sample_config, monkeypatch, "release", response)


async def test_base_agent_lock_rejects_paired_empty_generated_escrow_identity(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _corrupt(_lock_response("locked"), ("receipt", "escrow_id"), "")
    response = _corrupt(response, ("current_escrow", "escrow_id"), "")

    with pytest.raises(RuntimeError):
        await _base_lock_call(sample_config, monkeypatch, response)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("payer_account_id", ""), ("task_id", ""), ("created_at", None)],
)
async def test_platform_release_rejects_invalid_required_projection_fields(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    replacement: object,
) -> None:
    response = _corrupt(_release_response(), ("current_escrow", field), replacement)

    with pytest.raises(RuntimeError):
        await _platform_call(sample_config, monkeypatch, "release", response)


@pytest.mark.parametrize(
    ("operation", "response"),
    [("release", _release_response()), ("split", _split_response(40))],
)
async def test_native_settlement_preserves_empty_historical_creation_time(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    response: dict[str, Any],
) -> None:
    historical_projection = _corrupt(response, ("current_escrow", "created_at"), "")
    assert (
        await _platform_call(sample_config, monkeypatch, operation, historical_projection)
        == historical_projection
    )


@pytest.mark.parametrize("operation", ["terminal_lock", "release"])
async def test_settlement_projection_rejects_empty_nonnull_resolution_time(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    if operation == "terminal_lock":
        response = _corrupt(
            _lock_response("released"),
            ("current_escrow", "resolved_at"),
            "",
        )
        call = _base_lock_call(sample_config, monkeypatch, response)
    else:
        response = _corrupt(
            _release_response(),
            ("current_escrow", "resolved_at"),
            "",
        )
        call = _platform_call(sample_config, monkeypatch, "release", response)

    with pytest.raises(RuntimeError):
        await call


@pytest.mark.parametrize(
    ("operation", "path"),
    [
        ("lock", ("receipt", "committed_at")),
        ("lock", ("receipt", "escrow_id")),
        ("lock", ("current_escrow", "created_at")),
        ("terminal_lock", ("current_escrow", "resolved_at")),
        ("release", ("current_escrow", "payer_account_id")),
        ("release", ("current_escrow", "task_id")),
        ("release", ("receipt", "legs", 0, "transaction_id")),
    ],
)
async def test_sdk_rejects_invalid_utf8_in_required_financial_text(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    path: tuple[PathPart, ...],
) -> None:
    if operation == "release":
        response = _corrupt(_release_response(), path, "\ud800")
        call = _platform_call(sample_config, monkeypatch, "release", response)
    else:
        status = "released" if operation == "terminal_lock" else "locked"
        response = _corrupt(_lock_response(status), path, "\ud800")
        if path == ("receipt", "escrow_id"):
            response = _corrupt(response, ("current_escrow", "escrow_id"), "\ud800")
        call = _base_lock_call(sample_config, monkeypatch, response)

    with pytest.raises(RuntimeError):
        await call


@pytest.mark.parametrize("worker_pct", [0, 40, 100])
async def test_platform_split_accepts_zero_partial_and_full_native_receipts(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    worker_pct: int,
) -> None:
    response = _split_response(worker_pct)
    agent = PlatformAgent(sample_config)
    agent.agent_id = "a-platform"
    monkeypatch.setattr(agent, "_request", AsyncMock(return_value=response))
    try:
        result = await agent.split_escrow(
            "esc-one",
            "a-worker",
            "a-poster",
            worker_pct,
            operation_id=_SPLIT_ID,
            source_reference="task:t-one:ruling:r-one",
        )
    finally:
        await agent.close()
    assert result == response


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("receipt", "request_sha256"), "f" * 64),
        (("receipt", "source_reference"), "changed-source"),
        (("receipt", "result", "worker_pct"), 41),
        (("receipt", "legs", 0, "role"), "poster"),
        (("receipt", "legs", 0, "account_id"), "a-other"),
        (("receipt", "legs", 0, "amount"), 41),
        (("current_escrow", "payer_account_id"), "a-other"),
        (("current_escrow", "amount"), 101),
        (("current_escrow", "status"), "released"),
        (("receipt", "legs"), _DELETE),
    ],
)
async def test_platform_split_rejects_corrupt_native_receipts(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[PathPart, ...],
    replacement: object,
) -> None:
    response = _corrupt(_split_response(40), path, replacement)
    with pytest.raises(RuntimeError):
        await _platform_call(sample_config, monkeypatch, "split", response)


@pytest.mark.parametrize(
    ("leg_index", "field", "replacement"),
    [
        (0, "transaction_id", "tx-for-zero"),
        (0, "balance_after", 0),
        (1, "transaction_id", "tx-for-zero"),
        (1, "balance_after", 0),
    ],
)
async def test_platform_split_rejects_materialized_zero_legs(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    leg_index: int,
    field: str,
    replacement: object,
) -> None:
    worker_pct = 0 if leg_index == 0 else 100
    response = _corrupt(
        _split_response(worker_pct),
        ("receipt", "legs", leg_index, field),
        replacement,
    )
    agent = PlatformAgent(sample_config)
    agent.agent_id = "a-platform"
    monkeypatch.setattr(agent, "_request", AsyncMock(return_value=response))
    try:
        with pytest.raises(RuntimeError):
            await agent.split_escrow(
                "esc-one",
                "a-worker",
                "a-poster",
                worker_pct,
                operation_id=_SPLIT_ID,
                source_reference="task:t-one:ruling:r-one",
            )
    finally:
        await agent.close()


@pytest.mark.parametrize("projection_status", ["locked", "released", "split"])
async def test_base_agent_lock_accepts_current_or_terminal_projection(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    projection_status: str,
) -> None:
    response = _lock_response(projection_status)
    agent = BaseAgent(sample_config)
    agent.agent_id = "a-poster"
    monkeypatch.setattr(agent, "_request", AsyncMock(return_value=response))
    try:
        result = await agent.lock_escrow(80, "t-one", operation_id=_LOCK_ID)
    finally:
        await agent.close()
    assert result == response


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("receipt", "request_sha256"), "f" * 64),
        (("receipt", "source_reference"), "changed-task"),
        (("receipt", "result", "escrow_status_at_commit"), "released"),
        (("receipt", "legs", 0, "account_id"), "a-other"),
        (("receipt", "legs", 0, "amount"), 79),
        (("receipt", "legs", 0, "direction"), "credit"),
        (("current_escrow", "payer_account_id"), "a-other"),
        (("current_escrow", "task_id"), "t-other"),
        (("current_escrow", "amount"), 79),
        (("current_escrow", "resolved_at"), _COMMITTED_AT),
        (("receipt", "result"), _DELETE),
    ],
)
async def test_base_agent_lock_rejects_corrupt_native_receipts(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[PathPart, ...],
    replacement: object,
) -> None:
    response = _corrupt(_lock_response("locked"), path, replacement)
    agent = BaseAgent(sample_config)
    agent.agent_id = "a-poster"
    monkeypatch.setattr(agent, "_request", AsyncMock(return_value=response))
    try:
        with pytest.raises(RuntimeError):
            await agent.lock_escrow(80, "t-one", operation_id=_LOCK_ID)
    finally:
        await agent.close()


def _real_cli_factory(
    config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    actor_id: str,
    corrupt_credit: dict[str, Any],
) -> tuple[MagicMock, AsyncMock, AsyncMock]:
    actor = BaseAgent(config)
    actor.agent_id = actor_id
    monkeypatch.setattr(actor, "register", AsyncMock())
    monkeypatch.setattr(actor, "get_balance", AsyncMock(return_value={"balance": 500}))
    actor_close = AsyncMock(wraps=actor._http.aclose)
    monkeypatch.setattr(actor._http, "aclose", actor_close)

    platform = PlatformAgent(config)
    platform.agent_id = "a-platform"
    monkeypatch.setattr(platform, "register", AsyncMock())
    platform_close = AsyncMock(wraps=platform._http.aclose)
    monkeypatch.setattr(platform._http, "aclose", platform_close)

    async def transport(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        del method, kwargs
        if url.endswith("/accounts"):
            return {"account_id": actor_id, "balance": 0, "created_at": _COMMITTED_AT}
        return copy.deepcopy(corrupt_credit)

    monkeypatch.setattr(platform, "_request", AsyncMock(side_effect=transport))
    factory = MagicMock()
    factory.create_agent.return_value = actor
    factory.platform_agent.return_value = platform
    return factory, actor_close, platform_close


async def test_fund_feeder_rejects_corrupt_receipt_before_success_and_closes_clients(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    corrupt = _corrupt(
        _credit_response(_CREDIT_ID, "a-feeder", 500, "manual-funding"),
        ("receipt", "request_sha256"),
        "f" * 64,
    )
    factory, actor_close, platform_close = _real_cli_factory(
        sample_config,
        monkeypatch,
        "a-feeder",
        corrupt,
    )
    caplog.set_level(logging.INFO, logger="fund_feeder_cli")
    try:
        with (
            patch.object(fund_feeder_main, "AgentFactory", return_value=factory),
            pytest.raises(RuntimeError),
        ):
            await fund_feeder_main._fund(500, _CREDIT_ID, "manual-funding")
    finally:
        actor_close.assert_awaited_once()
        platform_close.assert_awaited_once()

    assert capsys.readouterr().out == ""
    assert not any("Funding confirmed" in record.getMessage() for record in caplog.records)


async def test_treasury_rejects_corrupt_receipt_before_success_and_closes_clients(
    sample_config: AgentConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = TreasuryConfig(
        handle="operator",
        genesis_amount=500,
        genesis_reference="treasury-genesis",
    )
    operation_id = operation_id_for(
        "treasury-genesis",
        ("a-operator", settings.genesis_reference),
    )
    corrupt = _corrupt(
        _credit_response(operation_id, "a-operator", 500, settings.genesis_reference),
        ("receipt", "legs", 0, "amount"),
        499,
    )
    factory, actor_close, platform_close = _real_cli_factory(
        sample_config,
        monkeypatch,
        "a-operator",
        corrupt,
    )
    caplog.set_level(logging.INFO, logger="treasury_provision_cli")
    try:
        with (
            patch.object(treasury_main, "load_treasury_settings", return_value=settings),
            patch.object(treasury_main, "AgentFactory", return_value=factory),
            pytest.raises(RuntimeError),
        ):
            await treasury_main._provision()
    finally:
        actor_close.assert_awaited_once()
        platform_close.assert_awaited_once()

    assert capsys.readouterr().out == ""
    assert not any("funding confirmed" in record.getMessage().lower() for record in caplog.records)
