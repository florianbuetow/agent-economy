"""Unit tests for math_worker.config."""

import pytest
from pydantic import ValidationError

from math_worker.config import LLMConfig, MathWorkerConfig


@pytest.mark.unit
class TestLLMConfig:
    def test_creates_config(self) -> None:
        config = LLMConfig(
            base_url="http://127.0.0.1:1234/v1",
            api_key="lm-studio",
            model_id="gemma-3-1b-it",
            temperature=0.7,
            max_tokens=2048,
        )
        assert config.base_url == "http://127.0.0.1:1234/v1"
        assert config.model_id == "gemma-3-1b-it"

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            LLMConfig(
                base_url="http://127.0.0.1:1234/v1",
                api_key="lm-studio",
                model_id="gemma-3-1b-it",
                temperature=0.7,
                max_tokens=2048,
                unknown_field="oops",  # type: ignore[call-arg]
            )


@pytest.mark.unit
class TestMathWorkerConfig:
    def test_creates_config(self) -> None:
        config = MathWorkerConfig(
            handle="mathbot",
            scan_interval_seconds=10,
            poll_interval_seconds=3,
            max_poll_attempts=100,
            error_backoff_seconds=5,
            min_reward=50,
            max_reward=10000,
        )
        assert config.handle == "mathbot"
        assert config.scan_interval_seconds == 10
        assert config.min_reward == 50

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            MathWorkerConfig(
                handle="mathbot",
                scan_interval_seconds=10,
                poll_interval_seconds=3,
                max_poll_attempts=100,
                error_backoff_seconds=5,
                min_reward=50,
                max_reward=10000,
                bad="field",  # type: ignore[call-arg]
            )
