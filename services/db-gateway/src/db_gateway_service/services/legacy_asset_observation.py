"""Pure canonical contracts for migrated legacy asset evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.task_time import parse_utc_text_to_epoch_us

type LegacyAssetScalar = str | int | None
type LegacyAssetValue = LegacyAssetScalar | MappingProxyType[str, LegacyAssetScalar]
type AttributionStatus = Literal["verified_worker", "worker_unassigned", "uploader_mismatch"]
type AssetSetKind = Literal[
    "source_rows", "asset_observations", "observed_bytes", "generated_copies"
]

_SOURCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "asset_id",
        "task_id",
        "uploader_id",
        "filename",
        "content_type",
        "size_bytes",
        "storage_path",
        "content_hash",
        "uploaded_at",
    }
)
_SOURCE_TEXT_FIELDS: Final[frozenset[str]] = frozenset(
    {"asset_id", "task_id", "uploader_id", "filename", "content_type", "storage_path"}
)
_OBSERVATION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "format_version",
        "migration_plan_id",
        "legacy_asset_id",
        "source_row",
        "source_row_sha256",
        "assigned_worker_id",
        "attribution_status",
        "byte_status",
        "declared_hash_status",
        "copy_status",
        "observed_size_bytes",
        "observed_sha256",
        "generated_artifact_id",
        "generated_object_key",
        "observed_at_us",
    }
)
_ROW_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "observation_id",
        "observation_contract_version",
        "migration_plan_id",
        "legacy_asset_id",
        "task_id",
        "uploader_id",
        "assigned_worker_id",
        "source_row_sha256",
        "attribution_status",
        "byte_status",
        "declared_hash_status",
        "copy_status",
        "canonical_observation_json",
        "observation_sha256",
        "observed_at_us",
        "observed_size_bytes",
        "observed_sha256",
        "generated_artifact_id",
        "generated_object_key",
    }
)
_ATTRIBUTION_STATUSES: Final[frozenset[str]] = frozenset(
    {"verified_worker", "worker_unassigned", "uploader_mismatch"}
)
_NO_READ_STATUSES: Final[frozenset[str]] = frozenset(
    {"unsafe_path", "shared_source_conflict", "missing", "symlink", "non_regular"}
)
_BYTE_STATUSES: Final[frozenset[str]] = _NO_READ_STATUSES | {
    "verified",
    "size_mismatch",
    "hash_mismatch",
}
_DECLARED_HASH_STATUSES: Final[frozenset[str]] = frozenset(
    {"absent", "unobserved", "matched", "mismatch"}
)
_COPY_STATUSES: Final[frozenset[str]] = frozenset({"installed", "unavailable"})
_SHA256: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class CanonicalLegacyAssetSourceRow:
    values: MappingProxyType[str, LegacyAssetScalar]
    canonical_json: str
    source_row_sha256: str


@dataclass(frozen=True, slots=True)
class CanonicalLegacyAssetSourceContext:
    source: CanonicalLegacyAssetSourceRow
    assigned_worker_id: str | None
    attribution_status: AttributionStatus


@dataclass(frozen=True, slots=True)
class MigratedArtifactIdentity:
    artifact_id: str
    object_key: str


@dataclass(frozen=True, slots=True)
class CanonicalLegacyAssetObservation:
    values: MappingProxyType[str, LegacyAssetValue]
    observation_id: str
    canonical_json: str
    observation_sha256: str


@dataclass(frozen=True, slots=True)
class CanonicalAssetSet:
    entries: tuple[tuple[str | int, ...], ...]
    canonical_json: str
    sha256: str


def _error(code: str, message: str) -> ServiceError:
    return ServiceError(code, message, 500, {})


def _source_invalid() -> ServiceError:
    return _error("source_asset_row_invalid", "Legacy asset source row is invalid")


def _reference_invalid() -> ServiceError:
    return _error("source_asset_reference_invalid", "Legacy asset references are invalid")


def _observation_invalid() -> ServiceError:
    return _error("legacy_asset_observation_invalid", "Legacy asset observation is invalid")


def _canonical(value: object, *, error: ServiceError) -> str:
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        rendered.encode("utf-8")
        return rendered
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise error from exc


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_text(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _is_integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= SQLITE_MAX_INTEGER


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _observation_invalid()
    copied = dict(cast("Mapping[object, object]", value))
    if not all(isinstance(key, str) for key in copied) or frozenset(copied) != fields:
        raise _observation_invalid()
    return cast("dict[str, object]", copied)


def _source_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _source_invalid()
    copied = dict(cast("Mapping[object, object]", value))
    if not all(isinstance(key, str) for key in copied) or frozenset(copied) != _SOURCE_FIELDS:
        raise _source_invalid()
    return cast("dict[str, object]", copied)


def _freeze_source(value: Mapping[str, object]) -> MappingProxyType[str, LegacyAssetScalar]:
    return MappingProxyType({key: cast("LegacyAssetScalar", item) for key, item in value.items()})


def parse_legacy_asset_source_row(value: object) -> CanonicalLegacyAssetSourceRow:
    """Parse, copy and hash one exact historical ``board_assets`` row."""
    source = _source_mapping(value)
    if any(not _is_text(source[field]) for field in _SOURCE_TEXT_FIELDS):
        raise _source_invalid()
    if not _is_integer(source["size_bytes"]):
        raise _source_invalid()
    content_hash = source["content_hash"]
    if content_hash is not None and not _is_hash(content_hash):
        raise _source_invalid()
    uploaded_at = source["uploaded_at"]
    if not _is_text(uploaded_at):
        raise _source_invalid()
    try:
        parse_utc_text_to_epoch_us(uploaded_at)
    except ValueError as exc:
        raise _source_invalid() from exc
    canonical = _canonical(source, error=_source_invalid())
    return CanonicalLegacyAssetSourceRow(
        values=_freeze_source(source),
        canonical_json=canonical,
        source_row_sha256=_digest(canonical),
    )


def _reference(value: object, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _reference_invalid()
    copied = dict(cast("Mapping[object, object]", value))
    if not all(isinstance(key, str) for key in copied) or frozenset(copied) != fields:
        raise _reference_invalid()
    return cast("dict[str, object]", copied)


def validate_legacy_asset_source_context(
    source: CanonicalLegacyAssetSourceRow,
    *,
    task: object,
    uploader: object,
    worker: object,
) -> CanonicalLegacyAssetSourceContext:
    """Bind a parsed source row to exact authoritative task and identity facts."""
    if type(source) is not CanonicalLegacyAssetSourceRow:
        raise _reference_invalid()
    task_value = _reference(task, frozenset({"task_id", "worker_id"}))
    uploader_value = _reference(uploader, frozenset({"agent_id"}))
    task_id = task_value["task_id"]
    worker_id = task_value["worker_id"]
    uploader_id = uploader_value["agent_id"]
    if (
        not _is_text(task_id)
        or task_id != source.values["task_id"]
        or not _is_text(uploader_id)
        or uploader_id != source.values["uploader_id"]
        or (worker_id is not None and not _is_text(worker_id))
    ):
        raise _reference_invalid()
    if worker_id is None:
        if worker is not None:
            raise _reference_invalid()
        return CanonicalLegacyAssetSourceContext(source, None, "worker_unassigned")
    worker_value = _reference(worker, frozenset({"agent_id"}))
    if not _is_text(worker_value["agent_id"]) or worker_value["agent_id"] != worker_id:
        raise _reference_invalid()
    attribution: AttributionStatus = (
        "verified_worker" if worker_id == uploader_id else "uploader_mismatch"
    )
    return CanonicalLegacyAssetSourceContext(source, cast("str", worker_id), attribution)


def _plan_and_asset(migration_plan_id: object, legacy_asset_id: object) -> tuple[str, str]:
    if not _is_hash(migration_plan_id) or not _is_text(legacy_asset_id):
        raise _observation_invalid()
    return cast("str", migration_plan_id), cast("str", legacy_asset_id)


def derive_legacy_asset_observation_id(
    *,
    migration_plan_id: str,
    legacy_asset_id: str,
) -> str:
    """Derive the deterministic per-file observation identity."""
    plan_id, asset_id = _plan_and_asset(migration_plan_id, legacy_asset_id)
    return "obs-" + str(
        uuid5(
            NAMESPACE_URL,
            f"urn:agent-economy:board-legacy-asset-observation:v1:{plan_id}:{asset_id}",
        )
    )


def derive_migrated_artifact_identity(
    *,
    migration_plan_id: str,
    legacy_asset_id: str,
) -> MigratedArtifactIdentity:
    """Derive the deterministic copied-artifact identity and object key."""
    plan_id, asset_id = _plan_and_asset(migration_plan_id, legacy_asset_id)
    artifact_id = str(
        uuid5(
            NAMESPACE_URL,
            f"urn:agent-economy:board-artifact-v6:{plan_id}:{asset_id}",
        )
    )
    return MigratedArtifactIdentity(artifact_id, f"live/{artifact_id[:2]}/{artifact_id}.blob")


def _attribution(values: Mapping[str, object], source: CanonicalLegacyAssetSourceRow) -> None:
    assigned = values["assigned_worker_id"]
    status = values["attribution_status"]
    if status not in _ATTRIBUTION_STATUSES:
        raise _observation_invalid()
    uploader = source.values["uploader_id"]
    if status == "worker_unassigned":
        if assigned is not None:
            raise _observation_invalid()
        return
    if not _is_text(assigned):
        raise _observation_invalid()
    if (status == "verified_worker") != (assigned == uploader):
        raise _observation_invalid()


def _observed_pair(values: Mapping[str, object]) -> tuple[int | None, str | None]:
    observed_size = values["observed_size_bytes"]
    observed_hash = values["observed_sha256"]
    observed = observed_size is not None or observed_hash is not None
    if observed_size is None or observed_hash is None:
        if observed:
            raise _observation_invalid()
        return None, None
    elif not _is_integer(observed_size) or not _is_hash(observed_hash):
        raise _observation_invalid()
    return cast("int", observed_size), cast("str", observed_hash)


def _declared_status(
    source: CanonicalLegacyAssetSourceRow,
    observed_hash: str | None,
) -> str:
    declared_hash = source.values["content_hash"]
    if declared_hash is None:
        return "absent"
    if observed_hash is None:
        return "unobserved"
    return "matched" if observed_hash == declared_hash else "mismatch"


def _validate_byte_status(
    byte_status: object,
    declared_status: object,
    observed_size: int | None,
    source_size: LegacyAssetScalar,
) -> None:
    if byte_status not in _BYTE_STATUSES or declared_status not in _DECLARED_HASH_STATUSES:
        raise _observation_invalid()
    observed = observed_size is not None
    if byte_status in _NO_READ_STATUSES and observed:
        raise _observation_invalid()
    if byte_status not in _NO_READ_STATUSES and not observed:
        raise _observation_invalid()
    if byte_status == "verified":
        if observed_size != source_size or declared_status not in {"absent", "matched"}:
            raise _observation_invalid()
    elif byte_status == "size_mismatch":
        if observed_size == source_size:
            raise _observation_invalid()
    elif byte_status == "hash_mismatch" and (
        observed_size != source_size or declared_status != "mismatch"
    ):
        raise _observation_invalid()


def _validate_copy_status(values: Mapping[str, object], byte_status: object) -> None:
    copy_status = values["copy_status"]
    generated_id = values["generated_artifact_id"]
    generated_key = values["generated_object_key"]
    if copy_status not in _COPY_STATUSES:
        raise _observation_invalid()
    installed = copy_status == "installed"
    generated = generated_id is not None or generated_key is not None
    if generated_id is None or generated_key is None:
        if generated:
            raise _observation_invalid()
    elif not _is_text(generated_id) or not _is_text(generated_key):
        raise _observation_invalid()
    if installed != (byte_status == "verified") or installed != generated:
        raise _observation_invalid()


def _byte_and_copy(values: Mapping[str, object], source: CanonicalLegacyAssetSourceRow) -> None:
    observed_size, observed_hash = _observed_pair(values)
    byte_status = values["byte_status"]
    declared_status = values["declared_hash_status"]
    if declared_status != _declared_status(source, observed_hash):
        raise _observation_invalid()
    _validate_byte_status(
        byte_status,
        declared_status,
        observed_size,
        source.values["size_bytes"],
    )
    _validate_copy_status(values, byte_status)


def parse_legacy_asset_observation(value: object) -> CanonicalLegacyAssetObservation:
    """Parse and freeze one exact per-file migration observation."""
    observation = _exact_mapping(value, _OBSERVATION_FIELDS)
    if type(observation["format_version"]) is not int or observation["format_version"] != 1:
        raise _observation_invalid()
    plan_id, asset_id = _plan_and_asset(
        observation["migration_plan_id"], observation["legacy_asset_id"]
    )
    try:
        source = parse_legacy_asset_source_row(observation["source_row"])
    except ServiceError as exc:
        raise _observation_invalid() from exc
    if (
        asset_id != source.values["asset_id"]
        or observation["source_row_sha256"] != source.source_row_sha256
    ):
        raise _observation_invalid()
    if not _is_hash(observation["source_row_sha256"]):
        raise _observation_invalid()
    if not _is_integer(observation["observed_at_us"]):
        raise _observation_invalid()
    _attribution(observation, source)
    _byte_and_copy(observation, source)
    identity = derive_migrated_artifact_identity(
        migration_plan_id=plan_id,
        legacy_asset_id=asset_id,
    )
    if observation["copy_status"] == "installed" and (
        observation["generated_artifact_id"] != identity.artifact_id
        or observation["generated_object_key"] != identity.object_key
    ):
        raise _observation_invalid()
    copied: dict[str, LegacyAssetValue] = {
        key: (
            MappingProxyType(dict(source.values))
            if key == "source_row"
            else cast("LegacyAssetScalar", item)
        )
        for key, item in observation.items()
    }
    canonical_source = dict(source.values)
    canonical_input = {
        key: (canonical_source if key == "source_row" else item)
        for key, item in observation.items()
    }
    canonical = _canonical(canonical_input, error=_observation_invalid())
    return CanonicalLegacyAssetObservation(
        values=MappingProxyType(copied),
        observation_id=derive_legacy_asset_observation_id(
            migration_plan_id=plan_id,
            legacy_asset_id=asset_id,
        ),
        canonical_json=canonical,
        observation_sha256=_digest(canonical),
    )


def _canonical_uuid(value: object, *, observation: bool) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.removeprefix("obs-") if observation else value
    if observation and not value.startswith("obs-"):
        return False
    try:
        parsed = UUID(candidate)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 5 and str(parsed) == candidate


def validate_legacy_asset_observation_row(
    context: CanonicalLegacyAssetSourceContext,
    row: object,
) -> CanonicalLegacyAssetObservation:
    """Reparse canonical bytes and validate all 19 stored projections."""
    if type(context) is not CanonicalLegacyAssetSourceContext:
        raise _observation_invalid()
    stored = _exact_mapping(row, _ROW_FIELDS)
    canonical = stored["canonical_observation_json"]
    if not isinstance(canonical, str):
        raise _observation_invalid()
    try:
        decoded: object = json.loads(canonical)
    except (json.JSONDecodeError, TypeError) as exc:
        raise _observation_invalid() from exc
    parsed = parse_legacy_asset_observation(decoded)
    if canonical != parsed.canonical_json:
        raise _observation_invalid()
    if (
        type(stored["observation_contract_version"]) is not int
        or stored["observation_contract_version"] != 1
    ):
        raise _observation_invalid()
    values = parsed.values
    source = cast("Mapping[str, LegacyAssetScalar]", values["source_row"])
    projections: dict[str, object] = {
        "observation_id": parsed.observation_id,
        "migration_plan_id": values["migration_plan_id"],
        "legacy_asset_id": values["legacy_asset_id"],
        "task_id": source["task_id"],
        "uploader_id": source["uploader_id"],
        "assigned_worker_id": values["assigned_worker_id"],
        "source_row_sha256": values["source_row_sha256"],
        "attribution_status": values["attribution_status"],
        "byte_status": values["byte_status"],
        "declared_hash_status": values["declared_hash_status"],
        "copy_status": values["copy_status"],
        "canonical_observation_json": parsed.canonical_json,
        "observation_sha256": parsed.observation_sha256,
        "observed_at_us": values["observed_at_us"],
        "observed_size_bytes": values["observed_size_bytes"],
        "observed_sha256": values["observed_sha256"],
        "generated_artifact_id": values["generated_artifact_id"],
        "generated_object_key": values["generated_object_key"],
    }
    if any(stored[name] != expected for name, expected in projections.items()):
        raise _observation_invalid()
    if (
        values["source_row"] != context.source.values
        or values["assigned_worker_id"] != context.assigned_worker_id
        or values["attribution_status"] != context.attribution_status
    ):
        raise _observation_invalid()
    return parsed


def _set_entry(kind: AssetSetKind, value: object) -> tuple[str | int, ...]:
    if not isinstance(value, (list, tuple)):
        raise _observation_invalid()
    item = tuple(value)
    lengths = {
        "source_rows": 2,
        "asset_observations": 2,
        "observed_bytes": 3,
        "generated_copies": 4,
    }
    if len(item) != lengths[kind]:
        raise _observation_invalid()
    identity = item[0]
    if kind in {"source_rows", "observed_bytes"}:
        if not _is_text(identity):
            raise _observation_invalid()
    elif not _canonical_uuid(identity, observation=kind == "asset_observations"):
        raise _observation_invalid()
    if kind == "observed_bytes" and not _is_integer(item[1]):
        raise _observation_invalid()
    if kind == "generated_copies":
        if not isinstance(identity, str) or item[1] != f"live/{identity[:2]}/{identity}.blob":
            raise _observation_invalid()
        if not _is_integer(item[2]):
            raise _observation_invalid()
    if not _is_hash(item[-1]):
        raise _observation_invalid()
    return cast("tuple[str | int, ...]", item)


def canonical_asset_set(kind: AssetSetKind, entries: object) -> CanonicalAssetSet:
    """Validate, sort and hash one exact migration evidence set."""
    if kind not in {"source_rows", "asset_observations", "observed_bytes", "generated_copies"}:
        raise _observation_invalid()
    if not isinstance(entries, (list, tuple)):
        raise _observation_invalid()
    parsed = tuple(_set_entry(kind, entry) for entry in entries)
    identities = [entry[0] for entry in parsed]
    if len(set(identities)) != len(identities):
        raise _observation_invalid()
    ordered = tuple(sorted(parsed, key=lambda entry: cast("str", entry[0]).encode("utf-8")))
    canonical = _canonical([list(entry) for entry in ordered], error=_observation_invalid())
    return CanonicalAssetSet(ordered, canonical, _digest(canonical))


__all__ = [
    "CanonicalAssetSet",
    "CanonicalLegacyAssetObservation",
    "CanonicalLegacyAssetSourceContext",
    "CanonicalLegacyAssetSourceRow",
    "MigratedArtifactIdentity",
    "canonical_asset_set",
    "derive_legacy_asset_observation_id",
    "derive_migrated_artifact_identity",
    "parse_legacy_asset_observation",
    "parse_legacy_asset_source_row",
    "validate_legacy_asset_observation_row",
    "validate_legacy_asset_source_context",
]
