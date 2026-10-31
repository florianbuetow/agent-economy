"""Transport observations for native Task Board commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from service_auth.errors import raise_for_api_error

if TYPE_CHECKING:
    import httpx


@dataclass(frozen=True, slots=True)
class TaskBoardCommandResponse:
    """Actual HTTP status and decoded body, prior to caller-specific verification."""

    status_code: int
    body: dict[str, Any]


class TaskBoardCommandContractError(RuntimeError):
    """A successful HTTP response contradicts the command transport contract."""

    def __init__(self, *, status_code: int, reason: str) -> None:
        self.status_code = status_code
        self.reason = reason
        super().__init__(reason)


def parse_task_board_command_response(
    response: httpx.Response,
    *,
    allowed_status_codes: frozenset[int],
    effect_kind: str | frozenset[str] | None,
) -> TaskBoardCommandResponse:
    """Retain real status; leave identity/hash/result checks to the bound caller.

    ``effect_kind`` may be a set when the server chooses the effect (an approval is a
    release at full budget and an amount split below it).
    """
    raise_for_api_error(response)
    status_code = response.status_code
    if status_code not in allowed_status_codes:
        raise TaskBoardCommandContractError(
            status_code=status_code, reason="Unexpected Task Board command success status"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise TaskBoardCommandContractError(
            status_code=status_code, reason="Task Board command response is not valid JSON"
        ) from exc
    if not isinstance(body, dict):
        raise TaskBoardCommandContractError(
            status_code=status_code, reason="Task Board command response must be an object"
        )
    if effect_kind is not None:
        _validate_operation_state(body, status_code=status_code, effect_kind=effect_kind)
    return TaskBoardCommandResponse(status_code=status_code, body=body)


def _validate_operation_state(
    body: dict[str, Any], *, status_code: int, effect_kind: str | frozenset[str]
) -> None:
    operation = body.get("operation")
    allowed = frozenset({effect_kind}) if isinstance(effect_kind, str) else effect_kind
    expected_state = "claimed" if status_code == 202 else "completed"
    expected_effect = "pending" if status_code == 202 else "succeeded"
    if allowed == {"none"}:
        expected_effect = "none"
    if (
        not isinstance(operation, dict)
        or operation.get("operation_state") != expected_state
        or operation.get("effect_kind") not in allowed
        or operation.get("effect_state") != expected_effect
        or (status_code == 202 and allowed == {"none"})
    ):
        raise TaskBoardCommandContractError(
            status_code=status_code,
            reason="Task Board operation state contradicts the HTTP command response",
        )
