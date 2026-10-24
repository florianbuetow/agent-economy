"""Pure L1 format contract for canonical legacy acceptance observations."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import operator
from dataclasses import FrozenInstanceError
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.task_time import epoch_us_to_utc_text

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

pytestmark = pytest.mark.unit

_MODULE = "db_gateway_service.services.legacy_artifact_continuation"
_ACCEPTED_US: Final[int] = 4_070_995_200_000_000
_BID_US: Final[int] = _ACCEPTED_US - 1
_BIDDING_CUTOFF_US: Final[int] = _ACCEPTED_US + 30_000_000
_EXECUTION_CUTOFF_US: Final[int] = _ACCEPTED_US + 120_000_000

_ALL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "format_version",
        "artifact_contract_version",
        "binding_kind",
        "observation_status",
        "task_id",
        "poster_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "deadline_seconds",
        "review_deadline_seconds",
        "bidding_deadline",
        "escrow_id",
        "escrow_payer_id",
        "escrow_amount",
        "escrow_status",
        "accepted_bid_id",
        "worker_id",
        "winning_bid_amount",
        "winning_bid_proposal",
        "winning_bid_submitted_at",
        "accepted_at",
        "execution_cutoff_at",
        "execution_cutoff_us",
        "timing_contract_version",
        "accepted_reward",
        "accepted_bid_amount",
        "lifecycle_status",
        "lifecycle_version",
        "input_provenance",
        "source_publication_request_sha256",
    }
)

_POSITIVE_INTEGER_FIELDS: Final[tuple[str, ...]] = (
    "reward",
    "bidding_deadline_seconds",
    "deadline_seconds",
    "review_deadline_seconds",
    "escrow_amount",
    "accepted_reward",
)
_NONNEGATIVE_INTEGER_FIELDS: Final[tuple[str, ...]] = (
    "winning_bid_amount",
    "execution_cutoff_us",
    "accepted_bid_amount",
    "lifecycle_version",
)
_ZERO_CAPABLE_INTEGER_FIELDS: Final[tuple[str, ...]] = (
    "winning_bid_amount",
    "accepted_bid_amount",
    "lifecycle_version",
)
_EXACT_INTEGER_FIELDS: Final[tuple[str, ...]] = (
    "format_version",
    "artifact_contract_version",
    "timing_contract_version",
)
_INTEGER_FIELDS: Final[tuple[str, ...]] = (
    *_POSITIVE_INTEGER_FIELDS,
    *_NONNEGATIVE_INTEGER_FIELDS,
    *_EXACT_INTEGER_FIELDS,
)
_TEXT_FIELDS: Final[tuple[str, ...]] = tuple(sorted(_ALL_FIELDS - set(_INTEGER_FIELDS)))
_UTC_FIELDS: Final[tuple[str, ...]] = (
    "bidding_deadline",
    "winning_bid_submitted_at",
    "accepted_at",
    "execution_cutoff_at",
)
_INCOMPLETE_NULLABLE_FIELDS: Final[tuple[str, ...]] = (
    "escrow_payer_id",
    "escrow_amount",
    "escrow_status",
    "accepted_bid_id",
    "worker_id",
    "winning_bid_amount",
    "winning_bid_proposal",
    "winning_bid_submitted_at",
    "accepted_at",
    "execution_cutoff_at",
    "execution_cutoff_us",
    "timing_contract_version",
    "accepted_reward",
    "accepted_bid_amount",
    "lifecycle_version",
)
_INCOMPLETE_REQUIRED_FIELDS: Final[tuple[str, ...]] = tuple(
    sorted(_ALL_FIELDS - set(_INCOMPLETE_NULLABLE_FIELDS) - {"source_publication_request_sha256"})
)
_INCOMPLETE_CONCRETE_VALUES: Final[dict[str, object]] = {
    "escrow_payer_id": "poster-legacy",
    "escrow_amount": 100,
    "escrow_status": "locked",
    "accepted_bid_id": "bid-legacy-format",
    "worker_id": "worker-legacy",
    "winning_bid_amount": 90,
    "winning_bid_proposal": "Recovered proposal",
    "winning_bid_submitted_at": epoch_us_to_utc_text(_BID_US),
    "accepted_at": epoch_us_to_utc_text(_ACCEPTED_US),
    "execution_cutoff_at": epoch_us_to_utc_text(_EXECUTION_CUTOFF_US),
    "execution_cutoff_us": _EXECUTION_CUTOFF_US,
    "timing_contract_version": 2,
    "accepted_reward": 100,
    "accepted_bid_amount": 90,
    "lifecycle_version": 1,
}
_OBSERVATION_STATUSES: Final[tuple[str, ...]] = ("verified", "incomplete_corrupt")


def _api() -> Any:
    spec = importlib.util.find_spec(_MODULE)
    assert spec is not None, f"Missing L1 capability: {_MODULE}"
    module = importlib.import_module(_MODULE)
    for symbol in (
        "CanonicalLegacyAcceptanceObservation",
        "LegacyAcceptanceRecord",
        "parse_legacy_acceptance_observation",
        "build_live_legacy_acceptance_observation",
        "validate_stored_legacy_observation_time",
    ):
        assert hasattr(module, symbol), f"Missing L1 capability: {symbol}"
    return module


def _utc(value_us: int) -> str:
    return epoch_us_to_utc_text(value_us)


def _verified(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "format_version": 1,
        "artifact_contract_version": 0,
        "binding_kind": "legacy_accepted_continuation",
        "observation_status": "verified",
        "task_id": "t-legacy-format",
        "poster_id": "poster-legacy",
        "title": "Résumé 世界",
        "spec": "Return Δ evidence byte-for-byte.",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "deadline_seconds": 120,
        "review_deadline_seconds": 90,
        "bidding_deadline": _utc(_BIDDING_CUTOFF_US),
        "escrow_id": "escrow-legacy-format",
        "escrow_payer_id": "poster-legacy",
        "escrow_amount": 100,
        "escrow_status": "locked",
        "accepted_bid_id": "bid-legacy-format",
        "worker_id": "worker-legacy",
        "winning_bid_amount": 90,
        "winning_bid_proposal": "Deliver café output.",
        "winning_bid_submitted_at": _utc(_BID_US),
        "accepted_at": _utc(_ACCEPTED_US),
        "execution_cutoff_at": _utc(_EXECUTION_CUTOFF_US),
        "execution_cutoff_us": _EXECUTION_CUTOFF_US,
        "timing_contract_version": 2,
        "accepted_reward": 100,
        "accepted_bid_amount": 90,
        "lifecycle_status": "accepted",
        "lifecycle_version": 1,
        "input_provenance": "unknown_pre_v6",
        "source_publication_request_sha256": None,
    }
    value.update(overrides)
    assert frozenset(value) == _ALL_FIELDS
    return value


def _incomplete(**overrides: object) -> dict[str, object]:
    value = _verified(observation_status="incomplete_corrupt")
    for field in _INCOMPLETE_NULLABLE_FIELDS:
        value[field] = None
    value.update(overrides)
    return value


def _shape(status: str, **overrides: object) -> dict[str, object]:
    value = _verified() if status == "verified" else _incomplete()
    value.update(overrides)
    return value


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _invalid(call: Callable[[], object]) -> None:
    with pytest.raises(ServiceError) as raised:
        call()
    assert (raised.value.status_code, raised.value.error) == (
        500,
        "legacy_observation_invalid",
    )


def _set_attribute(value: object, name: str, replacement: object) -> None:
    setattr(value, name, replacement)


@pytest.mark.parametrize("status", _OBSERVATION_STATUSES)
def test_parser_accepts_both_exact_status_shapes(status: str) -> None:
    api = _api()
    value = _shape(status)
    parsed = api.parse_legacy_acceptance_observation(value)
    assert parsed.observation_status == status
    assert frozenset(parsed.values) == _ALL_FIELDS
    assert type(parsed.values) is MappingProxyType
    assert parsed.accepted_at_us == (_ACCEPTED_US if status == "verified" else None)


@pytest.mark.parametrize("status", _OBSERVATION_STATUSES)
@pytest.mark.parametrize("defect", ["missing", "extra"])
def test_parser_requires_the_exact_32_field_set(status: str, defect: str) -> None:
    api = _api()
    value = _shape(status)
    if defect == "missing":
        del value["title"]
    else:
        value["created_at"] = "not an approved canonical field"
    _invalid(lambda: api.parse_legacy_acceptance_observation(value))


@pytest.mark.parametrize("field", _TEXT_FIELDS)
@pytest.mark.parametrize("status", _OBSERVATION_STATUSES)
def test_every_text_or_null_field_rejects_the_wrong_concrete_type(
    field: str,
    status: str,
) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_acceptance_observation(_shape(status, **{field: 1})))


@pytest.mark.parametrize("field", _INTEGER_FIELDS)
@pytest.mark.parametrize("status", _OBSERVATION_STATUSES)
def test_every_integer_field_rejects_boolean(field: str, status: str) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_acceptance_observation(_shape(status, **{field: True})))


@pytest.mark.parametrize("field", _INTEGER_FIELDS)
@pytest.mark.parametrize("status", _OBSERVATION_STATUSES)
def test_every_integer_field_rejects_numeric_string_coercion(
    field: str,
    status: str,
) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_acceptance_observation(_shape(status, **{field: "1"})))


@pytest.mark.parametrize("field", _POSITIVE_INTEGER_FIELDS)
@pytest.mark.parametrize("status", _OBSERVATION_STATUSES)
def test_positive_integer_fields_reject_zero(field: str, status: str) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_acceptance_observation(_shape(status, **{field: 0})))


@pytest.mark.parametrize("field", _ZERO_CAPABLE_INTEGER_FIELDS)
def test_nonnegative_integer_fields_accept_zero_when_duplicates_remain_coherent(field: str) -> None:
    api = _api()
    overrides: dict[str, object] = {field: 0}
    if field == "winning_bid_amount":
        overrides["accepted_bid_amount"] = 0
    elif field == "accepted_bid_amount":
        overrides["winning_bid_amount"] = 0
    parsed = api.parse_legacy_acceptance_observation(_verified(**overrides))
    assert parsed.values[field] == overrides[field]


@pytest.mark.parametrize("field", _INCOMPLETE_REQUIRED_FIELDS)
def test_incomplete_shape_rejects_null_for_every_required_field(field: str) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_acceptance_observation(_incomplete(**{field: None})))


@pytest.mark.parametrize("field", _INCOMPLETE_NULLABLE_FIELDS)
def test_verified_shape_rejects_null_for_every_evidence_field(field: str) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_acceptance_observation(_verified(**{field: None})))


@pytest.mark.parametrize("field", _INCOMPLETE_NULLABLE_FIELDS)
def test_each_incomplete_nullable_field_accepts_its_own_concrete_type_and_rejects_wrong_type(
    field: str,
) -> None:
    api = _api()
    concrete = _INCOMPLETE_CONCRETE_VALUES[field]
    parsed = api.parse_legacy_acceptance_observation(_incomplete(**{field: concrete}))
    assert parsed.values[field] == concrete
    wrong: object = "wrong-integer" if field in _INTEGER_FIELDS else 7
    _invalid(lambda: api.parse_legacy_acceptance_observation(_incomplete(**{field: wrong})))


@pytest.mark.parametrize("field", _UTC_FIELDS)
@pytest.mark.parametrize("status", _OBSERVATION_STATUSES)
def test_present_utc_fields_require_explicit_valid_utc(field: str, status: str) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_acceptance_observation(_shape(status, **{field: "not-utc"})))


@pytest.mark.parametrize("field", _UTC_FIELDS)
@pytest.mark.parametrize("status", _OBSERVATION_STATUSES)
def test_equivalent_offset_utc_text_is_preserved_in_values_and_canonical_bytes(
    field: str,
    status: str,
) -> None:
    api = _api()
    original = _verified()[field]
    assert isinstance(original, str) and original.endswith("Z")
    offset = original[:-1] + "+00:00"
    source = _shape(status, **{field: offset})
    parsed = api.parse_legacy_acceptance_observation(source)
    assert parsed.values[field] == offset
    assert parsed.canonical_json == _canonical(source)


def test_parser_emits_exact_utf8_canonical_hash_and_an_immutable_copy() -> None:
    api = _api()
    source = _verified()
    expected_json = _canonical(source)
    parsed = api.parse_legacy_acceptance_observation(source)
    assert parsed.canonical_json == expected_json
    assert parsed.observation_sha256 == hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
    assert "Résumé 世界" in parsed.canonical_json
    assert "\\u00e9" not in parsed.canonical_json
    source["title"] = "mutated later"
    assert parsed.values["title"] == "Résumé 世界"
    with pytest.raises(TypeError):
        operator.setitem(parsed.values, "title", "mutation denied")
    with pytest.raises(FrozenInstanceError):
        _set_attribute(parsed, "observation_status", "incomplete_corrupt")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format_version", 2),
        ("artifact_contract_version", 1),
        ("timing_contract_version", 3),
        ("binding_kind", "publication_contract"),
        ("observation_status", "unknown"),
        ("input_provenance", "native_v6"),
        ("source_publication_request_sha256", "A" * 64),
        ("source_publication_request_sha256", "a" * 63),
        ("escrow_status", "unknown"),
    ],
)
@pytest.mark.parametrize("status", _OBSERVATION_STATUSES)
def test_invariant_discriminators_versions_and_hash_shape_are_exact(
    field: str,
    value: object,
    status: str,
) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_acceptance_observation(_shape(status, **{field: value})))


def test_timing_contract_version_one_is_a_valid_parser_control() -> None:
    api = _api()
    parsed = api.parse_legacy_acceptance_observation(_verified(timing_contract_version=1))
    assert parsed.values["timing_contract_version"] == 1


def test_incomplete_timing_contract_version_one_is_a_valid_concrete_control() -> None:
    api = _api()
    parsed = api.parse_legacy_acceptance_observation(_incomplete(timing_contract_version=1))
    assert parsed.values["timing_contract_version"] == 1


@pytest.mark.parametrize("status", ["locked", "released", "split"])
def test_parser_accepts_every_known_persisted_escrow_status(status: str) -> None:
    api = _api()
    parsed = api.parse_legacy_acceptance_observation(_verified(escrow_status=status))
    assert parsed.values["escrow_status"] == status


@pytest.mark.parametrize("status", ["released", "split"])
def test_incomplete_parser_accepts_known_concrete_escrow_status(status: str) -> None:
    api = _api()
    parsed = api.parse_legacy_acceptance_observation(_incomplete(escrow_status=status))
    assert parsed.values["escrow_status"] == status


@pytest.mark.parametrize("source_hash", [None, "a" * 64])
def test_incomplete_source_publication_hash_accepts_null_or_lowercase_hex(
    source_hash: str | None,
) -> None:
    api = _api()
    parsed = api.parse_legacy_acceptance_observation(
        _incomplete(source_publication_request_sha256=source_hash)
    )
    assert parsed.values["source_publication_request_sha256"] == source_hash


@pytest.mark.parametrize("source_hash", ["A" * 64, "a" * 63, 7])
def test_incomplete_source_publication_hash_rejects_invalid_shape(
    source_hash: object,
) -> None:
    api = _api()
    _invalid(
        lambda: api.parse_legacy_acceptance_observation(
            _incomplete(source_publication_request_sha256=source_hash)
        )
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("escrow_payer_id", "different-poster"),
        ("escrow_amount", 101),
        ("accepted_reward", 101),
        ("accepted_bid_amount", 91),
    ],
)
def test_parser_rejects_duplicated_fact_disagreement(field: str, value: object) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_acceptance_observation(_verified(**{field: value})))


@pytest.mark.parametrize(
    "overrides",
    [
        {"winning_bid_submitted_at": _utc(_ACCEPTED_US + 1)},
        {"winning_bid_submitted_at": _utc(_BIDDING_CUTOFF_US)},
        {"execution_cutoff_us": _EXECUTION_CUTOFF_US - 1},
        {"execution_cutoff_us": _EXECUTION_CUTOFF_US + 1},
        {"execution_cutoff_us": 0},
        {"execution_cutoff_at": _utc(_EXECUTION_CUTOFF_US + 1)},
    ],
)
def test_parser_rejects_inside_object_chronology_and_cutoff_contradictions(
    overrides: dict[str, object],
) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_acceptance_observation(_verified(**overrides)))


def test_equal_bid_and_acceptance_microseconds_are_valid() -> None:
    api = _api()
    parsed = api.parse_legacy_acceptance_observation(
        _verified(winning_bid_submitted_at=_utc(_ACCEPTED_US))
    )
    assert parsed.accepted_at_us == _ACCEPTED_US


def test_bid_one_microsecond_before_bidding_cutoff_is_valid() -> None:
    api = _api()
    submitted = _BIDDING_CUTOFF_US - 1
    accepted = submitted
    deadline_seconds = 120
    execution_cutoff = accepted + deadline_seconds * 1_000_000
    parsed = api.parse_legacy_acceptance_observation(
        _verified(
            deadline_seconds=deadline_seconds,
            winning_bid_submitted_at=_utc(submitted),
            accepted_at=_utc(accepted),
            execution_cutoff_at=_utc(execution_cutoff),
            execution_cutoff_us=execution_cutoff,
        )
    )
    assert parsed.values["winning_bid_submitted_at"] == _utc(submitted)
    assert parsed.accepted_at_us == accepted


def test_live_builder_returns_only_verified_record_at_exact_observed_time() -> None:
    api = _api()
    record = api.build_live_legacy_acceptance_observation(
        _verified(),
        observed_at_us=_ACCEPTED_US,
    )
    assert record.observed_at_us == _ACCEPTED_US
    assert record.observation.observation_status == "verified"
    assert record.observation.accepted_at_us == _ACCEPTED_US
    with pytest.raises(FrozenInstanceError):
        _set_attribute(record, "observed_at_us", _ACCEPTED_US + 1)


@pytest.mark.parametrize(
    "observed_at_us",
    [True, -1, _ACCEPTED_US - 1, _ACCEPTED_US + 1],
)
def test_live_builder_rejects_invalid_or_non_equal_observation_time(
    observed_at_us: object,
) -> None:
    api = _api()
    _invalid(
        lambda: api.build_live_legacy_acceptance_observation(
            _verified(),
            observed_at_us=observed_at_us,
        )
    )


def test_live_builder_rejects_incomplete_observation() -> None:
    api = _api()
    _invalid(
        lambda: api.build_live_legacy_acceptance_observation(
            _incomplete(),
            observed_at_us=_ACCEPTED_US,
        )
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("escrow_status", "released"),
        ("escrow_status", "split"),
        ("lifecycle_status", "open"),
    ],
)
def test_live_builder_requires_locked_accepted_facts(field: str, value: str) -> None:
    api = _api()
    _invalid(
        lambda: api.build_live_legacy_acceptance_observation(
            _verified(**{field: value}),
            observed_at_us=_ACCEPTED_US,
        )
    )


@pytest.mark.parametrize("observed_at_us", [_ACCEPTED_US, _ACCEPTED_US + 1])
def test_stored_time_accepts_exact_or_later_observation(observed_at_us: int) -> None:
    api = _api()
    observation = api.parse_legacy_acceptance_observation(_verified())
    assert (
        api.validate_stored_legacy_observation_time(
            observation,
            observed_at_us=observed_at_us,
        )
        == observed_at_us
    )


@pytest.mark.parametrize("observed_at_us", [True, -1, _ACCEPTED_US - 1])
def test_stored_time_rejects_boolean_negative_or_backdated_value(observed_at_us: object) -> None:
    api = _api()
    observation = api.parse_legacy_acceptance_observation(_verified())
    _invalid(
        lambda: api.validate_stored_legacy_observation_time(
            observation,
            observed_at_us=observed_at_us,
        )
    )


def test_stored_time_allows_incomplete_row_with_unknown_acceptance_time() -> None:
    api = _api()
    observation = api.parse_legacy_acceptance_observation(_incomplete())
    assert observation.accepted_at_us is None
    assert (
        api.validate_stored_legacy_observation_time(
            observation,
            observed_at_us=0,
        )
        == 0
    )
