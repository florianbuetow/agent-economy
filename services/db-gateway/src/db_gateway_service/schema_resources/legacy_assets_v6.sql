-- MP-09 H1: additive per-file legacy asset observations.
-- Canonical SHA-256, UUIDv5 derivation, and source-context authority are
-- validated by the Gateway parser/audit layer rather than SQLite.

CREATE TABLE board_legacy_asset_observations (
    observation_id              TEXT PRIMARY KEY NOT NULL
                                CHECK (
                                    typeof(observation_id) = 'text'
                                    AND length(CAST(observation_id AS BLOB)) > 0
                                ),
    observation_contract_version INTEGER NOT NULL
                                CHECK (
                                    typeof(observation_contract_version) = 'integer'
                                    AND observation_contract_version = 1
                                ),
    migration_plan_id           TEXT NOT NULL
                                CHECK (
                                    typeof(migration_plan_id) = 'text'
                                    AND length(migration_plan_id) = 64
                                    AND length(CAST(migration_plan_id AS BLOB)) = 64
                                    AND migration_plan_id NOT GLOB '*[^0-9a-f]*'
                                ),
    legacy_asset_id             TEXT NOT NULL UNIQUE
                                CHECK (
                                    typeof(legacy_asset_id) = 'text'
                                    AND length(CAST(legacy_asset_id AS BLOB)) > 0
                                ),
    task_id                     TEXT NOT NULL
                                CHECK (
                                    typeof(task_id) = 'text'
                                    AND length(CAST(task_id AS BLOB)) > 0
                                ),
    uploader_id                 TEXT NOT NULL
                                CHECK (
                                    typeof(uploader_id) = 'text'
                                    AND length(CAST(uploader_id AS BLOB)) > 0
                                ),
    assigned_worker_id          TEXT
                                CHECK (
                                    assigned_worker_id IS NULL
                                    OR (
                                        typeof(assigned_worker_id) = 'text'
                                        AND length(CAST(assigned_worker_id AS BLOB)) > 0
                                    )
                                ),
    source_row_sha256           TEXT NOT NULL
                                CHECK (
                                    typeof(source_row_sha256) = 'text'
                                    AND length(source_row_sha256) = 64
                                    AND length(CAST(source_row_sha256 AS BLOB)) = 64
                                    AND source_row_sha256 NOT GLOB '*[^0-9a-f]*'
                                ),
    attribution_status          TEXT NOT NULL
                                CHECK (
                                    typeof(attribution_status) = 'text'
                                    AND attribution_status IN (
                                        'verified_worker',
                                        'worker_unassigned',
                                        'uploader_mismatch'
                                    )
                                ),
    byte_status                 TEXT NOT NULL
                                CHECK (
                                    typeof(byte_status) = 'text'
                                    AND byte_status IN (
                                        'verified',
                                        'unsafe_path',
                                        'shared_source_conflict',
                                        'missing',
                                        'symlink',
                                        'non_regular',
                                        'size_mismatch',
                                        'hash_mismatch'
                                    )
                                ),
    declared_hash_status        TEXT NOT NULL
                                CHECK (
                                    typeof(declared_hash_status) = 'text'
                                    AND declared_hash_status IN (
                                        'absent', 'unobserved', 'matched', 'mismatch'
                                    )
                                ),
    copy_status                 TEXT NOT NULL
                                CHECK (
                                    typeof(copy_status) = 'text'
                                    AND copy_status IN ('installed', 'unavailable')
                                ),
    canonical_observation_json  TEXT NOT NULL
                                CHECK (
                                    typeof(canonical_observation_json) = 'text'
                                    AND length(CAST(canonical_observation_json AS BLOB)) > 0
                                    AND json_valid(canonical_observation_json) = 1
                                    AND json_type(canonical_observation_json) = 'object'
                                ),
    observation_sha256          TEXT NOT NULL UNIQUE
                                CHECK (
                                    typeof(observation_sha256) = 'text'
                                    AND length(observation_sha256) = 64
                                    AND length(CAST(observation_sha256 AS BLOB)) = 64
                                    AND observation_sha256 NOT GLOB '*[^0-9a-f]*'
                                ),
    observed_at_us              INTEGER NOT NULL
                                CHECK (
                                    typeof(observed_at_us) = 'integer'
                                    AND observed_at_us BETWEEN 0 AND 9223372036854775807
                                ),
    observed_size_bytes         INTEGER
                                CHECK (
                                    observed_size_bytes IS NULL
                                    OR (
                                        typeof(observed_size_bytes) = 'integer'
                                        AND observed_size_bytes BETWEEN 0 AND 9223372036854775807
                                    )
                                ),
    observed_sha256             TEXT
                                CHECK (
                                    observed_sha256 IS NULL
                                    OR (
                                        typeof(observed_sha256) = 'text'
                                        AND length(observed_sha256) = 64
                                        AND length(CAST(observed_sha256 AS BLOB)) = 64
                                        AND observed_sha256 NOT GLOB '*[^0-9a-f]*'
                                    )
                                ),
    generated_artifact_id       TEXT UNIQUE
                                CHECK (
                                    generated_artifact_id IS NULL
                                    OR (
                                        typeof(generated_artifact_id) = 'text'
                                        AND length(CAST(generated_artifact_id AS BLOB)) > 0
                                    )
                                ),
    generated_object_key        TEXT UNIQUE
                                CHECK (
                                    generated_object_key IS NULL
                                    OR (
                                        typeof(generated_object_key) = 'text'
                                        AND length(CAST(generated_object_key AS BLOB)) > 0
                                    )
                                ),

    CHECK ((observed_size_bytes IS NULL) = (observed_sha256 IS NULL)),
    CHECK ((generated_artifact_id IS NULL) = (generated_object_key IS NULL)),
    CHECK (
        (
            byte_status IN ('verified', 'size_mismatch', 'hash_mismatch')
            AND observed_size_bytes IS NOT NULL
            AND observed_sha256 IS NOT NULL
        )
        OR (
            byte_status IN (
                'unsafe_path',
                'shared_source_conflict',
                'missing',
                'symlink',
                'non_regular'
            )
            AND observed_size_bytes IS NULL
            AND observed_sha256 IS NULL
        )
    ),
    CHECK (
        (byte_status = 'verified' AND declared_hash_status IN ('absent', 'matched'))
        OR (
            byte_status IN (
                'unsafe_path',
                'shared_source_conflict',
                'missing',
                'symlink',
                'non_regular'
            )
            AND declared_hash_status IN ('absent', 'unobserved')
        )
        OR (
            byte_status = 'size_mismatch'
            AND declared_hash_status IN ('absent', 'matched', 'mismatch')
        )
        OR (byte_status = 'hash_mismatch' AND declared_hash_status = 'mismatch')
    ),
    CHECK (
        (
            copy_status = 'installed'
            AND byte_status = 'verified'
            AND generated_artifact_id IS NOT NULL
            AND generated_object_key IS NOT NULL
        )
        OR (
            copy_status = 'unavailable'
            AND byte_status <> 'verified'
            AND generated_artifact_id IS NULL
            AND generated_object_key IS NULL
        )
    ),
    CHECK (
        (
            attribution_status = 'verified_worker'
            AND assigned_worker_id IS NOT NULL
            AND assigned_worker_id = uploader_id
        )
        OR (attribution_status = 'worker_unassigned' AND assigned_worker_id IS NULL)
        OR (
            attribution_status = 'uploader_mismatch'
            AND assigned_worker_id IS NOT NULL
            AND assigned_worker_id <> uploader_id
        )
    ),

    FOREIGN KEY (legacy_asset_id) REFERENCES board_assets (asset_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (task_id) REFERENCES board_tasks (task_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (uploader_id) REFERENCES identity_agents (agent_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (assigned_worker_id) REFERENCES identity_agents (agent_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX idx_board_legacy_asset_observations_task
    ON board_legacy_asset_observations (task_id, legacy_asset_id);

CREATE INDEX idx_board_legacy_asset_observations_plan
    ON board_legacy_asset_observations (migration_plan_id, observation_id);
