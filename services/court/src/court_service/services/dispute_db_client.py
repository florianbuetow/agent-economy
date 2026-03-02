"""DB Gateway-backed dispute storage."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from court_service.services.errors import DuplicateDisputeError


class DisputeDbClient:
    """Dispute storage backed by the DB Gateway HTTP API."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _new_dispute_id(self) -> str:
        return f"disp-{uuid.uuid4()}"

    def _new_rebuttal_id(self) -> str:
        return f"reb-{uuid.uuid4()}"

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

    def _get_claim(self, dispute_id: str) -> dict[str, Any] | None:
        response = self._client.get(f"/court/claims/{dispute_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return self._json(response)

    def _get_rebuttal(self, dispute_id: str) -> dict[str, Any] | None:
        response = self._client.get(f"/court/claims/{dispute_id}/rebuttal")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return self._json(response)

    def _get_ruling(self, dispute_id: str) -> dict[str, Any] | None:
        response = self._client.get(f"/court/rulings/{dispute_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return self._json(response)

    def _get_escrow_id(self, task_id: str) -> str:
        """Look up escrow_id from the board task record."""
        response = self._client.get(f"/board/tasks/{task_id}")
        if response.status_code != 200:
            return ""
        data = self._json(response)
        return str(data.get("escrow_id", ""))

    def _parse_votes(self, dispute_id: str, judge_votes_raw: object) -> list[dict[str, Any]]:
        if isinstance(judge_votes_raw, str):
            try:
                parsed: object = json.loads(judge_votes_raw)
            except json.JSONDecodeError:
                return []
        else:
            parsed = judge_votes_raw

        if not isinstance(parsed, list):
            return []

        votes: list[dict[str, Any]] = []
        for index, vote in enumerate(parsed):
            if not isinstance(vote, dict):
                continue
            judge_id = str(vote.get("judge_id", ""))
            worker_pct_raw = vote.get("worker_pct")
            worker_pct = int(worker_pct_raw) if isinstance(worker_pct_raw, int) else 50
            reasoning = str(vote.get("reasoning", ""))
            voted_at = str(vote.get("voted_at", self._now_iso()))
            votes.append(
                {
                    "vote_id": f"vote-{dispute_id}-{index}",
                    "dispute_id": dispute_id,
                    "judge_id": judge_id,
                    "worker_pct": worker_pct,
                    "reasoning": reasoning,
                    "voted_at": voted_at,
                }
            )
        return votes

    def _compose_dispute(
        self,
        dispute_id: str,
        claim: dict[str, Any],
        rebuttal: dict[str, Any] | None,
        ruling: dict[str, Any] | None,
    ) -> dict[str, Any]:
        judge_votes = ruling.get("judge_votes") if ruling is not None else []
        votes = self._parse_votes(dispute_id, judge_votes)
        return {
            "dispute_id": dispute_id,
            "task_id": str(claim["task_id"]),
            "claimant_id": str(claim["claimant_id"]),
            "respondent_id": str(claim["respondent_id"]),
            "claim": str(claim["reason"]),
            "rebuttal": str(rebuttal["content"]) if rebuttal is not None else None,
            "status": str(claim["status"]),
            "rebuttal_deadline": str(claim.get("rebuttal_deadline", "")),
            "worker_pct": int(ruling["worker_pct"]) if ruling is not None else None,
            "ruling_summary": str(ruling["summary"]) if ruling is not None else None,
            "escrow_id": self._get_escrow_id(str(claim["task_id"])),
            "filed_at": str(claim["filed_at"]),
            "rebutted_at": str(rebuttal["submitted_at"]) if rebuttal is not None else None,
            "ruled_at": str(ruling["ruled_at"]) if ruling is not None else None,
            "votes": votes,
        }

    def get_dispute_row(self, dispute_id: str) -> Any:
        return self.get_dispute(dispute_id)

    def get_votes(self, dispute_id: str) -> list[dict[str, Any]]:
        ruling = self._get_ruling(dispute_id)
        if ruling is None:
            return []
        return self._parse_votes(dispute_id, ruling.get("judge_votes"))

    def insert_dispute(
        self,
        task_id: str,
        claimant_id: str,
        respondent_id: str,
        claim: str,
        escrow_id: str,
        rebuttal_deadline: str,
    ) -> dict[str, Any]:
        dispute_id = self._new_dispute_id()
        filed_at = self._now_iso()
        payload: dict[str, Any] = {
            "claim_id": dispute_id,
            "task_id": task_id,
            "claimant_id": claimant_id,
            "respondent_id": respondent_id,
            "reason": claim,
            "status": "rebuttal_pending",
            "rebuttal_deadline": rebuttal_deadline,
            "filed_at": filed_at,
            "event": {
                "event_source": "court",
                "event_type": "claim.filed",
                "timestamp": filed_at,
                "task_id": task_id,
                "agent_id": claimant_id,
                "summary": f"Claim filed for task {task_id}",
                "payload": json.dumps({"claim_id": dispute_id, "escrow_id": escrow_id}),
            },
        }

        response = self._client.post("/court/claims", json=payload)
        if response.status_code == 409:
            raise DuplicateDisputeError(f"A dispute already exists for task_id={task_id}")
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        created = self.get_dispute(dispute_id)
        if created is None:
            msg = "Failed to load newly created dispute"
            raise RuntimeError(msg)
        return created

    def get_dispute(self, dispute_id: str) -> dict[str, Any] | None:
        claim = self._get_claim(dispute_id)
        if claim is None:
            return None
        rebuttal = self._get_rebuttal(dispute_id)
        ruling = self._get_ruling(dispute_id)
        return self._compose_dispute(dispute_id, claim, rebuttal, ruling)

    def update_rebuttal(self, dispute_id: str, rebuttal: str) -> None:
        claim = self._get_claim(dispute_id)
        if claim is None:
            return

        submitted_at = self._now_iso()
        payload: dict[str, Any] = {
            "rebuttal_id": self._new_rebuttal_id(),
            "claim_id": dispute_id,
            "agent_id": str(claim["respondent_id"]),
            "content": rebuttal,
            "submitted_at": submitted_at,
            "event": {
                "event_source": "court",
                "event_type": "rebuttal.submitted",
                "timestamp": submitted_at,
                "agent_id": str(claim["respondent_id"]),
                "summary": f"Rebuttal submitted for claim {dispute_id}",
                "payload": json.dumps({"claim_id": dispute_id}),
            },
        }

        response = self._client.post("/court/rebuttals", json=payload)
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

    def set_status(self, dispute_id: str, status: str) -> None:
        response = self._client.post(
            f"/court/claims/{dispute_id}/status",
            json={"status": status},
        )
        if response.status_code == 404:
            return
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

    def revert_to_rebuttal_pending(self, dispute_id: str) -> None:
        self.set_status(dispute_id, "rebuttal_pending")
        self._delete_ruling(dispute_id)

    def _delete_ruling(self, dispute_id: str) -> None:
        """Delete the ruling record for a dispute (removes votes too)."""
        response = self._client.delete(f"/court/rulings/{dispute_id}")
        if response.status_code not in (200, 404):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

    def persist_ruling(
        self,
        dispute_id: str,
        worker_pct: int,
        ruling_summary: str,
        votes: list[dict[str, Any]],
    ) -> None:
        dispute = self.get_dispute(dispute_id)
        if dispute is None:
            return

        ruled_at = self._now_iso()
        payload: dict[str, Any] = {
            "ruling_id": dispute_id,
            "claim_id": dispute_id,
            "task_id": dispute["task_id"],
            "worker_pct": worker_pct,
            "summary": ruling_summary,
            "judge_votes": json.dumps(votes),
            "ruled_at": ruled_at,
            "claim_status_update": "ruled",
            "event": {
                "event_source": "court",
                "event_type": "ruling.delivered",
                "timestamp": ruled_at,
                "task_id": dispute["task_id"],
                "summary": f"Ruling delivered for claim {dispute_id}",
                "payload": json.dumps({"claim_id": dispute_id, "worker_pct": worker_pct}),
            },
        }

        response = self._client.post("/court/rulings", json=payload)
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

    def list_disputes(self, task_id: str | None, status: str | None) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if status is not None:
            params["status"] = status

        response = self._client.get("/court/claims", params=params)
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        claims_raw = self._json(response).get("claims", [])
        claims = claims_raw if isinstance(claims_raw, list) else []
        disputes: list[dict[str, Any]] = []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            claim_task_id = str(claim.get("task_id", ""))
            if task_id is not None and claim_task_id != task_id:
                continue
            dispute_id = str(claim.get("claim_id", ""))
            claim_status = str(claim.get("status", ""))
            ruling = self._get_ruling(dispute_id) if claim_status == "ruled" else None
            disputes.append(
                {
                    "dispute_id": dispute_id,
                    "task_id": claim_task_id,
                    "claimant_id": str(claim.get("claimant_id", "")),
                    "respondent_id": str(claim.get("respondent_id", "")),
                    "status": claim_status,
                    "worker_pct": int(ruling["worker_pct"]) if ruling is not None else None,
                    "filed_at": str(claim.get("filed_at", "")),
                    "ruled_at": str(ruling["ruled_at"]) if ruling is not None else None,
                }
            )
        return disputes

    def count_disputes(self) -> int:
        response = self._client.get("/court/claims/count")
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return int(self._json(response)["count"])

    def count_active(self) -> int:
        response = self._client.get("/court/claims/count-active")
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return int(self._json(response)["count"])

    def close(self) -> None:
        self._client.close()


__all__ = ["DisputeDbClient"]
