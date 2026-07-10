"""Compatibility shim — ``BaseAgent`` moved to ``service_auth`` (WP-02)."""

from service_auth.agent import BaseAgent

__all__ = ["BaseAgent"]
