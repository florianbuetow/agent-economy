"""Operator participation proxy routes: cancel, feedback, bid, upload, submit, rebuttal.

Each route signs as the configured operator through the UI's UserAgent. These
tests replace the UserAgent with a network-free fake and assert the UI-owned
contract: what is forwarded, what is passed back, and which requests are refused
before or by the upstream service.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.config import AgentConfig
from service_auth.jws import TokenPolicy
from service_auth.task_board_responses import TaskBoardCommandResponse

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration

OPERATION_ID = "op-00000000-0000-4000-8000-0000000000a1"
IDENTITY = {"operation_id": OPERATION_ID, "expected_lifecycle_version": 4}


def _operation_body(command: str, task_id: str) -> dict[str, Any]:
    source, target, effect = {
        "cancel_task": ("open", "cancelled", "bank_release"),
        "submit_deliverable": ("accepted", "submitted", "none"),
    }[command]
    return {
        "task_id": task_id,
        "status": target,
        "lifecycle_version": 5,
        "operation": {
            "protocol_version": 1,
            "operation_id": OPERATION_ID,
            "task_id": task_id,
            "command": command,
            "request_sha256": "b" * 64,
            "operation_state": "completed",
            "source_status": source,
            "source_lifecycle_version": 4,
            "target_status": target,
            "target_lifecycle_version": 5,
            "effect_kind": effect,
            "effect_state": "none" if effect == "none" else "succeeded",
            "effect_operation_id": None if effect == "none" else "op-bank-release",
            "effect_result_reference": None if effect == "none" else "tx-1",
            "effect_intent": None if effect == "none" else {"fixture": command},
            "event_id": 91,
            "result": {"task_id": task_id, "status": target},
        },
    }


def _upstream_error(status_code: int, error: str, url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", url)
    response = httpx.Response(
        status_code,
        request=request,
        json={"error": error, "message": f"{error} from upstream", "details": {}},
    )
    return httpx.HTTPStatusError(error, request=request, response=response)


class _FakeOperator:
    """Records every signed call the proxy makes; raises when told to."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        private_key = Ed25519PrivateKey.generate()
        self.config = AgentConfig(
            name="UI operator",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url="https://identity.test",
            bank_url="https://bank.test",
            task_board_url="https://board.test",
            reputation_url="https://reputation.test",
            court_url="https://court.test",
            token_ttl_seconds=30,
            token_policy=TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0),
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.raises: Exception | None = None
        self.upload_response: httpx.Response | None = None

    def _record(self, name: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((name, kwargs))
        if self.raises is not None:
            raise self.raises

    async def cancel_task(self, task_id: str, **kwargs: Any) -> TaskBoardCommandResponse:
        self._record("cancel_task", {"task_id": task_id, **kwargs})
        return TaskBoardCommandResponse(200, _operation_body("cancel_task", task_id))

    async def submit_deliverable(self, task_id: str, **kwargs: Any) -> TaskBoardCommandResponse:
        self._record("submit_deliverable", {"task_id": task_id, **kwargs})
        return TaskBoardCommandResponse(200, _operation_body("submit_deliverable", task_id))

    async def submit_bid(
        self, task_id: str, amount: int, **kwargs: Any
    ) -> TaskBoardCommandResponse:
        self._record("submit_bid", {"task_id": task_id, "amount": amount, **kwargs})
        return TaskBoardCommandResponse(
            201,
            {
                "bid_id": "bid-00000000-0000-4000-8000-0000000000a1",
                "task_id": task_id,
                "bidder_id": self.agent_id,
                "amount": amount,
                "submitted_at": "2026-10-30T10:00:00Z",
            },
        )

    async def submit_worker_rebuttal(
        self, task_id: str, dispute_id: str, rebuttal: str
    ) -> dict[str, Any]:
        self._record(
            "submit_worker_rebuttal",
            {"task_id": task_id, "dispute_id": dispute_id, "rebuttal": rebuttal},
        )
        return {"dispute_id": dispute_id, "rebuttal": rebuttal, "status": "rebuttal"}

    async def submit_feedback(self, **kwargs: Any) -> dict[str, Any]:
        self._record("submit_feedback", kwargs)
        return {"feedback_id": "fb-new", **kwargs, "visible": False}

    def _auth_header(
        self, payload: dict[str, object], *, audience: str, resource: str
    ) -> dict[str, str]:
        self.calls.append(
            ("auth_header", {"payload": payload, "audience": audience, "resource": resource})
        )
        return {"Authorization": "Bearer signed-by-operator"}

    async def _request_raw(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append(("request_raw", {"method": method, "url": url, **kwargs}))
        assert self.upload_response is not None
        return self.upload_response

    async def close(self) -> None:
        return None


def _install(agent_id: str) -> _FakeOperator:
    fake = _FakeOperator(agent_id)
    get_app_state().user_agent = fake
    return fake


def _names(fake: _FakeOperator) -> list[str]:
    return [name for name, _ in fake.calls]


# ── Cancel (poster, open task) ────────────────────────────────────────────


async def test_cancel_forwards_operation_identity_and_returns_result(client) -> None:
    fake = _install("a-bob")
    response = await client.post("/api/proxy/tasks/t-task7/cancel", json=IDENTITY)
    assert response.status_code == 200
    assert response.json() == _operation_body("cancel_task", "t-task7")
    assert fake.calls == [("cancel_task", {"task_id": "t-task7", **IDENTITY})]


async def test_cancel_invalid_status_is_passed_through(client) -> None:
    fake = _install("a-alice")
    fake.raises = _upstream_error(409, "invalid_status", "https://board.test/tasks/t-task1/cancel")
    response = await client.post("/api/proxy/tasks/t-task1/cancel", json=IDENTITY)
    assert response.status_code == 409
    assert response.json()["error"] == "invalid_status"


async def test_cancel_requires_json_content_type(client) -> None:
    fake = _install("a-bob")
    response = await client.post(
        "/api/proxy/tasks/t-task7/cancel", content=b"{}", headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 415
    assert fake.calls == []


# ── Bid (operator on someone else's open task) ────────────────────────────


async def test_bid_forwards_amount_and_returns_created_bid(client) -> None:
    fake = _install("a-operator")
    response = await client.post("/api/proxy/tasks/t-task7/bids", json={"amount": 25, **IDENTITY})
    assert response.status_code == 201
    assert response.json()["amount"] == 25
    assert response.json()["bidder_id"] == "a-operator"
    assert fake.calls == [("submit_bid", {"task_id": "t-task7", "amount": 25, **IDENTITY})]


async def test_bid_self_bid_is_refused_upstream(client) -> None:
    fake = _install("a-bob")
    fake.raises = _upstream_error(400, "self_bid", "https://board.test/tasks/t-task7/bids")
    response = await client.post("/api/proxy/tasks/t-task7/bids", json={"amount": 25, **IDENTITY})
    assert response.status_code == 400
    assert response.json()["error"] == "self_bid"


@pytest.mark.parametrize("amount", [0, -3, "10"])
async def test_bid_amount_must_be_a_positive_integer(client, amount: object) -> None:
    fake = _install("a-operator")
    response = await client.post(
        "/api/proxy/tasks/t-task7/bids", json={"amount": amount, **IDENTITY}
    )
    assert response.status_code == 422
    assert fake.calls == []


async def test_bid_response_from_another_bidder_is_rejected(client) -> None:
    fake = _install("a-operator")

    async def foreign_bid(task_id: str, amount: int, **_kwargs: Any) -> TaskBoardCommandResponse:
        return TaskBoardCommandResponse(
            201,
            {
                "bid_id": "bid-x",
                "task_id": task_id,
                "bidder_id": "a-someone-else",
                "amount": amount,
                "submitted_at": "2026-10-30T10:00:00Z",
            },
        )

    fake.submit_bid = foreign_bid  # type: ignore[method-assign]
    response = await client.post("/api/proxy/tasks/t-task7/bids", json={"amount": 25, **IDENTITY})
    assert response.status_code == 502
    assert response.json()["error"] == "bid_submission_failed"


# ── Upload (assigned worker) ──────────────────────────────────────────────


async def test_upload_signs_as_operator_and_forwards_multipart(client) -> None:
    fake = _install("a-carol")
    asset = {
        "asset_id": "asset-1",
        "task_id": "t-task2",
        "uploader_id": "a-carol",
        "filename": "result.txt",
        "content_type": "text/plain",
        "size_bytes": 5,
        "content_hash": "c" * 64,
        "uploaded_at": "2026-10-30T10:00:00Z",
    }
    fake.upload_response = httpx.Response(
        201, json=asset, request=httpx.Request("POST", "https://board.test/tasks/t-task2/assets")
    )
    response = await client.post(
        "/api/proxy/tasks/t-task2/assets",
        files={"file": ("result.txt", b"hello", "text/plain")},
        headers={"Idempotency-Key": "upload-key-1"},
    )
    assert response.status_code == 201
    assert response.json() == asset
    (auth_name, auth), (raw_name, raw) = fake.calls
    assert auth_name == "auth_header"
    assert auth == {
        "payload": {"action": "upload_asset", "task_id": "t-task2"},
        "audience": "task-board",
        "resource": "POST /tasks/t-task2/assets",
    }
    assert raw_name == "request_raw"
    assert raw["method"] == "POST"
    assert raw["url"] == "https://board.test/tasks/t-task2/assets"
    assert raw["headers"]["Authorization"] == "Bearer signed-by-operator"
    assert raw["headers"]["Idempotency-Key"] == "upload-key-1"
    assert raw["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b"hello" in raw["content"]


async def test_upload_by_non_worker_is_refused_upstream(client) -> None:
    fake = _install("a-operator")
    fake.upload_response = httpx.Response(
        403,
        json={
            "error": "forbidden",
            "message": "Only the assigned worker may upload",
            "details": {},
        },
        request=httpx.Request("POST", "https://board.test/tasks/t-task2/assets"),
    )
    response = await client.post(
        "/api/proxy/tasks/t-task2/assets",
        files={"file": ("result.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


async def test_upload_requires_multipart_body(client) -> None:
    fake = _install("a-carol")
    response = await client.post("/api/proxy/tasks/t-task2/assets", json={"file": "x"})
    assert response.status_code == 415
    assert fake.calls == []


# ── Submit (assigned worker, accepted task) ───────────────────────────────


async def test_submit_forwards_operation_identity(client) -> None:
    fake = _install("a-carol")
    response = await client.post("/api/proxy/tasks/t-task2/submit", json=IDENTITY)
    assert response.status_code == 200
    assert response.json() == _operation_body("submit_deliverable", "t-task2")
    assert fake.calls == [("submit_deliverable", {"task_id": "t-task2", **IDENTITY})]


async def test_submit_on_wrong_status_is_passed_through(client) -> None:
    fake = _install("a-bob")
    fake.raises = _upstream_error(409, "invalid_status", "https://board.test/tasks/t-task1/submit")
    response = await client.post("/api/proxy/tasks/t-task1/submit", json=IDENTITY)
    assert response.status_code == 409
    assert response.json()["error"] == "invalid_status"


# ── Rebuttal (disputed worker) ────────────────────────────────────────────


async def test_rebuttal_uses_the_tasks_recorded_dispute(client, write_db) -> None:
    await write_db.execute("UPDATE board_tasks SET dispute_id='disp-11' WHERE task_id='t-task11'")
    await write_db.commit()
    fake = _install("a-eve")
    response = await client.post(
        "/api/proxy/tasks/t-task11/rebuttal", json={"rebuttal": "The spec allowed this."}
    )
    assert response.status_code == 200
    assert fake.calls == [
        (
            "submit_worker_rebuttal",
            {"task_id": "t-task11", "dispute_id": "disp-11", "rebuttal": "The spec allowed this."},
        )
    ]


async def test_rebuttal_on_undisputed_task_is_refused(client) -> None:
    fake = _install("a-carol")
    response = await client.post(
        "/api/proxy/tasks/t-task2/rebuttal", json={"rebuttal": "Nothing to rebut"}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "invalid_status"
    assert fake.calls == []


async def test_rebuttal_by_non_worker_is_forbidden(client, write_db) -> None:
    await write_db.execute("UPDATE board_tasks SET dispute_id='disp-11' WHERE task_id='t-task11'")
    await write_db.commit()
    fake = _install("a-operator")
    response = await client.post(
        "/api/proxy/tasks/t-task11/rebuttal", json={"rebuttal": "Not my task"}
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    assert fake.calls == []


# ── Feedback (poster or worker, approved/ruled task) ──────────────────────


async def test_poster_feedback_rates_the_workers_delivery(client) -> None:
    fake = _install("a-alice")
    response = await client.post(
        "/api/proxy/tasks/t-task1/feedback", json={"rating": "satisfied", "comment": "Good"}
    )
    assert response.status_code == 201
    assert fake.calls == [
        (
            "submit_feedback",
            {
                "task_id": "t-task1",
                "to_agent_id": "a-bob",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Good",
            },
        )
    ]


async def test_worker_feedback_rates_the_posters_spec(client) -> None:
    fake = _install("a-carol")
    response = await client.post(
        "/api/proxy/tasks/t-task5/feedback", json={"rating": "dissatisfied", "comment": None}
    )
    assert response.status_code == 201
    assert fake.calls == [
        (
            "submit_feedback",
            {
                "task_id": "t-task5",
                "to_agent_id": "a-dave",
                "category": "spec_quality",
                "rating": "dissatisfied",
                "comment": None,
            },
        )
    ]


async def test_feedback_from_outsider_is_forbidden(client) -> None:
    fake = _install("a-operator")
    response = await client.post(
        "/api/proxy/tasks/t-task1/feedback", json={"rating": "satisfied", "comment": None}
    )
    assert response.status_code == 403
    assert response.json()["error"] == "not_task_party"
    assert fake.calls == []


async def test_feedback_before_outcome_is_refused(client) -> None:
    fake = _install("a-alice")
    response = await client.post(
        "/api/proxy/tasks/t-task2/feedback", json={"rating": "satisfied", "comment": None}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "task_not_eligible"
    assert fake.calls == []


async def test_feedback_rating_vocabulary_is_enforced(client) -> None:
    fake = _install("a-alice")
    response = await client.post(
        "/api/proxy/tasks/t-task1/feedback", json={"rating": "great", "comment": None}
    )
    assert response.status_code == 422
    assert fake.calls == []


# ── Task read: deadlines and accepted price; scoped history ──────────────


async def test_task_read_exposes_every_deadline_and_accepted_price(client, write_db) -> None:
    await write_db.execute(
        "UPDATE board_task_contracts SET accepted_bid_amount=70 WHERE task_id='t-task2'"
    )
    await write_db.commit()
    response = await client.get("/api/tasks/t-task2")
    assert response.status_code == 200
    body = response.json()
    assert set(body["deadlines"]) == {
        "bidding_deadline",
        "acceptance_deadline",
        "execution_deadline",
        "review_deadline",
    }
    assert body["deadlines"]["acceptance_deadline"] is not None
    assert body["accepted_price"] == 70


async def test_open_task_has_no_accepted_price(client) -> None:
    response = await client.get("/api/tasks/t-task7")
    assert response.status_code == 200
    assert response.json()["accepted_price"] is None


async def test_events_can_be_scoped_to_one_task(client) -> None:
    response = await client.get("/api/events?task_id=t-task1&limit=200")
    assert response.status_code == 200
    events = response.json()["events"]
    assert events
    assert {event["task_id"] for event in events} == {"t-task1"}
