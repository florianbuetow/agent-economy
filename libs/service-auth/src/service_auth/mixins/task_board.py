"""Task Board mixin — task lifecycle, bidding, contracts."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import quote

from service_auth.errors import raise_for_api_error
from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import SQLITE_MAX_INTEGER, validate_operation_id
from service_auth.task_board_responses import (
    TaskBoardCommandResponse,
    parse_task_board_command_response,
)
from service_auth.task_postings import (
    TaskPostingIntent,
    TaskPostingResponse,
    parse_task_posting_status_response,
    parse_task_posting_submit_response,
)

if TYPE_CHECKING:
    import httpx

    from service_auth.config import AgentConfig


IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"


def _operation_identity(operation_id: str, expected_lifecycle_version: int) -> tuple[str, int]:
    validated_id = validate_operation_id(operation_id)
    if (
        type(expected_lifecycle_version) is not int
        or not 0 <= expected_lifecycle_version <= SQLITE_MAX_INTEGER
    ):
        raise ValueError("expected_lifecycle_version must be a nonnegative SQLite-safe integer")
    return validated_id, expected_lifecycle_version


class _TaskBoardClient(Protocol):
    config: AgentConfig
    agent_id: str | None

    def _sign_jws(self, payload: dict[str, object], *, audience: str, resource: str) -> str: ...

    def _auth_header(
        self, payload: dict[str, object], *, audience: str, resource: str
    ) -> dict[str, str]: ...

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]: ...

    async def _request_raw(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...


async def _send_task_board_command(
    client: _TaskBoardClient,
    *,
    url: str,
    token: str,
    allowed_status_codes: frozenset[int],
    effect_kind: str | frozenset[str] | None,
) -> TaskBoardCommandResponse:
    response = await client._request_raw("POST", url, json={"token": token})
    return parse_task_board_command_response(
        response, allowed_status_codes=allowed_status_codes, effect_kind=effect_kind
    )


class TaskBoardMixin:
    """Methods for interacting with the Task Board service (port 8003)."""

    def prepare_task_posting(
        self: _TaskBoardClient,
        title: str,
        spec: str,
        reward: int,
        bidding_deadline_seconds: int,
        acceptance_window_seconds: int,
        execution_deadline_seconds: int,
        review_deadline_seconds: int,
    ) -> TaskPostingIntent:
        """Allocate and validate one immutable task-publication intent."""
        poster_id = self.agent_id
        if poster_id is None:
            raise RuntimeError("Agent must be registered before preparing a task posting")
        task_id = f"t-{uuid.uuid4()}"
        lock_operation_id = operation_id_for("task-lock", (task_id, poster_id))
        operation_id = f"op-{uuid.uuid4()}"
        while operation_id == lock_operation_id:
            operation_id = f"op-{uuid.uuid4()}"
        return TaskPostingIntent.from_json(
            {
                "protocol_version": 1,
                "operation_id": operation_id,
                "task_id": task_id,
                "actor_kind": "agent",
                "actor_id": poster_id,
                "lock_operation_id": lock_operation_id,
                "title": title,
                "spec": spec,
                "reward": reward,
                "bidding_deadline_seconds": bidding_deadline_seconds,
                "acceptance_window_seconds": acceptance_window_seconds,
                "execution_deadline_seconds": execution_deadline_seconds,
                "review_deadline_seconds": review_deadline_seconds,
            }
        )

    async def submit_task_posting(
        self: _TaskBoardClient, intent: TaskPostingIntent
    ) -> TaskPostingResponse:
        """Sign fresh tokens and submit one previously prepared posting intent."""
        poster_id = self.agent_id
        if poster_id is None:
            raise RuntimeError("Agent must be registered before submitting a task posting")
        if not isinstance(intent, TaskPostingIntent):
            raise ValueError("intent must be a TaskPostingIntent")
        if intent.actor_id != poster_id:
            raise ValueError("task posting intent owner does not match the registered actor")
        url = f"{self.config.task_board_url}/tasks"
        task_token = self._sign_jws(
            {
                "action": "create_task",
                "protocol_version": intent.protocol_version,
                "operation_id": intent.operation_id,
                "task_id": intent.task_id,
                "poster_id": intent.actor_id,
                "lock_operation_id": intent.lock_operation_id,
                "title": intent.title,
                "spec": intent.spec,
                "reward": intent.reward,
                "bidding_deadline_seconds": intent.bidding_deadline_seconds,
                "acceptance_window_seconds": intent.acceptance_window_seconds,
                "execution_deadline_seconds": intent.execution_deadline_seconds,
                "review_deadline_seconds": intent.review_deadline_seconds,
            },
            audience="task-board",
            resource="POST /tasks",
        )
        escrow_token = self._sign_jws(
            {
                "action": "escrow_lock",
                "operation_id": intent.lock_operation_id,
                "task_id": intent.task_id,
                "amount": intent.reward,
                "agent_id": intent.actor_id,
            },
            audience="central-bank",
            resource="POST /escrow/lock",
        )
        response = await self._request_raw(
            "POST", url, json={"task_token": task_token, "escrow_token": escrow_token}
        )
        return parse_task_posting_submit_response(response, intent)

    async def get_task_posting(self: _TaskBoardClient, operation_id: str) -> TaskPostingResponse:
        """Read one owner-visible publication state using fresh signed authority."""
        poster_id = self.agent_id
        if poster_id is None:
            raise RuntimeError("Agent must be registered before reading a task posting")
        validated_operation_id = validate_operation_id(operation_id)
        resource = f"GET /task-publications/{validated_operation_id}"
        response = await self._request_raw(
            "GET",
            f"{self.config.task_board_url}/task-publications/{validated_operation_id}",
            headers=self._auth_header(
                {
                    "action": "get_task_publication",
                    "operation_id": validated_operation_id,
                    "poster_id": poster_id,
                },
                audience="task-board",
                resource=resource,
            ),
        )
        return parse_task_posting_status_response(response, validated_operation_id)

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

    async def cancel_task(
        self: _TaskBoardClient,
        task_id: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        """Cancel a task."""
        operation_id, expected_lifecycle_version = _operation_identity(
            operation_id, expected_lifecycle_version
        )
        url = f"{self.config.task_board_url}/tasks/{task_id}/cancel"
        token = self._sign_jws(
            {
                "action": "cancel_task",
                "operation_id": operation_id,
                "expected_lifecycle_version": expected_lifecycle_version,
                "task_id": task_id,
                "poster_id": self.agent_id,
            },
            audience="task-board",
            resource=f"POST /tasks/{task_id}/cancel",
        )
        return await _send_task_board_command(
            self,
            url=url,
            token=token,
            allowed_status_codes=frozenset({200, 202}),
            effect_kind="bank_release",
        )

    async def submit_bid(
        self: _TaskBoardClient,
        task_id: str,
        amount: int,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        """Submit a bid on a task."""
        operation_id, expected_lifecycle_version = _operation_identity(
            operation_id, expected_lifecycle_version
        )
        url = f"{self.config.task_board_url}/tasks/{task_id}/bids"
        token = self._sign_jws(
            {
                "action": "submit_bid",
                "operation_id": operation_id,
                "expected_lifecycle_version": expected_lifecycle_version,
                "task_id": task_id,
                "bidder_id": self.agent_id,
                "amount": amount,
            },
            audience="task-board",
            resource=f"POST /tasks/{task_id}/bids",
        )
        return await _send_task_board_command(
            self,
            url=url,
            token=token,
            allowed_status_codes=frozenset({201}),
            effect_kind=None,
        )

    async def list_bids(self: _TaskBoardClient, task_id: str) -> list[dict[str, Any]]:
        """List bids for a task."""
        url = f"{self.config.task_board_url}/tasks/{task_id}/bids"
        headers = self._auth_header(
            {
                "action": "list_bids",
                "task_id": task_id,
                "poster_id": self.agent_id,
            },
            audience="task-board",
            resource=f"GET /tasks/{task_id}/bids",
        )
        response = await self._request("GET", url, headers=headers)
        return cast("list[dict[str, Any]]", response["bids"])

    async def accept_bid(
        self: _TaskBoardClient,
        task_id: str,
        bid_id: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        """Accept a bid on a task."""
        operation_id, expected_lifecycle_version = _operation_identity(
            operation_id, expected_lifecycle_version
        )
        url = f"{self.config.task_board_url}/tasks/{task_id}/bids/{bid_id}/accept"
        token = self._sign_jws(
            {
                "action": "accept_bid",
                "operation_id": operation_id,
                "expected_lifecycle_version": expected_lifecycle_version,
                "task_id": task_id,
                "bid_id": bid_id,
                "poster_id": self.agent_id,
            },
            audience="task-board",
            resource=f"POST /tasks/{task_id}/bids/{bid_id}/accept",
        )
        return await _send_task_board_command(
            self,
            url=url,
            token=token,
            allowed_status_codes=frozenset({200}),
            effect_kind="none",
        )

    async def upload_asset(
        self: _TaskBoardClient,
        task_id: str,
        filename: str,
        content: bytes,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file asset for a task.

        ``idempotency_key`` (1-128 chars of ``[A-Za-z0-9._:-]``) makes a retry after a
        lost response return the already stored asset instead of a duplicate.
        """
        url = f"{self.config.task_board_url}/tasks/{task_id}/assets"
        headers = self._auth_header(
            {
                "action": "upload_asset",
                "task_id": task_id,
            },
            audience="task-board",
            resource=f"POST /tasks/{task_id}/assets",
        )
        if idempotency_key is not None:
            headers[IDEMPOTENCY_KEY_HEADER] = idempotency_key
        return await self._request(
            "POST", url, headers=headers, files={"file": (filename, content)}
        )

    async def submit_deliverable(
        self: _TaskBoardClient,
        task_id: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        """Submit deliverables for review."""
        operation_id, expected_lifecycle_version = _operation_identity(
            operation_id, expected_lifecycle_version
        )
        url = f"{self.config.task_board_url}/tasks/{task_id}/submit"
        token = self._sign_jws(
            {
                "action": "submit_deliverable",
                "operation_id": operation_id,
                "expected_lifecycle_version": expected_lifecycle_version,
                "task_id": task_id,
                "worker_id": self.agent_id,
            },
            audience="task-board",
            resource=f"POST /tasks/{task_id}/submit",
        )
        return await _send_task_board_command(
            self,
            url=url,
            token=token,
            allowed_status_codes=frozenset({200}),
            effect_kind="none",
        )

    async def approve_task(
        self: _TaskBoardClient,
        task_id: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        """Approve a submitted task."""
        operation_id, expected_lifecycle_version = _operation_identity(
            operation_id, expected_lifecycle_version
        )
        url = f"{self.config.task_board_url}/tasks/{task_id}/approve"
        token = self._sign_jws(
            {
                "action": "approve_task",
                "operation_id": operation_id,
                "expected_lifecycle_version": expected_lifecycle_version,
                "task_id": task_id,
                "poster_id": self.agent_id,
            },
            audience="task-board",
            resource=f"POST /tasks/{task_id}/approve",
        )
        return await _send_task_board_command(
            self,
            url=url,
            token=token,
            allowed_status_codes=frozenset({200, 202}),
            effect_kind=frozenset({"bank_release", "bank_split"}),
        )

    async def dispute_task(
        self: _TaskBoardClient,
        task_id: str,
        reason: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        """Dispute a submitted task."""
        operation_id, expected_lifecycle_version = _operation_identity(
            operation_id, expected_lifecycle_version
        )
        url = f"{self.config.task_board_url}/tasks/{task_id}/dispute"
        token = self._sign_jws(
            {
                "action": "dispute_task",
                "operation_id": operation_id,
                "expected_lifecycle_version": expected_lifecycle_version,
                "task_id": task_id,
                "poster_id": self.agent_id,
                "reason": reason,
            },
            audience="task-board",
            resource=f"POST /tasks/{task_id}/dispute",
        )
        return await _send_task_board_command(
            self,
            url=url,
            token=token,
            allowed_status_codes=frozenset({200, 202}),
            effect_kind="court_claim",
        )

    async def list_assets(self: _TaskBoardClient, task_id: str) -> list[dict[str, Any]]:
        """List a task's files (signed; poster, assigned worker, platform or operator)."""
        url = f"{self.config.task_board_url}/tasks/{quote(task_id, safe='')}/assets"
        headers = self._auth_header(
            {"action": "list_assets", "task_id": task_id},
            audience="task-board",
            resource=f"GET /tasks/{task_id}/assets",
        )
        response = await self._request("GET", url, headers=headers)
        return cast("list[dict[str, Any]]", response["assets"])

    async def download_asset(self: _TaskBoardClient, task_id: str, asset_id: str) -> bytes:
        """Download a file's exact bytes (signed; same principals as ``list_assets``)."""
        url = (
            f"{self.config.task_board_url}/tasks/{quote(task_id, safe='')}"
            f"/assets/{quote(asset_id, safe='')}"
        )
        headers = self._auth_header(
            {"action": "download_asset", "task_id": task_id, "asset_id": asset_id},
            audience="task-board",
            resource=f"GET /tasks/{task_id}/assets/{asset_id}",
        )
        response = await self._request_raw("GET", url, headers=headers)
        raise_for_api_error(response)
        return response.content

    async def get_task_inputs(self: _TaskBoardClient, task_id: str) -> dict[str, Any]:
        """Read a task's frozen input manifest (public while the task is non-terminal)."""
        url = f"{self.config.task_board_url}/tasks/{quote(task_id, safe='')}/inputs"
        return await self._request("GET", url)

    async def submit_worker_rebuttal(
        self: _TaskBoardClient,
        task_id: str,
        dispute_id: str,
        rebuttal: str,
    ) -> dict[str, Any]:
        """Submit a worker rebuttal through the Task Board mediation layer."""
        url = f"{self.config.task_board_url}/tasks/{task_id}/rebuttal"
        token = self._sign_jws(
            {
                "action": "submit_rebuttal",
                "task_id": task_id,
                "dispute_id": dispute_id,
                "worker_id": self.agent_id,
                "rebuttal": rebuttal,
            },
            audience="task-board",
            resource=f"POST /tasks/{task_id}/rebuttal",
        )
        return await self._request("POST", url, json={"token": token})

    # --- Protocol-2 posting with input attachments -------------------------------

    def new_task_publication(
        self: _TaskBoardClient,
        *,
        task_id: str,
        title: str,
        spec: str,
        reward: int,
        bidding_deadline_seconds: int,
        acceptance_window_seconds: int,
        execution_deadline_seconds: int,
        review_deadline_seconds: int,
        expected_input_count: int,
    ) -> dict[str, Any]:
        """Build the admission intent for a protocol-2 publication (poster-signed later).

        The returned dict carries ``publication_operation_id`` (fresh UUID) and the
        derived ``lock_operation_id``; pass it to ``admit_task_publication`` and
        ``publish_task_publication``. Persist it yourself if you need restart safety.
        """
        poster_id = self.agent_id
        if poster_id is None:
            msg = "Register the agent before building a task publication"
            raise ValueError(msg)
        return {
            "publication_operation_id": f"op-{uuid.uuid4()}",
            "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
            "task_id": task_id,
            "poster_id": poster_id,
            "title": title,
            "spec": spec,
            "reward": reward,
            "bidding_deadline_seconds": bidding_deadline_seconds,
            "acceptance_window_seconds": acceptance_window_seconds,
            "execution_deadline_seconds": execution_deadline_seconds,
            "review_deadline_seconds": review_deadline_seconds,
            "expected_input_count": expected_input_count,
        }

    async def admit_task_publication(
        self: _TaskBoardClient, intent: dict[str, Any]
    ) -> dict[str, Any]:
        """Admit a protocol-2 publication; returns the 202 body (``input_manifest_id``)."""
        fields = (
            "publication_operation_id",
            "task_id",
            "poster_id",
            "title",
            "spec",
            "reward",
            "bidding_deadline_seconds",
            "acceptance_window_seconds",
            "execution_deadline_seconds",
            "review_deadline_seconds",
            "expected_input_count",
        )
        token = self._sign_jws(
            {"action": "task_publication_admit", **{field: intent[field] for field in fields}},
            audience="task-board",
            resource="POST /task-publications",
        )
        url = f"{self.config.task_board_url}/task-publications"
        return await self._request("POST", url, json={"token": token})

    async def upload_task_input(
        self: _TaskBoardClient,
        publication_operation_id: str,
        filename: str,
        content: bytes,
        *,
        content_type: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Upload one input file into an admitted publication (poster only, 201)."""
        publication_id = validate_operation_id(publication_operation_id)
        headers = self._auth_header(
            {
                "action": "task_publication_upload_input",
                "publication_operation_id": publication_id,
                "poster_id": self.agent_id,
            },
            audience="task-board",
            resource=f"POST /task-publications/{publication_id}/inputs",
        )
        headers[IDEMPOTENCY_KEY_HEADER] = idempotency_key
        url = (
            f"{self.config.task_board_url}/task-publications/"
            f"{quote(publication_id, safe='')}/inputs"
        )
        return await self._request(
            "POST", url, headers=headers, files={"file": (filename, content, content_type)}
        )

    async def publish_task_publication(
        self: _TaskBoardClient, intent: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        """Freeze the inputs, lock escrow and publish; ``(201, body)`` once published,
        ``(202, body)`` while the lock is still pending. Safe to retry."""
        publication_id = validate_operation_id(str(intent["publication_operation_id"]))
        task_token = self._sign_jws(
            {
                "action": "task_publication_publish",
                "publication_operation_id": publication_id,
                "task_id": intent["task_id"],
                "poster_id": self.agent_id,
            },
            audience="task-board",
            resource=f"POST /task-publications/{publication_id}/publish",
        )
        escrow_token = self._sign_jws(
            {
                "action": "escrow_lock",
                "operation_id": intent["lock_operation_id"],
                "task_id": intent["task_id"],
                "amount": intent["reward"],
                "agent_id": self.agent_id,
            },
            audience="central-bank",
            resource="POST /escrow/lock",
        )
        url = (
            f"{self.config.task_board_url}/task-publications/"
            f"{quote(publication_id, safe='')}/publish"
        )
        response = await self._request_raw(
            "POST", url, json={"task_token": task_token, "escrow_token": escrow_token}
        )
        raise_for_api_error(response)
        return response.status_code, cast("dict[str, Any]", response.json())
