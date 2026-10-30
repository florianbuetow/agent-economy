"""Text-classification workload: task source, public spec contract and review rule.

A classification task is authored as structured data (inputs, label set,
reference labels, acceptance threshold) and rendered into the feeder's
existing ``RawTask`` source shape, so the durable posting pipeline is reused
unchanged:

* ``spec`` holds human-readable instructions plus one machine-readable
  ``TASK DATA`` JSON block. Today the inputs are inline in that block; when
  input attachments land, ``inputs`` can be replaced by a file reference
  without changing the rest of the contract.
* ``solutions`` holds exactly one canonical JSON object with the poster's
  reference labels. It is never published; only the feeder's reviewer reads it.
* ``problem_type`` is ``text_classification`` so the reviewer can dispatch.

The worker must upload two outputs: ``labels.json`` and ``rationale.txt``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from service_auth.posting_journal import RawTask

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

CLASSIFICATION_PROBLEM_TYPE = "text_classification"
LABELS_FILE = "labels.json"
RATIONALE_FILE = "rationale.txt"

_DATA_BEGIN = "--- TASK DATA (JSON) ---"
_DATA_END = "--- END TASK DATA ---"


class ClassificationSpecError(ValueError):
    """Raised when a classification task or spec violates the contract."""


@dataclass(frozen=True)
class ClassificationTaskData:
    """The public, worker-visible part of a classification task."""

    task_type: str
    labels: tuple[str, ...]
    inputs: tuple[tuple[str, str], ...]
    labels_file: str
    rationale_file: str
    rationale_max_chars: int
    min_correct: int

    @property
    def input_ids(self) -> tuple[str, ...]:
        return tuple(item_id for item_id, _text in self.inputs)


@dataclass(frozen=True)
class ClassificationTask:
    """One authored classification task including the private reference labels."""

    title: str
    task_type: str
    level: int
    instructions: str
    labels: tuple[str, ...]
    inputs: tuple[tuple[str, str], ...]
    expected: Mapping[str, str]
    min_correct: int
    rationale_max_chars: int

    def __post_init__(self) -> None:
        if not self.labels or len(set(self.labels)) != len(self.labels):
            raise ClassificationSpecError(f"{self.title}: labels must be nonempty and unique")
        ids = [item_id for item_id, _text in self.inputs]
        if not ids or len(set(ids)) != len(ids):
            raise ClassificationSpecError(f"{self.title}: input ids must be nonempty and unique")
        if set(self.expected) != set(ids):
            raise ClassificationSpecError(f"{self.title}: expected labels must cover every input")
        unknown = sorted(set(self.expected.values()) - set(self.labels))
        if unknown:
            raise ClassificationSpecError(f"{self.title}: expected labels not allowed: {unknown}")
        if not 1 <= self.min_correct <= len(ids):
            raise ClassificationSpecError(f"{self.title}: min_correct must be in 1..{len(ids)}")
        if self.rationale_max_chars < 1:
            raise ClassificationSpecError(f"{self.title}: rationale_max_chars must be positive")
        if type(self.level) is not int or self.level < 0:
            raise ClassificationSpecError(f"{self.title}: level must be a nonnegative integer")

    def public_data(self) -> ClassificationTaskData:
        return ClassificationTaskData(
            task_type=self.task_type,
            labels=self.labels,
            inputs=self.inputs,
            labels_file=LABELS_FILE,
            rationale_file=RATIONALE_FILE,
            rationale_max_chars=self.rationale_max_chars,
            min_correct=self.min_correct,
        )


@dataclass(frozen=True)
class ReviewVerdict:
    """The feeder's decision on one classification submission."""

    approved: bool
    correct: int
    reason: str


# ----------------------------------------------------------------------
# Rendering (poster side)
# ----------------------------------------------------------------------


def _data_json(data: ClassificationTaskData) -> dict[str, Any]:
    return {
        "workload": CLASSIFICATION_PROBLEM_TYPE,
        "task_type": data.task_type,
        "labels": list(data.labels),
        "inputs": [{"id": item_id, "text": text} for item_id, text in data.inputs],
        "outputs": {
            "labels_file": data.labels_file,
            "rationale_file": data.rationale_file,
            "rationale_max_chars": data.rationale_max_chars,
        },
        "acceptance": {"min_correct": data.min_correct},
    }


def render_spec(task: ClassificationTask) -> str:
    """Render the precise public specification for one task."""
    data = task.public_data()
    count = len(data.inputs)
    lines = [
        f"TASK: {task.instructions}",
        "",
        f"LABELS (choose exactly one per input): {', '.join(data.labels)}",
        "",
        "INPUTS:",
        *(f"  [{item_id}] {text}" for item_id, text in data.inputs),
        "",
        "OUTPUTS (upload both files, then submit):",
        f"  1. {data.labels_file}: a JSON object mapping every input id to exactly one label.",
        f"  2. {data.rationale_file}: plain text, at most {data.rationale_max_chars} "
        "characters, one line per input id explaining the label.",
        "",
        f"ACCEPTANCE: approved when {data.labels_file} is a valid JSON object covering every "
        f"input id with allowed labels, {data.rationale_file} is nonempty, and at least "
        f"{data.min_correct} of {count} labels match the poster's reference labels. "
        "Otherwise the submission is disputed.",
        "",
        _DATA_BEGIN,
        json.dumps(_data_json(data), sort_keys=True, ensure_ascii=False),
        _DATA_END,
    ]
    return "\n".join(lines)


def render_raw_task(task: ClassificationTask) -> RawTask:
    """Render one authored task into the feeder's ``RawTask`` source shape."""
    reference = json.dumps(dict(task.expected), sort_keys=True, separators=(",", ":"))
    return RawTask(
        title=task.title,
        spec=render_spec(task),
        solutions=(reference,),
        level=task.level,
        problem_type=CLASSIFICATION_PROBLEM_TYPE,
        solution_note=None,
    )


def load_seed(path: Path) -> list[ClassificationTask]:
    """Load structured seed tasks from a JSON file (a list of task objects)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ClassificationSpecError(f"Seed file must contain a JSON list: {path}")
    tasks: list[ClassificationTask] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ClassificationSpecError("Seed entries must be objects")
        tasks.append(
            ClassificationTask(
                title=str(item["title"]),
                task_type=str(item["task_type"]),
                level=int(item["level"]),
                instructions=str(item["instructions"]),
                labels=tuple(str(label) for label in item["labels"]),
                inputs=tuple((str(entry["id"]), str(entry["text"])) for entry in item["inputs"]),
                expected={str(k): str(v) for k, v in item["expected"].items()},
                min_correct=int(item["min_correct"]),
                rationale_max_chars=int(item["rationale_max_chars"]),
            )
        )
    return tasks


def write_task_source(tasks: list[ClassificationTask], out_path: Path) -> None:
    """Write tasks as the JSONL source the task feeder reads."""
    lines = [json.dumps(render_raw_task(task).as_json(), ensure_ascii=False) for task in tasks]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------
# Parsing (worker side)
# ----------------------------------------------------------------------


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ClassificationSpecError(message)


def parse_task_spec(spec: str) -> ClassificationTaskData:
    """Parse the public task data block from a posted spec."""
    begin = spec.find(_DATA_BEGIN)
    end = spec.find(_DATA_END)
    _require(begin >= 0 and end > begin, "spec has no classification task data block")
    try:
        data = json.loads(spec[begin + len(_DATA_BEGIN) : end])
    except json.JSONDecodeError as exc:
        raise ClassificationSpecError("task data block is not valid JSON") from exc
    _require(isinstance(data, dict), "task data must be an object")
    _require(data.get("workload") == CLASSIFICATION_PROBLEM_TYPE, "not a classification task")
    labels = data.get("labels")
    inputs = data.get("inputs")
    outputs = data.get("outputs")
    acceptance = data.get("acceptance")
    _require(isinstance(labels, list) and bool(labels), "labels must be a nonempty list")
    _require(isinstance(inputs, list) and bool(inputs), "inputs must be inline in this version")
    _require(isinstance(outputs, dict) and isinstance(acceptance, dict), "missing contract")
    assert isinstance(labels, list) and isinstance(inputs, list)  # narrowed above
    assert isinstance(outputs, dict) and isinstance(acceptance, dict)
    parsed_inputs: list[tuple[str, str]] = []
    for entry in inputs:
        _require(isinstance(entry, dict), "each input must be an object")
        item_id, text = entry.get("id"), entry.get("text")
        _require(isinstance(item_id, str) and isinstance(text, str), "input needs id and text")
        parsed_inputs.append((str(item_id), str(text)))
    task_type = data.get("task_type")
    labels_file = outputs.get("labels_file")
    rationale_file = outputs.get("rationale_file")
    max_chars = outputs.get("rationale_max_chars")
    min_correct = acceptance.get("min_correct")
    _require(isinstance(task_type, str) and bool(task_type), "task_type must be text")
    _require(isinstance(labels_file, str) and isinstance(rationale_file, str), "output names")
    _require(type(max_chars) is int and type(min_correct) is int, "numeric contract fields")
    return ClassificationTaskData(
        task_type=str(task_type),
        labels=tuple(str(label) for label in labels),
        inputs=tuple(parsed_inputs),
        labels_file=str(labels_file),
        rationale_file=str(rationale_file),
        rationale_max_chars=int(str(max_chars)),
        min_correct=int(str(min_correct)),
    )


def is_classification_task(raw_task: RawTask) -> bool:
    """Return True when a feeder source task belongs to this workload."""
    return raw_task.problem_type == CLASSIFICATION_PROBLEM_TYPE


def reference_labels(raw_task: RawTask) -> dict[str, str]:
    """Return the poster's private reference labels for a source task."""
    _require(len(raw_task.solutions) == 1, "classification task needs one reference")
    value = json.loads(raw_task.solutions[0])
    _require(isinstance(value, dict), "reference labels must be an object")
    return {str(k): str(v) for k, v in value.items()}


# ----------------------------------------------------------------------
# Review (poster side)
# ----------------------------------------------------------------------


def _parse_labels(data: ClassificationTaskData, labels_file: bytes | None) -> dict[str, Any] | str:
    """Return the submitted labels object, or why it violates the output contract."""
    if labels_file is None:
        return f"No {data.labels_file} output was uploaded"
    try:
        submitted = json.loads(labels_file.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"{data.labels_file} is not valid UTF-8 JSON"
    if not isinstance(submitted, dict):
        return f"{data.labels_file} must be a JSON object"
    missing = sorted(set(data.input_ids) - set(submitted))
    if missing:
        return f"{data.labels_file} misses input ids {missing}"
    disallowed = sorted({str(value) for value in submitted.values() if value not in data.labels})
    if disallowed:
        return f"{data.labels_file} uses labels outside the allowed set: {disallowed}"
    return submitted


def review_submission(
    raw_task: RawTask,
    *,
    labels_file: bytes | None,
    rationale_file: bytes | None,
) -> ReviewVerdict:
    """Apply the spec's published acceptance rule to the two submitted outputs."""
    data = parse_task_spec(raw_task.spec)
    expected = reference_labels(raw_task)
    total = len(data.inputs)
    submitted = _parse_labels(data, labels_file)
    if isinstance(submitted, str):
        return ReviewVerdict(approved=False, correct=0, reason=submitted)
    wrong = [item_id for item_id in data.input_ids if submitted[item_id] != expected[item_id]]
    correct = total - len(wrong)
    if rationale_file is None or not rationale_file.strip():
        reason = f"No nonempty {data.rationale_file} output was uploaded"
        return ReviewVerdict(approved=False, correct=correct, reason=reason)
    if correct < data.min_correct:
        reason = (
            f"{correct} of {total} labels match the reference; the spec requires at least "
            f"{data.min_correct}. Mismatched ids: {wrong}"
        )
        return ReviewVerdict(approved=False, correct=correct, reason=reason)
    return ReviewVerdict(
        approved=True,
        correct=correct,
        reason=f"{correct} of {total} labels match the reference",
    )
