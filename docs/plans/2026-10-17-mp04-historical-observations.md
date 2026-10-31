# MP-04 historical financial observations

Status: implemented and independently verified for T-116/T-117, with genuine red/green proof and
root19 exit0. See `openspec/changes/complete-agent-economy/evidence/mp04-receipts/completion-audit.md`
for the final requirement mapping and explicit evidence limitations.
This uses the existing v2 marker/cutover tables. It changes no schema, frozen migration or economic
policy. Native receipt semantics remain unchanged.

## Distinguish the new attempt from the historical effect

Exact historical credit and lock collisions return a distinct envelope:

```json
{
  "outcome": "legacy_observation",
  "attempt": {
    "operation_id": "op-00000000-0000-4000-8000-000000000001",
    "operation_kind": "credit",
    "request_sha256": "<hash of this submitted request>"
  },
  "observation": {
    "operation_id": null,
    "observed_kind": "observed_credit",
    "transaction": {
      "tx_id": "tx-historical",
      "account_id": "a-example",
      "type": "credit",
      "amount": 50,
      "balance_after": 50,
      "reference": "genesis",
      "timestamp": "<historical value>",
      "event_id": null
    },
    "observed_fact_sha256": "<stored marker hash>"
  },
  "current_escrow": null
}
```

All illustrated keys are required. A lock uses `observed_kind: observed_escrow_lock`, includes the
original lock transaction with `type: escrow_lock`, and adds these required observation fields:

- `escrow_lock`: exact `escrow_id`, `payer_account_id`, `amount`, `task_id`, `created_at`, `event_id`.
- `observed_status`: the escrow marker's immutable cutover status.
- `lock_fact_sha256`: the stored hash of `escrow_lock`.
- `resolution_fact`: null for a lock unresolved at cutover; otherwise exact `escrow_id`,
  `observed_status`, `resolved_at` and sorted historical release `transaction_ids`.
- `resolution_fact_sha256`: null for unresolved-at-cutover locks, otherwise the stored hash.

Lock `current_escrow` has the existing projection keys (`escrow_id`, payer, task, amount, status,
creation and resolution times) and reflects current state. A later native settlement does not
change the historical observation: an observed `locked` status and null historical resolution
remain unchanged even when the current projection is released or split.

The attempt hash uses the existing canonical request object. Fact hashes use sorted, compact,
UTF-8 JSON with `ensure_ascii=False` and `allow_nan=False`, exactly as the receipt audit does.
No historical request hash exists. Neither attempt identity nor attempt hash is stored, aliased,
or presented as ownership of the old effect. Different attempts can observe the same unchanged
history. Global native operation-ID replay/conflict checks still run before natural-key lookup.

## Branches and no-mutation requirements

| Command and state | Response |
| --- | --- |
| Credit: attested same account/reference/amount | 200 legacy observation |
| Lock: attested same payer/task/amount | 201 legacy observation with original IDs |
| Same legacy natural key, changed amount | 409 `legacy_operation_conflict` |
| Terminal escrow resolved at cutover, new release or split | 409 `historical_settlement_unreplayable` |
| Historical lock settled later by a native command | Native winner conflict or exact native replay |
| Historical lock still locked | A new native settlement is permitted under the existing rules |
| Valid cutover set, but the selected effect has neither native ownership nor a legacy marker | 500 `receipt_ownership_missing` |
| Failed cutover/fact verification, overlapping ownership or unsupported historical storage | 500 `receipt_ownership_failed` |

Historical conflicts have exact safe detail keys:

| Conflict | Exact detail keys |
| --- | --- |
| Changed credit | `winning_operation_id`, `observed_kind`, `tx_id`, `observed_fact_sha256` |
| Changed lock | `winning_operation_id`, `observed_kind`, `tx_id`, `observed_fact_sha256`, `escrow_id`, `observed_status`, `lock_fact_sha256`, `resolution_fact_sha256` |
| Historical terminal settlement | `winning_operation_id`, `escrow_id`, `observed_status`, `lock_fact_sha256`, `resolution_fact_sha256` |

`winning_operation_id` is always null in these historical conflicts. Other values are the actual
safe marker facts, including nullable resolution hash. Do not add recipients, percentage, source
reference, caller operation identity or an original request hash to these detail objects.
Observations and conflicts leave accounts, ledger, escrow, events, receipts, legs, markers and
cutover rows unchanged; candidate IDs prepared by the Bank are discarded.

Deleting an attested marker changes the marker-set aggregate and therefore returns
`receipt_ownership_failed`. An unowned post-cutover effect with the original valid marker set
retains `receipt_ownership_missing`. Neither failure may become an observation or ordinary 409
conflict. Bank and shared clients preserve these safe typed corruption outcomes.

Existing account creation remains 409 `account_exists` for both positive and zero balances. An
`initial_balance` reference is only an observed credit, not proof of historical account issuance.
A generic credit command may observe that credit; it must not relabel it as issuance.

## Verify attestation inside the transaction

Before any historical classification, use the command's existing `BEGIN IMMEDIATE` snapshot to
verify both complete marker sets against the frozen seven-field cutover tuple. Reuse the audit's
fact builders, ordering and hash algorithm. Then verify the selected effect's raw facts, marker
hashes and exclusive native/legacy ownership. Compare requested business terms only after these
checks pass. Per-row hash matching alone does not prove membership in the cutover set.

A narrow verifier belongs alongside the existing receipt audit. It scans historical marker sets,
then checks selected effects through indexed lookups; it does not rerun the whole money/native
receipt audit for each request. Startup retains that full audit. No marker verification is added
to ordinary new operations or native exact replay. Rewriting both a database and its cutover
attestation remains outside the previously documented integrity threat model.

For historical terminal resolution, require all named release transactions to retain their legacy
ownership and no native legs. For a historical lock resolved later, require exactly one native
settlement owner and no historical resolution owner. Both owners or neither is corruption.
Resolution transaction IDs are sorted and unique. A historical `released` escrow has exactly one
positive release transaction; a `split` has one or two, according to its nonzero allocations.

## Preserve admitted historical types

Use separate strict legacy fact/projection models. The frozen schema and migration admit empty
historical timestamp strings and nonpositive event IDs; native constraints must not rewrite them.

- IDs/references are nonempty strict text, as enforced by the existing money audit.
- Historical timestamps and creation times are strict strings, including empty strings.
- Resolution time is a strict string or null, including empty strings. Do not add native
  status/time rules to historical facts that migration did not enforce.
- Event IDs are null or strict SQLite-range integers, including zero and negative integers.
- Money uses existing audited strict integer ranges; type and status use the audited literals.

Unsupported raw storage (such as a BLOB time or text event ID) is a corruption response at this
boundary, even if an older migration attested it. Do not use `str()`/`int()`, substitute times or
IDs, widen wire fields to arbitrary objects, or modify the frozen migration to hide the issue.
Every emitted fact must reproduce its marker hash without normalization.

### New settlements over historical escrows

A native release/split receipt can settle an attested historical lock. Its current projection's
`created_at` is the original lock creation value, not the new settlement time, so it must preserve
an admitted empty historical string. The native receipt's `committed_at` and new resolution time
remain nonempty. A native lock receipt cannot represent historical lock ownership and therefore
still requires a nonempty creation time. Apply this distinction in native semantic validation;
do not fabricate a creation time, add an origin flag, or relax identity/monetary rules.

The earlier isolated SDK negative that rejected empty creation time on every release projection
did not account for this valid mixed historical/native case. Reconcile that case to reject invalid
types/UTF-8, retain the native-lock empty-time rejection, and add real migrated public settlement
proof plus direct native release/split controls before implementing the contextual rule.

## Consumer and proof sequence

1. Add real migrated-v1 Gateway tests for exact/changed credit and lock, terminal settlement,
   missing/altered/inserted markers, dual ownership and unsupported raw storage. Preserve complete
   database snapshots. Include a historical lock followed by native settlement and another lock
   observation, with immutable cutover facts and a fresh terminal projection.
2. Add strict union parser tests for credit/lock only: bind the attempt and historical business
   facts, validate all fact hashes and exact fields, preserve admitted historical values, reject
   forged identities/coercion and reject legacy outcomes for issuance/release/split.
3. Implement the narrow verifier/lookup and writer branches without marker or cutover writes.
4. Propagate validated observations through Bank and SDK/shared/demo credit/lock boundaries.
   Log historical effects explicitly as observations, never newly credited/locked operations.
   Funding commands reject observations as proof of their new operation before success output.
5. Preserve Task Board's requirement for an actually locked native receipt before publishing new
   work. A legacy observation must not trigger task insertion or compensation of old funds.
6. Verify public Bank/Gateway propagation, native regressions, component gates and fresh isolated
   root CI. T-116/T-117 remain open until the whole package's required evidence is complete.
