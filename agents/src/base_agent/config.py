"""Compatibility shim — agent config moved to ``service_auth`` (WP-02)."""

from service_auth.config import AgentConfig, load_agent_config
from service_auth.jws import TokenPolicy

__all__ = ["AgentConfig", "TokenPolicy", "load_agent_config"]
