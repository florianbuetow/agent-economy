# MP-04 receipt contract

Status: concrete contract for T-116 red tests and subsequent T-117 implementation. MP-06 is closed
at root15. No receipt implementation is claimed here. Receipt-only schema v2 precedes transition
schema v3 so financial work remains a bounded package; Q-M4 is now resolved to poster-defined
acceptance windows, but its separate lifecycle implementation/proof is still required.

## Identity and outcomes

Financial operation IDs are caller-owned canonical strings `op-<UUID>` (39 characters, lowercase
canonical UUID spelling). Network methods never allocate a new operation ID during retry. IDs are
global across financial kinds. They are required for credit, lock, release, split and nonzero
account issuance. Ordinary zero-balance account creation keeps its natural account identity and
creates no financial receipt.

New native responses use this explicit envelope:

```json
{
  "outcome": "native_receipt",
  "receipt": {
    "receipt_version": 1,
    "operation_id": "op-00000000-0000-4000-8000-000000000001",
    "operation_kind": "escrow_release",
    "request_sha256": "<64 lowercase hexadecimal characters>",
    "source_reference": "<stable business or lifecycle reference>",
    "committed_at": "<recorded UTC transaction time>",
    "event_id": 12,
    "escrow_id": "esc-example",
    "result": {},
    "legs": []
  },
  "current_escrow": null
}
```

The example illustrates required keys, not a valid zero-leg release. Every successful release has
one positive credit leg. `receipt` is the immutable persisted operation result; exact replay compares
the entire object. `current_escrow` is a separate current projection, required but null for non-escrow
operations. Escrow operations return actual escrow identity, payer, task, amount, status, creation
and resolution times in that projection. A lock receipt records `escrow_status_at_commit: locked`;
replay after settlement retains that receipt while projecting the actual released/split status.
There is no ambiguous top-level `status` and no separate invented `transaction_ids` shortcut.

Each leg has `role`, `account_id`, `direction` (`debit` or `credit`), integer `amount`, nullable
`transaction_id` and nullable integer `balance_after`. Lock has one positive payer debit; issuance
and credit have one account credit; release has one recipient credit; split always has worker and
poster logical credit legs, including zero allocations. Positive legs require matching transaction
facts and a unique transaction FK. Zero split legs have null transaction/balance fields and require
no bank-account existence. Do not directly foreign-key their logical account IDs to bank accounts.

## Replay matrix

| Existing effect | Submitted command | Required result |
| --- | --- | --- |
| None | Valid native operation | Atomic effect, event, receipt and legs |
| Native same operation ID | Same kind and request hash | Exact original receipt, no new effect; fresh current projection |
| Native same operation ID | Changed kind or business request | 409 `operation_id_conflict`, no mutation |
| Native natural credit/lock key | Different operation ID | 409 `financial_operation_already_committed`, true winning ID, no alias/rebinding |
| Attested legacy credit/lock key | Exact observed request | Explicit `legacy_observation`, null operation identity; never native success proof |
| Attested legacy credit/lock key | Changed request | 409 `legacy_operation_conflict`, null winning ID and safe marker facts |
| Attested legacy resolved escrow | New settlement attempt | 409 `historical_settlement_unreplayable`, no movement or invented caller identity |

Native opposing settlements have exactly one immutable winner. A loser returns an explicit conflict
with the winning operation ID; generic already-resolved errors never imply success. The MP-06
no-double-debit guarantee is preserved: native exact replay now includes the same stable operation
ID, while a different ID is explicitly distinguished. Legacy observations are not valid native
receipts for a new lifecycle claim.

## Hash and schema boundary

Hash a canonical versioned object with fixed kind-specific business fields, sorted keys, compact
UTF-8 JSON and no numeric/string coercion. Reject strings that cannot be represented as valid UTF-8.
Exclude generated transaction/event/escrow IDs, transport timestamps, summaries and current
projections. The canonical object always includes `request_version: 1` and `operation_kind`, plus:

| Operation kind | Canonical business fields |
| --- | --- |
| `account_issuance` | `account_id`, `amount` (the initial balance), `reference: "initial_balance"` |
| `credit` | `account_id`, `amount`, `reference` |
| `escrow_lock` | `payer_account_id`, `task_id`, `amount` |
| `escrow_release` | `escrow_id`, `recipient_account_id`, `source_reference` |
| `escrow_split` | `escrow_id`, `worker_account_id`, `poster_account_id`, `worker_pct`, `source_reference` |

Issuance retains the existing fixed initial-credit reference as a protocol value, not a newly
invented funding policy. Credit's receipt source reference is its explicit credit reference; lock's
is its task ID. Release/split callers explicitly provide the stable business/lifecycle reference.
The authoritative split computes exact integer shares from stored escrow amount and strict worker
percentage. The new financial command owns its locked-state precondition; caller-controlled CAS
constraints are not accepted as ignored non-hashed content. Caller propagation and the concrete
schema/migration plan must still be finished before T-117 implementation.

Schema v2 adds native receipts/legs plus immutable observed-fact markers for every pre-v2 monetary
effect and a deterministic marker-set cutover attestation. Historical credit rows remain observed
credits; nullable event IDs/conventional references do not prove whether they were initial issuance.
Never invent old operation IDs or original request hashes. Effects with neither native receipt nor
attested legacy marker fail audit as corruption. Preserve all v1 money, IDs, events and asset/key
references through a separately verified-backup-bound migration; freeze v1 and retain v0-to-v1 proof.

T-116 first proves actual response loss after commit, same-ID conflicting changes that currently
move money again, and the absence of durable receipt identities. Lock replay already preserves
current status under MP-06; a test expecting the whole old locked-status body to remain identical
after settlement is invalid. Compare the immutable receipt and current projection separately.

## Concrete command and consumer boundary

All business strings are nonempty, valid UTF-8, and retain their exact spelling; money and
percentages are strict integers (never booleans, floats or numeric strings). Positive monetary
inputs fit SQLite's signed integer range. Percentages are in the inclusive range 0 through 100.
The fixed hash object includes no operation ID: the global ID selects the persisted command,
while its kind and canonical business object determine whether a retry matches it. Unknown
business fields are rejected, not silently removed before hashing. Authentication and signed
action/resource validation still precede business validation.

The `result` object is kind-specific and immutable:

| Kind | Required result fields |
| --- | --- |
| `account_issuance` | `account_id`, `issued_amount`, `balance_after` |
| `credit` | `account_id`, `amount`, `balance_after`, `reference` |
| `escrow_lock` | `escrow_status_at_commit: locked` |
| `escrow_release` | `escrow_status_at_commit: released` |
| `escrow_split` | `escrow_status_at_commit: split`, `worker_pct` |

Issuance/credit use leg role `account`; lock uses `payer`; release uses `recipient`; split uses
ordered `worker`, then `poster` legs. `event_id` is the actual event joined to all positive legs,
not a caller-supplied or regenerated placeholder. The receipt's `committed_at` is the single
recorded timestamp for the winning command. A canonical request JSON record is retained for
hash/audit verification; it is not a public response field. `operation_id_conflict` details contain
the original `winning_operation_id`. Other native ownership conflicts also expose that true ID.

The Gateway split command now takes `worker_pct` and calculates the two shares from its stored
escrow amount. Caller-provided shares and caller-controlled financial `constraints` are removed
from the new command contract. The writer always owns the locked-state predicate. Generated
transaction/escrow IDs and event presentation metadata may be prepared by the Bank before the
request, but an exact replay returns only the stored winning identities and metadata. Candidate
IDs generated during a retry never become a new effect or replace the stored receipt.

| Caller | Operation identity and response handling |
| --- | --- |
| Bank public routes → storage → Gateway client | Require and pass the signed caller's ID unchanged; return the Gateway's validated native/legacy envelope without reconstructing identities or balances |
| Shared Bank client / platform SDK | Explicit operation ID argument; settlement also requires explicit source reference; never allocate an ID inside a retrying request method |
| Agent direct escrow lock | Explicit caller operation ID; task posting allocates task intent first and derives the lock ID from that stable task identity |
| Treasury provision CLI | Deterministic operation ID from the operator account and explicitly configured genesis reference; reruns reuse it |
| Feeder funding CLI | Required explicit operation ID and reference so an operator can retry the same intent; remove a new random reference on each invocation |
| Task Board settlement callers | Stable identity and source reference from task plus semantic action/ruling; opposing semantic actions have different IDs and must conflict at the Bank |
| Demo and test setup callers | Explicit per-intent identity, reused only for a deliberate replay; retain authorization and monetary assertions |

MP-07 replaces provisional Board action identities with persisted transition claims, and MP-05
makes posting intent durable across caller restarts. Neither capability is claimed by MP-04.
Receipt parsing belongs to typed Bank/Gateway contract boundaries; malformed native responses
are dependency errors, never converted into zero/default balances or generic resolved success.

This is an explicit breaking API reconciliation. Existing tests whose valid requests omit the
new required fields must supply them; old flat response assertions must inspect the documented
receipt/result/current projection. Preserve their underlying money, authority, status and event
assertions. Tests for unrelated invalid inputs must continue to reach the intended validation,
and missing-ID/unknown-field cases receive separate negative coverage. No permissive production
fallback, hidden test-only API or automatic aliasing of a new ID is permitted.

## Provisional caller intent identities

The shared pure helper `service_auth.financial_identity.operation_id_for(purpose, identity_parts)`
derives a canonical `op-UUID` using UUID5 with the standard URL namespace. Its name input is the
fixed protocol prefix `https://agent-economy.invalid/financial-operations/v1/` followed by compact,
sorted-key UTF-8 JSON containing `version: 1`, the explicit `purpose`, and `identity` as the ordered
list of explicit, nonempty, valid UTF-8 identity parts. This is a protocol namespace, not a network
URL or configurable endpoint. It never generates entropy and never grants authority.

Intent owners may call this helper before sending a financial request. Transport clients continue
to require an explicit operation ID and never derive one during a retry. Derived identities exclude
changeable financial terms so changed amounts, recipients or ruling percentages conflict under the
same intent instead of silently becoming a second command.

| Intent owner | Purpose and identity parts | Source reference |
| --- | --- | --- |
| SDK task-posting intent | `task-lock`, `(task_id, poster_id)` | The existing task ID |
| Treasury provisioning | `treasury-genesis`, `(operator_id, configured_genesis_reference)` | The explicit configured genesis reference |
| Task Board approval, including review timeout and pending retry | `task-settlement`, `(task_id, "approved")` | `task:<task_id>:approved` |
| Task Board bidding/execution expiry and pending retry | `task-settlement`, `(task_id, "expired")` | `task:<task_id>:expired` |
| Task Board cancellation | `task-settlement`, `(task_id, "cancelled")` | `task:<task_id>:cancelled` |
| Task Board unpublished-post compensation | `task-settlement`, `(task_id, "posting_compensation")` | `task:<task_id>:posting_compensation` |
| Task Board ruling, including zero/full release and partial split | `task-settlement`, `(task_id, "ruling", ruling_id)` | `task:<task_id>:ruling:<ruling_id>` |

Approval and its existing pending retry deliberately share one provisional financial intent.
MP-07/08 replace this status-derived convention with persisted winning transition claims; the
convention does not prove lifecycle race safety. A ruling requires its actual ruling ID, with no
fallback. Funding CLI callers explicitly provide an operation ID and reference. A new SDK post
still creates its existing task intent once before transmission; durable post-intent recovery remains
MP-05. Task Board accepts a new posting only from a native lock receipt matching its task/payer/reward
and an actually locked current projection; a legacy observation or settled projection is not proof
of a newly funded task.
