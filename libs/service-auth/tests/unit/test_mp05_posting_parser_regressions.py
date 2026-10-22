"""Independent MP-05 parser review regressions beyond the original matrix."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from service_auth import TaskPostingIntent
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import parse_task_posting_status_response
from tests.unit.test_mp05_posting_responses import _envelope, _intent

pytestmark = pytest.mark.unit


def _status(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json=body,
        request=httpx.Request("GET", "https://board/task-publications/op-test"),
    )


@pytest.mark.parametrize("reason", [[], {}])
def test_non_string_block_reason_is_a_contract_error(reason: object) -> None:
    body = _envelope("blocked_integrity")
    error = body["error"]
    assert isinstance(error, dict)
    details = error["details"]
    assert isinstance(details, dict)
    details["reason"] = reason
    with pytest.raises(RuntimeError):
        parse_task_posting_status_response(_status(body), _intent().operation_id)


def test_unicode_year_is_not_the_canonical_ascii_timestamp() -> None:
    body = _envelope("awaiting_lock")
    body["admitted_at"] = "٢٠٢٦-10-18T09:59:59.000000Z"
    with pytest.raises(RuntimeError):
        parse_task_posting_status_response(_status(body), _intent().operation_id)


def test_posting_preserves_valid_public_task_id_hex_spelling() -> None:
    body = _intent().as_json()
    task_id = "t-ABCDEF12-1234-4ABC-8DEF-ABCDEF123456"
    actor_id = _intent().actor_id
    body["task_id"] = task_id
    body["lock_operation_id"] = operation_id_for("task-lock", (task_id, actor_id))
    intent = TaskPostingIntent.from_json(body)
    assert intent.task_id == task_id
    assert intent.as_json() == body


def test_zero_epoch_is_an_admissible_native_clock_value() -> None:
    body = _envelope("awaiting_lock")
    body["admitted_at"] = "1970-01-01T00:00:00.000000Z"
    result = parse_task_posting_status_response(_status(body), _intent().operation_id)
    assert result.status_code == 200
    assert result.body == body
