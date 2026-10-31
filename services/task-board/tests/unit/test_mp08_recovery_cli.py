"""MP-08 signed operator CLI acceptance contract."""

from __future__ import annotations

import importlib
import json
import tomllib
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
import pytest
import yaml
from service_auth.gateway import verify_gateway_request
from service_auth.jws import TokenPolicy
from service_auth.signing import generate_keypair

from tests.fakes.board_mp08_recovery import EFFECT_OPERATION_ID, diagnostic

pytestmark = pytest.mark.unit


class _Main(Protocol):
    def __call__(self, argv: list[str]) -> int: ...


def _main() -> _Main:
    try:
        module = importlib.import_module("task_board_service.recovery_cli")
    except ModuleNotFoundError as exc:
        if exc.name != "task_board_service.recovery_cli":
            raise
        pytest.fail("Missing task-board-recovery CLI capability")
    value = getattr(module, "main", None)
    if not callable(value):
        pytest.fail("Missing callable recovery_cli.main capability")
    return cast("_Main", value)


def _config(tmp_path: Path) -> tuple[Path, object]:
    kid = "gateway-task-board"
    private_key, public_key = generate_keypair(kid, tmp_path)
    del private_key
    config = {
        "service": {"name": "task-board", "version": "0.1.0"},
        "server": {"host": "127.0.0.1", "port": 8003, "log_level": "info"},
        "logging": {"level": "INFO", "directory": str(tmp_path / "logs")},
        "database": {"path": str(tmp_path / "unused.db")},
        "identity": {
            "base_url": "http://127.0.0.1:1",
            "get_agent_path": "/agents",
            "verify_jws_path": "/agents/verify-jws",
            "timeout_seconds": 10,
        },
        "central_bank": {
            "base_url": "http://127.0.0.1:2",
            "escrow_lock_path": "/escrow/lock",
            "escrow_release_path": "/escrow/{escrow_id}/release",
            "escrow_split_path": "/escrow/{escrow_id}/split",
            "escrow_settle_path": "/escrow/{escrow_id}/settle",
            "timeout_seconds": 10,
        },
        "platform": {"agent_id": "", "private_key_path": "", "agent_config_path": "x"},
        "assets": {
            "storage_path": str(tmp_path / "assets"),
            "max_file_size": 1,
            "max_files_per_task": 1,
        },
        "artifacts": {
            "max_manifest_bytes": 104857600,
            "reservation_ttl_seconds": 300,
            "reservation_renew_before_expiry_seconds": 60,
            "max_upload_duration_seconds": 900,
            "recovery_interval_seconds": 60.0,
            "stale_file_seconds": 900,
            "archive_after_seconds": 259200,
            "archive_batch_size": 50,
        },
        "asset_quota": {"max_total_bytes_per_agent": 536870912, "max_total_bytes": 4294967296},
        "asset_upload": {
            "stream_chunk_bytes": 262_144,
            "max_multipart_overhead_bytes": 1_048_576,
            "max_header_count": 8,
            "max_header_size": 4_224,
            "max_boundary_bytes": 70,
            "max_display_filename_utf8_bytes": 255,
        },
        "request": {"max_body_size": 1024},
        "recovery": {"maintenance_lock_path": str(tmp_path / "maintenance.lock")},
        "db_gateway": {
            "url": "http://gateway.invalid",
            "timeout_seconds": 10,
            "signing": {
                "kid": kid,
                "private_key_path": str(tmp_path / f"{kid}.key"),
                "audience": "db-gateway",
                "token_ttl_seconds": 300,
            },
        },
        "deadline_evaluation": {
            "actor_id": "system-task-board-deadline",
            "evaluation_interval_seconds": 10,
        },
        "posting_recovery": {
            "interval_seconds": 2,
            "batch_size": 10,
            "diagnostic_limit": 10,
        },
        "financial_effect_recovery": {
            "interval_seconds": 2.0,
            "max_delivery_attempts_per_cycle": 10,
        },
    }
    path = tmp_path / "explicit-cli-config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path, public_key


def _response(request: httpx.Request, body: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body, request=request)


def _assert_signed(request: httpx.Request, public_key: object, action: str) -> None:
    authorization = request.headers.get("Authorization", "")
    assert authorization.startswith("Bearer ")
    verify_gateway_request(
        authorization.removeprefix("Bearer "),
        public_key,  # type: ignore[arg-type]
        policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=0),
        audience="db-gateway",
        method=request.method,
        path=request.url.path,
        body=request.content,
        query=request.url.query,
        allowed_actions=action,
    )


def test_list_prints_only_strict_json_and_signs_exact_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path, public_key = _config(tmp_path)
    body = {"protocol_version": 1, "items": [diagnostic(state="dead_letter")], "next_after": None}
    requests: list[httpx.Request] = []

    def send(_client: httpx.Client, request: httpx.Request, **_kwargs: object) -> httpx.Response:
        requests.append(request)
        return _response(request, body)

    monkeypatch.setattr(httpx.Client, "send", send)
    result = _main()(
        [
            "--config",
            str(config_path),
            "effects",
            "list",
            "--state",
            "dead_letter",
            "--after",
            "",
            "--limit",
            "10",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == body
    assert captured.err == ""
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/board/task-effect-deliveries"
    assert dict(request.url.params) == {"state": "dead_letter", "after": "", "limit": "10"}
    assert len(request.url.params.multi_items()) == 3
    _assert_signed(request, public_key, "gateway.board.write")


def test_show_validates_same_identity_and_uses_signed_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path, public_key = _config(tmp_path)
    body = {
        "protocol_version": 1,
        "delivery": diagnostic(state="dead_letter"),
    }
    requests: list[httpx.Request] = []

    def send(_client: httpx.Client, request: httpx.Request, **_kwargs: object) -> httpx.Response:
        requests.append(request)
        return _response(request, body)

    monkeypatch.setattr(httpx.Client, "send", send)
    result = _main()(
        [
            "--config",
            str(config_path),
            "effects",
            "show",
            "--operation-id",
            EFFECT_OPERATION_ID,
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == body
    assert captured.err == ""
    request = requests[0]
    assert request.url.path == f"/board/task-effect-deliveries/{EFFECT_OPERATION_ID}"
    assert request.method == "GET"
    _assert_signed(request, public_key, "gateway.board.write")


def test_retry_reads_original_then_posts_suffix_and_proves_immutable_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path, public_key = _config(tmp_path)
    before = {"protocol_version": 1, "delivery": diagnostic(state="dead_letter")}
    after = {"protocol_version": 1, "delivery": diagnostic(state="pending")}
    requests: list[httpx.Request] = []

    def send(_client: httpx.Client, request: httpx.Request, **_kwargs: object) -> httpx.Response:
        requests.append(request)
        return _response(request, before if len(requests) == 1 else after)

    monkeypatch.setattr(httpx.Client, "send", send)
    result = _main()(
        [
            "--config",
            str(config_path),
            "effects",
            "retry",
            "--operation-id",
            EFFECT_OPERATION_ID,
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == after
    assert captured.err == ""
    assert [request.method for request in requests] == ["GET", "POST"]
    assert [request.url.path for request in requests] == [
        f"/board/task-effect-deliveries/{EFFECT_OPERATION_ID}",
        f"/board/task-effect-deliveries/{EFFECT_OPERATION_ID}/retry",
    ]
    assert requests[1].content == b"{}"
    for request in requests:
        assert request.url.host == "gateway.invalid"
        _assert_signed(request, public_key, "gateway.board.write")


@pytest.mark.parametrize(
    "tail",
    [
        ["effects", "show", "--operation-id", "not-an-operation"],
        [
            "effects",
            "list",
            "--state",
            "all",
            "--after",
            "",
            "--limit",
            "0",
        ],
    ],
)
def test_invalid_arguments_exit_one_without_trusted_stdout(
    tail: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path, _public_key = _config(tmp_path)
    requests = 0

    def send(_client: httpx.Client, _request: httpx.Request, **_kwargs: object) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError("invalid CLI arguments reached HTTP")

    monkeypatch.setattr(httpx.Client, "send", send)
    assert _main()(["--config", str(config_path), *tail]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip()
    assert requests == 0


def test_missing_explicit_config_exits_one_without_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests = 0

    def send(_client: httpx.Client, _request: httpx.Request, **_kwargs: object) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError("missing config reached HTTP")

    monkeypatch.setattr(httpx.Client, "send", send)
    assert _main()(["effects", "show", "--operation-id", EFFECT_OPERATION_ID]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip()
    assert requests == 0


@pytest.mark.parametrize(
    "body",
    [
        {"protocol_version": 1},
        {"protocol_version": 1, "delivery": diagnostic(), "extra": True},
        {
            "protocol_version": 1,
            "delivery": diagnostic(effect_operation_id="op-22222222-2222-4222-8222-222222222222"),
        },
        {
            "protocol_version": 1,
            "delivery": diagnostic(
                state="pending", source_id="op-22222222-2222-4222-8222-222222222222"
            ),
        },
        {
            "protocol_version": 1,
            "delivery": diagnostic(state="pending", effect_kind="bank_split"),
        },
        {
            "protocol_version": 1,
            "delivery": {**diagnostic(state="pending"), "task_id": "t-different"},
        },
        {"protocol_version": 1, "delivery": diagnostic(state="completed")},
    ],
)
def test_ambiguous_retry_response_exits_one_and_emits_no_trusted_result(
    body: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path, _public_key = _config(tmp_path)

    requests = 0

    def send(_client: httpx.Client, request: httpx.Request, **_kwargs: object) -> httpx.Response:
        nonlocal requests
        requests += 1
        before = {"protocol_version": 1, "delivery": diagnostic(state="dead_letter")}
        return _response(request, before if requests == 1 else body)

    monkeypatch.setattr(httpx.Client, "send", send)
    result = _main()(
        [
            "--config",
            str(config_path),
            "effects",
            "retry",
            "--operation-id",
            EFFECT_OPERATION_ID,
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err.strip()


@pytest.mark.parametrize("status_code", [401, 403, 409, 500, 503])
def test_http_status_failures_exit_one_without_trusted_output(
    status_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path, _public_key = _config(tmp_path)

    def send(_client: httpx.Client, request: httpx.Request, **_kwargs: object) -> httpx.Response:
        return _response(
            request,
            {
                "error": "upstream_failure",
                "message": "private-secret-upstream-text",
                "details": {"private": "private-secret-upstream-text"},
            },
            status=status_code,
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    result = _main()(
        ["--config", str(config_path), "effects", "show", "--operation-id", EFFECT_OPERATION_ID]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err.strip()
    assert "private-secret-upstream-text" not in captured.out
    assert "private-secret-upstream-text" not in captured.err


def test_transport_failure_exits_one_without_exception_text_on_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path, _public_key = _config(tmp_path)

    def send(_client: httpx.Client, request: httpx.Request, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("private transport details", request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    result = _main()(
        ["--config", str(config_path), "effects", "show", "--operation-id", EFFECT_OPERATION_ID]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err.strip()
    assert "private transport details" not in captured.out
    assert "private transport details" not in captured.err


@pytest.mark.parametrize(
    ("command", "body"),
    [
        ("show", {"protocol_version": 1}),
        ("show", {"protocol_version": 1, "delivery": diagnostic(), "extra": True}),
        ("list", {"protocol_version": 1, "items": "not-a-list", "next_after": None}),
        ("list", {"protocol_version": 1, "items": [], "next_after": "bad-cursor"}),
    ],
)
def test_malformed_list_and_show_responses_exit_one(
    command: str,
    body: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path, _public_key = _config(tmp_path)

    def send(_client: httpx.Client, request: httpx.Request, **_kwargs: object) -> httpx.Response:
        return _response(request, body)

    monkeypatch.setattr(httpx.Client, "send", send)
    tail = (
        ["effects", "show", "--operation-id", EFFECT_OPERATION_ID]
        if command == "show"
        else ["effects", "list", "--state", "all", "--after", "", "--limit", "10"]
    )
    assert _main()(["--config", str(config_path), *tail]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip()


def test_console_script_points_to_recovery_entrypoint() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    document = tomllib.loads(pyproject.read_text())
    assert document["project"]["scripts"]["task-board-recovery"] == (
        "task_board_service.recovery_cli:cli"
    )


@pytest.mark.parametrize(
    "failure",
    ["missing_file", "malformed_yaml", "missing_signing_key", "missing_section", "bad_shape"],
)
def test_configuration_failures_exit_one_without_http_or_secret_output(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path, _public_key = _config(tmp_path)
    if failure == "missing_file":
        config_path = tmp_path / "absent-config.yaml"
    elif failure == "malformed_yaml":
        config_path.write_text("db_gateway: [private-secret-config")
    elif failure == "missing_signing_key":
        (tmp_path / "gateway-task-board.key").unlink()
    else:
        document = yaml.safe_load(config_path.read_text())
        assert isinstance(document, dict)
        if failure == "missing_section":
            del document["financial_effect_recovery"]
        elif failure == "bad_shape":
            document["financial_effect_recovery"] = {
                "interval_seconds": "private-secret-config",
                "max_delivery_attempts_per_cycle": True,
            }
        else:
            raise AssertionError(f"Unknown failure: {failure}")
        config_path.write_text(yaml.safe_dump(document))
    requests = 0

    def send(_client: httpx.Client, _request: httpx.Request, **_kwargs: object) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError("invalid configuration reached HTTP")

    monkeypatch.setattr(httpx.Client, "send", send)
    result = _main()(
        [
            "--config",
            str(config_path),
            "effects",
            "show",
            "--operation-id",
            EFFECT_OPERATION_ID,
        ]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert requests == 0
    assert captured.out == ""
    assert captured.err.strip()
    assert "private-secret-config" not in captured.out
    assert "private-secret-config" not in captured.err
