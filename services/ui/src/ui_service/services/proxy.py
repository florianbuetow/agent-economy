"""Proxy service — delegates task lifecycle operations to UserAgent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from service_commons.exceptions import ServiceError

from ui_service.core.state import get_app_state

if TYPE_CHECKING:
    from service_auth import UserAgent


def _get_user_agent() -> UserAgent:
    """Get the UserAgent from app state, raising ServiceError if unavailable."""
    state = get_app_state()
    if state.user_agent is None:
        raise ServiceError(
            "user_agent_unavailable",
            "UserAgent is not initialized. Task lifecycle operations are unavailable.",
            503,
            {},
        )
    return state.user_agent


def _ensure_dict_response(value: Any, error_code: str) -> dict[str, Any]:
    """Ensure proxy operations return object payloads."""
    if not isinstance(value, dict):
        raise ServiceError(
            error_code,
            "Unexpected response type from task-board operation.",
            502,
            {},
        )
    return value


async def get_identity() -> str:
    """Return the UserAgent's agent_id."""
    agent = _get_user_agent()
    agent_id = agent.agent_id
    if not isinstance(agent_id, str) or agent_id == "":
        raise ServiceError(
            "user_agent_not_registered",
            "UserAgent is not registered with the Identity service.",
            503,
            {},
        )
    return agent_id


async def create_task(
    title: str,
    spec: str,
    reward: int,
    bidding_deadline_seconds: int,
    execution_deadline_seconds: int,
    review_deadline_seconds: int,
) -> dict[str, Any]:
    """Post a new task via UserAgent."""
    agent = _get_user_agent()
    try:
        result = await agent.post_task(
            title=title,
            spec=spec,
            reward=reward,
            bidding_deadline_seconds=bidding_deadline_seconds,
            execution_deadline_seconds=execution_deadline_seconds,
            review_deadline_seconds=review_deadline_seconds,
        )
        return _ensure_dict_response(result, "task_creation_failed")
    except Exception as exc:
        raise ServiceError(
            "task_creation_failed",
            f"Failed to create task: {exc}",
            502,
            {},
        ) from exc


async def list_bids(task_id: str) -> list[dict[str, Any]]:
    """List bids through the operator's poster-authorized Task Board client."""
    agent = _get_user_agent()
    untyped_agent: Any = agent
    try:
        result: object = await untyped_agent.list_bids(task_id=task_id)
    except ServiceError:
        raise
    except Exception as exc:
        response: Any = getattr(exc, "response", None)
        if getattr(response, "status_code", None) == 403:
            try:
                body = response.json()
            except Exception:
                body = None
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("error"), str)
                or not isinstance(body.get("message"), str)
                or not isinstance(body.get("details"), dict)
            ):
                raise ServiceError(
                    "bid_listing_failed",
                    "Task Board returned a malformed authorization error.",
                    502,
                    {},
                ) from exc
            raise ServiceError(
                body["error"],
                body["message"],
                403,
                body["details"],
            ) from exc
        raise ServiceError(
            "bid_listing_failed",
            f"Failed to list bids: {exc}",
            502,
            {},
        ) from exc
    if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
        raise ServiceError(
            "bid_listing_failed",
            "Unexpected response type from task-board operation.",
            502,
            {},
        )
    return cast("list[dict[str, Any]]", result)


async def accept_bid(task_id: str, bid_id: str) -> dict[str, Any]:
    """Accept a bid on a task via UserAgent."""
    agent = _get_user_agent()
    try:
        result = await agent.accept_bid(task_id=task_id, bid_id=bid_id)
        return _ensure_dict_response(result, "bid_acceptance_failed")
    except Exception as exc:
        raise ServiceError(
            "bid_acceptance_failed",
            f"Failed to accept bid: {exc}",
            502,
            {},
        ) from exc


async def approve_task(task_id: str) -> dict[str, Any]:
    """Approve a submitted task via UserAgent."""
    agent = _get_user_agent()
    try:
        result = await agent.approve_task(task_id=task_id)
        return _ensure_dict_response(result, "task_approval_failed")
    except Exception as exc:
        raise ServiceError(
            "task_approval_failed",
            f"Failed to approve task: {exc}",
            502,
            {},
        ) from exc


async def file_dispute(task_id: str, reason: str) -> dict[str, Any]:
    """File a dispute on a task via UserAgent."""
    agent = _get_user_agent()
    try:
        result = await agent.dispute_task(task_id=task_id, reason=reason)
        return _ensure_dict_response(result, "dispute_filing_failed")
    except Exception as exc:
        raise ServiceError(
            "dispute_filing_failed",
            f"Failed to file dispute: {exc}",
            502,
            {},
        ) from exc
