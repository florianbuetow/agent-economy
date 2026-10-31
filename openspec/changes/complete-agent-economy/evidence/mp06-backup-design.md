# MP-06 pre-migration backup and restore design

Date: 2026-10-15

Status: design evidence only. No recovery, migration, or operational restore implementation is
claimed by this document. The only executed technical probe was a disposable `/tmp` SQLite WAL
experiment described below.

## Purpose and scope

MP-06 must establish a verified backup before any real-data schema migration. The backup must
preserve the SQLite database including committed WAL state, configured identities, stored assets,
and the configuration evidence needed to explain how those resources were bound. A missing,
unreadable, incomplete, changed, or otherwise ineligible backup must prevent migration without
changing the source database.

This is the recovery foundation required by task 5.2. It is deliberately narrower than MP-21.
MP-06 proves an exact offline snapshot, verification, restoration to a fresh isolated target, and
migration admission. MP-21 later proves a complete live-like operational restore, reconciliation of
pending work, and safe promotion into normal service operation.

An owned offline maintenance window satisfies MP-06's current consistency requirement. MP-06 does
not specify zero-downtime backup. A later online or reduced-downtime workflow may reuse the same
bundle and verification format, but it must not be claimed from this foundation.

## Current boundaries that make the backup necessary

The Gateway currently opens SQLite before it has a versioned schema-admission decision and runs
opportunistic column changes from the writer constructor. Gateway startup also tolerates a missing
schema file and constructs the writer with no schema SQL. Those behaviors provide no point at which
a verified backup identifier can be required before mutation.

Task Board is the other mutation owner relevant to the snapshot. Asset upload writes the file first
and records its metadata through the Gateway afterward. A database-only backup can therefore be
readable while its evidence files are incomplete or from a different point in time. UI is a direct
read-only SQLite consumer. It cannot change economic rows, but its connection participates in the
same WAL lifetime and must be absent while raw DB/WAL files are captured, verified, migrated, or
restored.

The existing isolated runtime already supplies the process ownership and explicit path information
needed to prove quiescence safely. It owns exact process groups, stops services in reverse dependency
order, and generates per-run database, asset, key, and configuration paths. The recovery design
extends that existing binding rather than inventing a second service-discovery mechanism.

## Four separate recovery states

The implementation must not collapse these states into a single `valid` flag.

### Snapshot preservation

`snapshot_preserved` means the source state that could be read safely was copied exactly into an
immutable bundle, source and destination hashes agree, and the manifest describes both present and
absent resources honestly.

Snapshot preservation does not require the legacy data to satisfy the future schema's financial
constraints. REAL-valued money, duplicate escrows, historical identity aliases, foreign-key
findings, missing asset references, or other legacy anomalies must not prevent preservation of the
pre-migration state. Those findings are recorded in a separate audit report. Requiring the target
invariants before taking the backup would create a circular and unsafe migration requirement.

Unopenable SQLite corruption or an unsafe path may prevent a restorable snapshot, but the command
must still report exactly what was and was not captured. It must never follow an unsafe symlink to
make the result appear complete.

### Identity completeness

`identity_complete` means every configured economic identity and every Gateway caller keypair was
captured under the key-store locking contract, each private/public pair matches, private material has
the required protection, and the restored public fingerprints match the source configuration and
registered identity records where applicable.

Economic roster aliases are allowed. Two roster handles may intentionally share one public
fingerprint and must both be preserved and reported as aliases. Gateway callers remain stricter:
Gateway fingerprints must be unique among Gateway callers and no Gateway fingerprint may match any
economic roster fingerprint. The backup must mirror the current provisioning rule and must not add
a roster-to-roster uniqueness rule.

### Migration eligibility

`migration_eligible` means all of the following are true:

- the snapshot was preserved and its immutable files still match the manifest;
- identity completeness and the required asset/config capture obligations are satisfied;
- a disposable copy of the DB/WAL set is readable and storage-restorable;
- the live source DB/WAL identity still exactly matches the source recorded by the backup;
- the source schema is an explicitly recognized version, rather than partial, unknown, or newer;
- every anomaly that the selected migration must transform has an implemented deterministic audit
  and reconciliation path.

A preserved backup may therefore be ineligible for migration. This is expected and must not cause
the backup to be deleted. Migration reports the blocking findings without mutating the database.

### Operational restore verification

`operational_restore_verified` is an MP-21 result. It requires starting a fresh isolated economy from
the restored resources, reconciling supply and domain links, and continuing pending work once with
the original identities and evidence. MP-06 does not claim this state merely because a restored
SQLite file opens or a component test passes.

## Shared maintenance lock

The maintenance primitive belongs in `service-commons`, not in the Gateway package, because Gateway,
Task Board, and UI must use it without importing each other's service packages.

The proposed public API is a context manager similar to:

```python
class MaintenanceLockMode(StrEnum):
    SHARED = "shared"
    EXCLUSIVE = "exclusive"


@contextmanager
def hold_maintenance_lock(
    path: Path,
    *,
    mode: MaintenanceLockMode,
    blocking: bool,
) -> Iterator[None]:
    ...
```

The primitive opens a persistent regular `0600` lock file with `O_NOFOLLOW` and `O_CLOEXEC`, checks
the opened and current device/inode identity before and after `flock`, and applies the requested lock
without silently falling back to blocking behavior. The public API has library-owned tests for
missing paths, creation, modes, symlinks, inode replacement before and after flock, shared
coexistence, exclusive exclusion, nonblocking failure, release, and exception cleanup.

All three processes acquire the lock immediately after configuration validation and before any
protected resource mutation or open:

- Gateway acquires `SHARED` before creating the database directory or opening SQLite and holds it
  until after its reader/writer closes.
- Task Board acquires `SHARED` before creating the asset directory, registering its platform agent,
  or starting deadline work and holds it until requests, the periodic sweep, and clients drain.
- UI acquires `SHARED` before loading the operator key, registering the operator, or opening its
  read-only SQLite connection and holds it until both the agent and database connection close.

Each lifespan releases the lock in one outer `finally` path on successful shutdown and on every
startup failure after acquisition. Backup and migration acquire the same lock `EXCLUSIVE` and
nonblocking. Failure returns an actionable `runtime_active` error instead of waiting indefinitely.

Gateway, Task Board, and UI gain a required maintenance-lock field whose new contract is explicitly
config-parent-relative. The isolated runtime writes one absolute per-run path into all three generated
configurations and validates that the three resolved paths are identical before starting any process.
Its owned-process teardown
remains the first quiescence proof; the exclusive lock is an independent fail-closed check that the
database writer, asset writer, and direct database reader are no longer active.

## Public key snapshot surface

Recovery code must not import private signing helpers or call `load_keypair` separately for an
unbounded inventory. `service-auth` should expose a public batch snapshot operation that owns the
existing per-handle lock implementation:

```python
@dataclass(frozen=True)
class KeypairSnapshot:
    handle: str
    private_pem: bytes
    public_pem: bytes
    public_fingerprint_sha256: str
    private_mode: int
    public_mode: int


def snapshot_keypairs(
    targets: tuple[tuple[str, Path], ...],
    *,
    blocking: bool,
) -> tuple[KeypairSnapshot, ...]:
    ...
```

Each target is a handle and its canonical public-key path. Recovery owns completeness: it constructs
the required roster and Gateway target inventory and passes the whole inventory in one call. The
public signing API requires each target filename to be exactly `<handle>.pub`, derives the sibling
`<handle>.key` and `<handle>.lock`, and rejects any collision among every private, public, and lock
path in the complete inventory.

Before acquiring locks, the implementation validates every path and component without following
symlinks. It then acquires all pair locks shared and nonblocking in sorted canonical lock-path order.
The existing key-lock open must be hardened to compare `fstat` with `lstat` after opening and again
after flock, so replacement of the lock inode cannot redirect or split synchronization. All locks
remain held until all key bytes, modes, pair matches, and fingerprints have been read and validated.
The function returns immutable snapshots and releases locks in reverse order on success or failure.

The recovery layer then applies the existing cross-domain uniqueness rule and writes protected
backup copies. It preserves separate files and manifest entries for allowed economic aliases. It
does not import `_key_lock`, `_read_keypair`, or other private signing functions.

## DB and WAL identity

The source identity is a tuple covering the main database and the presence or absence, size, and
SHA-256 of its WAL. The shared-memory `-shm` file is not durable state and is not copied.

Under the exclusive maintenance lock, backup creation performs these steps:

1. Record WAL presence with `lstat` and open the main file and present WAL using
   `O_RDONLY | O_NOFOLLOW | O_CLOEXEC`.
2. Require each opened descriptor to reference a regular file and compare `fstat` device/inode with
   a fresh `lstat` of the path after open.
3. Hash and copy from those already-validated descriptors, never by reopening the path. Reads use an
   explicit offset so the same descriptor can be hashed again.
4. After copying, compare a second `fstat` and `lstat`, rehash the opened descriptors, and recheck WAL
   presence from the parent directory.
5. Abort if the main or WAL inode, type, size, or hash changed, if a path was replaced, or if the WAL
   appeared or disappeared.
6. Compare staged file hashes with the first source identity, fsync them, and close the source
   descriptors only after the raw copy is complete.

The staged immutable DB/WAL pair is never opened directly. Verification first copies that pair to a
disposable directory and opens only the disposable copy. SQLite may checkpoint, modify, or remove the
disposable WAL without altering the preserved evidence. After that verification, the immutable
staged hashes are checked again.

Schema classification, `PRAGMA integrity_check`, `foreign_key_check`, asset/database reconciliation,
and all financial/application audits run only against disposable clones of the preserved raw set.
Neither backup creation nor audit code opens the source SQLite files through SQLite. The normalized
audit result excludes timestamps and unstable paths and has an `audit_contract_version` and
SHA-256 recorded in the manifest before immutable publication.

Migration compares the current raw source tuple to the manifest before opening SQLite read-write. It
then creates a fresh clone from the immutable bundle, reruns the versioned audits, and requires the
normalized result checksum to match the manifest. It also recomputes and verifies the ordered
migration-set checksum recorded by the backup. An audit-code/version mismatch, audit-result mismatch,
or migration-checksum mismatch blocks source mutation. The pre-open identity, audit checksum,
migration checksum, and backup identifier are written to the migration attempt report because
opening the source may subsequently checkpoint the WAL.

The only executed implementation-adjacent evidence for this design was a synthetic `/tmp` probe. A
SQLite connection committed a row with WAL autocheckpoint disabled; copying the frozen main and WAL
to another directory restored the row successfully. The observed result was:

```text
wal_present=True restored_value=committed-in-wal
```

This probe does not prove the proposed lock, backup command, manifest, restore, or migration behavior.

## Backup bundle and publication

The bundle layout is:

```text
<backup-root>/<backup-id>/
  COMPLETE
  payload/
    manifest.json
    manifest.sha256
    database/economy.db
    database/economy.db-wal     # present only when the source WAL existed
    assets/<task>/<asset>/<file>
    keys/<handle>.key
    keys/<handle>.pub
    configs/source/<logical-name>.yaml
    audits/pre-migration.json
```

The manifest records the bundle-format version, backup ID, source schema classification, raw DB/WAL
identity, every file's role/path/size/mode/hash, key fingerprints and allowed alias groups, database
asset references, actual filesystem asset inventory, exact source configuration hashes, and the four
recovery-state results described above.

Creation reserves the final backup ID with an exclusive `mkdir(<backup-id>, 0700)`. The directory
must not already exist; no implementation path deletes, replaces, or reuses an existing ID. Work is
performed under `<backup-id>/.attempt-<attempt-id>/payload`. Private and public backup key material is
stored with mode `0600`; restoration may return public keys to their supported public mode while
retaining private mode `0600`. The backup root must not overlap any source DB, asset, key, or
configuration tree. Symlinks are never followed.

Raw files are copied first. A disposable clone of those raws is then classified and audited before
the immutable payload is published. The normalized audit report is part of the staged payload even
when it contains migration-blocking financial findings. Snapshot preservation and application audit
success remain separate fields.

The payload manifest is written last within the attempt. Payload files and every containing
directory are fsynced, `manifest.sha256` is written and fsynced, and the attempt payload is renamed to
the reserved directory's previously absent `payload` name. That rename is no-clobber because the
final directory is exclusively reserved and `payload` must not exist.

Publication uses a verification-required completion marker. The implementation writes the backup ID,
manifest digest, payload digest, and bundle-format version into a temporary marker, fsyncs it, and
creates `COMPLETE` from that exact inode with an exclusive hard link. Creation of the `COMPLETE` link
is the publication linearization point. Verifiers ignore any reserved directory without a parseable
marker whose digests match the payload. After the link, the implementation unlinks the temporary
marker and fsyncs the final directory and backup-root directory.

Failure before the `COMPLETE` link leaves an incomplete reserved ID which is never treated as a
backup and is never automatically deleted or overwritten. A new create attempt uses a new backup ID.
If any fsync or close after the completion link fails, the result is `publication_uncertain`; the
implementation must not remove or overwrite it. `backup verify --backup-id ...` performs an exact
read-only verification, and `backup finalize --backup-id ...` may retry only the required file and
directory fsync operations after exact verification. It never rewrites payload or marker bytes.

Attempt diagnostics live outside the immutable bundle under `<backup-root>/.attempts/`. Backup and
migration attempt reports are atomically published there and refer to the reserved backup ID,
attempt ID, stage reached, and safe error code. Failed-attempt diagnostics are not inserted into or
used to modify a completed bundle.

Local directory and file permissions satisfy the current protected-backup requirement. Encryption,
off-host replication, and retention/deletion schedules are deferred operational policies, not
blockers for the MP-06 local protected-backup foundation.

## Configuration and service-package boundaries

The Gateway recovery CLI must not import Task Board, UI, agents, or another service's configuration
package. Those packages are not installed in the Gateway environment and such imports would reverse
service boundaries.

`service-commons` owns a neutral, versioned serialized contract in
`service_commons.recovery_contract`. It contains no imports from a service package. The initial
models are conceptually:

```python
class RecoveryInventoryV1(BaseModel):
    contract_version: Literal[1]
    runtime_id: str
    source_root: Path
    agents_package_root: Path
    maintenance_lock_path: Path
    maintenance_lock_identity: MaintenanceLockIdentityV1
    resources: tuple[SourceResourceV1, ...]
    configs: tuple[SourceConfigV1, ...]
    field_bindings: tuple[SourceFieldBindingV1, ...]
    keypairs: tuple[SourceKeypairV1, ...]
    process_ownership: tuple[OwnedProcessV1, ...]


class SourceConfigV1(BaseModel):
    logical_id: str
    captured_path: Path
    capture_root: Path
    process_cwd: Path
    sha256: str


class SourceFieldBindingV1(BaseModel):
    binding_id: str
    config_id: str
    field_path: tuple[str | int, ...]
    binding_kind: BindingKind
    captured_value: str | int | tuple[str, ...]
    resolution_base: PathResolutionBase | None
    resolved_source_path: Path | None
    resource_id: str | None


class TargetBindingsV1(BaseModel):
    contract_version: Literal[1]
    target_root: Path
    process_cwds: dict[str, Path]
    agents_package_root: Path
    resource_paths: dict[str, Path]
    endpoint_values: dict[str, str | int | list[str]]
```

Unknown contract versions, unknown dotted fields, and unknown binding kinds fail closed. A
filesystem binding requires its resolved absolute source path and the exact base used to resolve a
relative captured value. A non-filesystem endpoint forbids a resolved path. The models serialize to
canonical JSON for hashing. `RecoveryInventoryV1` describes what the source process actually used;
`TargetBindingsV1` describes where those logical resources and endpoints will live in the fresh
target. Neither model asserts that a complete service configuration is valid.

Every source resource and selected configuration has a required `capture_root`. The producer
assigns the owned runtime root to mutable resources and an explicitly supplied code root to
repository schema and frontend inputs. It rejects configured paths outside those declared roots.
Consumers validate lexical containment and traverse the complete path without following symlinks;
the declared boundary does not independently grant filesystem permission. Resource/config IDs used
in artifact filenames must be safe single components. Destination-overlap checks use the actual
captured files or subtrees, rather than excluding the entire provenance or code root.

Two explicit file intents preserve actual runtime topology. `ABSENT_FILE` records expected absence
of a legitimate optional legacy Board database or unprovisioned task input; capture verifies absence
before and after, and restore leaves it absent. It cannot replace a required primary database,
schema, selected configuration, or keypair. `RUNTIME_LOCK` records synchronization identity without
copying lock bytes. Exactly one maintenance lock is required by recovery completeness, all protected
service bindings reference it, and the held descriptor must match the inventory's captured device
and inode. The context rechecks identity before release. Restore creates a fresh empty `0600` lock.

Each configured keypair records its expected public fingerprint, registration requirement
(`required`, `optional`, or Gateway-only `forbidden`), captured registration state, and nullable
expected registered agent ID. Registration evidence comes from successful Identity list/detail/list
reads using exact canonical public keys; display names never bind identities. Managed registrations
must exist, unused roster keys may remain unregistered, and external registered agents do not require
server-owned private keys. Pre-stop and offline-capture checks repeat the fingerprint/registry
comparison. Consistent economic aliases may share a key, but one agent ID cannot claim different
fingerprints, Gateway fingerprints must be unique, and economic/Gateway domains must be disjoint.
Cold optional/unregistered identities may be source-complete while operational restore remains
unverified. No registry error is treated as an empty registry.

In isolated runs, the existing runtime generator is the authoritative inventory producer because it
already owns the generated database, asset, key, roster, agent-config, service-config, log, port, and
process bindings. It embeds canonical `RecoveryInventoryV1` JSON in `runtime.json`. For ordinary local
operation, root orchestration creates the same neutral inventory from explicit config paths and
narrowly typed projections. Gateway consumes only the neutral JSON.

The following table is the exhaustive MP-06 binding table for the confirmed current service/runtime
fields. Wildcards expand to every configured entry. `not_filesystem` means the value is a port, URL,
or origin and is not path-resolved. Proposed recovery fields use `config_parent` by explicit new
contract; this does not change the existing fields' behavior.

| Config | Dotted field | Relative base | Binding |
| --- | --- | --- | --- |
| all service configs | `server.port` | `not_filesystem` | target service port |
| all service configs | `logging.directory` | `process_cwd` | target log root |
| agents | `platform.identity_url` | `not_filesystem` | Identity endpoint |
| agents | `platform.bank_url` | `not_filesystem` | Central Bank endpoint |
| agents | `platform.task_board_url` | `not_filesystem` | Task Board endpoint |
| agents | `platform.reputation_url` | `not_filesystem` | Reputation endpoint |
| agents | `platform.court_url` | `not_filesystem` | Court endpoint |
| agents | `data.keys_dir` | `config_parent` | key root |
| agents | `data.roster_path` | `config_parent` | roster file |
| agents | `task_feeder.tasks_file` | `agents_package_root` | configured task-input file |
| agents | `workers.*.llm.base_url` | `not_filesystem` | configured provider endpoint |
| db-gateway | `database.path` | `process_cwd` | primary DB |
| db-gateway | `database.schema_path` | `process_cwd` | schema source |
| db-gateway | `auth.trusted_callers.*.public_key_path` | `config_parent` | Gateway public key by KID |
| db-gateway | `recovery.maintenance_lock_path` | `config_parent` | shared maintenance lock |
| db-gateway | `recovery.backup_root` | `config_parent` | source backup root, rebound to target recovery root |
| identity | `db_gateway.url` | `not_filesystem` | Gateway endpoint |
| identity | `db_gateway.signing.private_key_path` | `config_parent` | Gateway Identity private key |
| central-bank | `identity.base_url` | `not_filesystem` | Identity endpoint |
| central-bank | `platform.agent_config_path` | `config_parent` | agents config |
| central-bank | `db_gateway.url` | `not_filesystem` | Gateway endpoint |
| central-bank | `db_gateway.signing.private_key_path` | `config_parent` | Gateway Central Bank private key |
| task-board | `database.path` | `process_cwd` | legacy Task Board path binding retained as configuration evidence |
| task-board | `identity.base_url` | `not_filesystem` | Identity endpoint |
| task-board | `central_bank.base_url` | `not_filesystem` | Central Bank endpoint |
| task-board | `platform.agent_config_path` | `config_parent` | agents config |
| task-board | `assets.storage_path` | `process_cwd` | asset root |
| task-board | `db_gateway.url` | `not_filesystem` | Gateway endpoint |
| task-board | `db_gateway.signing.private_key_path` | `config_parent` | Gateway Task Board private key |
| task-board | `recovery.maintenance_lock_path` | `config_parent` | shared maintenance lock |
| reputation | `platform.agent_config_path` | `config_parent` | agents config |
| reputation | `identity.base_url` | `not_filesystem` | Identity endpoint |
| reputation | `db_gateway.url` | `not_filesystem` | Gateway endpoint |
| reputation | `db_gateway.signing.private_key_path` | `config_parent` | Gateway Reputation private key |
| court | `platform.agent_config_path` | `config_parent` | agents config |
| court | `judges.judges.*.api_base` | `not_filesystem` | configured provider endpoint when present |
| court | `db_gateway.url` | `not_filesystem` | Gateway endpoint |
| court | `db_gateway.signing.private_key_path` | `config_parent` | Gateway Court private key |
| ui | `database.path` | `process_cwd` | primary DB read binding |
| ui | `frontend.web_root` | `process_cwd` | static web root |
| ui | `browser_security.allowed_origins` | `not_filesystem` | target UI origins |
| ui | `user_agent.agent_config_path` | `process_cwd` | agents config |
| ui | `recovery.maintenance_lock_path` | `config_parent` | shared maintenance lock |

`CONFIG_PATH` and `AGENT_CONFIG_PATH` only select which YAML file a process loads. They do not provide
a generic field-overlay mechanism. The inventory records the selected absolute YAML paths and each
`SourceConfigV1.process_cwd`. For every filesystem field, the producer records its captured value,
explicit base enum, and resolved absolute source path. It resolves `process_cwd` fields against the
captured source process CWD and `config_parent` fields against the captured YAML parent. It never
resolves either class against the recovery CLI's current directory.

`task_feeder.tasks_file` is the one confirmed package-anchored exception: Task Feeder resolves a
relative value against `Path(__file__).resolve().parents[2]`, the captured agents package root
(`agents/src/task_feeder/loop.py:45`). The inventory therefore captures `agents_package_root` and
uses that explicit base for the field. Worker `llm.base_url` is passed through as a literal URL
(`agents/src/base_agent/worker_factory.py:101`); only the API-key field performs environment
placeholder handling, and secret environment values are not copied into the backup. Court
`judges.judges.*.api_base` is likewise a literal optional URL. Gateway trusted-caller public paths
remain config-parent-relative as implemented by `services/db-gateway/src/db_gateway_service/app.py:30`.

The Gateway recovery CLI validates inventory structure, process ownership, containment, hashes, and
bundle semantics. It does not validate restored Task Board, UI, Court, or other service settings and
does not import their Settings models. After target rendering, each actual service validates its own
complete configuration during normal startup. Failure prevents readiness and remains visible as
restore-validation evidence.

Runnable CLI examples follow the repository's Python execution contract:

```text
uv run --directory services/db-gateway python -m db_gateway_service.recovery_cli \
  backup create --inventory /absolute/path/runtime.json

uv run --directory services/db-gateway python -m db_gateway_service.recovery_cli \
  backup verify --inventory /absolute/path/runtime.json --backup-id <id>
```

## Restored paths and exact configuration evidence

Exact original configuration bytes remain immutable evidence under `configs/source/`. A restored
runtime must not use those files unchanged when they contain absolute source paths, source ports, or
source URLs.

The renderer always starts from the captured source configuration bytes in the selected bundle. It
must not reload a current repository template, because the repository may have changed since the
backup. It parses each captured YAML mapping, applies only the inventory's exhaustive dotted-field
bindings using `TargetBindingsV1`, and writes a separate active target config. Generic string
replacement and unrecorded field mutation are prohibited. Every old-to-new value is recorded in
`restore-manifest.json` with its config identity and dotted field.

For a captured relative filesystem value, the renderer first trusts the inventory's already-resolved
absolute source path; it never re-resolves that value from the recovery process CWD. It maps the
logical resource to its absolute target path, then serializes the target value relative to the
corresponding target `process_cwd`, target config parent, or target `agents_package_root` according to
the same base enum. An originally absolute binding remains absolute. This preserves each consumer's
existing resolution semantics and does not normalize application behavior as part of backup work.

The clean ownership split is:

- the recovery library restores and verifies database, WAL, asset, key, and source-config evidence;
- the isolated-runtime/configuration layer renders target-specific active configurations using its
  existing binding knowledge;
- each service's actual startup validates the resulting complete configuration.

Restore reserves a target path that does not exist using exclusive `mkdir`. It builds and verifies a
payload beneath that reserved directory, restores resources and source evidence, renders target
configs from captured source bytes through the runtime layer, and verifies hashes, fingerprints,
containment, DB readability, and recorded bindings. It then publishes a verification-required
`COMPLETE` marker using the same no-clobber protocol as backup publication. Only a target with a
matching marker may be opened by the supervisor. A post-marker fsync failure is
`publication_uncertain` and requires exact verify/finalize retry; it never triggers deletion or
overwrite.

In-place replacement of an existing ordinary runtime is not part of MP-06. MP-21 may add a stopped-
runtime promotion operation that first preserves the displaced target and proves exact ownership.

## Migration admission

Schema classification and migration are removed from `DbWriter` construction and placed in a
dedicated Gateway migration module. Classification and admission audits run against clones; normal
Gateway startup opens the source only after classification evidence says it is already current.

- A missing or empty database may be initialized at the current schema without a backup.
- An exact current schema is validated and opened without mutation.
- An exact supported legacy schema makes normal startup fail with `migration_required` and an
  actionable command; startup does not migrate it automatically.
- A partial, unknown, or newer schema fails readiness without mutation.
- The explicit offline migration command requires an exact backup ID and the exclusive maintenance
  lock.

Before opening the source read-write, migration verifies the immutable bundle, required recovery
states, and exact current DB/WAL identity. It clones the preserved raws, reruns schema classification
and every versioned pre-migration audit on that clone, verifies the normalized audit checksum and
ordered migration-set checksum, and determines eligibility. Audit findings remain attached to the
backup and may block mutation without invalidating snapshot preservation. Every migration attempt
report is published under the external `.attempts` area and never modifies the completed bundle.

An eligible migration runs its ordered steps in one transaction, installs the new schema version,
records the verified backup ID and migration checksum, and runs structural, integrity, foreign-key,
financial-conservation, and type checks before commit. Failure rolls back the transaction and retains
the original bundle. It never restores automatically over the source, deletes receipts, regenerates
keys, coerces invalid values, merges aliases, or chooses a canonical duplicate escrow silently.

The explicit command is run through the Gateway environment:

```text
uv run --directory services/db-gateway python -m db_gateway_service.recovery_cli \
  migrate --inventory /absolute/path/runtime.json --backup-id <id>
```

## Proposed implementation boundaries

- `libs/service-commons/src/service_commons/maintenance_lock.py`: shared lock primitive.
- `libs/service-commons/tests/unit/test_maintenance_lock.py`: path, mode, exclusion, and cleanup tests.
- `libs/service-commons/src/service_commons/recovery_contract.py`: neutral
  `RecoveryInventoryV1`/`TargetBindingsV1` serialized models and canonical hashing.
- `libs/service-commons/tests/unit/test_recovery_contract.py`: version, unknown-field, canonical JSON,
  and binding validation.
- `libs/service-auth/src/service_auth/signing.py`: public batch key snapshot surface.
- `libs/service-auth/tests/unit/test_keypair_snapshot.py`: complete-set locks, aliases, modes, and
  mutation-race tests.
- `services/db-gateway/src/db_gateway_service/recovery.py`: manifest, snapshot, verification, and
  restore-resource logic with no imports from other service packages.
- `services/db-gateway/src/db_gateway_service/recovery_cli/__main__.py`: explicit backup, verify,
  restore, audit, and migrate commands.
- `services/db-gateway/src/db_gateway_service/schema_validation.py`: exact supported-layout and
  current-layout classification.
- `services/db-gateway/src/db_gateway_service/migrations.py`: ordered migration and admission logic.
- Gateway configuration/lifespan/writer: required recovery paths, shared lock, mandatory schema,
  current-schema-only writer construction.
- Task Board configuration/lifespan: same shared lock around the asset mutation lifetime.
- UI configuration/lifespan: the same shared lock around key loading, registration, and the direct
  read-only DB connection.
- `scripts/isolated_test_runtime.py`: recovery inventory, target path/endpoint bindings, and restored
  runtime creation/publication.
- Component and runtime tests: WAL, publication faults, legacy preservation, identity completeness,
  config rebinding, migration admission, and fresh-target restoration.

No new external dependency is required.

## Required failing-test-first coverage

The implementation needs dedicated red cases, including these named races and uncertain-publication
histories:

1. `test_gateway_holds_shared_maintenance_lock_before_database_directory_or_open`.
2. `test_task_board_holds_shared_maintenance_lock_before_asset_directory_or_registration`.
3. `test_ui_read_connection_holds_shared_maintenance_lock`.
4. `test_each_lifespan_releases_maintenance_lock_after_startup_failure` and the corresponding clean
   shutdown case for Gateway, Task Board, and UI.
5. `test_runtime_rejects_three_different_maintenance_lock_paths_before_start`.
6. `test_main_inode_replaced_after_fd_open_aborts_snapshot`.
7. `test_wal_inode_replaced_after_fd_open_aborts_snapshot`.
8. `test_wal_appears_or_disappears_during_fd_copy_aborts_snapshot`.
9. `test_committed_wal_only_data_survives_snapshot_and_fresh_restore`.
10. `test_clone_audit_does_not_checkpoint_or_change_immutable_bundle_wal`.
11. `test_legacy_financial_audit_failure_still_publishes_preserved_snapshot`.
12. `test_migration_reaudit_checksum_mismatch_blocks_source_mutation`.
13. `test_key_lock_inode_swap_after_open_or_flock_aborts_batch`.
14. `test_key_batch_rejects_private_public_or_lock_path_collision`.
15. `test_key_batch_holds_all_sorted_locks_until_complete_snapshot`.
16. `test_roster_aliases_are_preserved_but_gateway_collisions_are_incomplete`.
17. Missing, unsafe, orphaned, or hash-mismatched assets are preserved/reported honestly rather than
    followed, omitted silently, or presented as complete.
18. `test_restore_renderer_uses_captured_configs_when_repo_templates_change`.
19. `test_recovery_inventory_lists_every_runtime_relative_dotted_field`.
20. `test_source_bindings_resolve_with_recorded_process_cwd_or_config_parent_not_recovery_cwd`.
21. `test_task_feeder_input_uses_captured_agents_package_root`.
22. `test_url_bindings_are_never_path_resolved`.
23. `test_config_and_agent_environment_variables_select_yaml_without_field_overlay`.
24. `test_gateway_recovery_import_graph_contains_no_other_service_package`.
25. Each restored service runs its real startup validation against rendered target configs.
26. `test_two_creators_reserving_same_backup_id_have_one_winner_and_no_clobber`.
27. `test_verifier_ignores_reserved_directory_without_matching_complete_marker`.
28. `test_failure_before_complete_marker_never_becomes_valid_backup`.
29. `test_post_marker_parent_fsync_failure_returns_publication_uncertain`.
30. `test_uncertain_publication_exact_verify_and_finalize_never_rewrite_payload`.
31. `test_restore_reservation_refuses_existing_target_without_change`.
32. Missing, tampered, source-mismatched, unknown-schema, or audit-blocked backup IDs prevent
    migration with the source DB/WAL unchanged.
33. A valid eligible migration records its backup ID, while an injected migration failure rolls back,
    writes only an external attempt report, and leaves the original backup restorable.

## Deferred policy and stop condition

Encryption, off-host replication, retention/deletion schedules, zero-downtime operation, and
production in-place promotion remain MP-21 or deployment-policy work. They do not block the local
protected MP-06 foundation.

Actual conflicting historical duplicate escrows or identity aliases may require an operator-approved
economic reconciliation decision. The backup and audit machinery can be implemented and tested
without silently making that decision.

Task 5.2 is not complete until the implementation and fresh tests prove snapshot preservation,
identity completeness where required, exact fresh-target restore, and fail-closed migration admission.
This design file and the synthetic WAL probe are not completion evidence.
