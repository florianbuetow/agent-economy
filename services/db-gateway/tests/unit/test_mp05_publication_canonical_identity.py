"""The persisted posting request retains its identity; its semantic hash excludes it."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest

from db_gateway_service.task_publication_requests import parse_task_publication_request
from tests.fakes.mp05_publication_history import publication_intent

pytestmark = pytest.mark.unit


def test_canonical_publication_request_preserves_every_admitted_field() -> None:
    payload = publication_intent("a-poster").body()
    request = parse_task_publication_request(payload)
    assert json.loads(request.canonical_json) == payload
    assert request.canonical_json == json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def test_distinct_operation_identity_changes_saved_request_but_not_semantic_hash() -> None:
    payload = publication_intent("a-poster").body()
    first = parse_task_publication_request(payload)
    payload["operation_id"] = f"op-{uuid4()}"
    second = parse_task_publication_request(payload)
    assert first.canonical_json != second.canonical_json
    assert first.request_sha256 == second.request_sha256
    semantic = {key: value for key, value in payload.items() if key != "operation_id"}
    encoded = json.dumps(
        semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    assert first.request_sha256 == hashlib.sha256(encoded).hexdigest()
