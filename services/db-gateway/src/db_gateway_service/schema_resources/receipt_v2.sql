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
