"""Canonical financial identity vectors, following the public T-116 replay reds."""

from __future__ import annotations

import hashlib
import json

import pytest

from db_gateway_service.financial_requests import (
    canonical_request,
    parse_canonical_request,
    validate_operation_id,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        "",
        "op-a",
        "op-00000000000040008000000000000001",
        "OP-00000000-0000-4000-8000-000000000001",
        "op-AAAAAAAA-0000-4000-8000-000000000001",
        "op-00000000-0000-4000-8000-00000000000g",
    ],
)
def test_operation_identity_requires_exact_canonical_uuid(value: object) -> None:
    with pytest.raises(ValueError, match="canonical op-UUID"):
        validate_operation_id(value)


def test_operation_identity_is_preserved_exactly() -> None:
    operation_id = "op-00000000-0000-4000-8000-000000000001"
    assert validate_operation_id(operation_id) == operation_id


def test_release_hash_matches_independent_utf8_protocol_vector() -> None:
    request = canonical_request(
        "escrow_release",
        {"escrow_id": "esc-1", "recipient_account_id": "a-worker", "source_reference": "résumé"},
    )
    wire = (
        '{"escrow_id":"esc-1","operation_kind":"escrow_release",'
        '"recipient_account_id":"a-worker","request_version":1,"source_reference":"résumé"}'
    )
    assert request.canonical_json == wire
    assert request.request_sha256 == hashlib.sha256(wire.encode("utf-8")).hexdigest()
    assert request.source_reference == "résumé"
    assert parse_canonical_request(wire) == request


@pytest.mark.parametrize(
    ("kind", "fields", "source"),
    [
        (
            "account_issuance",
            {"account_id": "a-1", "amount": 1, "reference": "initial_balance"},
            "initial_balance",
        ),
        ("credit", {"account_id": "a-1", "amount": (1 << 63) - 1, "reference": "fund-1"}, "fund-1"),
        ("escrow_lock", {"payer_account_id": "a-1", "task_id": "t-1", "amount": 1}, "t-1"),
        (
            "escrow_split",
            {
                "escrow_id": "esc-1",
                "worker_account_id": "a-w",
                "poster_account_id": "a-p",
                "worker_pct": 0,
                "source_reference": "ruling-1",
            },
            "ruling-1",
        ),
        (
            "escrow_split",
            {
                "escrow_id": "esc-1",
                "worker_account_id": "a-w",
                "poster_account_id": "a-p",
                "worker_pct": 100,
                "source_reference": "ruling-1",
            },
            "ruling-1",
        ),
    ],
)
def test_every_kind_round_trips_exact_business_identity(
    kind: str, fields: dict[str, object], source: str
) -> None:
    request = canonical_request(kind, fields)
    assert request.source_reference == source
    assert parse_canonical_request(request.canonical_json) == request
    assert json.loads(request.canonical_json) == {
        "request_version": 1,
        "operation_kind": kind,
        **fields,
    }


@pytest.mark.parametrize("amount", [True, False, 1.0, "1", None, 0, -1, 1 << 63])
def test_credit_hash_never_coerces_invalid_money(amount: object) -> None:
    with pytest.raises(ValueError, match="amount must be an integer"):
        canonical_request("credit", {"account_id": "a-1", "amount": amount, "reference": "r"})


@pytest.mark.parametrize("percentage", [True, False, 50.0, "50", None, -1, 101])
def test_split_hash_never_coerces_invalid_percentage(percentage: object) -> None:
    with pytest.raises(ValueError, match="worker_pct must be an integer"):
        canonical_request(
            "escrow_split",
            {
                "escrow_id": "esc-1",
                "worker_account_id": "a-w",
                "poster_account_id": "a-p",
                "worker_pct": percentage,
                "source_reference": "ruling-1",
            },
        )


@pytest.mark.parametrize("reference", [None, 1, True, "", "bad\ud800text"])
def test_hash_requires_explicit_valid_utf8_reference(reference: object) -> None:
    with pytest.raises(ValueError, match="reference must"):
        canonical_request("credit", {"account_id": "a-1", "amount": 1, "reference": reference})


def test_hash_rejects_omitted_and_unknown_business_fields() -> None:
    fields: dict[str, object] = {"account_id": "a-1", "amount": 1, "reference": "r"}
    with pytest.raises(ValueError, match="fields do not match"):
        canonical_request("credit", {"amount": 1, "reference": "r"})
    for extra in ("operation_id", "timestamp", "constraints", "tx_id"):
        with pytest.raises(ValueError, match="fields do not match"):
            canonical_request("credit", {**fields, extra: "unexpected"})


def test_hash_distinguishes_every_credit_business_field_and_kind() -> None:
    original: dict[str, object] = {"account_id": "a-1", "amount": 1, "reference": "initial_balance"}
    hashes = {canonical_request("credit", original).request_sha256}
    for field, value in (("account_id", "a-2"), ("amount", 2), ("reference", "new")):
        hashes.add(canonical_request("credit", {**original, field: value}).request_sha256)
    hashes.add(canonical_request("account_issuance", original).request_sha256)
    assert len(hashes) == 5


def test_issuance_requires_protocol_reference() -> None:
    with pytest.raises(ValueError, match="must be initial_balance"):
        canonical_request("account_issuance", {"account_id": "a", "amount": 1, "reference": "x"})


@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "[]",
        "{}",
        '{"request_version":true,"operation_kind":"credit"}',
        '{"request_version":2,"operation_kind":"credit"}',
        '{"request_version":1,"operation_kind":"unknown"}',
        '{"account_id":"a","amount":1,"operation_kind":"credit","reference":"r",'
        '"request_version":1,"request_version":1}',
        '{"account_id": "a","amount":1,"operation_kind":"credit","reference":"r",'
        '"request_version":1}',
    ],
)
def test_stored_request_rejects_noncanonical_or_unsupported_json(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_canonical_request(raw)
