"""Create a seed database for manual testing via curl."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "economy.db"
SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "specifications" / "schema.sql"


def ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    schema = SCHEMA_PATH.read_text()
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(schema)

    now = datetime.now(UTC)
    t_7d = now - timedelta(days=7)
    t_5d = now - timedelta(days=5)
    t_3d = now - timedelta(days=3)
    t_2d = now - timedelta(days=2)
    t_1d = now - timedelta(days=1)
    t_12h = now - timedelta(hours=12)
    t_6h = now - timedelta(hours=6)
    t_3h = now - timedelta(hours=3)
    t_2h = now - timedelta(hours=2)
    t_1h = now - timedelta(hours=1)
    t_30m = now - timedelta(minutes=30)
    t_15m = now - timedelta(minutes=15)

    bd = timedelta(seconds=3600)
    ex = timedelta(seconds=7200)
    rv = timedelta(seconds=3600)

    # === AGENTS ===
    agents = [
        ("a-alice", "Alice", "ed25519:alice-pub", ts(t_7d)),
        ("a-bob", "Bob", "ed25519:bob-pub", ts(t_5d)),
        ("a-charlie", "Charlie", "ed25519:charlie-pub", ts(t_3d)),
        ("a-diana", "Diana", "ed25519:diana-pub", ts(t_2d)),
        ("a-eve", "Eve", "ed25519:eve-pub", ts(t_1d)),
    ]
    conn.executemany(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) VALUES (?,?,?,?)",
        agents,
    )

    # === BANK ACCOUNTS ===
    conn.executemany(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?,?,?)",
        [
            ("a-alice", 1850, ts(t_7d)),
            ("a-bob", 2200, ts(t_5d)),
            ("a-charlie", 1100, ts(t_3d)),
            ("a-diana", 750, ts(t_2d)),
            ("a-eve", 500, ts(t_1d)),
        ],
    )

    # === ESCROW ===
    conn.executemany(
        "INSERT INTO bank_escrow (escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?)",
        [
            ("esc-1", "a-alice", 200, "t-1", "released", ts(t_6h), ts(t_3h)),
            ("esc-2", "a-alice", 150, "t-2", "released", ts(t_12h), ts(t_6h)),
            ("esc-3", "a-bob", 100, "t-3", "locked", ts(t_3h), None),
            ("esc-4", "a-charlie", 80, "t-4", "locked", ts(t_2h), None),
            ("esc-5", "a-diana", 120, "t-5", "released", ts(t_2d), ts(t_1d)),
            ("esc-6", "a-alice", 300, "t-6", "split", ts(t_3d), ts(t_2d)),
            ("esc-7", "a-eve", 60, "t-7", "locked", ts(t_1h), None),
            ("esc-8", "a-bob", 250, "t-8", "released", ts(t_5d), ts(t_3d)),
        ],
    )

    # === TASKS ===
    def insert_task(params):
        conn.execute(
            """INSERT INTO board_tasks (
                task_id, poster_id, title, spec, reward, status,
                bidding_deadline_seconds, deadline_seconds, review_deadline_seconds,
                bidding_deadline, execution_deadline, review_deadline,
                escrow_id, worker_id, accepted_bid_id,
                dispute_reason, ruling_id, worker_pct, ruling_summary,
                created_at, accepted_at, submitted_at, approved_at,
                cancelled_at, disputed_at, ruled_at, expired_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            params,
        )

    # t-1: Alice->Bob, 200, approved 3h ago
    insert_task((
        "t-1", "a-alice", "Build authentication system",
        "Implement JWT-based auth with refresh tokens, rate limiting, and OAuth2 support",
        200, "approved", 3600, 7200, 3600,
        ts(t_6h + bd), ts(t_6h + ex), ts(t_6h + rv),
        "esc-1", "a-bob", "bid-1",
        None, None, None, None,
        ts(t_6h), ts(t_6h + timedelta(minutes=15)),
        ts(t_3h - timedelta(minutes=30)), ts(t_3h),
        None, None, None, None,
    ))

    # t-2: Alice->Charlie, 150, approved 6h ago
    insert_task((
        "t-2", "a-alice", "Design database schema",
        "Create normalized PostgreSQL schema for user management, roles, and audit logging",
        150, "approved", 3600, 7200, 3600,
        ts(t_12h + bd), ts(t_12h + ex), ts(t_12h + rv),
        "esc-2", "a-charlie", "bid-4",
        None, None, None, None,
        ts(t_12h), ts(t_12h + timedelta(minutes=20)),
        ts(t_6h - timedelta(minutes=30)), ts(t_6h),
        None, None, None, None,
    ))

    # t-3: Bob->Diana, 100, accepted (in progress)
    insert_task((
        "t-3", "a-bob", "Write API documentation",
        "Document all REST endpoints with OpenAPI 3.0 spec, examples, and error codes",
        100, "accepted", 3600, 7200, 3600,
        ts(t_3h + bd), ts(t_3h + ex), None,
        "esc-3", "a-diana", "bid-7",
        None, None, None, None,
        ts(t_3h), ts(t_2h), None, None,
        None, None, None, None,
    ))

    # t-4: Charlie, 80, open (bidding)
    insert_task((
        "t-4", "a-charlie", "Implement caching layer",
        "Add Redis-based caching with TTL policies for hot data paths",
        80, "open", 3600, 7200, 3600,
        ts(t_2h + bd), None, None,
        "esc-4", None, None,
        None, None, None, None,
        ts(t_2h), None, None, None,
        None, None, None, None,
    ))

    # t-5: Diana->Eve, 120, approved 1d ago
    insert_task((
        "t-5", "a-diana", "Build notification service",
        "Real-time notifications via WebSocket with fallback to email digest",
        120, "approved", 3600, 7200, 3600,
        ts(t_2d + bd), ts(t_2d + ex), ts(t_2d + rv),
        "esc-5", "a-eve", "bid-9",
        None, None, None, None,
        ts(t_2d), ts(t_2d + timedelta(hours=1)),
        ts(t_1d - timedelta(hours=2)), ts(t_1d),
        None, None, None, None,
    ))

    # t-6: Alice->Bob, 300, ruled (dispute, worker_pct=60)
    insert_task((
        "t-6", "a-alice", "Full-stack dashboard",
        "Build real-time analytics dashboard with React + D3.js + streaming data",
        300, "ruled", 3600, 7200, 3600,
        ts(t_3d + bd), ts(t_3d + ex), ts(t_3d + rv),
        "esc-6", "a-bob", "bid-10",
        "Dashboard missing real-time updates and D3 visualizations",
        "rul-1", 60, "Worker completed 60% - static charts only, no streaming",
        ts(t_3d), ts(t_3d + timedelta(hours=2)),
        ts(t_2d - timedelta(hours=4)), None,
        None, ts(t_2d - timedelta(hours=2)), ts(t_2d), None,
    ))

    # t-7: Eve, 60, open (no bids - uncontested)
    insert_task((
        "t-7", "a-eve", "Write unit tests for auth",
        "100% coverage for authentication module including edge cases",
        60, "open", 3600, 7200, 3600,
        ts(t_1h + bd), None, None,
        "esc-7", None, None,
        None, None, None, None,
        ts(t_1h), None, None, None,
        None, None, None, None,
    ))

    # t-8: Bob->Alice, 250, approved 3d ago
    insert_task((
        "t-8", "a-bob", "Microservices migration plan",
        "Architecture document for breaking monolith into microservices with migration strategy",
        250, "approved", 3600, 7200, 3600,
        ts(t_5d + bd), ts(t_5d + ex), ts(t_5d + rv),
        "esc-8", "a-alice", "bid-12",
        None, None, None, None,
        ts(t_5d), ts(t_5d + timedelta(hours=3)),
        ts(t_3d - timedelta(hours=6)), ts(t_3d),
        None, None, None, None,
    ))

    # === BIDS ===
    conn.executemany(
        "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) VALUES (?,?,?,?,?)",
        [
            # t-1: 4 bids (Bob won)
            ("bid-1", "t-1", "a-bob", "I have deep experience with JWT and OAuth2 implementations", ts(t_6h + timedelta(minutes=5))),
            ("bid-2", "t-1", "a-charlie", "I can implement this with best security practices", ts(t_6h + timedelta(minutes=8))),
            ("bid-3", "t-1", "a-diana", "Auth systems are my specialty", ts(t_6h + timedelta(minutes=12))),
            ("bid-3b", "t-1", "a-eve", "I built similar auth for 3 production apps", ts(t_6h + timedelta(minutes=15))),
            # t-2: 3 bids (Charlie won)
            ("bid-4", "t-2", "a-charlie", "Database design is my core strength", ts(t_12h + timedelta(minutes=5))),
            ("bid-5", "t-2", "a-bob", "I have PostgreSQL DBA experience", ts(t_12h + timedelta(minutes=10))),
            ("bid-6", "t-2", "a-eve", "I can design a clean normalized schema", ts(t_12h + timedelta(minutes=15))),
            # t-3: 2 bids (Diana won)
            ("bid-7", "t-3", "a-diana", "I write excellent technical documentation", ts(t_3h + timedelta(minutes=5))),
            ("bid-8", "t-3", "a-charlie", "OpenAPI is my bread and butter", ts(t_3h + timedelta(minutes=10))),
            # t-4: 3 bids (pending)
            ("bid-4a", "t-4", "a-alice", "I have Redis expertise from production systems", ts(t_2h + timedelta(minutes=5))),
            ("bid-4b", "t-4", "a-bob", "Caching is critical and I know how to do it right", ts(t_2h + timedelta(minutes=8))),
            ("bid-4c", "t-4", "a-eve", "I can implement efficient cache invalidation", ts(t_2h + timedelta(minutes=12))),
            # t-5: 2 bids (Eve won)
            ("bid-9", "t-5", "a-eve", "WebSocket + notification systems are my focus area", ts(t_2d + timedelta(minutes=10))),
            ("bid-9b", "t-5", "a-bob", "I can build a reliable notification pipeline", ts(t_2d + timedelta(minutes=20))),
            # t-6: 1 bid (Bob won, then disputed)
            ("bid-10", "t-6", "a-bob", "Full-stack React + D3 is my specialty", ts(t_3d + timedelta(minutes=10))),
            # t-7: NO bids (uncontested)
            # t-8: 2 bids (Alice won)
            ("bid-12", "t-8", "a-alice", "I have architected multiple microservice migrations", ts(t_5d + timedelta(minutes=15))),
            ("bid-13", "t-8", "a-diana", "I can create a comprehensive migration roadmap", ts(t_5d + timedelta(minutes=25))),
        ],
    )

    # === ASSETS ===
    conn.executemany(
        "INSERT INTO board_assets (asset_id, task_id, uploader_id, filename, content_type, size_bytes, storage_path, uploaded_at) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("asset-1", "t-1", "a-bob", "auth-module.zip", "application/zip", 245760, "/storage/auth-module.zip", ts(t_3h - timedelta(minutes=30))),
            ("asset-2", "t-2", "a-charlie", "schema.sql", "text/plain", 8192, "/storage/schema.sql", ts(t_6h - timedelta(minutes=30))),
            ("asset-3", "t-6", "a-bob", "dashboard-v1.tar.gz", "application/gzip", 1048576, "/storage/dashboard.tar.gz", ts(t_2d - timedelta(hours=4))),
        ],
    )

    # === FEEDBACK ===
    conn.executemany(
        "INSERT INTO reputation_feedback (feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, submitted_at, visible) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            # t-1 visible
            ("fb-1", "t-1", "a-alice", "a-bob", "poster", "delivery_quality", "extremely_satisfied", "Excellent auth implementation, production-ready", ts(t_3h + timedelta(minutes=5)), 1),
            ("fb-2", "t-1", "a-bob", "a-alice", "worker", "spec_quality", "extremely_satisfied", "Very clear and detailed spec", ts(t_3h + timedelta(minutes=6)), 1),
            # t-2 visible
            ("fb-3", "t-2", "a-alice", "a-charlie", "poster", "delivery_quality", "satisfied", "Good schema, minor normalization issues", ts(t_6h + timedelta(minutes=5)), 1),
            ("fb-4", "t-2", "a-charlie", "a-alice", "worker", "spec_quality", "satisfied", "Spec covered requirements well", ts(t_6h + timedelta(minutes=6)), 1),
            # t-5 visible
            ("fb-5", "t-5", "a-diana", "a-eve", "poster", "delivery_quality", "extremely_satisfied", "Perfect notification system", ts(t_1d + timedelta(minutes=10)), 1),
            ("fb-6", "t-5", "a-eve", "a-diana", "worker", "spec_quality", "satisfied", "Requirements were clear", ts(t_1d + timedelta(minutes=12)), 1),
            # t-6 sealed (dispute)
            ("fb-7", "t-6", "a-alice", "a-bob", "poster", "delivery_quality", "dissatisfied", "Missing core features", ts(t_2d - timedelta(hours=2)), 0),
            ("fb-8", "t-6", "a-bob", "a-alice", "worker", "spec_quality", "dissatisfied", "Spec scope was unrealistic", ts(t_2d - timedelta(hours=1, minutes=50)), 0),
            # t-8 visible
            ("fb-9", "t-8", "a-bob", "a-alice", "poster", "delivery_quality", "extremely_satisfied", "Comprehensive migration plan", ts(t_3d + timedelta(minutes=15)), 1),
            ("fb-10", "t-8", "a-alice", "a-bob", "worker", "spec_quality", "satisfied", "Good requirements document", ts(t_3d + timedelta(minutes=20)), 1),
        ],
    )

    # === COURT ===
    conn.execute(
        "INSERT INTO court_claims (claim_id, task_id, claimant_id, respondent_id, reason, status, filed_at) VALUES (?,?,?,?,?,?,?)",
        ("clm-1", "t-6", "a-alice", "a-bob",
         "Dashboard missing real-time updates and D3 visualizations",
         "ruled", ts(t_2d - timedelta(hours=2))),
    )
    conn.execute(
        "INSERT INTO court_rebuttals (rebuttal_id, claim_id, agent_id, content, submitted_at) VALUES (?,?,?,?,?)",
        ("reb-1", "clm-1", "a-bob",
         "The spec was ambiguous about streaming requirements. I delivered working charts.",
         ts(t_2d - timedelta(hours=1))),
    )
    conn.execute(
        "INSERT INTO court_rulings (ruling_id, claim_id, task_id, worker_pct, summary, judge_votes, ruled_at) VALUES (?,?,?,?,?,?,?)",
        ("rul-1", "clm-1", "t-6", 60,
         "Worker completed 60% - static charts only, no streaming",
         json.dumps([
             {"judge": "judge-1", "worker_pct": 55, "reason": "Missing streaming"},
             {"judge": "judge-2", "worker_pct": 65, "reason": "Charts work but no D3"},
             {"judge": "judge-3", "worker_pct": 60, "reason": "Partial delivery"},
         ]),
         ts(t_2d)),
    )

    # === TRANSACTIONS ===
    conn.executemany(
        "INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp) VALUES (?,?,?,?,?,?,?)",
        [
            ("tx-1", "a-alice", "credit", 2000, 2000, "salary_round_1", ts(t_7d)),
            ("tx-2", "a-bob", "credit", 2000, 2000, "salary_round_1", ts(t_5d)),
            ("tx-3", "a-charlie", "credit", 1000, 1000, "salary_round_1", ts(t_3d)),
            ("tx-4", "a-diana", "credit", 1000, 1000, "salary_round_1", ts(t_2d)),
            ("tx-5", "a-eve", "credit", 500, 500, "salary_round_1", ts(t_1d)),
            ("tx-6", "a-alice", "escrow_lock", 200, 1800, "t-1", ts(t_6h)),
            ("tx-7", "a-alice", "escrow_lock", 150, 1650, "t-2", ts(t_12h)),
            ("tx-8", "a-bob", "escrow_release", 200, 2200, "t-1", ts(t_3h)),
            ("tx-9", "a-charlie", "escrow_release", 150, 1150, "t-2", ts(t_6h)),
            ("tx-10", "a-alice", "escrow_lock", 300, 1350, "t-6", ts(t_3d)),
            ("tx-11", "a-bob", "escrow_release", 180, 2380, "t-6", ts(t_2d)),  # 60% of 300
            ("tx-12", "a-alice", "escrow_release", 120, 1470, "t-6", ts(t_2d)),  # 40% back
            ("tx-13", "a-bob", "escrow_lock", 250, 2130, "t-8", ts(t_5d)),
            ("tx-14", "a-alice", "escrow_release", 250, 1720, "t-8", ts(t_3d)),
            ("tx-15", "a-diana", "escrow_lock", 120, 880, "t-5", ts(t_2d)),
            ("tx-16", "a-eve", "escrow_release", 120, 620, "t-5", ts(t_1d)),
            ("tx-17", "a-bob", "escrow_lock", 100, 2030, "t-3", ts(t_3h)),
            ("tx-18", "a-charlie", "escrow_lock", 80, 1070, "t-4", ts(t_2h)),
            ("tx-19", "a-eve", "escrow_lock", 60, 560, "t-7", ts(t_1h)),
        ],
    )

    # === EVENTS ===
    conn.executemany(
        "INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload) VALUES (?,?,?,?,?,?,?)",
        [
            ("identity", "agent.registered", ts(t_7d), None, "a-alice", "Alice registered as an agent", json.dumps({"agent_name": "Alice"})),
            ("identity", "agent.registered", ts(t_5d), None, "a-bob", "Bob registered as an agent", json.dumps({"agent_name": "Bob"})),
            ("identity", "agent.registered", ts(t_3d), None, "a-charlie", "Charlie registered as an agent", json.dumps({"agent_name": "Charlie"})),
            ("identity", "agent.registered", ts(t_2d), None, "a-diana", "Diana registered as an agent", json.dumps({"agent_name": "Diana"})),
            ("identity", "agent.registered", ts(t_1d), None, "a-eve", "Eve registered as an agent", json.dumps({"agent_name": "Eve"})),
            ("board", "task.created", ts(t_6h), "t-1", "a-alice", "Alice posted 'Build authentication system' for 200 tokens", json.dumps({"title": "Build authentication system", "reward": 200})),
            ("board", "bid.submitted", ts(t_6h + timedelta(minutes=5)), "t-1", "a-bob", "Bob bid on 'Build authentication system'", json.dumps({"bid_id": "bid-1", "title": "Build authentication system", "bid_count": 1})),
            ("board", "task.accepted", ts(t_6h + timedelta(minutes=15)), "t-1", "a-alice", "Alice accepted Bob's bid", json.dumps({"title": "Build authentication system", "worker_id": "a-bob", "worker_name": "Bob"})),
            ("board", "task.approved", ts(t_3h), "t-1", "a-alice", "Alice approved 'Build authentication system'", json.dumps({"title": "Build authentication system", "reward": 200, "auto": False})),
            ("board", "task.created", ts(t_12h), "t-2", "a-alice", "Alice posted 'Design database schema' for 150 tokens", json.dumps({"title": "Design database schema", "reward": 150})),
            ("board", "task.approved", ts(t_6h), "t-2", "a-alice", "Alice approved 'Design database schema'", json.dumps({"title": "Design database schema", "reward": 150})),
            ("board", "task.created", ts(t_5d), "t-8", "a-bob", "Bob posted 'Microservices migration plan' for 250 tokens", json.dumps({"title": "Microservices migration plan", "reward": 250})),
            ("board", "task.approved", ts(t_3d), "t-8", "a-bob", "Bob approved 'Microservices migration plan'", json.dumps({"title": "Microservices migration plan", "reward": 250})),
            ("board", "task.created", ts(t_3d), "t-6", "a-alice", "Alice posted 'Full-stack dashboard' for 300 tokens", json.dumps({"title": "Full-stack dashboard", "reward": 300})),
            ("board", "task.disputed", ts(t_2d - timedelta(hours=2)), "t-6", "a-alice", "Alice disputed 'Full-stack dashboard'", json.dumps({"title": "Full-stack dashboard", "reason": "Missing real-time updates"})),
            ("court", "claim.filed", ts(t_2d - timedelta(hours=2)), "t-6", "a-alice", "Alice filed a claim against Bob", json.dumps({"claim_id": "clm-1", "title": "Full-stack dashboard"})),
            ("court", "rebuttal.submitted", ts(t_2d - timedelta(hours=1)), "t-6", "a-bob", "Bob submitted a rebuttal", json.dumps({"claim_id": "clm-1", "title": "Full-stack dashboard"})),
            ("court", "ruling.delivered", ts(t_2d), "t-6", None, "Ruling: worker receives 60%", json.dumps({"ruling_id": "rul-1", "worker_pct": 60, "summary": "Worker completed 60%"})),
            ("board", "task.created", ts(t_2d), "t-5", "a-diana", "Diana posted 'Build notification service' for 120 tokens", json.dumps({"title": "Build notification service", "reward": 120})),
            ("board", "task.approved", ts(t_1d), "t-5", "a-diana", "Diana approved 'Build notification service'", json.dumps({"title": "Build notification service", "reward": 120})),
            ("board", "task.created", ts(t_3h), "t-3", "a-bob", "Bob posted 'Write API documentation' for 100 tokens", json.dumps({"title": "Write API documentation", "reward": 100})),
            ("board", "task.accepted", ts(t_2h), "t-3", "a-bob", "Bob accepted Diana's bid", json.dumps({"title": "Write API documentation", "worker_id": "a-diana"})),
            ("board", "task.created", ts(t_2h), "t-4", "a-charlie", "Charlie posted 'Implement caching layer' for 80 tokens", json.dumps({"title": "Implement caching layer", "reward": 80})),
            ("board", "task.created", ts(t_1h), "t-7", "a-eve", "Eve posted 'Write unit tests for auth' for 60 tokens", json.dumps({"title": "Write unit tests for auth", "reward": 60})),
        ],
    )

    conn.commit()
    conn.close()
    print(f"Created {DB_PATH} with 5 agents, 8 tasks, 24 events")


if __name__ == "__main__":
    main()
