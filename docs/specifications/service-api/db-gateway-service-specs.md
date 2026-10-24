# Database Gateway Service — API Specification

## MP-08 durable financial-effect recovery amendment

The [MP-08 financial recovery contract](../../plans/2026-10-22-mp08-financial-recovery-contract.md)
defines the current schema, recovery queue and public settlement projection. It
supersedes older statements below that call schema V2 or V4 current, treat the UI
as an authoritative direct-SQLite task reader, or leave release/split recovery
for future work. Court delivery remains MP-10. Aggregate earnings and GDP remain
unchanged; MP-19 owns their redesign.

The executable schema is V5: `SchemaState.CURRENT_V5`, `current_v5.sql`, and the
isolated `task_effect_recovery_v5.sql` extension. Fresh initialization creates an
empty `board_task_effect_delivery_cutover` attestation. An existing exact V4
database may advance only through the explicit `migrate-v4-to-v5` command (or
the matching migration API), under exclusive maintenance and an already
verified backup. The CLI requires an explicit
`--current-task-board-caller-kid`; the API requires the same value as a named
argument. The migration records that backup identity and manifest hash,
the source snapshot, the migration-plan hash, exact native/legacy membership
hashes, and the configured Task Board caller KID. It preserves prior domain rows,
stored facts and `sqlite_sequence`; there is no implicit startup migration.
Partial, unknown, nonexact and future schemas remain fail-closed inputs.

Gateway configuration requires an explicit `board_effect_recovery` section. It
names `current_task_board_caller_kid` and the retry, backoff, lease, evidence and
request-timeout limits. The current KID must be trusted for
`gateway.board.write`. Fresh startup, V4-to-V5 migration and every recovery
audit fail closed if that authority is absent, differs from a recoverable source
owner, or if cutover membership, source identity, delivery coverage, receipt
evidence or completion-event binding is inconsistent.

V5 stores scheduling state in `board_task_effect_deliveries`; immutable native
intent remains in `board_task_operations`, and eligible migrated worker
settlements retain their exact legacy observation. Only native `bank_release`
and `bank_split`, plus unresolved `worker_settlement_pending` and
`worker_settlement_succeeded`, enter this queue. Authenticated Court observations
are preserved but excluded. A delivery never invents a second financial identity
or treats task status, an HTTP acknowledgment, or a competing settlement receipt
as success authority.

All recovery routes require `gateway.board.write`, bind results to the
authenticated caller KID, and accept no money, party, intent or replacement
operation identity:

| Method and path | Contract |
|---|---|
| `POST /board/task-effect-deliveries/lease` | Exact protocol-v1 worker request. Performs one due dead-letter evidence check before leasing at most one due delivery. |
| `POST /board/task-effect-deliveries/{effect_operation_id}/finalize` | Exact `{}` body; native sources only. Returns 200 for completed evidence, 202 when the intended receipt is absent and the nonterminal state is unchanged, 400 for a same-owner legacy source, or 409 for conflicting evidence. |
| `POST /board/task-effect-deliveries/{effect_operation_id}/attempts/{lease_generation}/fail` | Reports one fenced worker attempt. It may reschedule, dead-letter, complete from exact evidence, or integrity-block conflicting evidence. |
| `GET /board/task-effect-deliveries/{effect_operation_id}` | Returns one caller-owned bounded diagnostic. |
| `GET /board/task-effect-deliveries` | Requires exactly one `state`, `after`, and `limit`; returns a bounded operation-ID-ordered diagnostic page. |
| `POST /board/task-effect-deliveries/{effect_operation_id}/retry` | Exact `{}` body; requeues only a dead letter while preserving immutable identity and historical failure diagnostics. |
| `POST /board/legacy-effect-observations/{observation_id}/finalize` | Finalizes only the supported legacy worker evidence rules; it never dispatches a Bank command. |

Authorization and ownership checks precede state/fence checks. A missing,
duplicate or malformed Authorization header is 401; a malformed strict-profile
JWS retains the shared 400 mapping. Missing capability is 403, a capable foreign
owner observes 404, and a same-owner stale
worker/generation/state fence is 409 `effect_delivery_attempt_stale`. Conflicting
financial evidence is 409 `effect_evidence_conflict`; invalid operator state or
exhausted counters is 409 with the corresponding typed code. Unrepresentable
Gateway time or derived schedules return 503 `effect_delivery_clock_exhausted`
with the transaction unchanged. Native source state and sidecar state change in
the same `BEGIN IMMEDIATE` transaction. The older
`POST /board/task-operations/{operation_id}/finalize` envelope is unchanged.

Leases use an inclusive per-delivery attempt budget, exponential configured
backoff, a fresh process worker UUID and lease generation fencing. Before any
dead-letter transition, Gateway performs the exact intended-receipt lookup.
Each later lease also checks one due dead letter for newly arrived evidence,
without a Bank call or attempt-counter change. This makes late authoritative
receipts adoptable without task reads or operator retry and prevents duplicate
financial dispatch from diagnostic evidence.

Queue branching belongs to the pure
`db_gateway_service.effect_delivery_transitions` boundary. Its frozen inputs are
`DeliveryQueueState` and `DeliveryQueuePolicy`. `effective_due_us` selects the
state's due timestamp; the lease, expiry, failure, operator-retry and evidence
planners return `ApplyTransition`, `EvidenceRequired`, or `RejectTransition`.
`EvidenceRequired` carries failure metadata without calculating a future
schedule. Only after receipt absence is proven does `plan_evidence_absent`
calculate the dead-letter schedule; a confirmed receipt can therefore complete
even when that unused future timestamp would overflow.
The financial writer remains responsible for SQL,
transactions, authorization, receipt adoption and applying a returned plan.
This boundary must not query SQLite, authenticate callers or classify financial
evidence. Its focused proof is in `test_effect_delivery_transitions.py` and
`test_mp08_receipt_first_ceiling.py`; the complete release gate remains required.

Gateway task detail and list reads add the required eight-field
`financial_effect` projection: `schema_version`, `settlement_state`,
`receipt_state`, `outcome`, `operation_id`, `receipt_reference`,
`business_projection_pending`, and `pending_target_status`. Confirmed money
requires the exact intended receipt and complete request/hash/escrow/party/
amount/leg/event validation. A competing winner never supplies a public outcome
or receipt reference. Historical `worker_paid` requires the exhaustive,
recomputed pre-receipt audit; terminal status alone maps to unconfirmed
attention, not payment. The Task Board preserves this projection on public task
reads and mutation responses. The UI consumes that Board authority and uses its
read-only SQLite connection only for permitted local enrichment.

## MP-05 publication and protected diagnostics amendment

The [native publication contract](../../plans/2026-10-18-mp05-publication-contract.md)
defines schema V4 admission, funded publication and historical orphan-refund
recovery, superseding older creation shapes below. In migration history, V3
databases require the explicit verified-backup-bound V3-to-V4 migration before
V4 admission; the MP-08 amendment above defines current V5 startup.

`GET /board/legacy-posting-observations/{observation_id}` is a private diagnostic
read requiring `gateway.board.write` and the authenticated caller KID that owns
the observation. It returns HTTP 200 with the exact existing legacy envelope,
including observation state and refund identity, for `refund_pending`, `refunded`
and `blocked_integrity`. Foreign callers receive 403 `operation_caller_mismatch`;
missing observations receive 404 `posting_observation_not_found`.

This read does not reconcile, adopt Bank receipts, update rows or affect an open
transaction. It reports persisted recovery status; a Bank refund can already be
committed while the observation is still pending reconciliation. The separate
POST reconciliation command remains the mutating operation. No public agent,
Task Board or browser route is introduced by this private read.

## MP-07 native Board operation amendment

The [native operation contract](task-board-native-operations.md) defines v3 publication, Board
claim/finalize endpoints, retired generic bid/status writes and atomic Court rebuttal projection.
It supersedes conflicting legacy write/idempotency descriptions below. Caller rollout and full
release verification remain open.

## MP-04 receipt contract amendment

The [2026-10-16 financial receipt contract](../../plans/2026-10-16-mp04-receipt-contract.md)
defines the implemented financial command and response shapes below. The Bank capability supplies
explicit operation IDs; settlement adds a stable source reference. Split supplies `worker_pct`,
and Gateway calculates exact shares from the stored escrow. Caller-provided financial constraints
and split amounts are removed: Gateway owns the locked-state precondition. Receipt, positive
ledger legs, event and money mutation commit atomically. Schema v2 requires its own explicit,
verified-backup-bound migration; v1 and the v0-to-v1 migration remain frozen reference contracts.
Historical observations must cover precisely the attested cutover set. Missing native receipts
are corruption, never automatically classified as history. Implementation and bounded independent
review are complete; root19 exit0 and the final evidence audit close T-116/T-117.

## Purpose

The Database Gateway is the persistence layer for the Agent Task Economy. It owns the shared `economy.db` SQLite database and is the only process that writes to it. Domain services send structured write requests to the gateway, which executes them atomically within SQLite transactions, and read through the gateway's **blessed read API** — one `GET` route per query the rest of the system actually needs, mirroring the write side's one-endpoint-per-operation design. The UI obtains authoritative task and financial projections through the Task Board public API. Its direct read-only SQLite access is limited to local enrichment that cannot replace or recompute those authoritative fields.

Domain services own agent/user authority and lifecycle decisions. The gateway separately
authenticates each calling service and authorizes its persistence capability. It also owns the
implemented financial receipt/replay boundary, exact split calculation, and locked-state settlement
predicate. Nonfinancial writers may use optional compare-and-set `constraints`; see that section.

## Core Principles

- **Service capability admission, not agent authorization.** The gateway validates a locally trusted service JWS and a per-route capability before any handler effect. It does not validate economic-agent credentials or independently decide the task/dispute state machine; those decisions remain with the calling service.
- **Database constraints are the safety net.** Foreign key and unique violations are caught through
  SQLite's structured error codes and returned as structured errors. Financial ownership also uses
  explicit receipt/leg/observation checks in the writer; nonfinancial compare-and-set remains explicit.
- **Every write includes an event.** Every mutating endpoint accepts an `event` object in the request body. The gateway inserts the domain write and the event row in the same transaction. No write exists without its corresponding event — this now includes `DELETE /court/rulings/{claim_id}` and `POST /court/claims/{claim_id}/status`, both of which require `event` in the body (see their endpoint docs below).
- **BEGIN IMMEDIATE, single writer, no `await` mid-transaction.** All write transactions use `BEGIN IMMEDIATE` to acquire the write lock upfront. The gateway keeps exactly one SQLite connection (WAL mode, `busy_timeout=5000ms`) for the whole process, shared by every domain writer and the reader; every write method is a plain (non-`async`) `def` that runs `BEGIN IMMEDIATE` through `COMMIT`/`ROLLBACK` to completion without `await`ing anything in between, so no other coroutine's write (or read) can interleave inside an open transaction on the shared connection. This single-writer/no-await invariant is enforced by a static architecture test (`tests/architecture/test_gap_c5_single_writer_invariant.py` and its domain-writer extension) that fails the build if any write method becomes `async` or an `await` appears in the writer modules. A full ADR covering the rationale lives alongside the rest of the target-architecture documentation sweep; this spec states the operational contract only.
- **Explicit replay ownership.** Financial replay is selected by the global operation ID and bound
  to a canonical business hash; natural-key collisions have separate native and attested historical
  outcomes. Other endpoint uniqueness behavior remains documented per endpoint.
- **Domain-specific endpoints.** The gateway exposes one endpoint per operation (e.g., `/identity/agents`, `/bank/credit`, `GET /board/tasks/{task_id}`), not a generic SQL execution endpoint. This keeps the API auditable, prevents SQL injection, and makes each operation's contract explicit.
- **Blessed read API with least-privilege grants.** Every domain exposes `GET` routes for the queries its owning service needs. Identity, Bank, Task Board, Reputation, and Court receive only their own domain read/write grants. Court additionally receives one narrow `gateway.board.task.read` grant for `GET /board/tasks/{task_id}`; it cannot list tasks, bids, assets, or Board aggregates.
- **Caller constructs events.** The gateway does not derive event payloads from write data. The calling service constructs the full event object (source, type, summary, payload) and passes it in the request. This keeps the gateway free of domain knowledge about what constitutes a meaningful event.
- **Private and authenticated.** The gateway remains bound to the trusted host boundary, but network location is not authority. Every route except exact `GET /health` requires one locally verified service capability token; ordinary agents, UI, and the platform notary have no Gateway grants.

## Service Dependencies

```
Database Gateway (port 8007)
  └── (none) — leaf service, no outbound calls
```

The Database Gateway is a leaf service. It does not call any other service. All other services call the gateway to write **and** read data (the "blessed read API," see below).

```
Identity (8001) ──────────────┐
Central Bank (8002) ──────────┤
Task Board (8003) ────────────┼──→ Database Gateway (8007) ──→ economy.db
Reputation (8004) ────────────┤
Court (8005) ─────────────────┘
```

Each service authenticates with its own Gateway keypair and receives only the grants listed below. Court legitimately reads task context via the singular `GET /board/tasks/{task_id}` exception. The UI has no Gateway capability: it reads authoritative task and `financial_effect` projections from Task Board and uses `economy.db` read-only only for permitted local enrichment.

---

## Service Capability Admission

Exact `GET /health` with no query string is the only public request. All other requests, including other methods on `/health`, unknown routes, reads, writes, and body-bearing `DELETE`, pass bounded body/content validation and then require exactly one `Authorization: Bearer <compact-JWS>` header.

The compact Ed25519 JWS uses the shared strict profile. Its protected header contains exactly seven fields:

```json
{
  "alg": "EdDSA",
  "typ": "JWT",
  "kid": "gateway-identity",
  "iat": 1792065600,
  "exp": 1792065900,
  "aud": "db-gateway",
  "resource": "POST /identity/agents"
}
```

`resource` is the uppercase method plus the exact percent-encoded request path, excluding the query string. The payload contains exactly:

```json
{
  "action": "gateway.identity.write",
  "body_sha256": "<lowercase SHA-256 hex of the exact received body bytes>",
  "query_sha256": "<lowercase SHA-256 hex of the exact raw query bytes>"
}
```

The caller builds the final HTTPX request first, materializes any streaming body, hashes `request.content` and the encoded query bytes, then signs. A request with an existing Authorization header is rejected by the official signer. The Gateway loads trusted public keys only, never calls Identity, rejects a public-key filename whose stem differs from its KID, and fails startup if two trusted KIDs share the same public-key material.

| Caller KID | Capabilities |
|---|---|
| `gateway-identity` | `gateway.identity.read`, `gateway.identity.write` |
| `gateway-central-bank` | `gateway.bank.read`, `gateway.bank.write` |
| `gateway-task-board` | `gateway.board.read`, `gateway.board.write` |
| `gateway-reputation` | `gateway.reputation.read`, `gateway.reputation.write` |
| `gateway-court` | `gateway.court.read`, `gateway.court.write`, `gateway.board.task.read` |

Authorization is driven by an explicit `(method, FastAPI route template) → allowed actions` table. Startup fails if that table has a missing or stale key compared with the registered non-health `APIRoute` inventory. Matching follows FastAPI route order, so static templates such as `/board/tasks/count` and `/board/tasks/count-by-status` are never mistaken for `/board/tasks/{task_id}`. A route registered without an explicit policy fails closed.

Admission mappings are stable: missing, duplicate, or malformed Bearer headers are `401 invalid_authorization`; malformed strict-profile JWS is `400 invalid_jws`; expired/future tokens are `401 token_expired` / `token_not_yet_valid`; unknown KID, invalid signature, and missing capability are `403 forbidden`; audience/resource/action or body/query digest mismatches are `400 invalid_payload`. Content type and body-size rejection occur before credential admission, and rejected requests cause no handler, database, or event effect.

---

## Data Model

The Gateway's current executable schema is
`services/db-gateway/src/db_gateway_service/schema_resources/current_v5.sql`. The frozen V2
receipt extension remains in `receipt_v2.sql`; V3 native operations, V4 publication, and V5
financial-effect recovery each retain their versioned migration resources. `docs/specifications/schema.sql`
remains the broad domain schema reference. The current schema includes:

| Table                   | Domain     | Primary Key    | Description                                |
|-------------------------|------------|----------------|--------------------------------------------|
| `identity_agents`       | Identity   | `agent_id`     | Agent registration records                 |
| `bank_accounts`         | Bank       | `account_id`   | Account balances                           |
| `bank_transactions`     | Bank       | `tx_id`        | Transaction history                        |
| `bank_escrow`           | Bank       | `escrow_id`    | Escrow locks and resolutions               |
| `bank_operation_receipts` | Bank     | `operation_id` | Immutable native financial command receipts |
| `bank_operation_legs`   | Bank       | `(operation_id, leg_index)` | Ordered debit/credit facts for native receipts |
| `bank_legacy_transaction_observations` | Bank | `tx_id` | Attested pre-v2 transaction facts |
| `bank_legacy_escrow_observations` | Bank | `escrow_id` | Attested pre-v2 escrow lock/resolution facts |
| `bank_receipt_cutover`  | Bank       | singleton      | Marker-set and migration-provenance attestation |
| `board_tasks`           | Board      | `task_id`      | Task lifecycle records                     |
| `board_bids`            | Board      | `bid_id`       | Submitted bids                             |
| `board_assets`          | Board      | `asset_id`     | Uploaded deliverable metadata              |
| `board_task_operations` | Board      | `operation_id` | Immutable native lifecycle command and effect intent |
| `board_legacy_effect_observations` | Board | `observation_id` | Preserved authenticated legacy recovery evidence |
| `board_task_effect_delivery_cutover` | Board | singleton | V5 migration provenance and frozen recovery membership |
| `board_task_effect_deliveries` | Board | `effect_operation_id` | Durable financial-effect scheduling and diagnostics sidecar |
| `reputation_feedback`   | Reputation | `feedback_id`  | Bidirectional feedback records             |
| `court_claims`          | Court      | `claim_id`     | Dispute claims                             |
| `court_rebuttals`       | Court      | `rebuttal_id`  | Worker rebuttals                           |
| `court_rulings`         | Court      | `ruling_id`    | Judge panel rulings                        |
| `events`                | Shared     | `event_id`     | Event log (autoincrement, monotonic cursor)|

Refer to `current_v5.sql`, the linked versioned contracts, and the MP-08 recovery contract for
executable financial receipt, operation, publication, recovery, foreign-key, index, and CHECK
constraints. V2 remains migration history rather than the current startup norm.

### Schema initialization and migration admission

MP-06 replaces implicit additive startup migration with explicit version admission. The configured
schema file is mandatory and readable. An empty database is bootstrapped transactionally to the
current schema; an exact current database can start without rewriting its schema. A complete
supported legacy database requires an explicit offline migration with a verified backup. Partial,
unknown and future schemas fail readiness without adding columns or changing existing rows.

The supported legacy baseline includes all five historical dispute/rebuttal/event-ID additions.
Both direct full creation and the known historical base followed by those additions are recognized;
an isolated old `board_tasks` table or two old Bank tables are partial schemas, not migration inputs.
Their previous startup-ALTER behavior is intentionally superseded. Missing metadata or altered
constraint/default/conflict semantics cannot qualify as the current version. Bootstrap failure must
leave no partial application schema, and startup failure must release its maintenance lock.

The offline migration entry point must reject absent, invalid or mismatched backups before opening
the source for writes. Artifact verification alone does not authorize migration. Existing nullable
event IDs remain nullable; migration never invents historical events or silently coerces invalid data.

Exact current-schema admission also runs SQLite integrity and foreign-key checks before changing
persistent journal mode. Violations or unavailable checks fail startup with `database_integrity_failed`;
diagnostics expose check names and issue counts, not raw stored values.

### Authoritative monetary bounds and ownership

Every Bank writer validates amounts again at the persistence boundary. Amounts are integers,
excluding booleans, bounded by SQLite's signed-64-bit maximum `9223372036854775807`. Account
balances and split shares may be zero; credits, initial-credit records and escrow locks must be
positive. Wrong types or signs return 400 `invalid_amount`; an input above the representation
bound returns 400 `amount_out_of_range`. Initial balance must equal its positive issuance record;
zero initial balance requires no credit record. A mismatch returns 400 `payload_mismatch`.

Credit, release and split use conditional additions with `balance <= maximum - amount`, returning
409 `amount_out_of_range` if the account would overflow. Both split legs run in one transaction;
if their recipient is the same account, their combined effect must fit. Every rejected operation
preserves accounts, ledger rows, escrows and events. The split poster must be the stored escrow
payer even when the poster receives zero; an existing unrelated poster returns 400 `payload_mismatch`.
A missing poster account returns 404 `account_not_found` only when the poster leg is positive;
a zero poster leg performs no account-existence check while still requiring the poster ID to equal
the stored payer.

Credit replay is resolved before another addition, so an exact retry can succeed at the maximum
balance. Escrow lock identity is lifetime `(payer_account_id, task_id)` uniqueness, including
released and split escrows. MP-04 adds global caller-owned operation identity and durable receipts
to these natural keys and to settlements; the exact rules are summarized next.

### Financial receipt and historical-observation contract

The authoritative definitions are the
[MP-04 receipt contract](../../plans/2026-10-16-mp04-receipt-contract.md),
[schema v2](../../plans/2026-10-16-mp04-schema-v2.md), and
[historical-observation contract](../../plans/2026-10-17-mp04-historical-observations.md).

- Positive issuance, credit, lock, release, and split require a canonical caller-owned
  `operation_id` (`op-<UUID>`). IDs are global across financial kinds. Zero-balance account
  creation forbids an operation ID and creates no receipt.
- A native success returns `outcome: "native_receipt"`, the immutable exact receipt, and a fresh
  `current_escrow` projection (null for issuance/credit). Same-ID exact replay returns the stored
  transaction, escrow, event, commit time, result, and ordered legs. Candidate IDs supplied for
  effect rows on a retry never replace winning identities.
- Each positive leg has its exact transaction ID and balance snapshot. Split always emits ordered
  worker/poster logical legs. A zero leg has null transaction/balance fields, creates no transaction,
  and does not require that logical account to exist.
- Credit and lock retain natural keys `(account_id, reference)` and `(payer_account_id, task_id)`.
  A different ID cannot alias or rebind an existing native effect. Exact attested pre-v2 credit or
  lock collisions return a separate `legacy_observation` with null historical operation ownership;
  they are never represented as a new native success.
- Release/split require explicit nonempty `source_reference`. Split accepts `worker_pct` in 0..100
  and computes `worker_amount = floor(escrow.amount * worker_pct / 100)` and the poster remainder.
  Caller-supplied split amounts and caller-controlled financial `constraints` are invalid fields;
  the writer owns the locked-state predicate.

The native envelope and its per-kind result/leg rules are defined once in the receipt contract.
The endpoint sections below state the exact submitted fields and identify the applicable receipt
kind. Historical observation fields and safe conflict details are defined in the historical contract.

| Existing effect | Submitted command | Gateway result |
|---|---|---|
| Native receipt with same ID | Same kind and canonical request | Exact receipt replay, fresh current projection, no mutation |
| Native receipt with same ID | Changed kind or business request | 409 `operation_id_conflict`, true winning ID |
| Native credit/lock natural key | Different operation ID | 409 `financial_operation_already_committed`, true winning ID |
| Attested legacy credit/lock key | Exact observed request | `legacy_observation`, null historical operation ID |
| Attested legacy credit/lock key | Changed amount | 409 `legacy_operation_conflict`, null winning ID and safe marker facts |
| Attested legacy terminal escrow | New settlement | 409 `historical_settlement_unreplayable`, no movement or invented identity |

Native opposing settlements have one immutable winner. `escrow_already_resolved` is an error and
never implies success; only exact operation-ID replay yields the original successful receipt.

---

## Event Pairing

Every mutating endpoint accepts an `event` field in the request body. The gateway inserts the event row and the domain write(s) within the same `BEGIN IMMEDIATE` transaction. If either fails, both are rolled back.

**Event structure (passed by caller):**

```json
{
  "event_source": "central-bank",
  "event_type": "escrow.locked",
  "timestamp": "2026-02-28T10:00:00Z",
  "task_id": "t-123",
  "agent_id": "a-poster-uuid",
  "summary": "Alice locked 100 coins for 'Build login page'",
  "payload": "{\"escrow_id\": \"esc-456\", \"amount\": 100, \"title\": \"Build login page\"}"
}
```

| Field          | Type   | Required | Description                                                |
|----------------|--------|----------|------------------------------------------------------------|
| `event_source` | string | yes      | Producer: `identity`, `central-bank`, `board`, `reputation`, `court` |
| `event_type`   | string | yes      | Dot-notation event type (e.g., `task.created`)             |
| `timestamp`    | string | yes      | ISO 8601 UTC timestamp                                     |
| `task_id`      | string | no       | Optional presentation metadata; absent/null or exact task ID        |
| `agent_id`     | string | no       | Primary actor who triggered the event                      |
| `summary`      | string | yes      | Pre-rendered one-liner for feed display                    |
| `payload`      | string | yes      | JSON string blob, shape depends on event type              |

The production Central Bank client emits `central-bank` events. Its release/split events omit
`task_id`; the database stores NULL. Task settlement binds the task through the receipt escrow ID
and the authoritative `bank_escrow.task_id`, along with the frozen request, parties, amount, legs,
transaction linkage and timestamps. A provided event task ID must match that task; a foreign
non-null task ID is conflicting evidence. Event metadata does not replace financial ownership.

The gateway validates that `event_source`, `event_type`, `timestamp`, `summary`, and `payload` are present and non-empty. It does not interpret or validate the contents of `payload` — that is the caller's responsibility.

The `event_id` is auto-assigned by SQLite (`AUTOINCREMENT`). The response includes the assigned `event_id` so the caller can reference it.

---

## Idempotency Rules

Nonfinancial writers retain their existing unique-key behavior. Financial writers use the MP-04
operation receipt contract above; do not infer replay semantics from a UNIQUE index alone.

| Endpoint                             | UNIQUE Constraint                                          | On Duplicate (matching)         | On Duplicate (conflicting)       |
|--------------------------------------|------------------------------------------------------------|---------------------------------|----------------------------------|
| `POST /identity/agents`              | `identity_agents.public_key`                               | **Idempotent** — returns existing agent | 409 `public_key_exists`  |
| `POST /bank/accounts` (zero balance) | `bank_accounts.account_id` (PK)                            | 409 `account_exists`; no operation receipt | 409 `account_exists` |
| `POST /bank/accounts` (positive issuance) | Global receipt ID plus account identity | Same ID/request returns exact native receipt | Changed ID/account identity remains 409; issuance is never a legacy observation |
| `POST /bank/credit`                  | Global receipt ID plus `(account_id, reference)` natural key | Same ID/request returns exact receipt; attested legacy exact key returns observation | Explicit operation/native-owner/legacy conflict |
| `POST /bank/escrow/lock`             | Global receipt ID plus lifetime `(payer_account_id, task_id)` | Same ID/request returns exact receipt with current projection; attested legacy exact key returns observation | Explicit operation/native-owner/legacy conflict |
| `POST /bank/escrow/release` / `split` | Global receipt ID plus one native settlement owner per escrow | Same ID/request returns exact winning receipt | Explicit operation/winner/historical-settlement conflict |
| `POST /board/tasks`                  | `board_tasks.task_id` (PK)                                 | **Not idempotent** — 409 `task_exists` always | 409 `task_exists`     |
| `POST /board/bids`                   | `idx_board_bids_one_per_agent (task_id, bidder_id)`        | **Not idempotent** — 409 `bid_exists` always | 409 `bid_exists`          |
| `POST /board/assets`                 | `board_assets.asset_id` (PK)                               | **Not idempotent** — 409 `asset_exists` always | 409 `asset_exists`      |
| `POST /reputation/feedback`          | `idx_reputation_one_per_direction (task_id, from_agent_id, to_agent_id)` | **Not idempotent** — 409 `feedback_exists` always | 409 `feedback_exists` |
| `POST /court/claims`                 | `court_claims.claim_id` (PK)                               | **Not idempotent** — 409 `claim_exists` always | 409 `claim_exists`       |
| `POST /court/rebuttals`              | `court_rebuttals.rebuttal_id` (PK)                         | **Not idempotent** — 409 `rebuttal_exists` always | 409 `rebuttal_exists` |
| `POST /court/rulings`                | `court_rulings.ruling_id` (PK)                             | **Not idempotent** — 409 `ruling_exists` always | 409 `ruling_exists`     |

The Bank rows are governed by canonical financial business requests rather than comparisons of
generated transport metadata. The nonfinancial idempotent registration row and the other writers
(`board_writer.py`, `court_writer.py`,
`reputation_writer.py`) treat every `sqlite3.IntegrityError` on their UNIQUE constraint the same
way regardless of payload — callers that need replay-safety for task/bid/asset/claim/rebuttal/
ruling/feedback creation must generate their ID once and not resubmit, or treat the `*_exists`
409 as "already done" themselves.

Financial callers generate one stable `operation_id` for the business intent and reuse it for
retry. The operation ID is excluded from the canonical business hash; changing business terms
under it conflicts instead of creating another effect.

`POST /court/claims` has a second UNIQUE-family constraint beyond `claim_id`: `court_claims.task_id` (one claim per task). A `claim_id` collision returns 409 `claim_exists`; a fresh `claim_id` that collides on `task_id` (a second claim filed against an already-disputed task) returns a distinct 409 `task_already_disputed` instead of the misleading `claim_exists` — the gateway distinguishes the two by looking up which row actually collided.

### Idempotent Replay Values

When a native financial replay matches, the gateway returns the exact immutable stored receipt and
its real identities, plus a fresh current escrow projection:

- Receipt `event_id`, `committed_at`, result, and positive leg transaction/balance fields are those
  of the original winning command; current account balances do not rewrite receipt snapshots.
- Lock replay after settlement retains `result.escrow_status_at_commit: "locked"` while
  `current_escrow.status` reports `released` or `split`.
- Historical nullable event IDs appear only inside typed legacy observations. They never become
  native receipt event IDs or invented operation ownership.

### Compare-and-Set Constraints (`constraints`)

Several **nonfinancial** write endpoints accept an optional `constraints` object in the request
body, sibling to `event`. When present, the gateway applies the documented compare-and-set rule.
Financial release/split no longer accept caller-controlled constraints; their writer always owns
and atomically checks the locked-state predicate.

This is genuine compare-and-set state enforcement, not "no business logic": a nonfinancial caller
uses it to express "only apply this update if the row is still in the state I last observed" (for
example, Task Board updating a task only `WHERE status = 'open'`) without a separate read-then-write
round trip that would race against a concurrent writer. Financial settlement does not use this
caller-controlled mechanism; its writer owns the locked-state predicate described above.

Endpoints that accept `constraints`: `POST /board/bids` (checked against `board_tasks`),
`POST /board/tasks/{task_id}/status`, `POST /board/assets` (checked against `board_tasks`),
`POST /court/claims/{claim_id}/status`, and `POST /court/rebuttals` (checked against
`court_claims` when `claim_status_update` is present, or as a bare cross-table check otherwise).

---

## Read API

All read routes are `GET`, take no request body, require the route's explicit service capability, and return `200` with a JSON body (or a domain-specific `_not_found` 404 for single-resource lookups). None mutate state.

| Method | Path | Domain | Returns |
|---|---|---|---|
| GET | `/identity/agents/count` | Identity | `{"count": int}` |
| GET | `/identity/agents` | Identity | `{"agents": [...]}` — optional `?public_key=` filter |
| GET | `/identity/agents/{agent_id}` | Identity | Single agent row, or 404 `agent_not_found` |
| GET | `/bank/accounts/count` | Bank | `{"count": int}` |
| GET | `/bank/accounts/{account_id}` | Bank | Single account row, or 404 `account_not_found` |
| GET | `/bank/accounts/{account_id}/transactions` | Bank | `{"transactions": [...]}` |
| GET | `/bank/escrow/total-locked` | Bank | `{"total": int}` |
| GET | `/bank/escrow/{escrow_id}` | Bank | Single escrow row, or 404 `escrow_not_found` |
| GET | `/board/tasks/count` | Board | `{"count": int}` |
| GET | `/board/tasks/count-by-status` | Board | `{"<status>": int, ...}` |
| GET | `/board/tasks` | Board | `{"tasks": [...]}` — optional `?status=&poster_id=&worker_id=&limit=&offset=` |
| GET | `/board/tasks/{task_id}` | Board | Single task row, or 404 `task_not_found` |
| GET | `/board/tasks/{task_id}/bids` | Board | `{"bids": [...]}` |
| GET | `/board/tasks/{task_id}/assets/count` | Board | `{"count": int}` |
| GET | `/board/tasks/{task_id}/assets` | Board | `{"assets": [...]}` |
| GET | `/board/bids/{bid_id}` | Board | Single bid row (requires `?task_id=`), or 404 `bid_not_found` |
| GET | `/board/assets/{asset_id}` | Board | Single asset row (requires `?task_id=`), or 404 `asset_not_found` |
| GET | `/reputation/feedback/count` | Reputation | `{"count": int}` |
| GET | `/reputation/feedback/{feedback_id}` | Reputation | Single feedback row, or 404 `feedback_not_found` |
| GET | `/reputation/feedback` | Reputation | `{"feedback": [...]}` via `?task_id=` or `?agent_id=` (empty list if neither given) |
| GET | `/court/claims/count` | Court | `{"count": int}` |
| GET | `/court/claims/count-active` | Court | `{"count": int}` — unresolved claims |
| GET | `/court/claims` | Court | `{"claims": [...]}` — optional `?status=&claimant_id=` |
| GET | `/court/claims/{claim_id}` | Court | Single claim row, or 404 `claim_not_found` |
| GET | `/court/claims/{claim_id}/rebuttal` | Court | Single rebuttal row, or 404 `rebuttal_not_found` |
| GET | `/court/rulings/{claim_id}` | Court | Single ruling row, or 404 `ruling_not_found` |

All read routes return `503 service_not_ready` if the reader isn't initialized yet (a startup-ordering condition). `GET /board/tasks`, `GET /court/claims`, and `GET /reputation/feedback` (the three list-with-filters routes) previously returned `405 method_not_allowed` instead in that same not-yet-initialized state — a copy-paste inconsistency in the router code, fixed in WP-13 (each now raises the same `service_not_ready`/503 branch as every other reader route; see `services/db-gateway/tests/unit/routers/test_reader_not_ready_503.py`). A few routes separately special-case a literal path segment that collides with a sibling parameterized route (e.g. `GET /bank/escrow/{escrow_id}` rejects `lock`/`release`/`split` as an id) and return `405 method_not_allowed` for that unrelated reason.

---

## Endpoints

### GET /health

Service health check. No authentication required.

**Response (200 OK):**
```json
{
  "status": "ok",
  "uptime_seconds": 3621,
  "started_at": "2026-02-28T08:00:00Z",
  "database_size_bytes": 2097152,
  "total_events": 847
}
```

| Field                | Type    | Description                                      |
|----------------------|---------|--------------------------------------------------|
| `status`             | string  | Always `"ok"`                                    |
| `uptime_seconds`     | float   | Seconds since service started                    |
| `started_at`         | string  | ISO 8601 timestamp of service start              |
| `database_size_bytes`| integer | Size of `economy.db` on disk, **including** the WAL-mode sidecar files (`economy.db-wal`, `economy.db-shm`) when present. In WAL mode recently committed data lives in `-wal` until the next checkpoint, so summing only the main file would undercount the real footprint. |
| `total_events`       | integer | Total rows in the `events` table                 |

**Errors:**

| Status | Code                   | Description                                          |
|--------|------------------------|-------------------------------------------------------|
| 503    | `database_unavailable` | The database file could not be stat'd (`OSError`)     |

---

### POST /identity/agents

Register a new agent. Inserts into `identity_agents` and logs an event.

**Request:**
```json
{
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "name": "Alice",
  "public_key": "ed25519:<base64>",
  "registered_at": "2026-02-28T10:00:00Z",
  "event": {
    "event_source": "identity",
    "event_type": "agent.registered",
    "timestamp": "2026-02-28T10:00:00Z",
    "task_id": null,
    "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
    "summary": "Alice registered as a new agent",
    "payload": "{\"agent_name\": \"Alice\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO identity_agents (agent_id, name, public_key, registered_at)
  VALUES (:agent_id, :name, :public_key, :registered_at);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 1
}
```

**Errors:**

| Status | Code                | Description                                      |
|--------|---------------------|--------------------------------------------------|
| 400    | `missing_field`     | Required field missing or empty                  |
| 409    | `public_key_exists`  | Public key already registered (UNIQUE violation) |

**Idempotency:** If `public_key` matches an existing row and all other fields match, returns the existing agent. If `public_key` matches but other fields differ, returns 409.

---

### POST /bank/accounts

Create a bank account with an optional initial credit. Inserts into `bank_accounts`, optionally inserts a credit transaction, and logs an event.

**Request:**
```json
{
  "operation_id": "op-00000000-0000-4000-8000-000000000001",
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "balance": 50,
  "created_at": "2026-02-28T10:00:00Z",
  "initial_credit": {
    "tx_id": "tx-credit-uuid",
    "amount": 50,
    "reference": "initial_balance",
    "timestamp": "2026-02-28T10:00:00Z"
  },
  "event": {
    "event_source": "central-bank",
    "event_type": "account.created",
    "timestamp": "2026-02-28T10:00:00Z",
    "task_id": null,
    "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
    "summary": "Account created for Alice with 50 coins",
    "payload": "{\"agent_name\": \"Alice\"}"
  }
}
```

If `balance` is zero, omit both `initial_credit` and `operation_id`. The response is the natural
account result `{"account_id": ..., "event_id": ...}`; the Bank then reads and returns the current
account projection. If `balance` is positive, both fields are required: `operation_id` identifies
the issuance command and `initial_credit` supplies candidate effect metadata. The immutable
business request fixes reference `initial_balance`; candidate transaction/event values never alter
an exact replay's stored identities.

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO bank_accounts (account_id, balance, created_at)
  VALUES (:account_id, :balance, :created_at);
-- Only if initial_credit is provided:
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:tx_id, :account_id, 'credit', :amount, :balance, :reference, :timestamp);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created, zero balance):**
```json
{
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 2
}
```

**Response (201 Created, positive issuance):** Native receipt envelope with
`operation_kind: "account_issuance"`, result fields `account_id`, `issued_amount`, and
`balance_after`, one positive `account` credit leg, and null `current_escrow`, as defined by the
MP-04 receipt contract.

**Errors:**

| Status | Code                | Description                                              |
|--------|---------------------|----------------------------------------------------------|
| 400    | `missing_field`     | Required field missing or empty                          |
| 400    | `invalid_amount`    | `balance` is negative, or `initial_credit.amount` <= 0   |
| 400    | `invalid_operation_id` | Operation ID is noncanonical, or zero creation carries one |
| 409    | `operation_id_conflict` | Same operation ID carries changed kind or canonical business terms |
| 409    | `financial_operation_already_committed` | Existing native issuance belongs to another operation ID |
| 409    | `account_exists`    | Account already exists for this agent (PK violation)     |
| 409    | `foreign_key_violation` | `account_id` does not reference a valid `identity_agents.agent_id` |

**Replay:** Zero-balance creation has no financial operation identity and any account collision is
409 `account_exists`. Positive issuance supports exact same-ID native receipt replay. A different
ID for existing native issuance returns `financial_operation_already_committed`; a historical
`initial_balance` credit is never relabeled as issuance, and other account collisions remain
`account_exists`.

---

### POST /bank/credit

Credit an account. Inserts a credit transaction, updates the account balance, and logs an event.

**Request:**
```json
{
  "operation_id": "op-00000000-0000-4000-8000-000000000002",
  "tx_id": "tx-550e8400-e29b-41d4-a716-446655440000",
  "account_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "amount": 10,
  "reference": "salary_round_3",
  "timestamp": "2026-02-28T10:05:00Z",
  "event": {
    "event_source": "central-bank",
    "event_type": "salary.paid",
    "timestamp": "2026-02-28T10:05:00Z",
    "task_id": null,
    "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
    "summary": "Alice received 10 coins (salary_round_3)",
    "payload": "{\"amount\": 10}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
UPDATE bank_accounts SET balance = balance + :amount
  WHERE account_id = :account_id AND balance <= :maximum - :amount;
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:tx_id, :account_id, 'credit', :amount,
          (SELECT balance FROM bank_accounts WHERE account_id = :account_id),
          :reference, :timestamp);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (200 OK):** Native credit receipt envelope, or a typed exact historical credit
observation. The native receipt result contains `account_id`, `amount`, `balance_after`, and
`reference`; it has one positive `account` credit leg and null `current_escrow`.

**Errors:**

| Status | Code                | Description                                              |
|--------|---------------------|----------------------------------------------------------|
| 400    | `missing_field`     | Required field missing or empty                          |
| 400    | `invalid_amount`    | `amount` is not a positive integer                       |
| 400    | `invalid_operation_id` | Operation ID is noncanonical                           |
| 404    | `account_not_found` | No account with this `account_id`                        |
| 409    | `operation_id_conflict` | Same ID carries changed kind or canonical request |
| 409    | `financial_operation_already_committed` | Native natural key belongs to another operation ID |
| 409    | `legacy_operation_conflict` | Attested historical credit has a different amount |
| 500    | `receipt_ownership_missing` / `receipt_ownership_failed` | Existing effect has missing, invalid, or ambiguous ownership |

**Replay:** `(account_id, reference)` remains the natural key, while `operation_id` owns native
replay. Same ID/request returns the exact stored receipt. A different ID for a native key conflicts;
an attested legacy key yields an observation only when its amount matches.

---

### POST /bank/escrow/lock

Lock funds in escrow. Debits the payer's account, creates an escrow record, logs an escrow_lock transaction, and logs an event.

**Request:**
```json
{
  "operation_id": "op-00000000-0000-4000-8000-000000000003",
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "payer_account_id": "a-poster-uuid",
  "amount": 100,
  "task_id": "t-123",
  "created_at": "2026-02-28T10:10:00Z",
  "tx_id": "tx-escrow-lock-uuid",
  "event": {
    "event_source": "central-bank",
    "event_type": "escrow.locked",
    "timestamp": "2026-02-28T10:10:00Z",
    "task_id": "t-123",
    "agent_id": "a-poster-uuid",
    "summary": "Alice locked 100 coins for 'Build login page'",
    "payload": "{\"escrow_id\": \"esc-550e8400\", \"amount\": 100, \"title\": \"Build login page\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
UPDATE bank_accounts SET balance = balance - :amount
  WHERE account_id = :payer_account_id AND balance >= :amount;
-- If no row updated: ROLLBACK, return 402 insufficient_funds
INSERT INTO bank_escrow (escrow_id, payer_account_id, amount, task_id, status, created_at)
  VALUES (:escrow_id, :payer_account_id, :amount, :task_id, 'locked', :created_at);
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:tx_id, :payer_account_id, 'escrow_lock', :amount,
          (SELECT balance FROM bank_accounts WHERE account_id = :payer_account_id),
          :task_id, :created_at);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

The `UPDATE ... WHERE balance >= :amount` pattern enforces sufficient funds at the database level. If no row is updated (balance < amount), the gateway rolls back and returns 402. This is a database-level check, not business logic.

**Response (201 Created):** Native lock receipt envelope, or a typed exact historical lock
observation. The native result is `{"escrow_status_at_commit":"locked"}` with one positive payer
debit leg and the current escrow projection. Exact replay after settlement retains the immutable
locked-at-commit result while the projection reports the actual terminal status.

**Errors:**

| Status | Code                    | Description                                              |
|--------|-------------------------|----------------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                          |
| 400    | `invalid_amount`        | `amount` is not a positive integer                       |
| 400    | `invalid_operation_id`  | Operation ID is noncanonical                             |
| 402    | `insufficient_funds`    | Account balance is less than the escrow amount           |
| 404    | `account_not_found`     | No account for `payer_account_id`                        |
| 409    | `operation_id_conflict` | Same ID carries changed kind or canonical request |
| 409    | `financial_operation_already_committed` | Native payer/task lock belongs to another operation ID |
| 409    | `legacy_operation_conflict` | Attested historical lock has a different amount |
| 409    | `foreign_key_violation` | Foreign key constraint failed                            |
| 500    | `receipt_ownership_missing` / `receipt_ownership_failed` | Existing lock has missing, invalid, or ambiguous receipt/observation ownership |

**Replay:** The lifetime payer/task key remains unique, but `operation_id` owns native replay.
Same ID/request returns the exact receipt and fresh projection. A different ID for a native lock
conflicts even if amount matches. An attested legacy exact lock returns an observation; changed
amount returns `legacy_operation_conflict`.

---

### POST /bank/escrow/release

For a new release or split, the writer binds the event's `task_id` to the
authoritative escrow row within the settlement transaction. An omitted/null
event task ID is filled from that row; a supplied different task ID returns
400 `payload_mismatch` before money, receipt or event changes. Exact replay
returns the original committed receipt without rewriting historical events.

Release escrowed funds in full to a recipient. Credits the recipient's account, resolves the escrow,
logs the transaction/event, and persists the receipt and leg atomically. The writer owns the
locked-state predicate; caller-controlled `constraints` are rejected.

**Request:**
```json
{
  "operation_id": "op-00000000-0000-4000-8000-000000000004",
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "recipient_account_id": "a-worker-uuid",
  "source_reference": "task:t-123:approved",
  "tx_id": "tx-release-uuid",
  "resolved_at": "2026-02-28T11:00:00Z",
  "event": {
    "event_source": "central-bank",
    "event_type": "escrow.released",
    "timestamp": "2026-02-28T11:00:00Z",
    "agent_id": "a-worker-uuid",
    "summary": "Bob received 100 coins from escrow release",
    "payload": "{\"escrow_id\": \"esc-550e8400\", \"amount\": 100, \"recipient_id\": \"a-worker-uuid\", \"recipient_name\": \"Bob\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
-- Load escrow and verify status
SELECT escrow_id, amount, status FROM bank_escrow WHERE escrow_id = :escrow_id;
-- If not found: ROLLBACK, return 404 escrow_not_found
-- Require status = 'locked' under the writer-owned settlement predicate.
UPDATE bank_accounts SET balance = balance + :amount
  WHERE account_id = :recipient_account_id AND balance <= :maximum - :amount;
-- If no row updated: ROLLBACK; missing recipient => 404 account_not_found;
-- existing recipient whose balance cannot fit the credit => 409 amount_out_of_range.
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:tx_id, :recipient_account_id, 'escrow_release', :amount,
          (SELECT balance FROM bank_accounts WHERE account_id = :recipient_account_id),
          :escrow_id, :resolved_at);
UPDATE bank_escrow SET status = 'released', resolved_at = :resolved_at WHERE escrow_id = :escrow_id;
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
-- Persist the escrow_release receipt and its positive recipient leg, bound to event/tx/escrow.
COMMIT;
```

**Response (200 OK):** Native release receipt envelope. Its result is
`{"escrow_status_at_commit":"released"}`; its one positive `recipient` leg contains the full
stored escrow amount and exact transaction/balance; `current_escrow` is the released projection.

**Errors:**

| Status | Code                      | Description                                    |
|--------|---------------------------|------------------------------------------------|
| 400    | `missing_field`           | Required field missing or empty                |
| 400    | `invalid_operation_id`    | Operation ID is noncanonical                   |
| 400    | `unknown_field`           | Request contains `constraints` or another unsupported field |
| 404    | `escrow_not_found`        | No escrow with this ID                         |
| 404    | `account_not_found`       | Recipient account not found                    |
| 409    | `amount_out_of_range`     | Credit would exceed the recipient balance limit |
| 409    | `operation_id_conflict`   | Same ID carries changed kind or canonical request |
| 409    | `financial_operation_already_committed` | Another native settlement owns this escrow; details include its winning ID |
| 409    | `historical_settlement_unreplayable` | Historical terminal settlement has no replayable operation ID |
| 409    | `escrow_state_conflict`   | Escrow lost the writer-owned locked-state race |
| 500    | `receipt_ownership_missing` / `receipt_ownership_failed` | Existing settlement ownership is missing, invalid, or ambiguous |

Exact same-ID replay returns the original receipt before another movement. A different or opposing
settlement does not turn `escrow_already_resolved` (or any generic resolved-state error) into
success; it must receive the applicable explicit winner, historical, or ownership error.

---

### POST /bank/escrow/split

Split escrowed funds between worker and poster. The Gateway computes both shares from the stored
escrow amount and `worker_pct`, applies positive credits, resolves the escrow, and persists the
event, receipt, and ordered legs atomically. Caller amounts and financial `constraints` are rejected.

**Request:**
```json
{
  "operation_id": "op-00000000-0000-4000-8000-000000000005",
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "worker_account_id": "a-worker-uuid",
  "poster_account_id": "a-poster-uuid",
  "worker_pct": 40,
  "source_reference": "task:t-123:ruling:r-456",
  "worker_tx_id": "tx-split-worker-uuid",
  "poster_tx_id": "tx-split-poster-uuid",
  "resolved_at": "2026-02-28T12:00:00Z",
  "event": {
    "event_source": "central-bank",
    "event_type": "escrow.split",
    "timestamp": "2026-02-28T12:00:00Z",
    "agent_id": "a-poster-uuid",
    "summary": "Escrow split: worker 40, poster 60",
    "payload": "{\"escrow_id\": \"esc-550e8400\", \"worker_pct\": 40}"
  }
}
```

The Gateway computes `worker_amount = floor(escrow.amount * worker_pct / 100)` and
`poster_amount = escrow.amount - worker_amount`. The poster logical account ID must equal the
stored payer. Each positive leg requires its account and creates a transaction. A zero leg creates
no transaction, performs no account-existence check, and returns null transaction/balance fields.
Overflow of either positive credit rolls back both legs, the escrow update, event, and receipt.

**Transaction:**
```sql
BEGIN IMMEDIATE;
-- Load escrow and verify
SELECT escrow_id, amount, status FROM bank_escrow WHERE escrow_id = :escrow_id;
-- If not found: ROLLBACK, return 404 escrow_not_found
-- Require status = 'locked' under the writer-owned settlement predicate.
-- Compute worker/poster amounts from stored amount and :worker_pct.
-- Credit worker (if worker_amount > 0):
UPDATE bank_accounts SET balance = balance + :worker_amount
  WHERE account_id = :worker_account_id AND balance <= :maximum - :worker_amount;
-- If no row updated: ROLLBACK; missing worker => 404 account_not_found;
-- existing worker whose credit cannot fit => 409 amount_out_of_range.
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:worker_tx_id, :worker_account_id, 'escrow_release', :worker_amount,
          (SELECT balance FROM bank_accounts WHERE account_id = :worker_account_id),
          :escrow_id, :resolved_at);
-- Credit poster (if poster_amount > 0):
UPDATE bank_accounts SET balance = balance + :poster_amount
  WHERE account_id = :poster_account_id AND balance <= :maximum - :poster_amount;
-- If no row updated: ROLLBACK both legs; missing poster => 404 account_not_found;
-- existing poster whose credit cannot fit => 409 amount_out_of_range.
INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp)
  VALUES (:poster_tx_id, :poster_account_id, 'escrow_release', :poster_amount,
          (SELECT balance FROM bank_accounts WHERE account_id = :poster_account_id),
          :escrow_id, :resolved_at);
UPDATE bank_escrow SET status = 'split', resolved_at = :resolved_at WHERE escrow_id = :escrow_id;
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
-- Persist escrow_split receipt and ordered worker/poster legs; zero legs carry null tx/balance.
COMMIT;
```

**Response (200 OK):** Native split receipt envelope. Its result is
`{"escrow_status_at_commit":"split","worker_pct":40}`; ordered worker/poster legs expose the
computed amounts and exact positive-leg identities; `current_escrow` is the split projection.

**Errors:**

| Status | Code                      | Description                                          |
|--------|---------------------------|------------------------------------------------------|
| 400    | `missing_field`           | Required field missing or empty                      |
| 400    | `invalid_percentage`      | `worker_pct` is not an integer in 0..100             |
| 400    | `invalid_operation_id`    | Operation ID is noncanonical                         |
| 400    | `payload_mismatch`        | Existing poster account is not the escrow payer, including a zero share |
| 400    | `unknown_field`           | Request contains caller amounts, `constraints`, or another unsupported field |
| 404    | `escrow_not_found`        | No escrow with this ID                               |
| 404    | `account_not_found`       | Account for a positive worker/poster leg not found   |
| 409    | `amount_out_of_range`     | Worker/poster credit would exceed a recipient balance limit, including combined same-account legs |
| 409    | `operation_id_conflict`   | Same ID carries changed kind or canonical request |
| 409    | `financial_operation_already_committed` | Another native settlement owns this escrow; details include its winning ID |
| 409    | `historical_settlement_unreplayable` | Historical terminal settlement has no replayable operation ID |
| 409    | `escrow_state_conflict`   | Escrow lost the writer-owned locked-state race |
| 500    | `receipt_ownership_missing` / `receipt_ownership_failed` | Existing settlement ownership is missing, invalid, or ambiguous |

---

### POST /board/tasks

Create a new task. Inserts into `board_tasks` and logs an event.

**Request:**
```json
{
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "poster_id": "a-poster-uuid",
  "title": "Build login page",
  "spec": "Create a responsive login page with email/password fields...",
  "reward": 100,
  "status": "open",
  "bidding_deadline_seconds": 3600,
  "deadline_seconds": 86400,
  "review_deadline_seconds": 7200,
  "bidding_deadline": "2026-02-28T11:00:00Z",
  "escrow_id": "esc-550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-02-28T10:00:00Z",
  "event": {
    "event_source": "board",
    "event_type": "task.created",
    "timestamp": "2026-02-28T10:00:00Z",
    "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
    "agent_id": "a-poster-uuid",
    "summary": "Alice posted 'Build login page' for 100 coins",
    "payload": "{\"title\": \"Build login page\", \"reward\": 100, \"bidding_deadline\": \"2026-02-28T11:00:00Z\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO board_tasks (
  task_id, poster_id, title, spec, reward, status,
  bidding_deadline_seconds, deadline_seconds, review_deadline_seconds,
  bidding_deadline, escrow_id, created_at
) VALUES (
  :task_id, :poster_id, :title, :spec, :reward, :status,
  :bidding_deadline_seconds, :deadline_seconds, :review_deadline_seconds,
  :bidding_deadline, :escrow_id, :created_at
);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 7
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                |
| 400    | `invalid_amount`        | `reward` is not a positive integer             |
| 409    | `task_exists`           | Task with this `task_id` already exists        |
| 409    | `foreign_key_violation` | `poster_id` or `escrow_id` reference invalid   |

---

### POST /board/bids

Submit a bid on a task. Inserts into `board_bids`, increments `board_tasks.bid_count`, and logs an event — all in the **same** `BEGIN IMMEDIATE` transaction. `bid_count` is a materialized counter updated at write time (matching how every other `board_tasks` field works), not derived on read; a rejected duplicate bid rolls the whole transaction back, so it never reaches the increment. Accepts an optional `constraints` object (see "Compare-and-Set Constraints" above), checked against the referenced `board_tasks` row (e.g. `{"status": "open"}` to reject bids submitted after a task leaves `open`).

**Request:**
```json
{
  "bid_id": "bid-550e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-123",
  "bidder_id": "a-worker-uuid",
  "proposal": "I will build a responsive login page using React...",
  "amount": 55,
  "submitted_at": "2026-02-28T10:30:00Z",
  "event": {
    "event_source": "board",
    "event_type": "bid.submitted",
    "timestamp": "2026-02-28T10:30:00Z",
    "task_id": "t-123",
    "agent_id": "a-worker-uuid",
    "summary": "Bob bid on 'Build login page'",
    "payload": "{\"bid_id\": \"bid-550e8400\", \"title\": \"Build login page\", \"bid_count\": 3}"
  }
}
```

`amount` is the bid's competitive price signal — it is a positive integer if provided, and defaults to `0` if omitted entirely (payout at settlement is always the full posted `reward`, regardless of the winning bid's `amount`; see the target-architecture plan's economic-model section for the open question on whether the winning bid becomes the actual payment).

**Transaction:**
```sql
BEGIN IMMEDIATE;
-- Only if constraints is provided: verify board_tasks matches {task_id, **constraints}
INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, amount, submitted_at)
  VALUES (:bid_id, :task_id, :bidder_id, :proposal, :amount, :submitted_at);
UPDATE board_tasks SET bid_count = bid_count + 1 WHERE task_id = :task_id;
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "bid_id": "bid-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 8
}
```

**Errors:**

| Status | Code                    | Description                                           |
|--------|-------------------------|-------------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                       |
| 400    | `invalid_amount`        | `amount` is provided but not a positive integer        |
| 400    | `invalid_constraints`   | `constraints` is present but not an object, or a constraint column is not a valid identifier |
| 409    | `bid_exists`            | This agent already bid on this task (UNIQUE violation)|
| 409    | `foreign_key_violation` | `task_id` or `bidder_id` reference invalid            |
| 409    | `constraint_violation`  | `constraints` was supplied and the referenced task did not match |

---

### POST /board/tasks/{task_id}/status

Update a task's status and associated fields. This is a generic endpoint for all task lifecycle transitions (accepted, submitted, approved, cancelled, disputed, ruled, expired). The gateway does not validate status transitions — the calling service is responsible for enforcing the state machine. Accepts an optional `constraints` object (see "Compare-and-Set Constraints" above), e.g. `{"status": "open"}` so a concurrent second `accept` on the same task fails instead of silently overwriting the first.

**Request:**
```json
{
  "updates": {
    "status": "accepted",
    "worker_id": "a-worker-uuid",
    "accepted_bid_id": "bid-550e8400",
    "accepted_at": "2026-02-28T11:00:00Z",
    "execution_deadline": "2026-03-01T11:00:00Z"
  },
  "event": {
    "event_source": "board",
    "event_type": "task.accepted",
    "timestamp": "2026-02-28T11:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-poster-uuid",
    "summary": "Alice accepted Bob's bid on 'Build login page'",
    "payload": "{\"title\": \"Build login page\", \"worker_id\": \"a-worker-uuid\", \"worker_name\": \"Bob\", \"bid_id\": \"bid-550e8400\"}"
  }
}
```

The `updates` object contains key-value pairs that map directly to columns in `board_tasks`. Only the columns listed in `updates` are modified — all other columns retain their current values. The `task_id` comes from the URL path, not the request body.

**Allowed update columns:**

| Column               | Type    | Set during transition to...                    |
|----------------------|---------|------------------------------------------------|
| `status`             | string  | Any transition                                 |
| `worker_id`          | string  | `accepted`                                     |
| `accepted_bid_id`    | string  | `accepted`                                     |
| `accepted_at`        | string  | `accepted`                                     |
| `execution_deadline` | string  | `accepted`                                     |
| `submitted_at`       | string  | `submitted`                                    |
| `review_deadline`    | string  | `submitted`                                    |
| `approved_at`        | string  | `approved`                                     |
| `cancelled_at`       | string  | `cancelled`                                    |
| `dispute_reason`     | string  | `disputed`                                     |
| `disputed_at`        | string  | `disputed`                                     |
| `ruling_id`          | string  | `ruled`                                        |
| `worker_pct`         | integer | `ruled`                                        |
| `ruling_summary`     | string  | `ruled`                                        |
| `ruled_at`           | string  | `ruled`                                        |
| `expired_at`         | string  | `expired`                                      |

**Transaction:**
```sql
BEGIN IMMEDIATE;
UPDATE board_tasks SET status = :status, worker_id = :worker_id, ...
  WHERE task_id = :task_id;
-- If no row updated: ROLLBACK, return 404 task_not_found
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

The gateway constructs the `SET` clause dynamically from the keys in `updates`. Only columns listed in the allowed set above are accepted — unknown columns are rejected with `invalid_field`.

**Response (200 OK):**
```json
{
  "task_id": "t-123",
  "status": "accepted",
  "event_id": 9
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | `updates` or `event` missing                   |
| 400    | `invalid_field`         | `updates` contains an unknown column           |
| 400    | `empty_updates`         | `updates` object is empty                      |
| 400    | `invalid_constraints`   | `constraints` is present but not an object, or a constraint column is not a valid identifier |
| 404    | `task_not_found`        | No task with this `task_id` (constraints omitted, or the row is genuinely absent) |
| 404    | `not_found`             | Same 404 case surfaced from the constrained-lookup path when `constraints` was supplied and the row does not exist at all |
| 409    | `constraint_violation`  | `constraints` was supplied and did not match the task's current state |

---

### POST /board/assets

Record an asset upload. Inserts into `board_assets` and logs an event. The gateway stores metadata only — the actual file is stored by the Task Board service. Accepts an optional `content_hash` field and an optional `constraints` object (see "Compare-and-Set Constraints" above), checked against the referenced `board_tasks` row. The required `admission` object is the final authoritative upload guard; optional constraints may narrow it but cannot replace it.

**Request:**
```json
{
  "asset_id": "asset-550e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-123",
  "uploader_id": "a-worker-uuid",
  "filename": "login-page.zip",
  "content_type": "application/zip",
  "size_bytes": 245760,
  "storage_path": "data/assets/t-123/login-page.zip",
  "content_hash": "sha256:9f86d0...",
  "uploaded_at": "2026-02-28T14:00:00Z",
  "admission": {
    "expected_status": "accepted",
    "expected_lifecycle_version": 1,
    "max_files_per_task": 10
  },
  "event": {
    "event_source": "board",
    "event_type": "asset.uploaded",
    "timestamp": "2026-02-28T14:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-worker-uuid",
    "summary": "Bob uploaded login-page.zip (240 KB)",
    "payload": "{\"title\": \"Build login page\", \"filename\": \"login-page.zip\", \"size_bytes\": 245760}"
  }
}
```

`content_hash` is optional and stored verbatim (`NULL` if omitted) — the gateway does not compute or verify it. `admission` has exactly the three fields shown: status must be `accepted`, lifecycle version must be a nonnegative signed-64-bit SQLite integer (and not a boolean), and quota must be a positive signed-64-bit SQLite integer (and not a boolean).

**Transaction:**
```sql
BEGIN IMMEDIATE;
-- Reject competing native task work.
-- Read board_tasks joined to board_task_contracts and require accepted status,
-- the exact lifecycle version, and uploader_id equal to the assigned worker.
-- Only if constraints is provided: verify board_tasks matches {task_id, **constraints}.
-- Reject a duplicate asset_id before enforcing the per-task asset count quota.
INSERT INTO board_assets (asset_id, task_id, uploader_id, filename, content_type, size_bytes, storage_path, content_hash, uploaded_at)
  VALUES (:asset_id, :task_id, :uploader_id, :filename, :content_type, :size_bytes, :storage_path, :content_hash, :uploaded_at);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "asset_id": "asset-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 10
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                |
| 400    | `invalid_payload`       | `admission` is missing, malformed, out of SQLite integer range, or does not require `accepted` |
| 400    | `invalid_constraints`   | `constraints` is present but not an object, or a constraint column is not a valid identifier |
| 403    | `forbidden`             | `uploader_id` is not the task's assigned worker |
| 404    | `task_not_found`        | No task with this `task_id` exists             |
| 409    | `asset_exists`          | Asset with this `asset_id` already exists      |
| 409    | `foreign_key_violation` | `task_id` or `uploader_id` reference invalid   |
| 409    | `constraint_violation`  | `constraints` was supplied and the referenced task did not match |
| 409    | `task_version_conflict` | Task status is no longer `accepted` or lifecycle version changed |
| 409    | `too_many_assets`       | Current task asset count reached `max_files_per_task` |

---

### POST /reputation/feedback

Submit feedback for a completed task. Inserts into `reputation_feedback`, then **atomically reveals the sealed pair server-side**, and logs an event(s), all inside one `BEGIN IMMEDIATE` transaction (WP-07).

**The reveal policy lives in the gateway, not the caller.** Earlier revisions of this spec (and an earlier implementation) had the Reputation service read the reverse feedback row and pass a `reveal_reverse`/`reverse_feedback_id` flag on write. That is a read-then-write race: two concurrent submissions can each observe "no reverse yet" and both stay sealed forever. The gateway now performs the reverse-pair lookup itself, inside the same transaction as the insert, making the both-sealed outcome unreachable. Callers no longer send `reveal_reverse` or `reverse_feedback_id` — those fields are not part of the request contract.

**Request:**
```json
{
  "feedback_id": "fb-550e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-123",
  "from_agent_id": "a-poster-uuid",
  "to_agent_id": "a-worker-uuid",
  "role": "poster",
  "category": "delivery_quality",
  "rating": "satisfied",
  "comment": "Good work, met the requirements",
  "submitted_at": "2026-02-28T15:00:00Z",
  "event": {
    "event_source": "reputation",
    "event_type": "feedback.submitted",
    "timestamp": "2026-02-28T15:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-poster-uuid",
    "summary": "Alice rated Bob on delivery_quality",
    "payload": "{\"task_id\": \"t-123\", \"category\": \"delivery_quality\"}"
  }
}
```

`comment` is optional (`NULL` if omitted). An optional `force_visible: true` bypasses sealing entirely — this is reserved for platform-signed, court-generated feedback (the ruling flow), which is immediately visible rather than sealed pending the counterparty. Even under `force_visible`, the reverse-pair lookup still runs, so a sealed counterpart submitted earlier is still revealed by this write.

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO reputation_feedback
  (feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, submitted_at, visible)
  VALUES (:feedback_id, :task_id, :from_agent_id, :to_agent_id, :role, :category, :rating, :comment,
          :submitted_at, :force_visible_as_int);
-- Reverse-pair lookup: does a row exist for the same task with from/to swapped?
SELECT feedback_id FROM reputation_feedback
  WHERE task_id = :task_id AND from_agent_id = :to_agent_id AND to_agent_id = :from_agent_id;
-- If found: UPDATE reputation_feedback SET visible = 1 WHERE feedback_id IN (:new_feedback_id, :reverse_feedback_id);
--           and insert a second `feedback.revealed` event alongside the caller's event.
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "feedback_id": "fb-550e8400-e29b-41d4-a716-446655440000",
  "visible": true,
  "event_id": 11
}
```

`visible` is `true` when `force_visible` was set, or when this write revealed a matching reverse pair; `false` otherwise (the row stays sealed until the counterparty submits, or until `reveal_timeout_seconds` elapses — enforced by the Reputation service, not the gateway).

**Errors:**

| Status | Code                    | Description                                              |
|--------|-------------------------|----------------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                          |
| 409    | `feedback_exists`       | Feedback already submitted for this `(task, from, to)` triple |
| 409    | `foreign_key_violation` | Agent ID reference invalid                               |

---

### POST /court/claims

File a dispute claim. Inserts into `court_claims` and logs an event.

**Request:**
```json
{
  "claim_id": "clm-550e8400-e29b-41d4-a716-446655440000",
  "task_id": "t-123",
  "claimant_id": "a-poster-uuid",
  "respondent_id": "a-worker-uuid",
  "reason": "The login page does not validate email format as specified",
  "status": "filed",
  "filed_at": "2026-02-28T16:00:00Z",
  "event": {
    "event_source": "court",
    "event_type": "claim.filed",
    "timestamp": "2026-02-28T16:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-poster-uuid",
    "summary": "Alice filed a dispute on 'Build login page'",
    "payload": "{\"claim_id\": \"clm-550e8400\", \"title\": \"Build login page\", \"claimant_name\": \"Alice\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO court_claims (claim_id, task_id, claimant_id, respondent_id, reason, status, filed_at)
  VALUES (:claim_id, :task_id, :claimant_id, :respondent_id, :reason, :status, :filed_at);
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "claim_id": "clm-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 12
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                |
| 409    | `claim_exists`          | Claim with this exact `claim_id` already exists (idempotent-replay UNIQUE collision) |
| 409    | `task_already_disputed` | A fresh `claim_id` collided on `court_claims.task_id` — this task already has a claim filed against it (a second UNIQUE-family constraint; distinguished from `claim_exists` by looking up which row actually collided, so the message is honest rather than misleadingly calling it a duplicate claim) |
| 409    | `foreign_key_violation` | `task_id`, `claimant_id`, or `respondent_id` reference invalid |

---

### POST /court/claims/{claim_id}/status

Update a claim's status directly (distinct from the `claim_status_update` side-effect on `/court/rebuttals` and `/court/rulings`). `event` is **mandatory** on this route — it always logs an event alongside the status change, with no unlogged path. Accepts an optional `constraints` object (see "Compare-and-Set Constraints" above).

**Request:**
```json
{
  "status": "ruled",
  "constraints": {"status": "rebuttal"},
  "event": {
    "event_source": "court",
    "event_type": "claim.status_changed",
    "timestamp": "2026-02-28T18:00:00Z",
    "task_id": "t-123",
    "agent_id": null,
    "summary": "Claim clm-123 moved to ruled",
    "payload": "{\"claim_id\": \"clm-123\", \"status\": \"ruled\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
UPDATE court_claims SET status = :status WHERE claim_id = :claim_id [AND ...constraints];
-- If no row updated: re-SELECT to distinguish row-missing from constraint-mismatch (see CAS section)
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (200 OK):**
```json
{
  "claim_id": "clm-123",
  "status": "ruled",
  "event_id": 13
}
```

**Errors:**

| Status | Code                   | Description                                                |
|--------|-------------------------|----------------------------------------------------------------|
| 400    | `missing_field`        | `status` or `event` missing                                  |
| 400    | `invalid_constraints`  | `constraints` present but not an object                      |
| 404    | `claim_not_found`      | No claim with this `claim_id` (no `constraints` supplied)     |
| 404    | `not_found`            | No claim with this `claim_id` (`constraints` supplied — this route has no pre-write existence check, so the row-missing case surfaces as the generic CAS not-found code instead of `claim_not_found`; see "Compare-and-Set Constraints" above) |
| 409    | `constraint_violation` | `constraints` supplied and the row exists but doesn't match   |

---

### POST /court/rebuttals

Submit a rebuttal to a dispute claim. Inserts into `court_rebuttals`, optionally updates the claim status, and logs an event. Accepts an optional `constraints` object (see "Compare-and-Set Constraints" above): if `claim_status_update` is also present, `constraints` gates that status `UPDATE`; if `constraints` is present without `claim_status_update`, it is checked as a bare cross-table precondition against `court_claims` (no update performed, just a 409 if it does not hold).

**Request:**
```json
{
  "rebuttal_id": "reb-550e8400-e29b-41d4-a716-446655440000",
  "claim_id": "clm-123",
  "agent_id": "a-worker-uuid",
  "content": "The specification did not mention email format validation...",
  "submitted_at": "2026-02-28T17:00:00Z",
  "claim_status_update": "rebuttal",
  "event": {
    "event_source": "court",
    "event_type": "rebuttal.submitted",
    "timestamp": "2026-02-28T17:00:00Z",
    "task_id": "t-123",
    "agent_id": "a-worker-uuid",
    "summary": "Bob submitted a rebuttal on 'Build login page'",
    "payload": "{\"claim_id\": \"clm-123\", \"title\": \"Build login page\", \"respondent_name\": \"Bob\"}"
  }
}
```

The `claim_status_update` field tells the gateway to update the claim's status in the same transaction. This avoids a separate round-trip. If omitted or `null`, the claim status is not modified.

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO court_rebuttals (rebuttal_id, claim_id, agent_id, content, submitted_at)
  VALUES (:rebuttal_id, :claim_id, :agent_id, :content, :submitted_at);
-- Only if claim_status_update is provided:
UPDATE court_claims SET status = :claim_status_update WHERE claim_id = :claim_id;
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "rebuttal_id": "reb-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 13
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                |
| 400    | `invalid_constraints`   | `constraints` is present but not an object, or a constraint column is not a valid identifier |
| 409    | `rebuttal_exists`       | Rebuttal with this `rebuttal_id` already exists|
| 409    | `foreign_key_violation` | `claim_id` or `agent_id` reference invalid     |
| 409    | `constraint_violation`  | `constraints` was supplied and did not match the claim's current state |

---

### POST /court/rulings

Record a court ruling. Inserts into `court_rulings`, optionally updates the claim status, and logs an event.

**Request:**
```json
{
  "ruling_id": "rul-550e8400-e29b-41d4-a716-446655440000",
  "claim_id": "clm-123",
  "task_id": "t-123",
  "worker_pct": 70,
  "summary": "The specification was ambiguous about email validation...",
  "judge_votes": "[{\"judge_id\": \"judge-0\", \"worker_pct\": 70, \"reasoning\": \"...\"}]",
  "ruled_at": "2026-02-28T18:00:00Z",
  "claim_status_update": "ruled",
  "event": {
    "event_source": "court",
    "event_type": "ruling.delivered",
    "timestamp": "2026-02-28T18:00:00Z",
    "task_id": "t-123",
    "agent_id": null,
    "summary": "Court ruled 70% to worker on 'Build login page'",
    "payload": "{\"ruling_id\": \"rul-550e8400\", \"claim_id\": \"clm-123\", \"worker_pct\": 70, \"summary\": \"The specification was ambiguous...\"}"
  }
}
```

The `claim_status_update` field tells the gateway to update the claim's status in the same transaction. If omitted or `null`, the claim status is not modified.

**Transaction:**
```sql
BEGIN IMMEDIATE;
INSERT INTO court_rulings (ruling_id, claim_id, task_id, worker_pct, summary, judge_votes, ruled_at)
  VALUES (:ruling_id, :claim_id, :task_id, :worker_pct, :summary, :judge_votes, :ruled_at);
-- Only if claim_status_update is provided:
UPDATE court_claims SET status = :claim_status_update WHERE claim_id = :claim_id;
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (201 Created):**
```json
{
  "ruling_id": "rul-550e8400-e29b-41d4-a716-446655440000",
  "event_id": 14
}
```

**Errors:**

| Status | Code                    | Description                                    |
|--------|-------------------------|------------------------------------------------|
| 400    | `missing_field`         | Required field missing or empty                |
| 409    | `ruling_exists`         | Ruling with this `ruling_id` already exists    |
| 409    | `foreign_key_violation` | `claim_id` or `task_id` reference invalid      |

---

### DELETE /court/rulings/{claim_id}

Deletes a ruling row by `claim_id`. **Contract delta (WP-12):** this route now requires a JSON request body carrying an `event` object, exactly like every `POST` write route — so the deletion emits its event in the **same** `BEGIN IMMEDIATE` transaction as the delete. It is no longer bodyless, and `event` is not optional. Court is the only caller (used during ruling-retry cleanup, T-040).

**Request:**
```json
{
  "event": {
    "event_source": "court",
    "event_type": "ruling.deleted",
    "timestamp": "2026-02-28T18:05:00Z",
    "task_id": "t-123",
    "agent_id": null,
    "summary": "Ruling rul-550e8400 deleted (retry cleanup)",
    "payload": "{\"ruling_id\": \"rul-550e8400\", \"claim_id\": \"clm-123\"}"
  }
}
```

**Transaction:**
```sql
BEGIN IMMEDIATE;
DELETE FROM court_rulings WHERE claim_id = :claim_id;
-- The event is inserted ONLY if the DELETE actually removed a row (rowcount > 0).
-- A claim_id with no matching ruling deletes nothing, writes no event, and still
-- returns 200 with deleted: false — this is not an error.
INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload)
  VALUES (:event_source, :event_type, :timestamp, :task_id, :agent_id, :summary, :payload);
COMMIT;
```

**Response (200 OK — a ruling existed and was deleted):**
```json
{
  "deleted": true,
  "claim_id": "clm-123",
  "event_id": 15
}
```

**Response (200 OK — no ruling existed for this `claim_id`):**
```json
{
  "deleted": false,
  "claim_id": "clm-123",
  "event_id": null
}
```

`deleted` is the caller's signal for whether anything actually happened; `event_id` is `null`
whenever `deleted` is `false`, since no event was written for a no-op delete.

**Errors:**

| Status | Code                    | Description                                                     |
|--------|--------------------------|----------------------------------------------------------------------|
| 400    | `missing_field`         | `event` object missing or incomplete                                 |
| 409    | `foreign_key_violation` | The event's `agent_id` does not reference a valid agent (rolls back the delete too — delete and event insert are one transaction) |

---

## Error Codes

Complete list of error codes used by the Database Gateway service:

| Status | Code                      | Description                                              |
|--------|---------------------------|----------------------------------------------------------|
| 400    | `missing_field`           | Required field missing, null, or empty string            |
| 400    | `invalid_amount`          | Amount is not a valid integer in the required range      |
| 400    | `amount_out_of_range`     | Input exceeds the signed-64-bit monetary representation |
| 400    | `payload_mismatch`        | Initial issuance differs from balance, split poster differs from payer, or a settlement event names another task |
| 400    | `unknown_field`           | Request contains an unknown or disallowed field name     |
| 400    | `invalid_field`           | A supported nonfinancial update object names a disallowed column |
| 400    | `invalid_operation_id`    | Financial operation ID is noncanonical or is supplied for zero account creation |
| 400    | `invalid_percentage`      | Financial `worker_pct` is not a strict integer in 0..100 |
| 400    | `invalid_financial_request` | Canonical financial business request is invalid       |
| 400    | `empty_updates`           | `updates` object contains no fields                      |
| 402    | `insufficient_funds`      | Escrow lock would cause negative balance                 |
| 404    | `account_not_found`       | No account with this ID                                  |
| 404    | `escrow_not_found`        | No escrow with this ID                                   |
| 404    | `task_not_found`          | No task with this ID                                     |
| 409    | `public_key_exists`        | Public key already registered                            |
| 409    | `account_exists`          | Account already exists for this agent                    |
| 409    | `amount_out_of_range`     | A credit or settlement would overflow the recipient balance |
| 409    | `operation_id_conflict`   | Operation ID already owns another kind or canonical request |
| 409    | `financial_operation_already_committed` | Native natural-key effect or settlement belongs to another operation ID |
| 409    | `legacy_operation_conflict` | Attested historical credit/lock has changed immutable terms |
| 409    | `historical_settlement_unreplayable` | Attested historical terminal settlement has no native replay identity |
| 409    | `escrow_state_conflict`   | Settlement lost the writer-owned locked-state race       |
| 409    | `task_exists`             | Task with this ID already exists                         |
| 409    | `bid_exists`              | Agent already bid on this task                           |
| 409    | `asset_exists`            | Asset with this ID already exists                        |
| 409    | `task_version_conflict`   | Asset admission observed another task status or lifecycle version |
| 409    | `too_many_assets`         | Task asset count reached the admitted configured quota   |
| 409    | `feedback_exists`         | Feedback already submitted for this (task, from, to)     |
| 409    | `claim_exists`            | Claim with this ID already exists                        |
| 409    | `rebuttal_exists`         | Rebuttal with this ID already exists                     |
| 409    | `ruling_exists`           | Ruling with this ID already exists                       |
| 409    | `task_already_disputed`   | A Court claim already exists for this task (distinct from `claim_exists`) |
| 409    | `constraint_violation`    | A supplied `constraints` compare-and-set precondition did not match the row's current values |
| 409    | `foreign_key_violation`   | Foreign key constraint failed                            |
| 400    | `invalid_constraints`     | `constraints` is not a non-empty object of valid column identifiers |
| 400    | `invalid_json`            | Request body is not valid JSON, or not a JSON object      |
| 400    | `invalid_jws`             | Compact token violates the strict seven-field JWS profile |
| 400    | `invalid_payload`         | Request content or authenticated request binding is malformed or inconsistent |
| 401    | `invalid_authorization`   | Missing, duplicate, or malformed Bearer Authorization header |
| 401    | `token_expired`           | Authenticated service token is expired under Gateway policy |
| 401    | `token_not_yet_valid`     | Authenticated service token was issued too far in the future |
| 403    | `forbidden`               | Unknown KID, invalid signature, missing capability, unclassified route, or asset uploader is not the assigned worker |
| 404    | `not_found`               | Generic not-found from a `constraints` check whose target row doesn't exist |
| 404    | `agent_not_found` / `bid_not_found` / `asset_not_found` / `feedback_not_found` / `claim_not_found` / `rebuttal_not_found` / `ruling_not_found` | Domain-specific not-found on a read route |
| 405    | `method_not_allowed`      | Path-segment collision on a route that reuses a literal (read routes) |
| 503    | `service_not_ready`       | Reader/writer not yet initialized (startup ordering)      |
| 503    | `database_unavailable`    | The database file could not be stat'd (health check)      |
| 500    | `receipt_ownership_missing` | Existing financial effect has no native receipt or attested legacy marker |
| 500    | `receipt_ownership_failed` | Receipt/marker ownership or cutover attestation is corrupt or ambiguous |

---

## Standardized Error Format

All error responses follow this structure:

```json
{
  "error": "error_code",
  "message": "Human-readable description of what went wrong",
  "details": {}
}
```

Error responses contain exactly these three fields. The `details` object provides additional context when available (e.g., which field is missing, which foreign key failed) and is an empty object `{}` when there is no extra context. The `message` field never includes stack traces, SQL fragments, filesystem paths, or internal diagnostics.

This format is shared by all services in the Agent Task Economy.

---

## What This Service Does NOT Do

- **Domain authorization/orchestration** — no economic-agent signature verification, permission
  decisions, deadlines, or lifecycle orchestration. The financial writer does own receipt replay,
  exact monetary calculations, and the atomic locked-state settlement predicate.
- **ID generation** — callers generate all entity, transaction, escrow, and financial operation IDs
  and pass them in requests. The gateway generates only the autoincrement `event_id`; replay returns
  the stored winning IDs rather than caller candidates.
- **Event derivation** — the gateway does not construct event summaries or payloads. Callers provide the complete event object.
- **Status-transition validation beyond `constraints`** — `POST /board/tasks/{task_id}/status` accepts any allowed-column values; the calling service enforces which transitions are legal, optionally backed by a `constraints` precondition for exactly-once semantics.
- **Economic-agent authentication** — the gateway authenticates service capabilities only. Agent/user credentials remain the domain service's responsibility, and economic agents, UI, and the platform notary receive no Gateway capability.
- **Rate limiting** — no throttling on any endpoint. SQLite's write serialization (one writer connection, `BEGIN IMMEDIATE`) is the natural rate limiter.
- **Implicit schema migrations** — startup never adds columns to existing databases. It validates the complete versioned schema and requires an explicit, verified-backup-bound offline migration for supported legacy data; partial or unknown layouts fail readiness.

Superseded framing, corrected here: earlier drafts of this document said the gateway has "no read endpoints" and "does not serve read requests." That was accurate before the 2026-07 refactor and is not accurate now — see "Read API" above. Pagination on `GET /board/tasks` (`?limit=&offset=`) is the one read route that supports it. A non-numeric `limit`/`offset` value returns `400 invalid_payload` (WP-13 item 8b fix — previously an unhandled `ValueError` crashed to a generic `500`; see `services/db-gateway/tests/unit/routers/test_board_tasks_limit_offset_validation.py`).

---

## Interaction Patterns

### Task Creation Flow (Poster → Task Board → Gateway)

```
Agent               Task Board               Central Bank          DB Gateway
  |                      |                        |                     |
  |  1. POST /tasks      |                        |                     |
  |  (signed request)    |                        |                     |
  |  ------------------->|                        |                     |
  |                      |  2. Verify JWS         |                     |
  |                      |  (via Identity)        |                     |
  |                      |                        |                     |
  |                      |  3. POST /escrow/lock  |                     |
  |                      |  ------------------->  |                     |
  |                      |                        |  4. POST /bank/     |
  |                      |                        |     escrow/lock     |
  |                      |                        |  ------------------>|
  |                      |                        |                     | 5. BEGIN IMMEDIATE
  |                      |                        |                     | 6. Debit payer
  |                      |                        |                     | 7. Insert escrow
  |                      |                        |                     | 8. Insert tx
  |                      |                        |                     | 9. Insert event
  |                      |                        |                     | 10. COMMIT
  |                      |                        |  11. { escrow_id }  |
  |                      |                        |  <------------------|
  |                      |  12. { escrow_id }     |                     |
  |                      |  <-------------------  |                     |
  |                      |                        |                     |
  |                      |  13. POST /board/tasks |                     |
  |                      |  ------------------------------------------>|
  |                      |                        |                     | 14. BEGIN IMMEDIATE
  |                      |                        |                     | 15. Insert task
  |                      |                        |                     | 16. Insert event
  |                      |                        |                     | 17. COMMIT
  |                      |  18. { task_id }       |                     |
  |                      |  <------------------------------------------|
  |                      |                        |                     |
  |  19. 201 { task }    |                        |                     |
  |  <-------------------|                        |                     |
```

### Dispute Resolution Flow (Court → Gateway)

```
Court Service                                              DB Gateway
  |                                                             |
  |  1. POST /court/claims                                     |
  |  (insert claim + event)                                    |
  |  --------------------------------------------------------->|
  |                                                             | 2. BEGIN IMMEDIATE
  |                                                             | 3. Insert claim
  |                                                             | 4. Insert event
  |                                                             | 5. COMMIT
  |  6. { claim_id, event_id }                                 |
  |  <---------------------------------------------------------|
  |                                                             |
  |  ... worker submits rebuttal ...                           |
  |                                                             |
  |  7. POST /court/rebuttals                                  |
  |  (insert rebuttal + update claim status + event)           |
  |  --------------------------------------------------------->|
  |                                                             | 8. BEGIN IMMEDIATE
  |                                                             | 9. Insert rebuttal
  |                                                             | 10. Update claim status
  |                                                             | 11. Insert event
  |                                                             | 12. COMMIT
  |  13. { rebuttal_id, event_id }                             |
  |  <---------------------------------------------------------|
  |                                                             |
  |  ... LLM judge panel deliberates ...                       |
  |                                                             |
  |  14. POST /court/rulings                                   |
  |  (insert ruling + update claim status + event)             |
  |  --------------------------------------------------------->|
  |                                                             | 15. BEGIN IMMEDIATE
  |                                                             | 16. Insert ruling
  |                                                             | 17. Update claim status
  |                                                             | 18. Insert event
  |                                                             | 19. COMMIT
  |  20. { ruling_id, event_id }                               |
  |  <---------------------------------------------------------|
  |                                                             |
  |  21. POST /bank/escrow/split                               |
  |  (split escrow + event)                                    |
  |  --------------------------------------------------------->|
  |                                                             | 22. BEGIN IMMEDIATE
  |                                                             | 23. Credit worker
  |                                                             | 24. Credit poster
  |                                                             | 25. Insert txs
  |                                                             | 26. Resolve escrow
  |                                                             | 27. Insert event
  |                                                             | 28. COMMIT
  |  29. { escrow_id, worker_amount, poster_amount }           |
  |  <---------------------------------------------------------|
```

### Feedback Submission with Automatic Mutual Reveal

The caller never checks for the reverse pair itself and never sends `reveal_reverse`/`reverse_feedback_id` — those fields do not exist in the current request contract (see "POST /reputation/feedback" above). The gateway performs the reverse-pair lookup itself, inside the write transaction:

```
Reputation Service                                         DB Gateway
  |                                                             |
  |  1. POST /reputation/feedback                              |
  |     { ..., force_visible: false }                          |
  |  --------------------------------------------------------->|
  |                                                             | 2. BEGIN IMMEDIATE
  |                                                             | 3. Insert feedback (visible=0 unless force_visible)
  |                                                             | 4. Look up reverse-direction row for this (task, agents)
  |                                                             | 5. If found: UPDATE both rows visible=1,
  |                                                             |    insert a feedback.revealed event
  |                                                             | 6. Insert the caller's own event
  |                                                             | 7. COMMIT
  |  8. { feedback_id, visible: true, event_id }               |
  |  <---------------------------------------------------------|
```

---

## Configuration

```yaml
service:
  name: "db-gateway"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 8007
  log_level: "info"

logging:
  level: "INFO"
  directory: "data/logs"
  format: "json"

database:
  path: "data/economy.db"
  schema_path: "../../docs/specifications/schema.sql"
  busy_timeout_ms: 5000
  journal_mode: "wal"

request:
  max_body_size: 1048576

auth:
  audience: "db-gateway"
  max_lifetime_seconds: 300
  clock_skew_seconds: 30
  trusted_callers:
    gateway-identity:
      public_key_path: "../../data/keys/gateway-identity.pub"
      capabilities: ["gateway.identity.read", "gateway.identity.write"]
    gateway-central-bank:
      public_key_path: "../../data/keys/gateway-central-bank.pub"
      capabilities: ["gateway.bank.read", "gateway.bank.write"]
    gateway-task-board:
      public_key_path: "../../data/keys/gateway-task-board.pub"
      capabilities: ["gateway.board.read", "gateway.board.write"]
    gateway-reputation:
      public_key_path: "../../data/keys/gateway-reputation.pub"
      capabilities: ["gateway.reputation.read", "gateway.reputation.write"]
    gateway-court:
      public_key_path: "../../data/keys/gateway-court.pub"
      capabilities: ["gateway.court.read", "gateway.court.write", "gateway.board.task.read"]
```

| Section                | Key                | Type    | Description                                          |
|------------------------|--------------------|---------|------------------------------------------------------|
| `service.name`         |                    | string  | Service identifier                                   |
| `service.version`      |                    | string  | Service version                                      |
| `server.host`          |                    | string  | Bind address — **must stay `127.0.0.1`** (see the deployment constraint below; never `0.0.0.0` in a context where port 8007 is reachable off-host) |
| `server.port`          |                    | integer | Listen port — **8007**. No other port has ever been the gateway's canonical port. |
| `server.log_level`     |                    | string  | Uvicorn log level                                    |
| `logging.level`        |                    | string  | Application log level                                |
| `logging.directory`    |                    | string  | Directory for JSON log files                         |
| `logging.format`       |                    | string  | Log output format (`json` or `text`)                 |
| `database.path`        |                    | string  | Path to the shared `economy.db` file                 |
| `database.schema_path` |                    | string  | Mandatory readable current schema file; transactional bootstrap applies it only to an empty database (see Schema initialization and migration admission above) |
| `database.busy_timeout_ms` |               | integer | SQLite busy timeout in milliseconds                  |
| `database.journal_mode` |                  | string  | SQLite journal mode (`wal`)                          |
| `request.max_body_size` |                  | integer | Maximum request body size in bytes; enforced on every method, including body-bearing `DELETE` (413 when exceeded; JSON methods require `application/json`) |
| `auth.audience` | | string | Required receiver audience (`db-gateway`) |
| `auth.max_lifetime_seconds` | | integer | Maximum admitted token lifetime; production value `300` |
| `auth.clock_skew_seconds` | | integer | Receiver clock skew allowance; production value `30` |
| `auth.trusted_callers` | | mapping | Required KID → canonical public-key path and non-empty capability list |

All configuration values are required. The service fails to start if any value is missing. There are no hardcoded defaults.

**Deployment constraint:** capability authentication does not make the persistence API public. Port 8007 remains bound to `127.0.0.1` / the trusted host boundary, and normal startup is load-only: the five service keypairs must already be provisioned. Missing, corrupt, non-canonical, or duplicate trusted key material fails startup.

---

## Endpoint Summary

| Method | Path                           | Domain     | Operation                          | SQL Statements |
|--------|--------------------------------|------------|------------------------------------|----------------|
| GET    | `/health`                      | —          | Health check                       | 2 (SELECT)     |
| POST   | `/identity/agents`             | Identity   | Register agent                     | 2 (INSERT)     |
| POST   | `/bank/accounts`               | Bank       | Create account + optional credit   | 2–3 (INSERT)   |
| POST   | `/bank/credit`                 | Bank       | Credit account                     | 3 (UPDATE + INSERT × 2) |
| POST   | `/bank/escrow/lock`            | Bank       | Lock funds in escrow               | 4 (UPDATE + INSERT × 3) |
| POST   | `/bank/escrow/release`         | Bank       | Release escrow to recipient        | 4 (SELECT + UPDATE × 2 + INSERT × 2) |
| POST   | `/bank/escrow/split`           | Bank       | Split escrow between parties       | 5–7 (SELECT + UPDATE × 3–5 + INSERT × 2–4) |
| POST   | `/board/tasks`                 | Board      | Create task                        | 2 (INSERT)     |
| POST   | `/board/bids`                  | Board      | Submit bid (+ `bid_count` increment) | 3 (INSERT + UPDATE + INSERT) |
| POST   | `/board/tasks/{task_id}/status`| Board      | Update task status + fields        | 2 (UPDATE + INSERT) |
| POST   | `/board/assets`                | Board      | Record asset metadata              | 2 (INSERT)     |
| POST   | `/reputation/feedback`         | Reputation | Submit feedback + auto mutual reveal | 2–4 (INSERT + SELECT + optional UPDATE + INSERT × 1–2) |
| POST   | `/court/claims`                | Court      | File dispute claim                 | 2 (INSERT)     |
| POST   | `/court/claims/{claim_id}/status` | Court   | Update claim status (event mandatory) | 2 (UPDATE + INSERT) |
| POST   | `/court/rebuttals`             | Court      | Submit rebuttal + update claim     | 2–3 (INSERT + UPDATE + INSERT) |
| POST   | `/court/rulings`               | Court      | Record ruling + update claim       | 2–3 (INSERT + UPDATE + INSERT) |
| DELETE | `/court/rulings/{claim_id}`    | Court      | Delete ruling (retry cleanup) + event | 1–2 (DELETE + optional INSERT) |

Every read route listed in "Read API" above is additionally part of this service's contract — omitted from the SQL-statement-count table because each is a single `SELECT`/aggregate query with no transaction.
