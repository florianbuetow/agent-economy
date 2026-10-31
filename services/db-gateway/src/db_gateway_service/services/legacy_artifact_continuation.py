"""Canonical facts recorded when a legacy task is accepted."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from service_commons.exceptions import ServiceError

from db_gateway_service.task_time import add_seconds_to_epoch_us, parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    import sqlite3

type LegacyObservationScalar = str | int | None
type ObservationStatus = Literal["verified", "incomplete_corrupt"]

_INTEGER_MINIMUM: Final[dict[str, int]] = {
    "format_version": 0,
    "artifact_contract_version": 0,
    "timing_contract_version": 0,
    "reward": 1,
    "bidding_deadline_seconds": 1,
    "deadline_seconds": 1,
    "review_deadline_seconds": 1,
    "escrow_amount": 1,
    "accepted_reward": 1,
    "winning_bid_amount": 0,
    "execution_cutoff_us": 0,
    "accepted_bid_amount": 0,
    "lifecycle_version": 0,
}
_TEXT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "binding_kind",
        "observation_status",
        "task_id",
        "poster_id",
        "title",
        "spec",
        "bidding_deadline",
        "escrow_id",
        "escrow_payer_id",
        "escrow_status",
        "accepted_bid_id",
        "worker_id",
        "winning_bid_proposal",
        "winning_bid_submitted_at",
        "accepted_at",
        "execution_cutoff_at",
        "lifecycle_status",
        "input_provenance",
        "source_publication_request_sha256",
    }
)
_FIELDS: Final[frozenset[str]] = _TEXT_FIELDS | _INTEGER_MINIMUM.keys()
_INCOMPLETE_NULLABLE: Final[frozenset[str]] = frozenset(
    {
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
    }
)
_CHOICES: Final[dict[str, tuple[LegacyObservationScalar, ...]]] = {
    "format_version": (1,),
    "artifact_contract_version": (0,),
    "timing_contract_version": (1, 2),
    "binding_kind": ("legacy_accepted_continuation",),
    "observation_status": ("verified", "incomplete_corrupt"),
    "input_provenance": ("unknown_pre_v6",),
    "escrow_status": ("locked", "released", "split"),
}
_UTC_FIELDS: Final[tuple[str, ...]] = (
    "bidding_deadline",
    "winning_bid_submitted_at",
    "accepted_at",
    "execution_cutoff_at",
)
_SHA256: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class CanonicalLegacyAcceptanceObservation:
    values: MappingProxyType[str, LegacyObservationScalar]
    observation_status: ObservationStatus
    canonical_json: str
    observation_sha256: str
    accepted_at_us: int | None


@dataclass(frozen=True, slots=True)
class LegacyAcceptanceRecord:
    observation: CanonicalLegacyAcceptanceObservation
    observed_at_us: int


def _invalid() -> ServiceError:
    return ServiceError(
        "legacy_observation_invalid",
        "Legacy acceptance evidence is invalid",
        500,
        {},
    )


def _unavailable() -> ServiceError:
    return ServiceError(
        "legacy_continuation_unavailable",
        "Complete legacy continuation evidence is unavailable",
        409,
        {},
    )


def _binding_mismatch() -> ServiceError:
    return ServiceError(
        "accepted_binding_mismatch",
        "Accepted artifact binding differs",
        409,
        {},
    )


def validate_legacy_publication_provenance(
    cursor: sqlite3.Cursor,
    *,
    task_id: str,
    poster_id: str,
    source_publication_request_sha256: object,
) -> None:
    """Require absent history or one exact retained published V1 journal."""
    publications = cursor.execute(
        "SELECT request_protocol_version,state,task_id,actor_id,request_sha256 "
        "FROM board_task_publications WHERE task_id=?",
        (task_id,),
    ).fetchall()
    if not publications:
        if source_publication_request_sha256 is not None:
            raise _invalid()
        return
    if len(publications) != 1:
        raise _invalid()
    publication = publications[0]
    if (
        publication[0] != 1
        or publication[1] != "published"
        or publication[2] != task_id
        or publication[3] != poster_id
        or source_publication_request_sha256 is None
        or publication[4] != source_publication_request_sha256
    ):
        raise _invalid()


def _stored_legacy_contract(
    cursor: sqlite3.Cursor,
    *,
    task_id: str,
    accepted_binding_kind: str,
    accepted_binding_sha256: str,
) -> tuple[Any, ...]:
    contract = cursor.execute(
        "SELECT artifact_contract_version,input_provenance,accepted_binding_kind,"
        "accepted_publication_contract_sha256,legacy_acceptance_observation_sha256,"
        "legacy_acceptance_observation_status,source_publication_request_sha256,"
        "base_request_sha256,input_manifest_id,input_manifest_sha256,"
        "publication_contract_sha256,publication_operation_id,input_manifest_kind,input_actor_id "
        "FROM board_task_artifact_contracts WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if contract is None:
        raise ServiceError(
            "artifact_contract_missing",
            "Task artifact contract is missing",
            409,
            {},
        )
    if (
        type(contract[0]) is not int
        or contract[0] != 0
        or accepted_binding_kind != "legacy_accepted_continuation"
        or contract[2] != accepted_binding_kind
        or contract[4] != accepted_binding_sha256
    ):
        raise _binding_mismatch()
    if contract[1] != "unknown_pre_v6" or any(
        contract[index] is not None for index in (3, 7, 8, 9, 10, 11, 12, 13)
    ):
        raise _invalid()
    if contract[5] is None or contract[5] == "incomplete_corrupt":
        raise _unavailable()
    if contract[5] != "verified":
        raise _invalid()
    return tuple(contract)


def _scalar(name: str, value: object, *, incomplete: bool) -> LegacyObservationScalar:
    if value is None:
        if name == "source_publication_request_sha256" or (
            incomplete and name in _INCOMPLETE_NULLABLE
        ):
            return None
        raise _invalid()
    if name in _INTEGER_MINIMUM:
        if type(value) is not int or value < _INTEGER_MINIMUM[name]:
            raise _invalid()
    elif not isinstance(value, str):
        raise _invalid()
    if name in _CHOICES and value not in _CHOICES[name]:
        raise _invalid()
    if name == "source_publication_request_sha256" and (
        not isinstance(value, str) or _SHA256.fullmatch(value) is None
    ):
        raise _invalid()
    return value


def _equal_when_present(left: LegacyObservationScalar, right: LegacyObservationScalar) -> None:
    if left is not None and right is not None and left != right:
        raise _invalid()


def _validate_relationships(
    values: Mapping[str, LegacyObservationScalar],
    times: Mapping[str, int],
) -> None:
    for left, right in (
        ("poster_id", "escrow_payer_id"),
        ("reward", "escrow_amount"),
        ("reward", "accepted_reward"),
        ("winning_bid_amount", "accepted_bid_amount"),
    ):
        _equal_when_present(values[left], values[right])
    bid = times.get("winning_bid_submitted_at")
    accepted = times.get("accepted_at")
    if bid is not None and (
        bid >= times["bidding_deadline"] or (accepted is not None and bid > accepted)
    ):
        raise _invalid()
    cutoff_text = times.get("execution_cutoff_at")
    cutoff_integer = values["execution_cutoff_us"]
    _equal_when_present(cutoff_text, cutoff_integer)
    if accepted is not None:
        expected = add_seconds_to_epoch_us(accepted, values["deadline_seconds"])
        _equal_when_present(cutoff_text, expected)
        _equal_when_present(cutoff_integer, expected)


def parse_legacy_acceptance_observation(value: object) -> CanonicalLegacyAcceptanceObservation:
    """Validate and copy the exact observation without inventing missing facts."""
    if not isinstance(value, Mapping):
        raise _invalid()
    raw = dict(cast("Mapping[str, object]", value))
    if raw.keys() != _FIELDS:
        raise _invalid()
    incomplete = raw["observation_status"] == "incomplete_corrupt"
    values = {name: _scalar(name, raw[name], incomplete=incomplete) for name in _FIELDS}
    try:
        times = {
            name: parse_utc_text_to_epoch_us(values[name])
            for name in _UTC_FIELDS
            if values[name] is not None
        }
        if not incomplete:
            _validate_relationships(values, times)
        canonical = json.dumps(
            values, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (ValueError, TypeError) as exc:
        raise _invalid() from exc
    return CanonicalLegacyAcceptanceObservation(
        values=MappingProxyType(values),
        observation_status=cast("ObservationStatus", values["observation_status"]),
        canonical_json=canonical,
        observation_sha256=digest,
        accepted_at_us=times.get("accepted_at"),
    )


def validate_stored_legacy_observation_time(
    observation: CanonicalLegacyAcceptanceObservation,
    *,
    observed_at_us: object,
) -> int:
    """Permit an actual later migration observation without backdating history."""
    if type(observed_at_us) is not int or observed_at_us < 0:
        raise _invalid()
    if observation.accepted_at_us is not None and observed_at_us < observation.accepted_at_us:
        raise _invalid()
    return observed_at_us


def build_live_legacy_acceptance_observation(
    value: Mapping[str, object],
    *,
    observed_at_us: object,
) -> LegacyAcceptanceRecord:
    """Freeze verified live facts at the transaction's acceptance timestamp."""
    observation = parse_legacy_acceptance_observation(value)
    observed = validate_stored_legacy_observation_time(observation, observed_at_us=observed_at_us)
    if (
        observation.observation_status != "verified"
        or observation.values["escrow_status"] != "locked"
        or observation.values["lifecycle_status"] != "accepted"
        or observed != observation.accepted_at_us
    ):
        raise _invalid()
    return LegacyAcceptanceRecord(observation=observation, observed_at_us=observed)


def validate_legacy_continuation(
    cursor: sqlite3.Cursor,
    *,
    task_id: str,
    worker_id: str,
    expected_lifecycle_version: int,
    accepted_binding_kind: str,
    accepted_binding_sha256: str,
) -> CanonicalLegacyAcceptanceObservation:
    """Revalidate one stored legacy binding against every authoritative current fact."""
    contract = _stored_legacy_contract(
        cursor,
        task_id=task_id,
        accepted_binding_kind=accepted_binding_kind,
        accepted_binding_sha256=accepted_binding_sha256,
    )

    row = cursor.execute(
        "SELECT observation_status,canonical_observation_json,observation_sha256,observed_at_us "
        "FROM board_legacy_artifact_observations WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if row is None or row[0] == "incomplete_corrupt":
        raise _unavailable()
    if row[0] != "verified" or row[2] != contract[4]:
        raise _invalid()
    try:
        canonical_json = row[1]
        if not isinstance(canonical_json, str):
            raise _invalid()
        decoded: object = json.loads(canonical_json)
        observation = parse_legacy_acceptance_observation(decoded)
        if (
            observation.observation_status != "verified"
            or observation.values["lifecycle_status"] != "accepted"
            or observation.values["escrow_status"] != "locked"
            or observation.canonical_json != canonical_json
            or observation.observation_sha256 != row[2]
            or observation.observation_sha256 != accepted_binding_sha256
        ):
            raise _invalid()
        validate_stored_legacy_observation_time(observation, observed_at_us=row[3])
    except ServiceError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _invalid() from exc

    task = cursor.execute(
        "SELECT task_id,poster_id,title,spec,reward,status,bidding_deadline_seconds,"
        "deadline_seconds,review_deadline_seconds,bidding_deadline,escrow_id,worker_id,"
        "accepted_bid_id,accepted_at,execution_deadline FROM board_tasks WHERE task_id=?",
        (task_id,),
    ).fetchone()
    timing = cursor.execute(
        "SELECT lifecycle_version,timing_contract_version,execution_cutoff_us,"
        "accepted_reward,accepted_bid_amount FROM board_task_contracts WHERE task_id=?",
        (task_id,),
    ).fetchone()
    values = observation.values
    if task is None or timing is None:
        raise _invalid()
    bid_id = task[12]
    bid = cursor.execute(
        "SELECT bid_id,task_id,bidder_id,proposal,amount,submitted_at FROM board_bids "
        "WHERE bid_id=? AND task_id=?",
        (bid_id, task_id),
    ).fetchone()
    escrow = cursor.execute(
        "SELECT escrow_id,payer_account_id,amount,task_id,status FROM bank_escrow "
        "WHERE escrow_id=?",
        (task[10],),
    ).fetchone()
    if bid is None or escrow is None:
        raise _invalid()
    expected_task: tuple[Any, ...] = (
        values["task_id"],
        values["poster_id"],
        values["title"],
        values["spec"],
        values["reward"],
        values["lifecycle_status"],
        values["bidding_deadline_seconds"],
        values["deadline_seconds"],
        values["review_deadline_seconds"],
        values["bidding_deadline"],
        values["escrow_id"],
        values["worker_id"],
        values["accepted_bid_id"],
        values["accepted_at"],
        values["execution_cutoff_at"],
    )
    expected_timing = (
        values["lifecycle_version"],
        values["timing_contract_version"],
        values["execution_cutoff_us"],
        values["accepted_reward"],
        values["accepted_bid_amount"],
    )
    expected_bid = (
        values["accepted_bid_id"],
        task_id,
        values["worker_id"],
        values["winning_bid_proposal"],
        values["winning_bid_amount"],
        values["winning_bid_submitted_at"],
    )
    expected_escrow = (
        values["escrow_id"],
        values["escrow_payer_id"],
        values["escrow_amount"],
        task_id,
        values["escrow_status"],
    )
    if (
        tuple(task) != expected_task
        or task[11] != worker_id
        or tuple(timing) != expected_timing
        or timing[0] != expected_lifecycle_version
        or tuple(bid) != expected_bid
        or tuple(escrow) != expected_escrow
        or contract[1] != values["input_provenance"]
        or contract[6] != values["source_publication_request_sha256"]
    ):
        raise _invalid()
    validate_legacy_publication_provenance(
        cursor,
        task_id=task_id,
        poster_id=str(task[1]),
        source_publication_request_sha256=contract[6],
    )
    return observation


__all__ = [
    "CanonicalLegacyAcceptanceObservation",
    "LegacyAcceptanceRecord",
    "build_live_legacy_acceptance_observation",
    "parse_legacy_acceptance_observation",
    "validate_legacy_continuation",
    "validate_legacy_publication_provenance",
    "validate_stored_legacy_observation_time",
]
