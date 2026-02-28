-- Agent Task Economy — Unified SQLite Schema
-- Single file: data/economy.db
-- Table prefix per service domain
-- All timestamps are ISO 8601 UTC
-- All IDs are TEXT with format prefixes (a-, t-, bid-, esc-, etc.)

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- ============================================================================
-- IDENTITY
-- Owner: Identity Service (8001)
-- Readers: all services (for agent name resolution)
-- ============================================================================

CREATE TABLE identity_agents (
    agent_id       TEXT PRIMARY KEY,            -- "a-<uuid4>"
    name           TEXT NOT NULL,
    public_key     TEXT NOT NULL UNIQUE,        -- "ed25519:<base64>"
    registered_at  TEXT NOT NULL                -- ISO 8601
);


-- ============================================================================
-- CENTRAL BANK
-- Owner: Central Bank Service (8002)
-- Readers: UI (balances, escrow totals, transaction history)
-- ============================================================================

CREATE TABLE bank_accounts (
    account_id     TEXT PRIMARY KEY,            -- same as agent_id
    balance        INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,

    FOREIGN KEY (account_id) REFERENCES identity_agents (agent_id)
);

CREATE TABLE bank_transactions (
    tx_id          TEXT PRIMARY KEY,            -- "tx-<uuid4>"
    account_id     TEXT NOT NULL,
    type           TEXT NOT NULL,               -- "credit" | "escrow_lock" | "escrow_release"
    amount         INTEGER NOT NULL,            -- always positive
    balance_after  INTEGER NOT NULL,
    reference      TEXT NOT NULL,               -- idempotency key (e.g. "salary_round_3", task_id)
    timestamp      TEXT NOT NULL,

    FOREIGN KEY (account_id) REFERENCES bank_accounts (account_id)
);

CREATE UNIQUE INDEX idx_bank_tx_idempotent
    ON bank_transactions (account_id, reference)
    WHERE type = 'credit';

CREATE INDEX idx_bank_tx_history
    ON bank_transactions (account_id, timestamp, tx_id);

CREATE TABLE bank_escrow (
    escrow_id        TEXT PRIMARY KEY,          -- "esc-<uuid4>"
    payer_account_id TEXT NOT NULL,
    amount           INTEGER NOT NULL,
    task_id          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'locked',  -- "locked" | "released" | "split"
    created_at       TEXT NOT NULL,
    resolved_at      TEXT,                      -- null while locked

    FOREIGN KEY (payer_account_id) REFERENCES bank_accounts (account_id)
);

CREATE UNIQUE INDEX idx_bank_escrow_active
    ON bank_escrow (payer_account_id, task_id)
    WHERE status = 'locked';


-- ============================================================================
-- TASK BOARD
-- Owner: Task Board Service (8003)
-- Readers: UI, Court
-- ============================================================================

CREATE TABLE board_tasks (
    task_id                  TEXT PRIMARY KEY,  -- "t-<uuid4>"
    poster_id                TEXT NOT NULL,
    title                    TEXT NOT NULL,
    spec                     TEXT NOT NULL,
    reward                   INTEGER NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'open',
                                               -- open | accepted | submitted
                                               -- approved | cancelled | expired
                                               -- disputed | ruled

    -- deadlines (durations in seconds, set at creation)
    bidding_deadline_seconds   INTEGER NOT NULL,
    deadline_seconds           INTEGER NOT NULL,
    review_deadline_seconds    INTEGER NOT NULL,

    -- computed absolute deadlines (set when relevant transition occurs)
    bidding_deadline           TEXT NOT NULL,   -- created_at + bidding_deadline_seconds
    execution_deadline         TEXT,            -- accepted_at + deadline_seconds
    review_deadline            TEXT,            -- submitted_at + review_deadline_seconds

    -- escrow
    escrow_id                TEXT NOT NULL,

    -- assignment
    worker_id                TEXT,
    accepted_bid_id          TEXT,

    -- dispute / ruling
    dispute_reason           TEXT,
    ruling_id                TEXT,
    worker_pct               INTEGER,          -- 0-100, court-determined
    ruling_summary           TEXT,

    -- lifecycle timestamps (null until transition occurs)
    created_at               TEXT NOT NULL,
    accepted_at              TEXT,
    submitted_at             TEXT,
    approved_at              TEXT,
    cancelled_at             TEXT,
    disputed_at              TEXT,
    ruled_at                 TEXT,
    expired_at               TEXT,

    FOREIGN KEY (poster_id) REFERENCES identity_agents (agent_id),
    FOREIGN KEY (worker_id) REFERENCES identity_agents (agent_id),
    FOREIGN KEY (escrow_id) REFERENCES bank_escrow (escrow_id)
);

CREATE INDEX idx_board_tasks_status    ON board_tasks (status);
CREATE INDEX idx_board_tasks_poster    ON board_tasks (poster_id);
CREATE INDEX idx_board_tasks_worker    ON board_tasks (worker_id);
CREATE INDEX idx_board_tasks_created   ON board_tasks (created_at);

CREATE TABLE board_bids (
    bid_id         TEXT PRIMARY KEY,            -- "bid-<uuid4>"
    task_id        TEXT NOT NULL,
    bidder_id      TEXT NOT NULL,
    proposal       TEXT NOT NULL,
    submitted_at   TEXT NOT NULL,

    FOREIGN KEY (task_id)   REFERENCES board_tasks (task_id),
    FOREIGN KEY (bidder_id) REFERENCES identity_agents (agent_id)
);

CREATE UNIQUE INDEX idx_board_bids_one_per_agent
    ON board_bids (task_id, bidder_id);

CREATE TABLE board_assets (
    asset_id       TEXT PRIMARY KEY,            -- "asset-<uuid4>"
    task_id        TEXT NOT NULL,
    uploader_id    TEXT NOT NULL,
    filename       TEXT NOT NULL,
    content_type   TEXT NOT NULL,               -- MIME type
    size_bytes     INTEGER NOT NULL,
    storage_path   TEXT NOT NULL,               -- path on disk
    uploaded_at    TEXT NOT NULL,

    FOREIGN KEY (task_id)     REFERENCES board_tasks (task_id),
    FOREIGN KEY (uploader_id) REFERENCES identity_agents (agent_id)
);

CREATE INDEX idx_board_assets_task ON board_assets (task_id);


-- ============================================================================
-- REPUTATION
-- Owner: Reputation Service (8004)
-- Readers: UI, Court
-- ============================================================================

CREATE TABLE reputation_feedback (
    feedback_id    TEXT PRIMARY KEY,            -- "fb-<uuid4>"
    task_id        TEXT NOT NULL,
    from_agent_id  TEXT NOT NULL,
    to_agent_id    TEXT NOT NULL,
    role           TEXT NOT NULL,               -- "poster" | "worker" (role of the reviewer)
    category       TEXT NOT NULL,               -- "spec_quality" | "delivery_quality"
    rating         TEXT NOT NULL,               -- "dissatisfied" | "satisfied" | "extremely_satisfied"
    comment        TEXT,
    submitted_at   TEXT NOT NULL,
    visible        INTEGER NOT NULL DEFAULT 0,  -- 0 = sealed, 1 = revealed
                                                -- revealed when both parties submit

    FOREIGN KEY (task_id)       REFERENCES board_tasks (task_id),
    FOREIGN KEY (from_agent_id) REFERENCES identity_agents (agent_id),
    FOREIGN KEY (to_agent_id)   REFERENCES identity_agents (agent_id)
);

CREATE UNIQUE INDEX idx_reputation_one_per_direction
    ON reputation_feedback (task_id, from_agent_id, to_agent_id);

CREATE INDEX idx_reputation_to_agent
    ON reputation_feedback (to_agent_id);

CREATE INDEX idx_reputation_task
    ON reputation_feedback (task_id);

CREATE INDEX idx_reputation_visible
    ON reputation_feedback (visible, to_agent_id);


-- ============================================================================
-- COURT
-- Owner: Court Service (8005)
-- Readers: UI
-- ============================================================================

CREATE TABLE court_claims (
    claim_id        TEXT PRIMARY KEY,           -- "clm-<uuid4>"
    task_id         TEXT NOT NULL,
    claimant_id     TEXT NOT NULL,              -- poster who filed
    respondent_id   TEXT NOT NULL,              -- worker being disputed
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'filed',  -- "filed" | "rebuttal" | "judging" | "ruled"
    filed_at        TEXT NOT NULL,

    FOREIGN KEY (task_id)       REFERENCES board_tasks (task_id),
    FOREIGN KEY (claimant_id)   REFERENCES identity_agents (agent_id),
    FOREIGN KEY (respondent_id) REFERENCES identity_agents (agent_id)
);

CREATE INDEX idx_court_claims_task ON court_claims (task_id);

CREATE TABLE court_rebuttals (
    rebuttal_id    TEXT PRIMARY KEY,            -- "reb-<uuid4>"
    claim_id       TEXT NOT NULL,
    agent_id       TEXT NOT NULL,               -- respondent
    content        TEXT NOT NULL,
    submitted_at   TEXT NOT NULL,

    FOREIGN KEY (claim_id) REFERENCES court_claims (claim_id),
    FOREIGN KEY (agent_id) REFERENCES identity_agents (agent_id)
);

CREATE TABLE court_rulings (
    ruling_id      TEXT PRIMARY KEY,            -- "rul-<uuid4>"
    claim_id       TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    worker_pct     INTEGER NOT NULL,            -- 0-100
    summary        TEXT NOT NULL,
    judge_votes    TEXT NOT NULL,               -- JSON array of individual judge decisions
    ruled_at       TEXT NOT NULL,

    FOREIGN KEY (claim_id) REFERENCES court_claims (claim_id),
    FOREIGN KEY (task_id)  REFERENCES board_tasks (task_id)
);


-- ============================================================================
-- EVENTS (shared log)
-- Writers: all services
-- Readers: UI (via SSE stream + direct query)
-- ============================================================================

CREATE TABLE events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,  -- monotonic cursor for SSE polling
    event_source   TEXT NOT NULL,               -- "identity" | "bank" | "board" | "reputation" | "court"
    event_type     TEXT NOT NULL,               -- e.g. "task.created", "escrow.locked", "agent.registered"
    timestamp      TEXT NOT NULL,               -- ISO 8601
    task_id        TEXT,                        -- null for non-task events (agent.registered, salary.paid)
    agent_id       TEXT,                        -- primary actor who triggered the event
    summary        TEXT NOT NULL,               -- pre-rendered one-liner for feed display
    payload        TEXT NOT NULL DEFAULT '{}',  -- JSON blob, shape depends on event_source + event_type

    FOREIGN KEY (agent_id) REFERENCES identity_agents (agent_id)
);

CREATE INDEX idx_events_cursor     ON events (event_id);
CREATE INDEX idx_events_timestamp  ON events (timestamp);
CREATE INDEX idx_events_source     ON events (event_source);
CREATE INDEX idx_events_type       ON events (event_type);
CREATE INDEX idx_events_task       ON events (task_id);
CREATE INDEX idx_events_agent      ON events (agent_id);


-- ============================================================================
-- EVENT PAYLOAD REFERENCE (not a table — documentation)
-- ============================================================================
--
-- event_source: "identity"
--   agent.registered        { "agent_name": "Alice" }
--
-- event_source: "bank"
--   account.created         { "agent_name": "Alice" }
--   salary.paid             { "amount": 500 }
--   escrow.locked           { "escrow_id": "esc-...", "amount": 100, "title": "..." }
--   escrow.released         { "escrow_id": "esc-...", "amount": 100, "recipient_id": "a-...", "recipient_name": "Bob" }
--   escrow.split            { "escrow_id": "esc-...", "worker_amount": 70, "poster_amount": 30 }
--
-- event_source: "board"
--   task.created            { "title": "...", "reward": 100, "bidding_deadline": "..." }
--   task.cancelled          { "title": "..." }
--   task.expired            { "title": "...", "reason": "bidding|execution" }
--   bid.submitted           { "bid_id": "bid-...", "title": "...", "bid_count": 4 }
--   task.accepted           { "title": "...", "worker_id": "a-...", "worker_name": "Bob", "bid_id": "bid-..." }
--   asset.uploaded          { "title": "...", "filename": "login-page.zip", "size_bytes": 245760 }
--   task.submitted          { "title": "...", "worker_id": "a-...", "worker_name": "Bob", "asset_count": 3 }
--   task.approved           { "title": "...", "reward": 100, "auto": false }
--   task.auto_approved      { "title": "...", "reward": 100 }
--   task.disputed           { "title": "...", "reason": "The login page does not validate..." }
--   task.ruled              { "title": "...", "ruling_id": "rul-...", "worker_pct": 70, "worker_id": "a-..." }
--
-- event_source: "reputation"
--   feedback.revealed       { "task_id": "t-...", "from_name": "Alice", "to_name": "Bob", "category": "spec_quality" }
--
-- event_source: "court"
--   claim.filed             { "claim_id": "clm-...", "title": "...", "claimant_name": "Alice" }
--   rebuttal.submitted      { "claim_id": "clm-...", "title": "...", "respondent_name": "Bob" }
--   ruling.delivered        { "ruling_id": "rul-...", "claim_id": "clm-...", "worker_pct": 70, "summary": "..." }
--
