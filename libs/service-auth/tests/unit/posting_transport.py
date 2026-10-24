"""Truthful pending publication responses for outbound signing tests."""

from __future__ import annotations

import json

import httpx

from service_auth.jws import parse_jws
from service_auth.task_postings import TaskPostingIntent


def pending_posting_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    payload = dict(parse_jws(body["task_token"]).payload)
    assert payload.pop("action") == "create_task"
    payload["actor_id"] = payload.pop("poster_id")
    payload["actor_kind"] = "agent"
    intent = TaskPostingIntent.from_json(payload)
    return httpx.Response(
        202,
        request=request,
        json={
            "protocol_version": 1,
            "operation_id": intent.operation_id,
            "task_id": intent.task_id,
            "request_sha256": intent.request_sha256,
            "publication_state": "awaiting_lock",
            "lock_operation_id": intent.lock_operation_id,
            "escrow_id": None,
            "admitted_at": "2026-10-22T10:00:00.000000Z",
            "published_at": None,
            "result": None,
            "error": None,
        },
    )
