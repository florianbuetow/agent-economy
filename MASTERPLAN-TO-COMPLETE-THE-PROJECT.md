# Master plan to complete the Agent Task Economy

**Review date:** 2026-10-15. **Deliverable:** architecture and implementation audit plus an executable completion plan; this document does not claim the implementation is complete.

**Reviewed baseline:** branch `refactor`, HEAD `d4148401f4e3e5749d878e560a742c4ccb2aa68c`, including the tracked staged and unstaged changes present at review start. Three detached review worktrees received the complete `git diff HEAD --binary` snapshot. Existing user changes were preserved. Findings therefore describe more than the committed HEAD; recheck their cited functions before implementing on a different revision.

**Assessment:** the normal economic lifecycle exists, but the platform is not ready for trusted financial/adjudication use. The most urgent repairs are lifecycle races and lost-acknowledgment settlement, immutable recoverable Court rulings, unauthenticated gateway access, unsafe asset filenames, stored browser script injection, and sealed-market data leaks. Then finish artifact handling, restart-safe agents, external onboarding and the complete human UI journey. This document contains 56 finding entries (54 from the original audit plus the reported UI CI and reproduced deadline-precision gaps; some corroborate the same root cause), 27 work packages, 58 paired test/implementation tickets, three tracked decision tickets and 25 release scenarios.

## 1. Outcome and completion boundary

The product is an economy for specification-driven work. A human can observe real activity, fund a posting identity, publish a job with its complete specification and inputs, compare offers, select a worker, inspect delivered work, approve or dispute it, and see the resulting payment and reputation. Independently operated agents can register, discover and bid for compatible work, deliver artifacts, defend their work in Court, receive payment, and subsequently post work themselves. These flows must survive retries, concurrent actors, process restarts, and temporary service outages.

The distinctive economic rule is that a poster cannot obtain extra requirements for free after accepting work: an ambiguous specification favors the worker. A prompt containing that sentence is insufficient evidence that this incentive works. The evidence presented to judges, the judgment rubric, the settlement, the two reputation dimensions, and the UI must all agree about the same immutable task and ruling.

Two delivery gates keep the scope explicit:

1. **Functional platform gate:** the entire human and external-agent journey works with real service calls; money and lifecycle invariants hold; operational recovery works. Preserve the ratified local, single-operator deployment and fixed posted reward for this gate. It includes service-owned quality scores and an evaluated real Court profile, so Q-M1 (evidence/privacy) and Q-M3 (score semantics) must be resolved before this gate can close.
2. **Full economic-vision gate:** the non-math classification example demonstrates measured multi-round selection/adaptation using those quality scores, and the post-v1 competitive-pricing choice is resolved and tested. Expanded judge/economic evaluation supports those claims. The historical “product tail” is included in this plan, not silently forgotten. A fixed-reward launch must not claim that undercutting determines payments or that specialization has been demonstrated.

Public multi-user hosting, salaries, a general compute marketplace, real-money convertibility, a blockchain, Kubernetes, and a frontend rewrite are not implied prerequisites. Their absence is not a defect in the ratified v1. Supporting arbitrary trusted-network agents is required; giving those agents access to a privileged persistence API or shared notary keys is not an acceptable onboarding mechanism.

### 1.1 Required invariants

- **Money:** integer coins; no negative or overflowing balance; every change has ledger provenance; authorized issuance equals spendable balances plus locked escrow, allowing only explicitly recorded issuance/burn adjustments. An escrow resolves exactly once to its recorded parties and amount. Retries return the same result or a content-conflict error.
- **Tasks:** one accepted bid and worker; authorized, versioned state transitions; completed/expired/cancelled states cannot be overwritten by stale requests; no work accepted after an economically final refund; no task advertised before posting escrow is secured.
- **Recovery:** every committed external effect can be discovered after a lost acknowledgment; pending financial/workflow effects are durable and retried even for terminal business states. No in-memory lock, dictionary, or process-local task is the only recovery record.
- **Court:** one immutable evidence bundle and final ruling per dispute; one fenced judging owner at a time; retrying delivery never reruns judgment; the same ruling version drives escrow, Court history, and both reputation dimensions.
- **Evidence:** task inputs and deliverables are immutable, attributable, hashed, bounded and retrievable; a judge cannot silently decide from incomplete evidence. Untrusted content is data, never privileged instructions or executable code.
- **Privacy:** sealed bids and feedback remain sealed through every API, UI query, event feed and metric until their reveal condition. An internal database read is not automatically a public projection.
- **Authority:** signatures identify an actor; action/resource/party/state checks authorize the operation. Ordinary agents cannot mint, settle arbitrary escrow, write raw persistence commands, or impersonate another participant.
- **Participation:** local roster entries are examples, not an admission list for the platform. A new external agent can finish a job without the platform private key or a database edit.
- **Observability:** the UI displays persisted reality and recovery state, distinguishes reward/bid/payment, and converges after reconnect without replaying the whole economy as new activity.

## 2. Evidence, scope and existing strengths

The review covers all seven services, the three shared libraries, the autonomous agent runtime, demo/task-generation tools, schema, prompts, current API/test specifications, startup/CI recipes, and prior architecture decisions. Findings below distinguish **confirmed code behavior**, **inferred failure schedules**, **product gaps**, and **unverified operational behavior**. A source-level race is not presented as a reproduced load-test failure.

Independent review lanes used native specialist agents with their configured role models: a code reviewer for money/persistence, an architect for lifecycle/Court, and an executor for UI/agents. They worked read-only in separate Git worktrees. The leader reviewed Identity, signing, Reputation, shared infrastructure, operations, governance and the integration of findings. No extra model overrides or recursive delegation were used.

### 2.1 Preserve what already works

- The project already has seven services, gateway-owned SQLite writes, semantic events and a real browser application. This is not a proposal to restart the project.
- Individual bank writes use `BEGIN IMMEDIATE`; balance debits use a balance predicate. Atomic writes are useful foundations, although they do not make several HTTP requests one transaction.
- Registration uniqueness, credit replay provenance, atomic paired-feedback reveal, background deadline evaluation, mediated rebuttal, and automatic ruling triggers already have implementations and regression coverage.
- Court already fetches deliverable bytes; the current working tree adds the explicit vagueness rubric. The residual problem is evidence completeness and judgment correctness, not complete absence of content/rubric.
- The operator is separated from the notary identity; treasury provisioning is an explicit idempotent command. The feeder already accepts bids and reviews submissions, and a deterministic unattended-economy test exists.
- The gateway read API, vanilla JavaScript frontend, direct read-only UI database access, manual funding and local deployment are ratified choices. Keep them unless a specific requirement justifies changing one.
- Existing tests and old “DONE” rows are evidence of particular behavior, not proof that crash recovery, concurrent HTTP histories, browser-driven real writes or economic incentives are complete.

### 2.2 Verification performed for this audit

| Check | Observed result | What it proves / limit |
|---|---|---|
| Identity unit suite, `uv run --no-sync --offline pytest tests/unit -q -o addopts=''` in `services/identity` | 75 passed, 11 warnings | Existing Identity assertions pass on the dirty review baseline; not external-agent onboarding or adversarial completeness. |
| Reputation unit suite, same command in `services/reputation` | 233 passed | Existing feedback assertions pass; fake-store tests do not prove task-party authorization or public event sealing. |
| Shared infrastructure unit suite, same command in `libs/service-commons` | 4 passed | Existing shared infrastructure tests pass. |
| Isolated in-memory signing probe | Non-object Identity header raised `AttributeError`; locally signed header with unexpected `alg` and string `exp` was accepted by local verifier | Concrete parser/policy inconsistency; no cryptographic forgery was demonstrated or claimed. |
| Whole-repository CI | Not yet established by this audit | Root CI resets databases and stops processes by shared ports/process patterns. An isolated worktree alone does not isolate these effects. See MP-01 and the final evidence addendum. |

No source code was fixed as part of this planning deliverable. Existing passing tests are retained as regression obligations. Full gate success and economic/model quality remain execution work, never assumed from this document.

## 3. Current architecture and target repair

| Component | Current responsibility | Target repair, preserving ownership |
|---|---|---|
| Identity + `service-auth` | Registration, Ed25519 JWS signing and verification | Durable key provisioning, consistent strict token validation, resource authorization at consumers, documented external identities. |
| Central Bank | Accounts, issuance, ledger and escrow policy | Bound integer arithmetic, durable idempotent settlement receipts, complete accounting and reconciliation. |
| Task Board | Jobs, offers, worker assignment, assets, deadlines, review and settlement coordination | Atomic transition claims, immutable accepted terms/evidence, durable effects and recovery. |
| Court | Claims, rebuttal, judge panel and ruling distribution | Fenced adjudication, persisted evidence/verdict before delivery, resumable delivery, evaluated prompts. |
| Reputation | Raw feedback and reveal policy | Task-party validation, consistent reveal projections, independent quality scores and provenance. |
| DB Gateway | Shared SQLite schema, reads, transactional writes/events | Service authority, domain invariant checks, versioned migrations and atomic domain commands. |
| UI | Read-only projections and operator-signed write proxy | Privacy-aware projections, complete human journey, accurate money/state, reconnect-safe events. |
| Agents/tools | SDK, feeder, math worker, scripted examples | External integration path, restart reconciliation, bounded execution and honest non-math demonstrations. |

Keep the existing service boundaries. Do not introduce a generic distributed transaction framework, message broker or new database as a prerequisite. Because the gateway already owns the relevant tables, it can execute narrowly scoped atomic commands where appropriate. For effects crossing services, use a durable operation record/outbox in the gateway, stable operation IDs, persisted request hashes/results, conditional claims and reconciliation. Task Board still owns workflow/settlement decisions; Bank still validates financial policy; Court does not acquire direct Bank ownership.

### 3.1 Lifecycle repair contract

Maintain public statuses `open`, `accepted`, `submitted`, `approved`, `cancelled`, `disputed`, `ruled`, `expired`. Add explicit operation/version/effect fields or operation records; do not overload a business status to mean “every downstream effect succeeded.”

| Command | Required atomic predicate | Durable result/effect |
|---|---|---|
| Publish task | Stable posting ID, same specification/reward/escrow request hash on replay | Secured escrow and exactly one published task; incomplete posting reconciles or refunds with provenance. |
| Place bid | Task open and bidding cutoff valid; worker eligible; one bid per worker | Immutable bid amount/identity/timestamp; count and event agree. |
| Accept bid | Open version, bid belongs to task, cutoff policy satisfied | Winning worker and accepted terms frozen atomically; cancellation cannot win afterward. |
| Upload/submit | Assigned worker, accepted version, execution cutoff, asset/count limits | Immutable artifact manifest and submission revision; expiry cannot settle an already committed submission. |
| Approve/auto-approve | Submitted version, review deadline policy | One settlement intent, worker payout receipt, final task projection. |
| Dispute | Submitted version and authorized poster before cutoff | One claim intent and stable dispute ID; mutually exclusive with approval. |
| Cancel/expire | Eligible version and deadline predicate | One refund intent and receipt; no later bid acceptance or submission overwrites it. |
| Record ruling | Disputed version, matching dispute and immutable ruling hash | Same ruling on replay; conflicting content rejected; exact receipt and both feedback records recoverable. |

Use database time/version predicates consistently; test exact cutoff boundaries with a clock seam. GET may trigger a durable evaluation for compatibility, but background reconciliation must finish work without a reader. Specify whether equality at each cutoff is inclusive in the API/test spec before changing code.

## 4. Findings register

Severity: **P0** immediate integrity/security release blocker; **P1** required functional/recovery gap; **P2** important correctness, operability or economic-model work; **P3** optional improvement. Confidence labels describe evidence, not priority. All implementation items remain open.

### 4.1 Identity, keys and reputation

#### F-A01 — Concurrent key creation can split platform identity (P1; inferred from explicit file operations)

`libs/service-auth/src/service_auth/factory.py:_load_config` checks whether both files exist, then calls `generate_keypair`. `libs/service-auth/src/service_auth/signing.py:46-80` writes private and public files separately with replacement semantics. `justfile:266-280` starts several services that load the same platform handle concurrently. Two first-run processes can generate different keys, register different identities, retain different in-memory keys, and leave a mismatched or last-writer pair on disk. A single missing half of a previously registered pair also causes regeneration, losing access to the old identity. Key permissions depend on process umask.

**Repair:** one explicit bootstrap owner; exclusive creation/locking and atomic publication; verify keypair consistency; restrictive private-file permissions; fail on partial/corrupt pairs rather than silently replace. Consumer services should load required provisioned keys. Maintain a documented backup/recovery procedure. **Proof:** barrier-controlled concurrent fresh bootstrap plus interrupted writes and missing/mismatched halves; every successful process uses the same key/agent ID and funds stay attributable. **Work:** MP-03.

#### F-A02 — Token policy is optional and inconsistent (P1 for strict admission; confirmed)

`libs/service-auth/src/service_auth/signing.py:162-255` permits no-expiry issuance, accepts absent/non-integer expiration on local verification and does not validate header algorithm/type or decoded object shape. Identity enforces its algorithm but tolerates absent expiry (`agent_registry.py:186-190`). Factory silently selects no-expiry when signing config is absent. Signed requests have no common operation identifier or audience contract. Expiry does not stop replay within the window.

**Repair:** one specified production token profile, required typed issuance/expiry with bounded lifetime/skew, algorithm and object checks, resource/action binding, and stable per-command idempotency IDs. Keep fixtures explicitly separate from production admission. Key revocation remains the ratified v1 deferral; rotation/revocation becomes required for public multi-user operation. **Proof:** all consumers agree on missing/wrong-type/expired/future claims, exact expiry boundary, wrong target/action, replay and conflicting replay. **Work:** MP-03, MP-04. This is validation hardening, not evidence that Ed25519 was broken.

#### F-A03 — Malformed Identity header causes an unhandled server error (P2; reproduced)

`services/identity/src/identity_service/services/agent_registry.py:125-143` calls `.get` on decoded JSON without first requiring an object. An unsigned protected header containing `[]` reaches `AttributeError` before signature verification. **Repair:** reject non-object/malformed header and all invalid claim types with the uniform client error. Canonicalize public-key encodings before enforcing uniqueness as part of the same parser review. **Proof:** null/list/string/number headers and alternate encodings never yield 500 or duplicate identities for identical key bytes. **Work:** MP-03.

#### F-R01 — A valid signer can rate unrelated tasks and participants (P0; confirmed missing authorization)

`services/reputation/src/reputation_service/routers/feedback.py:199-259` checks signer equals `from_agent_id`; `services/reputation/src/reputation_service/services/feedback.py:65-199` validates field values, not task membership or completion. `db_gateway_service/services/reputation_writer.py:103-121` inserts feedback with FKs only; `docs/specifications/schema.sql:181-200` does not tie reviewers to the task's poster/worker. Any registered attacker can submit feedback about another registered agent using an existing task, including an unfinished task. Signing proves authorship, not entitlement.

**Repair:** validate the authoritative task, eligible terminal outcome, actual parties and category direction in a transaction-backed command; treat Court feedback as a distinct provenance-bearing source tied to a finalized ruling. Reject unrelated/self/category-swapped feedback. Preserve separately authored human/agent feedback and Court judgments without accidental uniqueness collisions. **Proof:** three-agent adversarial matrix covering unrelated task, unfinished task, wrong recipient/category and authorized Court retry. **Work:** MP-13.

#### F-R02 — Sealed feedback leaks through events and timeout visibility diverges (P0/P1; confirmed)

`reputation_service/services/feedback_db_client.py:86-100` includes rating/category in the `feedback.submitted` event before mutual reveal. UI event reads serialize that payload. Meanwhile `feedback.py:is_visible` computes timeout visibility dynamically while UI aggregates use persisted `visible=1`. A supposedly sealed rating is observable early in one route and may remain absent late in another.

**Repair:** separate private audit data from public event projections; do not publish sealed content or inferable rating-dependent aggregates; commit reveal state and its event through a durable timed operation, or centralize an equivalent time-aware projection used everywhere. **Proof:** before/after reveal cross-check raw APIs, all UI routes, SSE reconnect/history and aggregates, including one-sided feedback timing out with no reads. **Work:** MP-12, MP-13.

#### F-R03 — Reputation lacks the service-owned score contract required by the vision (P1 product gap)

The Reputation routes expose raw records, not the ratified score endpoint (Q-12). Court payout share alone cannot independently measure specification quality and delivery quality: a worker can win because a spec was vague or because delivery was excellent, and these are different facts. UI/agent consumer formulas risk diverging.

**Repair:** versioned service-owned scoring with sample counts, source/outcome provenance and explicit treatment of no evidence; separate the two quality dimensions in judge output and aggregation. Preserve raw feedback and explain uncertainty rather than assigning earned trust to an untested identity. Q-M3 records the only unresolved economic choice. **Proof:** clear-bad-work and vague-adequate-work fixtures produce distinct dimensions, all consumers agree, sealed feedback has no premature influence, and retries do not double-count. **Work:** MP-14, MP-24.

#### F-R04 — Gateway client errors conceal invalid data and conflicts (P2; confirmed)

`reputation_service/services/feedback_db_client.py:25-32,104-111,145-159` turns every 409 into duplicate feedback, returns empty objects for malformed JSON, and silently filters malformed list entries. Other client adapters have similar tolerant normalization. A missing task FK can be reported as “feedback exists”; invalid upstream data can appear as an empty history. **Repair:** typed response validation, explicit error-code mapping and consistent upstream-unavailable errors; never coerce a corrupt primary response into a valid-looking empty/zero result. **Proof:** invalid JSON/shape, FK conflicts, genuine duplicates and timeout responses are distinguishable. **Work:** MP-20.

### 4.2 Money and persistence

#### F-M01 — DB Gateway writes bypass every domain authorization rule (P0; code evidence with explicit failure schedule)

**Evidence:** `services/db-gateway/src/db_gateway_service/app.py:29-40` installs routers and request-shape middleware only. `routers/bank.py:22-162` accepts raw account, credit, lock, release, and split payloads without authenticating the caller. `config.yaml:8-10` binds to loopback, which limits reachability but does not authenticate local callers. The autonomous agents and all services run on that same host. By contrast, Central Bank enforces platform signatures at `services/central-bank/src/central_bank_service/routers/accounts.py:173-176` and `routers/escrow.py:125-137`, `:202-219`.

**Confirmed failure:** Any local process can POST directly to `/bank/credit` with an arbitrary account, positive amount, reference, and event, minting funds without a platform signature. It can also settle any known escrow. The same boundary exposes every other domain writer, so a compromised/buggy service has cross-domain write authority.

**Impact/root cause:** The security policy exists only in domain services, while the authoritative persistence service trusts all HTTP callers. Loopback is a deployment constraint, not an authorization control.

**Fix:** Define authenticated service identities and per-domain capabilities at DB Gateway. Reuse the existing Ed25519/service-auth machinery or an equivalent authenticated transport; bind each caller identity to allowed route prefixes/actions, verify body integrity and freshness, and reject missing/wrong service credentials. Keep the Gateway private in deployment, but do not use network location as the primary contract. Do not add a compatibility path that accepts unsigned writes.

**Regression/acceptance test:** Start the real Gateway. Prove unsigned `/bank/credit` is rejected and balance/events remain unchanged; a Central Bank-authenticated request succeeds; a Task Board credential cannot call `/bank/*`; replay/tamper/expired credentials fail. Repeat one representative route per domain to prove capability scoping.

**Dependencies:** MP-03 credential provisioning and the explicit per-domain capability contract; shared client transport changes precede endpoint enforcement. This is a technical design task, not another major product question.

**Work:** MP-02.


#### F-M02 — Committed escrow settlement cannot be replayed, so Task Board state and money can diverge permanently (P0; code evidence with explicit failure schedule)

**Evidence:** Gateway release/split reject every resolved escrow in `_load_escrow` (`services/db-gateway/src/db_gateway_service/services/bank_writer.py:411-429`) and persist no settlement request key or recipient/result. Central Bank converts all 409s to `escrow_already_resolved` (`services/central-bank/src/central_bank_service/services/ledger_db_client.py:250-260`, `:333-341`). Task approval and ruling settle escrow first, then update task state in a separate HTTP transaction (`services/task-board/src/task_board_service/services/task_review.py:176-185`; `task_ruling.py:406-434`). Deadline recovery retries release but treats every `ServiceError` as failure, leaving `escrow_pending` set (`escrow_coordinator.py:79-95`, `:116-128`).

**Concrete interleavings:**

1. Gateway commits worker credit + escrow `released`; the response is lost. Central Bank/Task Board sees a timeout.
2. A retry sends a new `tx_id`; Gateway observes `released` and returns 409 rather than the original success.
3. For approval/ruling, the task remains `submitted`/`disputed` even though money moved. Future retries stop on 409 before updating the task. For deadline processing, the task is terminal but `escrow_pending=1` remains forever.
4. The identical-ruling fast path at `task_ruling.py:380-384` only helps after task status was successfully persisted as `ruled`; it does not cover the settlement-committed/state-update-failed window.

**Impact/root cause:** This violates recoverability and creates externally inconsistent money/lifecycle state. The root cause is a non-idempotent settlement contract across two independently committed services.

**Fix:** Give release/split a stable caller-supplied settlement idempotency key and persist the complete settlement intent/result atomically with the escrow resolution. On exact replay, return the original successful response; on the same key or escrow with different recipient/split, return an explicit payload conflict. Store enough data to validate release recipient or both split recipients/amounts. Task Board must derive stable keys from the lifecycle operation and use CAS when persisting the terminal state. A reconciler may retry pending records, but it must use this primary idempotent contract rather than interpreting a generic 409 as success.

**Regression/acceptance test:** Fault-inject a transport error after Gateway commit for approve, cancel/expire, and 0/partial/100% ruling settlement. Retry the same lifecycle request. Assert one set of credit transactions/events, correct balances, one terminal task state, and `escrow_pending=0`. A replay with changed recipient or percentage must fail without writes.

**Dependencies:** Schema migration for settlement identity/result; Gateway writer and Central Bank API/client changes; Task Board orchestration changes. Land before relying on Court retry semantics.

**Work:** MP-04, MP-07, MP-08.


#### F-M03 — Task creation can orphan escrow after a non-duplicate persistence failure (P1; code evidence with explicit failure schedule)

**Evidence:** Task creation locks escrow before inserting the task (`services/task-board/src/task_board_service/services/task_creation.py:232-260`). Compensation exists only for `DuplicateTaskError` (`:290-305`). Any other store/Gateway failure after a successful lock exits without refund. Even the duplicate compensation logs and suppresses a failed refund (`:293-299`) without a durable recovery record.

**Concrete interleaving:** Central Bank commits the lock. `insert_task` then times out before commit, fails from a schema/runtime error, or its response is ambiguous. No task row is available to drive deadline/cancel recovery, while the escrow remains locked. If insert actually committed but its response was lost, blindly refunding would be wrong; the code needs a read-after-ambiguity decision.

**Impact/root cause:** Funds can become permanently unavailable with no task owner/state representing them. The root cause is an incomplete saga around the required lock-before-open sequence.

**Fix:** Persist a durable task-creation operation record/outbox keyed by `task_id`, or implement a recoverable saga: use the stable escrow lock identity, attempt task creation, and on ambiguous failure query task and escrow state before deciding. If no task exists, issue an idempotent refund settlement and persist/emit the compensation result. If task exists with the same escrow/spec identity, return the created task. Never swallow failed compensation without a durable pending record.

**Regression/acceptance test:** Inject (a) definite pre-commit task insert failure, (b) post-commit response loss, and (c) refund response loss. Assert respectively: refund exactly once/no task; task retained/no refund; refund exactly once after retry. In every case, `account balances + locked escrow` conserves value.

**Dependencies:** F-M02 idempotent settlement; stable task-create idempotency contract.

**Work:** MP-05.


#### F-M04 — Authoritative split transaction does not enforce that the poster leg returns to the escrow payer (P0; code evidence with explicit failure schedule)

**Evidence:** Central Bank performs a pre-read guard (`services/central-bank/src/central_bank_service/services/ledger_db_client.py:293-315`). The authoritative Gateway transaction loads only `escrow_id`, `amount`, and `status` (`services/db-gateway/src/db_gateway_service/services/bank_writer.py:411-429`) and credits `data["poster_account_id"]` without comparing it to `payer_account_id` (`:487-510`). No direct Gateway test covers an impostor poster; the existing parity test exercises the higher client facade (`services/central-bank/tests/unit/test_ledger_store_contract.py:325-332`).

**Confirmed failure:** A direct Gateway request, or a future Central Bank bug between pre-read and write construction, can split 40/60 from Alice's escrow to worker Bob and unrelated account Mallory. The transaction commits because all referenced accounts exist and amounts sum correctly.

**Impact/root cause:** A persistence invariant that determines ownership of funds is enforced only client-side. F-M01 makes the direct exploit reachable; even after Gateway auth, the authoritative transaction should reject malformed service input.

**Fix:** Select `payer_account_id` inside `_load_escrow`, compare it to `poster_account_id` before inserting the event or crediting either leg, and return `payload_mismatch` atomically. Prefer deriving the poster account from the escrow row and removing it from the persistence request if the API contract permits.

**Regression/acceptance test:** Directly call Gateway split with a valid worker, valid unrelated poster, and sums matching escrow. Assert 400 `payload_mismatch`, unchanged escrow/status/balances/transactions/events. Exercise both zero and non-zero poster legs.

**Dependencies:** None; can land before F-M01.

**Work:** MP-06.


#### F-M05 — Unbounded money values overflow SQLite INTEGER into REAL and lose precision (P0; code evidence with explicit failure schedule)

**Evidence:** Central Bank checks only sign (`accounts.py:76`, `:158-160`; `escrow.py:55-57`, `:189-191`). Gateway integer helpers check type/sign only (`services/db-gateway/src/db_gateway_service/routers/helpers.py:83-104`). The schema has `INTEGER` affinity but no `CHECK(typeof(...)='integer')`, non-negative constraint, or maximum (`docs/specifications/schema.sql:31-74`). Credits add without an overflow predicate (`bank_writer.py:109-138`). A safe in-memory SQLite probe on this snapshot confirmed that `9223372036854775807 + 1` is stored as `(9.223372036854776e+18, 'real')`. Ledger reads then coerce it with `int(...)` (`ledger_db_client.py:100-104`), hiding the type change and preserving a rounded value.

**Impact/root cause:** Large validly signed credits can change the storage type, lose unit precision, break equality/replay assumptions, and corrupt accounting totals. Values beyond SQLite's binding range can instead raise an unhandled error.

**Fix:** Define one explicit maximum monetary value and maximum account balance in the authoritative Gateway configuration/contract. Validate every amount and initial balance, and make credit/release/split updates conditional on `balance <= max_balance - amount`. Add schema-level integer/non-negative checks for new databases and a migration/rebuild for existing tables. Map overflow/cap violations to a stable 400/409 error; do not rely on later `int()` coercion.

**Regression/acceptance test:** Test max-allowed creation/credit, max+1 rejection, credit that would cross max, release/split into a near-max recipient, and a direct schema insert/update. All rejected operations must leave no event/transaction and `typeof(balance)` must remain `integer`.

**Dependencies:** Explicit safe representation bounds in configuration and the migration plan. This is an engineering constraint, not an unresolved currency/supply policy.

**Work:** MP-06.


#### F-M06 — Partial or missing schemas can pass startup and fail on the first money request (P1; code evidence with explicit failure schedule)

**Evidence:** Lifespan silently sets `schema_sql=None` when the configured schema path is missing (`services/db-gateway/src/db_gateway_service/core/lifespan.py:36-47`). `DbWriter` skips all initialization in that case (`services/db-gateway/src/db_gateway_service/services/db_writer.py:72-73`). When a schema is present, `_schema_is_absent` considers any non-SQLite table proof of a complete database (`:81-98`). Migrations return without error when a target table is absent (`:100-129`). Thus a DB containing one leftover/foreign table skips bootstrap and all missing-table checks.

**Impact/root cause:** Health/startup can report operational while account/escrow requests later fail with raw `no such table` errors. A failed or interrupted first initialization can become unrecoverable on restart. This conflicts with the project's fail-fast configuration contract.

**Fix:** Make the schema path mandatory and readable. Introduce a schema-version table and ordered transactional migrations. At startup, validate the exact required tables, columns, indexes, foreign keys, and `PRAGMA integrity_check`/`foreign_key_check` result appropriate to the deployment. An unknown/partial schema must fail startup with a diagnostic; a known old version must migrate deterministically. Do not use “any table exists” as the gate.

**Regression/acceptance test:** Start against: missing schema file, empty DB, DB with one unrelated table, DB missing one money table/index, known prior version, and a migration interrupted after one DDL. Only empty/known versions may start; partial/unknown layouts must fail before health is ready, and a migrated DB must preserve balances/escrows.

**Dependencies:** Schema version/migration design. Complete before settlement and constraint schema changes are deployed.

**Work:** MP-06, MP-21.


#### F-M07 — Account creation can record a balance that disagrees with its initial credit transaction (P1; code evidence with explicit failure schedule)

**Evidence:** Gateway requires `initial_credit` for positive balances and validates that its amount is positive, but never compares it with `balance` (`services/db-gateway/src/db_gateway_service/routers/bank.py:30-44`). Writer stores `initial_credit.amount` while independently storing `data["balance"]` as `balance_after` (`services/db-gateway/src/db_gateway_service/services/bank_writer.py:42-65`).

**Confirmed failure:** `{balance: 100, initial_credit: {amount: 1, ...}}` creates an account holding 100 credits whose only issuance transaction says 1 credit. Both rows commit.

**Impact/root cause:** The transaction history cannot reconstruct or audit issuance. The shared Central Bank client happens to send matching values, but the authoritative endpoint accepts an inconsistent ledger.

**Fix:** Require `initial_credit.amount == balance`; require `initial_credit` absent when balance is zero; enforce in the writer as well as route validation. Consider deriving balance from the initial credit rather than accepting two sources of truth.

**Regression/acceptance test:** Direct Gateway create with mismatch, zero plus initial credit, and positive balance without initial credit. Assert rejection and no account/event/transaction. Matching values succeed.

**Dependencies:** None; coordinate with F-M05 bounds.

**Work:** MP-06.


#### F-M08 — Escrow lock idempotency ends at settlement, allowing the same task to be debited again (P1; code evidence with explicit failure schedule)

**Evidence:** The schema unique index covers `(payer_account_id, task_id)` only while `status='locked'` (`docs/specifications/schema.sql:72-74`). Replay lookup also searches only locked rows (`services/db-gateway/src/db_gateway_service/services/bank_writer.py:306-319`). After release/split changes the status, the same signed task lock can create a new escrow ID and debit again.

**Impact/root cause:** A delayed replay after settlement is treated as a new operation even though task IDs are globally single-use in Task Board. This creates an orphan escrow and breaks end-to-end idempotency.

**Fix:** Make `(payer_account_id, task_id)` unique for the escrow's lifetime and look up any status on replay. Exact replay should return the existing escrow identity/state without debiting; changed amount must conflict. If the public contract should reject settled replays rather than return them, specify that explicitly, but it must never create a second lock.

**Regression/acceptance test:** Lock, release/split, then replay identical and changed-amount lock payloads. Assert no new escrow/transaction/event and no balance change. Add a migration test that detects/reconciles any historical duplicates before creating the full unique index.

**Dependencies:** Migration framework from F-M06.

**Work:** MP-04, MP-05, MP-06.


#### F-M09 — Shared clients silently replace malformed persistence responses with plausible defaults (P2; code evidence with explicit failure schedule)

**Evidence:** `GatewayClient` returns `0` for missing counts/totals and `[]` or filtered subsets for malformed list bodies (`libs/service-clients/src/service_clients/gateway.py:83-97`, `:161-181`, `:347-369`, `:388-395`). `LedgerDbClient.credit` substitutes the newly generated tx ID and balance `0` when fields are absent (`services/central-bank/src/central_bank_service/services/ledger_db_client.py:134-137`); release substitutes amount from a prior read (`:227-269`). `BaseServiceClient._response_dict` turns invalid/non-object successful JSON into `{}` (`libs/service-clients/src/service_clients/base.py:118-129`).

**Impact/root cause:** A broken Gateway contract can be reported as a successful zero balance/count or an empty history. This suppresses diagnostics and violates the root-cause guard; callers cannot distinguish “none” from malformed persistence output.

**Fix:** Validate success response schemas strictly at the shared client boundary and raise a visible `invalid_service_response`/502 containing service/path and safe field diagnostics. Remove `.get(..., 0)` and list-item filtering for required fields. Keep narrow `None` handling only for documented 404 contracts.

**Regression/acceptance test:** Mock 2xx responses with invalid JSON, missing required fields, wrong types, and mixed valid/invalid list items. Every malformed response must fail visibly; complete valid responses remain unchanged.

**Dependencies:** Agree canonical response models; can proceed independently.

**Work:** MP-20.


#### F-M10 — JSON booleans pass Central Bank's integer checks and fail downstream with the wrong status (P2; code evidence with explicit failure schedule)

**Evidence:** Python `bool` is an `int`. Central Bank accepts booleans under `isinstance(value, int)` for initial balance, credit/lock amount, and worker percentage (`services/central-bank/src/central_bank_service/routers/accounts.py:76`, `:158-160`; `routers/escrow.py:55-57`, `:189-191`). Gateway correctly excludes booleans (`services/db-gateway/src/db_gateway_service/routers/helpers.py:83-104`). LedgerDbClient converts an unexpected Gateway 400 into `RuntimeError` for several paths (`ledger_db_client.py:70-75`, `:121-132`, `:193-205`), so a signed `true` can become a Central Bank 500 rather than the documented 400.

**Impact/root cause:** Observable validation semantics differ between the domain API, its fake store, and production persistence. The production path leaks an internal dependency failure for valid JSON with invalid content.

**Fix:** Use one explicit integer validator that rejects `bool` at the Central Bank boundary and in storage interfaces. Map authoritative Gateway validation errors to the same domain `ServiceError`, not a generic runtime exception.

**Regression/acceptance test:** Send signed `true`/`false` for every money numeric field against both the in-memory contract suite and real Gateway-backed integration path. Assert documented 400 code, zero calls/writes, and parity between stores.

**Dependencies:** None.

**Work:** MP-06, MP-20.


### 4.3 Task Board, assets and Court

#### F-L01 — Lifecycle actions do not atomically claim a transition (P0; code evidence; failure histories require execution proofs)

**Evidence.** Deadline-driven transitions correctly use compare-and-swap through `expected_status` (`services/task-board/src/task_board_service/services/deadline_evaluator.py:108-112`, `134-138`, `159-163`), and `TaskDbClient` already knows how to forward that constraint (`services/task-board/src/task_board_service/services/task_db_client.py:172-201`). The interactive lifecycle paths do not use it:

- bid acceptance writes `open -> accepted` with `expected_status=None` (`services/task-board/src/task_board_service/services/task_bidding.py:263-302`);
- submission writes `accepted -> submitted` without a status constraint (`services/task-board/src/task_board_service/services/task_review.py:64-109`);
- approval releases escrow, then writes `submitted -> approved` without a constraint (`services/task-board/src/task_board_service/services/task_review.py:155-185`);
- cancellation releases escrow, then writes `open -> cancelled` without a constraint (`services/task-board/src/task_board_service/services/task_creation.py:392-422`);
- dispute filing calls Court first, then writes `submitted -> disputed` without a constraint (`services/task-board/src/task_board_service/services/task_ruling.py:78-163`);
- ruling settlement writes `disputed -> ruled` without a constraint (`services/task-board/src/task_board_service/services/task_ruling.py:375-434`).

The Gateway explicitly delegates transition validity to the caller and performs an unconditional update when constraints are absent (`services/db-gateway/src/db_gateway_service/services/board_writer.py:181-192`, `214-245`). The existing “first action wins” test is sequential—approve completes before dispute starts—so it does not exercise the race (`services/task-board/tests/unit/routers/test_lifecycle.py:564-594`).

**Concrete interleaving.** Approval and dispute both read `submitted`. Approval releases escrow to the worker. Before approval writes status, dispute files a Court claim. Both unconditional updates succeed; whichever arrives last owns the task row. The result can be `disputed` with escrow already paid, or `approved` with an active Court claim. Equivalent failures exist for cancel versus accept and submit versus deadline expiry.

**Recommendation.** Require every lifecycle mutation to provide the expected source status and treat a zero-row update as `409 invalid_status`. For operations with remote side effects, a simple CAS at the final write is insufficient; first acquire a durable operation/transition intent (F-L02), then perform the side effect, then finalize. Do not add more read-before-write checks.

**Failing-test acceptance scenario.** Place barriers after both approve and dispute have loaded the same `submitted` task, then run them concurrently. Exactly one request returns success, the other returns `409`; exactly one escrow resolution exists; if approval wins there is no Court claim, and if dispute wins escrow remains locked. Repeat for accept/cancel, submit/expiry, and two different bid acceptances.

**Work:** MP-07, MP-08.


#### F-L02 — Manual settlements and task creation lack durable recovery (P0; code evidence; failure histories require execution proofs)

**Evidence.** Approval, cancellation, and ruling settle escrow before persisting the terminal task state (`task_review.py:176-185`, `task_creation.py:413-422`, `task_ruling.py:406-434`). Central Bank deliberately rejects a second release/split after escrow is resolved with `409 escrow_already_resolved` (`docs/specifications/service-api/central-bank-service-specs.md:474-492`, `571-596`). Therefore a Gateway failure after the Bank call leaves the task retriable only in theory: the next request repeats the Bank operation, gets 409, and never reaches the task update.

Creation has the inverse gap. It locks escrow before inserting the task (`services/task-board/src/task_board_service/services/task_creation.py:232-289`), but only `DuplicateTaskError` enters rollback (`:290-305`). A Gateway transport/5xx failure escapes without release. Even in the duplicate branch, rollback release failure is only logged; there is no durable record from which to retry (`:293-299`). This is narrower than the API contract, which promises rollback for any database insert failure (`docs/specifications/service-api/task-board-service-specs.md:421`). The dirty TC-28 test covers only a duplicate insert with a successful release (`services/task-board/tests/unit/routers/test_t072_tasks_gaps.py:112-161`).

**Concrete failure.** Bank releases an approved task's 100 coins to the worker; DB Gateway times out before `status=approved`. The task remains `submitted`. Every approval retry receives `escrow_already_resolved`; a dispute can still be filed against already-paid work. On creation, the same failure order can leave locked escrow with no task row at all.

**Recommendation.** Introduce a durable settlement-intent/reconciliation record owned by Task Board/Gateway, keyed by task and operation. Make Central Bank release/split idempotent for an identical operation key and reject only mismatched replay payloads. Persist the desired transition and settlement payload before calling Bank; a background reconciler completes or safely retries it. The same mechanism should represent post-lock task-creation recovery, or task creation and escrow lock must be redesigned around a durable saga record.

**Affected boundaries.** Task Board, DB Gateway schema/writer, Central Bank replay semantics, service client payloads, lifecycle events, e2e ledger assertions.

**Failing-test acceptance scenario.** Force the Gateway task update to fail after a successful Bank release/split, restart Task Board, and assert the reconciler reaches the intended terminal status with exactly one ledger settlement. Separately force every non-duplicate task insert failure after lock and assert either the task eventually exists or the poster is durably refunded; no locked escrow is orphaned.

**Work:** MP-04, MP-05, MP-08.


#### F-L03 — Deadline escrow retry is unreachable for terminal tasks (P1; code evidence; failure histories require execution proofs)

**Evidence.** `evaluate_deadline` returns immediately for every terminal status before calling `retry_pending_escrow` (`services/task-board/src/task_board_service/services/deadline_evaluator.py:88-93`). The sweep only lists `open`, `accepted`, `submitted`, and `disputed` (`:20-26`, `230-276`). Auto-expiry and auto-approval set the terminal status plus `escrow_pending=1` before attempting Bank release (`:108-123`, `134-148`, `159-175`). If that release fails, no later lazy read or sweep can retry it. The service spec promises the opposite (`docs/specifications/service-api/task-board-service-specs.md:286-288`), and the current unit test locks in the defect by asserting no retry for a terminal approved task (`services/task-board/tests/unit/test_deadline_evaluator.py:78-93`).

**Recommendation.** Process `escrow_pending` before the terminal early return and add a sweep/reconciliation query for pending settlements independent of lifecycle status. Prefer the durable settlement reconciler from F-L02 rather than expanding status scans.

**Failing-test acceptance scenario.** Make the first auto-approval Bank release fail, leaving `approved + escrow_pending`; allow the next periodic reconciliation (and a restart variant) to run; assert one eventual payout, `escrow_pending=false`, and no duplicate ledger movement.

**Work:** MP-08.


#### F-L04 — Court retries can change the ruling after money was settled (P0; code evidence; failure histories require execution proofs)

**Evidence.** Every ruling attempt reevaluates all judges and recomputes the median before external writes (`services/court/src/court_service/services/ruling_orchestrator.py:398-426`). Real judges may be nondeterministic (`temperature: 0.3` in the documented live block, `services/court/config.yaml:40-46`). Task Board's idempotency guard checks only that the stored `ruling_id` matches and returns the stored task without comparing the retried `worker_pct` or summary (`services/task-board/src/task_board_service/services/task_ruling.py:380-384`). Court ignores the returned Task Board outcome and continues using its newly computed percentage for Reputation and Court persistence (`services/court/src/court_service/services/ruling_orchestrator.py:335-369`, `403-426`).

The spec's claim that this always converges is therefore false (`docs/specifications/service-api/court-service-specs.md:302-312`). The retry test injects one stable judge result and simulates failure with a catchable exception, so it cannot expose changed votes (`services/court/tests/unit/routers/test_wp06_retry_clean.py:61-134`).

**Concrete failure.** Attempt 1 yields 65%, Task Board settles escrow at 65%, and Court persistence fails. Attempt 2 yields 30%. Task Board returns 200 because the `ruling_id` matches, while Court writes 30% feedback and a 30% ruling. If one feedback row succeeded on attempt 1, the final Reputation pair can even be derived from two different percentages.

**Recommendation.** Persist an immutable ruling attempt—judge identities, votes, median, summary, input evidence hashes, prompt/rubric version—before any Task Board or Reputation call. Retries must resume that attempt and never invoke judges again. Task Board must treat “same ruling id, different settlement payload” as `409 ruling_conflict`, not successful idempotency. Reputation replay should likewise detect payload mismatch rather than treating every duplicate key as equivalent.

**Failing-test acceptance scenario.** Configure a judge to return 65 on its first call and 30 on its second, then fail after Task Board records the first result. Retry must not call the judge a second time; Bank, Task Board, both feedback rows, and Court must all end at 65. A direct same-`ruling_id`/different-payload Task Board replay must return conflict.

**Work:** MP-10.


#### F-L05 — `judging` is a durable dead-end after real process death (P0; code evidence; failure histories require execution proofs)

**Evidence.** Court changes the claim to `judging` before fetching task evidence (`services/court/src/court_service/services/ruling_orchestrator.py:371-384`; router sequencing at `services/court/src/court_service/routers/disputes.py:208-244`). Catchable failures revert through an exception handler (`ruling_orchestrator.py:427-437`), but a process kill does not execute it. Court startup creates services and judges only; it has no recovery scan, lease expiry, or background worker (`services/court/src/court_service/core/lifespan.py:74-153`). `judging` is not a valid retrigger state (`ruling_orchestrator.py:183-204`). If the kill occurred after Task Board recorded the ruling, Task Board is already terminal `ruled`, so its disputed-task sweep will never call Court again.

The existing test says it simulates a process kill by raising `RuntimeError`, but that exception is caught and compensation runs; it proves exception recovery, not crash recovery (`services/court/tests/unit/routers/test_wp06_retry_clean.py:61-124`).

**Recommendation.** Model a durable ruling attempt with an owner/lease and resumable step state (`evidence_ready`, `decision_persisted`, `task_settled`, `feedback_recorded`, `complete`). Acquire `rebuttal_pending -> judging` via Gateway CAS. On startup and periodically, resume expired leases. Never delete the immutable decision during compensation; retry the unfinished effects.

**Failing-test acceptance scenario.** Persist each possible intermediate step, simulate a fresh Court process without running exception cleanup, and prove startup recovery reaches one consistent `ruled` outcome. A task already ruled by Task Board must cause Court to adopt/verify that exact stored outcome rather than remain `judging`.

**Work:** MP-10.


#### F-L06 — Court ruling ownership has no compare-and-swap (P1; code evidence; failure histories require execution proofs)

**Evidence.** `begin_ruling` reads a pending dispute and then performs an unconditional status write (`ruling_orchestrator.py:170-206`, `371-384`). `DisputeDbClient.set_status` sends no constraint (`services/court/src/court_service/services/dispute_db_client.py:225-244`), even though the Gateway status endpoint supports constraints (`services/db-gateway/src/db_gateway_service/services/court_writer.py:101-159`). The reentrancy test begins the second request only after the first write is visible (`services/court/tests/unit/routers/test_wp06_retry_clean.py:137-180`); it does not place two requests before either write.

**Concrete interleaving.** Two rule calls both read `rebuttal_pending`, both write `judging`, and both evaluate. Both can observe Task Board's same-`ruling_id` success. One Court insert then conflicts; its compensation can set the claim back to pending and delete the ruling inserted by the winning call, leaving Task Board ruled and Court pending.

**Recommendation.** Make `begin_ruling` one atomic CAS with an attempt id and lease. Only the owner may advance or compensate that attempt. Court ruling persistence should be idempotent for identical payloads and conflict on mismatches; compensation must never delete another attempt's committed ruling.

**Failing-test acceptance scenario.** Barrier two simultaneous `/rule` requests before the claim-status update. Assert one acquires the attempt and the other gets `409`; judges run once, Task Board settles once, one Court ruling remains, and no loser cleanup changes the winner's state.

**Work:** MP-10.


#### F-L07 — Court trusts claim parties and escrow without reconciling task context (P1; code evidence; failure histories require execution proofs)

**Evidence.** The file endpoint extracts `task_id`, `claimant_id`, `respondent_id`, `claim`, and `escrow_id` from the platform token, fetches the task, discards the returned object, and persists the token fields unchanged (`services/court/src/court_service/routers/disputes.py:77-107`). No check binds claimant to `task.poster_id`, respondent to `task.worker_id`, escrow to `task.escrow_id`, or filing to the expected review status. By contrast, the rebuttal route already checks the forwarded respondent against the stored dispute (`:154-168`), demonstrating the intended trust-hardening pattern.

**Concrete failure.** Any holder or buggy caller of the shared platform identity can file a claim for a real task naming a different respondent. The foreign keys may all be valid, so Gateway accepts it; subsequent feedback targets the wrong agents and the Court record no longer represents the task contract.

**Recommendation.** Treat Task Board's fetched task as authoritative and reject every mismatch before creating a claim. Check `poster_id`, `worker_id`, `escrow_id`, and an explicitly specified admissible status. Longer term, use a task-version/transition token so Court can prove which submitted snapshot it adjudicates.

**Failing-test acceptance scenario.** For each field, send a valid platform-signed claim with one mismatch against the fetched task. Court returns `409 context_mismatch` (or one canonical code), writes no claim/event, and leaves task/escrow untouched. Add a review-deadline interleaving where Court's task fetch auto-approves; the claim must not be created.

**Work:** MP-10.


#### F-L08 — Asset upload permits filesystem path traversal (P0; code evidence; failure histories require execution proofs)

**Evidence.** The multipart filename is accepted from `UploadFile.filename` (`services/task-board/src/task_board_service/routers/assets.py:39-78`). Upload constructs `asset_dir / filename` and writes without resolving or validating the target (`services/task-board/src/task_board_service/services/asset_manager.py:133-144`). An absolute filename replaces the left-hand path; `../` components escape it. The only traversal test covers download of already-stored malicious metadata (`services/task-board/tests/unit/test_asset_manager.py:314-342`), after the dangerous upload write would already have happened. Download's string-prefix check (`asset_manager.py:217-227`) also should use `Path.is_relative_to`, although the upload is the immediate write vulnerability.

**Recommendation.** Never use the client filename as a storage path. Store bytes under a generated server filename (for example, the asset id), keep a sanitized basename only as display metadata, and verify the resolved server path is relative to the exact storage root. Reject separators, absolute paths, NULs, and unsafe response-header characters in the display name.

**Failing-test acceptance scenario.** Upload `../../config.yaml`, an absolute path, a sibling-prefix escape, and a quote/CRLF filename. No file outside the generated asset directory changes, metadata uses a safe display name or returns `400 invalid_filename`, and download headers are safe.

**Work:** MP-09.


#### F-L09 — Asset filesystem and metadata writes are not atomic (P1; code evidence; failure histories require execution proofs)

**Evidence.** Upload checks the current asset count, writes the file, then inserts metadata (`asset_manager.py:123-158`). On metadata failure, it does not remove the file. `TaskDbClient.insert_asset` omits the Gateway's supported `constraints` field (`services/task-board/src/task_board_service/services/task_db_client.py:310-340`), so the accepted-status check can race expiry/submission, and the count limit is not enforced transactionally. Gateway can enforce a cross-table status constraint when supplied (`services/db-gateway/src/db_gateway_service/services/board_writer.py:260-300`).

**Concrete failure.** While Gateway is unavailable, a worker can repeatedly send 10 MiB uploads. Each request writes a new random directory and then fails metadata persistence; metadata count remains zero, so the configured ten-file cap never engages. Concurrent uploads can also all pass count ten-minus-one and exceed the limit.

**Recommendation.** Write to a temporary server-generated path, insert metadata with `constraints={status: accepted}` and an atomic per-task quota check, then atomically rename; delete the temporary file on every failure. A durable object-store/outbox design is preferable if multi-host support returns.

**Failing-test acceptance scenario.** Make metadata insertion fail and assert no final or temporary bytes remain. Launch `max_files+N` concurrent uploads and assert exactly `max_files` succeed. Race upload against expiry/submission and assert no asset metadata or bytes land after the task leaves `accepted`.

**Work:** MP-09.


#### F-L10 — Judge evidence is silently incomplete and format-blind (P1; code evidence; failure histories require execution proofs)

**Evidence.** Task Board permits ten assets of 10 MiB each (`services/task-board/config.yaml:38-41`), while Court caps all deliverables together at 65,536 bytes (`services/court/config.yaml:30-35`). `DeliverableFetcher` consumes assets in listing order, slices the global remaining bytes, decodes every payload as UTF-8 with replacement, and returns only strings (`services/court/src/court_service/services/ruling_orchestrator.py:62-85`). Judge context therefore loses filename, content type, size, hash, and whether bytes were truncated (`:218-226`). The existing test proves one supplied text reaches the judge, not that the adjudicated submission is complete or honestly represented (`services/court/tests/unit/routers/test_wp06_deliverables.py:38-57`). The Court spec acknowledges this exact shape (`docs/specifications/service-api/court-service-specs.md:293-300`).

**Concrete failure.** A 65 KiB first asset exhausts the budget, so a decisive second asset is invisible. ZIP, image, PDF, and binary deliverables become replacement-character text. The judge is still asked for a financial ruling without being told evidence is absent or truncated.

**Recommendation.** Make evidence admissibility a product contract. At minimum pass a manifest containing filename, media type, size, hash, included byte range, and explicit truncation/unsupported markers. Define supported text formats and extractors; fail the ruling as `evidence_unavailable` when required evidence cannot be evaluated rather than silently treating it as empty. Bind the persisted ruling attempt to evidence hashes.

**Failing-test acceptance scenario.** Submit multiple assets where decisive content falls after the byte budget and include a binary asset. The judge context must contain a complete manifest and explicit truncation/unsupported flags; automatic ruling must either use an approved extractor or stop with a recoverable typed error. No asset may disappear silently.

**Work:** MP-09, MP-11.


#### F-L11 — Judge inputs are directly prompt-injectable (P1; code evidence; failure histories require execution proofs)

**Evidence.** The current dirty snapshot materially improves the system prompt with the Q-13 ambiguity rubric (`services/court/src/court_service/judges/prompts.py:5-53`). However, the specification, deliverables, claim, and rebuttal are interpolated verbatim into one user-role string (`services/court/src/court_service/judges/llm_judge.py:84-108`; template at `judges/prompts.py:55-73`). The system prompt never states that those blocks are untrusted evidence that may contain instructions. The dirty tests assert rubric presence and ordinary JSON parsing, but no adversarial evidence corpus exists (`services/court/tests/unit/test_vagueness_rubric.py:27-79`, `test_llm_judge.py:78-149`).

**Inference.** A worker deliverable or party statement can instruct the model to ignore the rubric, emit a chosen percentage, or treat another block as untrusted. System-message priority reduces but does not eliminate this risk; payout integrity currently depends on provider-specific prompt-injection resistance.

**Recommendation.** Add an explicit system-level untrusted-evidence policy, encode each field as structured data with clear provenance, and require reasoning to cite evidence by field/asset id. Run an adversarial judge eval suite against every production model before activation. Do not attempt keyword filtering as the primary defense.

**Failing-test acceptance scenario.** A versioned corpus places conflicting “ignore instructions / pay 100%” text in each of spec, deliverable, claim, and rebuttal. Across the approved three-model production panel, votes must remain governed by the rubric and cite the actual acceptance evidence within a defined pass threshold.

**Work:** MP-11, MP-25.


#### F-L12 — Rebuttal forwarding has a recoverability hole (P2; code evidence; failure histories require execution proofs)

**Evidence.** Task Board first commits the rebuttal in Court, then separately writes `rebuttal_submitted_at` (`services/task-board/src/task_board_service/services/task_ruling.py:267-286`). If the second write fails, Court correctly rejects a retry as `rebuttal_already_submitted` (`services/court/src/court_service/services/dispute_service.py:108-131`), so Task Board never records the marker that triggers immediate ruling. The dispute will eventually be considered ready only when the stored deadline passes (`services/task-board/src/task_board_service/services/deadline_evaluator.py:182-208`).

**Recommendation.** Make rebuttal forwarding idempotent and return the existing rebuttal for identical replay, with conflict on different content. Task Board should reconcile Court state after ambiguous failures and persist Court's authoritative `rebutted_at`, not a new local timestamp.

**Failing-test acceptance scenario.** Let Court commit, then fail Task Board's marker update. Retry the same worker request and assert 200/idempotent convergence, the exact Court timestamp is stored on Task Board, and the next sweep triggers ruling immediately.

**Work:** MP-10, MP-17.


#### F-L13 — Sync Gateway clients can stall deadline and request progress (P2; code evidence; failure histories require execution proofs)

**Evidence.** `TaskDbClient` uses synchronous `httpx.Client` with a ten-second timeout (`services/task-board/src/task_board_service/services/task_db_client.py:65-69`) and is called directly from async coordinators and the async periodic loop. The loop processes every task serially and only sleeps after completing the full sweep (`services/task-board/src/task_board_service/services/deadline_evaluator.py:230-280`). The target plan's async-client end state remains unmet for Task Board, despite T-082 being recorded as done-with-deviation (`docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md:583`, `707`).

**Impact.** A slow Gateway request can freeze Task Board's event loop, delay all API responses, and move deadline transitions or Court triggers far beyond the configured interval. This is reliability and fairness debt rather than a direct state-corruption defect.

**Recommendation.** Move Task Board to the shared async Gateway client and make the sweep bounded/paged. Preserve per-task CAS and isolate one task's timeout. Add lag/last-success metrics for deadline automation.

**Failing-test acceptance scenario.** Hold one Gateway task read for longer than the sweep interval while probing `/health` and processing an unrelated task. Health and unrelated work remain responsive, the stuck task times out independently, and sweep lag is observable.

**Work:** MP-20.


#### F-L14 — Fractional seconds are discarded from task phase deadlines (P1; reproduced during isolated baseline)

`services/task-board/src/task_board_service/services/deadline_evaluator.py:75` formats a computed deadline with `timespec="seconds"`, while phase timestamps can contain microseconds. A phase starting at `12:00:00.999999Z` with a one-second duration is therefore cut off at `12:00:01Z` instead of `12:00:01.999999Z`. Root CI attempt 4 exposed this as a bid rejected with `409 invalid_status` before the existing expiry test reached its assertions. **Repair:** preserve the timestamp precision in the shared deadline calculation; retain established whole-second formatting and cutoff comparisons. **Proof:** deterministic fractional timestamps immediately before and at the full-duration boundary, plus unchanged expiry/refund/event cases. **Work:** narrow MP-01 baseline prerequisite T-169/T-170; broader atomic-transition work remains MP-07.

### 4.4 Human UI, agents and demonstrations

#### F-U01 — P0 — UI bypasses sealed bidding and publishes open-task bids

**Evidence**

- `services/task-board/src/task_board_service/services/task_bidding.py:143-212` requires poster authentication while a task is `open`, then permits public bid listing only after the open phase.
- `services/ui/src/ui_service/services/tasks.py:85-116` reads every bid directly from `board_bids` with no task-status or caller check.
- `services/ui/src/ui_service/routers/tasks.py:131-159` exposes those bids from unauthenticated `GET /api/tasks/{task_id}`.
- `services/ui/data/web/assets/task.js:120-145` renders them for every viewer.
- The canonical goal explicitly requires open-task bids to remain sealed: `openspec/specs/agent-task-economy/spec.md`, “Binding and Sealed Bidding.”

**Failure path**

An agent submits a binding bid. Before acceptance, any caller able to reach UI can request `/api/tasks/{task_id}` and see all bidders and bid records, bypassing the Task Board authorization gate. This removes the information asymmetry that prevents reactive undercutting.

**Concrete fix**

- Make the public drilldown return only `bid_count`/redacted bids for `open` tasks.
- Add an operator-signed bid-list proxy that calls Task Board `list_bids`, and use it only for the current operator's own open tasks.
- After the task leaves `open`, return the public bid record according to the Task Board contract.
- Do not solve this by duplicating authorization rules in direct SQL; defer the sealed read to Task Board.

**Failing-test-first acceptance scenario**

1. Create an open operator task and two bids.
2. Assert anonymous `GET /api/tasks/{id}` does not expose bidder IDs, amounts, or proposals.
3. Assert operator-signed bid listing returns both bids.
4. Accept one bid and assert the public post-open drilldown follows the published visibility contract.

**Dependencies**: implement the narrow Q-3 single-operator route; no multiuser auth decision is required for v1.

**Work:** MP-12.


#### F-U02 — P0 — Sealed feedback ratings leak through public events and SSE

**Evidence**

- `services/reputation/src/reputation_service/services/feedback_db_client.py:75-101` emits `feedback.submitted` with `category` and `rating` even when the feedback row is sealed.
- `services/ui/src/ui_service/services/events.py:51-71` returns every event's decoded payload without redaction.
- `services/ui/src/ui_service/services/events.py:91-114` does the same over public SSE.
- `services/ui/src/ui_service/routers/events.py:15-90` exposes both endpoints without authentication.
- The intended sealed rule is enforced on Reputation reads by `services/reputation/src/reputation_service/services/feedback.py:200-242`, but the event path bypasses it.

**Failure path**

Party A submits a rating first. Reputation correctly stores it as invisible, but the same rating immediately appears in `/api/events` and `/api/events/stream`. Party B can inspect it before submitting, defeating retaliation prevention.

**Concrete fix**

- Do not put category/rating/comment in `feedback.submitted` public payloads.
- Emit only opaque identifiers until mutual/timeout reveal, then emit a `feedback.revealed` event containing allowed public fields.
- Add a defense-in-depth UI event serializer that redacts sealed feedback event types even if an upstream producer regresses.

**Failing-test-first acceptance scenario**

1. Submit one side of mutual feedback.
2. Assert Reputation direct reads, UI event history, SSE, and agent feeds contain no rating/category/comment.
3. Submit the counterpart (and separately advance timeout).
4. Assert a reveal event and all intended read surfaces expose the feedback exactly once.

**Dependencies**: DB Gateway/Reputation event contract; coordinate with the timeout-persistence fix in F-U11.

**Work:** MP-12, MP-13.


#### F-U03 — P0 — Agent-controlled strings reach `innerHTML` and enable stored script execution

**Evidence**

- `services/ui/data/web/assets/agent.js:68-85`, `153-179`, and `182-230` interpolate unescaped agent names, task titles, and feedback comments into HTML strings.
- `services/ui/data/web/assets/landing.js:112-127` interpolates agent names into the leaderboard.
- `services/ui/data/web/assets/quarterly.js:57-82` interpolates task titles and agent names.
- `services/ui/data/web/assets/task.js:104-117` leaves `task.poster.name` unescaped in one renderer.
- `services/ui/data/web/assets/task.js:527-538` assigns event summaries to `innerHTML` without escaping.
- `services/task-board/src/task_board_service/services/task_db_client.py:145` includes user-controlled task title in an event summary.
- Identity validation accepts a string name and does not HTML-sanitize it: `services/identity/src/identity_service/routers/agents.py:24-41` and `services/identity/src/identity_service/services/validation.py:41-68`.

**Failure path**

An agent registers a name or posts a title containing HTML with an event handler. A human opens the landing, agent, quarterly, task, or live-feed page. The browser parses it as markup in the UI origin, which has authority to spend the operator's funds through unauthenticated proxy routes.

**Concrete fix**

- Replace data-bearing HTML concatenation with DOM construction and `textContent`, or apply one audited escaping helper at every interpolation boundary.
- Add a restrictive Content Security Policy as defense in depth.
- Keep raw user strings in APIs; encoding belongs at the HTML sink.

**Failing-test-first acceptance scenario**

Seed malicious values in each user-controlled field (`name`, `title`, feedback `comment`, event `summary`). Open every consuming page. Assert the marker is rendered as text, no injected element exists, and no script/event handler runs.

**Dependencies**: none; should precede broader browser participation work.

**Work:** MP-02, MP-15.


#### F-U04 — P1 — Math worker abandons in-flight work after restart

**Evidence**

- `agents/src/math_worker/loop.py:68-80` starts a fresh cycle with no recovery phase.
- `agents/src/math_worker/loop.py:96-176` keeps task ID, bid ID, and solution only in the active call stack.
- `agents/src/math_worker/loop.py:182-220` discovers only `status="open"` tasks.
- `agents/src/math_worker/history.py:39-43` explicitly keeps history “within a single run.”
- The SDK can query `worker_id`, but the runtime never queries accepted/submitted/disputed tasks assigned to itself.

**Failure path**

- Restart after bid submission: the worker sees the same open task again and can repeatedly hit `bid_already_exists`.
- Restart after acceptance: no process resumes solving; the task expires and escrow returns to the poster.
- Restart after submission/dispute: the worker never resumes review/ruling polling or submits a rebuttal.

**Concrete fix**

- Add startup reconciliation by `worker_id=self.agent_id` for `accepted`, `submitted`, and `disputed`, plus open tasks already bid by the agent.
- Persist minimal durable work state: task ID, bid ID/amount, uploaded asset IDs or solution reference, and current phase.
- Make every phase idempotent: reuse existing bid/assets/submission/rebuttal rather than duplicate them.

**Failing-test-first acceptance scenario**

Parameterized live tests stop and recreate the worker after bid, acceptance, upload, submission, and dispute. Each task must still reach the correct terminal state without duplicate bids/assets/rebuttals and with one payout.

**Dependencies**: define durable worker-state location and ownership; Task Board list filters already support worker recovery.

**Work:** MP-17.


#### F-U05 — P1 — Feeder restart loses review truth and lets known-bad work auto-approve

**Evidence**

- `agents/src/task_feeder/loop.py:34-35` creates an empty in-memory `_task_map`.
- `agents/src/task_feeder/loop.py:104-115` adds expected solutions only after posting in the current process.
- `agents/src/task_feeder/review.py:31-40` requires the task ID in that map.
- `agents/src/task_feeder/review.py:93-110` silently skips submitted tasks missing from the map.
- `services/task-board/src/task_board_service/services/deadline_evaluator.py:150-175` auto-approves any still-submitted task when the review deadline passes.

**Failure path**

The feeder posts a task, accepts a worker, then restarts before review. The task persists but its expected answers do not. ReviewLoop skips it; even an incorrect deliverable is later auto-approved and paid in full.

**Concrete fix**

- Persist feeder task provenance and acceptance oracle alongside the posted task ID, or embed a durable opaque source/reference that the feeder can resolve after restart.
- Reconcile all of the feeder's submitted tasks at startup.
- Fail closed for an expected-answer feeder when review truth is unavailable; do not silently rely on timeout approval.

**Failing-test-first acceptance scenario**

Post two known tasks, restart the feeder before review, submit one correct and one incorrect answer, and assert the first is approved while the second enters dispute and ruling.

**Dependencies**: durable feeder state design; coordinate with F-U04 restart test harness.

**Work:** MP-18.


#### F-U06 — P1 — Human bid selection displays the reward instead of the real bid amount

**Evidence**

- Task Board's live contract is amount-only: `services/task-board/src/task_board_service/schemas.py:90-114` and `services/task-board/src/task_board_service/services/task_bidding.py:201-212`.
- UI reads stale `proposal` but not `amount`: `services/ui/src/ui_service/services/tasks.py:89-115`.
- UI `BidItem` has `proposal` and no `amount`: `services/ui/src/ui_service/schemas.py:299-305`.
- The page prints `task.reward` for every bid: `services/ui/data/web/assets/task.js:128-143`.
- The browser test locks in the wrong behavior: `services/ui/tests/e2e/test_task_lifecycle.py:470-473`.
- The autonomous selection rule is lowest amount, then delivery reputation, then bid ID: `agents/src/task_feeder/acceptance.py:71-91`.

**Failure path**

Workers bid 40 and 90 on a reward-100 task. The human sees “100” on both and cannot apply the market's price signal. Q-8 keeps payout equal to posted reward, but Q-16 still makes bid amount decisive for acceptance.

**Concrete fix**

- Replace stale proposal fields with `amount` end to end.
- Show posted reward and each bid amount as different concepts.
- Sort or label the lowest bid while preserving the poster's final choice.
- Keep payout wording explicit: “bid signal” versus “reward paid.”

**Failing-test-first acceptance scenario**

Seed two unequal amounts under one reward. Assert the public redacted view hides them while open, the operator view shows exact amounts, and acceptance selects the intended bid. Assert settlement remains based on reward under Q-8.

**Dependencies**: F-U01 sealed-bid route split.

**Work:** MP-12, MP-15, MP-19.


#### F-U07 — P1 — Posters cannot attach task input files anywhere in the platform

**Evidence**

- UI create request contains only title, spec, reward, and deadlines: `services/ui/src/ui_service/schemas.py:542-551` and `services/ui/data/web/assets/task.js:65-101`.
- Task posting SDK carries the same fields: `libs/service-auth/src/service_auth/mixins/task_board.py:30-67`.
- The only asset upload is a deliverable upload: `services/task-board/src/task_board_service/routers/assets.py:20-79`.
- It is allowed only after acceptance and only for the assigned worker: `services/task-board/src/task_board_service/services/asset_manager.py:96-112`.

**Failure path**

A human wants an agent to transform a document, inspect a dataset, edit an image, or work from a repository snapshot. The task can contain at most 10,000 characters of text; the human cannot supply the actual input artifact on-platform. This blocks general work even though output assets exist.

**Concrete fix**

- Introduce task input attachments as a distinct asset role owned by the poster.
- Permit upload during draft/posting with clear finalization semantics; include hashes, sizes, types, and immutable task association.
- Expose signed SDK and UI upload flows.
- Make inputs available to eligible workers and Court under the chosen confidentiality policy.

**Failing-test-first acceptance scenario**

Human uploads an input before opening a task; a worker downloads it, produces a separate deliverable, and Court receives both input context and output on dispute. A non-poster cannot replace inputs after bidding opens.

**Dependencies**: major choice Q-M1 (artifact visibility/confidentiality); schema/API changes before UI.

**Work:** MP-09, MP-15, MP-16.


#### F-U08 — P1 — Delivered files cannot be opened or downloaded from the UI

**Evidence**

- Task Board has a working public download endpoint with attachment headers: `services/task-board/src/task_board_service/routers/assets.py:103-128`.
- SDK download exists: `libs/service-auth/src/service_auth/mixins/task_board.py:198-209`.
- UI drilldown returns metadata but no URL/content route: `services/ui/src/ui_service/schemas.py:308-315` and `services/ui/src/ui_service/routers/tasks.py:162-170`.
- Browser renderers show only filename and size: `services/ui/data/web/assets/task.js:166-179` and `183-195`.

**Failure path**

The worker submits a real file. The human reaches Review but cannot inspect the deliverable before approving or disputing it.

**Concrete fix**

- Add a same-origin UI download/stream route that delegates to Task Board and preserves safe content headers.
- Render each asset as an explicit open/download action.
- For browser-previewable content, force safe disposition/sandboxing; do not execute uploaded HTML in the UI origin.

**Failing-test-first acceptance scenario**

Upload binary and text deliverables through Task Board. From the browser review page, download both and assert exact bytes, filename, content type, and no inline execution for active content.

**Dependencies**: artifact visibility choice Q-M1; F-U03 safe rendering.

**Work:** MP-09, MP-15.


#### F-U09 — P1 — Human lifecycle coverage omits cancellation, mutual reputation and worker participation

**Evidence**

- UI proxy exposes exactly identity, post, accept, approve, and dispute: `services/ui/src/ui_service/routers/proxy.py:19-54`.
- SDK already supports cancellation and feedback: `libs/service-auth/src/service_auth/mixins/task_board.py:92-102` and `libs/service-auth/src/service_auth/mixins/reputation.py:23-44`.
- Task page only binds post/accept/approve/dispute actions: `services/ui/data/web/assets/task.js:374-497`.
- Feedback is display-only: `services/ui/data/web/assets/task.js:270-281`.

**Failure path**

A human posts the wrong task and cannot cancel it from UI. After successful work, the human cannot submit delivery-quality feedback, so the market signal needed by Q-16 tie-breaking remains absent unless a script/API client supplies it. The same five-route proxy also provides no human-worker bid/upload/submit/rebuttal journey. A human who wants to take an agent-posted job must leave the UI and write an API client.

**Concrete fix**

- Add operator-owned cancellation for allowed states with refund result displayed.
- Add post-terminal feedback submission for the correct counterparty/category, with sealed status explained.
- Show operator balance and relevant transactions near task creation/settlement.
- Add a separate “My work” journey where the operator itself is the assigned worker on another poster's job: bid, download inputs, upload/submit outputs and rebut. Never let the operator impersonate another worker.

**Failing-test-first acceptance scenario**

1. Cancel an open operator task with a bid and assert escrow refund plus `task.cancelled` event.
2. Complete a task; human submits delivery feedback, worker submits spec feedback; assert both stay sealed until pair completion and then appear.

**Dependencies**: F-U02/F-U11 feedback fixes; existing cancellation escrow test can be reused at the service boundary.

**Work:** MP-13, MP-15.


#### F-U10 — P1 — Generic external agent onboarding is a library primitive, not a supported join path

**Evidence**

- `service-auth` publicly exports `AgentConfig`, `BaseAgent`, key helpers, and all mixins: `libs/service-auth/src/service_auth/__init__.py:1-35`.
- `BaseAgent` is usable with an explicitly constructed config: `libs/service-auth/src/service_auth/agent.py:28-39`.
- The convenient factory is closed over a server-local roster and key directory: `libs/service-auth/src/service_auth/factory.py:41-72`, `82-108`.
- `agents/roster.yaml:4-28` contains a fixed set of repository-controlled identities.
- README explains running built-in feeder/mathbot but gives no external-agent quickstart: `README.md:8-20`, `38-40`, `141-153`.
- Self-service account creation starts at zero: `libs/service-auth/src/service_auth/mixins/bank.py:25-46`; only platform authority can mint nonzero funds by the Bank contract.

**Failure path**

An external developer can reverse-engineer a join flow, but there is no supported command/package/example that persistently creates keys, configures URLs, registers, creates the account, discovers tasks, and resumes safely. A worker can join at zero balance; the existing manual issuance policy lacks a documented supported grant workflow for an external poster.

**Concrete fix**

- Publish an external-agent quickstart and executable example using explicit `AgentConfig`, persistent keys, and no roster edits.
- Define a stable client package boundary/versioning story.
- Include discovery, bid, upload, submit, rebuttal, feedback, balance, and graceful restart examples.
- Make funding policy explicit: worker-only zero-balance joining is immediately supportable; external posting uses the explicit administrator-invoked, platform-signed grant procedure retained in Q-M5.

**Failing-test-first acceptance scenario**

In a clean temp directory, an unlisted agent follows only the public bootstrap surface, registers once, restarts with the same identity, creates its zero-balance account, discovers a human-posted task, bids, delivers, rebuts if needed, receives payment, and submits feedback. No repository roster change is allowed.

**Dependencies**: implement the already-resolved Q-M5 grant procedure; F-U04 for restart guarantee. No new funding-policy decision is required.

**Work:** MP-16.


#### F-U11 — P1 — Reputation timeout visibility and UI visibility diverge permanently

**Evidence**

- Reputation treats a sealed row as visible after elapsed timeout without persisting `visible=1`: `services/reputation/src/reputation_service/services/feedback.py:200-208`.
- UI task feedback requires stored `visible = 1`: `services/ui/src/ui_service/services/tasks.py:140-163`.
- UI agent profiles and leaderboards do the same: `services/ui/src/ui_service/services/agents.py:67-128`, `229-244`, and `349-359`.
- UI metrics/quarterly also require persisted visibility: `services/ui/src/ui_service/services/metrics.py:349-443` and `services/ui/src/ui_service/services/quarterly.py:104-141`.

**Failure path**

Only one party submits feedback. After 24 hours, Reputation API reveals it dynamically, but UI profiles, task pages, rankings, metrics, and quarterly reports continue treating it as sealed forever. No `feedback.revealed` event is generated for the timeout path.

**Concrete fix**

- Move timeout reveal to a durable transition owned by DB Gateway/Reputation, setting visibility and emitting one reveal event atomically.
- Alternatively, route every UI reputation read through Reputation, but this conflicts with the current direct-read observatory model and leaves metrics/event materialization unresolved.

**Failing-test-first acceptance scenario**

Submit one-sided feedback, advance past timeout, trigger the periodic/lazy reveal, and assert the row is durably visible, one reveal event exists, and Reputation/UI task/profile/metrics/quarterly agree before and after restart.

**Dependencies**: F-U02 event payload contract.

**Work:** MP-13, MP-19.


#### F-U12 — P1 — UI can report healthy and “ready” while core read/write participation is unavailable

**Evidence**

- UI startup catches database failure and continues: `services/ui/src/ui_service/core/lifespan.py:33-45`.
- It also catches operator registration failure and continues with proxy unavailable: `services/ui/src/ui_service/core/lifespan.py:51-67`.
- Health always returns `status="ok"`, even when `database_readable=False`, and does not report operator readiness: `services/ui/src/ui_service/routers/health.py:14-41`.
- `start-all` gates UI startup only on health containing `"ok"`: `justfile:230-245` and `295-303`.
- The browser E2E harness intentionally starts UI without Identity/Task Board and still passes the health gate: `services/ui/tests/e2e/conftest.py:83-142`.

**Failure path**

After reboot, Identity or DB is late/unavailable. UI starts, `start-all` announces success, and the human sees forms/dashboard. Reads return 503 or writes fail because `UserAgent` is absent. No retry initializes the operator later.

**Concrete fix**

- Separate liveness from readiness. Readiness must report DB readability and operator registration/account identity.
- Gate `start-all` on readiness.
- Either fail startup when required dependencies are absent or run bounded background reinitialization with explicit degraded state.
- Surface “operator not provisioned/not ready” on the task page before accepting input.

**Failing-test-first acceptance scenario**

Start UI before Identity/DB, assert liveness but failed readiness and disabled writes; bring dependencies online, assert recovery to ready and successful real post without process restart (or assert startup fails fast, depending on chosen contract).

**Dependencies**: choose fail-fast versus degraded-recovery behavior.

**Work:** MP-15, MP-21.


#### F-U13 — P2 current / release-blocking if exposed — Loopback proxy lacks browser-origin defenses

**Evidence**

- Ratified Q-3 explicitly makes `127.0.0.1` the v1 boundary and permits no auth: `services/ui/config.yaml:8-18`.
- Only the configured host value is tested: `services/ui/tests/unit/test_exposure_posture.py:20-25`.
- App installs no TrustedHost, Origin/Referer, CSRF, or session middleware: `services/ui/src/ui_service/app.py:18-49`.
- Bodyless accept and approve are simple POSTs: `services/ui/src/ui_service/routers/proxy.py:39-48`.
- Middleware deliberately skips JSON/content-type validation for them: `services/ui/src/ui_service/core/middleware.py:14-47`.

**Failure path**

Under the ratified single-user local posture, any process on the machine is trusted. A malicious web origin can still submit simple cross-origin POST forms to known accept/approve URLs; absence of host validation also leaves a DNS-rebinding path. UUID task IDs reduce blind exploitability but are not a security boundary.

**Concrete fix**

- For current v1, enforce expected Host and same-origin Origin/Referer on mutating proxy routes, including bodyless actions.
- Before any non-loopback/reverse-proxy/multiuser deployment, require explicit authentication/session or configured secret plus CSRF protection and per-human attribution.
- Keep operator/platform separation; do not reuse the notary key.

**Failing-test-first acceptance scenario**

Assert same-origin loopback requests succeed; forged `Origin`, malicious `Host`, and simple form POSTs fail before UserAgent invocation. Add a deployment guard that refuses non-loopback bind without configured auth.

**Dependencies**: none for loopback hardening; multiuser identity model is Q-M4.

**Work:** MP-02.


#### F-U14 — P2 — SSE replays duplicate history, races page state, and grows without bound

**Evidence**

- `connectSSE` defaults every connection to `last_event_id=0`: `services/ui/data/web/assets/shared.js:271-293`.
- Task init first loads up to 50 historical events, then opens that full replay: `services/ui/data/web/assets/task.js:541-549` and `577-585`.
- Every matching replay event independently refreshes task data and inserts another feed row: `services/ui/data/web/assets/task.js:514-539`.
- The server reads only the query parameter; it does not consume EventSource's `Last-Event-ID` header: `services/ui/src/ui_service/routers/events.py:77-90`.
- The dirty snapshot's test helper documents the resulting phase-reset race and works around it by waiting for feed growth to settle: `services/ui/tests/e2e/pages/task.py:63-101`.

**Failure path**

Opening a task duplicates its history. Reconnects replay everything again. Delayed refreshes can reset manual phase inspection to the live phase. Long-running/reconnecting pages accumulate unbounded duplicate DOM nodes and requests.

**Concrete fix**

- Load history once, record `newest_event_id`, and connect SSE after that cursor.
- Honor `Last-Event-ID` on reconnect.
- De-duplicate by event ID, serialize/coalesce task refreshes, and cap retained feed rows.
- Remove test-side “wait until race ends” as the product fix.

**Failing-test-first acceptance scenario**

Open a task with N historical events, assert exactly N unique IDs, add one live event, disconnect/reconnect, and assert N+1 unique rows with no phase-navigation reset and a fixed retention cap.

**Dependencies**: none.

**Work:** MP-19.


#### F-U15 — P2 — Participation metrics undercount ruled work and misstate labor-market denominators

**Evidence**

- Overall completed tasks count only `approved`, excluding `ruled`: `services/ui/src/ui_service/services/metrics.py:203-219`.
- Completion rate counts ruled tasks in the denominator but never numerator: `services/ui/src/ui_service/services/metrics.py:247-264`.
- “Agents with completed tasks” counts only approved work: `services/ui/src/ui_service/services/metrics.py:180-193`.
- Agent task completion and average-per-task denominators also count only approved tasks: `services/ui/src/ui_service/services/agents.py:38-45`, `217-220`, and `571-582`.
- Quarterly `completed` also excludes ruled outcomes while `disputed` includes both disputed and ruled: `services/ui/src/ui_service/services/quarterly.py:324-363`.
- Average bids computes only across tasks that received at least one bid, excluding zero-bid tasks: `services/ui/src/ui_service/services/metrics.py:479-487` and `services/ui/src/ui_service/services/quarterly.py:367-379`.

**Failure path**

A disputed task reaches a valid Court settlement and pays the worker, yet dashboards say it was not completed; it depresses completion rate and worker completion count. A market with many zero-bid jobs reports an inflated average bids/task because those jobs disappear from the denominator.

**Concrete fix**

- Define metric semantics once: “settled/completed” should normally include approved and ruled; “clean approval” should remain separate.
- Count all posted tasks in average-bid denominators with zero for no-bid tasks.
- Align top-level, agent, quarterly, and sparkline metrics.
- Add exact fixtures for approved, ruled 0/partial/100%, disputed pending, expired, and zero-bid tasks.

**Failing-test-first acceptance scenario**

Seed one task of each lifecycle outcome and assert exact completed, disputed-pending, settled, completion-rate, per-agent completion, and average-bid values across every API surface.

**Dependencies**: the settled metric vocabulary in MP-19; Q-12 reputation score endpoint remains a separate ratified build item.

**Work:** MP-19.


#### F-U16 — P2 — Browser tests validate render fixtures, not the human-to-economy write journey

**Evidence**

- UI browser E2E starts only UI against a seeded SQLite database: `services/ui/tests/e2e/conftest.py:67-142`.
- Its config lacks a complete operator config and no Identity/Task Board is started, so lifespan catches operator initialization failure.
- Lifecycle transitions in browser tests are commonly direct database mutations plus inserted events: `services/ui/tests/e2e/test_task_lifecycle.py:946-1046`.
- Bid-amount test explicitly expects reward displayed as bid amount: `services/ui/tests/e2e/test_task_lifecycle.py:470-473`.
- Separate live tests do prove the proxy backend and economic effects: `agents/tests/e2e/test_proxy_routes.py:71-216`.

**Failure path**

The browser suite can pass while Post/Accept/Approve/Dispute buttons are disconnected, ignore non-2xx responses, or cannot inspect downloads. The live proxy suite calls APIs directly, so the seam between rendered UI and real services remains unproved.

**Concrete fix**

- Keep fast seeded rendering tests.
- Add a small live Playwright lane against the full stack and provisioned operator: post from the form, external worker bids/submits, browser sees correct amount/downloads, accepts, approves/disputes, and observes balance/event changes.
- Make this lane own the primary human participation claim; avoid direct DB state transitions in it.

**Failing-test-first acceptance scenario**

One happy path and one dispute path driven by real browser clicks plus an external test agent, with exact database/ledger/event assertions after each transition and a restart checkpoint.

**Dependencies**: F-U01, F-U06, F-U08, F-U12; full-stack test harness.

**Work:** MP-22, MP-23.


#### F-U17 — P2 — Browser actions swallow downstream failures and destroy actionable service errors

**Evidence**

- Accept and approve do not inspect `resp.ok`; they treat any HTTP response as success and refresh: `services/ui/data/web/assets/task.js:430-459`.
- Create/dispute show an error, but backend wraps every downstream exception into generic 502 codes: `services/ui/src/ui_service/services/proxy.py:54-125`.
- The create page has no balance/escrow-affordability preflight: `services/ui/data/web/assets/task.js:65-101`.
- UI startup considers an unprovisioned zero-balance operator normal: `services/ui/src/ui_service/core/lifespan.py:46-50`.

**Failure path**

Insufficient funds, expired task, stale bid, or ownership conflict occurs. Accept/approve appears to do nothing; create reports generic “task creation failed” rather than insufficient balance or invalid state. The human cannot distinguish retryable dependency failure from a rejected business action.

**Concrete fix**

- Preserve structured downstream error code/status/details through the proxy with a bounded allow-list.
- Check every browser response; show inline failure and definitive success state.
- Disable duplicate submissions while pending and re-enable on failure.
- Show operator balance and reward affordability before post.

**Failing-test-first acceptance scenario**

For insufficient funds, expired bid, already-transitioned task, and unavailable dependency, assert exact visible error, no optimistic state change, button recovery, and no duplicate side effect.

**Dependencies**: F-U12 readiness; no product decision.

**Work:** MP-15, MP-20.


#### F-U18 — P2 — The built-in agent runtime is a math demo, not a generic execution runtime

**Evidence**

- Worker profiles are explicitly `math_worker`: `agents/config.yaml:49-100` and `agents/src/base_agent/worker_factory.py:71-118`.
- Task feeder reads JSONL with `solutions`, `level`, and `problem_type`: `agents/src/task_feeder/reader.py:14-72`.
- Review is exact normalized string matching: `agents/src/task_feeder/review.py:16-25`.
- Worker always writes one text solution asset: `agents/src/math_worker/loop.py:282-311`.
- Generic SDK primitives for arbitrary filenames/bytes already exist: `libs/service-auth/src/service_auth/mixins/task_board.py:143-171`.

**Failure path**

Starting `mathbot` against a human's arbitrary coding/design/data task does not make it a general worker. It may select based on generic task text, but its prompts/parser and one-text-file submission are math-specific, and feeder review only knows tasks created from its own answer-key file.

**Concrete fix**

Do not generalize the math loop by adding conditionals. Publish the external-agent SDK path in F-U10 and add a separate pluggable worker contract if an in-repo generic runtime is desired: task eligibility, workspace/input fetch, executor, deliverable manifest, resume state, and optional evaluator.

**Failing-test-first acceptance scenario**

An example non-math worker, built only on the public worker contract, consumes an input attachment, emits two deliverables, submits, handles dispute, and resumes after restart.

**Dependencies**: F-U07/F-U10. This plan chooses a supported external SDK plus a non-math example; an in-repository general execution framework is optional, not a blocking decision.

**Work:** MP-16, MP-17, MP-24.


### 4.5 Operations, verification and design smells

#### F-O01 — Startup can report success after required services fail (P1; confirmed)

`justfile:217-297` checks Identity failure explicitly, but ignores failures from the DB Gateway and remaining `wait_for_health` calls and ends with a success message. Health matching only searches for `"ok"`; an existing unrelated instance can satisfy a port probe. **Repair:** fail/clean up owned startup processes on any failed dependency, verify service identity/readiness, check port ownership and persist per-run process handles. Keep the already-fixed optional Court `.env` startup. **Proof:** absent dependency, occupied port, wrong service and crashing child all return nonzero without claiming success or killing unrelated processes. **Work:** MP-01, MP-21.

#### F-O02 — CI and worktrees share destructive runtime resources (P1; confirmed)

Root `justfile:test-integration-all` and `test-e2e` stop services, delete service databases and restart fixed ports. `stop-all` uses broad `pkill` patterns; service `kill` recipes select any listener on a port. A worktree isolates files, not ports/processes, and cannot safely run this gate alongside an active economy or another test tree. Service `ci-quiet` also runs a formatting fixer. **Repair:** a per-run runtime directory, ports, configuration and owned process registry; disposable seeded data; explicit reset command; read-only CI formatting checks. Make prerequisites fail fast and propagate startup/provisioning failures. **Proof:** two parallel stacks plus a sentinel unrelated process survive independent tests and teardown; failed startup cannot produce a green test recipe. **Work:** MP-01, MP-21, MP-22.

#### F-O03 — Backups, migrations and restoration are not a proven recovery contract (P1 operational gap)

Gateway schema bootstrapping uses “any table exists” and ad hoc added columns (`db_writer.py:81-129`); the supported runbook lacks a tested restoration workflow spanning database, keys and asset files. Copying only the SQLite main file under WAL or restoring DB without assets/identity keys cannot prove economic continuity. **Repair:** versioned schema migrations with validation, online-safe consistent backup, asset/key manifest, permission controls and a restore drill with ledger reconciliation. No automated deletion of original data on migration failure. **Proof:** migrate representative prior schemas and restore a disputed/settlement-pending economy, then continue it without reissuing money or losing evidence. **Work:** MP-06, MP-21.

#### F-O04 — Passing local tests do not establish the complete platform journey (P1; confirmed coverage limitation)

Unit suites use fakes; UI E2E uses seeded projections; Court CI uses deterministic mock judgments; existing crash tests need to be distinguished from actual process termination. Each is useful but none proves the entire new-user journey, adversarial state histories, or the economic thesis. **Repair:** a layered acceptance matrix, real browser-to-service writes, external agent from an independent directory, concurrency/fault injection and a separately cost-bounded real-model evaluation. **Proof:** release gates in section 8. **Work:** MP-22, MP-23, MP-25.

#### F-O05 — Shared abstractions and duplicated policy create maintenance traps (P2; confirmed architecture smell)

Examples include thin HTTP clients that coerce malformed data, domain business rules split between services/gateway writers, repeated middleware parsers with different validation, production/fake-store parity gaps, private SDK signing calls, and synchronous gateway clients called from async Task Board paths. The no-await transaction invariant is useful but is not an inter-service concurrency guarantee. **Repair:** first lock observable behavior with tests; concentrate each invariant at its authoritative boundary; keep small typed adapters and domain-specific transactional commands; remove dead alternatives only after consumer inventory. Do not rewrite all services or generalize a framework to fix a small seam. **Proof:** contract tests exercise production gateway paths, architecture checks enforce ownership, event-loop responsiveness survives a slow dependency. **Work:** MP-04, MP-07, MP-20.

#### F-O06 — Historical plans and the canonical backlog do not describe one current completion state (P2; confirmed)

The July plan contains completed work alongside stale original descriptions; OpenSpec retains historical Docker/salary/port requirements and T-101 through T-109 may already have implementations in the working tree. Comments still claim UI treasury minting. **Repair:** keep history, explicitly supersede outdated requirements, and reconcile each old ID with current proof. This master plan's checkboxes are an execution view; canonical ticket status stays in OpenSpec. **Proof:** all old IDs map to retained proof, an open task or a ratified deferral; no ticket closes from prose alone. **Work:** MP-26.

#### F-O07 — UI CI is red from outdated configuration fixtures and missing Chromium (P1; supplied failure log and source confirmed)

The user's `just ci` output reports **19 collected, 5 failed, 12 passed, 2 errors**. Two health tests error during setup, and `test_config_loads_from_yaml` plus `test_useragent_init_success_is_logged_not_failed` fail before their intended assertions because their generated YAML omits `user_agent`. `services/ui/src/ui_service/config.py:Settings` now requires that section and intentionally rejects its complete absence. The older fixtures in `tests/unit/routers/conftest.py`, `tests/unit/test_config.py` and `tests/unit/test_lifespan_logging.py` were not reconciled with that contract. `test_config_rejects_extra_fields` also omits the section, so its broad exception assertion can pass for the wrong reason.

The three tests in `services/ui/tests/unit/test_trend_visual_js.py` fail at `p.chromium.launch()`: the Python Playwright package expects `chromium_headless_shell-1208`, but its executable is absent. They never reach `ATE.trendVisual`, so the supplied output is not evidence of a new trend-rendering defect. Service `just init` installs Python dependencies but no browser; the root browser preflight runs only at the later UI E2E phase, after these unit tests already need Chromium.

**Repair:** reconcile valid test fixtures with the required `user_agent` contract using explicit isolated test values and stubs; retain every existing behavior assertion and the negative missing-section tests. Record the narrow fixture-only correction in the test specification before changing those existing test files. Add an early explicit Playwright browser prerequisite/install path matching the locked package version, preserving all three trend tests. Do not restore a production default, skip browser tests, weaken assertions or mask failures. **Work:** MP-00, T-165–T-168. The supplied log is historical red evidence; execution must reproduce the relevant failure against its current baseline before applying a repair.

## 5. Execution plan and task tracking

**Current working instruction:** prepare and implement in the existing `refactor` workspace. The latest owner instruction permits commits and pushes at any time on Saturday and Sunday and outside 08:00–18:00 on weekdays, in Europe/Amsterdam time, superseding the initial goal's all-hours prohibition for these two actions only. They remain forbidden Monday through Friday from 08:00 inclusive until 18:00 exclusive. Branch switches, merges, cherry-picks and rebases remain prohibited; read-only Git inspection is allowed. This overrides every later commit/push step in this document. Verify local time before any permitted Git write and preserve unrelated changes. No new delivery branch is required.

Every work package has a **test ticket** and a dependent **implementation ticket**. MP-01–MP-26 retain T-110–T-161; decision records retain T-162–T-164. MP-00 adds two independent proof/repair pairs, T-165/T-166 for UI fixtures and T-167/T-168 for Chromium readiness, recorded in the companion OpenSpec change. The subsequently reproduced deadline-precision baseline adds T-169/T-170 under MP-01 without changing MP-07's dependencies. MP identifiers group work; they are not a replacement tracker. Existing tickets remain linked in section 7. The original audit left implementation boxes unchecked. Verified execution below now marks MP-00, MP-01 and MP-03 complete at the recorded foundation checkpoint; remaining packages stay open.

**Execution protocol for every package:**

1. Read the cited current implementation and relevant API/test specification; capture the baseline revision and relevant configuration. Add any changed contract to the OpenSpec change before code.
2. In an isolated test worktree, write new tests for the observable outcomes below. Run them against current code and retain the actual failing assertion/output. Do not edit old acceptance assertions just to pass.
3. Mark the test dependency **red-proven**. This unblocks implementation, but does not claim the universal green-CI closure gate. Both tickets close only after the integrated green gate. This resolves the practical conflict between “test must fail first” and “every closed ticket needs green root CI” without suppressing tests.
4. Implement in its own assigned worktree. One owner per mutable schema/client contract; consumers coordinate against the published contract. Keep financial and cross-service changes small enough to review separately.
5. Run targeted new tests, existing affected tests, affected component `just ci-quiet`, cross-service contracts, then root `just ci-quiet` in the isolated runtime. Inspect exit status and complete logs. Record the red proof, green proof, commit and residual risks in the ledger.
6. Obtain an independent review for financial/state/security changes. Commit and push the implementation and evidence; only then close its canonical tickets. No task closes on an old green run or a prose claim.

Use native subagents and isolated worktrees only for independently owned slices. Prefer the configured low-cost explore role for bounded inventories, executor for routine implementation/test slices, and architect/code-reviewer for financial state machines and security. Reuse collected evidence; do not pay several agents to repeat the same inventory. Git worktrees must also use MP-01's isolated runtime before parallel tests.

### 5.1 Dependency waves

| Wave | Work packages | Exit condition / parallel boundaries |
|---|---|---|
| Preflight — restore UI test baseline | MP-00 (T-165–T-168) | Explicit valid fixtures, negative config checks and real-browser trend tests pass; root CI is still required for final closure. |
| 0 — trustworthy evidence | MP-01; begin MP-26 inventory | Reproducible isolated stack and protected baseline; new ticket/spec IDs allocated. |
| 1 — authoritative foundations | MP-03 → MP-02; MP-06 | Credentials/keys, schema version and financial constraints defined. One gateway/schema owner; policy/client work can proceed independently. |
| 2 — consistent state and money | MP-04 → MP-05; MP-07 → MP-08; MP-09 | Idempotent receipts, atomic transition claims, recoverable posting/settlement and safe artifacts. Merge contract changes before downstream clients. |
| 3 — trustworthy resolution | MP-11 → MP-10; MP-12 → MP-13 → MP-14 | Immutable rulings/evidence, private sealed projections, authorized feedback and score semantics. |
| 4 — complete participation | MP-15, MP-16 → MP-17, MP-18; MP-19; MP-20 | Humans and independent agents can complete jobs and recover their work. Frontend and worker modules can be owned independently. |
| 5 — reliable operation | MP-21, MP-22, MP-25 → MP-23; finish functional MP-26 | Fresh install, migration/restore, hostile histories, evaluated real Court profile, real UI + external agent journeys and full CI pass. |
| 6 — full economic thesis | MP-24; vision reruns of MP-25/MP-23/MP-26 | Price/reputation policy resolved, non-math market demonstrated, real judge evaluation passes. |

The arrows are dependencies, not permission handoffs. MP-20's response schemas should be agreed in wave 1 and implemented incrementally with each producer. MP-06 itself includes a tested pre-migration backup/restore foundation before any real migration; MP-21 later proves full operational restoration. MP-25's evaluation corpus can be authored before prompt implementation; live evaluation waits for MP-11. Cosmetic work cannot block any wave.

### 5.2 Work packages

#### MP-00 — Repair the reported UI unit-test and browser prerequisites

**Tickets:** T-165 test proof → T-166 fixture repair; T-167 browser-prerequisite proof → T-168 bootstrap repair. **Priority:** P1; first actionable work. **Depends on:** none for isolated UI proof; MP-01 isolation before destructive full-stack verification. **Owner:** UI test/configuration lane and test tooling. **Findings:** F-O07.

- [x] **Red proof:** preserve the supplied `just ci` result (5 failed, 12 passed, 2 setup errors), reproduce the four missing-`user_agent` paths with the existing tests, and confirm each fails before its intended assertions. Add a focused proof that an otherwise valid config with an extra field fails specifically for the forbidden field, not a missing required section. Preserve the six missing-required-section cases and partial-block behavior as separate contracts.
- [x] **Implement:** record the narrow fixture-only correction in the test spec, then supply explicit `user_agent.agent_config_path` and `user_agent.handle` in valid fixture YAML, using temporary configuration and a stubbed factory/registration where needed. Keep the config/lifespan/health assertions unchanged. Restore `CONFIG_PATH`, caches and app state on test failure as well as success. Do not make the production `user_agent` section optional or introduce a global backfill to accommodate invalid test inputs.
- [x] **Red proof:** capture the missing Chromium executable before the three trend assertions; reproduce browser absence in an isolated browser cache. Distinguish package installation, browser installation and browser launch readiness, including headless-shell revision mismatches after dependency upgrades.
- [x] **Implement:** provide a documented explicit `uv run playwright install chromium` setup path and an early availability/launch preflight before browser-dependent unit tests in local `just ci` and `just ci-quiet`. Preserve the standard service recipe contract; use a shared/scripted conditional UI hook if identical service justfiles remain required. Keep hosted CI browser preparation aligned with the locked Playwright version. Missing browsers must produce an actionable nonzero prerequisite error, never skipped/neutral/passing trend tests.
- [x] **Files:** UI test fixtures and test-spec additions; `services/ui/justfile` and root/shared setup recipes as permitted by structure checks; `.github/workflows/ci.yml`; prerequisite documentation. Production configuration validation and frontend trend implementation should remain unchanged unless separate evidence proves a defect.
- [x] **Green proof:** both health tests, config-loading test and successful UserAgent logging test reach and pass their original assertions; missing required configuration still fails; the extra-field case rejects the intended violation; all three increasing/decreasing/stable tests execute the real `shared.js` in Chromium and pass. The complete UI unit suite passes without skips, followed by UI CI and isolated root `just ci-quiet` exit 0 before closure. Record fresh output rather than claiming the supplied failures have already been fixed.

#### MP-01 — Isolate runtime and establish the current baseline

**Tickets:** T-110 tests → T-111 implementation. **Priority:** P1. **Depends on:** none. **Owner:** runtime/test infrastructure. **Findings:** F-O01, F-O02, F-O04.

- [x] **Red proof:** launch two disposable run profiles and an unrelated sentinel listener/process; demonstrate that present fixed-port startup/teardown cannot isolate them. Capture the current full-gate first failure without touching the user's active data.
- [x] **Implement:** introduce explicit run IDs, per-run data/assets/keys/config paths and port allocation; persist process ownership; make test setup consume those values. Keep the documented normal local ports for ordinary `just start-all`. Preflight tool/browser/model prerequisites and required Python version.
- [x] **Files:** root/service justfiles; `scripts/`; service config loaders only as needed; `tests/integration/`, agent/UI E2E fixtures. Use one change owner for shared startup commands.
- [x] **Green proof:** two concurrent disposable stacks perform separate registrations/tasks and teardown independently; sentinel survives; actual root CI output/exit captured for the reviewed snapshot; no source file autoformat or original data reset is hidden in “verification.”
- [x] **Handoff:** reproducible isolated commands and artifact locations used by every subsequent package. If baseline failures belong to existing staged work, record them and fix through their own tickets rather than mixing unrelated code into this audit.
- [x] **Discovered baseline prerequisite (T-169 → T-170; F-L14):** prove fractional-second deadline truncation with a controlled clock, preserve full configured durations without changing whole-second serialization, and verify existing expiry/refund/event assertions and Task Board CI. This isolated calculation repair does not close MP-07's atomic-transition tasks or bypass their schema prerequisites.


- [x] **Discovered baseline prerequisite (T-171):** the MP-02 UI audit reports GHSA-84m7-p3x7-pcfv in Semgrep's exact mcp 1.29.0 dependency. Preserve the nonzero audit and verify official advisory/pin evidence before remediation.
- [x] **Implement and verify (T-172):** use the smallest compatible patched package with regenerated locks, explicitly document any development-only upstream-pin override and removal condition, and pass all affected Semgrep/audit checks plus fresh isolated root CI. Do not suppress the new advisory.

#### MP-02 — Enforce persistence authority and the local browser boundary

**Tickets:** T-112 → T-113. **Priority:** P0. **Depends on:** MP-01, MP-03. **Owner:** auth/gateway, with UI proxy consumer. **Findings:** F-M01, F-U03, F-U13.

- [x] **Red proof:** malicious names/titles/comments/event summaries execute markup in the UI; unsigned and wrong-domain gateway calls can mutate valid fixture records; an off-origin browser request can invoke a bodyless operator action. Check private event/read routes as well as writes.
- [x] **Implement:** provision distinct service credentials/capabilities; authenticate the full command and restrict each service to its domain routes. Gateway stays private. Ordinary agent credentials are never gateway credentials. Replace implicit shared-key access with explicit allowed delegation for Task Board→Bank/Court and Court→Task Board/Reputation.
- [x] **Implement:** replace unsafe HTML interpolation with DOM text nodes or a single audited encoding boundary on every page; never render uploaded active content in the operator origin. Add a restrictive CSP and browser XSS regression fixtures for all identified sinks.
- [x] **Implement:** enforce allowed Host/Origin and CSRF/session protection for browser writes; reject non-loopback exposure without the required configured auth. Preserve local single-operator behavior. Add bounded request/admission limits before inviting untrusted network agents.
- [x] **Files:** gateway app/middleware/routers; shared transport/signing configuration; service lifespans and gateway adapters; UI app/proxy/middleware; deployment docs/config. Shared-library edits are a deliberately scoped library-owned change with all-consumer tests.
- [x] **Green proof:** malicious stored strings render as inert text on task/agent/landing/quarterly/feed pages; legitimate full lifecycle works; unsigned, tampered, expired, wrong-service and off-origin requests make no writes/events. Operator cannot mint or settle arbitrary escrow. Negative tests cover every domain capability, not just `/bank/credit`.

#### MP-03 — Make identities durable and token validation consistent

**Tickets:** T-114 → T-115. **Priority:** P1. **Depends on:** MP-01. **Owner:** Identity/signing. **Findings:** F-A01–F-A03.

- [x] **Red proof:** controlled concurrent first creation yields differing/mismatched keys; a missing key half regenerates identity; malformed header crashes; required expiry/algorithm/type checks differ across verifiers.
- [x] **Implement:** exclusive atomic key provisioning; private-file permissions; pair validation; explicit recovery instead of regeneration; public-key canonicalization. Consumers load provisioned credentials instead of racing to create them.
- [x] **Implement:** a typed JWS profile with required fields, finite lifetime/skew, object/algorithm checks and action/resource audience binding; stamp it consistently in official SDK and platform clients. Make missing signing configuration fail at startup. Preserve only explicitly scoped test fixtures, not a silent legacy production bypass.
- [x] **Files:** Identity registry/config/tests; `service-auth` signing/factory/config; service verifier adapters and startup; agent integration guide.
- [x] **Green proof:** parallel cold bootstrap converges on one ID, corrupt keys fail with actionable errors, all consumers share the malformed/expiry conformance table, and replay protection composes with MP-04 rather than treating TTL as exactly-once execution.

#### MP-04 — Give financial operations durable replay receipts

**Tickets:** T-116 → T-117. **Priority:** P0. **Depends on:** MP-02, MP-06. **Owner:** Bank/gateway. **Findings:** F-M02, F-M08; foundation for F-L01–F-L04.

Implementation checkpoint 2026-10-17: native and historical receipt persistence/validation,
Bank/in-memory storage, shared clients and funding/demo consumers have bounded independent approval.
Parent runs pass 24 public Bank tests and 26 cross-service tests. Root16 passed component gates with
929 unchanged source/config hashes, then exposed two integration fixtures, now repaired and reviewed.
Live-agent caller reconciliation and a fresh root closure gate remain required. Evidence
limitations remain explicit. See `openspec/changes/complete-agent-economy/evidence/mp04-receipts/history-checkpoint-2026-10-17.md`.

- [x] **Red proof:** drop the successful response after release/split commit, then retry; replay a lock after settlement; replay with altered parties/amount. Demonstrate non-convergence or second debit. The bounded eight-case public Bank/Gateway baseline was independently reproduced and approved on 2026-10-16; remaining identity, history, concurrency and rollback proofs stay separately required before their implementation (see `openspec/changes/complete-agent-economy/evidence/mp04-receipts/checkpoint.md`).
- [ ] **Implement:** stable operation ID plus canonical request hash and durable result for lock/release/split and issuance where needed. Atomically store receipt, escrow status, ledger legs and event. Exact replay returns the original successful result; changed content conflicts. Retain lifetime uniqueness of a task's escrow.
- [ ] **Implement:** receipts expose actual recipient(s), amount(s), ruling/operation reference and transaction/event IDs so callers can reconcile ambiguous outcomes. Never convert any generic `escrow_already_resolved` into success.
- [ ] **Files:** Bank API/schemas/router/store, gateway schema/writer/reader/bank routes, shared Bank/Gateway clients, Bank contract and cross-service tests.
- [ ] **Green proof:** Bank release/refund and 0/partial/100% split operations survive response loss with one set of ledger legs; changed recipient/percentage fails; concurrent opposing Bank commands produce one financial winner and its receipt. Cross-service consistency with the winning lifecycle claim is proved in MP-07/08, after atomic lifecycle claims exist.

#### MP-05 — Make task posting and compensation recoverable

**Tickets:** T-118 → T-119. **Priority:** P0. **Depends on:** MP-04, MP-07. **Owner:** Task Board posting. **Findings:** F-M03, F-M08.

- [ ] **Red proof:** fail after escrow lock but before task insert; lose task-insert response after commit; fail/refuse compensation; restart the process at each boundary.
- [ ] **Implement:** a durable posting intent with stable task/request identity; lock escrow, publish exactly once, and reconcile ambiguous insertion before refunding. Keep incomplete postings out of public open-job discovery. Record and retry compensation as an operation even when no task row exists.
- [ ] **Implement:** repeat submissions of the same client operation return the same job; a changed specification/reward under the same key conflicts. Validate that task terms, escrow payer and reward match.
- [ ] **Files:** `task_creation.py`, task store/client/protocol; gateway operation/publishing commands; SDK/proxy create-task paths; creation fault tests.
- [ ] **Green proof:** each failure history ends in one funded published task or one proven refund; no stranded escrow, duplicate task or refund for a successfully published task. The UI can resolve “request timed out” by operation ID.

#### MP-06 — Version the schema and enforce financial invariants

**Tickets:** T-120 → T-121. **Priority:** P0. **Depends on:** MP-01; coordinate MP-02. **Owner:** single schema/Bank writer lane. **Findings:** F-M04–F-M08, F-M10, F-O03.

- [x] **Red proof:** direct split to an unrelated poster; mismatched initial credit; bool and oversized monetary inputs; balance overflow; missing/partial schema; direct lifetime-unique escrow constraint and duplicate-escrow migration. Public Bank replay receipts belong to MP-04. Assert unchanged transactions/events on every rejected command.
- [x] **Implement:** first provide and test a consistent pre-migration backup plus restoration of the original DB/assets/key references, then add explicit schema version and transactional ordered migrations. Require a verified backup identifier before migrating real data; refuse migration if backup/validation fails. Mandatory readable schema and structural validation. Audit existing balances/types/escrows before constraint installation; quarantine/report invalid rows for reconciliation, never silently coerce or delete.
- [x] **Implement:** derive or validate payer in the settlement transaction, derive initial balance from its issuance record, reject booleans, constrain integer/nonnegative values and conditional arithmetic. Use an explicit maximum representable monetary value and guard additions; aggregate supply checks must also avoid SQL integer overflow. This technical representability bound is not a new monetary-policy question.
- [x] **Files:** `schema.sql`, gateway lifespan/writer/migrations/bank routes, Bank validators/contracts, migration fixtures and reconciliation command.
- [x] **Green proof:** fresh and supported old schemas converge; unknown/partial schemas fail before ready; authorized issuance minus recorded burn equals spendable balances plus locked escrow at bounds and under concurrent credit/lock/settlement; replay produces no duplicate ledger/event/receipt legs; no stored money column becomes REAL. Preserve backups before migrating real data.

MP-06 closure: root15 exited 0 with 888 unchanged source/config hashes, including the actual
migration process-death and maximum-bound independent-writer concurrency proofs. Independent
reviews have no unresolved findings; see the active change's `evidence/root15-checkpoint.md`.
Durable settlement receipts remain MP-04 and operational restored-runtime recovery remains MP-21.

#### MP-07 — Make all task transitions atomic and cutoff-aware

**Tickets:** T-122 → T-123. **Priority:** P0. **Depends on:** MP-06. **Owner:** Task Board/gateway transition contract. **Findings:** F-L01, F-L09; Q-16 cutoff interaction.

- [ ] **Red proof:** barrier-scheduled accept vs cancel/expire, submit vs expiry, approve vs dispute/auto-approve, duplicate acceptance and bid vs deadline. Execute through separate service instances/connections, not only one object's lock.
- [ ] **Implement:** required expected version/status predicates in authoritative writes; atomically claim an operation before external effects; validate bid/task/worker membership and cutoff in the transaction. Define exact deadline equality and the acceptance-window policy in the spec. A stale action cannot unconditionally replace a terminal state.
- [ ] **Implement:** reconcile Q-16 with the owner's 2026-10-16 decision: each poster explicitly selects an acceptance window following bidding close. Preserve early poster acceptance; stop new bids at bidding cutoff; expire zero-bid tasks immediately; tasks with bids remain acceptable until the selected cutoff. Do not impose feeder quorum/ranking on human selection or introduce a silent duration. Freeze published timing and accepted reward/offer terms; preserve existing contractual cutoffs during migration.
- [ ] **Files:** task creation/bidding/submission/review/ruling/deadline services, gateway board writer/routes, schemas/protocols, state-machine tests.
- [ ] **Green proof:** every concurrent history is equivalent to one valid serial history; losing commands return explicit conflicts and no money/claim/asset side effects; events name the actual winning transition.

#### MP-08 — Recover pending effects independently of task status

**Tickets:** T-124 → T-125. **Priority:** P0. **Depends on:** MP-04, MP-07. **Owner:** Task Board reconciliation. **Findings:** F-M02, F-L01–F-L03.

- [ ] **Red proof:** Bank outage during auto-approval/refund, task write failure after payout, process kill during effect delivery, and transient failure on an already terminal task. Run with no GET polling.
- [ ] **Implement:** durable operation/outbox records with retry count, next attempt, safe diagnostic and receipt reference; query pending work regardless of task status. Use bounded backoff, idempotent receipt lookups and fenced ownership. Distinguish business terminal state from settlement completion in the API.
- [ ] **Implement:** startup/background reconciliation; dead-letter/operator visibility for persistent failure, with a documented safe retry command. A failed item cannot starve the rest of the scan. Provide the generic operation primitives that MP-10 will use for Court claim/rebuttal handoffs; MP-08 closes only financial/task-effect recovery.
- [ ] **Files:** escrow coordinator, deadline evaluator/lifespan, gateway pending-operation queries, UI pending-status projection, integration fault harness.
- [ ] **Green proof:** terminal pending tasks settle exactly once after recovery; interruption at every await/commit boundary converges; no “paid” UI state without matching financial receipt and no pending item disappears from scheduling.

#### MP-09 — Make task inputs and deliverables safe, immutable evidence

**Tickets:** T-126 → T-127. **Priority:** P0. **Depends on:** MP-07. **Owner:** Task Board assets. **Findings:** F-L08–F-L10, F-U07, F-U08.

- [ ] **Red proof:** upload filename escapes its storage directory; two uploads bypass count/status checks; task input cannot be attached by poster; uploaded metadata/file failure leaves mismatched evidence; evidence changes after submission.
- [ ] **Implement:** generated storage names; treat supplied filename solely as display metadata; reject traversal/absolute paths and symlink escape; atomic file publication and durable metadata reconciliation. Enforce authorization, size/count and lifecycle predicates together. Bound streaming memory/disk use and clean only proven orphan files.
- [ ] **Implement:** poster input attachments before publication and an immutable input manifest; worker output manifest frozen at submission. Record hash, media type, filename, byte size, actor and revision. Accepted work is tied to one terms/input revision. Serve downloads safely as attachments with authorization/projection policy and correct content types.
- [ ] **Files:** Task Board asset manager/router/store, gateway asset schema/commands, SDK, UI upload/download paths, Court evidence adapter.
- [ ] **Green proof:** no write/read outside configured root; concurrent quotas hold; filenames/content/hashes survive upload→browser→Court; crash between bytes/metadata is repaired. Unsupported artifact formats are rejected or routed to an explicitly supported review path, never silently treated as text.

#### MP-10 — Persist one ruling and resume its delivery

**Tickets:** T-128 → T-129. **Priority:** P0. **Depends on:** MP-04, MP-07, MP-08, MP-09, MP-11; Q-M1 evidence decision. **Owner:** Court/gateway. **Findings:** F-L04–F-L07, F-L12.

- [ ] **Red proof:** concurrent ruling triggers; kill after `judging`; fail after Task Board settlement then retry with judges returning a different percentage; mismatch claim parties/escrow; fail rebuttal acknowledgment.
- [ ] **Implement:** claim context validated against authoritative task and linked dispute; fenced durable adjudication lease with restart recovery; snapshot evidence; persist votes and final ruling/hash before requesting settlement. Retry side effects from that same immutable result; do not judge again after a result exists.
- [ ] **Implement:** separate adjudication status from delivery progress (Task Board receipt and each reputation write); hash-checked idempotency at consumers; forbid compensation that deletes an already delivered ruling. Handle expired leases and repeated rebuttal with exact-payload replay/conflict behavior.
- [ ] **Files:** dispute service/store, ruling orchestrator/persistence, Court gateway writer/status routes, Task Board ruling/rebuttal handler, Court tests.
- [ ] **Green proof:** nondeterministic judge fixture settles and records one identical verdict under every retry; no permanently `judging` case after restart; simultaneous triggers produce one owned adjudication; no duplicate or contradictory feedback; mismatched claim parties/task/escrow/version cause no writes; identical rebuttal replay preserves the original Court timestamp, conflicting replay fails, and Task Board recovers the acknowledgment.

#### MP-11 — Define complete judge evidence and robust prompt/output contracts

**Tickets:** T-130 → T-131. **Priority:** P1. **Depends on:** MP-09; decisions Q-M1 and Q-M3. **Owner:** Court prompts/evidence. **Findings:** F-L10, F-L11, F-R03.

- [ ] **Red proof:** decisive evidence beyond current truncation budget; binary artifact; malicious instructions in each untrusted field; malformed/out-of-range model output; precise poor delivery vs vague acceptable delivery.
- [ ] **Implement:** typed, versioned evidence bundle with complete manifest and supported extracted content; explicit aggregate budget/overflow policy. Preserve identity of each file and any omitted bytes; block automatic judgment when required evidence cannot be evaluated. Never fetch arbitrary model-provided URLs or execute deliverables.
- [ ] **Implement:** system rubric and separate structured untrusted data; explain that task/claim/rebuttal text cannot redefine judge instructions. Validate structured output, separate specification and delivery judgments from payout, record model/prompt/version and reasons. Validation failure causes bounded retry or visible pending review, not an invented award.
- [ ] **Implement:** enforce configured real-run three-judge policy, deterministic median semantics, provider deadlines, concurrency and cost limits; keep mock judge profile explicit for CI only.
- [ ] **Green proof:** deterministic adversarial fixtures exercise typed manifests, hashes, filenames/media types, explicit truncation/unsupported-content outcomes, structured untrusted inputs and validated output schemas; provider context/time/cost bounds are enforced; no silent truncation or mock fallback in a real profile. Prompt changes alone do not close empirical injection/quality claims; MP-25 supplies measured evidence.

#### MP-12 — Close sealed-data leaks across public projections

**Tickets:** T-132 → T-133. **Priority:** P0. **Depends on:** MP-02. **Owner:** UI projection/reputation events. **Findings:** F-R02, F-U01, F-U02.

- [x] **Red proof:** unrelated caller reads open bids via UI task API; sealed rating appears in event history/SSE; personalized response is cached or rendered for another actor.
- [x] **Implement:** classify each read field/event as public, party-only or platform-only; centralize the projection rule across UI SQL/API, Task Board and Reputation. Authenticate the operator/party context where needed. Read-only DB access remains allowed but never bypasses domain visibility policy.
- [x] **Implement:** public event envelope without sealed payload; authorized poster gets actual bid amounts, identities and timestamps; unauthenticated observers see permitted aggregate counts only. Apply reveal timestamps consistently and prevent rating-dependent aggregate leakage.
- [x] **Files:** UI task/event/profile queries and schemas, SSE/event serializer, Reputation event payload/projection, authorization tests.
- [x] **Green proof:** multi-actor visibility matrix for open/accepted and sealed/revealed states across every read path, including reconnect, history, raw JSON and rendered UI. Public read-only observation still works.

Verified at root13; see `openspec/changes/complete-agent-economy/evidence/root13-checkpoint.md`.
This closes projection/sealing behavior. The direct visibility-fixture controls do not claim the
durable timeout/mutual reveal scheduler; that implementation and its real-service proof remain MP-13.

#### MP-13 — Authorize feedback and make reveal durable

**Tickets:** T-134 → T-135. **Priority:** P1 (P0 prerequisite for trustworthy reputation). **Depends on:** MP-07, MP-10, MP-12. **Owner:** Reputation/gateway. **Findings:** F-R01, F-R02, F-R04, F-U11.

- [ ] **Red proof:** unrelated signer/task/recipient, premature task feedback, swapped category, expired one-sided feedback absent from UI, mislabeled FK errors and duplicate Court delivery.
- [ ] **Implement:** task-party/category/outcome authorization at authoritative insertion; explicit source and subject roles for ordinary versus Court feedback; unique logical identities and exact-replay comparison. Preserve sealed mutual-reveal atomicity already implemented.
- [ ] **Implement:** durable timed reveals/events without a reader; same visibility used by API/UI/scores. Distinguish unknown task/agent, unauthorized, duplicate/conflicting content and unavailable dependency.
- [ ] **Files:** Reputation router/service/DB adapter, gateway feedback writer/reader/schema, timed job/query, tests; account for historical `role` ambiguity (T-106).
- [ ] **Green proof:** three-party adversarial test, concurrent counter-feedback, timeout across restart, Court partial delivery and zero premature public score influence all pass.

#### MP-14 — Expose trustworthy specification and delivery scores

**Tickets:** T-136 → T-137. **Priority:** P1 product completion. **Depends on:** MP-11, MP-13; resolve Q-M3. **Owner:** Reputation contract. **Findings:** F-R03; Q-12; F-U18.

- [ ] **Red proof:** no service-owned score query; UI/feeder give different standing; equal payouts conceal different specification/delivery failures; untested identities appear to have earned perfect quality.
- [ ] **Implement:** versioned `/reputation/agents/{id}/scores` contract with raw counts, dimensions, formula version and provenance; ratify a minimal formula with examples before code. Distinguish lack of observations from a measured 100%. Specify treatment of ordinary feedback, Court quality findings, cancellations and expired work.
- [ ] **Implement:** all consumers use this projection; tie-breaking uses delivery quality and a deterministic final tie rule, not duplicated ad hoc ratios. Keep outcome/payment separate from quality.
- [ ] **Files:** Reputation schemas/router/service, gateway read query if needed, UI agent/leaderboard projection, feeder selection/agent SDK, scoring fixtures/specs.
- [ ] **Green proof:** table-driven scoring fixtures, replay/no-double-count, sealed exclusion, version migration and identical values in UI and agent selection. No unverifiable claim of Sybil resistance.

#### MP-15 — Complete the human operator journey

**Tickets:** T-138 → T-139. **Priority:** P1. **Depends on:** MP-04, MP-05, MP-09, MP-12, MP-13, MP-19; score display after MP-14. **Owner:** UI product lane. **Findings:** F-U03, F-U06–F-U09, F-U12, F-U17.

- [ ] **Red proof:** a fresh operator cannot inspect balance/history, attach inputs, compare true amounts, download/review artifacts, cancel eligible jobs, give feedback, take an agent-posted job as the operator identity, or resolve ambiguous write errors entirely through supported UI.
- [ ] **Implement:** explicit operator identity/balance/funding status; job form with validation, inputs, terms/deadlines and insufficient-funds feedback; own-jobs queue and accurate offer comparison; acceptance confirmation showing full v1 reward vs signal bid; deliverable inspection/download; approve/dispute with reasons; eligible cancel; feedback; final ledger/ruling links; a “My work” view for the operator to bid, submit artifacts and rebut on an agent-posted job as itself.
- [ ] **Implement:** stable operation keys for double-click/retry, pending/error/recovery views, authorization-aware action eligibility, loading/empty states and keyboard-accessible forms. The UI must expose enough state to act safely, not merely hide an unauthorized button.
- [ ] **Files:** proxy/read endpoints and schemas, frontend API/store/pages/components; browser tests through real services.
- [ ] **Green proof:** human posts a non-math task with input, independent agent works, human approves; second task disputes and reaches ruling; cancel/refund and review-timeout paths are observable; balances and histories match Bank receipts. Also prove the operator bids/works on an agent-posted job, receives payment and can rebut a dispute as the actual assigned worker. Test after browser refresh and lost write response.

#### MP-16 — Make external-agent participation a supported interface

**Tickets:** T-140 → T-141. **Priority:** P1. **Depends on:** MP-02–MP-05, MP-09. **Owner:** SDK/onboarding. **Findings:** F-U10, F-U18.

- [ ] **Red proof:** run a new agent outside the repository with only its own keys and configured public service URLs; document every private roster/notary/filesystem dependency needed to finish registration→payment→posting.
- [ ] **Implement:** documented public SDK/config constructor or CLI using existing primitives; key provisioning/import, registration/recovery, zero-balance account opening, owned balance/history, pagination/discovery, task/asset methods, bids, accept/review, dispute/rebuttal and feedback. No private `_sign_jws` calls in examples. Document authenticated tunneled/private-network access to the agent-facing APIs while Gateway and the browser operator proxy remain inaccessible; the local deployment does not become a public multi-user site.
- [ ] **Implement:** an explicit administrator-invoked, platform-signed grant/top-up procedure for new posters with auditable issuance reference; workers can begin at zero balance per ratified policy. Give errors, idempotency/retry behavior, capabilities/limits and token profile a single integration contract.
- [ ] **Files:** agent/service-auth public API packaging, examples/config templates, integration guide, provisioning CLI and real-API tests. Reuse existing constructors where sufficient; do not invent a second SDK.
- [ ] **Green proof:** clean external directory on a separate host or isolated network namespace, with access only to documented agent-facing URLs and no shared filesystem, Gateway, operator proxy, local roster entry or notary key: agent earns from a human job, sees its receipt, then posts funded work. A second independently registered agent can complete it.

#### MP-17 — Recover worker commitments after restart

**Tickets:** T-142 → T-143. **Priority:** P1. **Depends on:** MP-07–MP-10, MP-16. **Owner:** worker runtime. **Findings:** F-U04, F-L12.

- [ ] **Red proof:** restart after bid, acceptance, upload, submission and dispute; original loop forgets its commitment or cannot rebut; failed/expired outcomes leave inconsistent earnings/history.
- [ ] **Implement:** reconcile remote open bids and accepted/submitted/disputed jobs at startup and periodically; durable local journal for execution checkpoints and operation IDs; resume or visibly fail owned commitments. Do not re-bid or re-upload completed work after an acknowledgment is lost.
- [ ] **Implement:** bounded concurrency/inference/time budgets and task-capability filtering; continue review/rebuttal tracking independently of new-job discovery. Record settled earnings from receipts, not promised reward or poll timeout. Handle expired/cancelled tasks and late acceptance explicitly.
- [ ] **Files:** math-worker loop/history/config, base-agent discovery/API helpers, optional generic worker example, restart/fault tests.
- [ ] **Green proof:** process termination at each commitment boundary recovers to one correct outcome; no abandoned accepted task, duplicate ledger earnings or missing rebuttal solely due to restart. Hostile task text cannot command local shell/tool execution in the example runtime.

#### MP-18 — Persist feeder review and acceptance policy

**Tickets:** T-144 → T-145. **Priority:** P1. **Depends on:** MP-05, MP-07, MP-13, MP-14, MP-16. **Owner:** feeder/runtime. **Findings:** F-U05, F-R03; Q-16.

- [ ] **Red proof:** restart loses expected answers and silently stops reviewing old submissions; bid window cutoff races expiration; lowest/tied offers yield unstable selection; selection-dependent pagination starves jobs.
- [ ] **Implement:** durable posted-task/expected-answer/review records tied to task version and hashes; reconcile server state on startup; separate posting, acceptance and review schedules so one failed task cannot block the economy.
- [ ] **Implement:** Q-16 window/quorum and cutoff policy from MP-07; lowest amount, service-owned delivery score then stable tie-break; explicit fallback when scores unavailable. Preserve generic task support by not applying math answer parsing to arbitrary human work.
- [ ] **Files:** feeder loop/reader/acceptance/review/config, gateway-free local journal or authorized service records, tests and runbook.
- [ ] **Green proof:** restart during an unattended approve and dispute round still reaches settlement without demo/human actions; reviews do not silently turn into timeout approvals because memory was lost; multiple feeders cannot post/review the same item twice.

#### MP-19 — Make observatory state, metrics and reconnects accurate

**Tickets:** T-146 → T-147. **Priority:** P1/P2. **Depends on:** MP-08, MP-12, MP-13. **Owner:** UI read model/frontend events. **Findings:** F-U06, F-U11, F-U14, F-U15.

- [ ] **Red proof:** accepted bidder amounts disappear; ruled work excluded from completion; multiple jobs for one worker distort employment; zero-bid tasks disappear from average; reconnect replays old events or skips racing new ones.
- [ ] **Implement:** one documented metric definition per field with explicit denominators, distinct-agent counting, outcome categories, time zone/window and payment basis. Differentiate posted reward, offered amount, settled payment, refunds and money supply; GDP must not double-count minting or locked escrow.
- [ ] **Implement:** consistent snapshot plus high-watermark, resume cursor/Last-Event-ID, stable event-ID deduplication and reconnect/disconnect behavior; bounded backfill and retention policy. Preserve chronological order even when timestamps match. Show pending settlement and model/mock provenance.
- [ ] **Files:** UI SQL/services/schemas, event/SSE routes, frontend shared state/API and dashboard/task/agent/report pages, metrics/event fixtures.
- [ ] **Green proof:** a hand-calculated dataset covering all terminal outcomes matches every chart/table; history→stream race and reconnect with new events deliver each event once in the view; score timeout reveal propagates consistently.

#### MP-20 — Tighten service contracts and remove unsafe blocking/fallbacks

**Tickets:** T-148 → T-149. **Priority:** P2, P1 where Task Board availability is affected. **Depends on:** producer contracts from MP-04/MP-07/MP-10; design starts earlier. **Owner:** shared-client maintainer plus service adapters. **Findings:** F-M09, F-M10, F-R04, F-O05, F-L13.

- [ ] **Red proof:** invalid 2xx JSON/missing fields returns fake zero/empty success; wrong 409 code is collapsed; slow synchronous gateway I/O blocks unrelated Task Board requests and deadline progress; fake store accepts inputs rejected in production.
- [ ] **Implement:** typed required response fields, strict errors and safe diagnostic context, explicit timeouts and cancellation; offload an entire sync operation safely or convert the boundary consistently to async. Do not split a single shared SQLite transaction across concurrent threadpool calls.
- [ ] **Implement:** public SDK methods replace consumer-private signing; remove dead client paths only after call-site inventory; consolidate duplicated validation where behavior is identical and covered. Keep domain policy at authoritative commands rather than hidden in convenience clients.
- [ ] **Files:** `service-clients`, `service-auth` public APIs, Task/Ledger/Feedback/Dispute adapters, Task Board sweeper and Court orchestrator/router, relevant fake/production contract tests.
- [ ] **Green proof:** malformed upstream replies produce explicit dependency errors, all consumers pass contract tests, Task Board health/unrelated work and sweep lag stay bounded during slow gateway probes, and existing public behavior survives each small refactor.

#### MP-21 — Provide reliable startup, health, backup and recovery operations

**Tickets:** T-150 → T-151. **Priority:** P1. **Depends on:** MP-01, MP-03, MP-06, MP-08, MP-10. **Owner:** operations. **Findings:** F-O01–F-O03.

- [ ] **Red proof:** required service missing but startup says success; keys/assets absent on restoration; stale pending work never resumes; false readiness with invalid schema/dependency; teardown kills unrelated process.
- [ ] **Implement:** owned-process lifecycle and readiness separate from liveness; fail-fast configuration with positive limits and valid paths; shutdown waits for durable claims or releases leases safely. Document single-gateway-process support until multi-process migrations/ownership are tested.
- [ ] **Implement:** consistent DB/WAL backup, hashed asset manifest and protected key backups; versioned restoration/migration command; reconciliation report for supply, balances, escrow/task/ruling links and pending effects. Include disk-full, permissions and failed migration recovery.
- [ ] **Implement:** structured operation/task/dispute/escrow correlation, pending-work age/retry/judge failure indicators and bounded logs. Do not log private keys, tokens or sealed evidence in public metrics.
- [ ] **Green proof:** restore a live-like snapshot to a fresh isolated run and finish pending work without extra mint/payment; failure drills produce actionable diagnostics; startup/teardown honor exact ownership and required dependencies.

#### MP-22 — Turn CI into an honest failure/concurrency gate

**Tickets:** T-152 → T-153. **Priority:** P1. **Depends on:** MP-01 through MP-21; MP-23–MP-26 consume this harness and are not prerequisites. **Owner:** test infrastructure. **Findings:** F-O02, F-O04; T-071/072/073/074/104/107.

- [ ] **Red proof:** fault scenarios escape current coverage; an earlier startup/provisioning failure can be masked by a later passing command; shared fixture order alters UI results; format step silently changes files.
- [ ] **Implement:** deterministic clock/barrier tests, real gateway contracts, actual process-kill tests, mock transport response-loss hooks and repeated multi-instance histories. Add property/model-based money/state invariants using existing tools first; any new dependency requires explicit approval.
- [ ] **Implement:** read-only formatting checks, fail-fast shell exit propagation, unique test runtime resources, reproducible seeded browser fixtures, required browser install preflight, clean checkout/frozen lock validation and retained failure artifacts. Audit the existing vulnerability exception T-108 against the installed dependency resolution at implementation time.
- [ ] **Files:** root/service CI recipes, `.github/workflows/ci.yml`, test fixtures/fault harness, architecture checks, proof/spec coverage mapping.
- [ ] **Green proof:** root `just ci-quiet` exits 0 from a clean disposable run with no source mutation; deliberately injected known faults fail their guards; the entire UI suite passes in isolation and varied order. Do not weaken flaky assertions to force green.

#### MP-23 — Prove the complete human and autonomous platform journeys

**Tickets:** T-154 → T-155. **Priority:** release gate. **Depends on:** MP-15–MP-22 and required MP-25 live-profile proof. **Owner:** independent verifier with integration owner. **Findings:** all functional-platform gaps.

- [ ] **Red proof:** encode each section 8 journey through public interfaces; record which fails on the current baseline. No direct DB inserts or monkeypatched browser write proxy in the end-to-end acceptance lane.
- [ ] **Implement:** only missing integration wiring and failures exposed by these journeys, assigned back to their owning package; do not place business behavior in test setup or demo scripts.
- [ ] **Green proof:** clean install/provision → human job with input → independent worker → submission → approval/payment/feedback; second job → rebuttal → real-profile Court ruling; cancellation and timeouts; two autonomous agents perform both approve/dispute rounds with restarts; UI matches real outcomes.
- [ ] **Green proof:** repeat with injected response loss and service restarts; record IDs, receipts, evidence hashes, screenshots/browser errors, logs and full CI result. Deterministic mock-judge CI and bounded real-judge evaluation are separate, labeled evidence.
- [ ] **Stop condition:** every required journey and invariant has inspected evidence, not merely a passing fixture name. Any failed journey remains an open blocking ticket.

#### MP-24 — Complete the economic mechanisms and non-math example

**Tickets:** T-156 → T-157. **Priority:** full-vision gate. **Depends on:** MP-04, MP-14, MP-16–MP-18; resolve Q-M2/Q-M3. **Owner:** economic-model and example-agent lane. **Findings:** F-R03, F-U18 and vision gaps.

- [ ] **Red proof:** current bids affect ranking but not payment; no general task capability/learning example proves specialization; classification-arena requirements are absent or unproven.
- [ ] **Implement:** for fixed-reward v1, state that fact in UI/API/docs and test payment equals reward. If the full-vision decision adopts price-forming awards, freeze accepted bid as contract price, settle worker share from that price and refund reward-minus-price; define rounding and all timeout/dispute/cancellation cases before migration. Never silently change outstanding accepted contracts.
- [ ] **Implement:** reproducible non-math task corpus and contrasting worker capabilities using existing dependencies; generic task/capability metadata or explicit supported content types; record costs, accepted prices, quality, outcomes and score-informed selection over rounds. Keep any provider-specific expense distinct from platform coins unless an exchange policy is explicitly defined.
- [ ] **Green proof:** real service APIs drive the classification example; measure specialization/quality rather than hard-coding role assignments or asserting a guaranteed emergent outcome. Repeated seeds show results and uncertainty. If the hypothesis fails, report that result and revise policy openly; do not falsify the demo.
- [ ] **Files:** agent examples/arena orchestration, task factory, scoring consumers, optional Bank settlement extension, economic specs and measured report.

#### MP-25 — Validate the real judge system against the core thesis

**Tickets:** T-158 → T-159. **Priority:** P1 before claims of trustworthy adjudication. **Depends on:** MP-10, MP-11, MP-14; corpus authoring can start earlier. **Owner:** independent Court evaluator. **Findings:** F-L10/F-L11, F-R03 and mock-only evidence gap.

- [ ] **Red proof:** evaluate current real prompts on paired precise/vague specs, compliant/noncompliant deliveries and injected instructions; mock 50% results do not count as quality proof.
- [ ] **Implement:** a versioned evaluation corpus with human-specified expected ranges/rationales, evidence completeness checks and neutral identifiers; configured three-judge real profile; structured-output success, consistency, injection resistance, worker-favoring ambiguity, cost and latency measurements. Set acceptance thresholds in the test spec before looking at the new model results.
- [ ] **Implement:** bounded opt-in provider execution with explicit credentials/budget, reproducible prompt/model settings, anonymized retained results and failure triage. No automatic fallback to mock payouts. Real-provider downtime leaves a recoverable pending dispute.
- [ ] **Green proof:** the panel meets predeclared quality/robustness thresholds, each verdict is traceable to frozen evidence, and retry stability is proven independently of model determinism. If it does not pass, keep adjudication readiness open and improve rubric/evidence/model configuration.
- [ ] **Files:** Court eval fixtures/runner/config, prompt versions, test specification and evaluation report. Model/provider choice within the agreed policy is configuration, not a major architectural open question.

#### MP-26 — Reconcile specifications, backlog and release evidence

**Tickets:** T-160 → T-161. **Priority:** P2; mandatory final handoff. **Depends on:** inventory starts immediately; closure follows MP-23 and MP-24/25 for full-vision completion. **Owner:** documentation/verifier.

- [ ] **Red proof:** create a behavior→spec→test→ticket matrix showing stale or unsupported claims; check commands, routes, JSON examples, lifecycle vocabularies and all links in the integration guide.
- [ ] **Implement:** update baseline/domain API/test specs and relevant ADRs after accepted behavior changes; label historical plans rather than erase history; reconcile T-001–T-109 using section 7; keep one canonical OpenSpec tracker and this execution view synchronized.
- [ ] **Implement:** fresh-start human guide, external-agent guide, financial/recovery runbook, judge-provider/evaluation guide, explicit supported deployment/artifact limits and remaining accepted risks. Remove claims of salaries, price-forming bids, multi-user hosting or mock-judge quality unless their gates passed.
- [ ] **Green proof:** a reader can reproduce both journeys without private knowledge; every task has red/green/full-gate/commit evidence or remains open; all new changes committed/pushed; exact release configuration and residual limitations recorded.
- [ ] **Stop condition:** no required unchecked task, unresolved blocking question, failing gate or unsupported completion claim remains. Preferences and visual polish stay outside the critical path.

## 6. Major open questions

These are financial/data-governance decisions, not layout, naming or provider preferences. Decision tickets transition OPEN → evidence/options prepared → decision accepted → reflected in specs/tests. They do not claim implementation completion, and need an accepted decision record for closure. Core requires Q-M1 and Q-M3; Q-M2 is Vision-only. They do not block immediate security, transaction, recovery, onboarding or test work. Record a decision and examples before implementing the dependent policy; do not silently invent one in code.

### Q-M1 — Which evidence may be judged automatically, and who may see artifacts?

**Ticket:** T-162. **Status:** OPEN decision. **Accountable owner:** project owner; implementation lead prepares the access/admissibility matrix. **Resolution gate:** accepted ADR with worked examples before MP-09 policy/MP-11 can close.

**Unresolved:** the platform accepts arbitrary files and publishes downloads, while Court effectively evaluates a small text prefix. General human jobs may contain private documents. The product needs an explicit admissibility and visibility contract.

**Recommended starting contract:** public job metadata; inputs/outputs restricted to authorized participants and Court, with optional explicit public publication; content hashes and safe metadata remain observable as permitted. Start automatic adjudication with explicitly listed text/source/structured-data formats and tested size limits; fail before acceptance for jobs outside that automatic envelope, or clearly select a separately designed human-review path. Never issue a financial ruling pretending omitted evidence was considered. Decide the legitimate need for bidders to inspect inputs before committing, with access/audit controls rather than revealing confidential inputs publicly.

**Alternatives:** deliberately public artifacts for an open synthetic economy; or private jobs plus a larger extraction/human arbitration system. These have different privacy, cost and adjudication obligations. This choice must also cover retention, redaction/deletion requests and whether evidence may be sent to external model providers. Preserve the immutable financial/audit record even if content retention expires.

**Blocks:** MP-09's access contract, MP-11, MP-15 downloads and the relevant MP-25 corpus. Safe storage/traversal fixes can proceed immediately. **Decision record required:** supported-format table, access matrix, failure/appeal path and model-data policy.

### Q-M2 — What is the post-v1 price-forming rule?

**Ticket:** T-163. **Status:** OPEN decision for the full-vision phase. **Accountable owner:** project owner; Bank/Task Board leads prepare conservation examples. **Resolution gate:** accepted economic ADR before the price-forming branch of MP-24; fixed-reward Core remains settled.

**Already settled for v1:** Q-8 makes bids signal-only; pay the posted reward. Do not re-open that as a prerequisite for the functional platform.

**Unresolved for the full competition vision:** whether accepted bid amount becomes the payable contract price, and how disputed price is split. With signal-only bidding, a worker can bid the minimum while still earning the full reward; lowest-bid selection does not demonstrate economically costly undercutting.

**Recommendation for the later economic phase:** treat reward as a maximum budget; freeze the accepted bid as the price; keep the full reward reserved until settlement or refund excess at acceptance under an explicitly tested rule. On dispute, apply worker percentage to accepted price and refund the remaining price plus any reserved excess to the poster. Use integer rounding with an exact conservation rule. Existing accepted tasks retain their recorded contract terms during rollout.

**Blocks:** price-forming branch of MP-24 and claims of a functioning price market. **Decision record required:** worked approve/cancel/expire/0/partial/100% examples, timing of excess refund, and migration treatment. If fixed reward remains the final choice, rename the economic claims accordingly and demonstrate the fixed-reward competition model honestly.

### Q-M3 — How do quality findings become economic reputation?

**Ticket:** T-164. **Status:** OPEN decision. **Accountable owner:** project owner; Reputation/Court leads prepare scored fixtures. **Resolution gate:** accepted scoring/output contract before MP-11/MP-14 can close.

**Already settled:** Q-12 assigns score ownership to Reputation; Q-13 requires a three-judge production panel and an explicit ambiguity rubric.

**Unresolved:** the versioned formula and source weighting for independent specification/delivery quality, no-history identities, sample size, and the influence of ordinary feedback versus Court findings. Payout share cannot identify both dimensions. Simply labeling complementary payout percentages as quality would encode a false measurement.

**Recommendation:** judges emit separate rubric-backed quality dimensions and payout; keep raw peer feedback separate in provenance; expose counts/uncertainty; use a documented, bounded, versioned aggregation with neutral/unrated presentation before observations. Start without time decay or complex reputation staking. Introduce any anti-collusion weighting only with a stated threat model and tests.

**Blocks:** MP-14 and reputation-driven specialization claims in MP-24. **Decision record required:** scoring table for no history, good/poor precise work, vague adequate work, peer disagreement, repeated task/ruling delivery, and malicious unrelated feedback.

### Decisions inherited or resolved by this plan, not open questions

- **Q-M4 — Human deployment:** retain Q-2/Q-3's local single operator for the functional gate. Browser origin/Host/XSS protections are required now. Public multi-human access is a separate scope change requiring per-human identities, sessions, custody/recovery and ownership tests; no partial login feature is needed to finish the current goal.
- **Q-M5 — Initial funding:** retain manual platform-authorized issuance and explicit provisioning. Extend the administrator-invoked, platform-signed grant procedure to a newly registered poster with an auditable idempotency reference. A new worker can start at zero, earn, then post with earned funds. A faucet, purchased credits, transfers or salaries are optional later economic changes, not blocked onboarding work.
- **Rebuttal cutoff:** preserve the existing worker-favoring rule that a late rebuttal is admissible until judging is atomically claimed; document/test the exact race. A strict cutoff would be a deliberate later contract change.
- **Judge providers and settings:** choose profiles by measured MP-25 results within Q-13's three-judge policy. Provider brands, model names, temperature and timeout values are config/engineering choices. Do not turn them into a permission queue.
- **Ruling quorum:** preserve the current all-configured-judges/sequential evaluation contract until a separately specified change is justified. One provider failure leaves recoverable pending work, not a smaller unannounced panel.
- **Metric vocabulary:** `approved` means clean/timeout approval; `ruled` means Court-decided; `settled` includes both when payment is reconciled; keep cancellation/expiry separate. “Completed” must be explicitly defined per view. This is an implementation/spec consistency task, not a major owner decision.
- **Representation bounds:** choose an explicit safe integer range compatible with SQLite/API clients and enforce it everywhere. This is not a decision about fixed supply or real-money value.
- **No new scope by implication:** salaries, dispute fees, work caps, key revocation for local v1, Docker, a formal co-signed contract artifact and a UI framework rewrite remain the existing deferrals. Accepted terms still need an immutable record inside the current task model.

## 7. Existing backlog reconciliation

The existing backlog is a requirements/history source. It does not contain sufficient current closure evidence for every entry; this audit does not close any implementation ticket. Read the July plan's associated commits and the current code before recreating old work.

| Existing ticket(s) | Current disposition from the audit | Completion handling |
|---|---|---|
| T-001 | No fresh full-root green result established here | MP-01 records baseline; MP-22/23 own final gate. |
| T-010–T-018 | Canonical statuses, mediated rebuttal, automatic claim/ruling, honest demo and unattended paths exist | Preserve tests; MP-07/08/10/17/23 add concurrency/restart proof. T-040 retry claims need strengthening rather than deletion of prior fixes. |
| T-020–T-026 | Error casing, local platform verification, gateway reads, Task Board settlement ownership, integer bid field, UI naming/vanilla JS are ratified | MP-26 aligns current contracts; preserve decisions. T-024 does not imply bid price controls payment. |
| T-030–T-034 | Bank event/type/zero-leg/store-parity/config work has implementation/tests | MP-04/06/20 verify authoritative numeric/integrity and replay behavior; do not infer full financial safety from parity alone. |
| T-035–T-038 | Deadline expiry, specific errors and contract cleanup mostly implemented | MP-07/08 repair concurrent interactive actions and terminal pending effects; reconcile cutoff policy. |
| T-039–T-041 | Court readiness/retry/content and mock/live docs exist | MP-10/11/25 replace exception-only “crash” proof and enforce immutable verdict/evidence/real-profile validation. |
| T-042–T-045 | Gateway events, original replay IDs, read docs and concurrent registration coverage exist in baseline/working changes | MP-04/06 add settlement/lifetime replay and schema guarantees; retain duplicate-registration proof. |
| T-046–T-049 | UI phase/bucket/trend/proxy work exists | MP-12/15/19/23 address privacy, real bid values and complete browser journey. |
| T-050–T-051 | Identity endpoint/persistence docs largely reconciled | MP-03/16/26 add consistent token and external-agent contract. |
| T-060–T-065 | Major documentation sweep already committed; further working changes exist | MP-26 updates only newly changed behavior and remaining stale claims. Preserve historical decision records. |
| T-070 | Shell acceptance files are retired in the current snapshot with a retirement record | Verify pytest replacement coverage by spec ID under MP-22; do not restore obsolete shell suites merely to satisfy old wording. |
| T-071–T-072 | New real integration and spec-gap tests are staged; not accepted as green by this audit | MP-01/22 run them, map every specification case and retain failure evidence. Avoid counting test filenames as coverage. |
| T-073 | Cancellation refund E2E added; timeout coverage exists | MP-08/22 add outage/response-loss/restart variants and prove exact refunds/payments. |
| T-074–T-075 | Architecture/funding-CLI guards exist | Retain them; extend to service authority, safe isolation and external grants. |
| T-081 | Docker was deliberately removed, not a current missing deployment | Record superseded by Q-6; supported deployment is local processes. |
| T-082–T-085 | Client/hygiene/semgrep/logging cleanup substantially exists | MP-20/21/22 target real residual defects rather than restarting broad cleanup. |
| T-090 | Salaries explicitly deferred | Keep deferral; manual funding remains supported. |
| T-091 | Classification arena/full specialization proof not established | MP-24 owns the concrete non-math example and measurement. |
| T-092 | Historical vision questions have ratified answers/deferrals | Do not ask again; only Q-M1–Q-M3 remain material new/full-vision policy work. |
| T-093–T-095 | Quarterly/dashboard/leaderboard views implemented | Correct semantic metrics and real-state behavior under MP-19; animation/button aesthetics cannot delay the functional gate. |
| T-100 | Historical migrated duplicate ID entry, closed with recorded old evidence | Keep history; no new implementation. |
| T-101 | Bid-count normalization/counting has evolved since discovery | MP-07/19 verify authoritative count under concurrency, including zero bids; inspect current production query before declaring this still broken. |
| T-102 | Deterministic worker path exists in unattended-economy test setup | Preserve it; separate deterministic CI from live-model evidence. |
| T-103 | Optional Court `.env` startup chain is fixed | Preserve regression; MP-21 fixes remaining ignored startup failures. |
| T-104 | Timing test changed in working tree | Verify with deterministic cutoff/concurrency tests under MP-07/22; sleep widening alone is weak evidence. |
| T-105 | Gateway transaction reads include `event_id` | Preserve provenance; closure requires original item proof plus root gate. |
| T-106 | Court-feedback `role` semantics remain ambiguous | MP-13 gives source and subject roles real meaning with migration/API tests. |
| T-107 | UI fixture reset/workarounds are present in working tree | MP-19 fixes product SSE race; MP-22 verifies whole-suite order independence. |
| T-108 | Development vulnerability exception remains documented | Re-evaluate installed resolver/upstream advisory at implementation time; do not claim it is safe or obsolete from this static audit. |
| T-109 | Explicit vagueness rubric and its transport test are present in the working tree | Preserve; MP-11/25 test completeness and adversarial/semantic effectiveness. |
| T-110–T-161 | New test/implementation pairs for MP-01–MP-26 | Accepted obligations synchronized into `openspec/specs/completion-backlog/spec.md`; stable IDs, dependencies and proof records are preserved. |
| T-162–T-164 | Q-M1/Q-M2/Q-M3 decision records, OPEN | Named accountable owners and resolution evidence in section 6; Q-M1/Q-M3 block Core policy closure, Q-M2 blocks the full-vision pricing claim. |
| T-165–T-168 | Reported UI CI failures, OPEN | MP-00: reproduce/correct valid configuration fixtures and separately prove/repair the matching Chromium prerequisite before unit CI. |
| T-169–T-170 | Reproduced deadline precision baseline, OPEN | MP-01: deterministic full-duration proof and narrow shared calculation repair; MP-07 atomic state transitions remain open. |

The companion change records **proposed** requirements and OPEN tasks; it does not silently supersede ratified decisions. If another workstream has allocated a proposed T-ID before merge, renumber the whole new pair and all references together. Never reuse an existing T-ID for different work.

## 8. Release acceptance matrix

Passing the functional gate requires resolved Q-M1/Q-M3 and all rows marked **Core**; passing the full economic-vision gate additionally requires Q-M2 and all **Vision** rows. Each row needs exact test names/commands, fresh logs, exit status, configuration, commit and relevant object IDs. “Test exists” is not pass evidence.

| Gate | Scenario / adversary | Required observations | Work packages |
|---|---|---|---|
| Core-01 | Fresh checkout/start/provision, then repeat provisioning | Seven correctly identified ready services; one operator identity; repeat bootstrap creates no extra money; missing dependency fails clearly. | 01, 03, 06, 21 |
| Core-02 | Human browser posts with input; external agent joins from separate host/network namespace | Real signature-authenticated job and escrow; independent keys/account; no shared filesystem/roster/notary/Gateway/operator-proxy access; input bytes/hash correct. | 09, 15, 16, 23 |
| Core-03 | Two bids with unequal amounts | Anonymous observer cannot see open bids; operator sees true amounts; one worker wins; displayed reward/bid/expected payment are distinct. | 07, 12, 15, 19 |
| Core-04 | Worker delivers multiple files; human approves | Safe browser downloads equal submitted bytes; one payout receipt, matching task/event/UI state; mutual feedback follows visibility policy. | 04, 09, 13, 15, 23 |
| Core-05 | Dispute, worker rebuttal, automatic ruling | Evidence contains immutable spec/inputs/outputs/claims; one ruling and one settlement; both reputation records use that ruling; no demo/manual trigger. | 08, 10, 11, 17, 23 |
| Core-06 | Cancel; bidding expiry with bids; execution expiry; review timeout | Correct refund or worker payout once; no active assignment after refund; deadlines progress without reads; terminal pending effects reconcile. | 04, 07, 08 |
| Core-07 | Concurrent conflicting lifecycle actions | One valid serial outcome; loser has explicit conflict; no orphan claim, contradictory status or double resolution. Exercise multiple processes. | 07, 08, 10, 22 |
| Core-08 | Lose acknowledgment after every financial/task/Court commit | Same operation returns original receipt; conflicting replay rejected; eventually consistent task/escrow/ruling/reputation/event state. | 04, 05, 08, 10 |
| Core-09 | Kill/restart Task Board, Court, feeder and worker at every commitment phase | Work discovered and resumed without exception cleanup; judging lease reclaimed safely; fixed decision not rerun; no phantom earnings or silent forfeiture. | 08, 10, 17, 18 |
| Core-10 | Bank/gateway/Identity/provider outage, disk full, invalid config | Explicit unavailable/pending/readiness state; bounded retries; unaffected work progresses; no fake success or mock financial fallback. | 11, 20, 21 |
| Core-11 | Unrelated actor/signer/service; replay; malformed JWS | No unauthorized write/read; no mint or settlement from ordinary agents; malformed input yields documented errors, not crashes. | 02, 03, 06, 13 |
| Core-12 | Filename traversal, active-content artifact, stored markup, cross-origin action | No outside-root writes, script execution in UI, off-origin spending or unsafe preview; upload quotas hold under concurrency. | 02, 09, 15 |
| Core-13 | Sealed bids/feedback through all projections and SSE | No pre-reveal secrets in response fields, payloads, rendered text or aggregates; timeout reveal durable across restart. | 12, 13, 19 |
| Core-14 | Money boundary cases and replay | Integer/nonnegative storage, no overflow, exact split rounding, correct payer/recipient, issuance reconciles balances+escrow; no extra event/ledger legs. | 04, 06 |
| Core-15 | Observe/reconnect during live economy | Snapshot/stream gap closed; unique ordered events; charts and per-agent totals agree with known receipts/statuses; pending vs settled distinct. | 19 |
| Core-16 | External worker earns then becomes poster | Earned funds can escrow a new job; second agent completes it; documented authorized grant procedure works for a new poster; no hidden privileged shortcut. | 15, 16, 23 |
| Core-17 | Entire autonomous economy with restarts | Feeder posts, waits/quorum-selects, worker submits, good work approved/bad work disputed and ruled; no demo engine or human required. | 17, 18, 23 |
| Core-18 | Upgrade and restore | Keys, schema, assets and ledger coherent; pending operations resume once; original snapshot retained if migration fails. | 03, 06, 21 |
| Core-19 | Clean full gate in isolated runtime | Root `just ci-quiet` exit 0; all seven services, libraries, agents, tools, integration, UI E2E and economy E2E pass; no skips/suppression hiding required behavior. | 22, 23, 26 |
| Core-20 | Real supported Court profile | Three configured judges, supported evidence, traceable decisions and recoverable provider failure; mock-only proof is clearly insufficient for real adjudication claims. | 11, 25 |
| Core-21 | Human takes an agent-posted job through the UI | Operator bids as itself, downloads input, uploads/submits work, receives payout and can rebut a dispute; no impersonation of another worker. | 09, 15, 23 |
| Core-22 | Reported UI CI regression and missing browser | Health/config/logging tests pass with explicit valid fixtures; missing-section and cause-specific extra-field checks remain meaningful; all three trend tests execute shipped JavaScript in matching Chromium; missing browser fails preflight without skips. | 00, 01, 22 |
| Vision-01 | Paired vague/precise specification corpus | Predeclared ambiguity policy and independent quality dimensions pass the empirical evaluation; economic penalties/worker protections are demonstrable. | 11, 14, 25 |
| Vision-02 | Non-math input→multi-output job and classification rounds | Only public APIs; measured adaptation/specialization and quality/cost outcomes with uncertainty; no hard-coded winner/oracle in market mechanics. | 16, 17, 24 |
| Vision-03 | Economic pricing choice implemented honestly | Chosen contract documented; signal-only payouts labeled, or accepted-price settlement/refund proven for every outcome with migration protection. | 04, 24 |

### Minimum deterministic adversarial schedules

Maintain these as reusable barrier/fault scenarios, not sleeps: accept A vs accept B; accept vs cancel; bid vs close; upload/submit vs expiry; approve vs dispute; approve vs timeout; duplicate dispute; rebuttal vs judging claim; two ruling triggers; response loss after each commit; Court kill before and after decision persistence; partial feedback delivery; restart after worker bid/upload; feeder restart before review; concurrent key bootstrap; schema migration interruption; concurrent maximum-balance credit; SSE reconnect between snapshot and subscription.

For each history, assert the same invariants at **all** layers: HTTP result, authoritative task/operation record, Bank balance/ledger/escrow, Court attempt/ruling, Reputation visibility/scores, event payloads and browser view. Mock transport tests do not replace at least one real multi-process proof of each crash/concurrency class.

## 9. Evidence and completion ledger

### Audit completion evidence

- [x] Project intent and ratified scope reviewed against current code, not only historical plans.
- [x] Three isolated parallel specialist reviews completed; their material findings and acceptance proposals incorporated above.
- [x] All seven services, shared libraries, prompts/judges, money, job lifecycle, assets, UI, agents, examples and operations covered in the scope/requirements map.
- [x] Focused baseline suites: 75 Identity + 233 Reputation + 4 shared-infrastructure tests passed. Actual signing parser/policy probe reproduced two discrepancies.
- [x] Findings mapped to test-first work packages, dependencies, owners, source surfaces, release gates and existing backlog.
- [x] Major unresolved policies separated from inherited decisions and implementation preferences.
- [x] Document/reference/ticket-coverage validation and independent plan review — observed results recorded in the delivery addendum below.
- Publication commit and remote verification are recorded in the delivery response; the publication change contains only this plan and its four OpenSpec companion files.

### Per-work-package evidence record (copy for each MP)

| Field | Required value |
|---|---|
| Package and tickets | MP-xx, test T-xxx, implementation T-xxx; linked old IDs |
| Baseline | Commit + dirty-snapshot note if applicable, schema/config versions |
| Red proof | New test names, exact command, exit status, assertion/failure artifact |
| Contract decision | Accepted OpenSpec/ADR reference, including any changed old assertion |
| Implementation | Commits, owner, affected modules; migration/recovery steps |
| Green proof | Targeted tests, component CI, fault/journey evidence and fresh root CI output/exit |
| Review | Independent reviewer, resolved blockers, approved residual risks |
| Delivery | Pushed commit/branch; canonical ticket state; synchronized MP checkboxes |

### What this audit does not certify

The product is not certified complete, secure under every possible attack, or economically validated by this plan. The review reports the material issues found in the inspected scope; static inspection cannot establish absence of all possible bugs. Existing fake/mock tests and three passing focused suites do not substitute for the release matrix. No live-provider credentials/cost were incurred, no live economy data was reset, and no full-root CI pass is claimed. Isolated lifecycle test attempts could not resolve uncached dependencies under restricted network access; no passing result is inferred from those attempts. The plan's requested deliverable can be complete while all implementation tickets remain open.

### Delivery addendum — original audit publication

- **Independent review:** money/persistence reviewer returned APPROVE; lifecycle/Court architect returned READY; participation/UI/agents reviewer returned READY. These verdicts apply to this planning artifact only. Corrections included explicit backup-before-migration, Court contract-before-orchestration ordering, financial-only receipt proof before lifecycle claims, real-profile judge proof before Core closure, exact canonical acceptance clauses, and tracked policy decisions.
- **Structural validation:** passed for 54 unique finding entries, 26 unique work packages, 55 unique proposed ticket scenarios (52 paired work tickets + three decisions), all finding-to-work mappings, required red/green proof fields, full implementation-clause mirroring into OpenSpec, cited concrete paths/line bounds, absence of placeholders and an acyclic implementation dependency graph.
- **Tool limitation:** `openspec` is not installed in the environment; no claim of official OpenSpec CLI validation. The spec-driven proposal/design/tasks/delta structure was manually reviewed and structurally checked. Full implementation CI remains a required future gate and is not claimed here.
- **Snapshot identity:** review-start tracked patch SHA-256 `b8966ca1ae9fefd59bb241257a6f59219cbac88cfe4d6419be895cff21550597`, applied to `d4148401f4e3e5749d878e560a742c4ccb2aa68c` in three isolated worktrees.
- **Concurrent workspace updates:** during finalization, existing refactor changes were committed as `d8559ba99156a5866af06da5e4575b61efc4a0fc`; obsolete root hook setup was removed and separate dependency/lockfile upgrades were underway. Those edits were inspected and preserved. They do not repair the cited lifecycle, settlement, privacy or participation paths. Focused test results above apply to the review-start environment, not the concurrently upgraded dependency environment; MP-01 must freeze and validate the eventual implementation baseline.
- **Delivery scope:** application source, existing tests, dependency upgrades, runtime data and ignore rules were not changed by this planning work. All implementation/decision tasks remain OPEN. The working copy of the requested file is provided even where the current workspace ignore rules exclude planning artifacts; publication uses a dedicated review branch.


### OpenSpec workflow recreation and UI CI follow-up

The later user request explicitly replaces the manually constructed OpenSpec planning documents. The original documents were preserved outside the repository before replacement. The installed `openspec-propose` skill was read and applied through the official pinned OpenSpec CLI 1.14.2: `new change`, `status`, per-artifact `instructions`, capability inventory/full-spec reads, sequential artifact creation and strict validation. CLI-resolved paths select the existing repository-local `complete-agent-economy` change.

The recreated change contains `proposal.md`, `design.md`, `tasks.md`, `.openspec.yaml` and four capability deltas: `agent-task-economy`, `completion-backlog`, `delivery-governance` and new `ui-ci-readiness`. Existing baseline specs remain untouched until the accepted change is later synced/archived. The task list uses the workflow's numbered checkbox format and keeps all implementation/decision work unfinished.

The new F-O07 / MP-00 records the user's actual `just ci` result: 19 collected, 5 failed, 12 passed, 2 errors. Configuration fixtures and Chromium readiness have separate T-165–T-168 proof/repair pairs; no application or test implementation was changed in this preparation, and no green application CI result is claimed. The historical audit's OpenSpec-CLI-unavailable limitation is superseded for these recreated artifacts by the current strict validation result. Git commands, staging, commits, pushes and branch changes were not used for this recreation.

Recreation verification: `openspec validate complete-agent-economy --strict --json` exited 0 with `valid: true` and no issues. `openspec status --change complete-agent-economy` reported all four planning artifact groups complete. The read-only apply instructions reported 109 tasks, 0 complete, 109 remaining. Cross-checks confirmed all four declared capability paths, T-110–T-168 without collisions, numbered verification-bearing tasks and preservation of existing scenario names in modified requirements. Independent review returned READY for the planning artifacts.


### Active implementation checkpoint — MP-00

Fresh baseline reproduced the reported 5 failures, 12 passes and 2 errors. The fixture-only reconciliation is recorded in the UI test spec; valid fixtures now provide explicit isolated operator settings, stub registration, and clean up state on failure. Production configuration and all original health/logging/trend assertions remain intact. The negative matrix contains six omitted-section cases (the original plan's count of seven was corrected from the actual test parameters).

An early shared test-prerequisite recipe now launches the browser required by the installed Playwright package, with explicit setup guidance on failure. The command is used before UI unit/CI and UI E2E execution; all seven service justfiles remain identical. Matching Chromium/headless-shell revision 1208 was installed. A sandboxed launch was denied by macOS; the same suite with browser-launch permission passed **22/22**, including the unchanged three trend tests and the new extra-field/preflight guards. Empty/stale isolated cache tests also pass by correctly rejecting missing prerequisites.

OpenSpec tasks 1.1–1.7 are verified; 1.8 and canonical T-165–T-168 closure remain pending isolated root CI; UI component `just ci-quiet` subsequently passed with exit 0. MP-00 is not declared closed. Logs and exact commands are preserved in `openspec/changes/complete-agent-economy/evidence/`. The overall implementation goal remains active; no Git mutations occurred.


MP-00 integration follow-up: the isolated service integration run passed the other six services (7/3/1/12/1/6 tests), then exposed the same incomplete UI fixture configuration in 223 setup errors plus one separate health failure. The valid-configuration reconciliation now explicitly includes UI integration fixtures; their behavior assertions remain unchanged. A follow-up task was added under T-166; the implementation checklist now has 110 tasks. Root CI and canonical MP-00/MP-01 closure remain pending.

Foundation checkpoint (root attempt 7): **20/112 tasks at that time**. MP-00 UI readiness, MP-01 isolation and MP-03 durable identity/strict tokens are verified. Root `just ci-quiet` attempt 7 exited 0, including 273 browser tests and 92 isolated economy E2E cases; all 794 source/config hashes matched before and after. A separate simultaneous two-economy proof verified different identities/tasks, foreign-task isolation, continued work after peer teardown and a surviving sentinel. MP-06 has verified red proof (12 intended failures and six controls), but its implementation remains open. Three existing credential-dependent skips remain a real-provider proof gap. Canonical tracker requirements are synchronized; the full plan remains active. All changes are local and uncommitted.


MP-02 integration checkpoint: **22/114 tasks verified** after adding the separate T-171/T-172 dependency-baseline pair and verifying MP-02 red proof. Current local authority/rendering implementation and reviews pass; UI component CI, 14 targeted browser cases, 69 UI unit/3 architecture/228 integration cases, six live Gateway cases and 92 isolated economy E2E cases are green. The new MCP advisory is patched through a Semgrep-version-scoped dependency override with all seven audits/scans passing; no new ignore was added. Fresh isolated root attempt 8 stopped at 15 cross-service helper setup errors (missing Gateway auth); the other 32 cases passed and all 806 source/config hashes were unchanged. Helper reconciliation and a fresh root run remain required. MP-02 implementation and T-171/T-172 remain open pending that gate; full Core/Vision scope and all three undecided policies remain unchanged. See evidence/mp02.md in the active OpenSpec change.


Latest MP-02 checkpoint: **22/115 tasks verified**; task4.5 explicitly tracks the additionally reproduced frontend-root escape. Root9 passed phases0–6 with52 cross-service/runtime cases, then exposed168 CSP-incompatible polling failures. Equivalent function predicates preserve every original condition/assertion; full browser rerun passes288. The frontend containment fix separately has6 red cases+2 controls,8 green cases and review approval. Fresh isolated root10 is running against the combined final code with809 source/config hashes snapshotted. MP02/T171–172 closure remains pending. MP06 backup design is independently approved but unimplemented; all remaining Core/Vision and policy/provider gates remain required.


Verified MP-02 checkpoint: **27/115 tasks**. Root attempt10 exited0 with809 unchanged source/config hashes,52 cross-service/runtime,288 browser and92 economy E2E cases; all14 owned service groups drained and were independently absent. T112/T113 and171/T172 are closed with evidence in mp02-checkpoint.md, including the explicit reconstructed-browser-baseline provenance limitation. Three credential-dependent skips do not count as real-provider proof. MP06 backup design and first key-lock red proof are ready; no backup/schema implementation or full Core/Vision completion is claimed. All changes remain uncommitted.

Recovery artifact foundation checkpoint (root attempt 12): **27/117 tasks**, including the new T-173/T-174 read-only CI regression pair. The reviewed backup/restore engine passes its component gate and actual seven-service public-workload restore/adoption proof. Root CI exited 0 with 165 cross-service/runtime, 288 browser and 92 economy cases; all 19 CLI-owned process groups were drained and absent. Three fixture files changed only by Ruff quote normalization (exact original hashes reconstructed; ASTs identical), so this run is not an unchanged-source checkpoint. CI's automatic formatting is being removed under T-173/T-174, with fresh read-only proof required. MP-06 task 5.2 stays open until the actual migration command rejects missing/invalid backups before writable source access. Schema/bootstrap and MP-12 test-first work continue; no full Core/Vision or real-provider completion is claimed. No Git mutation occurred.

Subsequent progress: **29/117 tasks**. T-173's CI mutation proof and T-132's sealed-projection proof are verified. Read-only CI repair passes 35 focused cases, independently rerun by the parent; its full unchanged-source root gate remains pending. MP-12 has 12 independently reproduced failures and three controls, with implementation now underway. Schema/bootstrap and backup-bound offline source checks are independently owned active MP-06 slices; neither constitutes migration authorization or closes task 5.2.

Verified root13 checkpoint: **32/117 tasks**. T-132/T-133 (MP-12 sealed projections) and T-173/T-174 (read-only CI) are closed with their own red/green/review evidence plus fresh root CI exit0. All875 source/config hashes are unchanged;200 cross/runtime,295 browser and94 economy tests pass;19 CLI-owned groups are drained and absent. Three credential-dependent skips remain outside real-provider proof. MP-06 schema/bootstrap, financial audit and backup-bound source prerequisites are reviewed and gated, but actual migration/authoritative writer work is still open. All Core/Vision scope and unresolved policy/provider requirements remain active. No Git mutation occurred.

Verified root15 checkpoint: **36/117 tasks**. MP-06/T-120/T-121 is complete, including actual
migration process-death recovery and maximum-bound concurrency through independent writers.
Root CI exited 0 with all 888 source/config hashes unchanged; 203 cross/runtime, 295 browser and
94 economy cases passed. Both CLI stacks drained their 19 groups and a fresh host check found none
remaining. Independent implementation and acceptance reviews have no unresolved findings. Native
financial receipts (MP-04), lifecycle claims (MP-07), operational restored-runtime recovery (MP-21),
the remaining Core/Vision packages, owner policy decisions and real-provider evaluation remain open.
No Git mutation occurred; the user's office-hours commit/push prohibition remains in force.
