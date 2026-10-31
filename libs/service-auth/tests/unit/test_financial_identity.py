"""Tests for deterministic financial operation identities."""

import json
import uuid
from typing import cast

import pytest

from service_auth.financial_identity import operation_id_for


@pytest.mark.unit
def test_operation_id_uses_the_versioned_url_uuid5_protocol() -> None:
    """The helper implements the documented protocol byte for byte."""
    identity_parts = ("t-123", "approved")
    canonical = json.dumps(
        {
            "identity": list(identity_parts),
            "purpose": "task-settlement",
            "version": 1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    expected = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "https://agent-economy.invalid/financial-operations/v1/" + canonical,
    )

    assert operation_id_for("task-settlement", identity_parts) == f"op-{expected}"


@pytest.mark.unit
def test_operation_id_is_stable_and_sensitive_to_ordered_identity() -> None:
    """A retry reuses its ID while a different ordered intent gets a different ID."""
    first = operation_id_for("task-settlement", ("t-123", "ruling", "r-7"))

    assert first == operation_id_for("task-settlement", ("t-123", "ruling", "r-7"))
    assert first != operation_id_for("task-settlement", ("t-123", "r-7", "ruling"))
    assert first != operation_id_for("task-lock", ("t-123", "ruling", "r-7"))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("purpose", "identity_parts"),
    [
        ("", ("t-123",)),
        ("task-settlement", ()),
        ("task-settlement", ("",)),
        ("\ud800", ("t-123",)),
        ("task-settlement", ("\ud800",)),
    ],
)
def test_operation_id_rejects_empty_or_non_utf8_protocol_parts(
    purpose: str,
    identity_parts: tuple[str, ...],
) -> None:
    """Every protocol component must be explicit, nonempty valid UTF-8."""
    with pytest.raises(ValueError):
        operation_id_for(purpose, identity_parts)


@pytest.mark.unit
def test_operation_id_rejects_non_string_identity_parts() -> None:
    """The protocol never coerces identity components into strings."""
    invalid_parts = cast("tuple[str, ...]", ("t-123", 7))
    with pytest.raises(TypeError):
        operation_id_for("task-settlement", invalid_parts)
