"""Public event projection for stored and newly produced economy events."""

from __future__ import annotations

import json
from typing import Any

from ui_service.taxonomy import VALID_TASK_STATUSES, TaskStatus

BID_SUBMITTED = "bid.submitted"
FEEDBACK_SUBMITTED = "feedback.submitted"
PUBLIC_BID_STATUSES: tuple[str, ...] = tuple(
    sorted(status for status in VALID_TASK_STATUSES if status != TaskStatus.OPEN)
)


def bid_details_are_public(task_status: object) -> bool:
    """Return whether a known task status has left the sealed open phase."""
    return isinstance(task_status, str) and task_status in PUBLIC_BID_STATUSES


def project_public_event(row: Any) -> dict[str, Any]:
    """Project one stored event into its actor-independent public form."""
    event_type = str(row["event_type"])
    task_status = row["task_status"]
    agent_id = row["agent_id"]
    summary = str(row["summary"])
    payload = _object_payload(row["payload"])

    if event_type == FEEDBACK_SUBMITTED:
        agent_id = None
        summary = "Feedback submitted"
        payload = {}
    elif event_type == BID_SUBMITTED and not bid_details_are_public(task_status):
        agent_id = None
        summary = "Bid submitted"
        payload = {}

    return {
        "event_id": row["event_id"],
        "event_source": row["event_source"],
        "event_type": event_type,
        "timestamp": row["timestamp"],
        "task_id": row["task_id"],
        "agent_id": agent_id,
        "summary": summary,
        "payload": payload,
    }


def _object_payload(value: object) -> dict[str, object]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
