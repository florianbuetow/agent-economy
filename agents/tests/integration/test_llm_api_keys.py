"""Integration tests that verify LLM API keys work against live endpoints.

Each test is skipped if the corresponding environment variable is not set.
These tests make real API calls — they are not mocked.
"""

from __future__ import annotations

import os

import pytest

from math_worker.config import LLMConfig
from math_worker.llm_client import LLMClient

SYSTEM_PROMPT = "You are a helpful assistant. Reply in one short sentence."
USER_PROMPT = "Hello, how are you?"


def _make_client(base_url: str, api_key: str, model_id: str) -> LLMClient:
    config = LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model_id=model_id,
        temperature=0.5,
        max_tokens=256,
    )
    return LLMClient(config)


@pytest.mark.integration
class TestLLMApiKeys:
    """Verify that configured API keys can complete a simple chat request."""

    @pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY not set",
    )
    @pytest.mark.asyncio
    async def test_openai_api_key(self) -> None:
        client = _make_client(
            base_url="https://api.openai.com/v1",
            api_key=os.environ["OPENAI_API_KEY"],
            model_id="gpt-4o-mini",
        )
        try:
            response = await client.complete(SYSTEM_PROMPT, USER_PROMPT)
            assert response.content, "OpenAI returned empty content"
            assert len(response.content) > 0
            assert response.completion_tokens > 0
        finally:
            await client.close()

    @pytest.mark.skipif(
        not os.environ.get("MISTRAL_API_KEY"),
        reason="MISTRAL_API_KEY not set",
    )
    @pytest.mark.asyncio
    async def test_mistral_api_key(self) -> None:
        client = _make_client(
            base_url="https://api.mistral.ai/v1",
            api_key=os.environ["MISTRAL_API_KEY"],
            model_id="mistral-small-latest",
        )
        try:
            response = await client.complete(SYSTEM_PROMPT, USER_PROMPT)
            assert response.content, "Mistral returned empty content"
            assert len(response.content) > 0
            assert response.completion_tokens > 0
        finally:
            await client.close()

    @pytest.mark.skipif(
        not os.environ.get("LMSTUDIO_API_KEY"),
        reason="LMSTUDIO_API_KEY not set",
    )
    @pytest.mark.asyncio
    async def test_lmstudio_api_key(self) -> None:
        client = _make_client(
            base_url="http://127.0.0.1:1234/v1",
            api_key=os.environ["LMSTUDIO_API_KEY"],
            model_id="gemma-3-1b-it",
        )
        try:
            response = await client.complete(SYSTEM_PROMPT, USER_PROMPT)
            assert response.content, "LM Studio returned empty content"
            assert len(response.content) > 0
            assert response.completion_tokens > 0
        finally:
            await client.close()
