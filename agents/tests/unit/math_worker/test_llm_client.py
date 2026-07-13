"""Unit tests for math_worker.llm_client's injectable transport seam (T-102).

Production wiring (no ``transport`` argument) must keep building the real
``AsyncOpenAI``-backed transport from config, config-driven, no env vars
beyond the already-sanctioned ``${VAR}`` LLM-key resolution. Injecting a
transport must fully bypass that construction, so tests can drive
``MathWorkerLoop`` LLM-free.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from math_worker.config import LLMConfig
from math_worker.llm_client import LLMClient, LLMResponse, _AsyncOpenAITransport


def _make_config() -> LLMConfig:
    return LLMConfig(
        base_url="http://127.0.0.1:1234/v1",
        api_key="lm-studio",
        model_id="gemma-3-1b-it",
        temperature=0.7,
        max_tokens=2048,
    )


class _FakeTransport:
    def __init__(self) -> None:
        self.complete = AsyncMock(
            return_value=LLMResponse(
                content="ANSWER: 42",
                finish_reason="stop",
                prompt_tokens=1,
                completion_tokens=1,
            )
        )
        self.close = AsyncMock()


@pytest.mark.unit
class TestProductionWiringUnchanged:
    def test_no_transport_builds_real_openai_transport(self) -> None:
        client = LLMClient(_make_config())
        assert isinstance(client._transport, _AsyncOpenAITransport)

    def test_production_transport_is_config_driven(self) -> None:
        config = _make_config()
        client = LLMClient(config)
        transport = client._transport
        assert isinstance(transport, _AsyncOpenAITransport)
        assert transport._config is config


@pytest.mark.unit
class TestInjectableTransportSeam:
    def test_injected_transport_bypasses_openai_construction(self) -> None:
        fake = _FakeTransport()
        client = LLMClient(_make_config(), transport=fake)
        assert client._transport is fake

    @pytest.mark.asyncio
    async def test_complete_delegates_to_injected_transport(self) -> None:
        fake = _FakeTransport()
        client = LLMClient(_make_config(), transport=fake)

        result = await client.complete("system", "user")

        fake.complete.assert_awaited_once_with("system", "user")
        assert result.content == "ANSWER: 42"

    @pytest.mark.asyncio
    async def test_close_delegates_to_injected_transport(self) -> None:
        fake = _FakeTransport()
        client = LLMClient(_make_config(), transport=fake)

        await client.close()

        fake.close.assert_awaited_once()
