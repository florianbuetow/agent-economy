# MP-07 runtime intent test interface

This note fixes the public interface exercised by T-181 before implementation. The proposed module
is `base_agent.runtime_intents`; it has no network client and stores no private key, signed token or
arbitrary upstream body.

## Public types

`RuntimeIntentError(reason: str, message: str)` is the stable local failure type. Tests use these
reasons: `invalid_input`, `unsafe_path`, `unsafe_mode`, `corrupt_record`, `owner_conflict`,
`owner_intent_mismatch`, `slot_conflict`, `operation_conflict`, `stale_revision`, `lease_busy`,
`record_too_large`, and `storage_failure`.

`RuntimeOwner` is a frozen value with these required keyword fields:

- `schema_version: int`, fixed to integer 1;
- `environment_id`, `role`, `profile`, `handle`, `registered_agent_id`, and `key_fingerprint`, each a
  nonempty exact string;
- `service_urls: Mapping[str, str]`, an immutable mapping containing exactly `identity_url`,
  `bank_url`, `task_board_url`, `reputation_url`, and `court_url`. The store performs no URL
  normalization.

`NewTaskIntent` is a frozen pre-publication value with required keyword fields `task_id`, `slot`,
`action`, `source_status`, `target_status`, `operation_id`, `expected_lifecycle_version`,
`public_arguments`, and `acknowledgment_context`. `slot` is exactly `bid`, `acceptance`, `submission`,
or `review-decision`. Public SDK arguments and acknowledgment validation context are separate deep
immutable JSON values.
All models reject booleans in integer fields, noncanonical operation IDs, action/slot/state
contradictions, mismatched IDs or versions inside arguments/context, non-JSON values, NaN/infinity,
and unknown fields. Every nested mapping and sequence remains immutable after construction and load.
An operation ID is unique across every task and slot in one identity journal.

`public_arguments` is the exact callable argument set of the existing SDK. Bid carries task, amount,
operation ID and version; acceptance additionally carries bid ID; submission and approval carry
task, operation ID and version; dispute additionally carries the exact reason. It does not invent a
common decision or amount argument for methods that do not accept one. The separate context holds
the action-specific response facts plus frozen parties, escrow, accepted reward and selected bid
amount where required.
The context also binds the command actor to the immutable owner. Bid and submission require the
worker owner; acceptance, approval and dispute require the poster/feeder owner. Actor mismatch fails
before an intent record is written.
Dispute intent context freezes worker and reason but never guesses the future Court-generated
dispute ID or effect reference. Completion admits a returned nonempty dispute ID only when the
immutable result ID and operation effect reference match exactly.

`FrozenTaskIntent` exposes the same semantic fields plus `schema_version`, `revision`,
`semantic_sha256`, `state`, `result`, and `diagnostics`. Initial state is `pending`, schema and
revision are integer 1, result is null, and diagnostics is an immutable sequence. Its public
arguments, context and operation identity never change across revisions.

## Store interface

`FileTaskIntentStore` is constructed only with explicit keyword arguments:

```python
FileTaskIntentStore(
    root_directory: Path,
    owner: RuntimeOwner,
    diagnostic_limit: int,
    max_record_bytes: int,
)
```

The absolute, canonical `root_directory` is the configured local ownership scope. The store is a
context manager. Entering acquires one exclusive, nonblocking identity lease through the public
`service_commons.maintenance_lock` API and validates or exclusively publishes the owner record.
Leaving releases it. The public inspection paths are `identity_directory`, `owner_path`, `lock_path`,
and `intent_path(task_id, slot)`.

The identity directory is `root_directory / "identities" / key_fingerprint`; it and the intent
directory are mode 0700. `owner.json`, the lease and intent records are regular non-symlink files at
mode 0600. Intent filenames are derived from a canonical hash of task and slot, never raw path text.
Every ancestor is opened without following symlinks and revalidated at publication boundaries.

Methods are:

- `load(task_id: str, slot: str) -> FrozenTaskIntent | None`;
- `create(intent: NewTaskIntent) -> FrozenTaskIntent`, returning the byte-identical existing record
  for an exact repeat and raising `slot_conflict` for changed semantics;
- `load_or_create(task_id: str, slot: str, factory: Callable[[], NewTaskIntent]) ->
  FrozenTaskIntent`, which performs validated stored lookup before invoking `factory`;
- `unfinished() -> tuple[FrozenTaskIntent, ...]`, which strictly scans the owned journal and returns
  `pending`, `blocked`, and `attention_required` intents in deterministic task/slot order while
  excluding `completed` and `definitive_rejection`;
- `record_acknowledgment(task_id: str, slot: str, expected_revision: int, status_code: int,
  body: object) -> FrozenTaskIntent`.

`record_acknowledgment` uses strict non-boolean comparisons against the immutable context and
requires the expected HTTP status. Bid completion is its real flat 201 response: deterministic bid
ID derived from the operation ID, task, bidder, amount and timestamp. Acceptance, submission,
approval and dispute completion require the real 200 task-mutation envelope, including request hash,
source/target, effect, event and immutable result. A matching 202 claimed operation,
malformed/uncertain body or a current target projection without the matching operation stays
`pending`. A foreign success becomes `attention_required`. A typed foreign `transition_pending`
body becomes `blocked` and records both operation IDs. A typed definitive non-application becomes
`definitive_rejection` while retaining the original identity and normalized exact error evidence in
`result`. Only exact documented 409 preclaim shapes qualify: bound `task_version_conflict`,
per-action `invalid_status`, or `cutoff_reached` with empty details. Same-operation
`transition_pending`, malformed errors and 5xx responses remain pending. Exact completed replay is
byte-idempotent. Stale revisions fail with `stale_revision`. This method does not fetch remote state.
The status is supplied by the reviewed typed SDK command response; non-2xx responses retain their
actual status and decoded body through `httpx.HTTPStatusError`. Runtime code must not infer a status
from body shape or call a private raw-request method.

Persisted completion excludes the mutable current-task projection. Bid result stores its status and
validated flat body. Mutation result stores its status and immutable operation envelope only. A
later current view beside the same operation is byte-idempotent, and no later error, pending,
malformed or changed-operation observation can downgrade completion. A matching 202 carries the full
request hash and effect intent and appends `remote_pending`; malformed 202 and typed 5xx stay pending
with `pending_unknown`.
Event IDs must be strict positive SQLite-safe integers in both envelope and result. Lifecycle
versions, bid amounts, accepted rewards and selected bid amounts are strict non-boolean integers no
larger than SQLite's maximum. Nested effect comparisons are type-strict: boolean `true` never equals
integer `1`, and a financial request hash must match the received request bytes.

Every publication uses canonical UTF-8 JSON (`ensure_ascii=False`, `allow_nan=False`) in a private
same-directory temporary file, file fsync, atomic publication, parent-directory fsync and validating
re-read. Initial owner publication is exclusive and never replacing: publish the complete fsynced
temporary inode with a same-directory no-replace hard link, preserve a concurrently winning owner
byte-for-byte and fail closed on mismatch. Intent revisions use atomic replacement. Any filesystem or fsync
failure raises `storage_failure`; callers must not perform mutation transport. If a complete record
became visible before the failure, a later reopen validates and reuses it.
The byte-limit proof uses a fully valid poster-owned dispute intent with one oversized Unicode
reason, matching public arguments, result facts and independently computed native request hash, so
`record_too_large` is the only admission failure.

## Proof boundary

Tests use real temporary files and a spawned second process for lease contention. They prove exact
reopen/replay, private admission, owner/slot conflicts, atomic fault behavior, and stale revision or
corruption observed by an open store. They do not claim detection of an older internally valid
revision restored across process restart without an external trusted anchor. They do not cover task
posting identity, asset/answer checkpoints, remote discovery, oracle provenance, rebuttals, or
receipt-derived earnings.
`unfinished()` is local known-work discovery for startup replay even after tasks leave public open or
submitted lists. It performs no network read and is not broader remote-discovery recovery.
