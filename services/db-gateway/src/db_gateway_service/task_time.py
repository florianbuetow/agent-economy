"""Exact integer-microsecond helpers for Task Board persistence contracts."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from db_gateway_service.schema import SQLITE_MAX_INTEGER

_UTC_TEXT = re.compile(
    r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)\Z"
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECONDS_PER_SECOND = 1_000_000


def parse_utc_text_to_epoch_us(value: object) -> int:
    """Parse strict UTC ISO text without floating-point timestamp conversion."""
    if not isinstance(value, str) or _UTC_TEXT.fullmatch(value) is None:
        raise ValueError("timestamp must be explicit ISO 8601 UTC text")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError("timestamp must be valid ISO 8601 UTC text") from None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp must use UTC")
    delta = parsed - _EPOCH
    result = (
        delta.days * 86_400 * _MICROSECONDS_PER_SECOND
        + delta.seconds * _MICROSECONDS_PER_SECOND
        + delta.microseconds
    )
    if result < 0 or result > SQLITE_MAX_INTEGER:
        raise ValueError("timestamp is outside the supported SQLite integer range")
    return result


def add_seconds_to_epoch_us(base_us: object, seconds: object) -> int:
    """Add an explicit nonnegative integer duration with checked arithmetic."""
    if type(base_us) is not int or not 0 <= base_us <= SQLITE_MAX_INTEGER:
        raise ValueError("base timestamp must be a nonnegative SQLite integer")
    if type(seconds) is not int or seconds < 0:
        raise ValueError("duration must be a nonnegative integer")
    if seconds > (SQLITE_MAX_INTEGER - base_us) // _MICROSECONDS_PER_SECOND:
        raise ValueError("deadline exceeds the supported SQLite integer range")
    return base_us + seconds * _MICROSECONDS_PER_SECOND


def epoch_us_to_utc_text(value: object) -> str:
    """Format an integer epoch microsecond using the established UTC-Z form."""
    if type(value) is not int or not 0 <= value <= SQLITE_MAX_INTEGER:
        raise ValueError("timestamp must be a nonnegative SQLite integer")
    try:
        rendered = _EPOCH + timedelta(microseconds=value)
    except OverflowError:
        raise ValueError("timestamp is outside the supported datetime range") from None
    return rendered.isoformat(timespec="auto").replace("+00:00", "Z")


def utc_now_us() -> int:
    """Return current UTC as integer epoch microseconds without float conversion."""
    delta = datetime.now(UTC) - _EPOCH
    return (
        delta.days * 86_400 * _MICROSECONDS_PER_SECOND
        + delta.seconds * _MICROSECONDS_PER_SECOND
        + delta.microseconds
    )


__all__ = [
    "add_seconds_to_epoch_us",
    "epoch_us_to_utc_text",
    "parse_utc_text_to_epoch_us",
    "utc_now_us",
]
