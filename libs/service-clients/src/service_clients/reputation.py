"""DB Gateway client for Reputation write operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from service_clients.base import BaseServiceClient


class ReputationClient(BaseServiceClient):
    """Async HTTP client for DB Gateway reputation endpoints."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            service_name="db_gateway",
        )

    async def submit_feedback(
        self,
        feedback_id: str,
        task_id: str,
        from_agent_id: str,
        to_agent_id: str,
        role: str,
        category: str,
        rating: str,
        submitted_at: str,
        comment: str | None,
        reveal_reverse: bool,
        reverse_feedback_id: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "feedback_id": feedback_id,
            "task_id": task_id,
            "from_agent_id": from_agent_id,
            "to_agent_id": to_agent_id,
            "role": role,
            "category": category,
            "rating": rating,
            "submitted_at": submitted_at,
            "event": {
                "event_source": "reputation",
                "event_type": "feedback.revealed",
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "task_id": task_id,
                "agent_id": from_agent_id,
                "summary": f"Feedback submitted for task {task_id}",
                "payload": json.dumps(
                    {
                        "feedback_id": feedback_id,
                        "category": category,
                        "rating": rating,
                    }
                ),
            },
        }
        if comment is not None:
            payload["comment"] = comment
        if reveal_reverse:
            payload["reveal_reverse"] = True
        if reverse_feedback_id is not None:
            payload["reverse_feedback_id"] = reverse_feedback_id

        response = await self._post_raw("/reputation/feedback", payload)
        if response.status_code in (200, 201):
            return self._response_dict(response)
        return {}
