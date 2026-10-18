"""T-072 coverage-vs-spec-ID sweep: gap-fill tests for claim status updates
and ruling deletion at the HTTP router layer.

test_write_integrity.py already exercises DbWriter.update_claim_status() and
DbWriter.delete_ruling() directly (missing-event rejection, atomic delete +
event, rollback on FK failure), but several router-level HTTP scenarios had
no test anywhere in services/db-gateway/tests/ (verified via a full
literal-ID grep):

- CSTAT-01: valid status update via the HTTP endpoint
- CSTAT-03: missing `status` field is rejected (400 missing_field) —
  validated by routers/court.py's `validate_required_fields(data, ["status"])`
  before either constraints or the event are even looked at.
- CSTAT-04/CSTAT-05: not-found nuance for a nonexistent claim_id, with vs.
  without constraints (same not_found/claim_not_found split documented for
  CAS-04/CAS-05, confirmed by reading
  services/db_gateway_service/services/court_writer.py's
  update_claim_status()).
- CSTAT-06/CSTAT-07: constraints match / mismatch on the status endpoint.
- CSTAT-08: a successful status update and its event row are atomic — both
  the `court_claims.status` change and the matching `events` row exist, and
  the response `event_id` matches the row.

NOTE (SUSPECTED-BUG, found while probing this area, not itself one of the 236
spec IDs): `update_claim_status()` in
services/db_gateway_service/services/court_writer.py does NOT catch
`sqlite3.IntegrityError` and translate it into a clean `ServiceError` the way
every sibling write method does (file_claim, submit_rebuttal, record_ruling,
and all board_writer/bank_writer methods all wrap the insert/update in
`except sqlite3.IntegrityError as exc: ... is_foreign_key_violation(exc) ...
raise ServiceError("foreign_key_violation", ..., 409, ...)`). Empirically
verified with `TestClient(app, raise_server_exceptions=False)`: a status
update whose `event.agent_id` references a nonexistent agent returns a raw
`500 internal_error`, not `409 foreign_key_violation`. This violates the
project's SEC-05 rule ("No endpoint returns 500 for any documented error
scenario"). Not fixed here — flagged only, per the sweep's rule not to touch
app source for suspected bugs.
- RDEL-03: DELETE /court/rulings/{claim_id} with no `event` body is rejected
  (400 missing_field) at the router layer — test_write_integrity.py only
  calls DbWriter.delete_ruling() directly with an event already supplied, so
  it never exercises routers/court.py's validate_event() gate.

New file — no existing test file was modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


def _register_agent(client: TestClient, agent_id: str | None = None, name: str = "Test") -> str:
    aid = agent_id or f"a-{uuid4()}"
    client.post(
        "/identity/agents",
        json={
            "agent_id": aid,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        },
    )
    return aid


def _setup_funded_account(
    client: TestClient, agent_id: str | None = None, balance: int = 500, name: str = "Test"
) -> str:
    aid = _register_agent(client, agent_id, name=name)
    data: dict[str, Any] = {
        "account_id": aid,
        "balance": balance,
        "created_at": "2026-02-28T10:00:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        data["operation_id"] = f"op-{uuid4()}"
        data["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-02-28T10:00:00Z",
        }
    client.post("/bank/accounts", json=data)
    return aid


def _lock_escrow(
    client: TestClient, payer_id: str, amount: int = 100, task_id: str | None = None
) -> tuple[str, str]:
    tid = task_id or f"t-{uuid4()}"
    eid = f"esc-{uuid4()}"
    client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": f"op-{uuid4()}",
            "escrow_id": eid,
            "payer_account_id": payer_id,
            "amount": amount,
            "task_id": tid,
            "created_at": "2026-02-28T10:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=tid),
        },
    )
    return eid, tid


def _create_task(client: TestClient, poster_id: str | None = None) -> tuple[str, str, str]:
    pid = poster_id or _setup_funded_account(client)
    eid, tid = _lock_escrow(client, pid)
    client.post(
        "/board/tasks",
        json={
            "task_id": tid,
            "poster_id": pid,
            "title": "Test Task",
            "spec": "Build a login page",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 86400,
            "deadline_seconds": 172800,
            "review_deadline_seconds": 43200,
            "bidding_deadline": "2026-03-01T10:00:00Z",
            "escrow_id": eid,
            "created_at": "2026-02-28T10:15:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=tid),
        },
    )
    return tid, pid, eid


def _setup_court_prerequisites(client: TestClient) -> tuple[str, str, str]:
    """Create alice, bob, and a task. Returns (alice, bob, task_id)."""
    alice = _setup_funded_account(client, name="Alice")
    bob = _register_agent(client, name="Bob")
    tid, _pid, _eid = _create_task(client, poster_id=alice)
    return alice, bob, tid


def _file_claim(client: TestClient, task_id: str, claimant_id: str, respondent_id: str) -> str:
    claim_id = f"clm-{uuid4()}"
    client.post(
        "/court/claims",
        json={
            "claim_id": claim_id,
            "task_id": task_id,
            "claimant_id": claimant_id,
            "respondent_id": respondent_id,
            "reason": "The login page does not validate email format",
            "status": "filed",
            "filed_at": "2026-02-28T16:00:00Z",
            "event": make_event(source="court", event_type="claim.filed", task_id=task_id),
        },
    )
    return claim_id


def _record_ruling(client: TestClient, claim_id: str, task_id: str) -> str:
    ruling_id = f"rul-{uuid4()}"
    client.post(
        "/court/rulings",
        json={
            "ruling_id": ruling_id,
            "claim_id": claim_id,
            "task_id": task_id,
            "worker_pct": 70,
            "summary": "The specification was ambiguous about email validation",
            "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
            '"reasoning": "Spec did not mention email format"}]',
            "ruled_at": "2026-02-28T18:00:00Z",
            "event": make_event(
                source="court", event_type="ruling.delivered", task_id=task_id, agent_id=None
            ),
        },
    )
    return ruling_id


@pytest.mark.unit
class TestClaimStatusUpdateHttp:
    """CSTAT-01/CSTAT-03 through CSTAT-08."""

    def test_valid_status_update(self, app_with_writer: TestClient) -> None:
        """CSTAT-01: Valid status update returns 200 with the new status."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        response = app_with_writer.post(
            f"/court/claims/{claim_id}/status",
            json={
                "status": "rebuttal",
                "event": make_event(source="court", event_type="claim.status_changed", task_id=tid),
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rebuttal"
        assert "event_id" in data

    def test_missing_status_is_rejected(self, app_with_writer: TestClient) -> None:
        """CSTAT-03: Missing status field is rejected."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        response = app_with_writer.post(
            f"/court/claims/{claim_id}/status",
            json={
                "event": make_event(source="court", event_type="claim.status_changed", task_id=tid),
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "missing_field"

    def test_claim_not_found_without_constraints_uses_specific_code(
        self, app_with_writer: TestClient
    ) -> None:
        """CSTAT-04: Claim not found, no constraints -> claim_not_found."""
        response = app_with_writer.post(
            "/court/claims/clm-does-not-exist/status",
            json={
                "status": "ruled",
                "event": make_event(source="court", event_type="claim.status_changed"),
            },
        )
        assert response.status_code == 404
        assert response.json()["error"] == "claim_not_found"

    def test_claim_not_found_with_constraints_uses_generic_code(
        self, app_with_writer: TestClient
    ) -> None:
        """CSTAT-05: Same nonexistent claim_id, with constraints -> generic not_found."""
        response = app_with_writer.post(
            "/court/claims/clm-does-not-exist/status",
            json={
                "status": "ruled",
                "constraints": {"status": "filed"},
                "event": make_event(source="court", event_type="claim.status_changed"),
            },
        )
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_status_update_with_matching_constraints_succeeds(
        self, app_with_writer: TestClient
    ) -> None:
        """CSTAT-06: Status update with matching constraints succeeds."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        response = app_with_writer.post(
            f"/court/claims/{claim_id}/status",
            json={
                "status": "rebuttal",
                "constraints": {"status": "filed"},
                "event": make_event(source="court", event_type="claim.status_changed", task_id=tid),
            },
        )
        assert response.status_code == 200

    def test_status_update_rejected_when_constraints_mismatch(
        self, app_with_writer: TestClient
    ) -> None:
        """CSTAT-07: Status update rejected when constraints don't match."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        app_with_writer.post(
            f"/court/claims/{claim_id}/status",
            json={
                "status": "rebuttal",
                "event": make_event(source="court", event_type="claim.status_changed", task_id=tid),
            },
        )

        response = app_with_writer.post(
            f"/court/claims/{claim_id}/status",
            json={
                "status": "ruled",
                "constraints": {"status": "filed"},
                "event": make_event(source="court", event_type="claim.status_changed", task_id=tid),
            },
        )
        assert response.status_code == 409
        assert response.json()["error"] == "constraint_violation"

    def test_status_update_and_event_are_atomic_on_success(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """CSTAT-08: the status change and its event row both exist together,
        and the response event_id matches the row."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        response = app_with_writer.post(
            f"/court/claims/{claim_id}/status",
            json={
                "status": "rebuttal",
                "event": make_event(source="court", event_type="claim.status_changed", task_id=tid),
            },
        )
        assert response.status_code == 200
        event_id = response.json()["event_id"]

        status_row = db_writer._db.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        assert status_row is not None
        assert status_row[0] == "rebuttal"

        event_row = db_writer._db.execute(
            "SELECT event_id, event_type FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        assert event_row is not None
        assert event_row[1] == "claim.status_changed"


@pytest.mark.unit
class TestDeleteRulingRequiresEventHttp:
    """RDEL-03: DELETE /court/rulings/{claim_id} without an event body is rejected."""

    def test_missing_event_body_is_rejected(self, app_with_writer: TestClient) -> None:
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        _record_ruling(app_with_writer, claim_id, tid)

        response = app_with_writer.request("DELETE", f"/court/rulings/{claim_id}", json={})

        assert response.status_code == 400
        assert response.json()["error"] == "missing_field"
