"""Async client for chat completions, with an injectable transport seam.

Production wiring is unchanged: ``LLMClient(config)`` builds the same
OpenAI-compatible transport it always has, config-driven (``config.yaml``'s
``workers.<profile>.llm`` section), no environment variables beyond the
already-sanctioned ``${VAR}`` API-key resolution (Q-7).

T-102 adds a second constructor path — ``LLMClient(config, transport=...)``
— so tests can run the worker loop LLM-free by injecting a fake
``LLMTransport`` (e.g. a deterministic arithmetic solver) instead of the
real ``AsyncOpenAI``-backed one. ``_select_task``/``_decide_bid``/``_solve``
in ``MathWorkerLoop`` all go through ``LLMClient.complete()``, so injecting
a transport there is enough to make the whole loop deterministic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

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


class LLMTransport(Protocol):
    """Injectable transport seam for ``LLMClient`` (T-102).

    Anything satisfying this protocol can stand in for the real
    OpenAI-compatible backend: a deterministic arithmetic solver for tests,
    or any other completion source.
    """

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Produce a completion for the given system/user prompt pair."""
        ...

    async def close(self) -> None:
        """Release any resources held by the transport."""
        ...


class _AsyncOpenAITransport:
    """Production transport — an OpenAI-compatible chat completion endpoint.

    Uses the ``openai`` SDK pointed at a custom ``base_url``
    (e.g. LM Studio at ``http://127.0.0.1:1234/v1``).
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        """Send a chat completion request and return the parsed response.

        Args:
            system_prompt: System-level instructions for the model.
            user_prompt:   The user message / question.

        Returns:
            An ``LLMResponse`` with the model's text and usage stats.

        Raises:
            RuntimeError: If the response contains no content.
        """
        logger.debug(
            "LLM request: model=%s tokens_limit=%d", self._config.model_id, self._config.max_tokens
        )

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


class LLMClient:
    """Client for chat completions, config-driven with an injectable transport.

    Args:
        config: LLM endpoint configuration. Always required (even when
            ``transport`` is supplied) to keep the constructor shape
            stable; unused by injected transports.
        transport: Optional injectable transport (T-102). When omitted,
            the real ``AsyncOpenAI``-backed transport is built from
            ``config`` — production behavior is unchanged.
    """

    def __init__(self, config: LLMConfig, transport: LLMTransport | None = None) -> None:
        self._config = config
        self._transport: LLMTransport = (
            transport if transport is not None else _AsyncOpenAITransport(config)
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
        """
        return await self._transport.complete(system_prompt, user_prompt)

    async def close(self) -> None:
        """Close the underlying transport."""
        await self._transport.close()
