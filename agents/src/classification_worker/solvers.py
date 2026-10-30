"""Classification strategies and the worker's two submitted outputs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from math_worker.llm_client import LLMResponse
    from task_feeder.workloads.classification import ClassificationTaskData


@dataclass(frozen=True)
class Answer:
    """One classification answer: a label per input id plus a rationale."""

    labels: Mapping[str, str]
    rationale: str

    def to_json(self) -> str:
        return json.dumps(
            {"labels": dict(self.labels), "rationale": self.rationale}, sort_keys=True
        )

    @classmethod
    def from_json(cls, text: str) -> Answer:
        value = json.loads(text)
        return cls(labels=dict(value["labels"]), rationale=str(value["rationale"]))


class Solver(Protocol):
    """A classification strategy."""

    async def solve(self, task: ClassificationTaskData) -> Answer | None:
        """Return a complete answer, or None when no valid answer could be produced."""
        ...


class CompletionClient(Protocol):
    """The OpenAI-compatible completion seam shared with the math worker."""

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse: ...


def _valid(task: ClassificationTaskData, labels: Mapping[str, Any]) -> bool:
    return set(labels) >= set(task.input_ids) and all(
        labels[item_id] in task.labels for item_id in task.input_ids
    )


class KeywordSolver:
    """Deterministic strategy: count configured keyword hits per allowed label.

    Ties and zero hits resolve to the earliest label in the task's own label
    order, so the result depends only on the task and the configured table.
    """

    def __init__(self, keywords: Mapping[str, Sequence[str]]) -> None:
        self._keywords = {
            label: tuple(word.lower() for word in words) for label, words in keywords.items()
        }

    async def solve(self, task: ClassificationTaskData) -> Answer | None:
        labels: dict[str, str] = {}
        lines: list[str] = []
        for item_id, text in task.inputs:
            lowered = text.lower()
            best_label = task.labels[0]
            best_hits: list[str] = []
            for label in task.labels:
                hits = [word for word in self._keywords.get(label, ()) if word in lowered]
                if len(hits) > len(best_hits):
                    best_label, best_hits = label, hits
            labels[item_id] = best_label
            if best_hits:
                lines.append(f"{item_id}: {best_label} (keywords: {', '.join(best_hits)})")
            else:
                lines.append(f"{item_id}: {best_label} (no keyword matched; first listed label)")
        return Answer(labels=labels, rationale="\n".join(lines))


SYSTEM_PROMPT = (
    "You are a careful text classifier. Assign exactly one allowed label to every input. "
    'Reply with only one JSON object: {"labels": {"<id>": "<label>"}, '
    '"rationale": {"<id>": "<one short sentence>"}}.'
)

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def build_prompt(task: ClassificationTaskData) -> str:
    """User prompt listing the allowed labels and every input."""
    items = "\n".join(f"[{item_id}] {text}" for item_id, text in task.inputs)
    return f"Allowed labels: {', '.join(task.labels)}\n\nInputs:\n{items}"


class LLMSolver:
    """LLM strategy through the configured OpenAI-compatible provider."""

    def __init__(self, client: CompletionClient) -> None:
        self._client = client

    async def solve(self, task: ClassificationTaskData) -> Answer | None:
        response = await self._client.complete(SYSTEM_PROMPT, build_prompt(task))
        match = _JSON_OBJECT.search(response.content)
        if match is None:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        labels = value.get("labels")
        rationale = value.get("rationale")
        if not isinstance(labels, dict) or not _valid(task, labels):
            return None
        reasons = rationale if isinstance(rationale, dict) else {}
        lines = [
            f"{item_id}: {labels[item_id]} ({reasons.get(item_id, 'no reason given')})"
            for item_id in task.input_ids
        ]
        return Answer(
            labels={item_id: str(labels[item_id]) for item_id in task.input_ids},
            rationale="\n".join(lines),
        )


def build_outputs(task: ClassificationTaskData, answer: Answer) -> list[tuple[str, bytes]]:
    """The two named deliverables, in upload order: labels file, then rationale file.

    This is the single place that turns an answer into submitted files, so a
    manifest-based submit protocol can consume the same list.
    """
    labels = {item_id: answer.labels[item_id] for item_id in task.input_ids}
    rationale = answer.rationale[: task.rationale_max_chars]
    return [
        (task.labels_file, json.dumps(labels, ensure_ascii=False).encode("utf-8")),
        (task.rationale_file, rationale.encode("utf-8")),
    ]
