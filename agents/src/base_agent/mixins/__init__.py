"""Compatibility shim — mixins moved to ``service_auth`` (WP-02)."""

from service_auth.mixins import (
    BankMixin,
    CourtMixin,
    IdentityMixin,
    ReputationMixin,
    TaskBoardMixin,
)

__all__ = [
    "BankMixin",
    "CourtMixin",
    "IdentityMixin",
    "ReputationMixin",
    "TaskBoardMixin",
]
