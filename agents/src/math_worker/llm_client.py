"""Thin async wrapper around an OpenAI-compatible chat completion endpoint."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

if TYPE_CHECKING:
    from math_worker.config import LLMConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMResponse:
    """Parsed chat completion result."""

    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int


class LLMClient:
    """Async client for an OpenAI-compatible chat completion endpoint.

    Uses the ``openai`` SDK pointed at a custom ``base_url``
    (e.g. LM Studio at ``http://127.0.0.1:1234/v1``).
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMResponse:
        """Send a chat completion request and return the parsed response.

        Args:
            system_prompt: System-level instructions for the model.
            user_prompt:   The user message / question.

        Returns:
            An ``LLMResponse`` with the model's text and usage stats.

        Raises:
            RuntimeError: If the response contains no content.
        """
        logger.debug("LLM request: model=%s tokens_limit=%d", self._config.model_id, self._config.max_tokens)

        response = await self._client.chat.completions.create(
            model=self._config.model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )

        choice = response.choices[0]
        content = choice.message.content
        if content is None:
            msg = "LLM returned empty content"
            raise RuntimeError(msg)

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        logger.debug(
            "LLM response: finish=%s prompt_tok=%d completion_tok=%d",
            choice.finish_reason,
            prompt_tokens,
            completion_tokens,
        )

        return LLMResponse(
            content=content.strip(),
            finish_reason=choice.finish_reason or "unknown",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()
