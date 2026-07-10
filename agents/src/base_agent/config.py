"""Compatibility shim — agent config moved to ``service_auth`` (WP-02)."""

from service_auth.config import AgentConfig, load_agent_config

__all__ = ["AgentConfig", "load_agent_config"]
