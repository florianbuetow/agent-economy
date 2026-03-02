"""LLM dress-up: rewrites deterministic math tasks as rich narrative word problems."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from math_task_factory.types import MathTask

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are rewriting a math problem as a rich, realistic word problem.

RULES:
- Preserve ALL numerical values exactly as given
- Preserve all mathematical operations (add, subtract, multiply, divide, modulo, percentage)
- Preserve rounding rules exactly (integer division means floor/truncate toward zero)
- Add realistic scenario context (warehouse, bakery, school, factory, farm, etc.)
- Add character names and narrative details
- Make the problem feel like a real-world scenario
- The answer must remain identical
- Keep the OUTPUT FORMAT and VERIFICATION sections from the original
- Do NOT solve the problem or include the answer anywhere in your output
- Do NOT include lines like "Output:", "Answer:", or "Solution:"
- Do NOT include any preamble, explanation, or markdown headers before the problem

Output ONLY the rewritten problem specification text, nothing else.\
"""


@dataclass(frozen=True)
class LLMDressUpConfig:
    """Configuration for the LLM dress-up client."""

    base_url: str
    api_key: str
    model_id: str
    temperature: float
    max_tokens: int
    max_retries: int


class LLMDressUp:
    """Rewrites deterministic math tasks as rich narrative word problems via LLM."""

    def __init__(self, config: LLMDressUpConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def dress_up(self, task: MathTask) -> MathTask | None:
        """Rewrite a deterministic task's spec as a rich word problem.

        Returns a new MathTask with the LLM-rewritten spec but identical solutions.
        On failure after retries, returns None (task is skipped).
        """
        original_numbers = _extract_numbers(task.spec)

        for attempt in range(1, self._config.max_retries + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=self._config.model_id,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": task.spec},
                    ],
                    temperature=self._config.temperature,
                    max_tokens=self._config.max_tokens,
                )
                content = response.choices[0].message.content
                if content is None:
                    logger.warning("LLM returned empty content (attempt %d)", attempt)
                    continue

                new_spec = _strip_answer_leakage(content.strip())

                # Sanity check: all original numbers should appear in the new spec
                missing = _check_numbers_preserved(original_numbers, new_spec)
                if missing:
                    logger.warning(
                        "Dress-up attempt %d: missing numbers %s, retrying",
                        attempt,
                        missing,
                    )
                    continue

                # Check the LLM didn't leak the answer
                if _has_answer_leakage(new_spec, task.solutions):
                    logger.warning(
                        "Dress-up attempt %d: answer leaked in spec, retrying",
                        attempt,
                    )
                    continue

                return MathTask(
                    title=task.title,
                    spec=new_spec,
                    solutions=task.solutions,
                    level=task.level,
                    problem_type=task.problem_type,
                    solution_note=task.solution_note,
                )

            except Exception:
                logger.exception("LLM dress-up attempt %d failed", attempt)

        logger.warning(
            "All %d dress-up attempts failed for '%s', skipping",
            self._config.max_retries,
            task.title,
        )
        return None

    async def dress_up_batch(self, tasks: list[MathTask]) -> list[MathTask]:
        """Dress up multiple tasks concurrently. Failed tasks are skipped."""
        results = await asyncio.gather(*(self.dress_up(t) for t in tasks))
        return [t for t in results if t is not None]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()


def _extract_numbers(text: str) -> list[str]:
    """Extract number-like substrings (2+ digits) from text for validation.

    Single-digit numbers are skipped because they cause false validation
    failures — the LLM might spell them out ("five") or they appear as
    incidental parts of other numbers.
    """
    import re

    return [n for n in re.findall(r"\d+(?:\.\d+)?", text) if len(n) >= 2]


def _check_numbers_preserved(
    original_numbers: list[str], new_text: str
) -> list[str]:
    """Return list of original numbers not found in new_text."""
    return [n for n in original_numbers if n not in new_text]


def _strip_answer_leakage(text: str) -> str:
    """Remove lines that look like the LLM solving the problem."""
    import re

    # Strip common preamble/answer patterns at the start
    lines = text.split("\n")
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip().lower()
        if re.match(
            r"^(\*\*)?(?:output|answer|solution|result)\s*[:=]",
            stripped,
        ):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _has_answer_leakage(spec: str, solutions: list[str]) -> bool:
    """Check if the spec contains a line that directly states the answer."""
    import re

    for line in spec.split("\n"):
        stripped = line.strip().lower()
        # Look for "the answer is X" or "= X" patterns outside VERIFICATION
        if re.match(r"^(?:the\s+)?(?:answer|result|solution)\s+is\s+", stripped):
            for sol in solutions:
                if sol.strip().lower() in stripped:
                    return True
    return False
