"""Compatibility shim — ``PlatformAgent`` moved to ``service_auth`` (WP-02)."""

from service_auth.platform import PlatformAgent

__all__ = ["PlatformAgent"]
