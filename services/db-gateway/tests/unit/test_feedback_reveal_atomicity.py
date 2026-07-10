"""Atomic sealed-feedback reveal — the gateway owns the reveal policy (GAP-A5).

The mutual-reveal check and the visibility update must happen inside the single
BEGIN IMMEDIATE transaction that writes the feedback row. A caller that reads the
reverse pair first and then tells the gateway what to reveal loses the race: two
concurrent counter-feedbacks both observe "no reverse yet" and both persist sealed.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.services.db_writer import DbWriter
from tests.conftest import make_event

if TYPE_CHECKING:
    from collections.abc import Iterator

BUSY_TIMEOUT_MS = 5000
JOURNAL_MODE = "wal"


# ---------------------------------------------------------------------------
# Seeding — raw inserts satisfy the FK chain
# identity_agents -> bank_accounts -> bank_escrow -> board_tasks
# ---------------------------------------------------------------------------


def _seed_agent(conn: sqlite3.Connection, agent_id: str, name: str) -> None:
    conn.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, ?)",
        (agent_id, name, f"ed25519:{uuid4()}", "2026-02-28T10:00:00Z"),
    )


def _seed_task(conn: sqlite3.Connection, task_id: str, poster_id: str) -> None:
    escrow_id = f"esc-{uuid4()}"
    conn.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
        (poster_id, 500, "2026-02-28T10:00:00Z"),
    )
    conn.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (escrow_id, poster_id, 100, task_id, "locked", "2026-02-28T10:10:00Z"),
    )
    conn.execute(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, escrow_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            poster_id,
            "Test Task",
            "Build a login page",
            100,
            "approved",
            86400,
            172800,
            43200,
            "2026-03-01T10:00:00Z",
            escrow_id,
            "2026-02-28T10:15:00Z",
        ),
    )


class _Fixture:
    """Agent ids and a task id seeded into the database under test."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.alice = f"a-{uuid4()}"
        self.bob = f"a-{uuid4()}"
        self.carol = f"a-{uuid4()}"
        self.task_id = f"t-{uuid4()}"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        _seed_agent(conn, self.alice, "Alice")
        _seed_agent(conn, self.bob, "Bob")
        _seed_agent(conn, self.carol, "Carol")
        _seed_task(conn, self.task_id, self.alice)
        conn.commit()
        conn.close()


@pytest.fixture
def seeded(initialized_db: str) -> _Fixture:
    return _Fixture(initialized_db)


@pytest.fixture
def writer(seeded: _Fixture) -> Iterator[DbWriter]:
    w = DbWriter(
        db_path=seeded.db_path,
        busy_timeout_ms=BUSY_TIMEOUT_MS,
        journal_mode=JOURNAL_MODE,
        schema_sql=None,
    )
    yield w
    w.close()


def _payload(
    task_id: str,
    from_agent_id: str,
    to_agent_id: str,
    *,
    role: str,
    category: str,
    submitted_at: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "feedback_id": f"fb-{uuid4()}",
        "task_id": task_id,
        "from_agent_id": from_agent_id,
        "to_agent_id": to_agent_id,
        "role": role,
        "category": category,
        "rating": "satisfied",
        "comment": "ok",
        "submitted_at": submitted_at,
        "event": make_event(
            source="reputation",
            event_type="feedback.submitted",
            task_id=task_id,
            agent_id=from_agent_id,
        ),
    }
    if extra is not None:
        body.update(extra)
    return body


def _poster_payload(f: _Fixture, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Alice (poster) rates Bob's delivery."""
    return _payload(
        f.task_id,
        f.alice,
        f.bob,
        role="poster",
        category="delivery_quality",
        submitted_at="2026-02-28T15:00:00Z",
        extra=extra,
    )


def _worker_payload(f: _Fixture, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bob (worker) rates Alice's spec — the reverse of _poster_payload."""
    return _payload(
        f.task_id,
        f.bob,
        f.alice,
        role="worker",
        category="spec_quality",
        submitted_at="2026-02-28T15:30:00Z",
        extra=extra,
    )


def _visible(db_path: str, feedback_id: str) -> int:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT visible FROM reputation_feedback WHERE feedback_id = ?",
        (feedback_id,),
    ).fetchone()
    conn.close()
    assert row is not None
    return int(row[0])


def _revealed_event_count(db_path: str, task_id: str) -> int:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT COUNT(*) FROM events "
        "WHERE event_source = 'reputation' AND event_type = 'feedback.revealed' AND task_id = ?",
        (task_id,),
    ).fetchone()
    conn.close()
    return int(row[0])


# ---------------------------------------------------------------------------
# Sealed / reveal semantics — the gateway decides, not the caller
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGatewayOwnedReveal:
    def test_first_feedback_stays_sealed(self, writer: DbWriter, seeded: _Fixture) -> None:
        """One-sided feedback has no reverse pair, so it persists sealed."""
        body = _poster_payload(seeded)

        result = writer.submit_feedback(body)

        assert result["visible"] is False
        assert _visible(seeded.db_path, body["feedback_id"]) == 0
        assert _revealed_event_count(seeded.db_path, seeded.task_id) == 0

    def test_counter_feedback_reveals_both_rows(self, writer: DbWriter, seeded: _Fixture) -> None:
        """The gateway finds the reverse pair itself and flips both rows."""
        first = _poster_payload(seeded)
        writer.submit_feedback(first)

        second = _worker_payload(seeded)
        result = writer.submit_feedback(second)

        assert result["visible"] is True
        assert _visible(seeded.db_path, first["feedback_id"]) == 1
        assert _visible(seeded.db_path, second["feedback_id"]) == 1

    def test_stale_caller_reveal_flag_is_ignored(self, writer: DbWriter, seeded: _Fixture) -> None:
        """Two racing callers both read 'no reverse yet' and send reveal_reverse=False.

        The gateway must reveal anyway — the caller's view of the reverse pair is
        stale by construction. This is the client-side race, made deterministic.
        """
        first = _poster_payload(seeded, extra={"reveal_reverse": False})
        writer.submit_feedback(first)

        second = _worker_payload(seeded, extra={"reveal_reverse": False})
        result = writer.submit_feedback(second)

        assert result["visible"] is True
        assert _visible(seeded.db_path, first["feedback_id"]) == 1
        assert _visible(seeded.db_path, second["feedback_id"]) == 1

    def test_unrelated_pair_is_unaffected(self, writer: DbWriter, seeded: _Fixture) -> None:
        """Visibility is per-pair: carol->bob does not reveal alice->bob."""
        alice_to_bob = _poster_payload(seeded)
        writer.submit_feedback(alice_to_bob)

        carol_to_bob = _payload(
            seeded.task_id,
            seeded.carol,
            seeded.bob,
            role="poster",
            category="delivery_quality",
            submitted_at="2026-02-28T15:10:00Z",
        )
        result = writer.submit_feedback(carol_to_bob)

        assert result["visible"] is False
        assert _visible(seeded.db_path, carol_to_bob["feedback_id"]) == 0
        assert _visible(seeded.db_path, alice_to_bob["feedback_id"]) == 0

        # And once the true reverse lands, only the alice/bob pair flips.
        bob_to_alice = _worker_payload(seeded)
        writer.submit_feedback(bob_to_alice)

        assert _visible(seeded.db_path, alice_to_bob["feedback_id"]) == 1
        assert _visible(seeded.db_path, bob_to_alice["feedback_id"]) == 1
        assert _visible(seeded.db_path, carol_to_bob["feedback_id"]) == 0

    def test_platform_feedback_is_visible_without_a_reverse(
        self, writer: DbWriter, seeded: _Fixture
    ) -> None:
        """force_visible is a caller policy: the new row is visible, nothing else changes.

        No reverse pair exists, so no other row is touched and no reveal is announced.
        """
        carol_to_bob = _payload(
            seeded.task_id,
            seeded.carol,
            seeded.bob,
            role="poster",
            category="delivery_quality",
            submitted_at="2026-02-28T14:00:00Z",
        )
        writer.submit_feedback(carol_to_bob)

        body = _poster_payload(seeded, extra={"force_visible": True})
        result = writer.submit_feedback(body)

        assert result["visible"] is True
        assert _visible(seeded.db_path, body["feedback_id"]) == 1
        assert _visible(seeded.db_path, carol_to_bob["feedback_id"]) == 0
        assert _revealed_event_count(seeded.db_path, seeded.task_id) == 0

    def test_platform_feedback_still_reveals_an_existing_reverse(
        self, writer: DbWriter, seeded: _Fixture
    ) -> None:
        """The two meanings compose: force_visible does not suppress the pair reveal.

        A sealed counterpart already exists, so the gateway flips it too — the reverse
        lookup is a fact about the database, independent of the caller's policy flag.
        """
        sealed = _poster_payload(seeded)
        writer.submit_feedback(sealed)
        assert _visible(seeded.db_path, sealed["feedback_id"]) == 0

        platform = _worker_payload(seeded, extra={"force_visible": True})
        result = writer.submit_feedback(platform)

        assert result["visible"] is True
        assert _visible(seeded.db_path, platform["feedback_id"]) == 1
        assert _visible(seeded.db_path, sealed["feedback_id"]) == 1
        assert _revealed_event_count(seeded.db_path, seeded.task_id) == 1

    def test_reveal_is_idempotent(self, writer: DbWriter, seeded: _Fixture) -> None:
        """Resubmitting a revealed pair is rejected and does not re-emit the event."""
        first = _poster_payload(seeded)
        writer.submit_feedback(first)
        second = _worker_payload(seeded)
        writer.submit_feedback(second)

        assert _revealed_event_count(seeded.db_path, seeded.task_id) == 1

        duplicate = _worker_payload(seeded)
        with pytest.raises(ServiceError) as exc_info:
            writer.submit_feedback(duplicate)

        assert exc_info.value.error == "feedback_exists"
        assert _revealed_event_count(seeded.db_path, seeded.task_id) == 1
        assert _visible(seeded.db_path, first["feedback_id"]) == 1
        assert _visible(seeded.db_path, second["feedback_id"]) == 1

    def test_reveal_emits_one_feedback_revealed_event(
        self, writer: DbWriter, seeded: _Fixture
    ) -> None:
        """The reveal emits exactly one feedback.revealed event carrying both names."""
        writer.submit_feedback(_poster_payload(seeded))
        writer.submit_feedback(_worker_payload(seeded))

        conn = sqlite3.connect(seeded.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT agent_id, payload FROM events "
            "WHERE event_source = 'reputation' AND event_type = 'feedback.revealed'",
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0]["agent_id"] == seeded.bob
        payload = rows[0]["payload"]
        assert "Alice" in payload
        assert "Bob" in payload


# ---------------------------------------------------------------------------
# Concurrency proof
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConcurrentReveal:
    def test_concurrent_counter_feedback_never_leaves_both_sealed(self, seeded: _Fixture) -> None:
        """Two counter-feedbacks written concurrently over separate connections.

        BEGIN IMMEDIATE serializes the two transactions, so whichever commits second
        sees the first row and reveals the pair. The both-sealed outcome is impossible.
        """
        poster_body = _poster_payload(seeded)
        worker_body = _worker_payload(seeded)

        writers = [
            DbWriter(
                db_path=seeded.db_path,
                busy_timeout_ms=BUSY_TIMEOUT_MS,
                journal_mode=JOURNAL_MODE,
                schema_sql=None,
            )
            for _ in range(2)
        ]
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def run(writer: DbWriter, body: dict[str, Any]) -> None:
            barrier.wait()
            try:
                writer.submit_feedback(body)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=run, args=(writers[0], poster_body)),
            threading.Thread(target=run, args=(writers[1], worker_body)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        for writer in writers:
            writer.close()

        assert errors == [], f"concurrent submit_feedback raised: {errors!r}"
        assert _visible(seeded.db_path, poster_body["feedback_id"]) == 1
        assert _visible(seeded.db_path, worker_body["feedback_id"]) == 1
        assert _revealed_event_count(seeded.db_path, seeded.task_id) == 1
