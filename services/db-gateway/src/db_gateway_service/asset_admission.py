"""Strict final-admission request for legacy Board asset metadata writes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SQLITE_MAX_INTEGER

_FIELDS = frozenset(
    {
        "expected_status",
        "expected_lifecycle_version",
        "max_files_per_task",
    }
)


@dataclass(frozen=True, slots=True)
class AssetAdmission:
    """Caller-observed state and configured quota required for final admission."""

    expected_lifecycle_version: int
    max_files_per_task: int


def _invalid() -> ServiceError:
    return ServiceError(
        "invalid_payload",
        "Asset admission is invalid",
        400,
        {"field": "admission"},
    )


def parse_asset_admission(value: object) -> AssetAdmission:
    """Require the exact accepted/version/quota object with SQLite-safe integers."""
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _invalid()
    if frozenset(value) != _FIELDS or value.get("expected_status") != "accepted":
        raise _invalid()

    version = value.get("expected_lifecycle_version")
    quota = value.get("max_files_per_task")
    if type(version) is not int or not 0 <= version <= SQLITE_MAX_INTEGER:
        raise _invalid()
    if type(quota) is not int or not 1 <= quota <= SQLITE_MAX_INTEGER:
        raise _invalid()
    return AssetAdmission(
        expected_lifecycle_version=version,
        max_files_per_task=quota,
    )
