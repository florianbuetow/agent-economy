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


class LLMJudge(Judge):
    """Judge implementation backed by LiteLLM."""

    def __init__(self, judge_id: str, model: str, temperature: float) -> None:
        self._judge_id = judge_id
        self._model = model
        self._temperature = temperature

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
                response_format={"type": "json_object"},
            )
            content = _extract_content(response)
            parsed = cast("dict[str, Any]", json.loads(content))
            worker_pct = parsed.get("worker_pct")
            reasoning = parsed.get("reasoning")
            if not isinstance(worker_pct, int) or not 0 <= worker_pct <= 100:
                raise ValueError("worker_pct must be an integer in [0, 100]")
            if not isinstance(reasoning, str) or reasoning.strip() == "":
                raise ValueError("reasoning must be a non-empty string")
        except Exception as exc:
            raise ServiceError(
                "JUDGE_UNAVAILABLE",
                f"Judge {self._judge_id} unavailable",
                502,
                {},
            ) from exc

        return JudgeVote(
            judge_id=self._judge_id,
            worker_pct=worker_pct,
            reasoning=reasoning,
            voted_at=_utc_now_iso(),
        )
