"""Strict immutable request types for V6 artifact upload reservations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, cast
from uuid import UUID

from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.task_operation_requests import parse_operation_id

type ActorRole = Literal["poster", "worker"]
type EvidenceFormat = Literal["text", "source", "json", "csv"]
type AcceptedBindingKind = Literal[
    "publication_contract",
    "legacy_accepted_continuation",
]

_POLICY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "contract_version",
        "max_file_bytes",
        "max_files_per_manifest",
        "max_manifest_bytes",
        "reservation_ttl_seconds",
        "reservation_renew_before_expiry_seconds",
        "max_upload_duration_seconds",
    }
)
_REQUEST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "protocol_version",
        "upload_operation_id",
        "resource",
        "task_id",
        "manifest_id",
        "actor_id",
        "actor_role",
        "display_filename",
        "evidence_format",
        "declared_size_bytes",
        "content_sha256",
        "expected_lifecycle_version",
        "accepted_binding_kind",
        "accepted_binding_sha256",
    }
)
_RESERVE_RESOURCE: Final[str] = "POST /board/artifact-uploads/reserve"
_HASH_LENGTH: Final[int] = 64
_INPUT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "protocol_version",
        "upload_operation_id",
        "resource",
        "manifest_kind",
        "publication_operation_id",
        "task_id",
        "manifest_id",
        "actor_id",
        "actor_role",
        "display_filename",
        "evidence_format",
        "declared_size_bytes",
        "content_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class ArtifactPolicy:
    """Explicit finite limits used by the reservation writer."""

    contract_version: int
    max_file_bytes: int
    max_files_per_manifest: int
    max_manifest_bytes: int
    reservation_ttl_seconds: int
    reservation_renew_before_expiry_seconds: int
    max_upload_duration_seconds: int


@dataclass(frozen=True, slots=True)
class ArtifactUploadRequest:
    """Canonical client intent for one upload operation."""

    protocol_version: int
    upload_operation_id: str
    resource: str
    task_id: str
    manifest_id: str
    actor_id: str
    actor_role: ActorRole
    display_filename: str
    evidence_format: EvidenceFormat
    declared_size_bytes: int
    content_sha256: str
    expected_lifecycle_version: int
    accepted_binding_kind: AcceptedBindingKind
    accepted_binding_sha256: str
    canonical_json: str
    request_sha256: str


@dataclass(frozen=True, slots=True)
class InputArtifactUploadRequestV2:
    """Canonical poster-owned prepublication input-upload intent."""

    protocol_version: Literal[2]
    upload_operation_id: str
    resource: str
    manifest_kind: Literal["input"]
    publication_operation_id: str
    task_id: str
    manifest_id: str
    actor_id: str
    actor_role: Literal["poster"]
    display_filename: str
    evidence_format: EvidenceFormat
    declared_size_bytes: int
    content_sha256: str
    canonical_json: str
    request_sha256: str


type ScopedArtifactUploadRequest = ArtifactUploadRequest | InputArtifactUploadRequestV2


def _error(code: str, message: str) -> ServiceError:
    return ServiceError(code, message, 400, {})


def _strict_integer(value: object, *, minimum: int) -> int:
    if type(value) is not int or not minimum <= value <= SQLITE_MAX_INTEGER:
        raise ValueError
    return value


def _nonempty_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def _lower_hex_hash(value: object) -> str:
    text = _nonempty_text(value)
    if len(text) != _HASH_LENGTH or any(character not in "0123456789abcdef" for character in text):
        raise ValueError
    return text


def _strict_mapping(payload: object, fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError
    if any(not isinstance(key, str) for key in payload) or frozenset(payload) != fields:
        raise ValueError
    return cast("Mapping[str, object]", payload)


def _uuid4_text(value: object) -> str:
    text = _nonempty_text(value)
    try:
        parsed = UUID(text)
    except ValueError as exc:
        raise ValueError from exc
    if parsed.version != 4 or str(parsed) != text:
        raise ValueError
    return text


def parse_artifact_policy(payload: object) -> ArtifactPolicy:
    """Parse a complete policy without defaults or ignored fields."""
    try:
        values = _strict_mapping(payload, _POLICY_FIELDS)
        contract_version = _strict_integer(values["contract_version"], minimum=1)
        max_file_bytes = _strict_integer(values["max_file_bytes"], minimum=1)
        max_files = _strict_integer(values["max_files_per_manifest"], minimum=1)
        max_manifest_bytes = _strict_integer(values["max_manifest_bytes"], minimum=1)
        ttl = _strict_integer(values["reservation_ttl_seconds"], minimum=1)
        renew_before = _strict_integer(values["reservation_renew_before_expiry_seconds"], minimum=0)
        duration = _strict_integer(values["max_upload_duration_seconds"], minimum=1)
        if contract_version != 1:
            raise ValueError
        if max_file_bytes > max_manifest_bytes or renew_before >= ttl or ttl > duration:
            raise ValueError
    except (KeyError, ValueError, TypeError) as exc:
        raise _error("invalid_artifact_policy", "Artifact policy is invalid") from exc
    return ArtifactPolicy(
        contract_version=contract_version,
        max_file_bytes=max_file_bytes,
        max_files_per_manifest=max_files,
        max_manifest_bytes=max_manifest_bytes,
        reservation_ttl_seconds=ttl,
        reservation_renew_before_expiry_seconds=renew_before,
        max_upload_duration_seconds=duration,
    )


def parse_artifact_upload_request(payload: object) -> ArtifactUploadRequest:
    """Parse and hash every immutable upload-intent field."""
    try:
        values = _strict_mapping(payload, _REQUEST_FIELDS)
        protocol_version = _strict_integer(values["protocol_version"], minimum=1)
        if protocol_version != 1:
            raise ValueError
        operation_id = _uuid4_text(values["upload_operation_id"])
        resource = _nonempty_text(values["resource"])
        if resource != _RESERVE_RESOURCE:
            raise ValueError
        task_id = _nonempty_text(values["task_id"])
        manifest_id = _nonempty_text(values["manifest_id"])
        actor_id = _nonempty_text(values["actor_id"])
        actor_role_value = _nonempty_text(values["actor_role"])
        if actor_role_value not in {"poster", "worker"}:
            raise ValueError
        evidence_format_value = _nonempty_text(values["evidence_format"])
        if evidence_format_value not in {"text", "source", "json", "csv"}:
            raise ValueError
        binding_kind_value = _nonempty_text(values["accepted_binding_kind"])
        if binding_kind_value not in {
            "publication_contract",
            "legacy_accepted_continuation",
        }:
            raise ValueError
        display_filename = _nonempty_text(values["display_filename"])
        declared_size = _strict_integer(values["declared_size_bytes"], minimum=0)
        content_sha256 = _lower_hex_hash(values["content_sha256"])
        lifecycle_version = _strict_integer(values["expected_lifecycle_version"], minimum=0)
        binding_sha256 = _lower_hex_hash(values["accepted_binding_sha256"])
        canonical_json = json.dumps(
            dict(values),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise _error("invalid_payload", "Artifact upload request is invalid") from exc
    return ArtifactUploadRequest(
        protocol_version=protocol_version,
        upload_operation_id=operation_id,
        resource=resource,
        task_id=task_id,
        manifest_id=manifest_id,
        actor_id=actor_id,
        actor_role=cast("ActorRole", actor_role_value),
        display_filename=display_filename,
        evidence_format=cast("EvidenceFormat", evidence_format_value),
        declared_size_bytes=declared_size,
        content_sha256=content_sha256,
        expected_lifecycle_version=lifecycle_version,
        accepted_binding_kind=cast("AcceptedBindingKind", binding_kind_value),
        accepted_binding_sha256=binding_sha256,
        canonical_json=canonical_json,
        request_sha256=hashlib.sha256(canonical_json.encode()).hexdigest(),
    )


def _parse_input_upload_request(payload: object) -> InputArtifactUploadRequestV2:
    try:
        values = _strict_mapping(payload, _INPUT_FIELDS)
        protocol_version = _strict_integer(values["protocol_version"], minimum=1)
        if protocol_version != 2:
            raise ValueError
        operation_id = _uuid4_text(values["upload_operation_id"])
        publication_id = parse_operation_id(values["publication_operation_id"])
        resource = _nonempty_text(values["resource"])
        if resource != f"POST /task-publications/{publication_id}/inputs":
            raise ValueError
        if values["manifest_kind"] != "input" or values["actor_role"] != "poster":
            raise ValueError
        task_id = _nonempty_text(values["task_id"])
        manifest_id = _nonempty_text(values["manifest_id"])
        actor_id = _nonempty_text(values["actor_id"])
        display_filename = _nonempty_text(values["display_filename"])
        evidence_format_value = _nonempty_text(values["evidence_format"])
        if evidence_format_value not in {"text", "source", "json", "csv"}:
            raise ValueError
        declared_size = _strict_integer(values["declared_size_bytes"], minimum=0)
        content_sha256 = _lower_hex_hash(values["content_sha256"])
        canonical_json = json.dumps(
            dict(values),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (KeyError, ValueError, TypeError, ServiceError) as exc:
        raise _error("invalid_payload", "Artifact upload request is invalid") from exc
    return InputArtifactUploadRequestV2(
        protocol_version=2,
        upload_operation_id=operation_id,
        resource=resource,
        manifest_kind="input",
        publication_operation_id=publication_id,
        task_id=task_id,
        manifest_id=manifest_id,
        actor_id=actor_id,
        actor_role="poster",
        display_filename=display_filename,
        evidence_format=cast("EvidenceFormat", evidence_format_value),
        declared_size_bytes=declared_size,
        content_sha256=content_sha256,
        canonical_json=canonical_json,
        request_sha256=hashlib.sha256(canonical_json.encode()).hexdigest(),
    )


def parse_scoped_artifact_upload_request(payload: object) -> ScopedArtifactUploadRequest:
    """Dispatch an exact worker-V1 or input-V2 request without normalization."""
    if not isinstance(payload, Mapping):
        raise _error("invalid_payload", "Artifact upload request is invalid")
    version = payload.get("protocol_version")
    if type(version) is not int:
        raise _error("invalid_payload", "Artifact upload request is invalid")
    if version == 1:
        return parse_artifact_upload_request(payload)
    if version == 2:
        return _parse_input_upload_request(payload)
    raise _error("invalid_payload", "Artifact upload request is invalid")
