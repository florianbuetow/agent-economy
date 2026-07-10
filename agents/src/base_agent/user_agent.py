"""Compatibility shim — ``UserAgent`` moved to ``service_auth`` (WP-02)."""

from service_auth.user_agent import UserAgent

__all__ = ["UserAgent"]
