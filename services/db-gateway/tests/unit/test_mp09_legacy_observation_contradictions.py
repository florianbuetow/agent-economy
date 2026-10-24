"""Regression contract for contradictory incomplete legacy observations."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.services.legacy_artifact_continuation import (
    build_live_legacy_acceptance_observation,
    parse_legacy_acceptance_observation,
    validate_stored_legacy_observation_time,
)
from db_gateway_service.task_time import epoch_us_to_utc_text

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

pytestmark = pytest.mark.unit

_BIDDING_CUTOFF_US: Final[int] = 4_070_995_200_000_000
_ACCEPTED_US: Final[int] = _BIDDING_CUTOFF_US + 10_000_000
_EXECUTION_CUTOFF_US: Final[int] = _ACCEPTED_US + 120_000_000

_CONTRADICTIONS: Final[tuple[tuple[str, Mapping[str, object]], ...]] = (
    ("reward-versus-escrow", {"reward": 100, "escrow_amount": 101}),
    (
        "winning-versus-accepted-bid",
        {"winning_bid_amount": 90, "accepted_bid_amount": 91},
    ),
    (
        "bid-after-bidding-deadline",
        {"winning_bid_submitted_at": epoch_us_to_utc_text(_BIDDING_CUTOFF_US + 1)},
    ),
    (
        "accepted-versus-execution-cutoff",
        {
            "execution_cutoff_at": epoch_us_to_utc_text(_EXECUTION_CUTOFF_US + 1),
            "execution_cutoff_us": _EXECUTION_CUTOFF_US + 1,
        },
    ),
)


def _observation(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "format_version": 1,
        "artifact_contract_version": 0,
        "binding_kind": "legacy_accepted_continuation",
        "observation_status": "incomplete_corrupt",
        "task_id": "task-legacy-contradiction",
        "poster_id": "poster-legacy",
        "title": "Preserved legacy evidence",
        "spec": "Keep every known conflicting fact.",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "deadline_seconds": 120,
        "review_deadline_seconds": 90,
        "bidding_deadline": epoch_us_to_utc_text(_BIDDING_CUTOFF_US),
        "escrow_id": "escrow-legacy-contradiction",
        "escrow_payer_id": "poster-legacy",
        "escrow_amount": 100,
        "escrow_status": "locked",
        "accepted_bid_id": "bid-legacy-contradiction",
        "worker_id": "worker-legacy",
        "winning_bid_amount": 90,
        "winning_bid_proposal": "Retain exact historical evidence.",
        "winning_bid_submitted_at": epoch_us_to_utc_text(_BIDDING_CUTOFF_US - 1),
        "accepted_at": epoch_us_to_utc_text(_ACCEPTED_US),
        "execution_cutoff_at": epoch_us_to_utc_text(_EXECUTION_CUTOFF_US),
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


@pytest.mark.parametrize(
    "overrides",
    [overrides for _, overrides in _CONTRADICTIONS],
    ids=[case for case, _ in _CONTRADICTIONS],
)
def test_incomplete_observation_preserves_known_contradictory_facts(
    overrides: Mapping[str, object],
) -> None:
    source = _observation(**overrides)
    canonical = _canonical(source)

    parsed = parse_legacy_acceptance_observation(source)

    for field, expected in overrides.items():
        assert parsed.values[field] == expected
    assert parsed.observation_status == "incomplete_corrupt"
    assert parsed.canonical_json == canonical
    assert parsed.observation_sha256 == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert parsed.accepted_at_us == _ACCEPTED_US


@pytest.mark.parametrize(
    "overrides",
    [overrides for _, overrides in _CONTRADICTIONS],
    ids=[case for case, _ in _CONTRADICTIONS],
)
def test_verified_observation_rejects_the_same_contradictory_facts(
    overrides: Mapping[str, object],
) -> None:
    source = _observation(observation_status="verified", **overrides)
    _invalid(lambda: parse_legacy_acceptance_observation(source))


@pytest.mark.parametrize(
    "overrides",
    [overrides for _, overrides in _CONTRADICTIONS],
    ids=[case for case, _ in _CONTRADICTIONS],
)
def test_live_builder_rejects_every_incomplete_contradictory_shape(
    overrides: Mapping[str, object],
) -> None:
    source = _observation(**overrides)
    _invalid(
        lambda: build_live_legacy_acceptance_observation(
            source,
            observed_at_us=_ACCEPTED_US,
        )
    )


@pytest.mark.parametrize(
    "overrides",
    [overrides for _, overrides in _CONTRADICTIONS],
    ids=[case for case, _ in _CONTRADICTIONS],
)
def test_incomplete_contradiction_keeps_actual_nonbackdated_observation_time(
    overrides: Mapping[str, object],
) -> None:
    parsed = parse_legacy_acceptance_observation(_observation(**overrides))

    assert (
        validate_stored_legacy_observation_time(
            parsed,
            observed_at_us=_ACCEPTED_US,
        )
        == _ACCEPTED_US
    )
    assert (
        validate_stored_legacy_observation_time(
            parsed,
            observed_at_us=_ACCEPTED_US + 1,
        )
        == _ACCEPTED_US + 1
    )
    _invalid(
        lambda: validate_stored_legacy_observation_time(
            parsed,
            observed_at_us=_ACCEPTED_US - 1,
        )
    )
