"""Test-only strict protocol-V2 input upload facts and helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from typing import Any, Final

from db_gateway_service.artifact_upload_requests import (
    parse_artifact_policy,
    parse_artifact_upload_request,
)
from db_gateway_service.services.artifact_writer import ArtifactWriter
from tests.fakes.mp09_input_publication_fixture import (
    BOARD_KID,
    NOW_US,
    POSTER_ID,
    PUBLICATION_ID,
    TASK_ID,
)

INPUT_BODY_A: Final[bytes] = b"input alpha\n"
INPUT_BODY_B: Final[bytes] = b"input beta\n"
INPUT_HASH_A: Final[str] = hashlib.sha256(INPUT_BODY_A).hexdigest()
INPUT_HASH_B: Final[str] = hashlib.sha256(INPUT_BODY_B).hexdigest()
INTERNAL_RESERVE_RESOURCE: Final[str] = "POST /board/artifact-uploads/reserve"
MAX_UPLOAD_DURATION_SECONDS: Final[int] = 900


@dataclass(slots=True)
class InputClock:
    """Deterministic microsecond clock shared by attempt operations."""

    now_us: int = NOW_US

    def __call__(self) -> int:
        return self.now_us


def canonical_json(value: object) -> str:
    """Return exact repository canonical JSON."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def input_upload_payload(
    *,
    operation_number: int,
    manifest_id: str,
    body: bytes,
    display_filename: str,
    **overrides: object,
) -> dict[str, object]:
    """Build one exact public signed input-upload intent."""
    values: dict[str, object] = {
        "protocol_version": 2,
        "upload_operation_id": f"80000000-0000-4000-8000-{operation_number:012d}",
        "resource": f"POST /task-publications/{PUBLICATION_ID}/inputs",
        "manifest_kind": "input",
        "publication_operation_id": PUBLICATION_ID,
        "task_id": TASK_ID,
        "manifest_id": manifest_id,
        "actor_id": POSTER_ID,
        "actor_role": "poster",
        "display_filename": display_filename,
        "evidence_format": "text",
        "declared_size_bytes": len(body),
        "content_sha256": hashlib.sha256(body).hexdigest(),
    }
    values.update(overrides)
    return values


def worker_v1_payload() -> dict[str, object]:
    """Return one complete existing worker-V1 request identity."""
    return {
        "protocol_version": 1,
        "upload_operation_id": "80000000-0000-4000-8000-000000000099",
        "resource": INTERNAL_RESERVE_RESOURCE,
        "task_id": "task-worker-v1-control",
        "manifest_id": "manifest-worker-v1-control",
        "actor_id": "worker-v1-control",
        "actor_role": "worker",
        "display_filename": "answer.txt",
        "evidence_format": "text",
        "declared_size_bytes": len(INPUT_BODY_A),
        "content_sha256": INPUT_HASH_A,
        "expected_lifecycle_version": 1,
        "accepted_binding_kind": "publication_contract",
        "accepted_binding_sha256": "a" * 64,
    }


def parse_scoped_request(payload: object) -> Any:
    """Require the missing strict tagged-union parser inside a test call."""
    from db_gateway_service import artifact_upload_requests

    parser = getattr(artifact_upload_requests, "parse_scoped_artifact_upload_request", None)
    assert callable(parser), "Missing V2 capability: parse_scoped_artifact_upload_request"
    return parser(payload)


def make_artifact_writer(connection: Any, clock: InputClock) -> ArtifactWriter:
    """Construct the shared attempt writer with explicit tiny policy."""
    policy = parse_artifact_policy(
        {
            "contract_version": 1,
            "max_file_bytes": 32,
            "max_files_per_manifest": 2,
            "max_manifest_bytes": 64,
            "reservation_ttl_seconds": 300,
            "reservation_renew_before_expiry_seconds": 60,
            "max_upload_duration_seconds": MAX_UPLOAD_DURATION_SECONDS,
        }
    )
    return ArtifactWriter(
        connection,
        clock_us=clock,
        policy=policy,
        current_task_board_caller_kid=BOARD_KID,
    )


def assert_worker_v1_canonical_unchanged() -> None:
    """Prove scoped dispatch preserves the exact existing worker-V1 value."""
    payload = worker_v1_payload()
    original = parse_artifact_upload_request(payload)
    scoped = parse_scoped_request(payload)
    rendered = canonical_json(payload)
    assert type(scoped) is type(original)
    assert tuple(field.name for field in fields(scoped)) == tuple(
        field.name for field in fields(original)
    )
    assert tuple(getattr(scoped, field.name) for field in fields(scoped)) == tuple(
        getattr(original, field.name) for field in fields(original)
    )
    assert original.canonical_json == rendered
    assert scoped.canonical_json == rendered
    expected_hash = hashlib.sha256(rendered.encode()).hexdigest()
    assert original.request_sha256 == expected_hash
    assert scoped.request_sha256 == expected_hash
