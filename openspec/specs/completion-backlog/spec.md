## Purpose

This spec is the canonical migration of `tickets.md` and `tickets-test-plans.md`. It preserves the known completion backlog, stable T-IDs, dependencies, ratified choices, and proof obligations so the markdown tracker files can be removed.

## Requirements

### Requirement: Repository Gate
The repository SHALL have a green full CI gate before any ticket can be closed.

#### Scenario: T-001 make CI green
- **WHEN** repo state is evaluated for completion readiness
- **THEN** the currently failing phase is captured, fixed, and `just ci-quiet` completes all phases with exit 0
- **AND** the gate is judged by the recipe exit code, not by truncated output

### Requirement: Critical Lifecycle Backlog
The real lifecycle from agents through Task Board, Court, escrow settlement, and reputation SHALL be internally consistent and proven end to end.

#### Scenario: T-010 agent status vocabulary
- **WHEN** agent loops and tests refer to task status
- **THEN** uppercase or nonexistent statuses are replaced with `open`, `accepted`, `submitted`, `approved`, `cancelled`, `disputed`, `ruled`, or `expired`
- **AND** proof includes no legacy status grep hits in `agents/src/`, green agent unit tests, and live feeder/mathbot progression beyond `open`

#### Scenario: T-011 Court mixin and rebuttal
- **WHEN** agents interact with Court
- **THEN** `CourtMixin.file_claim()` uses the full Court payload, `submit_rebuttal()` targets `/disputes/{dispute_id}/rebuttal`, and `MathWorkerLoop` submits rebuttals instead of new claims
- **AND** proof includes green `agents/tests/unit/test_court_mixin_rebuttal.py` and Court e2e without direct `_sign_jws` workarounds

#### Scenario: T-012 Court trust model decision
- **WHEN** Court filing or rebuttal auth is documented
- **THEN** the platform-signed trust model is recorded and specs state it for both filing and rebuttal

#### Scenario: T-013 dispute to Court handoff
- **WHEN** a poster disputes a submitted task through Task Board
- **THEN** exactly one Court dispute is filed for the task by the platform-mediated path
- **AND** proof includes an integration or e2e check that `GET /disputes?task_id=$TASK` returns one dispute

#### Scenario: T-014 specific lifecycle events
- **WHEN** Task Board status transitions occur
- **THEN** events are specific lifecycle events, not generic `task.updated`
- **AND** proof covers accept, submit, approve, auto-approve, dispute, ruling, cancel, and expire event types plus live UI metrics from unseeded live data

#### Scenario: T-015 Court in demo
- **WHEN** the demo claims a full lifecycle dispute path
- **THEN** demo replay implements Court client functions and `file_claim`, `submit_rebuttal`, and `trigger_ruling` actions in scenarios
- **AND** proof runs the full lifecycle scenario with no unknown action and shows a ruled Court dispute

#### Scenario: T-016 timeout outcome
- **WHEN** `MathWorkerLoop` review polling times out
- **THEN** history records `TaskOutcome.TIMEOUT` and does not add full reward as approved earnings

#### Scenario: T-017 MathWorkerLoop and Court mixin tests
- **WHEN** MathWorkerLoop or Court mixin behavior changes
- **THEN** unit tests guard state transitions and honest Court payloads
- **AND** mutation checks prove reverting a status to `BIDDING` fails tests

#### Scenario: T-018 dispute to ruling e2e chain
- **WHEN** the end-to-end dispute chain is tested
- **THEN** the task reaches `disputed`, Court files a dispute, rebuttal is accepted, ruling is produced, task becomes `ruled`, escrow settlement is visible, and Reputation has platform feedback for both parties

### Requirement: Cross-Cutting Decision Backlog
Cross-cutting decisions SHALL be reflected in code, specs, tests, and docs.

#### Scenario: T-020 error-code casing
- **WHEN** error specs or acceptance scripts assert error codes
- **THEN** snake_case is canonical and uppercase normative requirements are absent
- **AND** proof includes decision record, no stale uppercase error-code tables, and shell suite migration tracked by T-070

#### Scenario: T-021 local platform auth
- **WHEN** central-bank or reputation validates platform operations
- **THEN** local PlatformAgent verification is used and stale remote identity verification config is removed
- **AND** proof includes grep checks and runtime auth surviving Identity shutdown where applicable

#### Scenario: T-022 DB Gateway read API
- **WHEN** DB Gateway routes are documented
- **THEN** all implemented read routes are blessed and documented
- **AND** stale claims that reads bypass the gateway are removed

#### Scenario: T-023 escrow split ownership
- **WHEN** Court rulings settle escrow
- **THEN** Task Board owns escrow release or split on ruling
- **AND** Court specs remove unreachable Central Bank split errors while Task Board specs document ruling escrow behavior

#### Scenario: T-024 bid amount model
- **WHEN** bids are specified or tested
- **THEN** integer `amount` is documented and BID-09 through BID-12 cover amount validation

#### Scenario: T-025 ui naming and port
- **WHEN** frontend/observability docs, specs, and compose files are checked
- **THEN** `services/ui` on port 8008 is canonical
- **AND** proxy, sparklines, agent feed, and earnings endpoints are either specified and tested or removed

#### Scenario: T-026 frontend stack decision
- **WHEN** old frontend plans are used
- **THEN** the vanilla-JS decision is recorded and React-targeting plans or mockups carry superseded markers unless reactivated by a new decision

### Requirement: Central Bank Backlog
Central Bank SHALL align ledger, escrow, auth, and config behavior with the service contract.

#### Scenario: T-030 escrow transaction records
- **WHEN** escrow is locked, released, or split
- **THEN** transaction `type` values are `escrow_lock`, `escrow_release`, or split-specific values required by spec, and references are bare task or escrow ids rather than prefixed strings

#### Scenario: T-031 zero-amount split credits
- **WHEN** escrow split assigns 0 percent or 100 percent to the worker
- **THEN** zero-amount credit transactions are not written

#### Scenario: T-032 in-memory ledger parity
- **WHEN** in-memory ledger split logic is used
- **THEN** it enforces poster account parity, uses the same error codes as DB-backed storage, and returns the same response shape

#### Scenario: T-033 account-read auth precedence
- **WHEN** account reads have both bad action and non-owner signer
- **THEN** payload action validation wins before ownership checks as specified

#### Scenario: T-034 fail-fast config
- **WHEN** runtime-required config fields such as `db_gateway` are missing
- **THEN** settings parsing fails immediately instead of failing later in lifespan or request handling

### Requirement: Task Board Backlog
Task Board SHALL align lifecycle, validation, and API docs with current behavior.

#### Scenario: T-035 tasks with bids expire
- **WHEN** an open task with one or more bids passes its bidding deadline
- **THEN** lazy deadline evaluation expires it and releases escrow back to the poster exactly once

#### Scenario: T-036 action aliases
- **WHEN** Task Board validates action names
- **THEN** undocumented aliases such as `file_dispute` and `submit_ruling` are removed or explicitly specified
- **AND** Court e2e still uses canonical action names

#### Scenario: T-037 long title error
- **WHEN** task creation uses a title longer than 200 characters
- **THEN** the error is `invalid_payload`, not `title_too_long`

#### Scenario: T-038 task-board spec reconciliation
- **WHEN** task-list pagination or asset response fields are exposed
- **THEN** `offset`, `limit`, and `content_hash` are documented or removed consistently

### Requirement: Court Backlog
Court SHALL align error semantics, ruling atomicity, and judge configuration with implementation.

#### Scenario: T-039 Court error-code and status cleanup
- **WHEN** a ruling is attempted from an invalid dispute status
- **THEN** the documented invalid-dispute-status error is returned
- **AND** dead or undocumented states such as `rebuttal_submitted` are removed or specified

#### Scenario: T-040 ruling atomicity
- **WHEN** a ruling side effect fails after another side effect has succeeded
- **THEN** the system reaches the explicitly documented retry or compensated state
- **AND** tests match the chosen contract instead of promising impossible rollback

#### Scenario: T-041 judge configuration docs
- **WHEN** Court is configured for tests, local development, or production
- **THEN** docs describe mock provider use, local LM Studio setup, and production model/env requirements

### Requirement: DB Gateway Backlog
DB Gateway SHALL preserve write/event invariants, replay semantics, and route documentation.

#### Scenario: T-042 delete ruling event
- **WHEN** `DELETE /court/rulings/{id}` is retained
- **THEN** it writes an event in the same transaction
- **AND** if removed, the route is absent or returns method/not-found and Court revert still works

#### Scenario: T-043 idempotent replay values
- **WHEN** duplicate registration, credit, or escrow-lock requests are replayed
- **THEN** responses return the original positive `event_id` and real current `balance_after`, not placeholder zeroes

#### Scenario: T-044 DB Gateway spec completion
- **WHEN** DB Gateway specs are updated
- **THEN** they document `POST /court/claims/{id}/status`, all read routes, stale columns such as `bid_count`, `escrow_pending`, and `content_hash`, allowed update columns, and port 8007

#### Scenario: T-045 concurrent duplicate registration
- **WHEN** duplicate registration is attempted concurrently
- **THEN** tests assert exactly one 201 and one 409

### Requirement: UI Backlog
The UI service SHALL align metrics, frontend rendering, and proxy behavior with the API contract.

#### Scenario: T-046 economy phase stalled
- **WHEN** no tasks exist in the relevant recent window
- **THEN** economy phase is `stalled`, not `idle`

#### Scenario: T-047 reward bucket includes 100
- **WHEN** a task reward is exactly 100
- **THEN** it is counted in the `51_to_100` labor-market bucket

#### Scenario: T-048 trend arrow value
- **WHEN** the API emits `increasing`
- **THEN** the frontend recognizes it and renders the upward trend state

#### Scenario: T-049 proxy endpoints
- **WHEN** `/proxy/*` write endpoints remain in UI
- **THEN** each route is specified and integration-tested against the downstream service

### Requirement: Identity Backlog
Identity SHALL document and test the gateway-backed identity contract.

#### Scenario: T-050 verify-jws spec
- **WHEN** Identity exposes `POST /agents/verify-jws`
- **THEN** the endpoint and its behavior IDs are included in API and test specs or marked internal/experimental

#### Scenario: T-051 gateway-backed identity spec
- **WHEN** Identity specs describe persistence
- **THEN** they document DB Gateway dependency and remove local-SQLite or "calls nothing" narratives

### Requirement: Documentation Backlog
Project documentation SHALL match current services, ports, commands, architecture, and decisions.

#### Scenario: T-060 README accuracy
- **WHEN** README is checked
- **THEN** db-gateway is port 8007, ui is port 8008, phantom observatory and `ci-all` commands are gone, service-clients is present, Python is 3.12+, diagrams/TODO/license are resolved, and all listed `just` recipes exist

#### Scenario: T-061 agent instructions accuracy
- **WHEN** AGENTS.md or CLAUDE.md are checked
- **THEN** they describe seven services plus agents and service-clients, remove nonexistent paths and duplicated sections, and remove all beads/bd instructions in favor of OpenSpec or the canonical tracker

#### Scenario: T-062 spec staleness sweep
- **WHEN** service API/auth specs are swept after T-020, T-021, and T-023
- **THEN** casing, local-auth flow, platform config names, db_gateway/logging config, gateway persistence, and port 8007 are consistent across specs

#### Scenario: T-063 CHANGELOG count
- **WHEN** CHANGELOG initial entry is checked
- **THEN** it says seven-service rather than five-service

#### Scenario: T-064 implementation guide
- **WHEN** the service implementation guide is checked
- **THEN** it includes db-gateway, ui, architecture tests, middleware pattern, and service-clients guidance

#### Scenario: T-065 small-doc sweep
- **WHEN** remaining docs are swept
- **THEN** demo docs point to canonical demo flows, sequence diagrams reflect gateway/live wiring, events architecture records DB Gateway as winner, superseded mockups are marked, `pick-up.md` is retired once section 1 lands, and demo replay master/puppet roles are documented

### Requirement: Test Debt Backlog
Acceptance, integration, performance, coverage, lifecycle, and architecture test gaps SHALL be closed with executable tests.

#### Scenario: T-070 shell acceptance suites
- **WHEN** shell acceptance suites run
- **THEN** identity, central-bank, and reputation acceptance scripts pass against live services and assert canonical snake_case error codes

#### Scenario: T-071 integration and performance stubs
- **WHEN** integration/performance suites are checked
- **THEN** one-line stubs are replaced with real tests for identity, central-bank, and db-gateway at minimum

#### Scenario: T-072 pytest coverage gaps
- **WHEN** service tests are checked against test specs
- **THEN** each listed missing spec ID has a passing pytest test referencing it

#### Scenario: T-073 timeout and cancellation e2e
- **WHEN** lifecycle e2e tests are run
- **THEN** auto-approve review timeout releases escrow to worker and task cancellation refunds escrow to poster

#### Scenario: T-074 architecture tests
- **WHEN** architecture tests run
- **THEN** gateway-constraint and no-direct-db tests enforce the intended service boundaries

#### Scenario: T-075 fund-feeder CLI tests
- **WHEN** fund-feeder CLI tests run
- **THEN** arg parsing, non-positive amount errors, and exit paths are covered

### Requirement: Infrastructure and Hygiene Backlog
Infrastructure and cleanup work SHALL keep the repo runnable, lean, and internally consistent.

#### Scenario: T-081 docker-compose dependencies
- **WHEN** Docker Compose config is evaluated
- **THEN** court depends on central-bank, db-gateway and ui dependencies are declared, and reputation's docker config split is consolidated or documented

#### Scenario: T-082 client-layer consolidation
- **WHEN** service client modules are audited
- **THEN** exported client modules have callers or are removed, and task-board no longer hand-rolls a divergent Central Bank client unless it is a thin shim

#### Scenario: T-083 cleanup
- **WHEN** repo hygiene is checked
- **THEN** duplicate working copies, combined logs, stray API key tests, and dead config are removed or explicitly documented

#### Scenario: T-084 semgrep rule audit
- **WHEN** semgrep rules run
- **THEN** `no-default-values.yml` avoids known Pydantic/FastAPI false positives without adding suppressions that dodge the rule

#### Scenario: T-085 db-gateway health logging
- **WHEN** db-gateway health logging is checked
- **THEN** it routes through service_commons logging rather than direct stdlib logger setup

### Requirement: Vision Backlog
Vision-level claims SHALL either be implemented or explicitly scoped as future work.

#### Scenario: T-090 salary distribution
- **WHEN** v1 salary distribution is evaluated
- **THEN** it is either implemented with tests for repeated salary credits or de-scoped from v1 docs as future work while fund-feeder remains the funding mechanism

#### Scenario: T-091 text classification arena
- **WHEN** Demo Scenario 2 is implemented
- **THEN** Regex Ron, Sklearn Sam, and LLM Luna or equivalent worker engines run across rounds and tests assert emergent specialization by tier

#### Scenario: T-092 vision open questions
- **WHEN** the vision doc open questions are checked
- **THEN** dispute filing cost, sealed bids, zero balance, contract caps, judge panel, cadence, and salary sizing each have a decision, rationale, or explicit deferral

#### Scenario: T-093 quarterly report page
- **WHEN** the UI quarterly page is tested
- **THEN** it renders live GDP, Tasks, and Labor sections from `GET /api/quarterly-report` without console errors

#### Scenario: T-094 economy graph animation
- **WHEN** landing page animation is tested
- **THEN** the animation canvas exists, repaints between frames, has no console errors, and stays within the performance budget

#### Scenario: T-095 leaderboard, earnings, and satisfaction UI
- **WHEN** UI e2e tests inspect leaderboard and agent profile views
- **THEN** top agents, worker/poster sorting labels, monthly earnings chart, and satisfaction color tokens render as specified

### Requirement: Migrated Tickets
Tickets migrated from the retired root `tickets.md` tracker (Q-1 decision, 2026-07-10) SHALL keep their history under fresh non-colliding T-IDs.

#### Scenario: T-100 UI empty-economy phase label (migrated from tickets.md#T-001)
- **WHEN** the UI computes the economy phase for a zero-activity economy
- **THEN** it emits `stalled` (never `idle`), per the observatory spec's Economy Phases table and acceptance cases MET-12/MET-13
- **Status:** CLOSED 2026-07-10 — fixed in `9f506c9` alongside ratified T-046; recorded here for history after migration from `tickets.md#T-001` (which reused the T-001 ID already taken by "make CI green" above)

### Requirement: Discovered During Refactoring Execution
Defects and scope gaps discovered while executing the 2026-07-09 refactoring plan SHALL be tracked here with evidence.

#### Scenario: T-101 task-board bid_count never incremented on the gateway path
- **WHEN** a bid is placed against the gateway-backed task board and the task row or list summary is read
- **THEN** `bid_count` reflects the real number of bids (`list_bids` length), instead of staying 0 while only the in-memory store increments it (`in_memory_task_store.py:159-160`) — discovered during the WP-15 e2e (2026-07-10); the acceptance loop works around it by counting `list_bids`; fix belongs with the WP-04 store-parity work

#### Scenario: T-102 deterministic worker path for the unattended-round proof
- **WHEN** the definition-of-done e2e (plan §8 item 2: posted→bid→accepted→submitted→approved/ruled with feeder + mathbot, no demo, no human) runs in CI
- **THEN** a worker can bid/solve without a live LLM: `math_worker`'s `LLMClient` gains an injectable transport (or a deterministic arithmetic worker profile exists), since today `_select_task`/`_decide_bid`/`_solve` are hard-wired to AsyncOpenAI (verified 2026-07-10, WP-15) — lands with WP-09

#### Scenario: T-103 start-all silently skips Court when its .env is missing
- **WHEN** `just start-all` runs in a tree without `services/court/.env` (fresh clone or worktree)
- **THEN** Court still launches: the recipe's `cd services/court && set -a && [ -f .env ] && . .env && set +a && uv run uvicorn ... &` single `&&` chain must not short-circuit at `[ -f .env ]` — discovered 2026-07-12 while verifying WP-06 in a worktree; today only 6 of 7 services start and nothing reports the failure

#### Scenario: T-104 flaky bid-acceptance timing test
- **WHEN** `services/task-board/tests/unit/routers/test_bids.py::TestBidAcceptance::test_accept_after_bidding_deadline_is_rejected` runs under load
- **THEN** it does not race a real `asyncio.sleep(1.5)` against a 1-second deadline with a 0.5s margin (observed flaking once on a loaded machine, 2026-07-13, passing in isolation) — widen the margin or use a clock seam

#### Scenario: T-105 gateway get_transactions omits event_id
- **WHEN** a caller reads `/bank/accounts/{id}/transactions` through the gateway
- **THEN** each transaction row can carry its `event_id` provenance: `DbReader.get_transactions` never SELECTs the column that H-5 added, so the value is unreachable over HTTP (found during the WP-04 normalizer audit, 2026-07-13)

#### Scenario: T-106 role column meaningless on platform force_visible rows
- **WHEN** a court-generated (platform-signed, force_visible) feedback row is stored
- **THEN** its `role` column describes something real: today `_role_for_category` computes the from-agent's role, but the from-agent of a court row is the platform, so a spec_quality ruling row stores role="worker" describing nobody (found 2026-07-13, WP-07). Harmless while `role` is never read back; fix or spec it before any consumer reads `role` (candidate for WP-12's feedback-semantics spec)

#### Scenario: T-107 UI Playwright suite has test-ordering state leakage
- **WHEN** the full `services/ui/tests/e2e` Playwright suite runs together (`pytest tests/e2e -m e2e`, all files)
- **THEN** every test passes regardless of ordering: today 11-13 failures appear in `test_task_lifecycle.py`/`test_observatory.py` from cross-test state leakage (phase strip reads "Settle" early, worker_pct/ruling mismatches, off-by-one reward bucket) — reproduced on the unmodified baseline 2026-07-13 (WP-08), pre-existing; the suite had never been run as a whole (wired to nothing). Fix belongs with WP-13's suite wiring (T-072/F-3 area)

#### Scenario: T-108 drop the click PYSEC ignore once semgrep unpins
- **WHEN** semgrep releases a version whose click requirement admits >=8.3.3
- **THEN** the `--ignore-vuln PYSEC-2026-2132` flag (added 2026-07-13 to every service justfile's code-audit because semgrep 1.169.0 still pins `click~=8.1.8`, making the advisory's fix unreachable in the dev envs) is removed and click upgraded

#### Scenario: T-109 write the ratified vague-spec rubric into the judge prompt
- **WHEN** an LLM judge evaluates a dispute
- **THEN** its system prompt contains the explicit "vague specification" rubric the Q-13 decision ratified (structured criteria, not just the one-sentence ambiguity-favors-worker principle that exists today in `court_service/judges/prompts.py`) — with a test asserting the rubric text reaches the prompt (found unimplemented during the WP-12 sweep, 2026-07-13)

### Requirement: MP-01 Isolated runtime and baseline
The completion backlog SHALL retain the paired proof and implementation obligations for MP-01. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-110 Write acceptance tests for isolated runtime and baseline
- **WHEN** the test ticket for MP-01 is selected
- **THEN** the acceptance proof demonstrates the gap: Two disposable economy runs and an unrelated sentinel demonstrate any shared-port, shared-data or teardown interference, with the current gate failure recorded.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

#### Scenario: T-111 Deliver isolated runtime and baseline
- **WHEN** T-110 is red-proven and the prerequisites are satisfied: none
- **THEN** Independent test runs and teardown preserve each other and unrelated processes; required prerequisites and actual full-gate exit status are recorded against a frozen baseline.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

### Requirement: MP-02 Service and browser authority
The completion backlog SHALL retain the paired proof and implementation obligations for MP-02. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-112 Write acceptance tests for service and browser authority
- **WHEN** the test ticket for MP-02 is selected
- **THEN** the acceptance proof demonstrates the gap: Unsigned or wrong-domain persistence requests, malicious rendered content and off-origin operator actions demonstrate the current unauthorized effects.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

#### Scenario: T-113 Deliver service and browser authority
- **WHEN** T-112 is red-proven and the prerequisites are satisfied: MP-01, MP-03
- **THEN** Only authorized domain callers reach persistence; stored untrusted strings remain inert on every consuming page; Host/Origin and token failures make no data or event changes.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

### Requirement: MP-03 Durable identity and token validation
The completion backlog SHALL retain the paired proof and implementation obligations for MP-03. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-114 Write acceptance tests for durable identity and token validation
- **WHEN** the test ticket for MP-03 is selected
- **THEN** the acceptance proof demonstrates the gap: Concurrent provisioning, partial key material and malformed or inconsistent token fields reproduce identity or validation failures.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

#### Scenario: T-115 Deliver durable identity and token validation
- **WHEN** T-114 is red-proven and the prerequisites are satisfied: MP-01
- **THEN** Concurrent initializers retain one consistent identity, damaged keys fail without replacement, and all verifiers reject invalid token shapes/freshness/algorithms/action audiences without server crashes.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

### Requirement: MP-04 Financial operation receipts
The completion backlog SHALL retain the paired proof and implementation obligations for MP-04. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-116 Write acceptance tests for financial operation receipts
- **WHEN** the test ticket for MP-04 is selected
- **THEN** the acceptance proof demonstrates the gap: Lost responses after committed release/split and public lock replay after settlement expose duplicated or nonconvergent financial commands.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-117 Deliver financial operation receipts
- **WHEN** T-116 is red-proven and the prerequisites are satisfied: MP-02, MP-06
- **THEN** Exact Bank-operation replay returns the original receipt and ledger/event identities; changed content conflicts; concurrent opposing commands have one financial winner without duplicated movement.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-05 Recoverable task publication
The completion backlog SHALL retain the paired proof and implementation obligations for MP-05. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-118 Write acceptance tests for recoverable task publication
- **WHEN** the test ticket for MP-05 is selected
- **THEN** the acceptance proof demonstrates the gap: Failures before and after acknowledged or ambiguous publication, including failed compensation, expose orphaned escrow or incorrect refunds.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-119 Deliver recoverable task publication
- **WHEN** T-118 is red-proven and the prerequisites are satisfied: MP-04, MP-07
- **THEN** Recovery produces one funded published task or one documented refund; the same posting identity cannot create a duplicate job or refund an already published matching task.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-01 Read-only CI checks
CI SHALL reject formatting and lint failures without modifying source files. Explicit formatting commands SHALL remain available separately. Implementation T-174 depends on red-proven test ticket T-173; both retain the universal closure gate.

#### Scenario: T-173 Write acceptance tests for read-only CI formatting
- **WHEN** verbose or quiet service CI receives deliberately malformed source in an isolated disposable project
- **THEN** the acceptance proof demonstrates the existing auto-format mutation and requires a nonzero result with byte-identical source instead
- **AND** the exact command, before/after source hashes and failure are recorded before implementation
- **Status:** CLOSED — Own red proof, green acceptance and independent review are recorded; root13 exited 0 with 875 unchanged source/config hashes. See evidence/root13-checkpoint.md in the active change.

#### Scenario: T-174 Keep CI formatting checks read-only
- **WHEN** T-173 is red-proven
- **THEN** every service CI path uses the existing read-only style/format check, malformed source fails without rewriting it, and explicit code-format still performs requested formatting
- **AND** project structure validation and fresh isolated root CI pass with identical source hashes before and after
- **Status:** CLOSED — Own red proof, green acceptance and independent review are recorded; root13 exited 0 with 875 unchanged source/config hashes. See evidence/root13-checkpoint.md in the active change.

### Requirement: MP-06 Schema and monetary integrity
The completion backlog SHALL retain the paired proof and implementation obligations for MP-06. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-120 Write acceptance tests for schema and monetary integrity
- **WHEN** the test ticket for MP-06 is selected
- **THEN** the acceptance proof demonstrates the gap: Wrong escrow payer, inconsistent initial issuance, boolean/overflow values, partial schemas and invalid lifetime escrow uniqueness expose authoritative integrity gaps.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED — Own 12 intended failures/6 controls and subsequent targeted proof are retained; independent review and root14 exit0 with 886 unchanged source/config hashes satisfy this test ticket's closure gate. T-121's additional concurrent-lock/settlement and process-death proofs were subsequently gated at root15. See evidence/root14-checkpoint.md and evidence/root15-checkpoint.md in the active change.

#### Scenario: T-121 Deliver schema and monetary integrity
- **WHEN** T-120 is red-proven and the prerequisites are satisfied: MP-01
- **THEN** A verified restorable backup precedes real migration; supported schemas migrate safely and unknown schemas fail readiness; all money stays nonnegative integer; authorized issuance minus recorded burn equals spendable balances plus locked escrow; invalid commands and replay create no duplicate ledger/event/receipt legs.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED — Backup-bound migration, schema/data readiness, integer money guards, historical diagnostics, actual process-death recovery and authoritative maximum-bound concurrency are implemented and independently approved. Root15 exited 0 with 888 unchanged source/config hashes. See evidence/root15-checkpoint.md in the active change; durable settlement receipts and operational restored-runtime recovery remain their separate MP-04/MP-21 gates.

### Requirement: MP-07 Atomic task transitions
The completion backlog SHALL retain the paired proof and implementation obligations for MP-07. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-122 Write acceptance tests for atomic task transitions
- **WHEN** the test ticket for MP-07 is selected
- **THEN** the acceptance proof demonstrates the gap: Controlled conflicting acceptance/cancellation, submission/expiry and approval/dispute histories expose incompatible successful transitions.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-123 Deliver atomic task transitions
- **WHEN** T-122 is red-proven and the prerequisites are satisfied: MP-06
- **THEN** Each concurrent history has one valid serial outcome; losing operations return explicit conflicts with no contradictory side effects; cutoff equality and acceptance eligibility are consistent.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-08 Pending financial effect recovery
The completion backlog SHALL retain the paired proof and implementation obligations for MP-08. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-124 Write acceptance tests for pending financial effect recovery
- **WHEN** the test ticket for MP-08 is selected
- **THEN** the acceptance proof demonstrates the gap: A terminal task with a failed or ambiguously acknowledged payout/refund remains unfinished without reads or after process restart.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-125 Deliver pending financial effect recovery
- **WHEN** T-124 is red-proven and the prerequisites are satisfied: MP-04, MP-07
- **THEN** Pending financial/task effects are recovered regardless of business status, with one receipt, bounded retries and visible persistent failures; this package does not claim Court rebuttal-delivery completion.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-09 Safe immutable artifacts
The completion backlog SHALL retain the paired proof and implementation obligations for MP-09. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-126 Write acceptance tests for safe immutable artifacts
- **WHEN** the test ticket for MP-09 is selected
- **THEN** the acceptance proof demonstrates the gap: Hostile filenames, concurrent quotas, state races, failed metadata/file publication and missing poster input paths demonstrate unsafe or incomplete evidence handling.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-127 Deliver safe immutable artifacts
- **WHEN** T-126 is red-proven and the prerequisites are satisfied: MP-07; Q-M1 before policy closure
- **THEN** Supported inputs and submissions have immutable attributable manifests and exact retrievable bytes; no outside-root writes or quota/state bypass occurs; interrupted uploads reconcile; authorized users can download safely under the accepted artifact policy.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-10 Recoverable immutable adjudication
The completion backlog SHALL retain the paired proof and implementation obligations for MP-10. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-128 Write acceptance tests for recoverable immutable adjudication
- **WHEN** the test ticket for MP-10 is selected
- **THEN** the acceptance proof demonstrates the gap: Concurrent ruling triggers, process death and changed model outputs on retry expose conflicting decisions; mismatched claim context and lost rebuttal acknowledgments are rejected or stranded incorrectly.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-129 Deliver recoverable immutable adjudication
- **WHEN** T-128 is red-proven and the prerequisites are satisfied: MP-04, MP-07, MP-08, MP-09, MP-11
- **THEN** One fenced adjudication owns immutable evidence and a persisted decision before delivery; retries do not rejudge; all economic records agree; mismatched task/party/escrow/version causes no writes; identical rebuttal replay preserves the original timestamp and changed content conflicts.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-11 Admissible judge evidence and output
The completion backlog SHALL retain the paired proof and implementation obligations for MP-11. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-130 Write acceptance tests for admissible judge evidence and output
- **WHEN** the test ticket for MP-11 is selected
- **THEN** the acceptance proof demonstrates the gap: Truncated or unsupported decisive evidence, injected instructions, malformed model output and out-of-bound provider operation expose unsupported rulings.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-131 Deliver admissible judge evidence and output
- **WHEN** T-130 is red-proven and the prerequisites are satisfied: MP-09; Q-M1 and Q-M3 before contract closure
- **THEN** Judge evidence retains hashes/provenance/media types and explicit completeness; untrusted data cannot redefine the accepted rubric; validated outputs and provider time/context/cost limits apply; unsupported evidence blocks automatic judgment without silent mock fallback.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-12 Sealed public projections
The completion backlog SHALL retain the paired proof and implementation obligations for MP-12. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-132 Write acceptance tests for sealed public projections
- **WHEN** the test ticket for MP-12 is selected
- **THEN** the acceptance proof demonstrates the gap: An unrelated observer can retrieve open bids or sealed feedback content through UI/API/event history or streaming paths.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED — Own red proof, green acceptance and independent review are recorded; root13 exited 0 with 875 unchanged source/config hashes. See evidence/root13-checkpoint.md in the active change.

#### Scenario: T-133 Deliver sealed public projections
- **WHEN** T-132 is red-proven and the prerequisites are satisfied: MP-02
- **THEN** Only authorized poster views reveal open bid amounts/identities, while all public projections and aggregates honor sealing and later reveal consistently, including reconnect and history.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED — Own red proof, green acceptance and independent review are recorded; root13 exited 0 with 875 unchanged source/config hashes. See evidence/root13-checkpoint.md in the active change.

### Requirement: MP-13 Authorized durable feedback
The completion backlog SHALL retain the paired proof and implementation obligations for MP-13. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-134 Write acceptance tests for authorized durable feedback
- **WHEN** the test ticket for MP-13 is selected
- **THEN** the acceptance proof demonstrates the gap: Unrelated or premature feedback, wrong recipients/categories, duplicate Court deliveries and one-sided timeout visibility expose authorization or projection inconsistencies.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-135 Deliver authorized durable feedback
- **WHEN** T-134 is red-proven and the prerequisites are satisfied: MP-07, MP-10, MP-12
- **THEN** Only eligible task parties and finalized-ruling sources create appropriately directed feedback; exact replay is stable; reveal is durable and every intended API/UI/score view agrees after restart.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-14 Independent quality scores
The completion backlog SHALL retain the paired proof and implementation obligations for MP-14. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-136 Write acceptance tests for independent quality scores
- **WHEN** the test ticket for MP-14 is selected
- **THEN** the acceptance proof demonstrates the gap: Absent or divergent standing, payout-confounded dimensions and duplicated/hidden observations demonstrate the missing accepted score contract.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-137 Deliver independent quality scores
- **WHEN** T-136 is red-proven and the prerequisites are satisfied: MP-11, MP-13; Q-M3
- **THEN** A versioned service-owned query returns separate specification/delivery dimensions, observation counts and provenance; unmeasured standing is distinguishable; sealed or duplicate evidence cannot influence scores incorrectly; all consumers agree.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-15 Complete human participation
The completion backlog SHALL retain the paired proof and implementation obligations for MP-15. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-138 Write acceptance tests for complete human participation
- **WHEN** the test ticket for MP-15 is selected
- **THEN** the acceptance proof demonstrates the gap: Real browser actions cannot finish the operator poster and worker journeys, inspect artifacts/payments, or recover actionable errors and ambiguous writes.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-139 Deliver complete human participation
- **WHEN** T-138 is red-proven and the prerequisites are satisfied: MP-04, MP-05, MP-09, MP-12, MP-13, MP-19; MP-14 for scores
- **THEN** Through the UI the operator posts/works as itself, sees funding and real bids, transfers supported artifacts, accepts/submits/approves/disputes/rebuts/cancels/gives feedback when entitled, and sees matching receipts with no duplicate effects or impersonation.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-16 Independent external agent participation
The completion backlog SHALL retain the paired proof and implementation obligations for MP-16. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-140 Write acceptance tests for independent external agent participation
- **WHEN** the test ticket for MP-16 is selected
- **THEN** the acceptance proof demonstrates the gap: An unlisted participant on a separate host/network namespace exposes any reliance on server roster, shared files, gateway/operator access or privileged keys.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-141 Deliver independent external agent participation
- **WHEN** T-140 is red-proven and the prerequisites are satisfied: MP-02, MP-03, MP-04, MP-05, MP-09
- **THEN** Using only its own persistent keys and documented agent-facing URLs, an external agent completes work, reads its payment and posts a funded job; any initial grant is administrator-invoked and platform-signed; zero-balance working remains allowed.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-17 Worker commitment recovery
The completion backlog SHALL retain the paired proof and implementation obligations for MP-17. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-142 Write acceptance tests for worker commitment recovery
- **WHEN** the test ticket for MP-17 is selected
- **THEN** the acceptance proof demonstrates the gap: Restart after bid, acceptance, upload, submission or dispute abandons work, duplicates actions or records unearned income.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-143 Deliver worker commitment recovery
- **WHEN** T-142 is red-proven and the prerequisites are satisfied: MP-07, MP-08, MP-09, MP-10, MP-16
- **THEN** The restarted worker discovers and resumes owned commitments through one correct terminal outcome, preserves artifact/rebuttal identities and records earnings from receipts within explicit execution budgets.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-18 Feeder acceptance and review recovery
The completion backlog SHALL retain the paired proof and implementation obligations for MP-18. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-144 Write acceptance tests for feeder acceptance and review recovery
- **WHEN** the test ticket for MP-18 is selected
- **THEN** the acceptance proof demonstrates the gap: Restart loses review evidence, or cutoff/tie/selection failures prevent timely and deterministic autonomous progress.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-145 Deliver feeder acceptance and review recovery
- **WHEN** T-144 is red-proven and the prerequisites are satisfied: MP-05, MP-07, MP-13, MP-14, MP-16
- **THEN** The feeder resumes posted work and durable review evidence, honors configured window/quorum and lowest-bid/quality tie rules, and approves correct or disputes incorrect work without memory-loss timeout approval or duplicate posting.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-19 Accurate observatory and event delivery
The completion backlog SHALL retain the paired proof and implementation obligations for MP-19. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-146 Write acceptance tests for accurate observatory and event delivery
- **WHEN** the test ticket for MP-19 is selected
- **THEN** the acceptance proof demonstrates the gap: Known outcome fixtures and reconnect races expose inaccurate bid/payment values, completion/employment denominators or duplicated/missed events.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-147 Deliver accurate observatory and event delivery
- **WHEN** T-146 is red-proven and the prerequisites are satisfied: MP-08, MP-12, MP-13
- **THEN** Every view uses explicit consistent outcome and monetary definitions, correct zero-bid/distinct-worker counts, durable feedback visibility and a bounded resumable deduplicated event view.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-20 Strict service contracts and responsive task processing
The completion backlog SHALL retain the paired proof and implementation obligations for MP-20. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-148 Write acceptance tests for strict service contracts and responsive task processing
- **WHEN** the test ticket for MP-20 is selected
- **THEN** the acceptance proof demonstrates the gap: Malformed success bodies and conflated errors appear as plausible empty/zero results, and a slow persistence request stalls unrelated Task Board requests or deadline progress.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-149 Deliver strict service contracts and responsive task processing
- **WHEN** T-148 is red-proven and the prerequisites are satisfied: producer contracts from MP-04, MP-07, MP-10
- **THEN** Invalid upstream contracts fail visibly and distinctly; production/fake contracts agree; Task Board health, unrelated requests and deadline progress meet the documented bounds during slow dependency failures.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-21 Operational readiness and restoration
The completion backlog SHALL retain the paired proof and implementation obligations for MP-21. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-150 Write acceptance tests for operational readiness and restoration
- **WHEN** the test ticket for MP-21 is selected
- **THEN** the acceptance proof demonstrates the gap: Missing services, wrong port ownership, incomplete schema/keys/assets and interrupted restoration expose false readiness or unrecoverable work.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-151 Deliver operational readiness and restoration
- **WHEN** T-150 is red-proven and the prerequisites are satisfied: MP-01, MP-03, MP-06, MP-08, MP-10
- **THEN** Startup/teardown honor exact process ownership and readiness; restore recovers matching keys/evidence/money and pending work once; migrations preserve original backups; diagnostics and recovery operations are actionable and do not expose secrets.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-22 Honest comprehensive verification
The completion backlog SHALL retain the paired proof and implementation obligations for MP-22. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-152 Write acceptance tests for honest comprehensive verification
- **WHEN** the test ticket for MP-22 is selected
- **THEN** the acceptance proof demonstrates the gap: Known faults, early command failures, order leakage or source-mutating checks escape or undermine the current quality gate.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-153 Deliver honest comprehensive verification
- **WHEN** T-152 is red-proven and the prerequisites are satisfied: MP-01 through MP-21; MP-23 through MP-26 are consumers
- **THEN** The isolated root quality gate fails on injected known faults, propagates setup failures, preserves source and unrelated data, and passes all required suites with deterministic concurrency/restart and browser coverage without weakened assertions or skips.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-23 Complete platform journeys
The completion backlog SHALL retain the paired proof and implementation obligations for MP-23. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-154 Write acceptance tests for complete platform journeys
- **WHEN** the test ticket for MP-23 is selected
- **THEN** the acceptance proof demonstrates the gap: Public-interface journeys in the release matrix expose missing integration without direct database transitions or fake browser writes.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-155 Deliver complete platform journeys
- **WHEN** T-154 is red-proven and the prerequisites are satisfied: MP-15 through MP-22 and MP-25
- **THEN** Fresh human/external-agent poster and worker journeys, approval/dispute/timeout/cancellation, restarts and lost responses pass with matching receipts/evidence/events/UI state, the evaluated real Court profile and a fresh full-root gate.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-24 Measured non-math economic behavior
The completion backlog SHALL retain the paired proof and implementation obligations for MP-24. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-156 Write acceptance tests for measured non-math economic behavior
- **WHEN** the test ticket for MP-24 is selected
- **THEN** the acceptance proof demonstrates the gap: The non-math input/output and multi-round examples expose unsupported participation or claims of price formation/specialization that the active policy does not justify.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-157 Deliver measured non-math economic behavior
- **WHEN** T-156 is red-proven and the prerequisites are satisfied: MP-04, MP-14, MP-16, MP-17, MP-18; Q-M2 and Q-M3
- **THEN** Real public APIs drive reproducible non-math work and measured adaptation/quality/cost; pricing matches the accepted fixed-reward or post-v1 contract, with conserved rounding/refunds and protected outstanding tasks; failed hypotheses are reported honestly.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-25 Empirical real judge readiness
The completion backlog SHALL retain the paired proof and implementation obligations for MP-25. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-158 Write acceptance tests for empirical real judge readiness
- **WHEN** the test ticket for MP-25 is selected
- **THEN** the acceptance proof demonstrates the gap: The baseline real panel is evaluated against predeclared precise/vague, compliant/noncompliant and malicious-evidence cases; fixed mock output is excluded from quality proof.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-159 Deliver empirical real judge readiness
- **WHEN** T-158 is red-proven and the prerequisites are satisfied: MP-10, MP-11, MP-14
- **THEN** A configured three-judge real profile satisfies predeclared quality/robustness and operating-budget criteria with traceable immutable evidence, valid output, recoverable provider failure and independently verified retry stability.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-26 Consistent specifications and evidence
The completion backlog SHALL retain the paired proof and implementation obligations for MP-26. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-160 Write acceptance tests for consistent specifications and evidence
- **WHEN** the test ticket for MP-26 is selected
- **THEN** the acceptance proof demonstrates the gap: A behavior/spec/test/ticket and command/link audit exposes stale claims or missing current proof.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-161 Deliver consistent specifications and evidence
- **WHEN** T-160 is red-proven and the prerequisites are satisfied: MP-23; MP-24 and MP-25 for full Vision closure
- **THEN** Current specifications, guides and canonical tickets map every required outcome to fresh proof or an explicit open/ratified-deferral state; all accepted policies are reflected; final claims match the selected Core/Vision gate and office-hour history rules.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: Explicit completion policy decisions
The completion backlog SHALL track unresolved evidence/privacy, post-v1 pricing and quality-aggregation decisions with an accountable project owner, accepted examples and dependent-task gates. Existing local single-operator, manual issuance and fixed-reward v1 decisions SHALL remain effective until explicitly superseded.

#### Scenario: T-162 Decide Q-M1 evidence admissibility and artifact privacy
- **WHEN** the accountable project owner resolves Q-M1
- **THEN** the accepted decision defines supported formats/limits, immutable provenance, participant and bidder access, retention, external-provider disclosure and unsupported-evidence outcomes before MP-09 policy and MP-11 close
- **AND** the accepted record is linked from the affected specifications and implementation proofs without closing those implementations prematurely
- **Status:** OPEN — Explicit owner decision pending; no default option is accepted.

#### Scenario: T-163 Decide Q-M2 post-v1 price-forming economics
- **WHEN** the accountable project owner resolves Q-M2
- **THEN** the accepted decision defines whether post-v1 price formation is enabled, including excess-refund timing, rounding and existing-contract treatment before MP-24 pricing claims close; fixed-reward Core remains valid
- **AND** the accepted record is linked from the affected specifications and implementation proofs without closing those implementations prematurely
- **Status:** OPEN — Explicit owner decision pending; no default option is accepted.

#### Scenario: T-164 Decide Q-M3 independent quality aggregation
- **WHEN** the accountable project owner resolves Q-M3
- **THEN** the accepted versioned contract defines separate quality dimensions, payout separation, source weighting, no-history treatment and scored examples before MP-11 and MP-14 close
- **AND** the accepted record is linked from the affected specifications and implementation proofs without closing those implementations prematurely
- **Status:** OPEN — Explicit owner decision pending; no default option is accepted.

### Requirement: MP-01 Discovered deadline precision baseline
The completion backlog SHALL track the independently repairable deadline truncation exposed by the isolated root baseline as T-169/T-170. This prerequisite SHALL preserve configured durations without weakening existing expiry assertions. It SHALL NOT close or bypass MP-07's schema, atomic-transition or concurrency obligations.

#### Scenario: T-169 Prove full deadline duration at fractional timestamps
- **WHEN** a task phase starts at a timestamp with fractional seconds and has a configured duration
- **THEN** deterministic tests demonstrate that the current calculation can expire it before the full duration has elapsed
- **AND** just-before, exact-cutoff and whole-second compatibility cases are recorded before implementation
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

#### Scenario: T-170 Preserve deadline precision in the baseline
- **WHEN** T-169 is red-proven
- **THEN** the shared deadline calculation preserves the full configured duration and existing expiry, refund and event assertions remain intact
- **AND** focused tests, Task Board CI and a fresh isolated root gate pass before closure
- **AND** broader atomic state-transition requirements remain open under T-122/T-123
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

### Requirement: MP-00 Reported UI configuration fixture repair
The completion backlog SHALL prioritize reproducing and repairing the reported invalid UI test fixtures while retaining required production configuration and all existing health/configuration/logging assertions. T-166 SHALL depend on T-165 red proof; fixture-only corrections SHALL follow the recorded test-spec reconciliation.

#### Scenario: T-165 Prove UI fixtures omit required operator configuration
- **WHEN** the reported configuration, health-setup and successful-initialization cases are reproduced against the current baseline
- **THEN** the four missing-operator-section failures are recorded separately from their intended assertions
- **AND** an otherwise complete extra-field case proves its intended validation error and all required-section negative cases remain covered
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

#### Scenario: T-166 Repair explicit valid UI fixtures
- **WHEN** T-165 is red-proven and the narrow fixture-only reconciliation is recorded
- **THEN** health, valid configuration and successful initialization tests reach and pass their unchanged behavior assertions with explicit isolated operator settings
- **AND** missing required sections still fail, partial-section behavior remains separately covered and fixture state is cleaned after failures
- **AND** UI and isolated root quality gates pass before closure
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

### Requirement: MP-00 Reported UI browser prerequisite repair
The completion backlog SHALL prioritize the missing Chromium failure separately from configuration and JavaScript behavior. T-168 SHALL depend on T-167 proof of the missing or mismatched browser prerequisite; no trend assertion or browser test SHALL be skipped to restore a green gate.

#### Scenario: T-167 Prove browser prerequisite failure
- **WHEN** the three reported trend cases are run with an isolated missing or mismatched browser cache
- **THEN** the failure is identified as browser startup before the shipped JavaScript assertions
- **AND** local unit-CI prerequisite ordering and the installed browser revision are recorded
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

#### Scenario: T-168 Prepare and verify matching Chromium before unit CI
- **WHEN** T-167 is red-proven and explicit browser preparation has run for the installed package version
- **THEN** early local and hosted preflight verifies the matching browser and all increasing/decreasing/stable assertions run against the shipped JavaScript and pass
- **AND** absent or stale browser revisions cause an actionable nonzero prerequisite failure without skips, fake success or weakened assertions
- **AND** the complete UI unit suite, UI quality gate and isolated root quality gate pass before closure
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

### Requirement: MP-01 Audit-discovered development dependency repair
The completion backlog SHALL retain a separate fail-first obligation for the dependency finding surfaced during MP-02 verification. No vulnerability suppression or untested dependency override satisfies this baseline repair.

#### Scenario: T-171 Prove the development MCP advisory blocks CI
- **WHEN** the service dependency audit runs with Semgrep's pinned mcp 1.29.0
- **THEN** the gate reports GHSA-84m7-p3x7-pcfv and exits nonzero
- **AND** official dependency/advisory evidence identifies the affected environments, patched version and compatibility constraints before any override
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

#### Scenario: T-172 Repair the affected development dependency
- **WHEN** T-171 is red-proven and a compatible patched dependency is verified
- **THEN** affected service environments use the patched package through reproducible uv-generated locks, their configured Semgrep rules and audits pass, and no new vulnerability ignore is added
- **AND** any override of an upstream exact pin is explicit, limited to the development tool and documented with compatibility evidence and a removal condition
- **AND** a fresh isolated root `just ci-quiet` exits zero before closure
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.
