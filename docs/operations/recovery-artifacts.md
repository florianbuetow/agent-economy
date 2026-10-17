# Recovery artifacts

This guide covers recovery artifacts from a full isolated seven-service runtime. The source
runtime, keys, database, assets, and configuration are disposable and owned by that run. Restored
service startup and schema migration have separate acceptance gates. Current implementation and
review evidence is recorded in
[`mp06-recovery-foundation`](../../openspec/changes/complete-agent-economy/evidence/mp06-recovery-foundation/README.md).

Run the commands from the repository root. Supply absolute, lexically normalized paths with real
directory ancestors. The working directory used for verification must be outside the source,
backup storage, and restored target. Every backup ID and restore target is reserved without
overwriting an existing artifact.

The verification work root must belong to the effective operating-system user and have mode
`0700`. Recovery holds a persistent exclusive `.recovery-owner.lock` for the operation; concurrent
recovery operations using that root fail without proceeding. Processes using the same OS account
must honor this lease. It does not isolate recovery from arbitrary programs running as that account.

Database/WAL and key audit clones are retained beneath the private work root on success and failure.
Recovery closes their descriptors and releases its lease; it does not recursively delete named
scratch directories. An ownership anomaly leaves the visible entries untouched. The owning disposable
runtime or test lifecycle may later dispose of the dedicated work root once recovery has ended.
Keep unrelated files out of it and account for the retained copies when allocating scratch storage.
Python's descriptor-relative deletion APIs bind the parent directory, but offer no atomic
“remove only if this inode still matches” operation. See the
[Python filesystem API](https://docs.python.org/3.13/library/os.html#os.rmdir) and
[POSIX unlinkat contract](https://pubs.opengroup.org/onlinepubs/9699919799/functions/unlink.html).

1. Start a disposable runtime and prepare its recovery inventory after a command finishes:

   ```sh
   recovery_project_root="$(pwd -P)"
   recovery_artifacts="/absolute/path/to/disposable-recovery-runs"

   uv run --directory "$recovery_project_root/services/db-gateway" \
     "$recovery_project_root/scripts/isolated_test_runtime.py" \
     --artifacts-root "$recovery_artifacts" \
     --provision \
     --prepare-recovery \
     --registry-timeout-seconds 10 \
     --registry-max-attempts 2 \
     -- /usr/bin/true
   ```

   Replace the final command with the workload to capture. Preparation also runs after a command
   returns nonzero; that original nonzero exit is retained. Preparation failure is nonzero and
   retains diagnostic state in the run directory. The final success JSON names
   `recovery_inventory`, its SHA-256 digest, and `runtime_manifest`.

   Preparation checks startup configuration and maintenance-lock identity, captures stable
   public-key registry bindings, drains all owned processes, then publishes canonical inventory.
   It never treats a failed registry read as an empty registry. A normal run without
   `--prepare-recovery` performs its usual shutdown without producing an inventory.

2. Set the exact generated run directory from that output and create an artifact:

   ```sh
   recovery_source_run="/absolute/path/to/disposable-recovery-runs/runtime-EXACT-RUN"
   recovery_work="/absolute/path/to/separate-verification-work"
   recovery_backup_id="checkpoint-001"
   mkdir -m 700 "$recovery_work"

   uv run --directory "$recovery_project_root/services/db-gateway" \
     python -m db_gateway_service.recovery_cli backup create \
     --inventory "$recovery_source_run/recovery-inventory.json" \
     --backup-root "$recovery_source_run/backups" \
     --backup-id "$recovery_backup_id" \
     --work-root "$recovery_work"

   uv run --directory "$recovery_project_root/services/db-gateway" \
     python -m db_gateway_service.recovery_cli backup verify \
     --backup-root "$recovery_source_run/backups" \
     --backup-id "$recovery_backup_id" \
     --work-root "$recovery_work"
   ```

   The backup contains raw DB and present WAL bytes, assets, key material, selected configuration
   bytes, and their binding evidence. SQLite checks run on disposable copies. The source database
   and immutable backup database are never opened for migration by these commands.

3. Construct a `TargetBindingsV1` JSON file using
   [`build_target_bindings`](../../scripts/recovery_targets.py). Its inputs are explicit:
   the captured inventory, a fresh target path, one process CWD per selected configuration, the
   agents package root, and every port, URL, and origin binding. It preserves the source runtime's
   directory layout and places captured external schema/frontend inputs under `captured-code`.
   It performs no filesystem writes or port reservations.

   The [live restore acceptance test](../../tests/runtime/test_recovery_live_restore.py) contains a
   complete example of these mappings. Save the resulting model with `model_dump_json()`; do not
   use a `runtime.json` document in place of the standalone target-binding contract.

4. Restore into the fresh target and verify it:

   ```sh
   recovery_target="/absolute/path/to/fresh-restored-target"
   recovery_target_bindings="/absolute/path/to/target-bindings.json"

   uv run --directory "$recovery_project_root/services/db-gateway" \
     python -m db_gateway_service.recovery_cli restore \
     --backup-root "$recovery_source_run/backups" \
     --backup-id "$recovery_backup_id" \
     --target-bindings "$recovery_target_bindings" \
     --target-root "$recovery_target" \
     --work-root "$recovery_work"

   uv run --directory "$recovery_project_root/services/db-gateway" \
     python -m db_gateway_service.recovery_cli verify-restore \
     --target-root "$recovery_target" \
     --work-root "$recovery_work"
   ```

   Verification checks recorded file contents and modes, explicit absences, configuration
   rendering, keypairs and registry identities, and the fresh target maintenance lock. It repeats
   database audits on disposable copies and retains findings such as `unknown_schema`.

The result fields describe separate evidence:

| Field | Meaning |
| --- | --- |
| `artifact_verified` | The selected artifact's integrity and structural checks passed. |
| `snapshot_preserved` | Required captured bytes and audit evidence are preserved. |
| `identity_complete` | Required key material and exact captured key-to-agent bindings agree. |
| `migration_eligible` | Always false for artifact contract v1: this verifier performs no migration admission or authorization. |
| `operational_restore_verified` | The separate running-economy recovery gate passed. |
| `finding_codes` | Preserved audit and identity findings that require interpretation or follow-up. |

An artifact check does not automatically advance the migration or operational gates. The
[`adopt_restored_check_only`](../../scripts/recovery_adoption.py) Python API returns an immutable
view of verified bindings and configuration paths. It exposes no process-start, provisioning,
command-execution, or rewrite operation.

If publication reports `publication_uncertain`, keep the exact artifact and identifier. Run
`backup finalize` with the same backup options, or `finalize-restore` with the same target/work
paths. Finalization verifies the existing bytes and retries durability operations; it does not
overwrite a bundle, regenerate keys, or rerun economic commands. A reserved incomplete identifier
is never silently reused. Private keys in these artifacts retain restrictive permissions.

## Explicit offline schema migration

The `migrate` command accepts only the complete supported legacy v0 schema. Fresh databases use
normal v1 bootstrap. Existing v0 databases fail startup with `migration_required`; startup does
not migrate them automatically. Partial, unknown, future, or already-v1 schemas cannot be passed
through this v0-to-v1 command.

Stop the source runtime and create its exact pre-migration backup before invoking migration.
Use its captured inventory and backup ID, with the same private work-root requirements above:

```sh
uv run --directory "$recovery_project_root/services/db-gateway" \
  python -m db_gateway_service.recovery_cli migrate \
  --inventory "$recovery_source_run/recovery-inventory.json" \
  --backup-root "$recovery_source_run/backups" \
  --backup-id "$recovery_backup_id" \
  --work-root "$recovery_work" \
  --busy-timeout-ms 5000
```

The timeout is required and accepts integer milliseconds from 0 through 2147483647. Zero requests
an immediate failure on SQLite lock contention. The command acquires fresh backup/source authority,
checks the verified read-only clone's schema, integrity, foreign keys and money, then opens the
existing source for a single transaction. It rejects missing or changed backups, changed source
bytes, unavailable maintenance locks, and invalid historical financial state before migration.
It does not coerce money, merge duplicate escrows, regenerate keys, provision funds, or start services.
Invalid historical records require explicit reconciliation outside this fixed migration.

Success is JSON with `operation: migrate` and a result containing `applied: true`, `schema_version: 1`,
`observation: committed_v1_verified`, exact backup/manifest identity, a plan checksum, and a retained
`evidence_path`. The result follows both in-transaction validation and a fresh read-only validation
after the writer closes. The schema metadata records the exact pre-migration backup provenance.
Artifact-v1 `migration_eligible` remains false: the separate artifact verifier does not authorize
migrations, even after a source has successfully migrated.

Any commit exception exits nonzero with `error: commit_outcome_uncertain`. Its safe details report
`committed_v1_observed`, `not_applied_v0_observed`, `inconsistent_state_observed`, or `unreadable_state`.
An observed v1 state does not turn an uncertain commit into success. Post-commit verification or
evidence failures are also nonzero. Retain the exact source, backup and available evidence; inspect
the reported state before selecting recovery actions. The command never retries a commit implicitly
or claims rollback after a possibly completed commit.

Migration evidence is written once, without replacement, as a private `0600` file in the retained
leased scratch directory. Its reported path remains usable after the operation closes. Evidence-write
failures preserve the original failure; if no trustworthy evidence path could be published, the
failure report omits that path. Use the ordinary backup restore command to inspect the preserved v0
snapshot at a separate target; it does not overwrite the source or prove operational recovery.
