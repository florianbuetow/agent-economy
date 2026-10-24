"""Regression coverage for strict native posting acknowledgments."""

from __future__ import annotations

import pytest
from mp05_posting_fixtures import intent, publication
from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse
from service_commons.exceptions import ServiceError

from ui_service.services.proxy import _posting_result

pytestmark = pytest.mark.unit


def _blocked_wrapper(posting_intent: TaskPostingIntent) -> dict[str, object]:
    return {
        "error": "publication_integrity_blocked",
        "message": "Publication evidence requires reconciliation",
        "details": {
            "publication": publication(posting_intent, "blocked_integrity"),
        },
    }


def test_submit_rejects_bare_blocked_publication_as_malformed() -> None:
    posting_intent = intent()
    malformed = TaskPostingResponse(
        status_code=409,
        body=publication(posting_intent, "blocked_integrity"),
    )

    with pytest.raises(ServiceError) as caught:
        _posting_result(malformed, intent=posting_intent, observation="submit")

    assert caught.value.error == "task_posting_failed"
    assert caught.value.message == "Task Board returned a malformed posting acknowledgment"
    assert caught.value.status_code == 502
    assert caught.value.details == {}


def test_submit_preserves_valid_blocked_wrapper_and_transport_status() -> None:
    posting_intent = intent()
    response = TaskPostingResponse(
        status_code=409,
        body=_blocked_wrapper(posting_intent),
    )

    status_code, result = _posting_result(
        response,
        intent=posting_intent,
        observation="submit",
    )

    assert status_code == 409
    assert result == {
        "observation": "submit",
        "status_code": 409,
        "publication": publication(posting_intent, "blocked_integrity"),
    }
