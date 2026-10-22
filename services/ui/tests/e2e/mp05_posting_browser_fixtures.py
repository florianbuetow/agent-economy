from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent

if TYPE_CHECKING:
    from playwright.sync_api import Page

OPERATION_ID = "op-00000000-0000-4000-8000-000000000501"
SECOND_OPERATION_ID = "op-00000000-0000-4000-8000-000000000601"
TASK_ID = "t-00000000-0000-4000-8000-000000000502"
SECOND_TASK_ID = "t-00000000-0000-4000-8000-000000000602"
POSTING_KEY = f"ate.task-posting:v1:{OPERATION_ID}"
SECOND_POSTING_KEY = f"ate.task-posting:v1:{SECOND_OPERATION_ID}"
COMPLETION_KEY = f"ate.task-posting-completion:v1:{OPERATION_ID}"

TERMS = {
    "title": "Browser durable posting",
    "spec": "Persist this exact browser-owned task intent.",
    "reward": 75,
    "bidding_deadline_seconds": 120,
    "acceptance_window_seconds": 45,
    "execution_deadline_seconds": 300,
    "review_deadline_seconds": 90,
}


def _intent(operation_id: str, task_id: str, terms: dict[str, object]) -> TaskPostingIntent:
    return TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": "a-ui-native",
            "lock_operation_id": operation_id_for("task-lock", (task_id, "a-ui-native")),
            **terms,
        }
    )


_FIRST_INTENT = _intent(OPERATION_ID, TASK_ID, TERMS)
_SECOND_INTENT = _intent(SECOND_OPERATION_ID, SECOND_TASK_ID, {**TERMS, "reward": 999})
INTENT = _FIRST_INTENT.as_json()
SCOPE = {
    "schema_version": 1,
    "operator_agent_id": "a-ui-native",
    "operator_public_fingerprint_sha256": "5" * 64,
    "service_urls": {
        "identity_url": "http://127.0.0.1:8001",
        "bank_url": "http://127.0.0.1:8002",
        "task_board_url": "http://127.0.0.1:8003",
        "reputation_url": "http://127.0.0.1:8004",
        "court_url": "http://127.0.0.1:8005",
    },
}
RECORD = {
    "record_version": 1,
    "scope": SCOPE,
    "intent": INTENT,
    "request_sha256": _FIRST_INTENT.request_sha256,
}
SECOND_RECORD = {
    "record_version": 1,
    "scope": copy.deepcopy(SCOPE),
    "intent": _SECOND_INTENT.as_json(),
    "request_sha256": _SECOND_INTENT.request_sha256,
}


def _epoch_us(timestamp: str) -> int:
    difference = datetime.fromisoformat(timestamp) - datetime(1970, 1, 1, tzinfo=UTC)
    return (difference.days * 86_400 + difference.seconds) * 1_000_000 + difference.microseconds


def publication(state: str) -> dict[str, object]:
    published = state == "published"
    return {
        "protocol_version": 1,
        "operation_id": OPERATION_ID,
        "task_id": TASK_ID,
        "request_sha256": RECORD["request_sha256"],
        "publication_state": state,
        "lock_operation_id": INTENT["lock_operation_id"],
        "escrow_id": "esc-browser" if published else None,
        "admitted_at": "2026-10-19T09:00:00.000000Z",
        "published_at": "2026-10-19T09:00:01.000000Z" if published else None,
        "result": (
            {
                "task_id": TASK_ID,
                "poster_id": "a-ui-native",
                "title": TERMS["title"],
                "spec": TERMS["spec"],
                "reward": 75,
                "bidding_deadline_seconds": 120,
                "acceptance_window_seconds": 45,
                "execution_deadline_seconds": 300,
                "review_deadline_seconds": 90,
                "status": "open",
                "escrow_id": "esc-browser",
                "created_at": "2026-10-19T09:00:01.000000Z",
                "bidding_deadline": "2026-10-19T09:02:01.000000Z",
                "acceptance_deadline": "2026-10-19T09:02:46.000000Z",
                "bidding_cutoff_us": _epoch_us("2026-10-19T09:02:01.000000Z"),
                "acceptance_cutoff_us": _epoch_us("2026-10-19T09:02:46.000000Z"),
                "lifecycle_version": 0,
                "timing_contract_version": 2,
                "event_id": 501,
            }
            if published
            else None
        ),
        "error": None,
    }


def observation(state: str, *, kind: str) -> dict[str, object]:
    status = 200 if kind == "status" else (201 if state == "published" else 202)
    return {"observation": kind, "status_code": status, "publication": publication(state)}


def completion_marker() -> dict[str, object]:
    return {
        "scope": copy.deepcopy(SCOPE),
        "operation_id": OPERATION_ID,
        "task_id": TASK_ID,
        "request_sha256": RECORD["request_sha256"],
        "observed_status_code": 201,
        "publication": publication("published"),
    }


def install_trace(page: Page, *, fail_prefix: str | None = None) -> None:
    failure_json = json.dumps(fail_prefix)
    page.add_init_script(
        """(() => {
          const failurePrefix = __FAILURE__;
          window.__mp05Events = [];
          const originalSet = Storage.prototype.setItem;
          Storage.prototype.setItem = function(key, value) {
            window.__mp05Events.push({kind: 'store', key, value});
            if (failurePrefix && key.startsWith(failurePrefix)) {
              throw new DOMException('forced storage failure', 'QuotaExceededError');
            }
            return originalSet.call(this, key, value);
          };
          const originalFetch = window.fetch;
          window.fetch = function(input, init) {
            const url = typeof input === 'string' ? input : input.url;
            window.__mp05Events.push({kind: 'fetch', url, body: init && init.body});
            return originalFetch.apply(this, arguments);
          };
        })();""".replace("__FAILURE__", failure_json),
    )


def goto_form(page: Page, server: str) -> None:
    page.set_default_timeout(1_500)
    page.goto(f"{server}/task.html")
    page.locator("#btn-post-task").wait_for(state="visible")


def fill_form(page: Page, updates: dict[str, str] | None = None) -> None:
    values = {
        "#f-title": str(TERMS["title"]),
        "#f-spec": str(TERMS["spec"]),
        "#f-reward": str(TERMS["reward"]),
        "#f-bid-dl": str(TERMS["bidding_deadline_seconds"]),
        "#f-accept-window": str(TERMS["acceptance_window_seconds"]),
        "#f-exec-dl": str(TERMS["execution_deadline_seconds"]),
        "#f-rev-dl": str(TERMS["review_deadline_seconds"]),
    }
    values.update(updates or {})
    for selector, value in values.items():
        if any(character.isalpha() for character in value):
            page.locator(selector).evaluate("element => { element.type = 'text'; }")
        page.locator(selector).fill(value)


def events(page: Page) -> list[dict[str, Any]]:
    value = page.evaluate("window.__mp05Events || []")
    assert isinstance(value, list)
    return value


def stored(page: Page, key: str) -> object:
    value = page.evaluate("key => localStorage.getItem(key)", key)
    return None if value is None else json.loads(value)
