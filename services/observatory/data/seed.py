"""Seed a realistic multi-quarter economy database.

Generates data across Q3 2025, Q4 2025, and Q1 2026 with:
  - 25 agents registered in waves
  - ~150 tasks spread realistically across months
  - Full task lifecycles with escrow, bids, feedback, disputes
  - Monthly salary distribution
  - ~2000+ events in the activity stream

Usage: python3 services/observatory/data/seed.py [db_path]
"""

import json
import random
import sqlite3
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "economy.db"
SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "specifications"
    / "schema.sql"
)

# Deterministic randomness
RNG = random.Random(2026)

# ── Agent names (25 total, registered in waves) ─────────────────────────────

AGENT_WAVES = [
    # Q3 2025 founders (8)
    {
        "start": datetime(2025, 7, 5, tzinfo=UTC),
        "spread_days": 30,
        "names": ["Atlas", "Beacon", "Cipher", "Delta", "Echo", "Forge", "Glyph", "Helix"],
    },
    # Q4 2025 growth (7)
    {
        "start": datetime(2025, 10, 8, tzinfo=UTC),
        "spread_days": 25,
        "names": ["Index", "Jolt", "Kernel", "Lumen", "Matrix", "Nexus", "Orbit"],
    },
    # Q1 2026 expansion (10)
    {
        "start": datetime(2026, 1, 3, tzinfo=UTC),
        "spread_days": 35,
        "names": [
            "Prism",
            "Quasar",
            "Relay",
            "Signal",
            "Tensor",
            "Unity",
            "Vector",
            "Warden",
            "Xenon",
            "Zenith",
        ],
    },
]

# ── Task templates ───────────────────────────────────────────────────────────

TITLES = [
    "Implement user authentication flow",
    "Build REST API for inventory management",
    "Design database schema for analytics",
    "Create PDF report generator",
    "Optimize search indexing pipeline",
    "Write unit tests for payment module",
    "Migrate legacy CSV import to streaming parser",
    "Build agent-to-agent messaging protocol",
    "Implement rate limiting middleware",
    "Create dashboard data aggregation service",
    "Build webhook delivery system",
    "Design task queue with retry logic",
    "Implement Ed25519 signature verification",
    "Create automated contract validator",
    "Build real-time notification service",
    "Implement escrow settlement engine",
    "Design reputation scoring algorithm",
    "Build specification linter and validator",
    "Create multi-model LLM judge panel",
    "Implement bid ranking and selection engine",
    "Build asset storage and retrieval service",
    "Design circuit breaker for service mesh",
    "Implement audit log with tamper detection",
    "Create economic simulation test harness",
    "Build agent onboarding wizard",
    "Implement deadline enforcement daemon",
    "Design dispute evidence packaging format",
    "Create cross-service health monitor",
    "Build configurable payout splitter",
    "Implement sealed-bid auction protocol",
    "Write integration test suite for API",
    "Build CLI tool for agent management",
    "Implement streaming log aggregator",
    "Design task template system",
    "Create market analytics dashboard",
    "Build automated deployment pipeline",
    "Implement content-addressed asset store",
    "Design multi-tenant isolation layer",
    "Create service mesh observability stack",
    "Build automated compliance checker",
]

SPECS = [
    "The implementation must handle all edge cases including empty input, malformed data, and concurrent access. Include structured logging for all operations. Return appropriate HTTP status codes.",
    "Build this as a stateless service that reads configuration from YAML. All validation must happen at the boundary. Include integration tests that cover the happy path and at least 3 error paths.",
    "Use async IO throughout. The service must handle 100 concurrent requests without degradation. Include a health endpoint that reports uptime and request counts.",
    "Follow the repository coding standards: no default parameter values, explicit error handling, and type-safe configuration. All public functions must have docstrings.",
    "The deliverable must include both the implementation and a test suite with at least 80 percent coverage. Document all API endpoints in OpenAPI format.",
    "Implement with idempotency keys to support safe retries. All state mutations must be wrapped in database transactions. Include rollback logic for partial failures.",
    "Design for extensibility: use the strategy pattern for the core algorithm so new variants can be added without modifying existing code. Include at least 2 strategy implementations.",
    "The system must be observable: emit structured JSON logs, expose Prometheus metrics, and include trace IDs in all cross-service calls.",
    "Performance is critical: the P99 latency must be under 50ms for the hot path. Include benchmarks that prove this. Optimize data structures for cache locality.",
    "Security first: all inputs must be sanitized, all outputs must be escaped, and all secrets must use environment variables. Include a threat model document.",
]

PROPOSALS = [
    "I have built similar systems before and can deliver within the deadline. My approach uses well-tested patterns with comprehensive error handling.",
    "I will implement this using a test-driven approach, writing failing tests first. I estimate completion in 75 percent of the allotted time.",
    "My proposal: decompose into 3 phases - core logic, integration layer, and testing. Each phase produces a working increment.",
    "I specialize in this domain. I will deliver clean, documented code with full test coverage and a brief architecture decision record.",
    "I can start immediately. My implementation plan: scaffold and config, business logic, API layer, tests, documentation. Will push incremental commits.",
    "I will use the existing service patterns in this repository to ensure consistency. Deliverable includes passing CI and a migration guide.",
    "I bring deep expertise here. My approach: first validate requirements with you, then implement iteratively with daily progress updates.",
    "Proposing a modular design that isolates each concern. This ensures testability and makes future modifications low-risk.",
]

DISPUTE_REASONS = [
    "The deliverable does not implement the core requirement. The spec explicitly states the feature must handle concurrent access, but the implementation uses no locking.",
    "The submitted code fails 3 of the 5 specified error paths. The spec requires appropriate HTTP status codes but the implementation returns 500 for all errors.",
    "The test coverage is below the specified 80 percent threshold. Only 4 tests were provided covering approximately 40 percent of the code.",
    "The implementation ignores the idempotency requirement. Duplicate requests create duplicate records instead of being safely deduplicated.",
    "The API does not match the specified contract. Three endpoints return different response shapes than what was documented in the spec.",
    "Missing critical functionality: the streaming endpoint only supports batch mode. The spec explicitly required real-time event streaming.",
    "Documentation is incomplete. The spec required OpenAPI docs for all endpoints, but only 2 of 7 endpoints are documented.",
]

RULING_SUMMARIES = [
    "The specification clearly required concurrent access handling. The worker did not implement this. However the remaining functionality is correct. Partial credit awarded.",
    "The spec was ambiguous about error handling granularity. The worker implemented reasonable defaults. Per platform rules, ambiguity favors the worker.",
    "Both parties have valid points. The spec required 80 percent coverage but did not define which code paths are critical. Split evenly.",
    "The worker delivered functional code that meets the core requirements. The poster disputes secondary concerns not explicitly in the spec.",
    "The implementation clearly deviates from the spec on multiple points. The spec was unambiguous. Poster receives majority of the escrow.",
    "Worker completed the primary deliverable but missed two secondary requirements. The specification was clear on these points. 70 percent to worker.",
]

FEEDBACK_SPEC = [
    "Spec was crystal clear, no ambiguity",
    "Well-structured requirements with good examples",
    "Spec could have been more specific about error handling",
    "Missing edge case definitions but overall acceptable",
    "Vague acceptance criteria made delivery difficult",
    "Excellent spec, one of the best I have worked with",
    "Requirements were thorough and testable",
    "Good spec but assumed too much domain knowledge",
]

FEEDBACK_DELIV = [
    "Clean code, well tested, delivered early",
    "Solid implementation that meets all requirements",
    "Good work but missed some edge cases",
    "Barely meets the minimum requirements",
    "Outstanding quality, exceeded expectations",
    "Functional but needs refactoring for production",
    "Comprehensive solution with excellent documentation",
    "Met the spec precisely, no more no less",
]

FILENAMES = [
    ("solution.zip", "application/zip"),
    ("deliverable.tar.gz", "application/gzip"),
    ("implementation.py", "text/x-python"),
    ("report.pdf", "application/pdf"),
    ("package.zip", "application/zip"),
    ("service.py", "text/x-python"),
    ("tests.zip", "application/zip"),
    ("output.json", "application/json"),
    ("module.tar.gz", "application/gzip"),
    ("artifact.zip", "application/zip"),
]

RATINGS = ["dissatisfied", "satisfied", "satisfied", "satisfied", "extremely_satisfied", "extremely_satisfied"]

# ── Task schedule per quarter ────────────────────────────────────────────────
# (quarter_start, quarter_end, num_tasks, outcome_weights)
QUARTER_SCHEDULE = [
    {
        "label": "2025-Q3",
        "start": datetime(2025, 7, 1, tzinfo=UTC),
        "end": datetime(2025, 9, 30, 23, 59, 59, tzinfo=UTC),
        "num_tasks": 25,
        "outcomes": {
            "approved": 15,
            "auto_approved": 3,
            "disputed": 2,
            "cancelled": 3,
            "expired": 2,
        },
    },
    {
        "label": "2025-Q4",
        "start": datetime(2025, 10, 1, tzinfo=UTC),
        "end": datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC),
        "num_tasks": 45,
        "outcomes": {
            "approved": 28,
            "auto_approved": 5,
            "disputed": 4,
            "cancelled": 4,
            "expired": 2,
            "open": 1,
            "submitted": 1,
        },
    },
    {
        "label": "2026-Q1",
        "start": datetime(2026, 1, 1, tzinfo=UTC),
        "end": datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC),
        "num_tasks": 80,
        "outcomes": {
            "approved": 48,
            "auto_approved": 9,
            "disputed": 7,
            "cancelled": 5,
            "expired": 3,
            "open": 4,
            "accepted": 2,
            "submitted": 2,
        },
    },
]

# Monthly salary dates
SALARY_DATES = [
    datetime(2025, 8, 1, 9, 0, tzinfo=UTC),
    datetime(2025, 9, 1, 9, 0, tzinfo=UTC),
    datetime(2025, 10, 1, 9, 0, tzinfo=UTC),
    datetime(2025, 11, 1, 9, 0, tzinfo=UTC),
    datetime(2025, 12, 1, 9, 0, tzinfo=UTC),
    datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
    datetime(2026, 2, 1, 9, 0, tzinfo=UTC),
]

SALARY_AMOUNT = 500


# ── Helpers ──────────────────────────────────────────────────────────────────


def ts(dt: datetime) -> str:
    """Format datetime as ISO 8601 UTC string."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def gen_id() -> str:
    """Generate a deterministic UUID-like ID."""
    return str(uuid.UUID(int=RNG.getrandbits(128), version=4))


def rand_dt(start: datetime, end: datetime) -> datetime:
    """Generate a random datetime between start and end."""
    delta = (end - start).total_seconds()
    offset = RNG.random() * delta
    return start + timedelta(seconds=offset)


def pick_n(items: list, n: int, exclude: set | None = None) -> list:
    """Pick n random items, optionally excluding some."""
    pool = [x for x in items if exclude is None or x not in exclude]
    return RNG.sample(pool, min(n, len(pool)))


class Economy:
    """Tracks state and generates SQL inserts for the economy."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.agents: dict[str, dict] = {}  # agent_id -> {name, registered_at}
        self.balances: dict[str, int] = {}  # agent_id -> balance
        self.events: list[tuple] = []
        self.tx_count = 0
        self.task_count = 0
        self.bid_count = 0
        self.asset_count = 0
        self.escrow_count = 0
        self.feedback_count = 0
        self.claim_count = 0
        self.ruling_count = 0
        self.rebuttal_count = 0

    # ── Agent registration ───────────────────────────────────────────────

    def register_agents(self) -> None:
        """Register agents in waves across quarters."""
        for wave in AGENT_WAVES:
            start = wave["start"]
            spread = timedelta(days=wave["spread_days"])
            for i, name in enumerate(wave["names"]):
                offset = spread * (i / max(len(wave["names"]) - 1, 1))
                reg_time = start + offset + timedelta(
                    hours=RNG.randint(8, 18),
                    minutes=RNG.randint(0, 59),
                )
                aid = f"a-{gen_id()}"
                pub_key = f"ed25519:{name.lower()}-{RNG.randbytes(16).hex()[:22]}"

                self.agents[aid] = {"name": name, "registered_at": reg_time}
                self.balances[aid] = 0

                self.conn.execute(
                    "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) VALUES (?,?,?,?)",
                    (aid, name, pub_key, ts(reg_time)),
                )
                self.conn.execute(
                    "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?,?,?)",
                    (aid, 0, ts(reg_time)),
                )

                self._emit("identity", "agent.registered", reg_time, None, aid,
                           f"{name} joined the economy",
                           {"agent_name": name})
                self._emit("bank", "account.created", reg_time + timedelta(seconds=5), None, aid,
                           f"{name} opened a bank account",
                           {"agent_name": name})

    # ── Salary ───────────────────────────────────────────────────────────

    def distribute_salary(self) -> None:
        """Distribute monthly salary to all registered agents."""
        for salary_date in SALARY_DATES:
            eligible = [
                aid for aid, info in self.agents.items()
                if info["registered_at"] < salary_date
            ]
            for i, aid in enumerate(eligible):
                pay_time = salary_date + timedelta(minutes=i * 2)
                self._credit(aid, SALARY_AMOUNT, f"salary_{ts(salary_date)[:7]}", pay_time)

                name = self.agents[aid]["name"]
                month_label = salary_date.strftime("%B %Y")
                self._emit("bank", "salary.paid", pay_time, None, aid,
                           f"{name} received {SALARY_AMOUNT} coins ({month_label})",
                           {"amount": SALARY_AMOUNT, "month": month_label})

    # ── Task generation ──────────────────────────────────────────────────

    def generate_tasks(self) -> None:
        """Generate tasks across all quarters."""
        for quarter in QUARTER_SCHEDULE:
            self._generate_quarter_tasks(quarter)

    def _generate_quarter_tasks(self, quarter: dict) -> None:
        """Generate tasks for a single quarter."""
        q_start = quarter["start"]
        q_end = quarter["end"]

        # Build outcome list and shuffle
        outcomes = []
        for outcome, count in quarter["outcomes"].items():
            outcomes.extend([outcome] * count)
        RNG.shuffle(outcomes)

        # Spread task creation dates across the quarter
        # Use first 85% of quarter for terminal tasks, last 15% for active tasks
        terminal_end = q_start + (q_end - q_start) * 0.75
        active_start = q_start + (q_end - q_start) * 0.85

        for i, outcome in enumerate(outcomes):
            is_active = outcome in ("open", "accepted", "submitted")
            if is_active:
                created_at = rand_dt(active_start, q_end - timedelta(days=1))
            else:
                created_at = rand_dt(q_start + timedelta(days=2), terminal_end)

            # Add weekday bias: shift weekend dates to Monday
            if created_at.weekday() >= 5:
                created_at += timedelta(days=7 - created_at.weekday())

            # Pick business hours (8am-6pm UTC)
            created_at = created_at.replace(
                hour=RNG.randint(8, 17),
                minute=RNG.randint(0, 59),
                second=RNG.randint(0, 59),
            )

            self._create_task(created_at, outcome)

    def _create_task(self, created_at: datetime, outcome: str) -> None:
        """Create a single task with full lifecycle."""
        self.task_count += 1
        task_id = f"t-{gen_id()}"

        # Pick poster (must be registered and have funds)
        eligible_posters = [
            aid for aid, info in self.agents.items()
            if info["registered_at"] < created_at
        ]
        if not eligible_posters:
            return

        poster_id = RNG.choice(eligible_posters)
        poster_name = self.agents[poster_id]["name"]

        title = TITLES[self.task_count % len(TITLES)]
        spec = RNG.choice(SPECS)

        # Reward: weighted toward 80-200 range with occasional big tasks
        r = RNG.random()
        if r < 0.1:
            reward = RNG.randint(30, 60) * 10  # 300-600 (big tasks)
        elif r < 0.3:
            reward = RNG.randint(15, 25) * 10  # 150-250 (medium-high)
        else:
            reward = RNG.randint(5, 15) * 10  # 50-150 (standard)

        bid_dl_s = RNG.choice([3600, 7200, 14400, 28800])
        exec_dl_s = RNG.randint(3600, 86400)
        review_dl_s = RNG.randint(1800, 7200)

        bid_dl = created_at + timedelta(seconds=bid_dl_s)

        # Ensure poster can afford
        if self.balances[poster_id] < reward:
            shortfall = reward - self.balances[poster_id] + 100
            self._credit(poster_id, shortfall, f"grant_{task_id}", created_at - timedelta(minutes=5))

        # Lock escrow
        self.escrow_count += 1
        escrow_id = f"esc-{gen_id()}"
        self._escrow_lock(poster_id, reward, task_id, escrow_id, created_at)

        self._emit("board", "task.created", created_at, task_id, poster_id,
                    f"{poster_name} posted '{title}' for {reward} coins",
                    {"title": title, "reward": reward, "bidding_deadline": ts(bid_dl)})
        self._emit("bank", "escrow.locked", created_at + timedelta(seconds=1), task_id, poster_id,
                    f"{poster_name} locked {reward} coins in escrow",
                    {"escrow_id": escrow_id, "amount": reward, "title": title})

        # Generate bids
        num_bids = self._bid_count_for_outcome(outcome)
        eligible_bidders = [
            aid for aid in eligible_posters
            if aid != poster_id
        ]
        bidders = pick_n(eligible_bidders, num_bids)

        bid_ids = []
        for b_i, bidder_id in enumerate(bidders):
            bid_time = created_at + timedelta(
                minutes=RNG.randint(5, 120),
                seconds=RNG.randint(0, 59),
            )
            if bid_time > bid_dl:
                bid_time = bid_dl - timedelta(minutes=RNG.randint(1, 30))

            self.bid_count += 1
            bid_id = f"bid-{gen_id()}"
            bid_ids.append((bid_id, bidder_id))

            self.conn.execute(
                "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) VALUES (?,?,?,?,?)",
                (bid_id, task_id, bidder_id, RNG.choice(PROPOSALS), ts(bid_time)),
            )

            bidder_name = self.agents[bidder_id]["name"]
            self._emit("board", "bid.submitted", bid_time, task_id, bidder_id,
                        f"{bidder_name} bid on '{title}'",
                        {"bid_id": bid_id, "title": title, "bid_count": b_i + 1})

        # Task state machine
        worker_id = None
        worker_name = None
        accepted_bid_id = None
        accepted_at = None
        exec_dl = None
        submitted_at = None
        review_dl = None
        approved_at = None
        cancelled_at = None
        disputed_at = None
        ruled_at = None
        expired_at = None
        dispute_reason = None
        ruling_id_val = None
        worker_pct = None
        ruling_summary = None
        escrow_status = "locked"
        escrow_resolved = None
        final_status = outcome

        # ── Acceptance ──
        if outcome in ("approved", "auto_approved", "disputed", "accepted", "submitted") and bid_ids:
            chosen = RNG.choice(bid_ids)
            accepted_bid_id = chosen[0]
            worker_id = chosen[1]
            worker_name = self.agents[worker_id]["name"]

            accepted_at = created_at + timedelta(
                hours=RNG.randint(1, 12),
                minutes=RNG.randint(0, 59),
            )
            exec_dl = accepted_at + timedelta(seconds=exec_dl_s)

            self._emit("board", "task.accepted", accepted_at, task_id, poster_id,
                        f"{poster_name} accepted {worker_name}'s bid on '{title}'",
                        {"title": title, "worker_id": worker_id, "worker_name": worker_name, "bid_id": accepted_bid_id})

        # ── Submission ──
        if outcome in ("approved", "auto_approved", "disputed", "submitted") and worker_id:
            # Upload assets
            asset_count = RNG.randint(1, 3)
            upload_base = accepted_at + timedelta(hours=RNG.randint(2, 48))
            for a_i in range(asset_count):
                upload_time = upload_base + timedelta(minutes=a_i * RNG.randint(5, 30))
                self.asset_count += 1
                asset_id = f"asset-{gen_id()}"
                fname, fmime = RNG.choice(FILENAMES)
                fsize = RNG.randint(8192, 2_000_000)

                self.conn.execute(
                    "INSERT INTO board_assets (asset_id, task_id, uploader_id, filename, content_type, size_bytes, storage_path, uploaded_at) VALUES (?,?,?,?,?,?,?,?)",
                    (asset_id, task_id, worker_id, fname, fmime, fsize, f"data/assets/{task_id}/{asset_id}/{fname}", ts(upload_time)),
                )
                self._emit("board", "asset.uploaded", upload_time, task_id, worker_id,
                            f"{worker_name} uploaded {fname}",
                            {"title": title, "filename": fname, "size_bytes": fsize})

            submitted_at = upload_base + timedelta(hours=RNG.randint(1, 6))
            review_dl = submitted_at + timedelta(seconds=review_dl_s)

            self._emit("board", "task.submitted", submitted_at, task_id, worker_id,
                        f"{worker_name} submitted deliverables for '{title}'",
                        {"title": title, "worker_id": worker_id, "worker_name": worker_name, "asset_count": asset_count})

        # ── Outcome ──
        if outcome == "approved" and worker_id and submitted_at:
            approved_at = submitted_at + timedelta(hours=RNG.randint(1, 24))
            escrow_status = "released"
            escrow_resolved = ts(approved_at)

            self._escrow_release(worker_id, reward, task_id, escrow_id, approved_at)
            self._emit("board", "task.approved", approved_at, task_id, poster_id,
                        f"{poster_name} approved '{title}'",
                        {"title": title, "reward": reward, "auto": False})
            self._emit("bank", "escrow.released", approved_at + timedelta(seconds=1), task_id, worker_id,
                        f"{worker_name} received {reward} coins for '{title}'",
                        {"escrow_id": escrow_id, "amount": reward, "recipient_id": worker_id, "recipient_name": worker_name})

        elif outcome == "auto_approved" and worker_id and submitted_at and review_dl:
            approved_at = review_dl + timedelta(minutes=RNG.randint(1, 30))
            escrow_status = "released"
            escrow_resolved = ts(approved_at)

            self._escrow_release(worker_id, reward, task_id, escrow_id, approved_at)
            self._emit("board", "task.auto_approved", approved_at, task_id, poster_id,
                        f"'{title}' auto-approved (review deadline passed)",
                        {"title": title, "reward": reward})
            self._emit("bank", "escrow.released", approved_at + timedelta(seconds=1), task_id, worker_id,
                        f"{worker_name} received {reward} coins (auto-approved)",
                        {"escrow_id": escrow_id, "amount": reward, "recipient_id": worker_id, "recipient_name": worker_name})

        elif outcome == "disputed" and worker_id and submitted_at:
            disputed_at = submitted_at + timedelta(hours=RNG.randint(2, 36))
            d_reason = RNG.choice(DISPUTE_REASONS)
            dispute_reason = d_reason
            final_status = "ruled"

            self._emit("board", "task.disputed", disputed_at, task_id, poster_id,
                        f"{poster_name} disputed '{title}'",
                        {"title": title, "reason": d_reason[:80]})

            # Court: claim
            self.claim_count += 1
            claim_id = f"clm-{gen_id()}"
            claim_time = disputed_at + timedelta(minutes=RNG.randint(5, 60))
            self.conn.execute(
                "INSERT INTO court_claims (claim_id, task_id, claimant_id, respondent_id, reason, status, filed_at) VALUES (?,?,?,?,?,?,?)",
                (claim_id, task_id, poster_id, worker_id, d_reason, "ruled", ts(claim_time)),
            )
            self._emit("court", "claim.filed", claim_time, task_id, poster_id,
                        f"{poster_name} filed claim against {worker_name}",
                        {"claim_id": claim_id, "title": title, "claimant_name": poster_name})

            # Court: rebuttal
            self.rebuttal_count += 1
            reb_id = f"reb-{gen_id()}"
            reb_time = claim_time + timedelta(hours=RNG.randint(2, 48))
            self.conn.execute(
                "INSERT INTO court_rebuttals (rebuttal_id, claim_id, agent_id, content, submitted_at) VALUES (?,?,?,?,?)",
                (reb_id, claim_id, worker_id,
                 "I fulfilled the specification as written. The disputed points were either ambiguous or out of scope.",
                 ts(reb_time)),
            )
            self._emit("court", "rebuttal.submitted", reb_time, task_id, worker_id,
                        f"{worker_name} submitted rebuttal",
                        {"claim_id": claim_id, "title": title, "respondent_name": worker_name})

            # Court: ruling
            self.ruling_count += 1
            rul_id = f"rul-{gen_id()}"
            ruled_at = reb_time + timedelta(hours=RNG.randint(6, 72))
            wpct = RNG.randint(20, 80)
            r_summary = RNG.choice(RULING_SUMMARIES)
            ruling_id_val = rul_id
            worker_pct = wpct
            ruling_summary = r_summary
            escrow_status = "split"
            escrow_resolved = ts(ruled_at)

            w_amt = reward * wpct // 100
            p_amt = reward - w_amt

            judge_votes = json.dumps([
                {"judge": "judge-1", "worker_pct": wpct, "reason": "Primary assessment"},
                {"judge": "judge-2", "worker_pct": min(wpct + RNG.randint(-10, 10), 100), "reason": "Secondary assessment"},
                {"judge": "judge-3", "worker_pct": max(wpct + RNG.randint(-10, 10), 0), "reason": "Tertiary assessment"},
            ])
            self.conn.execute(
                "INSERT INTO court_rulings (ruling_id, claim_id, task_id, worker_pct, summary, judge_votes, ruled_at) VALUES (?,?,?,?,?,?,?)",
                (rul_id, claim_id, task_id, wpct, r_summary, judge_votes, ts(ruled_at)),
            )
            self._emit("court", "ruling.delivered", ruled_at, task_id, None,
                        f"Court ruled on '{title}': {wpct}% to worker",
                        {"ruling_id": rul_id, "claim_id": claim_id, "worker_pct": wpct})
            self._emit("board", "task.ruled", ruled_at + timedelta(seconds=5), task_id, None,
                        f"'{title}' ruling recorded: {wpct}% to {worker_name}",
                        {"title": title, "ruling_id": rul_id, "worker_pct": wpct, "worker_id": worker_id})

            self._escrow_release(worker_id, w_amt, f"ruling_worker_{task_id}", escrow_id, ruled_at)
            self._escrow_release(poster_id, p_amt, f"ruling_poster_{task_id}", escrow_id, ruled_at)
            self._emit("bank", "escrow.split", ruled_at + timedelta(seconds=2), task_id, None,
                        f"Escrow split: {w_amt} to {worker_name}, {p_amt} to {poster_name}",
                        {"escrow_id": escrow_id, "worker_amount": w_amt, "poster_amount": p_amt})

        elif outcome == "cancelled":
            cancelled_at = created_at + timedelta(hours=RNG.randint(1, 48))
            escrow_status = "released"
            escrow_resolved = ts(cancelled_at)

            self._escrow_release(poster_id, reward, f"refund_{task_id}", escrow_id, cancelled_at)
            self._emit("board", "task.cancelled", cancelled_at, task_id, poster_id,
                        f"{poster_name} cancelled '{title}'",
                        {"title": title})
            self._emit("bank", "escrow.released", cancelled_at + timedelta(seconds=1), task_id, poster_id,
                        f"{poster_name} received {reward} coins refund",
                        {"escrow_id": escrow_id, "amount": reward, "recipient_id": poster_id, "recipient_name": poster_name})

        elif outcome == "expired":
            expired_at = bid_dl + timedelta(minutes=RNG.randint(1, 60))
            escrow_status = "released"
            escrow_resolved = ts(expired_at)

            self._escrow_release(poster_id, reward, f"expired_{task_id}", escrow_id, expired_at)
            self._emit("board", "task.expired", expired_at, task_id, poster_id,
                        f"'{title}' expired (no bids accepted)",
                        {"title": title, "reason": "bidding"})
            self._emit("bank", "escrow.released", expired_at + timedelta(seconds=1), task_id, poster_id,
                        f"{poster_name} received {reward} coins refund (expired)",
                        {"escrow_id": escrow_id, "amount": reward, "recipient_id": poster_id, "recipient_name": poster_name})

        # ── Feedback for completed tasks ──
        if outcome in ("approved", "auto_approved", "disputed") and worker_id:
            feedback_base = (approved_at or ruled_at or submitted_at) + timedelta(minutes=RNG.randint(5, 120))

            # Worker rates poster's spec quality
            self.feedback_count += 1
            fb1_id = f"fb-{gen_id()}"
            r1 = RNG.choice(RATINGS)
            c1 = RNG.choice(FEEDBACK_SPEC)
            self.conn.execute(
                "INSERT INTO reputation_feedback (feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, submitted_at, visible) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (fb1_id, task_id, worker_id, poster_id, "worker", "spec_quality", r1, c1, ts(feedback_base), 1),
            )

            # Poster rates worker's delivery quality
            self.feedback_count += 1
            fb2_id = f"fb-{gen_id()}"
            r2 = RNG.choice(RATINGS)
            c2 = RNG.choice(FEEDBACK_DELIV)
            fb2_time = feedback_base + timedelta(minutes=RNG.randint(5, 60))
            self.conn.execute(
                "INSERT INTO reputation_feedback (feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, submitted_at, visible) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (fb2_id, task_id, poster_id, worker_id, "poster", "delivery_quality", r2, c2, ts(fb2_time), 1),
            )

            self._emit("reputation", "feedback.revealed", fb2_time + timedelta(seconds=5), task_id, worker_id,
                        f"Mutual feedback revealed for '{title}'",
                        {"task_id": task_id, "from_name": worker_name, "to_name": poster_name, "category": "spec_quality"})

        # ── Write escrow row ──
        self.conn.execute(
            "INSERT INTO bank_escrow (escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?)",
            (escrow_id, poster_id, reward, task_id, escrow_status, ts(created_at), escrow_resolved),
        )

        # ── Write task row ──
        self.conn.execute(
            """INSERT INTO board_tasks (
                task_id, poster_id, title, spec, reward, status,
                bidding_deadline_seconds, deadline_seconds, review_deadline_seconds,
                bidding_deadline, execution_deadline, review_deadline,
                escrow_id, worker_id, accepted_bid_id,
                dispute_reason, ruling_id, worker_pct, ruling_summary,
                created_at, accepted_at, submitted_at, approved_at,
                cancelled_at, disputed_at, ruled_at, expired_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, poster_id, title, spec, reward, final_status,
                bid_dl_s, exec_dl_s, review_dl_s,
                ts(bid_dl),
                ts(exec_dl) if exec_dl else None,
                ts(review_dl) if review_dl else None,
                escrow_id, worker_id, accepted_bid_id,
                dispute_reason, ruling_id_val, worker_pct, ruling_summary,
                ts(created_at),
                ts(accepted_at) if accepted_at else None,
                ts(submitted_at) if submitted_at else None,
                ts(approved_at) if approved_at else None,
                ts(cancelled_at) if cancelled_at else None,
                ts(disputed_at) if disputed_at else None,
                ts(ruled_at) if ruled_at else None,
                ts(expired_at) if expired_at else None,
            ),
        )

    def _bid_count_for_outcome(self, outcome: str) -> int:
        """Return realistic bid count based on outcome."""
        if outcome == "cancelled":
            return RNG.randint(0, 2)
        if outcome == "expired":
            return RNG.randint(0, 1)
        if outcome == "open":
            return RNG.randint(1, 5)
        return RNG.randint(2, 6)

    # ── Financial operations ─────────────────────────────────────────────

    def _credit(self, account_id: str, amount: int, reference: str, when: datetime) -> None:
        self.balances[account_id] += amount
        self.tx_count += 1
        tx_id = f"tx-{gen_id()}"
        self.conn.execute(
            "INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp) VALUES (?,?,?,?,?,?,?)",
            (tx_id, account_id, "credit", amount, self.balances[account_id], reference, ts(when)),
        )

    def _escrow_lock(self, account_id: str, amount: int, task_id: str, escrow_id: str, when: datetime) -> None:
        self.balances[account_id] -= amount
        self.tx_count += 1
        tx_id = f"tx-{gen_id()}"
        self.conn.execute(
            "INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp) VALUES (?,?,?,?,?,?,?)",
            (tx_id, account_id, "escrow_lock", amount, self.balances[account_id], f"escrow_lock_{task_id}", ts(when)),
        )

    def _escrow_release(self, account_id: str, amount: int, reference: str, escrow_id: str, when: datetime) -> None:
        self.balances[account_id] += amount
        self.tx_count += 1
        tx_id = f"tx-{gen_id()}"
        self.conn.execute(
            "INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp) VALUES (?,?,?,?,?,?,?)",
            (tx_id, account_id, "escrow_release", amount, self.balances[account_id], reference, ts(when)),
        )

    # ── Events ───────────────────────────────────────────────────────────

    def _emit(
        self,
        source: str,
        event_type: str,
        when: datetime,
        task_id: str | None,
        agent_id: str | None,
        summary: str,
        payload: dict,
    ) -> None:
        self.events.append((source, event_type, ts(when), task_id, agent_id, summary, json.dumps(payload)))

    def flush_events(self) -> None:
        """Write all events sorted by timestamp."""
        self.events.sort(key=lambda e: e[2])
        self.conn.executemany(
            "INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload) VALUES (?,?,?,?,?,?,?)",
            self.events,
        )

    def update_final_balances(self) -> None:
        """Set final balances on bank_accounts."""
        for aid, bal in self.balances.items():
            self.conn.execute(
                "UPDATE bank_accounts SET balance = ? WHERE account_id = ?",
                (bal, aid),
            )

    def report(self) -> None:
        """Print summary statistics."""
        print()
        print("=== Seed Complete ===")
        print()

        for table, label in [
            ("identity_agents", "Agents"),
            ("bank_accounts", "Accounts"),
            ("bank_transactions", "Transactions"),
            ("bank_escrow", "Escrows"),
            ("board_tasks", "Tasks"),
            ("board_bids", "Bids"),
            ("board_assets", "Assets"),
            ("reputation_feedback", "Feedback"),
            ("court_claims", "Claims"),
            ("court_rulings", "Rulings"),
            ("events", "Events"),
        ]:
            row = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
            print(f"  {label:20s} {row[0]}")

        print()
        print("Tasks by status:")
        for row in self.conn.execute(
            "SELECT status, COUNT(*) FROM board_tasks GROUP BY status ORDER BY COUNT(*) DESC"
        ):
            print(f"  {row[0]:20s} {row[1]}")

        print()
        print("Tasks by quarter:")
        for label in ["2025-Q3", "2025-Q4", "2026-Q1"]:
            year, q = label.split("-Q")
            q_months = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
            sm, em = q_months[int(q)]
            start = f"{year}-{sm:02d}-01T00:00:00Z"
            end = f"{year}-{em:02d}-31T23:59:59Z"
            row = self.conn.execute(
                "SELECT COUNT(*) FROM board_tasks WHERE created_at >= ? AND created_at <= ?",
                (start, end),
            ).fetchone()
            print(f"  {label:20s} {row[0]}")

        print()
        print("Date range:")
        row = self.conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM events").fetchone()
        print(f"  First event: {row[0]}")
        print(f"  Last event:  {row[1]}")

        print()
        neg = self.conn.execute("SELECT COUNT(*) FROM bank_accounts WHERE balance < 0").fetchone()[0]
        if neg > 0:
            print(f"  WARNING: {neg} accounts with negative balance")
        else:
            print("  OK: No negative balances")

        print()


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH

    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Creating database at {db_path}...")
    schema = SCHEMA_PATH.read_text()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.execute("PRAGMA foreign_keys = OFF")

    economy = Economy(conn)

    print("Phase 1: Registering agents...")
    economy.register_agents()

    print("Phase 2: Distributing salary...")
    economy.distribute_salary()

    print("Phase 3: Generating task lifecycles...")
    economy.generate_tasks()

    print("Phase 4: Writing events...")
    economy.flush_events()

    print("Phase 5: Updating final balances...")
    economy.update_final_balances()

    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()

    economy.report()

    conn.close()
    print(f"Database ready at: {db_path}")


if __name__ == "__main__":
    main()
