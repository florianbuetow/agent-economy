"""Shared request validation helpers for task-board routers."""

from __future__ import annotations

import json
from typing import Any

from service_commons.exceptions import ServiceError


def parse_json_body(raw_body: bytes) -> dict[str, Any]:
    """Parse JSON body, raising ServiceError on failure."""
    try:
        data = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "INVALID_JSON",
            "Request body is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(data, dict):
        raise ServiceError(
            "INVALID_JSON",
            "Request body must be a JSON object",
            400,
            {},
        )

    return data


def extract_token(data: dict[str, Any], field_name: str) -> str:
    """Extract and validate a token field from parsed JSON body."""
    if field_name not in data:
        raise ServiceError(
            "INVALID_JWS",
            f"Missing required field: {field_name}",
            400,
            {},
        )

    value = data[field_name]

    if value is None:
        raise ServiceError(
            "INVALID_JWS",
            f"Field '{field_name}' must not be null",
            400,
            {},
        )

    if not isinstance(value, str):
        raise ServiceError(
            "INVALID_JWS",
            f"Field '{field_name}' must be a string",
            400,
            {},
        )

    if not value:
        raise ServiceError(
            "INVALID_JWS",
            f"Field '{field_name}' must not be empty",
            400,
            {},
        )

    return value


def extract_bearer_token(authorization: str | None, *, required: bool) -> str | None:
    """Extract JWS token from Authorization header."""
    if authorization is None:
        if required:
            raise ServiceError(
                "INVALID_JWS",
                "Missing Authorization header",
                400,
                {},
            )
        return None

    if not authorization.startswith("Bearer "):
        raise ServiceError(
            "INVALID_JWS",
            "Authorization header must use Bearer scheme",
            400,
            {},
        )

    token = authorization[len("Bearer ") :]
    if not token:
        raise ServiceError(
            "INVALID_JWS",
            "Bearer token must not be empty",
            400,
            {},
        )

    return token
