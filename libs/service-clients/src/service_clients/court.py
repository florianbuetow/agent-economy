"""DB Gateway client for Court write operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from service_clients.base import BaseServiceClient


class CourtClient(BaseServiceClient):
    """Async HTTP client for DB Gateway court endpoints."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            service_name="db_gateway",
        )

    async def file_claim(
        self,
        claim_id: str,
        task_id: str,
        claimant_id: str,
        respondent_id: str,
        reason: str,
        status: str,
        filed_at: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "claim_id": claim_id,
            "task_id": task_id,
            "claimant_id": claimant_id,
            "respondent_id": respondent_id,
            "reason": reason,
            "status": status,
            "filed_at": filed_at,
            "event": {
                "event_source": "court",
                "event_type": "claim.filed",
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "task_id": task_id,
                "agent_id": claimant_id,
                "summary": f"Claim filed for task {task_id}",
                "payload": json.dumps({"claim_id": claim_id}),
            },
        }
        return await self._post_with_empty_fallback("/court/claims", payload)

    async def submit_rebuttal(
        self,
        rebuttal_id: str,
        claim_id: str,
        agent_id: str,
        content: str,
        submitted_at: str,
        claim_status_update: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rebuttal_id": rebuttal_id,
            "claim_id": claim_id,
            "agent_id": agent_id,
            "content": content,
            "submitted_at": submitted_at,
            "claim_status_update": claim_status_update,
            "event": {
                "event_source": "court",
                "event_type": "rebuttal.submitted",
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "agent_id": agent_id,
                "summary": f"Rebuttal submitted for claim {claim_id}",
                "payload": json.dumps({"claim_id": claim_id, "rebuttal_id": rebuttal_id}),
            },
        }
        return await self._post_with_empty_fallback("/court/rebuttals", payload)

    async def record_ruling(
        self,
        ruling_id: str,
        claim_id: str,
        task_id: str,
        worker_pct: int,
        summary: str,
        judge_votes: str,
        ruled_at: str,
        claim_status_update: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ruling_id": ruling_id,
            "claim_id": claim_id,
            "task_id": task_id,
            "worker_pct": worker_pct,
            "summary": summary,
            "judge_votes": judge_votes,
            "ruled_at": ruled_at,
            "claim_status_update": claim_status_update,
            "event": {
                "event_source": "court",
                "event_type": "ruling.delivered",
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "task_id": task_id,
                "summary": f"Ruling delivered for claim {claim_id}",
                "payload": json.dumps({"claim_id": claim_id, "ruling_id": ruling_id}),
            },
        }
        return await self._post_with_empty_fallback("/court/rulings", payload)

    async def _post_with_empty_fallback(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await self._post_raw(path, payload)
        if response.status_code in (200, 201):
            return self._response_dict(response)
        return {}
