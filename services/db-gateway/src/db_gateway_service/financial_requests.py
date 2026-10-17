"""Versioned business identity for authoritative financial commands.

Transport metadata and generated effect IDs never enter these request hashes.
The caller's operation ID selects a receipt; this independent hash checks its intent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from db_gateway_service.schema import SQLITE_MAX_INTEGER

if TYPE_CHECKING:
    from collections.abc import Mapping

_REQUEST_FIELDS: dict[str, tuple[str, ...]] = {
    "account_issuance": ("account_id", "amount", "reference"),
    "credit": ("account_id", "amount", "reference"),
    "escrow_lock": ("payer_account_id", "task_id", "amount"),
    "escrow_release": ("escrow_id", "recipient_account_id", "source_reference"),
    "escrow_split": (
        "escrow_id",
        "worker_account_id",
        "poster_account_id",
        "worker_pct",
        "source_reference",
    ),
}
_INTEGER_FIELDS = {"amount": (1, SQLITE_MAX_INTEGER), "worker_pct": (0, 100)}


@dataclass(frozen=True, slots=True)
class FinancialRequest:
    """Validated, immutable canonical command identity; no mutable field dictionary."""

    operation_kind: str
    canonical_json: str
    request_sha256: str
    source_reference: str


def validate_operation_id(value: object) -> str:
    """Require the exact public op-UUID spelling without coercion or regeneration."""
    if not isinstance(value, str) or len(value) != 39 or not value.startswith("op-"):
        raise ValueError("operation_id must be a canonical op-UUID string")
    try:
        parsed = UUID(value[3:])
    except ValueError as exc:
        raise ValueError("operation_id must be a canonical op-UUID string") from exc
    if value != f"op-{parsed}":
        raise ValueError("operation_id must be a canonical op-UUID string")
    return value


def _business_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must contain valid UTF-8 text") from exc
    return value


def _business_value(value: object, field: str) -> str | int:
    bounds = _INTEGER_FIELDS.get(field)
    if bounds is None:
        return _business_string(value, field)
    if type(value) is not int or not bounds[0] <= value <= bounds[1]:
        raise ValueError(f"{field} must be an integer in {bounds[0]}..{bounds[1]}")
    return value


def canonical_request(operation_kind: str, fields: Mapping[str, object]) -> FinancialRequest:
    """Validate exact business fields and hash the fixed version-1 canonical object."""
    if not isinstance(operation_kind, str) or operation_kind not in _REQUEST_FIELDS:
        raise ValueError("Unknown financial operation kind")
    required = _REQUEST_FIELDS[operation_kind]
    if set(fields) != set(required):
        raise ValueError("Financial business fields do not match the operation kind")
    payload: dict[str, str | int] = {
        "request_version": 1,
        "operation_kind": operation_kind,
    }
    for field in required:
        payload[field] = _business_value(fields[field], field)
    if operation_kind == "account_issuance" and payload["reference"] != "initial_balance":
        raise ValueError("Account issuance reference must be initial_balance")
    if operation_kind == "escrow_lock":
        source_reference = _business_string(payload["task_id"], "task_id")
    elif operation_kind in {"account_issuance", "credit"}:
        source_reference = _business_string(payload["reference"], "reference")
    else:
        source_reference = _business_string(payload["source_reference"], "source_reference")
    canonical_json = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return FinancialRequest(
        operation_kind=operation_kind,
        canonical_json=canonical_json,
        request_sha256=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
        source_reference=source_reference,
    )


def parse_canonical_request(raw: str) -> FinancialRequest:
    """Validate a stored canonical request, rejecting coercion and noncanonical JSON."""
    decoded: object = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Stored financial request must be an object")
    fields: dict[str, object] = {}
    for key, value in decoded.items():
        if not isinstance(key, str):
            raise ValueError("Stored financial field names must be strings")
        fields[key] = value
    version = fields.pop("request_version", None)
    if type(version) is not int or version != 1:
        raise ValueError("Unsupported financial request version")
    operation_kind = fields.pop("operation_kind", None)
    if not isinstance(operation_kind, str):
        raise ValueError("Stored financial request requires operation_kind")
    request = canonical_request(operation_kind, fields)
    if request.canonical_json != raw:
        raise ValueError("Stored financial request JSON is not canonical")
    return request
