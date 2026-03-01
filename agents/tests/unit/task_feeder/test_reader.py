"""Unit tests for task_feeder.reader."""

import json
import tempfile
from pathlib import Path

import pytest

from task_feeder.reader import RawTask, iterate_tasks, load_tasks


def _write_jsonl(tasks: list[dict[str, object]], path: Path) -> None:
    with path.open("w") as fh:
        for task in tasks:
            fh.write(json.dumps(task) + "\n")


SAMPLE_TASK = {
    "title": "Add numbers",
    "spec": "Calculate 2+3",
    "solutions": ["5"],
    "level": 1,
    "problem_type": "addition_positive",
}

SAMPLE_TASK_WITH_NOTE = {
    "title": "System infinite",
    "spec": "Solve the system",
    "solutions": ["INFINITELY_MANY"],
    "level": 6,
    "problem_type": "system_infinite",
    "solution_note": "Any (x, y) on the line.",
}


@pytest.mark.unit
class TestLoadTasks:
    def test_loads_single_task(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.jsonl"
        _write_jsonl([SAMPLE_TASK], path)

        tasks = load_tasks(path)
        assert len(tasks) == 1
        assert tasks[0].title == "Add numbers"
        assert tasks[0].level == 1
        assert tasks[0].solutions == ["5"]
        assert tasks[0].solution_note is None

    def test_loads_multiple_tasks(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.jsonl"
        _write_jsonl([SAMPLE_TASK, SAMPLE_TASK_WITH_NOTE], path)

        tasks = load_tasks(path)
        assert len(tasks) == 2
        assert tasks[1].solution_note == "Any (x, y) on the line."

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.jsonl"
        path.write_text(json.dumps(SAMPLE_TASK) + "\n\n" + json.dumps(SAMPLE_TASK) + "\n")

        tasks = load_tasks(path)
        assert len(tasks) == 2

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.jsonl"
        with pytest.raises(FileNotFoundError):
            load_tasks(path)

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "tasks.jsonl"
        path.write_text("not json\n")
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_tasks(path)


@pytest.mark.unit
class TestIterateTasks:
    def test_yields_in_order(self) -> None:
        tasks = [
            RawTask(title="A", spec="a", solutions=["1"], level=1, problem_type="x", solution_note=None),
            RawTask(title="B", spec="b", solutions=["2"], level=2, problem_type="y", solution_note=None),
        ]
        it = iterate_tasks(tasks, shuffle=False)
        titles = [next(it).title for _ in range(6)]
        assert titles == ["A", "B", "A", "B", "A", "B"]

    def test_empty_list_yields_nothing(self) -> None:
        it = iterate_tasks([], shuffle=False)
        result = list(zip(range(3), it))  # try to pull 3 items
        assert result == []

    def test_shuffle_produces_all_tasks(self) -> None:
        tasks = [
            RawTask(title=f"T{i}", spec="s", solutions=["x"], level=1, problem_type="t", solution_note=None)
            for i in range(10)
        ]
        it = iterate_tasks(tasks, shuffle=True)
        titles = {next(it).title for _ in range(10)}
        assert titles == {f"T{i}" for i in range(10)}
