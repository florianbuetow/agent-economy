"""Simulate live economy activity by inserting events AND domain table rows.

Updates all tables that the metrics endpoint reads from:
  - board_tasks, board_bids, bank_escrow, bank_accounts,
    reputation_feedback, court_claims, court_rebuttals, court_rulings, events

Usage:
    python data/simulate.py              # default: one event every 2-5 seconds
    python data/simulate.py --fast       # one event every 0.5-1.5 seconds
    python data/simulate.py --slow       # one event every 5-10 seconds
"""

import json
import random
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "economy.db"

AGENTS = [
    ("a-alice", "Alice"),
    ("a-bob", "Bob"),
    ("a-charlie", "Charlie"),
    ("a-diana", "Diana"),
    ("a-eve", "Eve"),
]

TASK_TITLES = [
    "Build payment gateway integration",
    "Design REST API for inventory service",
    "Implement search with Elasticsearch",
    "Create CI/CD pipeline with GitHub Actions",
    "Write load testing suite with k6",
    "Build WebSocket chat service",
    "Implement rate limiter middleware",
    "Design event-driven architecture",
    "Create data migration scripts",
    "Build admin dashboard with RBAC",
    "Implement GraphQL resolvers",
    "Write end-to-end test suite",
    "Build file upload service with S3",
    "Create monitoring and alerting setup",
    "Implement OAuth2 provider",
    "Design message queue consumers",
    "Build recommendation engine",
    "Create automated backup system",
    "Implement feature flag service",
    "Build real-time analytics pipeline",
]

SPECS = [
    "Implement with full test coverage and documentation",
    "Build production-ready solution with error handling and logging",
    "Create clean, maintainable implementation following best practices",
    "Deliver working prototype with integration tests",
]

PROPOSALS = [
    "I have deep experience with this and can deliver quickly",
    "This is my area of expertise, happy to take it on",
    "I've built similar systems in production before",
    "I can deliver a clean, well-tested implementation",
]

DISPUTE_REASONS = [
    "Deliverable does not match the specification",
    "Missing core requirements from the spec",
    "Quality is below acceptable standards",
    "Incomplete implementation of key features",
]

FEEDBACK_COMMENTS = {
    "extremely_satisfied": [
        "Excellent work, production-ready",
        "Outstanding quality and attention to detail",
        "Exceeded expectations on all fronts",
    ],
    "satisfied": [
        "Good work, minor issues only",
        "Solid implementation overall",
        "Met requirements adequately",
    ],
    "dissatisfied": [
        "Missing core features",
        "Quality below expectations",
        "Incomplete delivery",
    ],
}

_task_counter = 100
_bid_counter = 100
_escrow_counter = 100
_claim_counter = 100
_ruling_counter = 100
_rebuttal_counter = 100
_feedback_counter = 100
_tx_counter = 100


def _next(prefix: str, counter_name: str) -> str:
    g = globals()
    g[counter_name] += 1
    return f"{prefix}-sim-{g[counter_name]}"


def next_task_id() -> str:
    return _next("t", "_task_counter")


def next_bid_id() -> str:
    return _next("bid", "_bid_counter")


def next_escrow_id() -> str:
    return _next("esc", "_escrow_counter")


def next_claim_id() -> str:
    return _next("clm", "_claim_counter")


def next_ruling_id() -> str:
    return _next("rul", "_ruling_counter")


def next_rebuttal_id() -> str:
    return _next("reb", "_rebuttal_counter")


def next_feedback_id() -> str:
    return _next("fb", "_feedback_counter")


def next_tx_id() -> str:
    return _next("tx", "_tx_counter")


def now_ts() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def pick_agent() -> tuple[str, str]:
    return random.choice(AGENTS)


def pick_other_agent(exclude_id: str) -> tuple[str, str]:
    others = [(aid, name) for aid, name in AGENTS if aid != exclude_id]
    return random.choice(others)


def pick_bidders(exclude_id: str, count: int) -> list[tuple[str, str]]:
    """Pick N unique agents that aren't the poster."""
    others = [(aid, name) for aid, name in AGENTS if aid != exclude_id]
    random.shuffle(others)
    return others[:count]


def insert_event(
    conn: sqlite3.Connection,
    source: str,
    event_type: str,
    task_id: str | None,
    agent_id: str | None,
    summary: str,
    payload: dict,
) -> int:
    cursor = conn.execute(
        "INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload) VALUES (?,?,?,?,?,?,?)",
        (source, event_type, now_ts(), task_id, agent_id, summary, json.dumps(payload)),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Scenario: full task lifecycle (happy path)
#   create → escrow lock → bids → accept → submit → approve → escrow release → feedback
# ---------------------------------------------------------------------------

def scenario_task_lifecycle(conn: sqlite3.Connection, delay: tuple[float, float]) -> None:
    poster_id, poster_name = pick_agent()
    title = random.choice(TASK_TITLES)
    reward = random.choice([50, 80, 100, 120, 150, 200, 250, 300])
    spec = random.choice(SPECS)
    task_id = next_task_id()
    escrow_id = next_escrow_id()
    ts = now_ts()

    # 1. Lock escrow
    conn.execute(
        "INSERT INTO bank_escrow (escrow_id, payer_account_id, amount, task_id, status, created_at) VALUES (?,?,?,?,?,?)",
        (escrow_id, poster_id, reward, task_id, "locked", ts),
    )

    # 2. Create task (status=open)
    conn.execute(
        """INSERT INTO board_tasks (
            task_id, poster_id, title, spec, reward, status,
            bidding_deadline_seconds, deadline_seconds, review_deadline_seconds,
            bidding_deadline, escrow_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (task_id, poster_id, title, spec, reward, "open",
         3600, 7200, 3600, ts, escrow_id, ts),
    )
    conn.commit()

    eid = insert_event(conn, "board", "task.created", task_id, poster_id,
                       f"{poster_name} posted '{title}' for {reward} tokens",
                       {"title": title, "reward": reward})
    print(f"  [{eid}] TASK    {poster_name} posted '{title}' for {reward} tokens")

    eid = insert_event(conn, "bank", "escrow.locked", task_id, poster_id,
                       f"{poster_name} locked {reward} tokens in escrow",
                       {"escrow_id": escrow_id, "amount": reward, "title": title})
    print(f"  [{eid}] ESCROW  {poster_name} locked {reward} tokens in escrow")

    time.sleep(random.uniform(*delay))

    # 3. Bids
    num_bids = random.randint(1, 3)
    bidders = pick_bidders(poster_id, num_bids)
    bid_ids = []
    for i, (bidder_id, bidder_name) in enumerate(bidders):
        bid_id = next_bid_id()
        bid_ids.append((bid_id, bidder_id, bidder_name))
        conn.execute(
            "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) VALUES (?,?,?,?,?)",
            (bid_id, task_id, bidder_id, random.choice(PROPOSALS), now_ts()),
        )
        conn.commit()
        eid = insert_event(conn, "board", "bid.submitted", task_id, bidder_id,
                           f"{bidder_name} bid on '{title}'",
                           {"bid_id": bid_id, "title": title, "bid_count": i + 1})
        print(f"  [{eid}] BID     {bidder_name} bid on '{title}'")
        time.sleep(random.uniform(*delay))

    # 4. Accept first bidder
    winner_bid_id, worker_id, worker_name = bid_ids[0]
    ts = now_ts()
    conn.execute(
        """UPDATE board_tasks SET status='accepted', worker_id=?, accepted_bid_id=?,
           accepted_at=?, execution_deadline=? WHERE task_id=?""",
        (worker_id, winner_bid_id, ts, ts, task_id),
    )
    conn.commit()
    eid = insert_event(conn, "board", "task.accepted", task_id, poster_id,
                       f"{poster_name} accepted {worker_name}'s bid",
                       {"title": title, "worker_id": worker_id, "worker_name": worker_name, "bid_id": winner_bid_id})
    print(f"  [{eid}] ACCEPT  {poster_name} accepted {worker_name}'s bid")

    time.sleep(random.uniform(*delay))

    # 5. Worker submits
    ts = now_ts()
    conn.execute(
        "UPDATE board_tasks SET status='submitted', submitted_at=?, review_deadline=? WHERE task_id=?",
        (ts, ts, task_id),
    )
    conn.commit()
    eid = insert_event(conn, "board", "task.submitted", task_id, worker_id,
                       f"{worker_name} submitted work on '{title}'",
                       {"title": title, "worker_id": worker_id, "worker_name": worker_name, "asset_count": random.randint(1, 3)})
    print(f"  [{eid}] SUBMIT  {worker_name} submitted work on '{title}'")

    time.sleep(random.uniform(*delay))

    # 6. Poster approves
    ts = now_ts()
    conn.execute(
        "UPDATE board_tasks SET status='approved', approved_at=? WHERE task_id=?",
        (ts, task_id),
    )
    conn.execute(
        "UPDATE bank_escrow SET status='released', resolved_at=? WHERE escrow_id=?",
        (ts, escrow_id),
    )
    conn.commit()
    eid = insert_event(conn, "board", "task.approved", task_id, poster_id,
                       f"{poster_name} approved '{title}'",
                       {"title": title, "reward": reward, "auto": False})
    print(f"  [{eid}] APPROVE {poster_name} approved '{title}'")

    eid = insert_event(conn, "bank", "escrow.released", task_id, worker_id,
                       f"{reward} tokens released to {worker_name}",
                       {"escrow_id": escrow_id, "amount": reward, "recipient_id": worker_id, "recipient_name": worker_name})
    print(f"  [{eid}] PAYOUT  {reward} tokens released to {worker_name}")

    time.sleep(random.uniform(*delay))

    # 7. Mutual feedback (both visible)
    rating = random.choice(["extremely_satisfied", "satisfied"])
    for from_id, from_name, to_id, to_name, role, category in [
        (poster_id, poster_name, worker_id, worker_name, "poster", "delivery_quality"),
        (worker_id, worker_name, poster_id, poster_name, "worker", "spec_quality"),
    ]:
        fb_id = next_feedback_id()
        comment = random.choice(FEEDBACK_COMMENTS[rating])
        conn.execute(
            "INSERT INTO reputation_feedback (feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, submitted_at, visible) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fb_id, task_id, from_id, to_id, role, category, rating, comment, now_ts(), 1),
        )
        conn.commit()
        eid = insert_event(conn, "reputation", "feedback.revealed", task_id, from_id,
                           f"Feedback revealed: {from_name} → {to_name} ({category})",
                           {"task_id": task_id, "from_name": from_name, "to_name": to_name, "category": category})
        print(f"  [{eid}] REP     Feedback: {from_name} → {to_name} ({category}: {rating})")


# ---------------------------------------------------------------------------
# Scenario: task with dispute
#   create → escrow → bids → accept → submit → dispute → claim → ruling
# ---------------------------------------------------------------------------

def scenario_task_dispute(conn: sqlite3.Connection, delay: tuple[float, float]) -> None:
    poster_id, poster_name = pick_agent()
    title = random.choice(TASK_TITLES)
    reward = random.choice([100, 150, 200, 250, 300])
    spec = random.choice(SPECS)
    task_id = next_task_id()
    escrow_id = next_escrow_id()
    ts = now_ts()

    # 1. Lock escrow + create task
    conn.execute(
        "INSERT INTO bank_escrow (escrow_id, payer_account_id, amount, task_id, status, created_at) VALUES (?,?,?,?,?,?)",
        (escrow_id, poster_id, reward, task_id, "locked", ts),
    )
    conn.execute(
        """INSERT INTO board_tasks (
            task_id, poster_id, title, spec, reward, status,
            bidding_deadline_seconds, deadline_seconds, review_deadline_seconds,
            bidding_deadline, escrow_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (task_id, poster_id, title, spec, reward, "open",
         3600, 7200, 3600, ts, escrow_id, ts),
    )
    conn.commit()

    eid = insert_event(conn, "board", "task.created", task_id, poster_id,
                       f"{poster_name} posted '{title}' for {reward} tokens",
                       {"title": title, "reward": reward})
    print(f"  [{eid}] TASK    {poster_name} posted '{title}' for {reward} tokens")

    eid = insert_event(conn, "bank", "escrow.locked", task_id, poster_id,
                       f"{poster_name} locked {reward} tokens in escrow",
                       {"escrow_id": escrow_id, "amount": reward, "title": title})
    print(f"  [{eid}] ESCROW  {poster_name} locked {reward} tokens in escrow")

    time.sleep(random.uniform(*delay))

    # 2. Single bid
    worker_id, worker_name = pick_other_agent(poster_id)
    bid_id = next_bid_id()
    conn.execute(
        "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) VALUES (?,?,?,?,?)",
        (bid_id, task_id, worker_id, random.choice(PROPOSALS), now_ts()),
    )
    conn.commit()
    eid = insert_event(conn, "board", "bid.submitted", task_id, worker_id,
                       f"{worker_name} bid on '{title}'",
                       {"bid_id": bid_id, "title": title, "bid_count": 1})
    print(f"  [{eid}] BID     {worker_name} bid on '{title}'")

    time.sleep(random.uniform(*delay))

    # 3. Accept
    ts = now_ts()
    conn.execute(
        """UPDATE board_tasks SET status='accepted', worker_id=?, accepted_bid_id=?,
           accepted_at=?, execution_deadline=? WHERE task_id=?""",
        (worker_id, bid_id, ts, ts, task_id),
    )
    conn.commit()
    eid = insert_event(conn, "board", "task.accepted", task_id, poster_id,
                       f"{poster_name} accepted {worker_name}'s bid",
                       {"title": title, "worker_id": worker_id, "worker_name": worker_name, "bid_id": bid_id})
    print(f"  [{eid}] ACCEPT  {poster_name} accepted {worker_name}'s bid")

    time.sleep(random.uniform(*delay))

    # 4. Submit
    ts = now_ts()
    conn.execute(
        "UPDATE board_tasks SET status='submitted', submitted_at=?, review_deadline=? WHERE task_id=?",
        (ts, ts, task_id),
    )
    conn.commit()
    eid = insert_event(conn, "board", "task.submitted", task_id, worker_id,
                       f"{worker_name} submitted work on '{title}'",
                       {"title": title, "worker_id": worker_id, "worker_name": worker_name, "asset_count": 1})
    print(f"  [{eid}] SUBMIT  {worker_name} submitted work on '{title}'")

    time.sleep(random.uniform(*delay))

    # 5. Dispute
    reason = random.choice(DISPUTE_REASONS)
    ts = now_ts()
    conn.execute(
        "UPDATE board_tasks SET status='disputed', disputed_at=?, dispute_reason=? WHERE task_id=?",
        (ts, reason, task_id),
    )
    conn.commit()
    eid = insert_event(conn, "board", "task.disputed", task_id, poster_id,
                       f"{poster_name} disputed '{title}'",
                       {"title": title, "reason": reason})
    print(f"  [{eid}] DISPUTE {poster_name} disputed '{title}'")

    time.sleep(random.uniform(*delay))

    # 6. Claim filed
    claim_id = next_claim_id()
    conn.execute(
        "INSERT INTO court_claims (claim_id, task_id, claimant_id, respondent_id, reason, status, filed_at) VALUES (?,?,?,?,?,?,?)",
        (claim_id, task_id, poster_id, worker_id, reason, "filed", now_ts()),
    )
    conn.commit()
    eid = insert_event(conn, "court", "claim.filed", task_id, poster_id,
                       f"{poster_name} filed a claim against {worker_name}",
                       {"claim_id": claim_id, "title": title, "claimant_name": poster_name})
    print(f"  [{eid}] CLAIM   {poster_name} filed a claim against {worker_name}")

    time.sleep(random.uniform(*delay))

    # 7. Rebuttal
    rebuttal_id = next_rebuttal_id()
    conn.execute(
        "INSERT INTO court_rebuttals (rebuttal_id, claim_id, agent_id, content, submitted_at) VALUES (?,?,?,?,?)",
        (rebuttal_id, claim_id, worker_id, "The spec was ambiguous. I delivered what was asked.", now_ts()),
    )
    conn.execute(
        "UPDATE court_claims SET status='rebuttal' WHERE claim_id=?",
        (claim_id,),
    )
    conn.commit()
    eid = insert_event(conn, "court", "rebuttal.submitted", task_id, worker_id,
                       f"{worker_name} submitted a rebuttal",
                       {"claim_id": claim_id, "title": title, "respondent_name": worker_name})
    print(f"  [{eid}] REBUT   {worker_name} submitted a rebuttal")

    time.sleep(random.uniform(*delay))

    # 8. Ruling
    worker_pct = random.choice([0, 30, 50, 60, 70, 80, 100])
    ruling_id = next_ruling_id()
    ruling_summary = f"Worker completed {worker_pct}% of deliverables"
    worker_amount = reward * worker_pct // 100
    poster_amount = reward - worker_amount
    ts = now_ts()

    conn.execute(
        "INSERT INTO court_rulings (ruling_id, claim_id, task_id, worker_pct, summary, judge_votes, ruled_at) VALUES (?,?,?,?,?,?,?)",
        (ruling_id, claim_id, task_id, worker_pct, ruling_summary,
         json.dumps([{"judge": "j-1", "worker_pct": worker_pct, "reason": "Assessment"}]),
         ts),
    )
    conn.execute(
        "UPDATE court_claims SET status='ruled' WHERE claim_id=?",
        (claim_id,),
    )
    conn.execute(
        """UPDATE board_tasks SET status='ruled', ruling_id=?, worker_pct=?,
           ruling_summary=?, ruled_at=? WHERE task_id=?""",
        (ruling_id, worker_pct, ruling_summary, ts, task_id),
    )
    conn.execute(
        "UPDATE bank_escrow SET status='split', resolved_at=? WHERE escrow_id=?",
        (ts, escrow_id),
    )
    conn.commit()

    eid = insert_event(conn, "court", "ruling.delivered", task_id, None,
                       f"Ruling: worker receives {worker_pct}% ({worker_amount} tokens)",
                       {"ruling_id": ruling_id, "claim_id": claim_id, "worker_pct": worker_pct, "summary": ruling_summary})
    print(f"  [{eid}] RULING  Worker receives {worker_pct}% ({worker_amount} tokens)")

    # Feedback (dissatisfied, both sides)
    for from_id, from_name, to_id, to_name, role, category in [
        (poster_id, poster_name, worker_id, worker_name, "poster", "delivery_quality"),
        (worker_id, worker_name, poster_id, poster_name, "worker", "spec_quality"),
    ]:
        fb_id = next_feedback_id()
        rating = "dissatisfied"
        comment = random.choice(FEEDBACK_COMMENTS[rating])
        conn.execute(
            "INSERT INTO reputation_feedback (feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, submitted_at, visible) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fb_id, task_id, from_id, to_id, role, category, rating, comment, now_ts(), 1),
        )
        conn.commit()
        eid = insert_event(conn, "reputation", "feedback.revealed", task_id, from_id,
                           f"Feedback revealed: {from_name} → {to_name} ({category})",
                           {"task_id": task_id, "from_name": from_name, "to_name": to_name, "category": category})
        print(f"  [{eid}] REP     Feedback: {from_name} → {to_name} ({category}: {rating})")


# ---------------------------------------------------------------------------
# Scenario: just a new task posted (stays open, bumps open count + escrow)
# ---------------------------------------------------------------------------

def scenario_new_task(conn: sqlite3.Connection, delay: tuple[float, float]) -> None:
    poster_id, poster_name = pick_agent()
    title = random.choice(TASK_TITLES)
    reward = random.choice([50, 80, 100, 120, 150, 200, 250, 300])
    task_id = next_task_id()
    escrow_id = next_escrow_id()
    ts = now_ts()

    conn.execute(
        "INSERT INTO bank_escrow (escrow_id, payer_account_id, amount, task_id, status, created_at) VALUES (?,?,?,?,?,?)",
        (escrow_id, poster_id, reward, task_id, "locked", ts),
    )
    conn.execute(
        """INSERT INTO board_tasks (
            task_id, poster_id, title, spec, reward, status,
            bidding_deadline_seconds, deadline_seconds, review_deadline_seconds,
            bidding_deadline, escrow_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (task_id, poster_id, title, random.choice(SPECS), reward, "open",
         3600, 7200, 3600, ts, escrow_id, ts),
    )
    conn.commit()

    eid = insert_event(conn, "board", "task.created", task_id, poster_id,
                       f"{poster_name} posted '{title}' for {reward} tokens",
                       {"title": title, "reward": reward})
    print(f"  [{eid}] TASK    {poster_name} posted '{title}' for {reward} tokens")

    eid = insert_event(conn, "bank", "escrow.locked", task_id, poster_id,
                       f"{poster_name} locked {reward} tokens in escrow",
                       {"escrow_id": escrow_id, "amount": reward, "title": title})
    print(f"  [{eid}] ESCROW  {poster_name} locked {reward} tokens in escrow")


# ---------------------------------------------------------------------------
# Scenario: salary round (credits all agents)
# ---------------------------------------------------------------------------

def scenario_salary(conn: sqlite3.Connection, delay: tuple[float, float]) -> None:
    amount = random.choice([100, 200, 500])
    for agent_id, agent_name in AGENTS:
        conn.execute(
            "UPDATE bank_accounts SET balance = balance + ? WHERE account_id = ?",
            (amount, agent_id),
        )
        conn.commit()
        eid = insert_event(conn, "bank", "salary.paid", None, agent_id,
                           f"{agent_name} received salary of {amount} tokens",
                           {"amount": amount})
        print(f"  [{eid}] SALARY  {agent_name} received {amount} tokens")
        time.sleep(random.uniform(delay[0] * 0.3, delay[1] * 0.3))


# ---------------------------------------------------------------------------
# Weighted scenario selection
# ---------------------------------------------------------------------------

SCENARIOS = [
    (35, "task_lifecycle", scenario_task_lifecycle),
    (20, "task_dispute", scenario_task_dispute),
    (35, "new_task", scenario_new_task),
    (10, "salary", scenario_salary),
]


def pick_scenario() -> tuple[str, callable]:
    total = sum(w for w, _, _ in SCENARIOS)
    r = random.randint(1, total)
    cumulative = 0
    for weight, name, fn in SCENARIOS:
        cumulative += weight
        if r <= cumulative:
            return name, fn
    return SCENARIOS[-1][1], SCENARIOS[-1][2]


def main() -> None:
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        print("Run seed.py first to create the database.")
        sys.exit(1)

    if "--fast" in sys.argv:
        delay_range = (0.5, 1.5)
        label = "fast"
    elif "--slow" in sys.argv:
        delay_range = (5.0, 10.0)
        label = "slow"
    else:
        delay_range = (2.0, 5.0)
        label = "normal"

    print(f"Simulating economy events ({label} mode, {delay_range[0]}-{delay_range[1]}s between events)")
    print(f"Database: {DB_PATH}")
    print("Press Ctrl+C to stop\n")

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # relaxed for simulation

    try:
        cycle = 0
        while True:
            cycle += 1
            name, fn = pick_scenario()
            print(f"--- Scenario {cycle}: {name} ---")
            fn(conn, delay_range)
            time.sleep(random.uniform(*delay_range))
    except KeyboardInterrupt:
        print("\nSimulation stopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
