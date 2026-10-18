-- Agent Task Economy — Unified SQLite Schema v3 base
-- Single file: data/economy.db
-- Table prefix per service domain
-- All timestamps are ISO 8601 UTC
-- All IDs are TEXT with format prefixes (a-, t-, bid-, esc-, etc.)

-- Connection PRAGMAs are applied by DbWriter only after schema admission.

CREATE TABLE schema_metadata (
    singleton                        INTEGER PRIMARY KEY CHECK (singleton = 1),
    version                          INTEGER NOT NULL CHECK (version = 3),
    migrated_from_version            INTEGER,
    migration_backup_id              TEXT,
    migration_backup_manifest_sha256 TEXT,

    CHECK (
        (migrated_from_version IS NULL
         AND migration_backup_id IS NULL
         AND migration_backup_manifest_sha256 IS NULL)
        OR
        (migrated_from_version IS NOT NULL
         AND migrated_from_version = 2
         AND migration_backup_id IS NOT NULL
         AND migration_backup_manifest_sha256 IS NOT NULL)
    )
) WITHOUT ROWID;

INSERT INTO schema_metadata (
    singleton,
    version,
    migrated_from_version,
    migration_backup_id,
    migration_backup_manifest_sha256
) VALUES (1, 3, NULL, NULL, NULL);

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
    balance        INTEGER NOT NULL DEFAULT 0
                   CHECK (typeof(balance) = 'integer' AND balance BETWEEN 0 AND 9223372036854775807),
    created_at     TEXT NOT NULL,

    FOREIGN KEY (account_id) REFERENCES identity_agents (agent_id)
);

CREATE TABLE bank_transactions (
    tx_id          TEXT PRIMARY KEY,            -- "tx-<uuid4>"
    account_id     TEXT NOT NULL,
    type           TEXT NOT NULL,               -- "credit" | "escrow_lock" | "escrow_release"
    amount         INTEGER NOT NULL             -- always positive
                   CHECK (typeof(amount) = 'integer' AND amount BETWEEN 1 AND 9223372036854775807),
    balance_after  INTEGER NOT NULL
                   CHECK (typeof(balance_after) = 'integer' AND balance_after BETWEEN 0 AND 9223372036854775807),
    reference      TEXT NOT NULL,               -- idempotency key (e.g. "salary_round_3", task_id)
    timestamp      TEXT NOT NULL,
    event_id       INTEGER,                     -- event row emitted with this write; NULL for rows written before the column existed

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
    amount           INTEGER NOT NULL
                     CHECK (typeof(amount) = 'integer' AND amount BETWEEN 1 AND 9223372036854775807),
    task_id          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'locked',  -- "locked" | "released" | "split"
    created_at       TEXT NOT NULL,
    resolved_at      TEXT,                      -- null while locked
    event_id         INTEGER,                   -- event row emitted with this write; NULL for rows written before the column existed

    FOREIGN KEY (payer_account_id) REFERENCES bank_accounts (account_id)
);

CREATE UNIQUE INDEX idx_bank_escrow_lifetime
    ON bank_escrow (payer_account_id, task_id);


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
    reward                   INTEGER NOT NULL
                             CHECK (typeof(reward) = 'integer' AND reward BETWEEN 1 AND 9223372036854775807),
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
    bid_count                INTEGER NOT NULL DEFAULT 0,
    escrow_pending           INTEGER NOT NULL DEFAULT 0,

    -- escrow
    escrow_id                TEXT NOT NULL,

    -- assignment
    worker_id                TEXT,
    accepted_bid_id          TEXT,

    -- dispute / ruling
    dispute_reason           TEXT,
    dispute_id               TEXT,             -- court claim id, "disp-<uuid4>"
    rebuttal_deadline        TEXT,             -- court rebuttal window close (from file_claim)
    rebuttal_submitted_at    TEXT,             -- set when the worker rebuttal is forwarded
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
    amount         INTEGER NOT NULL DEFAULT 0
                   CHECK (typeof(amount) = 'integer' AND amount BETWEEN 0 AND 9223372036854775807),
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
    content_hash   TEXT,
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
    task_id         TEXT NOT NULL UNIQUE,
    claimant_id     TEXT NOT NULL,              -- poster who filed
    respondent_id   TEXT NOT NULL,              -- worker being disputed
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'filed',  -- "filed" | "rebuttal" | "judging" | "ruled"
    rebuttal_deadline TEXT,
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
    event_source   TEXT NOT NULL,               -- "identity" | "central-bank" | "board" | "reputation" | "court"
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
-- event_source: "central-bank"
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

-- MP-04 schema-v2 receipt ownership sidecar.
-- Applied only to an already admitted v1 schema by the explicit v1-to-v2 migration.

CREATE TABLE bank_receipt_cutover (
    singleton INTEGER PRIMARY KEY NOT NULL
              CHECK (typeof(singleton) = 'integer' AND singleton = 1),
    attestation_version INTEGER NOT NULL
              CHECK (typeof(attestation_version) = 'integer' AND attestation_version = 1),
    cutover_kind TEXT NOT NULL
              CHECK (typeof(cutover_kind) = 'text'
                     AND cutover_kind IN ('fresh_v2', 'v1_to_v2')),
    source_schema_version INTEGER,
    source_migrated_from_version INTEGER,
    source_migration_backup_id TEXT,
    source_migration_backup_manifest_sha256 TEXT,
    migration_backup_id TEXT,
    migration_backup_manifest_sha256 TEXT,
    migration_plan_sha256 TEXT,
    source_snapshot_sha256 TEXT,
    legacy_transaction_count INTEGER NOT NULL
              CHECK (typeof(legacy_transaction_count) = 'integer'
                     AND legacy_transaction_count >= 0),
    legacy_escrow_count INTEGER NOT NULL
              CHECK (typeof(legacy_escrow_count) = 'integer'
                     AND legacy_escrow_count >= 0),
    legacy_transaction_set_sha256 TEXT NOT NULL
              CHECK (typeof(legacy_transaction_set_sha256) = 'text'
                     AND length(legacy_transaction_set_sha256) = 64
                     AND legacy_transaction_set_sha256 NOT GLOB '*[^0-9a-f]*'),
    legacy_escrow_set_sha256 TEXT NOT NULL
              CHECK (typeof(legacy_escrow_set_sha256) = 'text'
                     AND length(legacy_escrow_set_sha256) = 64
                     AND legacy_escrow_set_sha256 NOT GLOB '*[^0-9a-f]*'),
    marker_set_sha256 TEXT NOT NULL
              CHECK (typeof(marker_set_sha256) = 'text'
                     AND length(marker_set_sha256) = 64
                     AND marker_set_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (
        (cutover_kind = 'fresh_v2'
         AND source_schema_version IS NULL
         AND source_migrated_from_version IS NULL
         AND source_migration_backup_id IS NULL
         AND source_migration_backup_manifest_sha256 IS NULL
         AND migration_backup_id IS NULL
         AND migration_backup_manifest_sha256 IS NULL
         AND migration_plan_sha256 IS NULL
         AND source_snapshot_sha256 IS NULL
         AND legacy_transaction_count = 0
         AND legacy_escrow_count = 0
         AND legacy_transaction_set_sha256 =
             '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
         AND legacy_escrow_set_sha256 =
             '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
         AND marker_set_sha256 =
             'a81b1a47e8cd319a73362c83a992f181986e799b1e82753bf139e60b35d0a4f6')
        OR
        (cutover_kind = 'v1_to_v2'
         AND typeof(source_schema_version) = 'integer'
         AND source_schema_version = 1
         AND (source_migrated_from_version IS NULL
              OR (typeof(source_migrated_from_version) = 'integer'
                  AND source_migrated_from_version = 0))
         AND ((source_migrated_from_version IS NULL
               AND source_migration_backup_id IS NULL
               AND source_migration_backup_manifest_sha256 IS NULL)
              OR
              (source_migrated_from_version = 0
               AND source_migration_backup_id IS NOT NULL
               AND typeof(source_migration_backup_id) = 'text'
               AND length(CAST(source_migration_backup_id AS BLOB)) > 0
               AND source_migration_backup_manifest_sha256 IS NOT NULL
               AND typeof(source_migration_backup_manifest_sha256) = 'text'
               AND length(source_migration_backup_manifest_sha256) = 64
               AND source_migration_backup_manifest_sha256
                   NOT GLOB '*[^0-9a-f]*'))
         AND migration_backup_id IS NOT NULL
         AND typeof(migration_backup_id) = 'text'
         AND length(CAST(migration_backup_id AS BLOB)) > 0
         AND migration_backup_manifest_sha256 IS NOT NULL
         AND typeof(migration_backup_manifest_sha256) = 'text'
         AND length(migration_backup_manifest_sha256) = 64
         AND migration_backup_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
         AND migration_plan_sha256 IS NOT NULL
         AND typeof(migration_plan_sha256) = 'text'
         AND length(migration_plan_sha256) = 64
         AND migration_plan_sha256 NOT GLOB '*[^0-9a-f]*'
         AND source_snapshot_sha256 IS NOT NULL
         AND typeof(source_snapshot_sha256) = 'text'
         AND length(source_snapshot_sha256) = 64
         AND source_snapshot_sha256 NOT GLOB '*[^0-9a-f]*')
    )
) WITHOUT ROWID;

CREATE TABLE bank_operation_receipts (
    operation_id TEXT PRIMARY KEY NOT NULL
              CHECK (typeof(operation_id) = 'text'
                     AND length(operation_id) = 39
                     AND substr(operation_id, 1, 3) = 'op-'
                     AND substr(operation_id, 12, 1) = '-'
                     AND substr(operation_id, 17, 1) = '-'
                     AND substr(operation_id, 22, 1) = '-'
                     AND substr(operation_id, 27, 1) = '-'
                     AND length(replace(substr(operation_id, 4), '-', '')) = 32
                     AND replace(substr(operation_id, 4), '-', '')
                         NOT GLOB '*[^0-9a-f]*'),
    receipt_version INTEGER NOT NULL
              CHECK (typeof(receipt_version) = 'integer' AND receipt_version = 1),
    operation_kind TEXT NOT NULL
              CHECK (typeof(operation_kind) = 'text'
                     AND operation_kind IN (
                  'account_issuance', 'credit', 'escrow_lock',
                  'escrow_release', 'escrow_split'
              )),
    canonical_request_json TEXT NOT NULL
              CHECK (typeof(canonical_request_json) = 'text'
                     AND json_valid(canonical_request_json) = 1
                     AND json_type(canonical_request_json) = 'object'),
    request_sha256 TEXT NOT NULL
              CHECK (typeof(request_sha256) = 'text'
                     AND length(request_sha256) = 64
                     AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_reference TEXT NOT NULL
              CHECK (typeof(source_reference) = 'text'
                     AND length(CAST(source_reference AS BLOB)) > 0),
    committed_at TEXT NOT NULL
              CHECK (typeof(committed_at) = 'text'
                     AND length(CAST(committed_at AS BLOB)) > 0),
    event_id INTEGER NOT NULL UNIQUE CHECK (typeof(event_id) = 'integer'),
    escrow_id TEXT,
    result_json TEXT NOT NULL
              CHECK (typeof(result_json) = 'text'
                     AND json_valid(result_json) = 1
                     AND json_type(result_json) = 'object'),
    CHECK (
        (operation_kind IN ('account_issuance', 'credit') AND escrow_id IS NULL)
        OR
        (operation_kind IN ('escrow_lock', 'escrow_release', 'escrow_split')
         AND escrow_id IS NOT NULL
         AND typeof(escrow_id) = 'text'
         AND length(CAST(escrow_id AS BLOB)) > 0)
    ),
    FOREIGN KEY (event_id) REFERENCES events (event_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (escrow_id) REFERENCES bank_escrow (escrow_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE UNIQUE INDEX idx_bank_receipt_one_native_lock
    ON bank_operation_receipts (escrow_id)
    WHERE operation_kind = 'escrow_lock';

CREATE UNIQUE INDEX idx_bank_receipt_one_native_settlement
    ON bank_operation_receipts (escrow_id)
    WHERE operation_kind IN ('escrow_release', 'escrow_split');

CREATE TABLE bank_operation_legs (
    operation_id TEXT NOT NULL,
    leg_index INTEGER NOT NULL
              CHECK (typeof(leg_index) = 'integer' AND leg_index BETWEEN 0 AND 1),
    role TEXT NOT NULL
              CHECK (typeof(role) = 'text'
                     AND role IN ('account', 'payer', 'recipient', 'worker', 'poster')),
    account_id TEXT NOT NULL
              CHECK (typeof(account_id) = 'text'
                     AND length(CAST(account_id AS BLOB)) > 0),
    direction TEXT NOT NULL
              CHECK (typeof(direction) = 'text' AND direction IN ('debit', 'credit')),
    amount INTEGER NOT NULL
              CHECK (typeof(amount) = 'integer'
                     AND amount BETWEEN 0 AND 9223372036854775807),
    transaction_id TEXT UNIQUE,
    balance_after INTEGER,
    PRIMARY KEY (operation_id, leg_index),
    UNIQUE (operation_id, role),
    CHECK (
        (amount = 0 AND transaction_id IS NULL AND balance_after IS NULL)
        OR
        (amount > 0
         AND transaction_id IS NOT NULL
         AND typeof(transaction_id) = 'text'
         AND length(CAST(transaction_id AS BLOB)) > 0
         AND balance_after IS NOT NULL
         AND typeof(balance_after) = 'integer'
         AND balance_after BETWEEN 0 AND 9223372036854775807)
    ),
    FOREIGN KEY (operation_id) REFERENCES bank_operation_receipts (operation_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (transaction_id) REFERENCES bank_transactions (tx_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE bank_legacy_transaction_observations (
    tx_id TEXT PRIMARY KEY NOT NULL
              CHECK (typeof(tx_id) = 'text' AND length(CAST(tx_id AS BLOB)) > 0),
    cutover_singleton INTEGER NOT NULL
              CHECK (typeof(cutover_singleton) = 'integer' AND cutover_singleton = 1),
    observed_kind TEXT NOT NULL
              CHECK (typeof(observed_kind) = 'text'
                     AND observed_kind IN (
                  'observed_credit', 'observed_escrow_lock', 'observed_escrow_release'
              )),
    observed_fact_sha256 TEXT NOT NULL
              CHECK (typeof(observed_fact_sha256) = 'text'
                     AND length(observed_fact_sha256) = 64
                     AND observed_fact_sha256 NOT GLOB '*[^0-9a-f]*'),
    FOREIGN KEY (tx_id) REFERENCES bank_transactions (tx_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (cutover_singleton) REFERENCES bank_receipt_cutover (singleton)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE bank_legacy_escrow_observations (
    escrow_id TEXT PRIMARY KEY NOT NULL
              CHECK (typeof(escrow_id) = 'text'
                     AND length(CAST(escrow_id AS BLOB)) > 0),
    cutover_singleton INTEGER NOT NULL
              CHECK (typeof(cutover_singleton) = 'integer' AND cutover_singleton = 1),
    observed_status TEXT NOT NULL
              CHECK (typeof(observed_status) = 'text'
                     AND observed_status IN ('locked', 'released', 'split')),
    lock_fact_sha256 TEXT NOT NULL
              CHECK (typeof(lock_fact_sha256) = 'text'
                     AND length(lock_fact_sha256) = 64
                     AND lock_fact_sha256 NOT GLOB '*[^0-9a-f]*'),
    resolution_fact_sha256 TEXT,
    CHECK (
        (observed_status = 'locked' AND resolution_fact_sha256 IS NULL)
        OR
        (observed_status IN ('released', 'split')
         AND resolution_fact_sha256 IS NOT NULL
         AND typeof(resolution_fact_sha256) = 'text'
         AND length(resolution_fact_sha256) = 64
         AND resolution_fact_sha256 NOT GLOB '*[^0-9a-f]*')
    ),
    FOREIGN KEY (escrow_id) REFERENCES bank_escrow (escrow_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (cutover_singleton) REFERENCES bank_receipt_cutover (singleton)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

-- Fresh v2 has no historical money effects and no invented migration provenance.
INSERT INTO bank_receipt_cutover (
    singleton, attestation_version, cutover_kind,
    source_schema_version, source_migrated_from_version,
    source_migration_backup_id, source_migration_backup_manifest_sha256,
    migration_backup_id, migration_backup_manifest_sha256,
    migration_plan_sha256, source_snapshot_sha256,
    legacy_transaction_count, legacy_escrow_count,
    legacy_transaction_set_sha256, legacy_escrow_set_sha256, marker_set_sha256
) VALUES (
    1, 1, 'fresh_v2', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, 0,
    '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    'a81b1a47e8cd319a73362c83a992f181986e799b1e82753bf139e60b35d0a4f6'
);

-- MP-07 schema-v3 task-transition sidecars.
-- Applied only to an admitted v2 schema by the explicit v2-to-v3 migration.

CREATE TABLE board_task_contracts (
    task_id TEXT PRIMARY KEY NOT NULL
            CHECK (typeof(task_id) = 'text' AND length(CAST(task_id AS BLOB)) > 0),
    lifecycle_version INTEGER NOT NULL
            CHECK (typeof(lifecycle_version) = 'integer'
                   AND lifecycle_version BETWEEN 0 AND 9223372036854775807),
    timing_contract_version INTEGER NOT NULL
            CHECK (typeof(timing_contract_version) = 'integer'
                   AND timing_contract_version IN (1, 2)),
    acceptance_window_seconds INTEGER,
    bidding_cutoff_us INTEGER NOT NULL
            CHECK (typeof(bidding_cutoff_us) = 'integer'
                   AND bidding_cutoff_us BETWEEN 0 AND 9223372036854775807),
    acceptance_cutoff_us INTEGER NOT NULL
            CHECK (typeof(acceptance_cutoff_us) = 'integer'
                   AND acceptance_cutoff_us BETWEEN 0 AND 9223372036854775807),
    execution_cutoff_us INTEGER
            CHECK (execution_cutoff_us IS NULL
                   OR (typeof(execution_cutoff_us) = 'integer'
                       AND execution_cutoff_us BETWEEN 0 AND 9223372036854775807)),
    review_cutoff_us INTEGER
            CHECK (review_cutoff_us IS NULL
                   OR (typeof(review_cutoff_us) = 'integer'
                       AND review_cutoff_us BETWEEN 0 AND 9223372036854775807)),
    accepted_reward INTEGER
            CHECK (accepted_reward IS NULL
                   OR (typeof(accepted_reward) = 'integer'
                       AND accepted_reward BETWEEN 1 AND 9223372036854775807)),
    accepted_bid_amount INTEGER
            CHECK (accepted_bid_amount IS NULL
                   OR (typeof(accepted_bid_amount) = 'integer'
                       AND accepted_bid_amount BETWEEN 0 AND 9223372036854775807)),
    CHECK (acceptance_cutoff_us >= bidding_cutoff_us),
    CHECK (
        (timing_contract_version = 1
         AND acceptance_window_seconds IS NULL
         AND acceptance_cutoff_us = bidding_cutoff_us)
        OR
        (timing_contract_version = 2
         AND typeof(acceptance_window_seconds) = 'integer'
         AND acceptance_window_seconds BETWEEN 1 AND 9223372036854
         AND bidding_cutoff_us <= 9223372036854775807 - acceptance_window_seconds * 1000000
         AND acceptance_cutoff_us = bidding_cutoff_us + acceptance_window_seconds * 1000000)
    ),
    CHECK (
        (accepted_reward IS NULL AND accepted_bid_amount IS NULL)
        OR
        (accepted_reward IS NOT NULL AND accepted_bid_amount IS NOT NULL)
    ),
    FOREIGN KEY (task_id) REFERENCES board_tasks (task_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE board_task_operations (
    operation_id TEXT PRIMARY KEY NOT NULL
            CHECK (typeof(operation_id) = 'text'
                   AND length(operation_id) = 39
                   AND substr(operation_id, 1, 3) = 'op-'
                   AND substr(operation_id, 12, 1) = '-'
                   AND substr(operation_id, 17, 1) = '-'
                   AND substr(operation_id, 22, 1) = '-'
                   AND substr(operation_id, 27, 1) = '-'
                   AND length(replace(substr(operation_id, 4), '-', '')) = 32
                   AND replace(substr(operation_id, 4), '-', '') NOT GLOB '*[^0-9a-f]*'),
    operation_contract_version INTEGER NOT NULL
            CHECK (typeof(operation_contract_version) = 'integer'
                   AND operation_contract_version = 1),
    task_id TEXT NOT NULL
            CHECK (typeof(task_id) = 'text' AND length(CAST(task_id AS BLOB)) > 0),
    command TEXT NOT NULL
            CHECK (typeof(command) = 'text' AND command IN (
                'submit_bid', 'accept_bid', 'cancel_task', 'expire_bidding',
                'expire_acceptance', 'submit_deliverable', 'expire_execution',
                'approve_task', 'auto_approve', 'dispute_task', 'record_ruling'
            )),
    canonical_request_json TEXT NOT NULL
            CHECK (typeof(canonical_request_json) = 'text'
                   AND json_valid(canonical_request_json) = 1
                   AND json_type(canonical_request_json) = 'object'),
    request_sha256 TEXT NOT NULL
            CHECK (typeof(request_sha256) = 'text'
                   AND length(request_sha256) = 64
                   AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    caller_kid TEXT NOT NULL
            CHECK (typeof(caller_kid) = 'text' AND length(CAST(caller_kid AS BLOB)) > 0),
    actor_kind TEXT NOT NULL
            CHECK (typeof(actor_kind) = 'text'
                   AND actor_kind IN ('agent', 'platform', 'system')),
    actor_id TEXT NOT NULL
            CHECK (typeof(actor_id) = 'text' AND length(CAST(actor_id AS BLOB)) > 0),
    source_status TEXT NOT NULL
            CHECK (typeof(source_status) = 'text' AND length(CAST(source_status AS BLOB)) > 0),
    source_lifecycle_version INTEGER NOT NULL
            CHECK (typeof(source_lifecycle_version) = 'integer'
                   AND source_lifecycle_version BETWEEN 0 AND 9223372036854775807),
    target_status TEXT NOT NULL
            CHECK (typeof(target_status) = 'text' AND length(CAST(target_status AS BLOB)) > 0),
    target_lifecycle_version INTEGER NOT NULL
            CHECK (typeof(target_lifecycle_version) = 'integer'
                   AND target_lifecycle_version BETWEEN 0 AND 9223372036854775807),
    operation_state TEXT NOT NULL
            CHECK (typeof(operation_state) = 'text'
                   AND operation_state IN ('claimed', 'blocked', 'completed')),
    effect_kind TEXT NOT NULL
            CHECK (typeof(effect_kind) = 'text'
                   AND effect_kind IN ('none', 'bank_release', 'bank_split', 'court_claim')),
    effect_state TEXT NOT NULL
            CHECK (typeof(effect_state) = 'text'
                   AND effect_state IN ('none', 'pending', 'succeeded', 'conflict')),
    effect_operation_id TEXT,
    effect_result_reference TEXT,
    effect_intent_json TEXT
            CHECK (effect_intent_json IS NULL
                   OR (typeof(effect_intent_json) = 'text'
                       AND json_valid(effect_intent_json) = 1
                       AND json_type(effect_intent_json) = 'object')),
    claimed_at_us INTEGER NOT NULL
            CHECK (typeof(claimed_at_us) = 'integer'
                   AND claimed_at_us BETWEEN 0 AND 9223372036854775807),
    completed_at_us INTEGER
            CHECK (completed_at_us IS NULL
                   OR (typeof(completed_at_us) = 'integer'
                       AND completed_at_us BETWEEN claimed_at_us AND 9223372036854775807)),
    event_id INTEGER,
    result_json TEXT
            CHECK (result_json IS NULL
                   OR (typeof(result_json) = 'text'
                       AND json_valid(result_json) = 1
                       AND json_type(result_json) = 'object')),
    error_json TEXT
            CHECK (error_json IS NULL
                   OR (typeof(error_json) = 'text'
                       AND json_valid(error_json) = 1
                       AND json_type(error_json) = 'object')),
    CHECK (
        (command = 'submit_bid'
         AND target_lifecycle_version = source_lifecycle_version)
        OR
        (command != 'submit_bid'
         AND target_lifecycle_version = source_lifecycle_version + 1)
    ),
    CHECK (effect_operation_id IS NULL
           OR (typeof(effect_operation_id) = 'text'
               AND length(CAST(effect_operation_id AS BLOB)) > 0)),
    CHECK (effect_result_reference IS NULL
           OR (typeof(effect_result_reference) = 'text'
               AND length(CAST(effect_result_reference AS BLOB)) > 0)),
    CHECK (
        (effect_kind = 'none'
         AND effect_state = 'none'
         AND effect_operation_id IS NULL
         AND effect_result_reference IS NULL
         AND effect_intent_json IS NULL)
        OR
        (effect_kind IN ('bank_release', 'bank_split')
         AND effect_state IN ('pending', 'succeeded', 'conflict')
         AND effect_operation_id = operation_id
         AND effect_intent_json IS NOT NULL)
        OR
        (effect_kind = 'court_claim'
         AND effect_state IN ('pending', 'succeeded', 'conflict')
         AND effect_operation_id IS NULL
         AND effect_intent_json IS NOT NULL)
    ),
    CHECK (
        (operation_state = 'claimed'
         AND effect_kind != 'none'
         AND effect_state = 'pending'
         AND completed_at_us IS NULL
         AND event_id IS NULL
         AND result_json IS NULL
         AND error_json IS NULL)
        OR
        (operation_state = 'blocked'
         AND effect_kind != 'none'
         AND effect_state = 'conflict'
         AND completed_at_us IS NULL
         AND event_id IS NULL
         AND result_json IS NULL
         AND error_json IS NOT NULL)
        OR
        (operation_state = 'completed'
         AND effect_state IN ('none', 'succeeded')
         AND completed_at_us IS NOT NULL
         AND event_id IS NOT NULL
         AND result_json IS NOT NULL
         AND error_json IS NULL)
    ),
    CHECK ((operation_state = 'completed' AND effect_kind = 'none')
           OR effect_result_reference IS NOT NULL
           OR operation_state = 'claimed'
           OR operation_state = 'blocked'),
    FOREIGN KEY (task_id) REFERENCES board_tasks (task_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (event_id) REFERENCES events (event_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE UNIQUE INDEX idx_board_task_operations_active
    ON board_task_operations (task_id)
    WHERE operation_state IN ('claimed', 'blocked');

CREATE INDEX idx_board_task_operations_task
    ON board_task_operations (task_id, operation_id);

CREATE INDEX idx_board_task_operations_state_effect
    ON board_task_operations (operation_state, effect_state, operation_id);

CREATE TABLE board_legacy_effect_observations (
    observation_id TEXT PRIMARY KEY NOT NULL
            CHECK (typeof(observation_id) = 'text'
                   AND length(observation_id) = 40
                   AND substr(observation_id, 1, 4) = 'obs-'
                   AND substr(observation_id, 13, 1) = '-'
                   AND substr(observation_id, 18, 1) = '-'
                   AND substr(observation_id, 23, 1) = '-'
                   AND substr(observation_id, 28, 1) = '-'
                   AND length(replace(substr(observation_id, 5), '-', '')) = 32
                   AND replace(substr(observation_id, 5), '-', '') NOT GLOB '*[^0-9a-f]*'),
    task_id TEXT NOT NULL
            CHECK (typeof(task_id) = 'text' AND length(CAST(task_id AS BLOB)) > 0),
    observation_kind TEXT NOT NULL
            CHECK (typeof(observation_kind) = 'text' AND observation_kind IN (
                'worker_settlement_succeeded',
                'worker_settlement_pending',
                'court_claim_succeeded'
            )),
    observed_fact_json TEXT NOT NULL
            CHECK (typeof(observed_fact_json) = 'text'
                   AND json_valid(observed_fact_json) = 1
                   AND json_type(observed_fact_json) = 'object'),
    observed_fact_sha256 TEXT NOT NULL
            CHECK (typeof(observed_fact_sha256) = 'text'
                   AND length(observed_fact_sha256) = 64
                   AND observed_fact_sha256 NOT GLOB '*[^0-9a-f]*'),
    external_reference TEXT NOT NULL
            CHECK (typeof(external_reference) = 'text'
                   AND length(CAST(external_reference AS BLOB)) > 0),
    effect_state TEXT NOT NULL
            CHECK (typeof(effect_state) = 'text' AND effect_state IN ('pending', 'succeeded')),
    target_outcome_hint TEXT NOT NULL
            CHECK (typeof(target_outcome_hint) = 'text'
                   AND target_outcome_hint IN ('approved', 'disputed')),
    unresolved INTEGER NOT NULL
            CHECK (typeof(unresolved) = 'integer' AND unresolved IN (0, 1)),
    CHECK (
        (observation_kind = 'worker_settlement_succeeded'
         AND effect_state = 'succeeded' AND target_outcome_hint = 'approved')
        OR
        (observation_kind = 'worker_settlement_pending'
         AND effect_state = 'pending' AND target_outcome_hint = 'approved')
        OR
        (observation_kind = 'court_claim_succeeded'
         AND effect_state = 'succeeded' AND target_outcome_hint = 'disputed')
    ),
    FOREIGN KEY (task_id) REFERENCES board_tasks (task_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE INDEX idx_board_legacy_effect_task
    ON board_legacy_effect_observations (task_id, unresolved, observation_id);
