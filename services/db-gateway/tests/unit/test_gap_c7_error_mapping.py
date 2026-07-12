"""GAP-C7 — sqlite_errorcode-based constraint mapping.

Covers three sub-issues:

1. FK/UNIQUE violations must be classified via `sqlite3.IntegrityError.sqlite_errorcode`
   constants, never by substring-matching the driver's message text. The message text
   embeds real table/column names straight from the schema (SEC-02): anchoring behavior
   to it means every branch has to inspect that text, which is exactly the leak this
   closes. The regression guard below asserts db_writer.py no longer touches
   `str(exc)` for classification at all.
2. `file_claim`'s IntegrityError handler previously reported `claim_exists` for BOTH
   a genuine duplicate claim_id (the claim_id PRIMARY KEY) and a second claim filed
   against an already-disputed task (the task_id UNIQUE index) — the message for the
   latter never even mentions "claim". These need distinct, accurate error codes.
3. `/health`'s `database_size_bytes` only stat'd the main db file, undercounting a
   database in WAL mode whose recent writes live in the `-wal` file (and `-shm`) until
   the next checkpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service.services.db_writer import DbWriter
from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from httpx import Response

_DB_WRITER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "db_gateway_service"
    / "services"
    / "db_writer.py"
)


# ---------------------------------------------------------------------------
# Sub-issue 1 — no more driver-message substring matching
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoDriverMessageInspection:
    """db_writer.py must classify IntegrityError purely via sqlite_errorcode."""

    def test_db_writer_never_inspects_the_raw_exception_string(self) -> None:
        source = _DB_WRITER_PATH.read_text()
        assert "str(exc)" not in source, (
            "db_writer.py still inspects the raw IntegrityError message text; "
            "classify via exc.sqlite_errorcode instead (SEC-02: the message text "
            "contains real table/column names)"
        )

    def test_db_writer_uses_sqlite_errorcode_constants(self) -> None:
        source = _DB_WRITER_PATH.read_text()
        assert "sqlite_errorcode" in source
        assert "SQLITE_CONSTRAINT_FOREIGNKEY" in source


# ---------------------------------------------------------------------------
# Sub-issue 2 — file_claim distinguishes claim_id vs task_id collisions
# ---------------------------------------------------------------------------


def _register_agent(client: TestClient, name: str) -> str:
    agent_id = f"a-{uuid4()}"
    resp = client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-03-01T09:00:00Z",
            "event": make_event(),
        },
    )
    assert resp.status_code == 201
    return agent_id


def _create_funded_account(client: TestClient, name: str, balance: int) -> str:
    agent_id = _register_agent(client, name)
    payload = {
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2026-03-01T09:05:00Z",
        "event": make_event(source="bank", event_type="account.created"),
        "initial_credit": {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-03-01T09:05:00Z",
        },
    }
    resp = client.post("/bank/accounts", json=payload)
    assert resp.status_code == 201
    return agent_id


def _create_task(client: TestClient, poster_id: str) -> str:
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    lock = client.post(
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
    assert lock.status_code == 201
    created = client.post(
        "/board/tasks",
        json={
            "task_id": task_id,
            "poster_id": poster_id,
            "title": "Dispute test task",
            "spec": "Build a thing",
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
    assert created.status_code == 201
    return task_id


def _file_claim(
    client: TestClient,
    task_id: str,
    claimant: str,
    respondent: str,
    claim_id: str,
) -> Response:
    return client.post(
        "/court/claims",
        json={
            "claim_id": claim_id,
            "task_id": task_id,
            "claimant_id": claimant,
            "respondent_id": respondent,
            "reason": "Spec was not met",
            "status": "filed",
            "filed_at": "2026-03-01T16:00:00Z",
            "event": make_event(source="court", event_type="claim.filed", task_id=task_id),
        },
    )


@pytest.mark.unit
class TestFileClaimDistinguishesTaskFromClaimCollision:
    """A second claim against an already-disputed task is not a claim_id collision."""

    def test_second_claim_against_disputed_task_is_not_claim_exists(
        self, app_with_writer: TestClient
    ) -> None:
        poster = _create_funded_account(app_with_writer, "Poster", balance=500)
        worker = _register_agent(app_with_writer, "Worker")
        task_id = _create_task(app_with_writer, poster)

        first = _file_claim(app_with_writer, task_id, poster, worker, claim_id=f"clm-{uuid4()}")
        assert first.status_code == 201

        second = _file_claim(app_with_writer, task_id, poster, worker, claim_id=f"clm-{uuid4()}")

        assert second.status_code == 409
        body = second.json()
        assert body["error"] == "task_already_disputed", (
            "A second claim against the same task (different claim_id) hit the "
            "task_id UNIQUE index, not a claim_id collision — it must not be "
            "reported as claim_exists"
        )

    def test_duplicate_claim_id_is_still_claim_exists(self, app_with_writer: TestClient) -> None:
        """Regression: the real claim_id collision (frozen CLM-02 scenario) is unaffected."""
        poster = _create_funded_account(app_with_writer, "Poster", balance=500)
        worker = _register_agent(app_with_writer, "Worker")
        task_id = _create_task(app_with_writer, poster)
        claim_id = f"clm-{uuid4()}"

        first = _file_claim(app_with_writer, task_id, poster, worker, claim_id=claim_id)
        assert first.status_code == 201

        second = _file_claim(app_with_writer, task_id, poster, worker, claim_id=claim_id)

        assert second.status_code == 409
        assert second.json()["error"] == "claim_exists"


# ---------------------------------------------------------------------------
# Sub-issue 3 — /health database size includes -wal and -shm sidecar files
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDatabaseSizeIncludesWalAndShm:
    """get_database_size_bytes must count the -wal/-shm sidecar files in WAL mode."""

    def test_size_matches_main_plus_wal_plus_shm(self, tmp_db_path: str, schema_sql: str) -> None:
        writer = DbWriter(
            db_path=tmp_db_path,
            busy_timeout_ms=5000,
            journal_mode="wal",
            schema_sql=schema_sql,
        )
        try:
            agent_id = f"a-{uuid4()}"
            writer.register_agent(
                {
                    "agent_id": agent_id,
                    "name": "Alice",
                    "public_key": f"ed25519:{uuid4()}",
                    "registered_at": "2026-03-01T10:00:00Z",
                    "event": make_event(agent_id=agent_id),
                }
            )

            wal_path = Path(f"{tmp_db_path}-wal")
            shm_path = Path(f"{tmp_db_path}-shm")
            assert wal_path.exists(), "expected a -wal sidecar file under wal journal_mode"

            expected = Path(tmp_db_path).stat().st_size + wal_path.stat().st_size
            if shm_path.exists():
                expected += shm_path.stat().st_size

            assert writer.get_database_size_bytes() == expected
        finally:
            writer.close()
