"""HTTP client for Task Board interactions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from court_service.services.platform_signer import PlatformSigner


class TaskBoardClient:
    """Async client for Task Board task lookups and ruling notifications."""

    def __init__(
        self,
        base_url: str,
        signer: PlatformSigner,
        timeout_seconds: int,
    ) -> None:
        self._signer = signer
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=float(timeout_seconds),
        )

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """Fetch task details used for filing/ruling."""
        try:
            response = await self._client.get(f"/tasks/{task_id}")
        except httpx.HTTPError as exc:
            raise ServiceError(
                "TASK_BOARD_UNAVAILABLE",
                "Cannot reach Task Board service",
                502,
                {},
            ) from exc

        if response.status_code == 200:
            body = response.json()
            if not isinstance(body, dict):
                raise ServiceError(
                    "TASK_BOARD_UNAVAILABLE",
                    "Task Board returned malformed task response",
                    502,
                    {},
                )
            return body

        if response.status_code == 404:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        raise ServiceError(
            "TASK_BOARD_UNAVAILABLE",
            f"Task Board returned unexpected status {response.status_code}",
            502,
            {},
        )

    async def record_ruling(self, task_id: str, ruling_payload: dict[str, Any]) -> None:
        """Notify Task Board that a dispute ruling was issued."""
        token = self._signer.sign(ruling_payload)
        try:
            response = await self._client.post(
                f"/tasks/{task_id}/ruling",
                json={"token": token},
            )
        except httpx.HTTPError as exc:
            raise ServiceError(
                "TASK_BOARD_UNAVAILABLE",
                "Cannot reach Task Board service",
                502,
                {},
            ) from exc

        if 200 <= response.status_code < 300:
            return

        raise ServiceError(
            "TASK_BOARD_UNAVAILABLE",
            f"Task Board returned unexpected status {response.status_code}",
            502,
            {},
        )

    async def close(self) -> None:
        """Close the underlying async client."""
        await self._client.aclose()
