"""Source evidence is immutable and cannot be assembled across a file replacement."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, cast

import pytest

from task_feeder import reader

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def test_raw_task_does_not_retain_mutable_constructor_input() -> None:
    solutions = ["a", " b "]
    task = reader.RawTask(
        title="Immutable",
        spec="Preserve original evidence",
        solutions=cast("Any", solutions),
        level=1,
        problem_type="proof",
        solution_note=None,
    )
    solutions.append("changed after construction")
    assert task.solutions == ("a", " b ")


def test_source_replacement_during_read_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tasks.jsonl"
    value = {
        "title": "Original",
        "spec": "Original specification",
        "solutions": ["1"],
        "level": 1,
        "problem_type": "proof",
        "solution_note": None,
    }
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text(json.dumps({**value, "title": "Changed"}) + "\n", encoding="utf-8")
    original_read = os.read
    replaced = False

    def read_then_replace(descriptor: int, amount: int) -> bytes:
        nonlocal replaced
        content = original_read(descriptor, amount)
        if not replaced:
            replacement.replace(path)
            replaced = True
        return content

    monkeypatch.setattr(os, "read", read_then_replace)
    with pytest.raises(ValueError, match="changed"):
        reader.load_task_snapshot(path, max_bytes=65_536)
    assert replaced
