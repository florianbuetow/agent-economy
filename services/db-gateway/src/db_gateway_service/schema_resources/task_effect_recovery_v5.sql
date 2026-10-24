-- MP-08 schema-v5 durable financial-effect recovery sidecars.
-- Applied only to an admitted v4 schema by the explicit v4-to-v5 migration.

CREATE TABLE board_task_effect_delivery_cutover (
    singleton INTEGER PRIMARY KEY NOT NULL
        CHECK (typeof(singleton) = 'integer' AND singleton = 1),
    cutover_version INTEGER NOT NULL
        CHECK (typeof(cutover_version) = 'integer' AND cutover_version = 1),
    cutover_kind TEXT NOT NULL
        CHECK (typeof(cutover_kind) = 'text'
               AND cutover_kind IN ('fresh', 'migrated')),
    source_schema_version INTEGER,
    source_snapshot_sha256 TEXT,
    migration_plan_sha256 TEXT,
    backup_id TEXT,
    backup_manifest_sha256 TEXT,
    caller_kid TEXT,
    native_member_count INTEGER NOT NULL
        CHECK (typeof(native_member_count) = 'integer'
               AND native_member_count BETWEEN 0 AND 9223372036854775807),
    native_members_sha256 TEXT NOT NULL
        CHECK (typeof(native_members_sha256) = 'text'
               AND length(native_members_sha256) = 64
               AND native_members_sha256 NOT GLOB '*[^0-9a-f]*'),
    legacy_member_count INTEGER NOT NULL
        CHECK (typeof(legacy_member_count) = 'integer'
               AND legacy_member_count BETWEEN 0 AND 9223372036854775807),
    legacy_members_sha256 TEXT NOT NULL
        CHECK (typeof(legacy_members_sha256) = 'text'
               AND length(legacy_members_sha256) = 64
               AND legacy_members_sha256 NOT GLOB '*[^0-9a-f]*'),
    combined_member_count INTEGER NOT NULL
        CHECK (typeof(combined_member_count) = 'integer'
               AND combined_member_count BETWEEN 0 AND 9223372036854775807),
    combined_members_sha256 TEXT NOT NULL
        CHECK (typeof(combined_members_sha256) = 'text'
               AND length(combined_members_sha256) = 64
               AND combined_members_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (combined_member_count = native_member_count + legacy_member_count),
    CHECK (
        (cutover_kind = 'fresh'
         AND source_schema_version IS NULL
         AND source_snapshot_sha256 IS NULL
         AND migration_plan_sha256 IS NULL
         AND backup_id IS NULL
         AND backup_manifest_sha256 IS NULL
         AND caller_kid IS NULL
         AND native_member_count = 0
         AND legacy_member_count = 0
         AND combined_member_count = 0
         AND native_members_sha256 =
             '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
         AND legacy_members_sha256 =
             '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
         AND combined_members_sha256 =
             '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945')
        OR
        (cutover_kind = 'migrated'
         AND typeof(source_schema_version) = 'integer'
         AND source_schema_version = 4
         AND typeof(source_snapshot_sha256) = 'text'
         AND length(source_snapshot_sha256) = 64
         AND source_snapshot_sha256 NOT GLOB '*[^0-9a-f]*'
         AND typeof(migration_plan_sha256) = 'text'
         AND length(migration_plan_sha256) = 64
         AND migration_plan_sha256 NOT GLOB '*[^0-9a-f]*'
         AND typeof(backup_id) = 'text'
         AND length(CAST(backup_id AS BLOB)) > 0
         AND typeof(backup_manifest_sha256) = 'text'
         AND length(backup_manifest_sha256) = 64
         AND backup_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
         AND typeof(caller_kid) = 'text'
         AND length(CAST(caller_kid AS BLOB)) > 0)
    )
) WITHOUT ROWID;

CREATE TABLE board_task_effect_deliveries (
    effect_operation_id TEXT PRIMARY KEY NOT NULL
        CHECK (typeof(effect_operation_id) = 'text'
               AND length(effect_operation_id) = 39
               AND substr(effect_operation_id, 1, 3) = 'op-'
               AND substr(effect_operation_id, 12, 1) = '-'
               AND substr(effect_operation_id, 17, 1) = '-'
               AND substr(effect_operation_id, 22, 1) = '-'
               AND substr(effect_operation_id, 27, 1) = '-'
               AND length(replace(substr(effect_operation_id, 4), '-', '')) = 32
               AND replace(substr(effect_operation_id, 4), '-', '')
                   NOT GLOB '*[^0-9a-f]*'),
    recovery_contract_version INTEGER NOT NULL
        CHECK (typeof(recovery_contract_version) = 'integer'
               AND recovery_contract_version = 1),
    task_id TEXT NOT NULL
        CHECK (typeof(task_id) = 'text'
               AND length(CAST(task_id AS BLOB)) > 0),
    source_kind TEXT NOT NULL
        CHECK (typeof(source_kind) = 'text'
               AND source_kind IN
                   ('native_task_operation', 'legacy_settlement_observation')),
    native_operation_id TEXT UNIQUE
        CHECK (native_operation_id IS NULL
               OR (typeof(native_operation_id) = 'text'
                   AND length(native_operation_id) = 39
                   AND substr(native_operation_id, 1, 3) = 'op-'
                   AND substr(native_operation_id, 12, 1) = '-'
                   AND substr(native_operation_id, 17, 1) = '-'
                   AND substr(native_operation_id, 22, 1) = '-'
                   AND substr(native_operation_id, 27, 1) = '-'
                   AND length(replace(substr(native_operation_id, 4), '-', '')) = 32
                   AND replace(substr(native_operation_id, 4), '-', '')
                       NOT GLOB '*[^0-9a-f]*')),
    legacy_observation_id TEXT UNIQUE
        CHECK (legacy_observation_id IS NULL
               OR (typeof(legacy_observation_id) = 'text'
                   AND length(legacy_observation_id) = 40
                   AND substr(legacy_observation_id, 1, 4) = 'obs-'
                   AND substr(legacy_observation_id, 13, 1) = '-'
                   AND substr(legacy_observation_id, 18, 1) = '-'
                   AND substr(legacy_observation_id, 23, 1) = '-'
                   AND substr(legacy_observation_id, 28, 1) = '-'
                   AND length(replace(substr(legacy_observation_id, 5), '-', '')) = 32
                   AND replace(substr(legacy_observation_id, 5), '-', '')
                       NOT GLOB '*[^0-9a-f]*')),
    owner_caller_kid TEXT NOT NULL
        CHECK (typeof(owner_caller_kid) = 'text'
               AND length(CAST(owner_caller_kid AS BLOB)) > 0),
    effect_kind TEXT NOT NULL
        CHECK (typeof(effect_kind) = 'text'
               AND effect_kind IN ('bank_release', 'bank_split')),
    delivery_state TEXT NOT NULL
        CHECK (typeof(delivery_state) = 'text'
               AND delivery_state IN
                   ('pending', 'leased', 'dead_letter', 'completed',
                    'integrity_blocked')),
    enqueued_at_us INTEGER NOT NULL
        CHECK (typeof(enqueued_at_us) = 'integer'
               AND enqueued_at_us BETWEEN 0 AND 253402300799999999),
    total_attempt_count INTEGER NOT NULL
        CHECK (typeof(total_attempt_count) = 'integer'
               AND total_attempt_count BETWEEN 0 AND 9223372036854775807),
    cycle_attempt_count INTEGER NOT NULL
        CHECK (typeof(cycle_attempt_count) = 'integer'
               AND cycle_attempt_count BETWEEN 0 AND total_attempt_count),
    lease_generation INTEGER NOT NULL
        CHECK (typeof(lease_generation) = 'integer'
               AND lease_generation BETWEEN 0 AND 9223372036854775807),
    next_attempt_at_us INTEGER
        CHECK (next_attempt_at_us IS NULL
               OR (typeof(next_attempt_at_us) = 'integer'
                   AND next_attempt_at_us BETWEEN 0 AND 253402300799999999)),
    lease_owner_id TEXT
        CHECK (lease_owner_id IS NULL
               OR (typeof(lease_owner_id) = 'text'
                   AND length(lease_owner_id) = 45
                   AND substr(lease_owner_id, 1, 9) = 'recovery-')),
    lease_acquired_at_us INTEGER
        CHECK (lease_acquired_at_us IS NULL
               OR (typeof(lease_acquired_at_us) = 'integer'
                   AND lease_acquired_at_us
                       BETWEEN enqueued_at_us AND 253402300799999999)),
    lease_expires_at_us INTEGER
        CHECK (lease_expires_at_us IS NULL
               OR (typeof(lease_expires_at_us) = 'integer'
                   AND lease_expires_at_us
                       BETWEEN enqueued_at_us AND 253402300799999999)),
    last_failure_code TEXT
        CHECK (last_failure_code IS NULL
               OR (typeof(last_failure_code) = 'text'
                   AND last_failure_code IN
                       ('central_bank_unavailable',
                        'gateway_unavailable',
                        'invalid_gateway_response',
                        'request_timeout',
                        'unexpected_recovery_error',
                        'lease_expired',
                        'attempt_counter_exhausted',
                        'lease_generation_exhausted',
                        'effect_evidence_conflict'))),
    last_failure_at_us INTEGER
        CHECK (last_failure_at_us IS NULL
               OR (typeof(last_failure_at_us) = 'integer'
                   AND last_failure_at_us
                       BETWEEN enqueued_at_us AND 253402300799999999)),
    operator_retry_count INTEGER NOT NULL
        CHECK (typeof(operator_retry_count) = 'integer'
               AND operator_retry_count BETWEEN 0 AND 9223372036854775807),
    last_operator_retry_at_us INTEGER
        CHECK (last_operator_retry_at_us IS NULL
               OR (typeof(last_operator_retry_at_us) = 'integer'
                   AND last_operator_retry_at_us
                       BETWEEN enqueued_at_us AND 253402300799999999)),
    last_operator_retry_caller_kid TEXT
        CHECK (last_operator_retry_caller_kid IS NULL
               OR (typeof(last_operator_retry_caller_kid) = 'text'
                   AND length(CAST(last_operator_retry_caller_kid AS BLOB)) > 0)),
    next_evidence_check_at_us INTEGER
        CHECK (next_evidence_check_at_us IS NULL
               OR (typeof(next_evidence_check_at_us) = 'integer'
                   AND next_evidence_check_at_us
                       BETWEEN enqueued_at_us AND 253402300799999999)),
    completed_at_us INTEGER
        CHECK (completed_at_us IS NULL
               OR (typeof(completed_at_us) = 'integer'
                   AND completed_at_us BETWEEN enqueued_at_us
                                           AND 253402300799999999)),
    completion_event_id INTEGER
        CHECK (completion_event_id IS NULL
               OR (typeof(completion_event_id) = 'integer'
                   AND completion_event_id BETWEEN 1 AND 9223372036854775807)),
    cutover_singleton INTEGER
        CHECK (cutover_singleton IS NULL
               OR (typeof(cutover_singleton) = 'integer'
                   AND cutover_singleton = 1)),
    CHECK (
        (source_kind = 'native_task_operation'
         AND native_operation_id IS NOT NULL
         AND native_operation_id = effect_operation_id
         AND legacy_observation_id IS NULL)
        OR
        (source_kind = 'legacy_settlement_observation'
         AND native_operation_id IS NULL
         AND legacy_observation_id IS NOT NULL
         AND effect_kind = 'bank_release'
         AND cutover_singleton IS NOT NULL
         AND cutover_singleton = 1)
    ),
    CHECK ((last_failure_code IS NULL AND last_failure_at_us IS NULL)
           OR (last_failure_code IS NOT NULL AND last_failure_at_us IS NOT NULL)),
    CHECK (
        (operator_retry_count = 0
         AND last_operator_retry_at_us IS NULL
         AND last_operator_retry_caller_kid IS NULL)
        OR
        (operator_retry_count > 0
         AND last_operator_retry_at_us IS NOT NULL
         AND last_operator_retry_caller_kid IS NOT NULL)
    ),
    CHECK (
        (delivery_state = 'pending'
         AND next_attempt_at_us IS NOT NULL
         AND lease_owner_id IS NULL
         AND lease_acquired_at_us IS NULL
         AND lease_expires_at_us IS NULL
         AND next_evidence_check_at_us IS NULL
         AND completed_at_us IS NULL
         AND completion_event_id IS NULL)
        OR
        (delivery_state = 'leased'
         AND next_attempt_at_us IS NULL
         AND lease_owner_id IS NOT NULL
         AND lease_acquired_at_us IS NOT NULL
         AND lease_expires_at_us IS NOT NULL
         AND lease_expires_at_us > lease_acquired_at_us
         AND next_evidence_check_at_us IS NULL
         AND completed_at_us IS NULL
         AND completion_event_id IS NULL)
        OR
        (delivery_state = 'dead_letter'
         AND next_attempt_at_us IS NULL
         AND lease_owner_id IS NULL
         AND lease_acquired_at_us IS NULL
         AND lease_expires_at_us IS NULL
         AND next_evidence_check_at_us IS NOT NULL
         AND last_failure_code IS NOT NULL
         AND last_failure_at_us IS NOT NULL
         AND completed_at_us IS NULL
         AND completion_event_id IS NULL)
        OR
        (delivery_state = 'integrity_blocked'
         AND next_attempt_at_us IS NULL
         AND lease_owner_id IS NULL
         AND lease_acquired_at_us IS NULL
         AND lease_expires_at_us IS NULL
         AND next_evidence_check_at_us IS NULL
         AND last_failure_code = 'effect_evidence_conflict'
         AND last_failure_at_us IS NOT NULL
         AND completed_at_us IS NULL
         AND completion_event_id IS NULL)
        OR
        (delivery_state = 'completed'
         AND next_attempt_at_us IS NULL
         AND lease_owner_id IS NULL
         AND lease_acquired_at_us IS NULL
         AND lease_expires_at_us IS NULL
         AND next_evidence_check_at_us IS NULL
         AND completed_at_us IS NOT NULL
         AND completion_event_id IS NOT NULL)
    ),
    UNIQUE (task_id),
    FOREIGN KEY (task_id) REFERENCES board_tasks (task_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (native_operation_id)
        REFERENCES board_task_operations (operation_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (legacy_observation_id)
        REFERENCES board_legacy_effect_observations (observation_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (completion_event_id) REFERENCES events (event_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (cutover_singleton)
        REFERENCES board_task_effect_delivery_cutover (singleton)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE INDEX idx_board_effect_delivery_pending
    ON board_task_effect_deliveries (next_attempt_at_us, effect_operation_id)
    WHERE delivery_state = 'pending';

CREATE INDEX idx_board_effect_delivery_leased
    ON board_task_effect_deliveries (lease_expires_at_us, effect_operation_id)
    WHERE delivery_state = 'leased';

CREATE INDEX idx_board_effect_delivery_dead_letter_evidence
    ON board_task_effect_deliveries
       (next_evidence_check_at_us, effect_operation_id)
    WHERE delivery_state = 'dead_letter';

CREATE INDEX idx_board_effect_delivery_diagnostic
    ON board_task_effect_deliveries (delivery_state, effect_operation_id);

-- Fresh v5 has no migrated financial effects and no invented migration provenance.
INSERT INTO board_task_effect_delivery_cutover (
    singleton,
    cutover_version,
    cutover_kind,
    source_schema_version,
    source_snapshot_sha256,
    migration_plan_sha256,
    backup_id,
    backup_manifest_sha256,
    caller_kid,
    native_member_count,
    native_members_sha256,
    legacy_member_count,
    legacy_members_sha256,
    combined_member_count,
    combined_members_sha256
) VALUES (
    1,
    1,
    'fresh',
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    0,
    '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    0,
    '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    0,
    '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
);
