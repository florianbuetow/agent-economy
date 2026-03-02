"""LiteLLM-backed judge implementation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import litellm
from service_commons.exceptions import ServiceError

from court_service.judges.base import DisputeContext, Judge, JudgeVote
from court_service.judges.prompts import EVALUATION_TEMPLATE, SYSTEM_PROMPT


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _extract_content(response: Any) -> str:
    """Extract content from LiteLLM response object."""
    choices: Any
    if isinstance(response, dict):
        choices = response.get("choices")
    else:
        choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or len(choices) == 0:
        raise ValueError("Missing choices in LLM response")

    first = choices[0]
    message: Any = (
        first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
    )
    if message is None:
        raise ValueError("Missing message in LLM response")

    content: Any
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if not isinstance(content, str) or content.strip() == "":
        raise ValueError("Missing content in LLM response")

    return content


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM output that may contain markdown fences or preamble."""
    stripped = text.strip()
    # Strip markdown code fences if present
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        lines = lines[1:]  # drop opening fence line
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    # Find the first { and last } to extract the JSON object
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        msg = f"No JSON object found in LLM response: {text[:200]}"
        raise ValueError(msg)
    return cast("dict[str, Any]", json.loads(stripped[start : end + 1]))


class LLMJudge(Judge):
    """Judge implementation backed by LiteLLM."""

    def __init__(
        self,
        judge_id: str,
        model: str,
        temperature: float,
        api_base: str | None,
        api_key: str | None,
    ) -> None:
        self._judge_id = judge_id
        self._model = model
        self._temperature = temperature
        self._api_base = api_base
        self._api_key = api_key

    async def evaluate(self, context: DisputeContext) -> JudgeVote:
        """Evaluate dispute context and return a vote."""
        rebuttal_text = (
            context.rebuttal if context.rebuttal is not None else "No rebuttal submitted"
        )
        prompt = EVALUATION_TEMPLATE.format(
            task_title=context.task_title,
            reward=context.reward,
            task_spec=context.task_spec,
            deliverables=json.dumps(context.deliverables, ensure_ascii=True),
            claim=context.claim,
            rebuttal=rebuttal_text,
        )

        try:
            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=self._temperature,
                api_base=self._api_base,
                api_key=self._api_key,
            )
            content = _extract_content(response)
            parsed = _extract_json(content)
            worker_pct = parsed.get("worker_pct")
            reasoning = parsed.get("reasoning")
            if not isinstance(worker_pct, int) or not 0 <= worker_pct <= 100:
                raise ValueError("worker_pct must be an integer in [0, 100]")
            if not isinstance(reasoning, str) or reasoning.strip() == "":
                raise ValueError("reasoning must be a non-empty string")
        except Exception as exc:
            raise ServiceError(
                "judge_unavailable",
                f"Judge {self._judge_id} failed: {exc}",
                502,
                {},
            ) from exc

        return JudgeVote(
            judge_id=self._judge_id,
            worker_pct=worker_pct,
            reasoning=reasoning,
            voted_at=_utc_now_iso(),
        )
