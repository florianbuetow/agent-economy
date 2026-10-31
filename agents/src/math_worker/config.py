"""Configuration for the Math Worker Agent."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LLMConfig(BaseModel):
    """OpenAI-compatible LLM endpoint configuration."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    api_key: str
    model_id: str
    temperature: float
    max_tokens: int


class MathWorkerConfig(BaseModel):
    """Math Worker Agent behaviour settings."""

    model_config = ConfigDict(extra="forbid")

    handle: str
    scan_interval_seconds: int
    poll_interval_seconds: int
    max_poll_attempts: int
    error_backoff_seconds: int
    min_reward: int
    max_reward: int
