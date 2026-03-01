"""Task Board mixin — task lifecycle, bidding, contracts."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


class _TaskBoardClient(Protocol):
    config: AgentConfig
    agent_id: str | None

    def _sign_jws(self, payload: dict[str, object]) -> str: ...

    def _auth_header(self, payload: dict[str, object]) -> dict[str, str]: ...

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]: ...


class TaskBoardMixin:
    """Methods for interacting with the Task Board service (port 8003)."""

    async def post_task(
        self: _TaskBoardClient,
        title: str,
        spec: str,
        reward: int,
        bidding_deadline_seconds: int,
        execution_deadline_seconds: int,
        review_deadline_seconds: int,
    ) -> dict[str, Any]:
        """Post a new task to the Task Board."""
        url = f"{self.config.task_board_url}/tasks"
        task_id = f"t-{uuid.uuid4()}"
        task_token = self._sign_jws(
            {
                "action": "create_task",
                "task_id": task_id,
                "poster_id": self.agent_id,
                "title": title,
                "spec": spec,
                "reward": reward,
                "bidding_deadline_seconds": bidding_deadline_seconds,
                "execution_deadline_seconds": execution_deadline_seconds,
                "review_deadline_seconds": review_deadline_seconds,
            }
        )
        escrow_token = self._sign_jws(
            {
                "action": "escrow_lock",
                "task_id": task_id,
                "amount": reward,
                "agent_id": self.agent_id,
            }
        )
        return await self._request(
            "POST",
            url,
            json={"task_token": task_token, "escrow_token": escrow_token},
        )

    async def list_tasks(
        self: _TaskBoardClient,
        status: str | None = None,
        poster_id: str | None = None,
        worker_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List tasks with optional filters."""
        url = f"{self.config.task_board_url}/tasks"
        params: dict[str, str] = {}
        if status is not None:
            params["status"] = status
        if poster_id is not None:
            params["poster_id"] = poster_id
        if worker_id is not None:
            params["worker_id"] = worker_id
        response = await self._request("GET", url, params=params)
        return cast("list[dict[str, Any]]", response["tasks"])

    async def get_task(self: _TaskBoardClient, task_id: str) -> dict[str, Any]:
        """Get task details."""
        url = f"{self.config.task_board_url}/tasks/{task_id}"
        return await self._request("GET", url)

    async def cancel_task(self: _TaskBoardClient, task_id: str) -> dict[str, Any]:
        """Cancel a task."""
        url = f"{self.config.task_board_url}/tasks/{task_id}/cancel"
        token = self._sign_jws(
            {
                "action": "cancel_task",
                "task_id": task_id,
                "poster_id": self.agent_id,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def submit_bid(self: _TaskBoardClient, task_id: str, amount: int) -> dict[str, Any]:
        """Submit a bid on a task."""
        url = f"{self.config.task_board_url}/tasks/{task_id}/bids"
        token = self._sign_jws(
            {
                "action": "submit_bid",
                "task_id": task_id,
                "bidder_id": self.agent_id,
                "amount": amount,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def list_bids(self: _TaskBoardClient, task_id: str) -> list[dict[str, Any]]:
        """List bids for a task."""
        url = f"{self.config.task_board_url}/tasks/{task_id}/bids"
        headers = self._auth_header(
            {
                "action": "list_bids",
                "task_id": task_id,
                "poster_id": self.agent_id,
            }
        )
        response = await self._request("GET", url, headers=headers)
        return cast("list[dict[str, Any]]", response["bids"])

    async def accept_bid(self: _TaskBoardClient, task_id: str, bid_id: str) -> dict[str, Any]:
        """Accept a bid on a task."""
        url = f"{self.config.task_board_url}/tasks/{task_id}/bids/{bid_id}/accept"
        token = self._sign_jws(
            {
                "action": "accept_bid",
                "task_id": task_id,
                "bid_id": bid_id,
                "poster_id": self.agent_id,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def upload_asset(
        self: _TaskBoardClient,
        task_id: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        """Upload a file asset for a task."""
        url = f"{self.config.task_board_url}/tasks/{task_id}/assets"
        headers = self._auth_header(
            {
                "action": "upload_asset",
                "task_id": task_id,
            }
        )
        return await self._request(
            "POST", url, headers=headers, files={"file": (filename, content)}
        )

    async def submit_deliverable(self: _TaskBoardClient, task_id: str) -> dict[str, Any]:
        """Submit deliverables for review."""
        url = f"{self.config.task_board_url}/tasks/{task_id}/submit"
        token = self._sign_jws(
            {
                "action": "submit_deliverable",
                "task_id": task_id,
                "worker_id": self.agent_id,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def approve_task(self: _TaskBoardClient, task_id: str) -> dict[str, Any]:
        """Approve a submitted task."""
        url = f"{self.config.task_board_url}/tasks/{task_id}/approve"
        token = self._sign_jws(
            {
                "action": "approve_task",
                "task_id": task_id,
                "poster_id": self.agent_id,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def dispute_task(self: _TaskBoardClient, task_id: str, reason: str) -> dict[str, Any]:
        """Dispute a submitted task."""
        url = f"{self.config.task_board_url}/tasks/{task_id}/dispute"
        token = self._sign_jws(
            {
                "action": "dispute_task",
                "task_id": task_id,
                "poster_id": self.agent_id,
                "reason": reason,
            }
        )
        return await self._request("POST", url, json={"token": token})
