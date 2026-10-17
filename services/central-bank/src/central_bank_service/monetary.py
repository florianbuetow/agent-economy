"""Central Bank monetary representation and guarded arithmetic."""

from __future__ import annotations

from service_auth import financial_responses
from service_commons.exceptions import ServiceError

SQLITE_MAX_INTEGER = financial_responses.SQLITE_MAX_INTEGER


def require_nonnegative_money(value: object, field_name: str) -> int:
    """Return a valid non-negative SQLite INTEGER monetary value."""
    if type(value) is not int or value < 0:
        raise ServiceError(
            "invalid_amount",
            f"{field_name} must be a non-negative integer",
            400,
            {},
        )
    if value > SQLITE_MAX_INTEGER:
        raise ServiceError(
            "amount_out_of_range",
            f"{field_name} exceeds the supported integer range",
            400,
            {},
        )
    return value


def require_positive_money(value: object, field_name: str) -> int:
    """Return a valid positive SQLite INTEGER monetary value."""
    if type(value) is not int or value <= 0:
        raise ServiceError(
            "invalid_amount",
            f"{field_name} must be a positive integer",
            400,
            {},
        )
    if value > SQLITE_MAX_INTEGER:
        raise ServiceError(
            "amount_out_of_range",
            f"{field_name} exceeds the supported integer range",
            400,
            {},
        )
    return value


def require_worker_percentage(value: object) -> int:
    """Return a strict integer percentage in the inclusive range 0..100."""
    if type(value) is not int:
        raise ServiceError("invalid_payload", "worker_pct must be an integer", 400, {})
    if value < 0 or value > 100:
        raise ServiceError("invalid_amount", "worker_pct must be 0-100", 400, {})
    return value


def checked_balance_add(balance: int, amount: int) -> int:
    """Add validated non-negative values without exceeding SQLite INTEGER."""
    result = balance + amount
    if result > SQLITE_MAX_INTEGER:
        raise ServiceError(
            "amount_out_of_range",
            "Resulting balance exceeds the supported integer range",
            409,
            {},
        )
    return result
