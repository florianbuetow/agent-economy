"""Dependency validation reads one fresh ordered prefix, without recursive expansion."""

from __future__ import annotations

from typing import Any

import pytest

from test_mp07_task_operations import _engine, _step

pytestmark = pytest.mark.unit


def test_dependency_validation_reads_each_prefix_slot_at_most_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _step("approve")
    engine = _engine([target])
    store = engine._require_store()
    original = store._read_json
    step_reads = 0

    def counted(directory: Any, name: str) -> object | None:
        nonlocal step_reads
        if name.endswith(".json") and name[:6].isdigit():
            step_reads += 1
        return original(directory, name)

    monkeypatch.setattr(store, "_read_json", counted)
    target_index = len(engine._scenario_steps) - 1

    assert store.step(target_index) is None
    assert step_reads == target_index + 1
