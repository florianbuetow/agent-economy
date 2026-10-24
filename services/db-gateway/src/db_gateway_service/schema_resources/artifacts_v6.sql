-- Additive MP-09 artifact schema capability. Runtime schema admission remains V5.

CREATE TABLE board_artifact_manifests (
    manifest_id TEXT PRIMARY KEY NOT NULL CHECK (typeof(manifest_id)='text' AND length(manifest_id)>0),
    task_id TEXT NOT NULL CHECK (typeof(task_id)='text' AND length(task_id)>0),
    kind TEXT NOT NULL CHECK (kind IN ('input','submission')),
    actor_id TEXT NOT NULL CHECK (typeof(actor_id)='text' AND length(actor_id)>0),
    format_version INTEGER NOT NULL CHECK (typeof(format_version)='integer' AND format_version=1),
    state TEXT NOT NULL CHECK (state IN ('draft','frozen','closed_unsubmitted')),
    canonical_manifest_json TEXT CHECK (canonical_manifest_json IS NULL OR (json_valid(canonical_manifest_json)=1 AND json_type(canonical_manifest_json)='object')),
    manifest_sha256 TEXT CHECK (manifest_sha256 IS NULL OR (length(manifest_sha256)=64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*')),
    frozen_at_us INTEGER CHECK (frozen_at_us IS NULL OR (typeof(frozen_at_us)='integer' AND frozen_at_us BETWEEN 0 AND 9223372036854775807)),
    publication_operation_id TEXT,
    public_task_id TEXT,
    UNIQUE (manifest_id,kind,task_id,actor_id),
    UNIQUE (manifest_id,kind,task_id,publication_operation_id,actor_id),
    CHECK ((state='frozen' AND canonical_manifest_json IS NOT NULL AND manifest_sha256 IS NOT NULL AND frozen_at_us IS NOT NULL) OR (state!='frozen' AND canonical_manifest_json IS NULL AND manifest_sha256 IS NULL AND frozen_at_us IS NULL)),
    CHECK ((kind='input' AND publication_operation_id IS NOT NULL AND public_task_id IS NULL) OR (kind='submission' AND publication_operation_id IS NULL AND public_task_id IS NOT NULL AND public_task_id=task_id)),
    FOREIGN KEY (publication_operation_id,task_id,actor_id)
        REFERENCES board_task_publications(operation_id,task_id,actor_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (public_task_id,actor_id)
        REFERENCES board_tasks(task_id,worker_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE UNIQUE INDEX idx_board_artifact_one_input_manifest
    ON board_artifact_manifests(publication_operation_id)
    WHERE publication_operation_id IS NOT NULL;

CREATE TABLE board_task_artifact_contracts (
    task_id TEXT PRIMARY KEY NOT NULL,
    artifact_contract_version INTEGER NOT NULL CHECK (typeof(artifact_contract_version)='integer' AND artifact_contract_version IN (0,1)),
    input_provenance TEXT NOT NULL CHECK (input_provenance IN ('native_v6','unknown_pre_v6')),
    accepted_binding_kind TEXT CHECK (accepted_binding_kind IS NULL OR accepted_binding_kind IN ('publication_contract','legacy_accepted_continuation')),
    base_request_sha256 TEXT,
    input_manifest_id TEXT,
    input_manifest_sha256 TEXT,
    publication_contract_sha256 TEXT,
    accepted_publication_contract_sha256 TEXT,
    legacy_acceptance_observation_sha256 TEXT,
    legacy_acceptance_observation_status TEXT CHECK (legacy_acceptance_observation_status IS NULL OR legacy_acceptance_observation_status IN ('verified','incomplete_corrupt')),
    source_publication_request_sha256 TEXT,
    publication_operation_id TEXT,
    input_manifest_kind TEXT,
    input_actor_id TEXT,
    CHECK ((artifact_contract_version=1 AND input_provenance='native_v6' AND base_request_sha256 IS NOT NULL AND input_manifest_id IS NOT NULL AND input_manifest_sha256 IS NOT NULL AND publication_contract_sha256 IS NOT NULL AND legacy_acceptance_observation_sha256 IS NULL AND legacy_acceptance_observation_status IS NULL AND source_publication_request_sha256 IS NULL AND publication_operation_id IS NOT NULL AND input_manifest_kind='input' AND input_actor_id IS NOT NULL) OR (artifact_contract_version=0 AND input_provenance='unknown_pre_v6' AND base_request_sha256 IS NULL AND input_manifest_id IS NULL AND input_manifest_sha256 IS NULL AND publication_contract_sha256 IS NULL AND accepted_publication_contract_sha256 IS NULL AND publication_operation_id IS NULL AND input_manifest_kind IS NULL AND input_actor_id IS NULL AND ((accepted_binding_kind IS NULL AND legacy_acceptance_observation_sha256 IS NULL AND legacy_acceptance_observation_status IS NULL) OR (accepted_binding_kind='legacy_accepted_continuation' AND legacy_acceptance_observation_sha256 IS NOT NULL AND legacy_acceptance_observation_status IS NOT NULL)))),
    CHECK ((accepted_binding_kind IS NULL AND accepted_publication_contract_sha256 IS NULL AND legacy_acceptance_observation_sha256 IS NULL AND legacy_acceptance_observation_status IS NULL) OR (accepted_binding_kind='publication_contract' AND accepted_publication_contract_sha256 IS NOT NULL AND legacy_acceptance_observation_sha256 IS NULL AND legacy_acceptance_observation_status IS NULL) OR (accepted_binding_kind='legacy_accepted_continuation' AND accepted_publication_contract_sha256 IS NULL AND legacy_acceptance_observation_sha256 IS NOT NULL AND legacy_acceptance_observation_status IS NOT NULL)),
    CHECK (base_request_sha256 IS NULL OR (length(base_request_sha256)=64 AND base_request_sha256 NOT GLOB '*[^0-9a-f]*')),
    CHECK (input_manifest_sha256 IS NULL OR (length(input_manifest_sha256)=64 AND input_manifest_sha256 NOT GLOB '*[^0-9a-f]*')),
    CHECK (publication_contract_sha256 IS NULL OR (length(publication_contract_sha256)=64 AND publication_contract_sha256 NOT GLOB '*[^0-9a-f]*')),
    CHECK (accepted_publication_contract_sha256 IS NULL OR (length(accepted_publication_contract_sha256)=64 AND accepted_publication_contract_sha256 NOT GLOB '*[^0-9a-f]*')),
    CHECK (legacy_acceptance_observation_sha256 IS NULL OR (length(legacy_acceptance_observation_sha256)=64 AND legacy_acceptance_observation_sha256 NOT GLOB '*[^0-9a-f]*')),
    CHECK (source_publication_request_sha256 IS NULL OR (length(source_publication_request_sha256)=64 AND source_publication_request_sha256 NOT GLOB '*[^0-9a-f]*')),
    FOREIGN KEY (task_id,input_actor_id) REFERENCES board_tasks(task_id,poster_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (publication_operation_id,task_id,input_actor_id) REFERENCES board_task_publications(operation_id,task_id,actor_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (input_manifest_id,input_manifest_kind,task_id,publication_operation_id,input_actor_id) REFERENCES board_artifact_manifests(manifest_id,kind,task_id,publication_operation_id,actor_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE board_artifact_uploads (
    upload_operation_id TEXT PRIMARY KEY NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256)=64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    canonical_request_json TEXT NOT NULL CHECK (json_valid(canonical_request_json)=1 AND json_type(canonical_request_json)='object'),
    caller_kid TEXT NOT NULL CHECK (typeof(caller_kid)='text' AND length(caller_kid)>0),
    resource TEXT NOT NULL,
    task_id TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('poster','worker')),
    display_filename TEXT NOT NULL CHECK (typeof(display_filename)='text' AND length(display_filename)>0),
    evidence_format TEXT NOT NULL CHECK (evidence_format IN ('text','source','json','csv')),
    media_type TEXT NOT NULL CHECK (typeof(media_type)='text' AND length(media_type)>0),
    declared_size_bytes INTEGER NOT NULL CHECK (typeof(declared_size_bytes)='integer' AND declared_size_bytes BETWEEN 0 AND 9223372036854775807),
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    expected_lifecycle_version INTEGER CHECK (expected_lifecycle_version IS NULL OR (typeof(expected_lifecycle_version)='integer' AND expected_lifecycle_version BETWEEN 0 AND 9223372036854775807)),
    accepted_binding_kind TEXT CHECK (accepted_binding_kind IS NULL OR accepted_binding_kind IN ('publication_contract','legacy_accepted_continuation')),
    accepted_binding_sha256 TEXT CHECK (accepted_binding_sha256 IS NULL OR (length(accepted_binding_sha256)=64 AND accepted_binding_sha256 NOT GLOB '*[^0-9a-f]*')),
    manifest_kind TEXT NOT NULL CHECK (manifest_kind IN ('input','submission')),
    publication_operation_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('reserved','ready','archived','orphaned','integrity_blocked')),
    artifact_id TEXT NOT NULL UNIQUE,
    object_key TEXT NOT NULL UNIQUE,
    current_attempt_id TEXT,
    created_at_us INTEGER NOT NULL CHECK (typeof(created_at_us)='integer' AND created_at_us BETWEEN 0 AND 9223372036854775807),
    finalized_at_us INTEGER CHECK (finalized_at_us IS NULL OR (typeof(finalized_at_us)='integer' AND finalized_at_us BETWEEN 0 AND 9223372036854775807)),
    CHECK ((state='reserved' AND current_attempt_id IS NOT NULL AND finalized_at_us IS NULL) OR (state!='reserved' AND current_attempt_id IS NULL AND finalized_at_us IS NOT NULL)),
    CHECK ((manifest_kind='input' AND publication_operation_id IS NOT NULL AND actor_role='poster' AND resource=('POST /task-publications/' || publication_operation_id || '/inputs') AND expected_lifecycle_version IS NULL AND accepted_binding_kind IS NULL AND accepted_binding_sha256 IS NULL) OR (manifest_kind='submission' AND publication_operation_id IS NULL AND actor_role='worker' AND resource='POST /board/artifact-uploads/reserve' AND expected_lifecycle_version IS NOT NULL AND accepted_binding_kind IS NOT NULL AND accepted_binding_sha256 IS NOT NULL)),
    FOREIGN KEY (manifest_id,manifest_kind,task_id,actor_id) REFERENCES board_artifact_manifests(manifest_id,kind,task_id,actor_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (manifest_id,manifest_kind,task_id,publication_operation_id,actor_id) REFERENCES board_artifact_manifests(manifest_id,kind,task_id,publication_operation_id,actor_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (current_attempt_id,upload_operation_id)
        REFERENCES board_artifact_upload_attempts(attempt_id,upload_operation_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE TABLE board_artifact_upload_attempts (
    attempt_id TEXT PRIMARY KEY NOT NULL,
    upload_operation_id TEXT NOT NULL,
    owner_id TEXT NOT NULL CHECK (typeof(owner_id)='text' AND length(owner_id)>0),
    generation INTEGER NOT NULL CHECK (typeof(generation)='integer' AND generation BETWEEN 1 AND 9223372036854775807),
    attempt_nonce TEXT NOT NULL CHECK (length(attempt_nonce)=32 AND attempt_nonce NOT GLOB '*[^0-9a-f]*'),
    staging_key TEXT NOT NULL UNIQUE,
    intended_object_key TEXT NOT NULL,
    lease_expires_at_us INTEGER NOT NULL CHECK (typeof(lease_expires_at_us)='integer' AND lease_expires_at_us BETWEEN 0 AND 9223372036854775807),
    absolute_upload_deadline_us INTEGER NOT NULL CHECK (typeof(absolute_upload_deadline_us)='integer' AND absolute_upload_deadline_us BETWEEN 0 AND 9223372036854775807),
    state TEXT NOT NULL CHECK (state IN ('active','staged','installed','expired','orphaned','finalized','finalize_unknown','integrity_blocked')),
    observed_size_bytes INTEGER CHECK (observed_size_bytes IS NULL OR (typeof(observed_size_bytes)='integer' AND observed_size_bytes BETWEEN 0 AND 9223372036854775807)),
    observed_sha256 TEXT CHECK (observed_sha256 IS NULL OR (length(observed_sha256)=64 AND observed_sha256 NOT GLOB '*[^0-9a-f]*')),
    failure_code TEXT CHECK (failure_code IS NULL OR (typeof(failure_code)='text' AND length(failure_code)>0)),
    created_at_us INTEGER NOT NULL CHECK (typeof(created_at_us)='integer' AND created_at_us BETWEEN 0 AND 9223372036854775807),
    updated_at_us INTEGER NOT NULL CHECK (typeof(updated_at_us)='integer' AND updated_at_us BETWEEN 0 AND 9223372036854775807),
    UNIQUE (upload_operation_id,generation),
    UNIQUE (upload_operation_id,attempt_nonce),
    UNIQUE (attempt_id,upload_operation_id),
    CHECK (lease_expires_at_us<=absolute_upload_deadline_us),
    FOREIGN KEY (upload_operation_id) REFERENCES board_artifact_uploads(upload_operation_id) ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX idx_board_artifact_upload_manifest_state ON board_artifact_uploads(manifest_id,state);

CREATE TABLE board_legacy_artifact_observations (
    task_id TEXT PRIMARY KEY NOT NULL,
    observation_status TEXT NOT NULL CHECK (observation_status IN ('verified','incomplete_corrupt')),
    canonical_observation_json TEXT NOT NULL CHECK (json_valid(canonical_observation_json)=1 AND json_type(canonical_observation_json)='object'),
    observation_sha256 TEXT NOT NULL CHECK (length(observation_sha256)=64 AND observation_sha256 NOT GLOB '*[^0-9a-f]*'),
    observed_at_us INTEGER NOT NULL CHECK (typeof(observed_at_us)='integer' AND observed_at_us BETWEEN 0 AND 9223372036854775807),
    FOREIGN KEY (task_id) REFERENCES board_tasks(task_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE board_artifact_archive_attempts (
    archive_operation_id TEXT PRIMARY KEY NOT NULL,
    artifact_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    attempt_nonce TEXT NOT NULL UNIQUE CHECK (length(attempt_nonce)=32 AND attempt_nonce NOT GLOB '*[^0-9a-f]*'),
    live_object_key TEXT NOT NULL,
    archive_object_key TEXT NOT NULL UNIQUE,
    expected_size_bytes INTEGER NOT NULL CHECK (typeof(expected_size_bytes)='integer' AND expected_size_bytes BETWEEN 0 AND 9223372036854775807),
    expected_sha256 TEXT NOT NULL CHECK (length(expected_sha256)=64 AND expected_sha256 NOT GLOB '*[^0-9a-f]*'),
    state TEXT NOT NULL CHECK (state IN ('planned','installed','finalized','finalize_unknown','integrity_blocked')),
    created_at_us INTEGER NOT NULL CHECK (typeof(created_at_us)='integer' AND created_at_us BETWEEN 0 AND 9223372036854775807),
    updated_at_us INTEGER NOT NULL CHECK (typeof(updated_at_us)='integer' AND updated_at_us BETWEEN 0 AND 9223372036854775807),
    FOREIGN KEY (artifact_id) REFERENCES board_artifact_uploads(artifact_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE board_artifact_cutover (
    singleton INTEGER PRIMARY KEY NOT NULL CHECK (typeof(singleton)='integer' AND singleton=1),
    cutover_state TEXT NOT NULL CHECK (cutover_state IN ('planned','copying','verified','completed','blocked')),
    source_schema_version INTEGER NOT NULL CHECK (typeof(source_schema_version)='integer' AND source_schema_version=5),
    migration_plan_sha256 TEXT NOT NULL CHECK (length(migration_plan_sha256)=64 AND migration_plan_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_row_set_sha256 TEXT NOT NULL CHECK (length(source_row_set_sha256)=64 AND source_row_set_sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at_us INTEGER NOT NULL CHECK (typeof(created_at_us)='integer' AND created_at_us BETWEEN 0 AND 9223372036854775807),
    completed_at_us INTEGER CHECK (completed_at_us IS NULL OR (typeof(completed_at_us)='integer' AND completed_at_us BETWEEN 0 AND 9223372036854775807)),
    CHECK ((cutover_state='completed' AND completed_at_us IS NOT NULL) OR (cutover_state!='completed' AND completed_at_us IS NULL))
) WITHOUT ROWID;
