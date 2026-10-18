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
