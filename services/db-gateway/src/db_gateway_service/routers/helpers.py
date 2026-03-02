"""Shared request parsing and validation helpers for all routers."""

from __future__ import annotations

import json
from typing import Any

from service_commons.exceptions import ServiceError

# Required event fields
EVENT_REQUIRED_FIELDS: list[str] = [
    "event_source",
    "event_type",
    "timestamp",
    "summary",
    "payload",
]


def parse_json_body(body: bytes) -> dict[str, Any]:
    """Parse JSON body, raising ServiceError on failure."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "invalid_json",
            "Request body is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(data, dict):
        raise ServiceError(
            "invalid_json",
            "Request body must be a JSON object",
            400,
            {},
        )

    return data


def validate_required_fields(data: dict[str, Any], fields: list[str]) -> None:
    """Validate that all required fields exist, are not null, and are not empty strings."""
    for field_name in fields:
        value = data.get(field_name)
        if value is None:
            raise ServiceError(
                "missing_field",
                f"Missing required field: {field_name}",
                400,
                {"field": field_name},
            )
        if isinstance(value, str) and not value.strip():
            raise ServiceError(
                "missing_field",
                f"Field cannot be empty: {field_name}",
                400,
                {"field": field_name},
            )


def validate_event(data: dict[str, Any]) -> None:
    """Validate that 'event' field exists and has all required sub-fields."""
    event = data.get("event")
    if event is None:
        raise ServiceError(
            "missing_field",
            "Missing required field: event",
            400,
            {"field": "event"},
        )
    if not isinstance(event, dict):
        raise ServiceError(
            "missing_field",
            "Field 'event' must be an object",
            400,
            {"field": "event"},
        )
    validate_required_fields(event, EVENT_REQUIRED_FIELDS)


def validate_positive_integer(data: dict[str, Any], field_name: str) -> None:
    """Validate that a field is a positive integer."""
    value = data.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ServiceError(
            "invalid_amount",
            f"Field '{field_name}' must be a positive integer",
            400,
            {"field": field_name},
        )


def validate_non_negative_integer(data: dict[str, Any], field_name: str) -> None:
    """Validate that a field is a non-negative integer."""
    value = data.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ServiceError(
            "invalid_amount",
            f"Field '{field_name}' must be a non-negative integer",
            400,
            {"field": field_name},
        )
