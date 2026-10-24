"""Pure canonical artifact metadata and upload-ledger validation."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final
from uuid import UUID

from service_commons.exceptions import ServiceError

from db_gateway_service.artifact_upload_requests import (
    InputArtifactUploadRequestV2,
    ScopedArtifactUploadRequest,
    parse_scoped_artifact_upload_request,
)
from db_gateway_service.task_time import epoch_us_to_utc_text

MEDIA_TYPES: Final[dict[str, str]] = {
    "text": "text/plain; charset=utf-8",
    "source": "text/plain; charset=utf-8",
    "json": "application/json",
    "csv": "text/csv; charset=utf-8",
}
UPLOAD_COLUMNS: Final[str] = (
    "upload_operation_id,request_sha256,canonical_request_json,caller_kid,resource,task_id,"
    "manifest_id,actor_id,actor_role,display_filename,evidence_format,media_type,"
    "declared_size_bytes,content_sha256,expected_lifecycle_version,accepted_binding_kind,"
    "accepted_binding_sha256,state,artifact_id,object_key,current_attempt_id,created_at_us,"
    "finalized_at_us,manifest_kind,publication_operation_id"
)


def _corrupt() -> ServiceError:
    return ServiceError(
        "artifact_state_corrupt",
        "Persisted artifact upload state failed integrity validation",
        500,
        {},
    )


def validate_upload_row(
    upload: tuple[Any, ...],
    *,
    expected_caller_kid: str | None,
) -> ScopedArtifactUploadRequest:
    """Reparse canonical intent and compare every immutable upload projection."""
    if expected_caller_kid is not None and str(upload[3]) != expected_caller_kid:
        raise ServiceError(
            "artifact_caller_mismatch",
            "Artifact upload belongs to another verified Task Board caller",
            403,
            {},
        )
    try:
        canonical = str(upload[2])
        decoded: object = json.loads(canonical)
        request = parse_scoped_artifact_upload_request(decoded)
        if request.canonical_json != canonical or request.request_sha256 != str(upload[1]):
            raise _corrupt()
        manifest_kind = (
            "input" if isinstance(request, InputArtifactUploadRequestV2) else "submission"
        )
        publication_id = (
            request.publication_operation_id
            if isinstance(request, InputArtifactUploadRequestV2)
            else None
        )
        lifecycle_version = (
            None
            if isinstance(request, InputArtifactUploadRequestV2)
            else request.expected_lifecycle_version
        )
        binding_kind = (
            None
            if isinstance(request, InputArtifactUploadRequestV2)
            else request.accepted_binding_kind
        )
        binding_sha256 = (
            None
            if isinstance(request, InputArtifactUploadRequestV2)
            else request.accepted_binding_sha256
        )
        projections = (
            request.upload_operation_id,
            request.resource,
            request.task_id,
            request.manifest_id,
            request.actor_id,
            request.actor_role,
            request.display_filename,
            request.evidence_format,
            MEDIA_TYPES[request.evidence_format],
            request.declared_size_bytes,
            request.content_sha256,
            lifecycle_version,
            binding_kind,
            binding_sha256,
            manifest_kind,
            publication_id,
        )
        persisted = (
            str(upload[0]),
            str(upload[4]),
            str(upload[5]),
            str(upload[6]),
            str(upload[7]),
            str(upload[8]),
            str(upload[9]),
            str(upload[10]),
            str(upload[11]),
            int(upload[12]),
            str(upload[13]),
            None if upload[14] is None else int(upload[14]),
            None if upload[15] is None else str(upload[15]),
            None if upload[16] is None else str(upload[16]),
            str(upload[23]),
            None if upload[24] is None else str(upload[24]),
        )
        if persisted != projections:
            raise _corrupt()
        artifact_id = str(upload[18])
        parsed_artifact = UUID(artifact_id)
        if parsed_artifact.version != 4 or str(parsed_artifact) != artifact_id:
            raise _corrupt()
        if str(upload[19]) != f"live/{artifact_id[:2]}/{artifact_id}.blob":
            raise _corrupt()
        return request
    except ServiceError as exc:
        if exc.error == "artifact_state_corrupt":
            raise
        raise _corrupt() from exc
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _corrupt() from exc


def validate_attempt_for_upload(
    upload: tuple[Any, ...],
    attempt: tuple[Any, ...],
) -> None:
    """Validate current-attempt parentage and immutable intended destination."""
    if (
        str(attempt[1]) != str(upload[0])
        or str(attempt[6]) != str(upload[19])
        or upload[20] is None
        or str(attempt[0]) != str(upload[20])
    ):
        raise _corrupt()


def artifact_item(upload: tuple[Any, ...]) -> dict[str, object]:
    """Project one already-validated ready upload into canonical manifest metadata."""
    return {
        "artifact_id": str(upload[18]),
        "display_filename": str(upload[9]),
        "evidence_format": str(upload[10]),
        "media_type": str(upload[11]),
        "byte_size": int(upload[12]),
        "sha256": str(upload[13]),
        "provenance": "v6_upload",
        "actor_id": str(upload[7]),
        "uploaded_at": epoch_us_to_utc_text(int(upload[22])),
    }


def canonical_manifest(
    *,
    manifest_id: str,
    task_id: str,
    kind: str,
    actor_id: str,
    uploads: list[tuple[Any, ...]],
) -> tuple[str, str]:
    """Build exact sorted canonical manifest bytes and SHA-256."""
    value = {
        "format_version": 1,
        "manifest_id": manifest_id,
        "task_id": task_id,
        "kind": kind,
        "actor_id": actor_id,
        "artifacts": [
            artifact_item(upload) for upload in sorted(uploads, key=lambda row: str(row[18]))
        ],
    }
    rendered = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return rendered, hashlib.sha256(rendered.encode("utf-8")).hexdigest()


__all__ = [
    "MEDIA_TYPES",
    "UPLOAD_COLUMNS",
    "artifact_item",
    "canonical_manifest",
    "validate_attempt_for_upload",
    "validate_upload_row",
]
