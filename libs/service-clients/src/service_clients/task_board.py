"""DB Gateway client for Task Board write operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from service_clients.base import BaseServiceClient


class TaskBoardClient(BaseServiceClient):
    """Async HTTP client for DB Gateway board endpoints."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            service_name="db_gateway",
        )

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _build_event(
        self,
        *,
        event_type: str,
        summary: str,
        payload: dict[str, Any],
        task_id: str,
        agent_id: str | None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "event_source": "board",
            "event_type": event_type,
            "timestamp": self._now(),
            "task_id": task_id,
            "summary": summary,
            "payload": json.dumps(payload),
        }
        if agent_id is not None:
            event["agent_id"] = agent_id
        return event

    async def create_task(
        self,
        task_id: str,
        poster_id: str,
        title: str,
        spec: str,
        reward: int,
        status: str,
        bidding_deadline_seconds: int,
        deadline_seconds: int,
        review_deadline_seconds: int,
        bidding_deadline: str,
        escrow_id: str,
        created_at: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": task_id,
            "poster_id": poster_id,
            "title": title,
            "spec": spec,
            "reward": reward,
            "status": status,
            "bidding_deadline_seconds": bidding_deadline_seconds,
            "deadline_seconds": deadline_seconds,
            "review_deadline_seconds": review_deadline_seconds,
            "bidding_deadline": bidding_deadline,
            "escrow_id": escrow_id,
            "created_at": created_at,
            "event": self._build_event(
                event_type="task.created",
                summary=f"Task created: {title}",
                payload={
                    "title": title,
                    "reward": reward,
                    "bidding_deadline": bidding_deadline,
                },
                task_id=task_id,
                agent_id=poster_id,
            ),
        }
        return await self._post("/board/tasks", payload, expected_status=201)

    async def submit_bid(
        self,
        bid_id: str,
        task_id: str,
        bidder_id: str,
        proposal: str,
        submitted_at: str,
        title: str,
        bid_count: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "bid_id": bid_id,
            "task_id": task_id,
            "bidder_id": bidder_id,
            "proposal": proposal,
            "submitted_at": submitted_at,
            "event": self._build_event(
                event_type="bid.submitted",
                summary=f"Bid submitted for {title}",
                payload={"bid_id": bid_id, "title": title, "bid_count": bid_count},
                task_id=task_id,
                agent_id=bidder_id,
            ),
        }
        return await self._post("/board/bids", payload, expected_status=201)

    async def update_task_status(
        self,
        task_id: str,
        updates: dict[str, Any],
        event_type: str,
        summary: str,
        event_payload: dict[str, Any],
        agent_id: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "updates": updates,
            "event": self._build_event(
                event_type=event_type,
                summary=summary,
                payload=event_payload,
                task_id=task_id,
                agent_id=agent_id,
            ),
        }
        return await self._post(f"/board/tasks/{task_id}/status", payload, expected_status=200)

    async def record_asset(
        self,
        asset_id: str,
        task_id: str,
        uploader_id: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        storage_path: str,
        uploaded_at: str,
        title: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "asset_id": asset_id,
            "task_id": task_id,
            "uploader_id": uploader_id,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "storage_path": storage_path,
            "uploaded_at": uploaded_at,
            "event": self._build_event(
                event_type="asset.uploaded",
                summary=f"Asset uploaded for {title}: {filename}",
                payload={
                    "title": title,
                    "filename": filename,
                    "size_bytes": size_bytes,
                },
                task_id=task_id,
                agent_id=uploader_id,
            ),
        }
        return await self._post("/board/assets", payload, expected_status=201)
