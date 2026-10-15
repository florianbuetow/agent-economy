"""T-072 coverage-vs-spec-ID sweep: gap-fill tests for Category 21 (constraints).

Covers CAS-04, CAS-05, CAS-09, CAS-10, CAS-11 — scenarios from
docs/specifications/service-tests/db-gateway-service-tests.md that had no
existing test (verified via a full literal-ID grep across
services/db-gateway/tests/ and tests/integration/). The underlying app
behavior was read directly from services/db-gateway/src/db_gateway_service/
services/{board_writer,court_writer}.py and routers/helpers.py before writing
these — every assertion here matches code that was already present.

This is a brand-new file; no existing test file was modified (per project
convention: tests are acceptance tests, add new files instead of editing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


def _register_agent(client: TestClient, *, name: str) -> str:
    agent_id = f"a-{uuid4()}"
    response = client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-03-01T09:00:00Z",
            "event": make_event(),
        },
    )
    assert response.status_code == 201
    return agent_id


def _create_funded_account(client: TestClient, *, name: str, balance: int) -> str:
    agent_id = _register_agent(client, name=name)
    payload: dict[str, Any] = {
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2026-03-01T09:05:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        payload["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-03-01T09:05:00Z",
        }
    response = client.post("/bank/accounts", json=payload)
    assert response.status_code == 201
    return agent_id


def _create_task(client: TestClient, *, poster_id: str) -> tuple[str, str]:
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    lock_response = client.post(
        "/bank/escrow/lock",
        json={
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": 100,
            "task_id": task_id,
            "created_at": "2026-03-01T09:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=task_id),
        },
    )
    assert lock_response.status_code == 201

    task_response = client.post(
        "/board/tasks",
        json={
            "task_id": task_id,
            "poster_id": poster_id,
            "title": "Constraint gap test task",
            "spec": "Test optimistic concurrency gaps",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 3600,
            "deadline_seconds": 7200,
            "review_deadline_seconds": 1800,
            "bidding_deadline": "2026-03-01T10:00:00Z",
            "escrow_id": escrow_id,
            "created_at": "2026-03-01T09:12:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=task_id),
        },
    )
    assert task_response.status_code == 201
    return task_id, escrow_id


@pytest.mark.unit
class TestCas04Cas05NotFoundNuance:
    """CAS-04/CAS-05: error code for a missing task_id depends on constraints."""

    def test_constrained_update_on_missing_task_returns_generic_not_found(
        self, app_with_writer: TestClient
    ) -> None:
        """CAS-04: constraints on a task_id that never existed -> generic `not_found`.

        There is no pre-write existence check on this endpoint, so a
        constrained UPDATE that matches zero rows falls through to
        check_constraint_violation(), which queries the row and — finding
        none — raises the generic `not_found` code (not `task_not_found`).
        """
        response = app_with_writer.post(
            "/board/tasks/t-does-not-exist/status",
            json={
                "updates": {"status": "accepted"},
                "constraints": {"status": "open"},
                "event": make_event(
                    source="board", event_type="task.accepted", task_id="t-does-not-exist"
                ),
            },
        )
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_unconstrained_update_on_missing_task_returns_task_not_found(
        self, app_with_writer: TestClient
    ) -> None:
        """CAS-05: same nonexistent task_id, no constraints -> `task_not_found`.

        Without constraints, a zero-rowcount UPDATE skips the
        check_constraint_violation() path entirely and raises the
        endpoint-specific `task_not_found` code.
        """
        response = app_with_writer.post(
            "/board/tasks/t-does-not-exist/status",
            json={
                "updates": {"status": "accepted"},
                "event": make_event(
                    source="board", event_type="task.accepted", task_id="t-does-not-exist"
                ),
            },
        )
        assert response.status_code == 404
        assert response.json()["error"] == "task_not_found"


@pytest.mark.unit
class TestCas09AssetCrossTableConstraint:
    """CAS-09: Asset recording with a cross-table constraint."""

    def test_asset_recording_rejected_when_task_no_longer_open(
        self, app_with_writer: TestClient
    ) -> None:
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        uploader_id = _register_agent(app_with_writer, name="Uploader")
        task_id, _escrow_id = _create_task(app_with_writer, poster_id=poster_id)

        cancel_response = app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "cancelled"},
                "event": make_event(source="board", event_type="task.cancelled", task_id=task_id),
            },
        )
        assert cancel_response.status_code == 200

        response = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": f"asset-{uuid4()}",
                "task_id": task_id,
                "uploader_id": uploader_id,
                "filename": "deliverable.zip",
                "content_type": "application/zip",
                "size_bytes": 1024,
                "storage_path": f"{task_id}/deliverable.zip",
                "uploaded_at": "2026-03-01T09:40:00Z",
                "constraints": {"status": "open"},
                "event": make_event(source="board", event_type="asset.uploaded", task_id=task_id),
            },
        )
        assert response.status_code == 409
        assert response.json()["error"] == "constraint_violation"


@pytest.mark.unit
class TestCas10RebuttalConstraint:
    """CAS-10: Rebuttal claim-status update honors constraints."""

    def test_rebuttal_with_matching_claim_status_constraint_succeeds(
        self, app_with_writer: TestClient
    ) -> None:
        alice = _create_funded_account(app_with_writer, name="Alice", balance=500)
        bob = _register_agent(app_with_writer, name="Bob")
        task_id, _escrow_id = _create_task(app_with_writer, poster_id=alice)

        claim_id = f"clm-{uuid4()}"
        claim_response = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": claim_id,
                "task_id": task_id,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "Work was not delivered",
                "status": "filed",
                "filed_at": "2026-03-01T10:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=task_id),
            },
        )
        assert claim_response.status_code == 201

        response = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "The work was delivered on time",
                "submitted_at": "2026-03-01T11:00:00Z",
                "claim_status_update": "rebuttal",
                "constraints": {"status": "filed"},
                "event": make_event(
                    source="court", event_type="rebuttal.submitted", task_id=task_id
                ),
            },
        )
        assert response.status_code == 201

        _wire_db_reader()
        claim_check = app_with_writer.get(f"/court/claims/{claim_id}")
        assert claim_check.status_code == 200
        assert claim_check.json()["status"] == "rebuttal"


@pytest.mark.unit
class TestCas07StaleConstraintOnAlreadyResolvedEscrow:
    """CAS-07: escrow split with a stale `{status: "locked"}` constraint on an
    already-released escrow. Adjudicated WP-13 (T-072 sweep, see the spec row):
    `escrow_already_resolved` wins over `constraint_violation` here — the
    unconditional resolved-check in `_load_escrow()` runs before any
    `constraints` handling can, for every escrow-lock-consuming endpoint."""

    def test_split_with_stale_locked_constraint_returns_escrow_already_resolved(
        self, app_with_writer: TestClient
    ) -> None:
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=100)
        worker_id = _create_funded_account(app_with_writer, name="Worker", balance=0)
        task_id, escrow_id = _create_task(app_with_writer, poster_id=poster_id)

        release_response = app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": escrow_id,
                "recipient_account_id": worker_id,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-03-01T09:20:00Z",
                "event": make_event(source="bank", event_type="escrow.released", task_id=task_id),
            },
        )
        assert release_response.status_code == 200

        split_response = app_with_writer.post(
            "/bank/escrow/split",
            json={
                "escrow_id": escrow_id,
                "worker_account_id": worker_id,
                "poster_account_id": poster_id,
                "worker_amount": 60,
                "poster_amount": 40,
                "worker_tx_id": f"tx-{uuid4()}",
                "poster_tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-03-01T09:21:00Z",
                "constraints": {"status": "locked"},
                "event": make_event(source="bank", event_type="escrow.split", task_id=task_id),
            },
        )
        assert split_response.status_code == 409
        assert split_response.json()["error"] == "escrow_already_resolved"


@pytest.mark.unit
class TestCas11InvalidConstraintsType:
    """CAS-11: constraints value present but not an object is rejected."""

    def test_string_constraints_value_is_rejected(self, app_with_writer: TestClient) -> None:
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, _escrow_id = _create_task(app_with_writer, poster_id=poster_id)

        response = app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "accepted"},
                "constraints": "open",
                "event": make_event(source="board", event_type="task.accepted", task_id=task_id),
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_constraints"
