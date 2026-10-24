"""Compatibility shim — ``AgentFactory`` moved to ``service_auth`` (WP-02)."""

from service_auth.factory import AgentFactory

__all__ = ["AgentFactory"]
