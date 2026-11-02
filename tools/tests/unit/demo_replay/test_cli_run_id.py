"""CLI run identity defaults for demo replay."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import httpx
import pytest

from demo_replay.__main__ import _resolve_run_id

pytestmark = pytest.mark.unit


def test_omitted_run_id_generates_printed_unique_uuid(
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = _resolve_run_id(None)
    second = _resolve_run_id(None)

    first_uuid = UUID(first.removeprefix("run-"))
    second_uuid = UUID(second.removeprefix("run-"))
    assert first_uuid.version == 4
    assert second_uuid.version == 4
    assert first != second
    assert capsys.readouterr().out.splitlines() == [
        f"Generated demo run ID: {first}",
        f"Generated demo run ID: {second}",
    ]


def test_supplied_run_id_is_preserved_without_generation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = "run-00000000-0000-4000-8000-000000000591"

    assert _resolve_run_id(run_id) == run_id
    assert capsys.readouterr().out == ""


def test_down_stack_stops_with_one_line_before_replay(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stopped stack gives one actionable line, not a wall of connection tracebacks."""
    from demo_replay import __main__ as cli
    from demo_replay.config import PlatformConfig

    def refuse(url: str, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", refuse)
    config = PlatformConfig(
        identity_url="http://127.0.0.1:9101",
        bank_url="http://127.0.0.1:9102",
        task_board_url="http://127.0.0.1:9103",
        reputation_url="http://127.0.0.1:9104",
        court_url="http://127.0.0.1:9105",
        platform_key_path=Path("unused.key"),
        token_ttl_seconds=60,
    )
    with pytest.raises(SystemExit) as raised:
        cli._require_stack(config, timeout_seconds=1)
    assert raised.value.code == 1
    err = capsys.readouterr().err.strip().splitlines()
    assert err == [
        "✗ The stack is not running (http://127.0.0.1:9101 is down). Start it with: just start-all"
    ]
