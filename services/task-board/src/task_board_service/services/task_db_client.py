"""DB Gateway-backed task storage."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError
from service_commons.exceptions import ServiceError

from task_board_service.services.errors import DuplicateBidError, DuplicateTaskError
from task_board_service.task_contract_projection import (
    GatewayBid,
    TaskContractProjection,
    validate_task_projection,
)
from task_board_service.task_operation_contract import (
    GatewayError,
    invalid_gateway_response,
    validate_command,
    validate_envelope,
    validate_operation_id,
)

if TYPE_CHECKING:
    from service_auth.gateway import GatewayRequestSigner

# Re-exported so callers outside the HTTP-client boundary (e.g. the deadline
# evaluator's platform-signed ruling trigger, GAP-A1) can catch transport errors
# from other services without importing httpx directly; only *DbClient modules
# are permitted to do that (tests/architecture/test_db_client_isolation.py).
PlatformHttpError = httpx.HTTPError

_LIFECYCLE_EVENTS_BY_STATUS: dict[str, str] = {
    "accepted": "task.accepted",
    "submitted": "task.submitted",
    "approved": "task.approved",
    "disputed": "task.disputed",
    "ruled": "task.ruled",
    "cancelled": "task.cancelled",
    "expired": "task.expired",
}


class TaskDbClient:
    """Task storage backed by the DB Gateway HTTP API."""

    _TASK_COLUMNS: tuple[str, ...] = (
        "task_id",
        "poster_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "deadline_seconds",
        "review_deadline_seconds",
        "status",
        "escrow_id",
        "bid_count",
        "worker_id",
        "accepted_bid_id",
        "created_at",
        "accepted_at",
        "submitted_at",
        "approved_at",
        "cancelled_at",
        "disputed_at",
        "dispute_reason",
        "dispute_id",
        "rebuttal_deadline",
        "rebuttal_submitted_at",
        "ruling_id",
        "ruled_at",
        "worker_pct",
        "ruling_summary",
        "expired_at",
        "escrow_pending",
        *TaskContractProjection.model_fields,
    )

    def __init__(
        self,
        base_url: str,
        timeout_seconds: int,
        signer: GatewayRequestSigner,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )
        self._signer = signer

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        request = self._client.build_request(method, path, json=json, params=params)
        signed_request = self._signer.sign_request(
            request,
            action=f"gateway.board.{'read' if method == 'GET' else 'write'}",
        )
        return self._client.send(signed_request)

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

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

    def _parse_iso(self, value: str) -> datetime:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value).astimezone(UTC)

    def _normalize_task(self, data: dict[str, Any]) -> dict[str, Any]:
        return {column: data.get(column) for column in self._TASK_COLUMNS}

    def _strict_json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
        except (ValueError, UnicodeError) as exc:
            raise invalid_gateway_response() from exc
        return data

    def _raise_gateway_error(self, response: httpx.Response) -> None:
        if not 400 <= response.status_code <= 599:
            raise invalid_gateway_response()
        try:
            error = GatewayError.model_validate(self._strict_json(response))
        except ValidationError as exc:
            raise invalid_gateway_response() from exc
        raise ServiceError(error.error, error.message, response.status_code, error.details)

    def claim_task_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Claim or replay one exact native command without replacing its identity."""
        command = validate_command(payload)
        response = self._request(
            "POST", "/board/task-operations", json=command.model_dump(), params=None
        )
        if response.status_code not in {200, 202}:
            self._raise_gateway_error(response)
        return validate_envelope(
            self._strict_json(response),
            status_code=response.status_code,
            operation_id=command.operation_id,
            command=command,
        )

    def finalize_task_operation(self, operation_id: str) -> dict[str, Any]:
        """Ask Gateway to validate its persisted evidence; never forward a success body."""
        validated_id = validate_operation_id(operation_id)
        response = self._request(
            "POST", f"/board/task-operations/{validated_id}/finalize", json={}, params=None
        )
        if response.status_code not in {200, 202}:
            self._raise_gateway_error(response)
        return validate_envelope(
            self._strict_json(response),
            status_code=response.status_code,
            operation_id=validated_id,
            command=None,
        )

    def _event_type_for_updates(self, updates: dict[str, Any]) -> str:
        status = updates.get("status")
        if status == "approved" and updates.get("escrow_pending") == 1:
            return "task.auto_approved"
        if isinstance(status, str):
            return _LIFECYCLE_EVENTS_BY_STATUS.get(status, "task.updated")
        return "task.updated"

    def insert_task(self, task_data: dict[str, Any]) -> None:
        created_at = str(task_data["created_at"])
        bidding_deadline_seconds = int(task_data["bidding_deadline_seconds"])
        bidding_deadline_dt = self._parse_iso(created_at) + timedelta(
            seconds=bidding_deadline_seconds
        )
        bidding_deadline = bidding_deadline_dt.isoformat(timespec="auto").replace("+00:00", "Z")

        payload: dict[str, Any] = {
            "task_id": task_data["task_id"],
            "poster_id": task_data["poster_id"],
            "title": task_data["title"],
            "spec": task_data["spec"],
            "reward": task_data["reward"],
            "status": task_data["status"],
            "bidding_deadline_seconds": task_data["bidding_deadline_seconds"],
            "acceptance_window_seconds": task_data["acceptance_window_seconds"],
            "deadline_seconds": task_data["deadline_seconds"],
            "review_deadline_seconds": task_data["review_deadline_seconds"],
            "bidding_deadline": bidding_deadline,
            "bid_count": task_data["bid_count"],
            "escrow_pending": task_data.get("escrow_pending", 0),
            "escrow_id": task_data["escrow_id"],
            "created_at": created_at,
            "event": self._build_event(
                event_type="task.created",
                summary=f"Task created: {task_data['title']}",
                payload={
                    "title": task_data["title"],
                    "reward": task_data["reward"],
                    "bidding_deadline": bidding_deadline,
                },
                task_id=str(task_data["task_id"]),
                agent_id=str(task_data["poster_id"]),
            ),
        }

        response = self._request("POST", "/board/tasks", json=payload, params=None)
        if response.status_code == 409:
            raise DuplicateTaskError(f"A task with task_id={task_data['task_id']} already exists")
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        response = self._request("GET", f"/board/tasks/{task_id}", json=None, params=None)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            self._raise_gateway_error(response)
        data = self._strict_json(response)
        validate_task_projection(data)
        if data.get("task_id") != task_id:
            raise invalid_gateway_response()
        return self._normalize_task(data)

    def update_task(
        self,
        task_id: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None,
    ) -> int:
        constraints: dict[str, Any] | None = None
        if expected_status is not None:
            constraints = {"status": expected_status}

        payload: dict[str, Any] = {
            "updates": updates,
            "constraints": constraints,
            "event": self._build_event(
                event_type=self._event_type_for_updates(updates),
                summary=f"Task updated: {task_id}",
                payload={"updates": updates},
                task_id=task_id,
                agent_id=None,
            ),
        }

        response = self._request(
            "POST", f"/board/tasks/{task_id}/status", json=payload, params=None
        )
        if response.status_code in {404, 409}:
            return 0
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return 1

    def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
        limit: int | None,
        offset: int | None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int | float | bool | None] = {}
        if status is not None:
            params["status"] = status
        if poster_id is not None:
            params["poster_id"] = poster_id
        if worker_id is not None:
            params["worker_id"] = worker_id
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset

        response = self._request("GET", "/board/tasks", json=None, params=params)
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        items = self._strict_json(response).get("tasks")
        if not isinstance(items, list):
            raise invalid_gateway_response()
        tasks: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise invalid_gateway_response()
            validate_task_projection(item)
            tasks.append(self._normalize_task(item))
        return tasks

    def count_tasks(self) -> int:
        response = self._request("GET", "/board/tasks/count", json=None, params=None)
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return int(self._json(response)["count"])

    def count_tasks_by_status(self) -> dict[str, int]:
        response = self._request("GET", "/board/tasks/count-by-status", json=None, params=None)
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        data = self._json(response)
        return {str(key): int(value) for key, value in data.items()}

    def insert_bid(self, bid_data: dict[str, Any]) -> None:
        amount = int(bid_data["amount"])
        payload: dict[str, Any] = {
            "bid_id": bid_data["bid_id"],
            "task_id": bid_data["task_id"],
            "bidder_id": bid_data["bidder_id"],
            "proposal": str(bid_data.get("proposal", amount)),
            "amount": amount,
            "submitted_at": bid_data["submitted_at"],
            "constraints": {"status": "open"},
            "event": self._build_event(
                event_type="bid.submitted",
                summary="Bid submitted",
                payload={},
                task_id=str(bid_data["task_id"]),
                agent_id=str(bid_data["bidder_id"]),
            ),
        }

        response = self._request("POST", "/board/bids", json=payload, params=None)
        if response.status_code == 409:
            raise DuplicateBidError("This agent already bid on this task")
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

    def get_bid(self, bid_id: str, task_id: str) -> dict[str, Any] | None:
        response = self._request(
            "GET", f"/board/bids/{bid_id}", json=None, params={"task_id": task_id}
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            self._raise_gateway_error(response)
        try:
            bid = GatewayBid.model_validate(self._strict_json(response))
        except ValidationError as exc:
            raise invalid_gateway_response() from exc
        if bid.bid_id != bid_id or bid.task_id != task_id:
            raise invalid_gateway_response()
        return bid.model_dump(exclude={"proposal"})

    def get_bids_for_task(self, task_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/board/tasks/{task_id}/bids", json=None, params=None)
        if response.status_code != 200:
            self._raise_gateway_error(response)

        data = self._strict_json(response)
        if set(data) != {"bids"} or not isinstance(data["bids"], list):
            raise invalid_gateway_response()
        bids: list[dict[str, Any]] = []
        for item in data["bids"]:
            try:
                bid = GatewayBid.model_validate(item)
            except ValidationError as exc:
                raise invalid_gateway_response() from exc
            if bid.task_id != task_id:
                raise invalid_gateway_response()
            bids.append(bid.model_dump(exclude={"proposal"}))
        return bids

    def insert_asset(self, asset_data: dict[str, Any]) -> None:
        task_id = str(asset_data["task_id"])
        asset_id = str(asset_data["asset_id"])
        filename = str(asset_data["filename"])
        storage_path = str(asset_data.get("storage_path", f"{task_id}/{asset_id}/{filename}"))
        payload: dict[str, Any] = {
            "asset_id": asset_id,
            "task_id": task_id,
            "uploader_id": asset_data["uploader_id"],
            "filename": filename,
            "content_type": asset_data["content_type"],
            "size_bytes": asset_data["size_bytes"],
            "storage_path": storage_path,
            "content_hash": asset_data.get("content_hash"),
            "uploaded_at": asset_data["uploaded_at"],
            "event": self._build_event(
                event_type="asset.uploaded",
                summary=f"Asset uploaded for task {task_id}: {filename}",
                payload={"filename": filename, "size_bytes": asset_data["size_bytes"]},
                task_id=task_id,
                agent_id=str(asset_data["uploader_id"]),
            ),
        }

        response = self._request("POST", "/board/assets", json=payload, params=None)
        if response.status_code == 409:
            msg = "Asset with this asset_id already exists"
            raise RuntimeError(msg)
        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

    def get_asset(self, asset_id: str, task_id: str) -> dict[str, Any] | None:
        response = self._request(
            "GET", f"/board/assets/{asset_id}", json=None, params={"task_id": task_id}
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data = self._json(response)
        return {
            "asset_id": str(data["asset_id"]),
            "task_id": str(data["task_id"]),
            "uploader_id": str(data["uploader_id"]),
            "filename": str(data["filename"]),
            "content_type": str(data["content_type"]),
            "size_bytes": int(data["size_bytes"]),
            "content_hash": (
                str(data["content_hash"]) if data.get("content_hash") is not None else ""
            ),
            "uploaded_at": str(data["uploaded_at"]),
        }

    def get_assets_for_task(self, task_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/board/tasks/{task_id}/assets", json=None, params=None)
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        items = self._json(response).get("assets", [])
        if not isinstance(items, list):
            return []
        return [
            {
                "asset_id": str(item["asset_id"]),
                "task_id": str(item["task_id"]),
                "uploader_id": str(item["uploader_id"]),
                "filename": str(item["filename"]),
                "content_type": str(item["content_type"]),
                "size_bytes": int(item["size_bytes"]),
                "content_hash": str(item["content_hash"])
                if item.get("content_hash") is not None
                else "",
                "uploaded_at": str(item["uploaded_at"]),
            }
            for item in items
            if isinstance(item, dict)
        ]

    def count_assets(self, task_id: str) -> int:
        response = self._request(
            "GET", f"/board/tasks/{task_id}/assets/count", json=None, params=None
        )
        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)
        return int(self._json(response)["count"])

    def close(self) -> None:
        self._client.close()


__all__ = ["TaskDbClient"]
