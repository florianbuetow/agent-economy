"""Shared test configuration and seed data fixtures."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# conftest.py -> tests -> observatory -> services -> repo_root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCHEMA_SQL_PATH = _REPO_ROOT / "docs" / "specifications" / "schema.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ts(dt: datetime) -> str:
    """Format a datetime as ISO 8601 UTC with trailing Z."""
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_task(conn, params):
    """Insert a single board_tasks row."""
    conn.execute(
        """INSERT INTO board_tasks (
            task_id, poster_id, title, spec, reward, status,
            bidding_deadline_seconds, deadline_seconds,
            review_deadline_seconds,
            bidding_deadline, execution_deadline, review_deadline,
            escrow_id, worker_id, accepted_bid_id,
            dispute_reason, ruling_id, worker_pct,
            ruling_summary,
            created_at, accepted_at, submitted_at, approved_at,
            cancelled_at, disputed_at, ruled_at, expired_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?
        )""",
        params,
    )


# ---------------------------------------------------------------------------
# schema_sql fixture
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def schema_sql() -> str:
    """Read the shared schema.sql from the repository root."""
    return _SCHEMA_SQL_PATH.read_text()


# ---------------------------------------------------------------------------
# empty_db_path -- schema only, no data
# ---------------------------------------------------------------------------
@pytest.fixture
def empty_db_path(tmp_path: Path, schema_sql: str) -> Path:
    """Create a temporary SQLite database with schema but NO data."""
    db_file = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(schema_sql)
    conn.close()
    return db_file


# ---------------------------------------------------------------------------
# seeded_db_path -- schema + standard economy seed data
# ---------------------------------------------------------------------------
@pytest.fixture
def seeded_db_path(tmp_path: Path, schema_sql: str) -> Path:
    """Create a temp SQLite database with the standard economy seed data.

    Seed data
    ---------
    - 3 agents: Alice, Bob, Charlie
    - 5 tasks in various lifecycle states
    - Bids, escrow, feedback, court claims, bank txns
    - 15 events covering the full economy activity

    Balance derivations (chronological)
    ------------------------------------
    Alice: 1000 - 100(t1) - 120(t5) - 50(t2) + 36(t5 split) = 766
    Bob:   1000 + 100(t1)  - 80(t3)  + 84(t5 split)          = 1104
    Charlie: 1000 + 50(t2) - 60(t4)                           = 990
    """
    db_file = tmp_path / "seeded.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(schema_sql)

    now = datetime.now(UTC)

    # Relative timestamps
    t_3h = now - timedelta(hours=3)
    t_2h30m = now - timedelta(hours=2, minutes=30)
    t_2h = now - timedelta(hours=2)
    t_1h30m = now - timedelta(hours=1, minutes=30)
    t_1h = now - timedelta(hours=1)
    t_45m = now - timedelta(minutes=45)
    t_30m = now - timedelta(minutes=30)
    t_15m = now - timedelta(minutes=15)

    # ==================================================================
    # AGENTS
    # ==================================================================
    conn.executemany(
        "INSERT INTO identity_agents "
        "(agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, ?)",
        [
            ("a-alice", "Alice", "ed25519:alice-key", _ts(t_3h)),
            ("a-bob", "Bob", "ed25519:bob-key", _ts(t_3h)),
            ("a-charlie", "Charlie", "ed25519:charlie-key", _ts(t_3h)),
        ],
    )

    # ==================================================================
    # BANK ACCOUNTS
    # ==================================================================
    conn.executemany(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
        [
            ("a-alice", 766, _ts(t_3h)),
            ("a-bob", 1104, _ts(t_3h)),
            ("a-charlie", 990, _ts(t_3h)),
        ],
    )

    # ==================================================================
    # ESCROW
    # ==================================================================
    conn.executemany(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, "
        "status, created_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            # t-1: released (approved, Bob got paid)
            ("esc-1", "a-alice", 100, "t-1", "released", _ts(t_2h30m), _ts(t_2h)),
            # t-2: released (approved, Charlie got paid)
            ("esc-2", "a-alice", 50, "t-2", "released", _ts(t_1h30m), _ts(t_1h)),
            # t-3: locked (in execution)
            ("esc-3", "a-bob", 80, "t-3", "locked", _ts(t_45m), None),
            # t-4: locked (open for bidding)
            ("esc-4", "a-charlie", 60, "t-4", "locked", _ts(t_15m), None),
            # t-5: split (disputed + ruled, worker_pct=70)
            ("esc-5", "a-alice", 120, "t-5", "split", _ts(t_2h30m), _ts(t_30m)),
        ],
    )

    # ==================================================================
    # TASKS
    # ==================================================================
    bd = timedelta(seconds=3600)  # bidding deadline offset
    ex = timedelta(seconds=7200)  # execution deadline offset
    rv = timedelta(seconds=3600)  # review deadline offset

    # t-1: Alice -> Bob, reward 100, approved 2h ago
    _insert_task(
        conn,
        (
            "t-1",
            "a-alice",
            "Build login page",
            "Create a responsive login page with OAuth",
            100,
            "approved",
            3600,
            7200,
            3600,
            _ts(t_2h30m + bd),
            _ts(t_2h30m + ex),
            _ts(t_2h30m + rv),
            "esc-1",
            "a-bob",
            "bid-1",
            None,
            None,
            None,
            None,
            _ts(t_2h30m),
            _ts(t_2h30m + timedelta(minutes=10)),
            _ts(t_2h - timedelta(minutes=10)),
            _ts(t_2h),
            None,
            None,
            None,
            None,
        ),
    )

    # t-2: Alice -> Charlie, reward 50, approved 1h ago
    _insert_task(
        conn,
        (
            "t-2",
            "a-alice",
            "Write unit tests",
            "Write comprehensive unit tests for auth module",
            50,
            "approved",
            3600,
            7200,
            3600,
            _ts(t_1h30m + bd),
            _ts(t_1h30m + ex),
            _ts(t_1h30m + rv),
            "esc-2",
            "a-charlie",
            "bid-4",
            None,
            None,
            None,
            None,
            _ts(t_1h30m),
            _ts(t_1h30m + timedelta(minutes=10)),
            _ts(t_1h - timedelta(minutes=10)),
            _ts(t_1h),
            None,
            None,
            None,
            None,
        ),
    )

    # t-3: Bob -> Charlie, reward 80, accepted 30m ago
    _insert_task(
        conn,
        (
            "t-3",
            "a-bob",
            "Design API schema",
            "Design RESTful API schema for task board",
            80,
            "accepted",
            3600,
            7200,
            3600,
            _ts(t_45m + bd),
            _ts(t_30m + ex),
            None,
            "esc-3",
            "a-charlie",
            "bid-6",
            None,
            None,
            None,
            None,
            _ts(t_45m),
            _ts(t_30m),
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    )

    # t-4: Charlie, open, reward 60, created 15m ago
    _insert_task(
        conn,
        (
            "t-4",
            "a-charlie",
            "Implement caching",
            "Add Redis caching layer for frequent data",
            60,
            "open",
            3600,
            7200,
            3600,
            _ts(t_15m + bd),
            None,
            None,
            "esc-4",
            None,
            None,
            None,
            None,
            None,
            None,
            _ts(t_15m),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    )

    # t-5: Alice -> Bob, reward 120, ruled 30m ago, worker_pct=70
    ruling_reason = "Deliverable does not meet spec requirements for connection pooling"
    ruling_summary = (
        "Worker completed 70% of requirements. Connection pooling was partially implemented."
    )
    _insert_task(
        conn,
        (
            "t-5",
            "a-alice",
            "Refactor database layer",
            "Refactor the DB layer to use connection pooling",
            120,
            "ruled",
            3600,
            7200,
            3600,
            _ts(t_2h30m + bd),
            _ts(t_2h30m + ex),
            _ts(t_2h30m + rv),
            "esc-5",
            "a-bob",
            "bid-7",
            ruling_reason,
            "rul-1",
            70,
            ruling_summary,
            _ts(t_2h30m),
            _ts(t_2h30m + timedelta(minutes=10)),
            _ts(t_1h),
            None,
            None,
            _ts(t_45m),
            _ts(t_30m),
            None,
        ),
    )

    # ==================================================================
    # BIDS
    # ==================================================================
    conn.executemany(
        "INSERT INTO board_bids "
        "(bid_id, task_id, bidder_id, proposal, submitted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            # t-1: 3 bids (Bob won)
            (
                "bid-1",
                "t-1",
                "a-bob",
                "I can build a login page with OAuth",
                _ts(t_2h30m + timedelta(minutes=2)),
            ),
            (
                "bid-2",
                "t-1",
                "a-charlie",
                "I have experience with auth UIs",
                _ts(t_2h30m + timedelta(minutes=3)),
            ),
            (
                "bid-3",
                "t-1",
                "a-alice",
                "I can deliver this quickly",
                _ts(t_2h30m + timedelta(minutes=4)),
            ),
            # t-2: 2 bids (Charlie won)
            (
                "bid-4",
                "t-2",
                "a-charlie",
                "I specialize in testing frameworks",
                _ts(t_1h30m + timedelta(minutes=2)),
            ),
            (
                "bid-5",
                "t-2",
                "a-bob",
                "I can write thorough tests",
                _ts(t_1h30m + timedelta(minutes=3)),
            ),
            # t-3: 1 bid (Charlie, accepted)
            (
                "bid-6",
                "t-3",
                "a-charlie",
                "I have API design experience",
                _ts(t_45m + timedelta(minutes=2)),
            ),
            # t-5: 1 bid (Bob, accepted)
            (
                "bid-7",
                "t-5",
                "a-bob",
                "I can refactor the database layer",
                _ts(t_2h30m + timedelta(minutes=2)),
            ),
            # t-4: 2 bids (pending)
            (
                "bid-8",
                "t-4",
                "a-alice",
                "I can implement Redis caching",
                _ts(t_15m + timedelta(minutes=2)),
            ),
            (
                "bid-9",
                "t-4",
                "a-bob",
                "I have caching expertise",
                _ts(t_15m + timedelta(minutes=3)),
            ),
        ],
    )

    # ==================================================================
    # FEEDBACK
    # ==================================================================
    conn.executemany(
        "INSERT INTO reputation_feedback "
        "(feedback_id, task_id, from_agent_id, to_agent_id, "
        "role, category, rating, comment, submitted_at, visible) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # t-1 visible (both submitted -> revealed)
            (
                "fb-1",
                "t-1",
                "a-alice",
                "a-bob",
                "poster",
                "delivery_quality",
                "satisfied",
                "Good work on the login page",
                _ts(t_2h + timedelta(minutes=5)),
                1,
            ),
            (
                "fb-2",
                "t-1",
                "a-bob",
                "a-alice",
                "worker",
                "spec_quality",
                "extremely_satisfied",
                "Clear and detailed spec",
                _ts(t_2h + timedelta(minutes=6)),
                1,
            ),
            # t-2 visible (both submitted -> revealed)
            (
                "fb-3",
                "t-2",
                "a-alice",
                "a-charlie",
                "poster",
                "delivery_quality",
                "extremely_satisfied",
                "Excellent test coverage",
                _ts(t_1h + timedelta(minutes=5)),
                1,
            ),
            (
                "fb-4",
                "t-2",
                "a-charlie",
                "a-alice",
                "worker",
                "spec_quality",
                "satisfied",
                "Spec was reasonable",
                _ts(t_1h + timedelta(minutes=6)),
                1,
            ),
            # t-5 sealed (dispute scenario)
            (
                "fb-5",
                "t-5",
                "a-alice",
                "a-bob",
                "poster",
                "delivery_quality",
                "dissatisfied",
                "Did not meet requirements",
                _ts(t_45m + timedelta(minutes=2)),
                0,
            ),
            (
                "fb-6",
                "t-5",
                "a-bob",
                "a-alice",
                "worker",
                "spec_quality",
                "dissatisfied",
                "Spec was unclear about pooling requirements",
                _ts(t_45m + timedelta(minutes=3)),
                0,
            ),
        ],
    )

    # ==================================================================
    # COURT -- 1 claim on t-5 with rebuttal and ruling
    # ==================================================================
    conn.execute(
        "INSERT INTO court_claims "
        "(claim_id, task_id, claimant_id, respondent_id, "
        "reason, status, filed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("clm-1", "t-5", "a-alice", "a-bob", ruling_reason, "ruled", _ts(t_45m)),
    )

    conn.execute(
        "INSERT INTO court_rebuttals "
        "(rebuttal_id, claim_id, agent_id, content, submitted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "reb-1",
            "clm-1",
            "a-bob",
            "I implemented connection pooling as described. "
            "The spec was ambiguous about pool size limits.",
            _ts(t_45m + timedelta(minutes=5)),
        ),
    )

    judge_votes = json.dumps(
        [
            {"judge": "judge-1", "worker_pct": 70, "reason": "Partial implementation"},
            {"judge": "judge-2", "worker_pct": 65, "reason": "Missing pool limits"},
            {"judge": "judge-3", "worker_pct": 75, "reason": "Core functionality present"},
        ]
    )
    conn.execute(
        "INSERT INTO court_rulings "
        "(ruling_id, claim_id, task_id, worker_pct, "
        "summary, judge_votes, ruled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("rul-1", "clm-1", "t-5", 70, ruling_summary, judge_votes, _ts(t_30m)),
    )

    # ==================================================================
    # BANK TRANSACTIONS  (chronological order)
    # ==================================================================
    conn.executemany(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, "
        "balance_after, reference, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            # Salary credits
            ("tx-1", "a-alice", "credit", 1000, 1000, "salary_round_1", _ts(t_3h)),
            ("tx-2", "a-bob", "credit", 1000, 1000, "salary_round_1", _ts(t_3h)),
            ("tx-3", "a-charlie", "credit", 1000, 1000, "salary_round_1", _ts(t_3h)),
            # Alice locks for t-1
            ("tx-4", "a-alice", "escrow_lock", 100, 900, "t-1", _ts(t_2h30m)),
            # Alice locks for t-5
            (
                "tx-5",
                "a-alice",
                "escrow_lock",
                120,
                780,
                "t-5",
                _ts(t_2h30m + timedelta(minutes=1)),
            ),
            # Bob receives t-1 payment
            ("tx-6", "a-bob", "escrow_release", 100, 1100, "t-1", _ts(t_2h)),
            # Alice locks for t-2
            ("tx-7", "a-alice", "escrow_lock", 50, 730, "t-2", _ts(t_1h30m)),
            # Charlie receives t-2 payment
            ("tx-8", "a-charlie", "escrow_release", 50, 1050, "t-2", _ts(t_1h)),
            # Bob locks for t-3
            ("tx-9", "a-bob", "escrow_lock", 80, 1020, "t-3", _ts(t_45m)),
            # t-5 ruling split: Bob 84, Alice 36
            ("tx-10", "a-bob", "escrow_release", 84, 1104, "t-5", _ts(t_30m)),
            ("tx-11", "a-alice", "escrow_release", 36, 766, "t-5", _ts(t_30m)),
            # Charlie locks for t-4
            ("tx-12", "a-charlie", "escrow_lock", 60, 990, "t-4", _ts(t_15m)),
        ],
    )

    # ==================================================================
    # EVENTS -- 15 events covering the full economy
    # ==================================================================
    bid_dl_t1 = _ts(t_2h30m + bd)
    bid_dl_t2 = _ts(t_1h30m + bd)
    bid_dl_t4 = _ts(t_15m + bd)
    bid_dl_t5 = _ts(t_2h30m + bd)

    conn.executemany(
        "INSERT INTO events "
        "(event_source, event_type, timestamp, "
        "task_id, agent_id, summary, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            # 1-3: Agent registrations
            (
                "identity",
                "agent.registered",
                _ts(t_3h),
                None,
                "a-alice",
                "Alice registered as an agent",
                json.dumps({"agent_name": "Alice"}),
            ),
            (
                "identity",
                "agent.registered",
                _ts(t_3h),
                None,
                "a-bob",
                "Bob registered as an agent",
                json.dumps({"agent_name": "Bob"}),
            ),
            (
                "identity",
                "agent.registered",
                _ts(t_3h),
                None,
                "a-charlie",
                "Charlie registered as an agent",
                json.dumps({"agent_name": "Charlie"}),
            ),
            # 4: Task t-1 created
            (
                "board",
                "task.created",
                _ts(t_2h30m),
                "t-1",
                "a-alice",
                "Alice posted 'Build login page' for 100 tokens",
                json.dumps(
                    {
                        "title": "Build login page",
                        "reward": 100,
                        "bidding_deadline": bid_dl_t1,
                    }
                ),
            ),
            # 5: Bid on t-1
            (
                "board",
                "bid.submitted",
                _ts(t_2h30m + timedelta(minutes=2)),
                "t-1",
                "a-bob",
                "Bob bid on 'Build login page'",
                json.dumps(
                    {
                        "bid_id": "bid-1",
                        "title": "Build login page",
                        "bid_count": 1,
                    }
                ),
            ),
            # 6: Task t-1 accepted (Bob wins)
            (
                "board",
                "task.accepted",
                _ts(t_2h30m + timedelta(minutes=10)),
                "t-1",
                "a-alice",
                "Alice accepted Bob's bid on 'Build login page'",
                json.dumps(
                    {
                        "title": "Build login page",
                        "worker_id": "a-bob",
                        "worker_name": "Bob",
                        "bid_id": "bid-1",
                    }
                ),
            ),
            # 7: Task t-1 approved
            (
                "board",
                "task.approved",
                _ts(t_2h),
                "t-1",
                "a-alice",
                "Alice approved 'Build login page'",
                json.dumps(
                    {
                        "title": "Build login page",
                        "reward": 100,
                        "auto": False,
                    }
                ),
            ),
            # 8: Task t-2 created
            (
                "board",
                "task.created",
                _ts(t_1h30m),
                "t-2",
                "a-alice",
                "Alice posted 'Write unit tests' for 50 tokens",
                json.dumps(
                    {
                        "title": "Write unit tests",
                        "reward": 50,
                        "bidding_deadline": bid_dl_t2,
                    }
                ),
            ),
            # 9: Task t-2 approved
            (
                "board",
                "task.approved",
                _ts(t_1h),
                "t-2",
                "a-alice",
                "Alice approved 'Write unit tests'",
                json.dumps(
                    {
                        "title": "Write unit tests",
                        "reward": 50,
                        "auto": False,
                    }
                ),
            ),
            # 10: Task t-5 created (will be disputed)
            (
                "board",
                "task.created",
                _ts(t_2h30m),
                "t-5",
                "a-alice",
                "Alice posted 'Refactor database layer' for 120 tokens",
                json.dumps(
                    {
                        "title": "Refactor database layer",
                        "reward": 120,
                        "bidding_deadline": bid_dl_t5,
                    }
                ),
            ),
            # 11: Task t-5 disputed
            (
                "board",
                "task.disputed",
                _ts(t_45m),
                "t-5",
                "a-alice",
                "Alice disputed 'Refactor database layer'",
                json.dumps(
                    {
                        "title": "Refactor database layer",
                        "reason": ruling_reason,
                    }
                ),
            ),
            # 12: Court claim filed for t-5
            (
                "court",
                "claim.filed",
                _ts(t_45m),
                "t-5",
                "a-alice",
                "Alice filed a claim against Bob",
                json.dumps(
                    {
                        "claim_id": "clm-1",
                        "title": "Refactor database layer",
                        "claimant_name": "Alice",
                    }
                ),
            ),
            # 13: Rebuttal submitted
            (
                "court",
                "rebuttal.submitted",
                _ts(t_45m + timedelta(minutes=5)),
                "t-5",
                "a-bob",
                "Bob submitted a rebuttal",
                json.dumps(
                    {
                        "claim_id": "clm-1",
                        "title": "Refactor database layer",
                        "respondent_name": "Bob",
                    }
                ),
            ),
            # 14: Ruling delivered
            (
                "court",
                "ruling.delivered",
                _ts(t_30m),
                "t-5",
                None,
                "Ruling: worker receives 70%",
                json.dumps(
                    {
                        "ruling_id": "rul-1",
                        "claim_id": "clm-1",
                        "worker_pct": 70,
                        "summary": ruling_summary,
                    }
                ),
            ),
            # 15: Task t-4 created (open)
            (
                "board",
                "task.created",
                _ts(t_15m),
                "t-4",
                "a-charlie",
                "Charlie posted 'Implement caching' for 60 tokens",
                json.dumps(
                    {
                        "title": "Implement caching",
                        "reward": 60,
                        "bidding_deadline": bid_dl_t4,
                    }
                ),
            ),
        ],
    )

    conn.commit()
    conn.close()
    return db_file
