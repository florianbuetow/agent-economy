"""CLI run identity defaults for demo replay."""

from __future__ import annotations

from uuid import UUID

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
