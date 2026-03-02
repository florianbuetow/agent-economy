"""Unit tests for worker_config models."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from base_agent.worker_config import (
    WorkerBehaviorConfig,
    WorkerLLMConfig,
    WorkerProfile,
    resolve_env_vars,
)


@pytest.mark.unit
class TestResolveEnvVars:
    """Tests for the resolve_env_vars helper."""

    def test_no_placeholders(self) -> None:
        assert resolve_env_vars("plain-string") == "plain-string"

    def test_single_placeholder(self) -> None:
        with patch.dict(os.environ, {"MY_KEY": "secret123"}):
            assert resolve_env_vars("${MY_KEY}") == "secret123"

    def test_placeholder_in_string(self) -> None:
        with patch.dict(os.environ, {"TOKEN": "abc"}):
            assert resolve_env_vars("Bearer ${TOKEN}") == "Bearer abc"

    def test_multiple_placeholders(self) -> None:
        with patch.dict(os.environ, {"A": "1", "B": "2"}):
            assert resolve_env_vars("${A}-${B}") == "1-2"

    def test_missing_env_var_raises(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(ValueError, match="NONEXISTENT_VAR"),
        ):
            resolve_env_vars("${NONEXISTENT_VAR}")

    def test_no_recursive_expansion(self) -> None:
        with patch.dict(os.environ, {"OUTER": "${INNER}", "INNER": "deep"}):
            result = resolve_env_vars("${OUTER}")
            assert result == "${INNER}"


@pytest.mark.unit
class TestWorkerLLMConfig:
    """Tests for WorkerLLMConfig validation."""

    def test_valid_config(self) -> None:
        config = WorkerLLMConfig(
            base_url="http://localhost:1234/v1",
            api_key="test-key",
            model_id="test-model",
            temperature=0.7,
            max_tokens=2048,
        )
        assert config.api_key.get_secret_value() == "test-key"
        assert config.base_url == "http://localhost:1234/v1"

    def test_https_url_accepted(self) -> None:
        config = WorkerLLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model_id="gpt-4",
            temperature=0.5,
            max_tokens=4096,
        )
        assert config.base_url == "https://api.openai.com/v1"

    def test_invalid_url_scheme_rejected(self) -> None:
        with pytest.raises(ValidationError, match="http:// or https://"):
            WorkerLLMConfig(
                base_url="ftp://evil.com",
                api_key="key",
                model_id="model",
                temperature=0.5,
                max_tokens=100,
            )

    def test_env_var_in_api_key(self) -> None:
        with patch.dict(os.environ, {"TEST_API_KEY": "resolved-secret"}):
            config = WorkerLLMConfig(
                base_url="https://api.example.com/v1",
                api_key="${TEST_API_KEY}",
                model_id="model",
                temperature=0.5,
                max_tokens=100,
            )
            assert config.api_key.get_secret_value() == "resolved-secret"

    def test_missing_env_var_in_api_key_raises(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(ValidationError, match="MISSING_KEY"),
        ):
            WorkerLLMConfig(
                base_url="https://api.example.com/v1",
                api_key="${MISSING_KEY}",
                model_id="model",
                temperature=0.5,
                max_tokens=100,
            )

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkerLLMConfig(
                base_url="http://localhost:1234/v1",
                api_key="key",
                model_id="model",
                temperature=0.5,
                max_tokens=100,
                extra_field="bad",
            )

    def test_api_key_masked_in_repr(self) -> None:
        config = WorkerLLMConfig(
            base_url="http://localhost:1234/v1",
            api_key="super-secret-key",
            model_id="model",
            temperature=0.5,
            max_tokens=100,
        )
        repr_str = repr(config)
        assert "super-secret-key" not in repr_str


@pytest.mark.unit
class TestWorkerBehaviorConfig:
    """Tests for WorkerBehaviorConfig validation."""

    def test_valid_config(self) -> None:
        config = WorkerBehaviorConfig(
            scan_interval_seconds=10,
            poll_interval_seconds=3,
            max_poll_attempts=100,
            error_backoff_seconds=5,
            min_reward=50,
            max_reward=10000,
        )
        assert config.scan_interval_seconds == 10

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkerBehaviorConfig(
                scan_interval_seconds=10,
                poll_interval_seconds=3,
                max_poll_attempts=100,
                error_backoff_seconds=5,
                min_reward=50,
                max_reward=10000,
                extra="bad",
            )


@pytest.mark.unit
class TestWorkerProfile:
    """Tests for WorkerProfile validation."""

    def _valid_profile_data(self) -> dict[str, object]:
        return {
            "handle": "test_worker",
            "type": "math_worker",
            "llm": {
                "base_url": "http://localhost:1234/v1",
                "api_key": "test-key",
                "model_id": "test-model",
                "temperature": 0.7,
                "max_tokens": 2048,
            },
            "behavior": {
                "scan_interval_seconds": 10,
                "poll_interval_seconds": 3,
                "max_poll_attempts": 100,
                "error_backoff_seconds": 5,
                "min_reward": 50,
                "max_reward": 10000,
            },
        }

    def test_valid_profile(self) -> None:
        data = self._valid_profile_data()
        profile = WorkerProfile(**data)
        assert profile.handle == "test_worker"
        assert profile.type == "math_worker"

    def test_invalid_handle_with_slashes(self) -> None:
        data = self._valid_profile_data()
        data["handle"] = "../etc/passwd"
        with pytest.raises(ValidationError, match="handle"):
            WorkerProfile(**data)

    def test_invalid_handle_with_dots(self) -> None:
        data = self._valid_profile_data()
        data["handle"] = "test.worker"
        with pytest.raises(ValidationError, match="handle"):
            WorkerProfile(**data)

    def test_unknown_type_rejected(self) -> None:
        data = self._valid_profile_data()
        data["type"] = "unknown_worker"
        with pytest.raises(ValidationError, match="Unknown worker type"):
            WorkerProfile(**data)

    def test_hyphenated_handle_accepted(self) -> None:
        data = self._valid_profile_data()
        data["handle"] = "my-worker-v2"
        profile = WorkerProfile(**data)
        assert profile.handle == "my-worker-v2"

    def test_extra_fields_rejected(self) -> None:
        data = self._valid_profile_data()
        data["extra"] = "bad"
        with pytest.raises(ValidationError):
            WorkerProfile(**data)
