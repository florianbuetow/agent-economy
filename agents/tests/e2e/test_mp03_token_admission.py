"""Live MP-03 token admission across remote and local receiver adapters."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import yaml

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _raw_token(
    private_key: Ed25519PrivateKey,
    header: object,
    payload: dict[str, object],
    *,
    signature: bytes | None = None,
) -> str:
    header_segment = _b64url(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    payload_segment = _b64url(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    actual_signature = private_key.sign(signing_input) if signature is None else signature
    return f"{header_segment}.{payload_segment}.{_b64url(actual_signature)}"


def _header(
    kid: str,
    audience: str,
    resource: str,
    *,
    issued_at: int | bool | None = None,
    expires_at: int | bool | None = None,
) -> dict[str, object]:
    now = int(time.time())
    return {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": kid,
        "iat": now if issued_at is None else issued_at,
        "exp": now + 60 if expires_at is None else expires_at,
        "aud": audience,
        "resource": resource,
    }


def _assert_error(response: httpx.Response, status: int, error: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"] == error


def _required_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        pytest.fail(f"{label} must be an object")
    return value


def _validate_agent_config(
    run_dir: Path,
    manifest: dict[str, object],
) -> dict[str, object]:
    raw_agent_config = os.environ.get("AGENT_CONFIG_PATH")
    if raw_agent_config is None:
        pytest.fail("AGENT_CONFIG_PATH is required for the disposable runtime")
    agent_config = Path(raw_agent_config).resolve()
    manifest_agent_config = Path(str(manifest.get("agent_config_path"))).resolve()
    if (
        agent_config != manifest_agent_config
        or not agent_config.is_relative_to(run_dir)
        or not agent_config.is_file()
    ):
        pytest.fail("AGENT_CONFIG_PATH must exactly match the owned runtime manifest")
    raw_settings: object = yaml.safe_load(agent_config.read_text())
    settings = _required_mapping(raw_settings, "disposable agent config")
    return _required_mapping(settings.get("platform"), "disposable agent config platform")


def _validate_service_bindings(
    run_dir: Path,
    manifest: dict[str, object],
    agent_platform: dict[str, object],
) -> None:
    ports = _required_mapping(manifest.get("ports"), "runtime.json ports")
    config_paths = _required_mapping(manifest.get("config_paths"), "runtime.json config_paths")
    service_urls = {
        "IDENTITY_URL": ("identity", "identity_url"),
        "BANK_URL": ("central-bank", "bank_url"),
        "TASK_BOARD_URL": ("task-board", "task_board_url"),
        "REPUTATION_URL": ("reputation", "reputation_url"),
        "COURT_URL": ("court", "court_url"),
    }
    for env_name, (service_name, agent_url_key) in service_urls.items():
        port = ports.get(service_name)
        if isinstance(port, bool) or not isinstance(port, int) or port <= 0:
            pytest.fail(f"runtime.json has no valid port for {service_name}")
        expected_url = f"http://127.0.0.1:{port}"
        if os.environ.get(env_name) != expected_url:
            pytest.fail(f"{env_name} must exactly match runtime.json port for {service_name}")
        if agent_platform.get(agent_url_key) != expected_url:
            pytest.fail(f"agent config URL mismatch for {service_name}")

        raw_config_path = config_paths.get(service_name)
        if not isinstance(raw_config_path, str):
            pytest.fail(f"runtime.json has no config path for {service_name}")
        config_path = Path(raw_config_path).resolve()
        if not config_path.is_relative_to(run_dir) or not config_path.is_file():
            pytest.fail(f"runtime config for {service_name} is outside the owned run directory")
        raw_config: object = yaml.safe_load(config_path.read_text())
        config = _required_mapping(raw_config, f"runtime config for {service_name}")
        service = _required_mapping(config.get("service"), f"{service_name} service config")
        server = _required_mapping(config.get("server"), f"{service_name} server config")
        if (
            service.get("name") != service_name
            or server.get("host") != "127.0.0.1"
            or server.get("port") != port
        ):
            pytest.fail(f"runtime config identity/host/port mismatch for {service_name}")


@pytest.fixture(scope="session")
def _isolated_runtime_dir() -> Path:
    raw_run_dir = os.environ.get("ISOLATED_RUNTIME_DIR")
    if raw_run_dir is None:
        pytest.fail("ISOLATED_RUNTIME_DIR is required for MP-03 live admission tests")
    run_dir = Path(raw_run_dir).resolve()
    manifest_path = run_dir / "runtime.json"
    if not manifest_path.is_file():
        pytest.fail(f"isolated runtime manifest is missing: {manifest_path}")
    raw_manifest: object = json.loads(manifest_path.read_text())
    manifest = _required_mapping(raw_manifest, "runtime.json")
    if Path(str(manifest.get("run_dir"))).resolve() != run_dir:
        pytest.fail("isolated runtime manifest does not own ISOLATED_RUNTIME_DIR")

    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id or os.environ.get("ISOLATED_RUNTIME_ID") != run_id:
        pytest.fail("ISOLATED_RUNTIME_ID must exactly match runtime.json run_id")
    agent_platform = _validate_agent_config(run_dir, manifest)
    _validate_service_bindings(run_dir, manifest, agent_platform)
    return run_dir


def _require_health(env_name: str) -> None:
    service_url = os.environ[env_name]
    try:
        response = httpx.get(f"{service_url}/health", timeout=3.0)
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        pytest.fail(f"isolated service is not healthy at {service_url}: {exc}")


# Override shared fixtures so ownership proof precedes real isolated health probes.
@pytest.fixture(scope="session", autouse=True)
def _require_identity_service(_isolated_runtime_dir: Path) -> None:
    _require_health("IDENTITY_URL")


@pytest.fixture(scope="session", autouse=True)
def _require_bank_service(_isolated_runtime_dir: Path) -> None:
    _require_health("BANK_URL")


@pytest.fixture(scope="session", autouse=True)
def _require_task_board_service(_isolated_runtime_dir: Path) -> None:
    _require_health("TASK_BOARD_URL")


@pytest.fixture(scope="session", autouse=True)
def _require_reputation_service(_isolated_runtime_dir: Path) -> None:
    _require_health("REPUTATION_URL")


@pytest.fixture(scope="session", autouse=True)
def _require_court_service(_isolated_runtime_dir: Path) -> None:
    _require_health("COURT_URL")


@pytest.mark.parametrize(
    ("case", "expected_status", "expected_error"),
    [
        ("wrong_audience", 400, "invalid_payload"),
        ("expired", 401, "token_expired"),
        ("missing_exp", 400, "invalid_jws"),
        ("bad_signature", 403, "forbidden"),
        ("bad_signature_missing_action", 403, "forbidden"),
    ],
)
async def test_bank_remote_identity_admission_matrix(
    make_funded_agent: Any,
    case: str,
    expected_status: int,
    expected_error: str,
) -> None:
    agent: BaseAgent = await make_funded_agent(name=f"MP03 Bank {case}", balance=10)
    assert agent.agent_id is not None
    resource = f"GET /accounts/{agent.agent_id}"
    control_payload = {"action": "get_balance"}
    payload = dict(control_payload)
    header = _header(agent.agent_id, "central-bank", resource)
    if case == "wrong_audience":
        header["aud"] = "task-board"
    elif case == "expired":
        now = int(time.time())
        header["iat"] = now - 100
        header["exp"] = now - 40
    elif case == "missing_exp":
        del header["exp"]
    elif case == "bad_signature_missing_action":
        del payload["action"]

    token = _raw_token(
        agent.config.private_key,
        header,
        payload,
        signature=(
            b"\x00" * 64 if case in {"bad_signature", "bad_signature_missing_action"} else None
        ),
    )
    try:
        control = await agent._request_raw(
            "GET",
            f"{agent.config.bank_url}/accounts/{agent.agent_id}",
            headers=agent._auth_header(
                control_payload,
                audience="central-bank",
                resource=resource,
            ),
        )
        assert control.status_code == 200, control.text
        response = await agent._request_raw(
            "GET",
            f"{agent.config.bank_url}/accounts/{agent.agent_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        _assert_error(response, expected_status, expected_error)
    finally:
        await agent.close()


@pytest.mark.parametrize(
    ("case", "expected_status", "expected_error"),
    [
        ("wrong_resource", 400, "invalid_payload"),
        ("wrong_action", 400, "invalid_payload"),
        ("future", 401, "token_not_yet_valid"),
        ("non_object_header", 400, "invalid_jws"),
        ("boolean_time", 400, "invalid_jws"),
    ],
)
async def test_task_board_remote_identity_admission_matrix_has_no_bid_effect(
    make_funded_agent: Any,
    case: str,
    expected_status: int,
    expected_error: str,
) -> None:
    poster: BaseAgent = await make_funded_agent(name=f"MP03 Board Poster {case}", balance=20)
    worker: BaseAgent = await make_funded_agent(name=f"MP03 Board Worker {case}", balance=0)
    assert worker.agent_id is not None
    task = await poster.post_task(
        title=f"MP03 admission {case}",
        spec="A rejected token must not create a bid",
        reward=10,
        bidding_deadline_seconds=300,
        execution_deadline_seconds=300,
        review_deadline_seconds=300,
    )
    task_id = str(task["task_id"])
    resource = f"POST /tasks/{task_id}/bids"
    payload: dict[str, object] = {
        "action": "submit_bid",
        "task_id": task_id,
        "bidder_id": worker.agent_id,
        "amount": 1,
    }
    header: object = _header(worker.agent_id, "task-board", resource)
    if case == "wrong_resource":
        assert isinstance(header, dict)
        header["resource"] = f"POST /tasks/{task_id}/cancel"
    elif case == "wrong_action":
        payload["action"] = "cancel_task"
    elif case == "future":
        assert isinstance(header, dict)
        now = int(time.time())
        header["iat"] = now + 120
        header["exp"] = now + 180
    elif case == "non_object_header":
        header = ["not", "an", "object"]
    elif case == "boolean_time":
        assert isinstance(header, dict)
        header["iat"] = True

    token = _raw_token(worker.config.private_key, header, payload)
    try:
        assert await poster.list_bids(task_id) == []
        response = await worker._request_raw(
            "POST",
            f"{worker.config.task_board_url}/tasks/{task_id}/bids",
            json={"token": token},
        )
        _assert_error(response, expected_status, expected_error)
        assert await poster.list_bids(task_id) == []

        created = await worker.submit_bid(task_id, 1)
        assert created["bidder_id"] == worker.agent_id
        bids = await poster.list_bids(task_id)
        assert len(bids) == 1
        assert bids[0]["bid_id"] == created["bid_id"]
    finally:
        await worker.close()
        await poster.close()


async def test_reputation_remote_context_rejection_has_no_feedback_effect(
    make_funded_agent: Any,
) -> None:
    agent: BaseAgent = await make_funded_agent(name="MP03 Reputation", balance=10)
    assert agent.agent_id is not None
    task_id = f"t-mp03-feedback-{uuid4()}"
    payload: dict[str, object] = {
        "action": "submit_feedback",
        "from_agent_id": agent.agent_id,
        "task_id": task_id,
    }
    wrong_context = _raw_token(
        agent.config.private_key,
        _header(agent.agent_id, "central-bank", "POST /feedback"),
        payload,
    )
    try:
        before = await agent._request_raw(
            "GET", f"{agent.config.reputation_url}/feedback/task/{task_id}"
        )
        assert before.status_code == 200
        assert before.json()["feedback"] == []
        control = await agent._request_raw(
            "POST",
            f"{agent.config.reputation_url}/feedback",
            json={
                "token": agent._sign_jws(
                    payload,
                    audience="reputation",
                    resource="POST /feedback",
                )
            },
        )
        assert control.status_code == 400, control.text
        assert control.json()["error"] != "invalid_payload"
        response = await agent._request_raw(
            "POST",
            f"{agent.config.reputation_url}/feedback",
            json={"token": wrong_context},
        )
        _assert_error(response, 400, "invalid_payload")
        after = await agent._request_raw(
            "GET", f"{agent.config.reputation_url}/feedback/task/{task_id}"
        )
        assert after.status_code == 200
        assert after.json()["feedback"] == []
    finally:
        await agent.close()


@pytest.mark.parametrize(
    ("case", "expected_status", "expected_error"),
    [
        ("wrong_audience", 400, "invalid_payload"),
        ("wrong_resource", 400, "invalid_payload"),
        ("wrong_action", 400, "invalid_payload"),
        ("expired", 401, "token_expired"),
        ("future", 401, "token_not_yet_valid"),
        ("missing_exp", 400, "invalid_jws"),
        ("bad_signature", 403, "forbidden"),
        ("wrong_platform_kid", 403, "forbidden"),
        ("bad_signature_missing_action", 403, "forbidden"),
        ("wrong_platform_kid_missing_action", 403, "forbidden"),
    ],
)
async def test_court_local_platform_admission_matrix_has_no_dispute_effect(
    platform_agent: PlatformAgent,
    case: str,
    expected_status: int,
    expected_error: str,
) -> None:
    assert platform_agent.agent_id is not None
    task_id = f"t-mp03-court-{uuid4()}"
    payload: dict[str, object] = {
        "action": "file_dispute",
        "task_id": task_id,
        "claimant_id": "a-claimant",
        "respondent_id": "a-respondent",
        "claim": "No domain effect is expected",
        "escrow_id": "esc-missing",
    }
    header = _header(platform_agent.agent_id, "court", "POST /disputes/file")
    if case == "wrong_audience":
        header["aud"] = "central-bank"
    elif case == "wrong_resource":
        header["resource"] = "POST /disputes/missing/rule"
    elif case == "wrong_action":
        payload["action"] = "trigger_ruling"
    elif case == "expired":
        now = int(time.time())
        header["iat"] = now - 100
        header["exp"] = now - 40
    elif case == "future":
        now = int(time.time())
        header["iat"] = now + 120
        header["exp"] = now + 180
    elif case == "missing_exp":
        del header["exp"]
    elif case == "wrong_platform_kid":
        header["kid"] = "a-not-the-platform"
    elif case == "bad_signature_missing_action":
        del payload["action"]
    elif case == "wrong_platform_kid_missing_action":
        header["kid"] = "a-not-the-platform"
        del payload["action"]

    token = _raw_token(
        platform_agent.config.private_key,
        header,
        payload,
        signature=(
            b"\x00" * 64 if case in {"bad_signature", "bad_signature_missing_action"} else None
        ),
    )
    before = await platform_agent._request_raw(
        "GET",
        f"{platform_agent.config.court_url}/disputes",
        params={"task_id": task_id},
    )
    assert before.status_code == 200
    assert before.json()["disputes"] == []
    control = await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/file",
        json={
            "token": platform_agent._sign_jws(
                {
                    "action": "file_dispute",
                    "task_id": task_id,
                    "claimant_id": "a-claimant",
                    "respondent_id": "a-respondent",
                    "claim": "No domain effect is expected",
                    "escrow_id": "esc-missing",
                },
                audience="court",
                resource="POST /disputes/file",
            )
        },
    )
    _assert_error(control, 404, "task_not_found")
    response = await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/file",
        json={"token": token},
    )
    _assert_error(response, expected_status, expected_error)
    after = await platform_agent._request_raw(
        "GET",
        f"{platform_agent.config.court_url}/disputes",
        params={"task_id": task_id},
    )
    assert after.status_code == 200
    assert after.json()["disputes"] == []
