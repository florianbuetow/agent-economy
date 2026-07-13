"""Request body parsing and field validation (GAP-E9: moved out of the router layer)."""

from __future__ import annotations

import json
from typing import Any

from service_commons.exceptions import ServiceError


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
    """Validate that all required fields exist and are not null."""
    for field_name in fields:
        if field_name not in data or data[field_name] is None:
            raise ServiceError(
                "missing_field",
                f"Missing required field: {field_name}",
                400,
                {"field": field_name},
            )


def validate_string_fields(data: dict[str, Any], fields: list[str]) -> None:
    """Validate that specified fields are strings."""
    for field_name in fields:
        if not isinstance(data[field_name], str):
            raise ServiceError(
                "invalid_field_type",
                f"Field '{field_name}' must be a string",
                400,
                {"field": field_name},
            )
