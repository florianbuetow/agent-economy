-- Additive MP-09 V6 publication journal. Runtime schema admission remains V5.

CREATE TABLE board_task_publications (
    operation_id TEXT PRIMARY KEY NOT NULL
        CHECK (typeof(operation_id)='text' AND length(operation_id)=39
               AND substr(operation_id,1,3)='op-' AND substr(operation_id,12,1)='-'
               AND substr(operation_id,17,1)='-' AND substr(operation_id,22,1)='-'
               AND substr(operation_id,27,1)='-'
               AND length(replace(substr(operation_id,4),'-',''))=32
               AND replace(substr(operation_id,4),'-','') NOT GLOB '*[^0-9a-f]*'),
    publication_contract_version INTEGER NOT NULL
        CHECK (typeof(publication_contract_version)='integer' AND publication_contract_version=1),
    task_id TEXT NOT NULL UNIQUE
        CHECK (typeof(task_id)='text' AND length(task_id)=38
               AND substr(task_id,1,2)='t-' AND substr(task_id,11,1)='-'
               AND substr(task_id,16,1)='-' AND substr(task_id,21,1)='-'
               AND substr(task_id,26,1)='-'
               AND length(replace(substr(task_id,3),'-',''))=32
               AND lower(replace(substr(task_id,3),'-','')) NOT GLOB '*[^0-9a-f]*'),
    canonical_request_json TEXT NOT NULL
        CHECK (typeof(canonical_request_json)='text' AND json_valid(canonical_request_json)=1
               AND json_type(canonical_request_json)='object'),
    request_sha256 TEXT NOT NULL
        CHECK (typeof(request_sha256)='text' AND length(request_sha256)=64
               AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    caller_kid TEXT NOT NULL CHECK (typeof(caller_kid)='text' AND length(CAST(caller_kid AS BLOB))>0),
    actor_id TEXT NOT NULL CHECK (typeof(actor_id)='text' AND length(CAST(actor_id AS BLOB))>0),
    lock_operation_id TEXT NOT NULL UNIQUE
        CHECK (typeof(lock_operation_id)='text' AND length(lock_operation_id)=39
               AND substr(lock_operation_id,1,3)='op-' AND substr(lock_operation_id,12,1)='-'
               AND substr(lock_operation_id,17,1)='-' AND substr(lock_operation_id,22,1)='-'
               AND substr(lock_operation_id,27,1)='-'
               AND length(replace(substr(lock_operation_id,4),'-',''))=32
               AND replace(substr(lock_operation_id,4),'-','') NOT GLOB '*[^0-9a-f]*'),
    state TEXT NOT NULL CHECK (state IN (
        'awaiting_inputs','awaiting_publish','awaiting_lock','published','blocked_integrity'
    )),
    admitted_at_us INTEGER NOT NULL CHECK (
        typeof(admitted_at_us)='integer' AND admitted_at_us BETWEEN 0 AND 9223372036854775807
    ),
    escrow_id TEXT,
    lock_request_sha256 TEXT,
    lock_receipt_event_id INTEGER,
    published_at_us INTEGER,
    creation_event_id INTEGER UNIQUE,
    result_json TEXT CHECK (result_json IS NULL OR (
        typeof(result_json)='text' AND json_valid(result_json)=1 AND json_type(result_json)='object'
    )),
    error_json TEXT CHECK (error_json IS NULL OR (
        typeof(error_json)='text' AND json_valid(error_json)=1 AND json_type(error_json)='object'
    )),
    request_protocol_version INTEGER NOT NULL
        CHECK (typeof(request_protocol_version)='integer' AND request_protocol_version IN (1,2)),
    expected_input_count INTEGER CHECK (expected_input_count IS NULL OR (
        typeof(expected_input_count)='integer' AND expected_input_count BETWEEN 0 AND 9223372036854775807
    )),
    base_request_sha256 TEXT CHECK (base_request_sha256 IS NULL OR (
        typeof(base_request_sha256)='text' AND length(base_request_sha256)=64
        AND base_request_sha256 NOT GLOB '*[^0-9a-f]*'
    )),
    freeze_operation_id TEXT UNIQUE,
    canonical_freeze_request_json TEXT CHECK (canonical_freeze_request_json IS NULL OR (
        typeof(canonical_freeze_request_json)='text' AND json_valid(canonical_freeze_request_json)=1
        AND json_type(canonical_freeze_request_json)='object'
    )),
    freeze_request_sha256 TEXT,
    input_manifest_sha256 TEXT,
    publication_contract_sha256 TEXT,
    inputs_frozen_at_us INTEGER,
    publish_operation_id TEXT UNIQUE,
    canonical_publish_request_json TEXT CHECK (canonical_publish_request_json IS NULL OR (
        typeof(canonical_publish_request_json)='text' AND json_valid(canonical_publish_request_json)=1
        AND json_type(canonical_publish_request_json)='object'
    )),
    publish_request_sha256 TEXT,
    publish_authorized_at_us INTEGER,
    UNIQUE (operation_id,task_id),
    UNIQUE (operation_id,task_id,actor_id),
    CHECK (operation_id!=lock_operation_id),
    CHECK (freeze_operation_id IS NULL OR (
        typeof(freeze_operation_id)='text' AND length(freeze_operation_id)=39
        AND substr(freeze_operation_id,1,3)='op-'
        AND substr(freeze_operation_id,12,1)='-'
        AND substr(freeze_operation_id,17,1)='-'
        AND substr(freeze_operation_id,22,1)='-'
        AND substr(freeze_operation_id,27,1)='-'
        AND length(replace(substr(freeze_operation_id,4),'-',''))=32
        AND replace(substr(freeze_operation_id,4),'-','') NOT GLOB '*[^0-9a-f]*'
        AND freeze_operation_id NOT IN (operation_id,lock_operation_id)
    )),
    CHECK (publish_operation_id IS NULL OR (
        typeof(publish_operation_id)='text' AND length(publish_operation_id)=39
        AND substr(publish_operation_id,1,3)='op-'
        AND substr(publish_operation_id,12,1)='-'
        AND substr(publish_operation_id,17,1)='-'
        AND substr(publish_operation_id,22,1)='-'
        AND substr(publish_operation_id,27,1)='-'
        AND length(replace(substr(publish_operation_id,4),'-',''))=32
        AND replace(substr(publish_operation_id,4),'-','') NOT GLOB '*[^0-9a-f]*'
        AND publish_operation_id NOT IN (operation_id,lock_operation_id)
        AND (freeze_operation_id IS NULL OR publish_operation_id!=freeze_operation_id)
    )),
    CHECK (freeze_request_sha256 IS NULL OR (
        typeof(freeze_request_sha256)='text' AND length(freeze_request_sha256)=64
        AND freeze_request_sha256 NOT GLOB '*[^0-9a-f]*'
    )),
    CHECK (input_manifest_sha256 IS NULL OR (
        typeof(input_manifest_sha256)='text' AND length(input_manifest_sha256)=64
        AND input_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    )),
    CHECK (publication_contract_sha256 IS NULL OR (
        typeof(publication_contract_sha256)='text' AND length(publication_contract_sha256)=64
        AND publication_contract_sha256 NOT GLOB '*[^0-9a-f]*'
    )),
    CHECK (publish_request_sha256 IS NULL OR (
        typeof(publish_request_sha256)='text' AND length(publish_request_sha256)=64
        AND publish_request_sha256 NOT GLOB '*[^0-9a-f]*'
    )),
    CHECK (inputs_frozen_at_us IS NULL OR (
        typeof(inputs_frozen_at_us)='integer' AND inputs_frozen_at_us BETWEEN admitted_at_us AND 9223372036854775807
    )),
    CHECK (publish_authorized_at_us IS NULL OR (
        typeof(publish_authorized_at_us)='integer'
        AND publish_authorized_at_us BETWEEN admitted_at_us AND 9223372036854775807
    )),
    CHECK (escrow_id IS NULL OR (typeof(escrow_id)='text' AND length(CAST(escrow_id AS BLOB))>0)),
    CHECK (lock_request_sha256 IS NULL OR (
        typeof(lock_request_sha256)='text' AND length(lock_request_sha256)=64
        AND lock_request_sha256 NOT GLOB '*[^0-9a-f]*'
    )),
    CHECK (lock_receipt_event_id IS NULL OR (
        typeof(lock_receipt_event_id)='integer' AND lock_receipt_event_id BETWEEN 1 AND 9223372036854775807
    )),
    CHECK (published_at_us IS NULL OR (
        typeof(published_at_us)='integer' AND published_at_us BETWEEN admitted_at_us AND 9223372036854775807
    )),
    CHECK (creation_event_id IS NULL OR (
        typeof(creation_event_id)='integer' AND creation_event_id BETWEEN 1 AND 9223372036854775807
    )),
    CHECK (
        (request_protocol_version=1
         AND expected_input_count IS NULL AND base_request_sha256 IS NULL
         AND freeze_operation_id IS NULL AND canonical_freeze_request_json IS NULL
         AND freeze_request_sha256 IS NULL AND input_manifest_sha256 IS NULL
         AND publication_contract_sha256 IS NULL AND inputs_frozen_at_us IS NULL
         AND publish_operation_id IS NULL AND canonical_publish_request_json IS NULL
         AND publish_request_sha256 IS NULL AND publish_authorized_at_us IS NULL
         AND state IN ('awaiting_lock','published','blocked_integrity'))
        OR
        (request_protocol_version=2
         AND task_id=lower(task_id)
         AND expected_input_count IS NOT NULL AND base_request_sha256 IS NOT NULL)
    ),
    CHECK (
        request_protocol_version=1 OR
        (state='awaiting_inputs'
         AND freeze_operation_id IS NULL AND canonical_freeze_request_json IS NULL
         AND freeze_request_sha256 IS NULL AND input_manifest_sha256 IS NULL
         AND publication_contract_sha256 IS NULL AND inputs_frozen_at_us IS NULL
         AND publish_operation_id IS NULL AND canonical_publish_request_json IS NULL
         AND publish_request_sha256 IS NULL AND publish_authorized_at_us IS NULL
         AND escrow_id IS NULL AND lock_request_sha256 IS NULL
         AND lock_receipt_event_id IS NULL AND published_at_us IS NULL
         AND creation_event_id IS NULL AND result_json IS NULL AND error_json IS NULL)
        OR
        (state='awaiting_publish'
         AND freeze_operation_id IS NOT NULL AND canonical_freeze_request_json IS NOT NULL
         AND freeze_request_sha256 IS NOT NULL AND input_manifest_sha256 IS NOT NULL
         AND publication_contract_sha256 IS NOT NULL AND inputs_frozen_at_us IS NOT NULL
         AND publish_operation_id IS NULL AND canonical_publish_request_json IS NULL
         AND publish_request_sha256 IS NULL AND publish_authorized_at_us IS NULL
         AND escrow_id IS NULL AND lock_request_sha256 IS NULL
         AND lock_receipt_event_id IS NULL AND published_at_us IS NULL
         AND creation_event_id IS NULL AND result_json IS NULL AND error_json IS NULL)
        OR
        (state='awaiting_lock'
         AND freeze_operation_id IS NOT NULL AND canonical_freeze_request_json IS NOT NULL
         AND freeze_request_sha256 IS NOT NULL AND input_manifest_sha256 IS NOT NULL
         AND publication_contract_sha256 IS NOT NULL AND inputs_frozen_at_us IS NOT NULL
         AND publish_operation_id IS NOT NULL AND canonical_publish_request_json IS NOT NULL
         AND publish_request_sha256 IS NOT NULL AND publish_authorized_at_us IS NOT NULL
         AND escrow_id IS NULL AND lock_request_sha256 IS NULL
         AND lock_receipt_event_id IS NULL AND published_at_us IS NULL
         AND creation_event_id IS NULL AND result_json IS NULL AND error_json IS NULL)
        OR
        (state='published'
         AND freeze_operation_id IS NOT NULL AND canonical_freeze_request_json IS NOT NULL
         AND freeze_request_sha256 IS NOT NULL AND input_manifest_sha256 IS NOT NULL
         AND publication_contract_sha256 IS NOT NULL AND inputs_frozen_at_us IS NOT NULL
         AND publish_operation_id IS NOT NULL AND canonical_publish_request_json IS NOT NULL
         AND publish_request_sha256 IS NOT NULL AND publish_authorized_at_us IS NOT NULL
         AND escrow_id IS NOT NULL AND lock_request_sha256 IS NOT NULL
         AND lock_receipt_event_id IS NOT NULL AND published_at_us IS NOT NULL
         AND creation_event_id IS NOT NULL AND result_json IS NOT NULL AND error_json IS NULL)
        OR
        (state='blocked_integrity'
         AND freeze_operation_id IS NOT NULL AND canonical_freeze_request_json IS NOT NULL
         AND freeze_request_sha256 IS NOT NULL AND input_manifest_sha256 IS NOT NULL
         AND publication_contract_sha256 IS NOT NULL AND inputs_frozen_at_us IS NOT NULL
         AND publish_operation_id IS NOT NULL AND canonical_publish_request_json IS NOT NULL
         AND publish_request_sha256 IS NOT NULL AND publish_authorized_at_us IS NOT NULL
         AND escrow_id IS NULL AND lock_request_sha256 IS NULL
         AND lock_receipt_event_id IS NULL AND published_at_us IS NULL
         AND creation_event_id IS NULL AND result_json IS NULL AND error_json IS NOT NULL)
    ),
    CHECK (
        request_protocol_version=2 OR
        (state='awaiting_lock' AND escrow_id IS NULL AND lock_request_sha256 IS NULL
         AND lock_receipt_event_id IS NULL AND published_at_us IS NULL
         AND creation_event_id IS NULL AND result_json IS NULL AND error_json IS NULL)
        OR
        (state='published' AND escrow_id IS NOT NULL AND lock_request_sha256 IS NOT NULL
         AND lock_receipt_event_id IS NOT NULL AND published_at_us IS NOT NULL
         AND creation_event_id IS NOT NULL AND result_json IS NOT NULL AND error_json IS NULL)
        OR
        (state='blocked_integrity' AND escrow_id IS NULL AND lock_request_sha256 IS NULL
         AND lock_receipt_event_id IS NULL AND published_at_us IS NULL
         AND creation_event_id IS NULL AND result_json IS NULL AND error_json IS NOT NULL)
    ),
    FOREIGN KEY (actor_id) REFERENCES identity_agents(agent_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (escrow_id) REFERENCES bank_escrow(escrow_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (lock_receipt_event_id) REFERENCES events(event_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (creation_event_id) REFERENCES events(event_id) ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX idx_board_task_publications_recovery
    ON board_task_publications (caller_kid, state, operation_id);

CREATE UNIQUE INDEX idx_board_tasks_task_poster_v6
    ON board_tasks (task_id, poster_id);

CREATE UNIQUE INDEX idx_board_tasks_task_worker_v6
    ON board_tasks (task_id, worker_id);

CREATE TABLE board_publication_cutover (
    singleton INTEGER PRIMARY KEY NOT NULL
            CHECK (typeof(singleton) = 'integer' AND singleton = 1),
    cutover_version INTEGER NOT NULL
            CHECK (typeof(cutover_version) = 'integer' AND cutover_version = 1),
    cutover_kind TEXT NOT NULL
            CHECK (typeof(cutover_kind) = 'text' AND cutover_kind IN ('fresh', 'migrated')),
    source_schema_version INTEGER,
    source_snapshot_sha256 TEXT,
    migration_plan_sha256 TEXT,
    backup_id TEXT,
    backup_manifest_sha256 TEXT,
    caller_kid TEXT,
    legacy_task_ids_sha256 TEXT NOT NULL
            CHECK (typeof(legacy_task_ids_sha256) = 'text'
                   AND length(legacy_task_ids_sha256) = 64
                   AND legacy_task_ids_sha256 NOT GLOB '*[^0-9a-f]*'),
    legacy_task_count INTEGER NOT NULL
            CHECK (typeof(legacy_task_count) = 'integer'
                   AND legacy_task_count BETWEEN 0 AND 9223372036854775807),
    legacy_observations_sha256 TEXT NOT NULL
            CHECK (typeof(legacy_observations_sha256) = 'text'
                   AND length(legacy_observations_sha256) = 64
                   AND legacy_observations_sha256 NOT GLOB '*[^0-9a-f]*'),
    legacy_observation_count INTEGER NOT NULL
            CHECK (typeof(legacy_observation_count) = 'integer'
                   AND legacy_observation_count BETWEEN 0 AND 9223372036854775807),
    CHECK (
        (cutover_kind = 'fresh'
         AND source_schema_version IS NULL
         AND source_snapshot_sha256 IS NULL
         AND migration_plan_sha256 IS NULL
         AND backup_id IS NULL
         AND backup_manifest_sha256 IS NULL
         AND caller_kid IS NULL)
        OR
        (cutover_kind = 'migrated'
         AND typeof(source_schema_version) = 'integer'
         AND source_schema_version = 3
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

CREATE TABLE board_legacy_posting_observations (
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
    observation_contract_version INTEGER NOT NULL
            CHECK (typeof(observation_contract_version) = 'integer'
                   AND observation_contract_version = 1),
    escrow_id TEXT NOT NULL UNIQUE
            CHECK (typeof(escrow_id) = 'text' AND length(CAST(escrow_id AS BLOB)) > 0),
    task_id TEXT NOT NULL
            CHECK (typeof(task_id) = 'text' AND length(CAST(task_id AS BLOB)) > 0),
    poster_id TEXT NOT NULL
            CHECK (typeof(poster_id) = 'text' AND length(CAST(poster_id AS BLOB)) > 0),
    amount INTEGER NOT NULL
            CHECK (typeof(amount) = 'integer' AND amount BETWEEN 1 AND 9223372036854775807),
    observed_fact_json TEXT NOT NULL
            CHECK (typeof(observed_fact_json) = 'text'
                   AND json_valid(observed_fact_json) = 1
                   AND json_type(observed_fact_json) = 'object'),
    observed_fact_sha256 TEXT NOT NULL
            CHECK (typeof(observed_fact_sha256) = 'text'
                   AND length(observed_fact_sha256) = 64
                   AND observed_fact_sha256 NOT GLOB '*[^0-9a-f]*'),
    caller_kid TEXT NOT NULL
            CHECK (typeof(caller_kid) = 'text' AND length(CAST(caller_kid AS BLOB)) > 0),
    state TEXT NOT NULL
            CHECK (typeof(state) = 'text'
                   AND state IN ('refund_pending', 'refunded', 'blocked_integrity')),
    refund_operation_id TEXT,
    refund_request_json TEXT
            CHECK (refund_request_json IS NULL
                   OR (typeof(refund_request_json) = 'text'
                       AND json_valid(refund_request_json) = 1
                       AND json_type(refund_request_json) = 'object')),
    refund_request_sha256 TEXT,
    refund_receipt_event_id INTEGER,
    refunded_at_us INTEGER,
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
    CHECK (refund_operation_id IS NULL
           OR (typeof(refund_operation_id) = 'text'
               AND length(refund_operation_id) = 39
               AND substr(refund_operation_id, 1, 3) = 'op-'
               AND substr(refund_operation_id, 12, 1) = '-'
               AND substr(refund_operation_id, 17, 1) = '-'
               AND substr(refund_operation_id, 22, 1) = '-'
               AND substr(refund_operation_id, 27, 1) = '-'
               AND length(replace(substr(refund_operation_id, 4), '-', '')) = 32
               AND replace(substr(refund_operation_id, 4), '-', '') NOT GLOB '*[^0-9a-f]*')),
    CHECK (refund_request_sha256 IS NULL
           OR (typeof(refund_request_sha256) = 'text'
               AND length(refund_request_sha256) = 64
               AND refund_request_sha256 NOT GLOB '*[^0-9a-f]*')),
    CHECK (refund_receipt_event_id IS NULL
           OR (typeof(refund_receipt_event_id) = 'integer'
               AND refund_receipt_event_id BETWEEN 1 AND 9223372036854775807)),
    CHECK (refunded_at_us IS NULL
           OR (typeof(refunded_at_us) = 'integer'
               AND refunded_at_us BETWEEN 0 AND 9223372036854775807)),
    CHECK (
        (state = 'refund_pending'
         AND refund_operation_id IS NOT NULL
         AND refund_request_json IS NOT NULL
         AND refund_request_sha256 IS NOT NULL
         AND refund_receipt_event_id IS NULL
         AND refunded_at_us IS NULL
         AND result_json IS NULL
         AND error_json IS NULL)
        OR
        (state = 'refunded'
         AND refunded_at_us IS NOT NULL
         AND result_json IS NOT NULL
         AND error_json IS NULL
         AND ((refund_operation_id IS NULL
               AND refund_request_json IS NULL
               AND refund_request_sha256 IS NULL
               AND refund_receipt_event_id IS NULL)
              OR
              (refund_operation_id IS NOT NULL
               AND refund_request_json IS NOT NULL
               AND refund_request_sha256 IS NOT NULL
               AND refund_receipt_event_id IS NOT NULL)))
        OR
        (state = 'blocked_integrity'
         AND refund_receipt_event_id IS NULL
         AND refunded_at_us IS NULL
         AND result_json IS NULL
         AND error_json IS NOT NULL
         AND ((refund_operation_id IS NULL
               AND refund_request_json IS NULL
               AND refund_request_sha256 IS NULL)
              OR
              (refund_operation_id IS NOT NULL
               AND refund_request_json IS NOT NULL
               AND refund_request_sha256 IS NOT NULL)))
    ),
    FOREIGN KEY (escrow_id) REFERENCES bank_escrow (escrow_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (refund_receipt_event_id) REFERENCES events (event_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE INDEX idx_board_legacy_posting_recovery
    ON board_legacy_posting_observations (caller_kid, state, observation_id);

-- Fresh v4 has no legacy publication ownership and no invented migration provenance.
INSERT INTO board_publication_cutover (
    singleton,
    cutover_version,
    cutover_kind,
    source_schema_version,
    source_snapshot_sha256,
    migration_plan_sha256,
    backup_id,
    backup_manifest_sha256,
    caller_kid,
    legacy_task_ids_sha256,
    legacy_task_count,
    legacy_observations_sha256,
    legacy_observation_count
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
    '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    0,
    '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
    0
);

