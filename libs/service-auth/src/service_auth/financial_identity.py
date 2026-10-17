"""Deterministic identities for caller-owned financial operation intents."""

from __future__ import annotations

import json
import uuid

_PROTOCOL_PREFIX = "https://agent-economy.invalid/financial-operations/v1/"


def _require_protocol_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if value == "":
        raise ValueError(f"{field} must be nonempty")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8") from exc
    return value


def operation_id_for(purpose: str, identity_parts: tuple[str, ...]) -> str:
    """Derive a canonical operation ID from an explicit financial intent."""
    validated_purpose = _require_protocol_text(purpose, "purpose")
    if not isinstance(identity_parts, tuple):
        raise TypeError("identity_parts must be a tuple")
    if not identity_parts:
        raise ValueError("identity_parts must be nonempty")
    validated_parts = [
        _require_protocol_text(part, f"identity_parts[{index}]")
        for index, part in enumerate(identity_parts)
    ]
    canonical = json.dumps(
        {
            "identity": validated_parts,
            "purpose": validated_purpose,
            "version": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    operation_uuid = uuid.uuid5(uuid.NAMESPACE_URL, _PROTOCOL_PREFIX + canonical)
    return f"op-{operation_uuid}"
