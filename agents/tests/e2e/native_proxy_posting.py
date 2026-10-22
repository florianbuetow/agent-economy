"""Effectful native task publication through the UI's operator proxy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import httpx


async def create_task_via_proxy(
    client: httpx.AsyncClient,
    terms: dict[str, object],
) -> dict[str, Any]:
    """Prepare once, retain the exact record, and submit that record once."""
    prepared = await client.post("/api/proxy/task-postings/prepare", json=terms)
    assert prepared.status_code == 200, prepared.text
    record = prepared.json()
    assert isinstance(record, dict)
    assert set(record) == {"record_version", "scope", "intent", "request_sha256"}
    assert record["record_version"] == 1

    scope = record["scope"]
    intent = record["intent"]
    assert isinstance(scope, dict)
    assert set(scope) == {
        "schema_version",
        "operator_agent_id",
        "operator_public_fingerprint_sha256",
        "service_urls",
    }
    assert scope["schema_version"] == 1
    service_urls = scope["service_urls"]
    assert isinstance(service_urls, dict)
    assert set(service_urls) == {
        "identity_url",
        "bank_url",
        "task_board_url",
        "reputation_url",
        "court_url",
    }
    assert isinstance(intent, dict)
    assert set(intent) == {
        "protocol_version",
        "operation_id",
        "task_id",
        "actor_kind",
        "actor_id",
        "lock_operation_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "acceptance_window_seconds",
        "execution_deadline_seconds",
        "review_deadline_seconds",
    }
    operation_id = intent["operation_id"]
    task_id = intent["task_id"]
    request_sha256 = record["request_sha256"]
    assert isinstance(operation_id, str) and operation_id
    assert isinstance(task_id, str) and task_id
    assert isinstance(request_sha256, str) and len(request_sha256) == 64
    assert intent["protocol_version"] == 1
    assert intent["actor_kind"] == "agent"
    assert intent["actor_id"] == scope["operator_agent_id"]

    submitted = await client.post(
        f"/api/proxy/task-postings/{operation_id}/submit",
        json=record,
    )
    assert submitted.status_code == 201, submitted.text
    acknowledgment = submitted.json()
    assert isinstance(acknowledgment, dict)
    assert set(acknowledgment) == {"observation", "status_code", "publication"}
    assert acknowledgment["observation"] == "submit"
    assert acknowledgment["status_code"] == 201

    publication = acknowledgment["publication"]
    assert isinstance(publication, dict)
    assert publication["operation_id"] == operation_id
    assert publication["task_id"] == task_id
    assert publication["request_sha256"] == request_sha256
    assert publication["lock_operation_id"] == intent["lock_operation_id"]
    assert publication["publication_state"] == "published"
    result = publication["result"]
    assert isinstance(result, dict)
    assert result["task_id"] == task_id
    assert result["poster_id"] == intent["actor_id"]
    return cast("dict[str, Any]", result)
