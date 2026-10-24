"""Router test fixtures with mocked PlatformAgent and Central Bank services."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import closing, suppress
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from cryptography.exceptions import InvalidSignature as _InvalidSignature
from db_gateway_service.board_authority import BoardAuthority
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.task_time import utc_now_us
from httpx import ASGITransport, AsyncClient
from service_auth.financial_identity import operation_id_for
from service_auth.jws import JwsAdmissionError, VerifiedJws, parse_jws, verify_jws_for_signer
from service_auth.platform import PlatformAgent
from service_auth.signing import generate_keypair as provision_keypair
from service_auth.signing import public_key_to_b64
from service_commons.exceptions import ServiceError

from task_board_service.app import create_app
from task_board_service.config import clear_settings_cache, get_settings
from task_board_service.core.lifespan import lifespan
from task_board_service.core.state import get_app_state, reset_app_state
from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.escrow_coordinator import EscrowCoordinator
from task_board_service.services.financial_effect_recovery import FinancialEffectRecovery
from task_board_service.services.posting_recovery import PostingRecovery
from task_board_service.services.task_manager import TaskManager
from task_board_service.services.token_validator import TokenValidator
from tests.fakes.gateway_task_store import GatewayTaskStore
from tests.fakes.router_gateway_effects import RouterGatewayEffects
from tests.helpers import generate_keypair, make_jws_token

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# ---------------------------------------------------------------------------
# Fixed agent IDs
# ---------------------------------------------------------------------------
PLATFORM_AGENT_ID = "a-platform-test-id"
ALICE_AGENT_ID = "a-alice-uuid"
BOB_AGENT_ID = "a-bob-uuid"
CAROL_AGENT_ID = "a-carol-uuid"


def _write_platform_agent_config(tmp_path: Path) -> Path:
    keys_dir = tmp_path / "agent-keys"
    provision_keypair("platform", keys_dir)
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(
        yaml.safe_dump({"agents": {"platform": {"name": "Platform", "type": "platform"}}})
    )
    agent_config_path = tmp_path / "agents.yaml"
    agent_config_path.write_text(
        yaml.safe_dump(
            {
                "platform": {
                    "identity_url": "http://localhost:8001",
                    "bank_url": "http://localhost:8002",
                    "task_board_url": "http://localhost:8003",
                    "reputation_url": "http://localhost:8004",
                    "court_url": "http://localhost:8005",
                },
                "data": {
                    "keys_dir": str(keys_dir),
                    "roster_path": str(roster_path),
                },
                "signing": {
                    "token_ttl_seconds": 300,
                    "max_lifetime_seconds": 300,
                    "clock_skew_seconds": 0,
                },
            }
        )
    )
    return agent_config_path


async def _register_test_platform(agent: PlatformAgent) -> dict[str, str]:
    agent.agent_id = PLATFORM_AGENT_ID
    return {"agent_id": PLATFORM_AGENT_ID}


# ---------------------------------------------------------------------------
# ID generators
# ---------------------------------------------------------------------------
def make_agent_id() -> str:
    """Generate a unique agent ID."""
    return f"a-{uuid.uuid4()}"


def make_task_id() -> str:
    """Generate a unique task ID."""
    return f"t-{uuid.uuid4()}"


def make_bid_id() -> str:
    """Generate a unique bid ID."""
    return f"bid-{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# Keypair fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def platform_keypair(tmp_path: Path) -> tuple[Ed25519PrivateKey, str]:
    """Use the same owned platform key in configuration, signatures and Gateway authority."""
    private_key, public_key = provision_keypair("platform", tmp_path / "agent-keys")
    return private_key, f"ed25519:{public_key_to_b64(public_key)}"


@pytest.fixture
def alice_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate Alice's keypair."""
    return generate_keypair()


@pytest.fixture
def bob_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate Bob's keypair."""
    return generate_keypair()


@pytest.fixture
def carol_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate Carol's keypair."""
    return generate_keypair()


@pytest.fixture
def platform_agent_id() -> str:
    """Return the platform agent ID."""
    return PLATFORM_AGENT_ID


@pytest.fixture
def alice_agent_id() -> str:
    """Return Alice's agent ID."""
    return ALICE_AGENT_ID


@pytest.fixture
def bob_agent_id() -> str:
    """Return Bob's agent ID."""
    return BOB_AGENT_ID


@pytest.fixture
def carol_agent_id() -> str:
    """Return Carol's agent ID."""
    return CAROL_AGENT_ID


# ---------------------------------------------------------------------------
# App + client fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
async def app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    alice_keypair: tuple[Ed25519PrivateKey, str],
    bob_keypair: tuple[Ed25519PrivateKey, str],
    carol_keypair: tuple[Ed25519PrivateKey, str],
) -> AsyncIterator[Any]:
    """Use real isolated persistence behind controllable external transport boundaries."""
    db_path = tmp_path / "test.db"
    provision_keypair("gateway-task-board", tmp_path)
    agent_config_path = _write_platform_agent_config(tmp_path)
    config_content = f"""\
service:
  name: "task-board"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8003
  log_level: "info"
logging:
  level: "WARNING"
  directory: "{tmp_path / "logs" / "task-board"}"
database:
  path: "{db_path}"
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/release"
  escrow_split_path: "/escrow/split"
  timeout_seconds: 10
platform:
  agent_id: "{PLATFORM_AGENT_ID}"
  agent_config_path: "{agent_config_path}"
asset_upload:
  stream_chunk_bytes: 262144
  max_multipart_overhead_bytes: 1048576
  max_header_count: 8
  max_header_size: 4224
  max_boundary_bytes: 70
  max_display_filename_utf8_bytes: 255
request:
  max_body_size: 1048576
recovery:
  maintenance_lock_path: "{tmp_path / "maintenance.lock"}"
posting_recovery:
  interval_seconds: 2
  batch_size: 100
  diagnostic_limit: 100
financial_effect_recovery:
  interval_seconds: 2.0
  max_delivery_attempts_per_cycle: 10
limits:
  max_file_size: 10485760
  max_assets_per_task: 20
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
  signing:
    kid: "gateway-task-board"
    private_key_path: "gateway-task-board.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
deadline_evaluation:
  evaluation_interval_seconds: 3600
  actor_id: system-task-board-deadline
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)

    old_config = os.environ.get("CONFIG_PATH")
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()
    monkeypatch.setattr(PlatformAgent, "register", _register_test_platform)
    fixture_posting_recovery_task: asyncio.Task[None] | None = None
    fixture_financial_effect_recovery_task: asyncio.Task[None] | None = None

    try:
        test_app = create_app()
        async with lifespan(test_app):
            state = get_app_state()
            real_platform = state.platform_agent
            assert real_platform is not None
            known_keys = {
                ALICE_AGENT_ID: alice_keypair[0].public_key(),
                BOB_AGENT_ID: bob_keypair[0].public_key(),
                CAROL_AGENT_ID: carol_keypair[0].public_key(),
            }

            def verify_fixture_identity(token: str) -> VerifiedJws:
                parsed = parse_jws(token)
                if parsed.header.kid == PLATFORM_AGENT_ID:
                    return real_platform.validate_certificate(token)
                public_key = known_keys.get(parsed.header.kid)
                if public_key is None:
                    raise _InvalidSignature("Unknown fixture identity")
                return verify_jws_for_signer(
                    token,
                    public_key,
                    policy=real_platform.config.token_policy,
                    expected_kid=parsed.header.kid,
                )

            with (
                closing(
                    GatewayTaskStore(
                        db_path=db_path,
                        busy_timeout_ms=5000,
                        journal_mode="wal",
                        schema_sql=load_schema_sql(SchemaState.CURRENT_V5),
                        caller_kid="gateway-task-board",
                        authority=BoardAuthority(
                            platform_public_key=platform_keypair[1],
                            deadline_actor_id="system-task-board-deadline",
                        ),
                        clock_us=utc_now_us,
                    )
                ) as gateway_store,
                closing(
                    GatewayTaskStore(
                        db_path=db_path,
                        busy_timeout_ms=5000,
                        journal_mode="wal",
                        schema_sql=None,
                        caller_kid="gateway-task-board",
                        authority=BoardAuthority(
                            platform_public_key=platform_keypair[1],
                            deadline_actor_id="system-task-board-deadline",
                        ),
                        clock_us=utc_now_us,
                    )
                ) as recovery_store,
            ):
                effects = RouterGatewayEffects(
                    gateway_store, utc_now_us, 3600, verify_fixture_identity
                )
                for agent_id, keypair, balance in (
                    (PLATFORM_AGENT_ID, platform_keypair, 0),
                    (ALICE_AGENT_ID, alice_keypair, 1_000_000),
                    (BOB_AGENT_ID, bob_keypair, 1_000_000),
                    (CAROL_AGENT_ID, carol_keypair, 1_000_000),
                ):
                    effects.register_actor(agent_id, keypair[1], balance)
                assert state.store is not None
                assert state.deadline_sweep_task is not None
                assert state.posting_recovery_task is not None
                assert state.financial_effect_recovery_task is not None
                state.deadline_sweep_task.cancel()
                with suppress(asyncio.CancelledError):
                    await state.deadline_sweep_task
                state.posting_recovery_task.cancel()
                with suppress(asyncio.CancelledError):
                    await state.posting_recovery_task
                state.financial_effect_recovery_task.cancel()
                with suppress(asyncio.CancelledError):
                    await state.financial_effect_recovery_task
                state.store.close()
                state.store = gateway_store
                settings = get_settings()
                assert settings.posting_recovery is not None
                test_app.state.gateway_store = gateway_store
                test_app.state.gateway_effects = effects
                test_app.state.verify_fixture_identity = verify_fixture_identity

                mock_platform = MagicMock()
                mock_platform.agent_id = PLATFORM_AGENT_ID
                mock_platform.validate_certificate = MagicMock(side_effect=verify_fixture_identity)
                mock_platform.file_claim = AsyncMock(side_effect=effects.file_claim)
                mock_platform.submit_rebuttal = AsyncMock(side_effect=effects.submit_rebuttal)
                mock_platform.trigger_ruling = AsyncMock(
                    return_value={"status": "rebuttal_pending"}
                )
                mock_platform.close = AsyncMock()
                state.platform_agent = mock_platform
                state.platform_signer = mock_platform

                mock_bank = AsyncMock()
                mock_bank.close = AsyncMock()
                mock_bank.escrow_lock = AsyncMock(side_effect=effects.lock)
                mock_bank.escrow_release = AsyncMock(side_effect=effects.release)
                mock_bank.escrow_split = AsyncMock(side_effect=effects.split)
                state.central_bank_client = mock_bank
                posting_recovery = PostingRecovery(
                    store=recovery_store,
                    bank=mock_bank,
                    batch_size=settings.posting_recovery.batch_size,
                    diagnostic_limit=settings.posting_recovery.diagnostic_limit,
                )
                state.posting_recovery = posting_recovery
                fixture_posting_recovery_task = asyncio.create_task(
                    posting_recovery.run_periodic(
                        interval_seconds=settings.posting_recovery.interval_seconds
                    )
                )
                state.posting_recovery_task = fixture_posting_recovery_task
                financial_effect_recovery = FinancialEffectRecovery(
                    store=recovery_store,
                    bank=mock_bank,
                    worker_id=None,
                    max_delivery_attempts_per_cycle=(
                        settings.financial_effect_recovery.max_delivery_attempts_per_cycle
                    ),
                )
                state.financial_effect_recovery = financial_effect_recovery
                fixture_financial_effect_recovery_task = asyncio.create_task(
                    financial_effect_recovery.run_periodic(
                        interval_seconds=settings.financial_effect_recovery.interval_seconds
                    )
                )
                state.financial_effect_recovery_task = fixture_financial_effect_recovery_task

                mock_identity = AsyncMock()
                mock_identity.close = AsyncMock()
                mock_identity.verify_jws = AsyncMock(side_effect=make_fixture_verify_jws(state))
                state.identity_client = mock_identity

                assert settings.limits is not None
                assert settings.deadline_evaluation is not None
                coordinator = EscrowCoordinator(mock_bank)
                validator = TokenValidator(mock_platform, mock_identity)
                evaluator = DeadlineEvaluator(
                    gateway_store,
                    coordinator,
                    platform_agent=mock_platform,
                    deadline_actor_id=settings.deadline_evaluation.actor_id,
                )
                assets = AssetManager(
                    store=gateway_store,
                    token_validator=validator,
                    deadline_evaluator=evaluator,
                    asset_storage_path=str(tmp_path / "assets"),
                    max_file_size=settings.limits.max_file_size,
                    max_files_per_task=settings.limits.max_assets_per_task,
                )
                manager = TaskManager(
                    store=gateway_store,
                    central_bank_client=mock_bank,
                    escrow_coordinator=coordinator,
                    token_validator=validator,
                    deadline_evaluator=evaluator,
                    asset_manager=assets,
                    platform_signer=mock_platform,
                    platform_agent_id=PLATFORM_AGENT_ID,
                )
                state.task_manager = manager
                state.token_validator = validator
                state.escrow_coordinator = coordinator
                state.asset_manager = assets
                sweep = asyncio.create_task(
                    evaluator.run_periodic_sweep(
                        settings.deadline_evaluation.evaluation_interval_seconds
                    )
                )
                state.deadline_sweep_task = sweep
                try:
                    yield test_app
                finally:
                    sweep.cancel()
                    with suppress(asyncio.CancelledError):
                        await sweep
                    assert fixture_posting_recovery_task is not None
                    fixture_posting_recovery_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await fixture_posting_recovery_task
                    assert fixture_financial_effect_recovery_task is not None
                    fixture_financial_effect_recovery_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await fixture_financial_effect_recovery_task
                    assets.close()
                    manager.close()

    finally:
        reset_app_state()
        clear_settings_cache()
        if old_config is None:
            os.environ.pop("CONFIG_PATH", None)
        else:
            os.environ["CONFIG_PATH"] = old_config


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Mock override fixtures (use these to replace default mock behavior)
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_identity_verify_success(_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the identity client mock to verify JWS successfully."""
    state = get_app_state()
    assert state.platform_agent is not None
    assert state.identity_client is not None
    monkeypatch.setattr(
        state.platform_agent,
        "validate_certificate",
        MagicMock(side_effect=_app.state.verify_fixture_identity),
    )
    monkeypatch.setattr(
        state.identity_client, "verify_jws", AsyncMock(side_effect=make_fixture_verify_jws(state))
    )


@pytest.fixture
def mock_identity_unavailable(_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the identity client mock to simulate service unavailability."""
    state = get_app_state()
    assert state.platform_agent is not None
    assert state.identity_client is not None
    monkeypatch.setattr(
        state.platform_agent,
        "validate_certificate",
        MagicMock(side_effect=ConnectionError("Identity service unreachable")),
    )
    monkeypatch.setattr(
        state.identity_client,
        "verify_jws",
        AsyncMock(
            side_effect=ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            )
        ),
    )


@pytest.fixture
def mock_identity_timeout(_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the identity client mock to simulate a timeout."""
    state = get_app_state()
    assert state.platform_agent is not None
    assert state.identity_client is not None
    monkeypatch.setattr(
        state.platform_agent,
        "validate_certificate",
        MagicMock(side_effect=TimeoutError("Identity service timed out")),
    )
    monkeypatch.setattr(
        state.identity_client,
        "verify_jws",
        AsyncMock(
            side_effect=ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            )
        ),
    )


@pytest.fixture
def mock_identity_unexpected_response(_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the identity client mock to return an unexpected response."""
    state = get_app_state()
    assert state.platform_agent is not None
    assert state.identity_client is not None
    monkeypatch.setattr(
        state.platform_agent,
        "validate_certificate",
        MagicMock(
            side_effect=JSONDecodeError("Unexpected response from Identity service", "not-json", 0)
        ),
    )
    monkeypatch.setattr(
        state.identity_client,
        "verify_jws",
        AsyncMock(
            side_effect=ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            )
        ),
    )


@pytest.fixture
def mock_central_bank_insufficient_funds(_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the Central Bank mock to reject escrow with insufficient funds."""
    state = get_app_state()
    assert state.central_bank_client is not None
    monkeypatch.setattr(
        state.central_bank_client,
        "escrow_lock",
        AsyncMock(side_effect=Exception("INSUFFICIENT_FUNDS")),
    )


@pytest.fixture
def mock_central_bank_unavailable(_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the Central Bank mock to simulate unavailability."""
    state = get_app_state()
    assert state.central_bank_client is not None
    monkeypatch.setattr(
        state.central_bank_client,
        "escrow_lock",
        AsyncMock(side_effect=ConnectionError("Central Bank unreachable")),
    )
    monkeypatch.setattr(
        state.central_bank_client,
        "escrow_release",
        AsyncMock(side_effect=ConnectionError("Central Bank unreachable")),
    )
    monkeypatch.setattr(
        state.central_bank_client,
        "escrow_split",
        AsyncMock(side_effect=ConnectionError("Central Bank unreachable")),
    )


# ---------------------------------------------------------------------------
# JWS helper utilities (used by token validation mocks)
# ---------------------------------------------------------------------------


def make_fixture_verify_jws(state_ref: Any) -> Callable[[str], Awaitable[VerifiedJws]]:
    """Create a verify_jws mock that delegates to platform_agent.validate_certificate.

    This ensures tests that override validate_certificate (e.g. with InvalidSignature)
    will see the same behavior when verification goes through identity_client.verify_jws.

    Signature/admission errors retain their typed rejection. Only explicitly simulated
    connection, timeout and JSON-decode failures map to dependency unavailability.
    Unexpected exceptions and malformed verifier results must fail the test visibly.
    """

    async def _delegating_verify_jws(token: str) -> VerifiedJws:
        try:
            verified = state_ref.platform_agent.validate_certificate(token)
            if not isinstance(verified, VerifiedJws):
                raise TypeError("Fixture verifier must return VerifiedJws")
            action = verified.payload.get("action")
            if not isinstance(action, str) or not action:
                raise ServiceError(
                    "invalid_payload",
                    "Identity rejected malformed payload action",
                    400,
                    {},
                )
            return verified
        except _InvalidSignature:
            raise
        except JwsAdmissionError as exc:
            raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc
        except ServiceError:
            raise
        except (ConnectionError, TimeoutError, JSONDecodeError) as exc:
            raise ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

    return _delegating_verify_jws


# ---------------------------------------------------------------------------
# Task lifecycle helper functions
# ---------------------------------------------------------------------------
def new_operation_fields(task_id: str) -> dict[str, object]:
    """Build one new test intent from the currently observed version before signing.

    A missing-task negative case explicitly sends version zero. Replay tests retain
    their own original payload instead of calling this new-intent helper again.
    """
    store = get_app_state().store
    assert store is not None
    task = store.get_task(task_id)
    return {
        "operation_id": f"op-{uuid.uuid4()}",
        "expected_lifecycle_version": 0 if task is None else task["lifecycle_version"],
    }


async def create_task(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    *,
    task_id: str | None = None,
    title: str = "Test task",
    spec: str = "Test specification for task",
    reward: int = 100,
    bidding_deadline_seconds: int = 3600,
    execution_deadline_seconds: int = 86400,
    review_deadline_seconds: int = 86400,
) -> Any:
    """Create a task via POST /tasks and return the response."""
    if task_id is None:
        task_id = make_task_id()

    private_key = poster_keypair[0]
    lock_operation_id = (
        f"op-{uuid.uuid4()}"
        if task_id == ""
        else operation_id_for("task-lock", (task_id, poster_id))
    )
    task_payload = {
        "action": "create_task",
        "protocol_version": 1,
        "operation_id": f"op-{uuid.uuid4()}",
        "task_id": task_id,
        "poster_id": poster_id,
        "lock_operation_id": lock_operation_id,
        "title": title,
        "spec": spec,
        "reward": reward,
        "bidding_deadline_seconds": bidding_deadline_seconds,
        "acceptance_window_seconds": 3600,
        "execution_deadline_seconds": execution_deadline_seconds,
        "review_deadline_seconds": review_deadline_seconds,
    }
    task_token = make_jws_token(private_key, poster_id, task_payload)

    escrow_payload = {
        "action": "escrow_lock",
        "task_id": task_id,
        "agent_id": poster_id,
        "amount": reward,
        "operation_id": lock_operation_id,
    }
    escrow_token = make_jws_token(
        private_key,
        poster_id,
        escrow_payload,
        audience="central-bank",
    )

    response = await client.post(
        "/tasks",
        json={"task_token": task_token, "escrow_token": escrow_token},
    )
    if response.status_code == 201:
        envelope = response.json()
        assert envelope["publication_state"] == "published"
        assert isinstance(envelope["result"], dict)
        assert envelope["result"]["task_id"] == task_id
    return response


async def submit_bid(
    client: AsyncClient,
    bidder_keypair: tuple[Ed25519PrivateKey, str],
    bidder_id: str,
    task_id: str,
    *,
    amount: int = 90,
) -> Any:
    """Submit a bid via POST /tasks/{task_id}/bids and return the response."""
    private_key = bidder_keypair[0]
    payload = {
        "action": "submit_bid",
        **new_operation_fields(task_id),
        "task_id": task_id,
        "bidder_id": bidder_id,
        "amount": amount,
    }
    token = make_jws_token(private_key, bidder_id, payload)
    return await client.post(f"/tasks/{task_id}/bids", json={"token": token})


async def accept_bid(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    task_id: str,
    bid_id: str,
) -> Any:
    """Accept a bid via POST /tasks/{task_id}/bids/{bid_id}/accept."""
    private_key = poster_keypair[0]
    payload = {
        "action": "accept_bid",
        **new_operation_fields(task_id),
        "task_id": task_id,
        "bid_id": bid_id,
        "poster_id": poster_id,
    }
    token = make_jws_token(private_key, poster_id, payload)
    return await client.post(f"/tasks/{task_id}/bids/{bid_id}/accept", json={"token": token})


async def upload_asset(
    client: AsyncClient,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
    task_id: str,
    *,
    filename: str = "test.txt",
    content: bytes = b"test file content",
) -> Any:
    """Upload an asset via POST /tasks/{task_id}/assets with Bearer auth."""
    private_key = worker_keypair[0]
    payload = {
        "action": "upload_asset",
        "task_id": task_id,
    }
    token = make_jws_token(private_key, worker_id, payload)
    return await client.post(
        f"/tasks/{task_id}/assets",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, "application/octet-stream")},
    )


async def submit_deliverable(
    client: AsyncClient,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
    task_id: str,
) -> Any:
    """Submit deliverable via POST /tasks/{task_id}/submit."""
    private_key = worker_keypair[0]
    payload = {
        "action": "submit_deliverable",
        **new_operation_fields(task_id),
        "task_id": task_id,
        "worker_id": worker_id,
    }
    token = make_jws_token(private_key, worker_id, payload)
    return await client.post(f"/tasks/{task_id}/submit", json={"token": token})


async def approve_task(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    task_id: str,
) -> Any:
    """Approve deliverable via POST /tasks/{task_id}/approve."""
    private_key = poster_keypair[0]
    payload = {
        "action": "approve_task",
        **new_operation_fields(task_id),
        "task_id": task_id,
        "poster_id": poster_id,
    }
    token = make_jws_token(private_key, poster_id, payload)
    return await client.post(f"/tasks/{task_id}/approve", json={"token": token})


async def file_dispute(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    task_id: str,
    *,
    reason: str = "Work does not meet specification",
) -> Any:
    """File a dispute via POST /tasks/{task_id}/dispute.

    Uses the canonical 'dispute_task' action — the undocumented 'file_dispute'
    alias was removed (T-036, exception #8).
    """
    private_key = poster_keypair[0]
    payload = {
        "action": "dispute_task",
        **new_operation_fields(task_id),
        "task_id": task_id,
        "poster_id": poster_id,
        "reason": reason,
    }
    token = make_jws_token(private_key, poster_id, payload)
    return await client.post(f"/tasks/{task_id}/dispute", json={"token": token})


async def submit_ruling(
    client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    platform_id: str,
    task_id: str,
    *,
    worker_pct: int = 50,
    ruling_summary: str = "Split ruling",
) -> Any:
    """Submit a ruling via POST /tasks/{task_id}/ruling.

    Uses the canonical 'record_ruling' action — the undocumented 'submit_ruling'
    alias was removed (T-036). record_ruling always requires an explicit
    ruling_id matching the stored dispute. A missing-dispute negative case uses
    an explicit unrelated claim identifier so source-state rejection is exercised.
    """
    private_key = platform_keypair[0]
    store = get_app_state().store
    assert store is not None
    task = store.get_task(task_id)
    ruling_id = (
        str(task["dispute_id"])
        if task is not None and task["dispute_id"] is not None
        else f"clm-{uuid.uuid4()}"
    )
    payload = {
        "action": "record_ruling",
        **new_operation_fields(task_id),
        "task_id": task_id,
        "ruling_id": ruling_id,
        "worker_pct": worker_pct,
        "ruling_summary": ruling_summary,
    }
    token = make_jws_token(private_key, platform_id, payload)
    return await client.post(f"/tasks/{task_id}/ruling", json={"token": token})


async def setup_task_in_execution(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
) -> tuple[str, str]:
    """Create a task and advance it to EXECUTION status.

    Returns (task_id, bid_id).
    """
    task_id = make_task_id()
    await create_task(client, poster_keypair, poster_id, task_id=task_id)

    bid_resp = await submit_bid(client, worker_keypair, worker_id, task_id)
    bid_id = bid_resp.json()["bid_id"]

    await accept_bid(client, poster_keypair, poster_id, task_id, bid_id)
    return task_id, bid_id


async def setup_task_in_review(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
) -> str:
    """Create a task and advance it to REVIEW status.

    Returns the task_id.
    """
    task_id, _bid_id = await setup_task_in_execution(
        client, poster_keypair, poster_id, worker_keypair, worker_id
    )
    await upload_asset(client, worker_keypair, worker_id, task_id)
    await submit_deliverable(client, worker_keypair, worker_id, task_id)
    return task_id


async def setup_task_in_dispute(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
) -> str:
    """Create a task and advance it to DISPUTED status.

    Returns the task_id.
    """
    task_id = await setup_task_in_review(
        client, poster_keypair, poster_id, worker_keypair, worker_id
    )
    await file_dispute(client, poster_keypair, poster_id, task_id)
    return task_id
