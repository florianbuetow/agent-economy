"""DB Gateway-backed feedback storage."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from reputation_service.services.exceptions import DuplicateFeedbackError
from reputation_service.types import FeedbackRecord


class FeedbackDbClient:
    """Feedback storage backed by the DB Gateway HTTP API."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

    def _role_for_category(self, category: str) -> str:
        if category == "spec_quality":
            return "worker"
        return "poster"

    def _dict_to_record(self, data: dict[str, Any]) -> FeedbackRecord:
        return FeedbackRecord(
            feedback_id=str(data["feedback_id"]),
            task_id=str(data["task_id"]),
            from_agent_id=str(data["from_agent_id"]),
            to_agent_id=str(data["to_agent_id"]),
            category=str(data["category"]),
            rating=str(data["rating"]),
            comment=str(data["comment"]) if data.get("comment") is not None else None,
            submitted_at=str(data["submitted_at"]),
            visible=bool(data["visible"]),
        )

    def _find_reverse_feedback_id(
        self,
        task_id: str,
        from_agent_id: str,
        to_agent_id: str,
    ) -> str | None:
        existing = self.get_by_task(task_id)
        for record in existing:
            if record.from_agent_id == to_agent_id and record.to_agent_id == from_agent_id:
                return record.feedback_id
        return None

    def insert_feedback(
        self,
        task_id: str,
        from_agent_id: str,
        to_agent_id: str,
        category: str,
        rating: str,
        comment: str | None,
        *,
        force_visible: bool,
    ) -> FeedbackRecord:
        feedback_id = f"fb-{uuid.uuid4()}"
        submitted_at = datetime.now(UTC).isoformat()

        reverse_feedback_id = self._find_reverse_feedback_id(
            task_id=task_id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
        )
        reveal_reverse = force_visible or reverse_feedback_id is not None

        payload: dict[str, Any] = {
            "feedback_id": feedback_id,
            "task_id": task_id,
            "from_agent_id": from_agent_id,
            "to_agent_id": to_agent_id,
            "role": self._role_for_category(category),
            "category": category,
            "rating": rating,
            "comment": comment,
            "submitted_at": submitted_at,
            "reveal_reverse": reveal_reverse,
            "reverse_feedback_id": reverse_feedback_id,
            "event": {
                "event_source": "reputation",
                "event_type": "feedback.submitted",
                "timestamp": submitted_at,
                "task_id": task_id,
                "agent_id": from_agent_id,
                "summary": f"Feedback submitted by {from_agent_id} for {to_agent_id}",
                "payload": json.dumps(
                    {
                        "from_agent_id": from_agent_id,
                        "to_agent_id": to_agent_id,
                        "category": category,
                        "rating": rating,
                    }
                ),
            },
        }

        response = self._client.post("/reputation/feedback", json=payload)
        if response.status_code == 409:
            raise DuplicateFeedbackError(
                f"Feedback already exists for ({task_id}, {from_agent_id}, {to_agent_id})"
            )
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        return FeedbackRecord(
            feedback_id=feedback_id,
            task_id=task_id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            category=category,
            rating=rating,
            comment=comment,
            submitted_at=submitted_at,
            visible=reveal_reverse,
        )

    def get_by_id(self, feedback_id: str) -> FeedbackRecord | None:
        response = self._client.get(f"/reputation/feedback/{feedback_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return self._dict_to_record(self._json(response))

    def get_by_task(self, task_id: str) -> list[FeedbackRecord]:
        response = self._client.get("/reputation/feedback", params={"task_id": task_id})
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data = self._json(response)
        items_raw = data.get("feedback", [])
        items = items_raw if isinstance(items_raw, list) else []
        return [self._dict_to_record(item) for item in items if isinstance(item, dict)]

    def get_by_agent(self, agent_id: str) -> list[FeedbackRecord]:
        response = self._client.get("/reputation/feedback", params={"agent_id": agent_id})
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data = self._json(response)
        items_raw = data.get("feedback", [])
        items = items_raw if isinstance(items_raw, list) else []
        return [self._dict_to_record(item) for item in items if isinstance(item, dict)]

    def count(self) -> int:
        response = self._client.get("/reputation/feedback/count")
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return int(self._json(response)["count"])

    def close(self) -> None:
        self._client.close()


__all__ = ["FeedbackDbClient"]
