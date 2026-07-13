"""Deterministic arithmetic transport for the T-102 headline e2e test.

Test infrastructure, not a config.yaml worker profile: this is not an LLM at
all, just a parser-driven fake that answers each of ``MathWorkerLoop``'s
decision points by reading the structured prompt text the real prompt
builders (``math_worker.prompts``) produce. Putting a non-LLM fake into
``workers:`` — the section that enumerates real LLM-backed profiles for
``WorkerFactory`` — would misrepresent it as a production option, so it
lives here instead and is wired directly via ``LLMClient(config,
transport=...)`` (T-102's injectable seam).
"""

from __future__ import annotations

import re

from math_worker.llm_client import LLMResponse
from math_worker.prompts import (
    BID_AMOUNT_SYSTEM,
    DISPUTE_REBUTTAL_SYSTEM,
    SOLVE_PROBLEM_SYSTEM,
    TASK_SELECTION_SYSTEM,
)

_TASK_ID_RE = re.compile(r"--- task_id: (\S+) ---")
_REWARD_RE = re.compile(r"Reward: (\d+)")
_ARITHMETIC_RE = re.compile(r"Calculate (\d+) \+ (\d+)")

_WRONG_ANSWER = "-999999"


class DeterministicArithmeticTransport:
    """Deterministic ``LLMTransport`` (T-102) — solves injected arithmetic
    problems without calling any LLM.

    Args:
        correct: When True, ``SOLVE_PROBLEM_SYSTEM`` answers are computed
            correctly by parsing "Calculate A + B" out of the prompt. When
            False, a fixed wrong answer is always returned regardless of
            the problem — the headline e2e's "wrong-answer variant" that
            drives the dispute path.
    """

    def __init__(self, *, correct: bool) -> None:
        self._correct = correct

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        if system_prompt == TASK_SELECTION_SYSTEM:
            content = self._select_task(user_prompt)
        elif system_prompt == BID_AMOUNT_SYSTEM:
            content = self._decide_bid(user_prompt)
        elif system_prompt == SOLVE_PROBLEM_SYSTEM:
            content = self._solve(user_prompt)
        elif system_prompt == DISPUTE_REBUTTAL_SYSTEM:
            content = "The submitted answer follows the specification; no error was made."
        else:
            msg = f"DeterministicArithmeticTransport: unknown system prompt: {system_prompt!r}"
            raise ValueError(msg)

        return LLMResponse(
            content=content,
            finish_reason="stop",
            prompt_tokens=0,
            completion_tokens=0,
        )

    async def close(self) -> None:
        """No resources to release."""

    def _select_task(self, user_prompt: str) -> str:
        match = _TASK_ID_RE.search(user_prompt)
        if match is None:
            return "NONE"
        return match.group(1)

    def _decide_bid(self, user_prompt: str) -> str:
        match = _REWARD_RE.search(user_prompt)
        reward = int(match.group(1)) if match else 1
        return str(reward)

    def _solve(self, user_prompt: str) -> str:
        if not self._correct:
            return f"ANSWER: {_WRONG_ANSWER}"

        match = _ARITHMETIC_RE.search(user_prompt)
        if match is None:
            msg = (
                "DeterministicArithmeticTransport: could not parse "
                f"'Calculate A + B' from prompt: {user_prompt!r}"
            )
            raise ValueError(msg)
        a, b = int(match.group(1)), int(match.group(2))
        return f"ANSWER: {a + b}"
