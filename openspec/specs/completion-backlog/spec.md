## Purpose

> **Owner scope update — 2026-10-23:** Follow `MASTERPLAN-TO-COMPLETE-THE-PROJECT.md` and `FEATURE-COMPLETENESS.md` for feature-first execution. Existing data is disposable test data: old-schema/data migration, historical asset conversion and backward compatibility are out of scope. Initialize fresh databases directly. Reuse existing tests and add only missing basic feature checks; no exhaustive fault matrices or repeated planning/review gates. Older migration/testing obligations below are historical where they conflict with this update. Completed work stays complete; no feature is closed by this scope change.


This spec is the canonical migration of `tickets.md` and `tickets-test-plans.md`. It preserves the known completion backlog, stable T-IDs, dependencies, ratified choices, and proof obligations so the markdown tracker files can be removed.

## Requirements

### Requirement: Office-hours Git guard
Commit and push hooks SHALL reject Monday through Friday from 08:00 inclusive
until 18:00 exclusive in Europe/Amsterdam, permit weekends and other hours,
and fail closed if the clock cannot be read. T-190 depends on red-proven T-189.

#### Scenario: T-189 Write acceptance tests for the office-hours Git guard
- **WHEN** either hook runs at a weekday boundary, during the weekend, or with an invalid clock result
- **THEN** deterministic tests assert the agreed allowance or rejection without attempting a commit or push
- **AND** the missing hook baseline is preserved separately from behavioral verification
- **Status:** CLOSED 2026-10-22 — Missing-hook capability baseline is explicit; all30 boundary/weekend/clock-error checks pass with independent review. Fresh isolated root37981 exited0 with1180 unchanged source/config hashes and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp05-posting/root-ci-2026-10-22.md).

#### Scenario: T-190 Restore the office-hours Git hooks
- **WHEN** T-189 is red-proven
- **THEN** both executable hooks share the Europe/Amsterdam guard and this checkout resolves Git hooks to that directory
- **AND** existing hooks are never silently overwritten, boundaries pass, and fresh isolated root CI passes before closure
- **Status:** CLOSED 2026-10-22 — Both executable hooks are installed through this checkout’s core.hooksPath; live-clock checks and root tests pass without a commit or push. Fresh isolated root37981 exited0 with1180 unchanged source/config hashes and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp05-posting/root-ci-2026-10-22.md).

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
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit 0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

#### Scenario: T-113 Deliver service and browser authority
- **WHEN** T-112 is red-proven and the prerequisites are satisfied: MP-01, MP-03
- **THEN** Only authorized domain callers reach persistence; stored untrusted strings remain inert on every consuming page; Host/Origin and token failures make no data or event changes.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit 0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

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
- **Status:** CLOSED 2026-10-17 — Own genuine replay/identity/history red proof, green coverage, independent review and root19 exit 0 verified. See evidence/mp04-receipts/completion-audit.md and evidence/root19-checkpoint.md in the active change; evidence limitations remain explicit.

#### Scenario: T-117 Deliver financial operation receipts
- **WHEN** T-116 is red-proven and the prerequisites are satisfied: MP-02, MP-06
- **THEN** Exact Bank-operation replay returns the original receipt and ledger/event identities; changed content conflicts; concurrent opposing commands have one financial winner without duplicated movement.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-17 — Native/historical receipts, consumers, public opposing-settlement proof and documentation are implemented and independently approved. Root19 passed with932 unchanged source/config hashes and19 owned stack groups absent. See the active change's evidence/mp04-receipts/completion-audit.md and evidence/root19-checkpoint.md; lifecycle claims/recovery remain MP-07/08.

### Requirement: MP-05 Recoverable task publication
The completion backlog SHALL retain the paired proof and implementation obligations for MP-05. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-118 Write acceptance tests for recoverable task publication
- **WHEN** the test ticket for MP-05 is selected
- **THEN** the acceptance proof demonstrates the gap: Failures before and after acknowledged or ambiguous publication, including failed compensation, expose orphaned escrow or incorrect refunds.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED 2026-10-22 — Original publication/compensation behavioral reds are independently verified; later harness evidence limitations remain explicit. Fresh isolated root37981 exited0 with1180 unchanged source/config hashes and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp05-posting/completion-audit.md).

#### Scenario: T-119 Deliver recoverable task publication
- **WHEN** T-118 is red-proven and the prerequisites are satisfied: MP-04, MP-07
- **THEN** Recovery produces one funded published task or one documented refund; the same posting identity cannot create a duplicate job or refund an already published matching task.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-22 — Durable Gateway, SDK, feeder, UI and demo posting plus exact replay, historical refunds, crash recovery and conservation are independently verified. Fresh isolated root37981 exited0 with1180 unchanged source/config hashes and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp05-posting/completion-audit.md).

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
- **Status:** CLOSED — Own 12 intended failures/6 controls and subsequent targeted proof are retained; independent review and root14 exit 0 with 886 unchanged source/config hashes satisfy this test ticket's closure gate. T-121's additional concurrent-lock/settlement and process-death proofs were subsequently gated at root15. See evidence/root14-checkpoint.md and evidence/root15-checkpoint.md in the active change.

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
- **Status:** CLOSED — Genuine multi-instance atomic/deadline/bid/window reds, serial-history proofs and the fresh Root22 gate are independently verified. Fixture/setup failures are separately classified. [Closure evidence](../../changes/complete-agent-economy/evidence/mp07-transitions/completion-audit.md).

#### Scenario: T-123 Deliver atomic task transitions
- **WHEN** T-122 is red-proven and the prerequisites are satisfied: MP-06
- **THEN** Each concurrent history has one valid serial outcome; losing operations return explicit conflicts with no contradictory side effects; cutoff equality and acceptance eligibility are consistent.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED — All eleven native transitions, claim-before-effect coordination, authoritative receipt/Court evidence, exact replay, selected acceptance windows, migration/audit and public producer rollout are implemented and independently verified in Root22. General posting/effect/adjudication recovery remains in its later packages. [Closure evidence](../../changes/complete-agent-economy/evidence/mp07-transitions/completion-audit.md).

### Requirement: MP-08 Pending financial effect recovery
The completion backlog SHALL retain the paired proof and implementation obligations for MP-08. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-124 Write acceptance tests for pending financial effect recovery
- **WHEN** the test ticket for MP-08 is selected
- **THEN** the acceptance proof demonstrates the gap: A terminal task with a failed or ambiguously acknowledged payout/refund remains unfinished without reads or after process restart.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED 2026-10-22 — Chronological behavioral reds and independently accepted retrospective equivalence prove the pending-work gap. The original exact-command/legacy-builder limitation remains explicit. Root R4 exits 0 with 1261 unchanged source files and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp08-recovery/completion-audit.md).

#### Scenario: T-125 Deliver pending financial effect recovery
- **WHEN** T-124 is red-proven and the prerequisites are satisfied: MP-04, MP-07
- **THEN** Pending financial/task effects are recovered regardless of business status, with one receipt, bounded retries and visible persistent failures; this package does not claim Court rebuttal-delivery completion.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-22 — V5 fenced financial recovery, receipt-first finalization, fair bounded scheduling, operator diagnostics/retry and truthful Board/UI projections are verified, including all 14 process cuts. Root R4 exits 0; Court adjudication/rebuttal delivery remains MP-10. [Closure evidence](../../changes/complete-agent-economy/evidence/mp08-recovery/completion-audit.md).

### Requirement: MP-09 Safe immutable artifacts
The completion backlog SHALL retain the paired proof and implementation obligations for MP-09. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-126 Write acceptance tests for safe immutable artifacts
- **WHEN** the test ticket for MP-09 is selected
- **THEN** the acceptance proof demonstrates the gap: Hostile filenames, concurrent quotas, state races, failed metadata/file publication and missing poster input paths demonstrate unsafe or incomplete evidence handling.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN, partial red proof through 2026-10-23 — Parent reproduced storage, quota/lifecycle, upload-ledger, publication/receipt, native/legacy acceptance and legacy-continuation failures before bounded repairs; oracle corrections retain original evidence and explicit reclassification. Parent848 and full Gateway CI14672 pass with 332 inputs/path set unchanged, and independent source review approves new-upload legacy continuation. Evidence: `openspec/changes/complete-agent-economy/evidence/mp09-artifacts/`. Live streaming/byte publication, poster inputs, immutable submission, grants and archive checks remain open; historical migration is excluded by the owner; this does not close T-126 or T-127.

#### Scenario: T-127 Deliver safe immutable artifacts
- **WHEN** T-126 is red-proven and the prerequisites are satisfied: MP-07; Q-M1 before policy closure
- **THEN** Supported inputs and submissions have immutable attributable manifests and exact retrievable bytes; no outside-root writes or quota/state bypass occurs; interrupted uploads reconcile; authorized users can download safely under the accepted artifact policy.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Bounded path/storage, upload-ledger, input-publication, manifest, receipt-adoption and native acceptance-binding components are implemented and verified at component scope. Actual byte staging, public input/upload routes, immutable submission, fresh current-schema runtime admission, grants/archive and the final root gate remain outstanding. Historical conversion, legacy continuation and mixed historical manifests are no longer release prerequisites.

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
- **THEN** Startup/teardown honor exact process ownership and readiness; restore recovers matching keys/evidence/money and pending work once; current-format backups preserve the running economy; diagnostics and recovery operations are actionable and do not expose secrets.
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

### Requirement: Current dependency audit baseline
The completion work SHALL resolve newly reported dependency vulnerabilities without
weakening audit coverage or adding vulnerability exclusions.

#### Scenario: T-191 Write acceptance evidence for the OpenTelemetry audit regression
- **WHEN** the affected installed service environments are audited before dependency changes
- **THEN** the audit records opentelemetry-api 1.37.0 and GHSA-xcq7-p3pf-p88h as a failing baseline
- **AND** official advisory and package metadata identify a coherent corrected dependency family and all affected consumers
- **Status:** CLOSED 2026-10-22 — Exact vulnerable OpenTelemetry audit and official coherent-family compatibility evidence retained; seven service consumers verified. Root R4 exits 0 without audit exclusions. [Closure evidence](../../changes/complete-agent-economy/evidence/mp08-recovery/completion-audit.md).

#### Scenario: T-192 Repair the OpenTelemetry audit regression
- **WHEN** T-191 is red-proven and upstream compatibility has been evaluated
- **THEN** the seven affected service environments use a coherent patched OpenTelemetry family through a narrowly scoped Semgrep override and uv-generated locks
- **AND** Semgrep startup/rule execution, unchanged vulnerability audits, affected component checks and fresh isolated root CI pass without new advisory exclusions
- **Status:** CLOSED 2026-10-22 — Seven Semgrep-scoped patched dependency families use uv-generated locks; all audits, Semgrep, components and root R4 pass. 1261 source files unchanged; independent owned-process cleanup passes. [Closure evidence](../../changes/complete-agent-economy/evidence/mp08-recovery/completion-audit.md).

#### Scenario: T-183 Write acceptance tests for the October dependency audit regression
- **WHEN** the current isolated Gateway component gate is reproduced
- **THEN** the audit records the exact vulnerable installed versions and advisory identities before any dependency change
- **AND** official upstream evidence and affected dependency paths identify compatible corrected versions across all consumers
- **Status:** CLOSED 2026-10-22 — Exact certifi/Pillow vulnerable-version baseline and upstream compatibility evidence retained. Fresh isolated root37981 exited0 with1180 unchanged source/config hashes and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp05-posting/root-ci-2026-10-22.md).

#### Scenario: T-184 Repair the October dependency audit regression
- **WHEN** T-183 is red-proven and dependency compatibility is verified
- **THEN** existing dependencies and uv-generated locks resolve corrected versions without new audit ignores or removal of required checks
- **AND** affected component checks and a fresh isolated root `just ci-quiet` pass before closure
- **Status:** CLOSED 2026-10-22 — Targeted existing dependency constraints and generated locks pass affected audits and all root gates, without new audit exclusions. Fresh isolated root37981 exited0 with1180 unchanged source/config hashes and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp05-posting/root-ci-2026-10-22.md).

#### Scenario: T-185 Write acceptance tests for remaining non-service dependency audit findings
- **WHEN** the eleven affected environments are audited after the certifi/Pillow repair
- **THEN** exact installed-path audits and lock metadata distinguish real remaining findings from temporary audit-tool overlay packages
- **AND** official upstream compatibility evidence establishes corrected versions for anyio, cryptography, idna, pytest and Starlette before changing their consumers
- **Status:** CLOSED 2026-10-22 — Exact installed-path non-service audit reds and compatible fixed-version evidence retained; overlay-only findings remain excluded. Fresh isolated root37981 exited0 with1180 unchanged source/config hashes and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp05-posting/root-ci-2026-10-22.md).

#### Scenario: T-186 Repair remaining non-service dependency audit findings
- **WHEN** T-185 is red-proven and the affected dependency combinations are verified
- **THEN** targeted uv-generated locks resolve the identified vulnerabilities without new audit exclusions or unrelated upgrades
- **AND** all affected exact-path audits, component gates and fresh isolated root `just ci-quiet` pass before closure
- **Status:** CLOSED 2026-10-22 — Targeted non-service lock corrections pass exact-path audits, affected components and all root gates without new audit exclusions. Fresh isolated root37981 exited0 with1180 unchanged source/config hashes and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp05-posting/root-ci-2026-10-22.md).

### Requirement: Race-free owned command identity
The isolated runtime SHALL preserve command exit and cleanup semantics when a
spawned command exits before the parent can inspect its process-group identity.

#### Scenario: T-187 Write acceptance tests for fast owned-command exit
- **WHEN** a privately spawned command exits before group/session inspection
- **THEN** deterministic tests reproduce the current ProcessLookupError and require the command's actual exit status and cleanup state to remain observable
- **AND** controls preserve wrong-session rejection, unrelated-process safety and retryable cleanup-failure bookkeeping
- **Status:** CLOSED 2026-10-22 — Fast-exit ownership race has deterministic red/green proof and preserved foreign-group/cleanup controls. Fresh isolated root37981 exited0 with1180 unchanged source/config hashes and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp05-posting/root-ci-2026-10-22.md).

#### Scenario: T-188 Preserve owned-command identity across fast exit
- **WHEN** T-187 is red-proven
- **THEN** command startup and identity capture cannot race a fast exit, without granting signaling authority from an arbitrary PID or stale manifest
- **AND** targeted lifecycle/cleanup tests and fresh isolated root `just ci-quiet` pass before closure
- **Status:** CLOSED 2026-10-22 — Owned launch gate preserves exact process identity, exit results and cleanup; independent review and full runtime gate pass. Fresh isolated root37981 exited0 with1180 unchanged source/config hashes and independent cleanup verification. [Closure evidence](../../changes/complete-agent-economy/evidence/mp05-posting/root-ci-2026-10-22.md).

### Requirement: Explicit completion policy decisions
The completion backlog SHALL track unresolved evidence/privacy, post-v1 pricing and quality-aggregation decisions with an accountable project owner, accepted examples and dependent-task gates. Existing local single-operator, manual issuance and fixed-reward v1 decisions SHALL remain effective until explicitly superseded.

#### Scenario: T-162 Decide Q-M1 evidence admissibility and artifact privacy
- **WHEN** the accountable project owner resolves Q-M1
- **THEN** the accepted decision defines supported formats/limits, immutable provenance, participant and bidder access, retention, external-provider disclosure and unsupported-evidence outcomes before MP-09 policy and MP-11 close
- **AND** the accepted record is linked from the affected specifications and implementation proofs without closing those implementations prematurely
- **Status:** OPEN — Owner selected public task instructions/files, configurable OpenAI-compatible Court providers, initial text/source/JSON/CSV evidence, immediate job-page download closure for completed/cancelled/expired jobs and archive after more than three days. See `openspec/changes/complete-agent-economy/evidence/policy-options/owner-decisions-2026-10-22.md`. Complete bounded-evidence specification, worked examples and closure proof remain pending; no other draft option is accepted.

#### Scenario: T-163 Decide Q-M2 post-v1 price-forming economics
- **WHEN** the accountable project owner resolves Q-M2
- **THEN** the accepted decision defines whether post-v1 price formation is enabled, including excess-refund timing and rounding; accepted-bid pricing is required for the finished Core product, with no historical-data conversion
- **AND** the accepted record is linked from the affected specifications and implementation proofs without closing those implementations prematurely
- **Status:** OPEN — Owner choices resolved on 2026-10-23: accepted-bid pricing, unused-budget return at final settlement, Court percentages of the accepted price with worker payment rounded down, and the poster receiving the remainder. There are no existing pricing policies: use this initial rule for all pricing introduced now, and preserve each job's rule on future changes. Accepted decision: `docs/plans/2026-10-23-q-m2-accepted-bid-pricing-decision.md`. Specification reconciliation, independent review and required verification remain open; no pricing implementation is claimed.

#### Scenario: T-164 Decide Q-M3 independent quality aggregation
- **WHEN** the accountable project owner resolves Q-M3
- **THEN** the accepted versioned contract defines separate quality dimensions, payout separation, source weighting, no-history treatment and scored examples before MP-11 and MP-14 close
- **AND** the accepted record is linked from the affected specifications and implementation proofs without closing those implementations prematurely
- **Status:** OPEN — Owner selected equal weighting per task, final Court override of peer feedback for that task, eligibility limited to approved/auto-approved/ruled submissions, and public scores/counts with source detail restricted to the subject, relevant participants and operator. See the owner decision record. Implementation and basic verification remain open; do not ask these choices again.

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
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit 0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

#### Scenario: T-172 Repair the affected development dependency
- **WHEN** T-171 is red-proven and a compatible patched dependency is verified
- **THEN** affected service environments use the patched package through reproducible uv-generated locks, their configured Semgrep rules and audits pass, and no new vulnerability ignore is added
- **AND** any override of an upstream exact pin is explicit, limited to the development tool and documented with compatibility evidence and a removal condition
- **AND** a fresh isolated root `just ci-quiet` exits zero before closure
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit 0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

### Requirement: MP-01 Quarterly navigation race repair
The quarterly report SHALL preserve every accepted Prev/Next click in order while earlier report requests are pending. Implementation T-176 depends on red-proven test ticket T-175. Existing navigation assertions and timeouts SHALL remain unchanged; the universal root closure gate applies to both tickets.

#### Scenario: T-175 Write acceptance tests for concurrent quarterly navigation
- **WHEN** several Prev/Next clicks occur before the first requested report completes
- **THEN** deterministic browser tests demonstrate the lost-navigation gap and assert the final requested quarter across year boundaries, including a no-data response
- **AND** report failures remain visible and a later navigation can recover
- **Status:** CLOSED 2026-10-17 — Two genuine reds/one control, focused green and independent review verified; root19 passed the complete298-case browser suite and full root gate. See the active change's evidence/quarter-navigation-checkpoint.md and evidence/root19-checkpoint.md.

#### Scenario: T-176 Preserve each quarterly navigation click
- **WHEN** T-175 is red-proven
- **THEN** each accepted click shifts from the preceding navigation result in order, without duplicate requests caused by stale quarter state
- **AND** the existing browser suite, new regression proofs, independent review and fresh isolated root CI pass without weakened assertions or increased timeouts
- **Status:** CLOSED 2026-10-17 — Ordered navigation is implemented without changing original assertions/timeouts; focused proof, independent review and root19 full UI/root gate pass. See the active change's evidence/quarter-navigation-checkpoint.md and evidence/root19-checkpoint.md.


### Requirement: MP-01 UI fixture readiness honors its existing budget
The controlled UI browser fixture SHALL tolerate transient connection and request timeouts until its existing readiness deadline, while preserving immediate child-exit failure and terminal timeout diagnostics. T-178 depends on red-proven T-177; neither item closes without the universal root gate.

#### Scenario: T-177 Write acceptance tests for delayed UI readiness
- **WHEN** the fixture's listening socket accepts a health connection before the child can answer HTTP
- **THEN** a deterministic test demonstrates the premature ReadTimeout failure and requires a later successful health response to establish readiness
- **AND** persistent timeouts and child exit remain explicit failures with server-log diagnostics
- **Status:** CLOSED 2026-10-17 — Two genuine readiness reds/two controls, four unit and ten focused browser passes, independent review and root19 full UI/root gate verified. See the active change's evidence/ui-readiness-checkpoint.md and evidence/root19-checkpoint.md.

#### Scenario: T-178 Preserve bounded UI readiness polling
- **WHEN** T-177 is red-proven
- **THEN** transient connection and timeout failures remain within the existing readiness polling budget without increasing timeout values or suppressing terminal failure
- **AND** focused readiness tests, unchanged single-agent browser cases, full UI suite, independent review and a fresh root gate pass
- **Status:** CLOSED 2026-10-17 — Transient timeout handling preserves the existing readiness budget and terminal diagnostics. Focused proof, independent review and root19 full298-case UI/root gate pass. See the active change's evidence/ui-readiness-checkpoint.md and evidence/root19-checkpoint.md.

### Requirement: Standalone integration log isolation
Every service test fixture that starts logging SHALL keep log writes inside its owned temporary resource directory, including when the root runtime supervisor is not used. Config-only fixtures that never start logging are not runtime-write evidence.

#### Scenario: T-179 Prove standalone integration log escape
- **WHEN** the audited Gateway, Task Board, Identity, Central Bank, Reputation and Court startup fixtures construct their service configurations
- **THEN** tests prove whether each log directory is absolute, owned by the temporary test root and distinct from the other service directory before any service starts
- **AND** ordinary runtime logs are not mutated by the proof itself
- **Status:** CLOSED — All seventeen cause-specific configuration reds are archived. The formerly missing six outputs were recovered byte-for-byte from original execution records and independently verified. Root22 supplies the required fresh full gate. [Closure evidence](../../changes/complete-agent-economy/evidence/mp07-transitions/completion-audit.md).

#### Scenario: T-180 Isolate standalone integration log writes
- **WHEN** T-179 is red-proven
- **THEN** all audited startup fixture configurations route logs to explicit service-specific directories inside their temporary root
- **AND** live regression execution leaves ordinary runtime log bytes unchanged, while existing logs are preserved without deletion or rewriting
- **AND** item-specific evidence and a fresh isolated root quality gate pass before closure
- **Status:** CLOSED — Audited startup logs use owned temporary paths. Root22 passes with all202 ordinary logs byte-identical, no additions/removals, and verified owned-process cleanup. Earlier ordinary log contents were preserved. [Closure evidence](../../changes/complete-agent-economy/evidence/mp07-transitions/completion-audit.md).

### Requirement: Durable caller-owned native task intents
The built-in feeder and worker SHALL retain an immutable native mutation intent before transport and replay that intent after interruption. This independent MP-07 producer prerequisite consumes the reviewed native command contract and completed MP-01/MP-03 isolation/key foundations; it does not close the broader MP-05/MP-16/MP-17/MP-18 recovery obligations. Runtime files SHALL be caller-owned and SHALL NOT use the private Gateway or weaken direct-SQL restrictions.

#### Scenario: T-181 Prove native runtime intent loss and durable replay boundaries
- **WHEN** a feeder or worker prepares a bid, acceptance, submission, approval or dispute and transport or the process is interrupted
- **THEN** test evidence demonstrates whether operation identity, source version, exact semantic terms and response-validation context survive before any retry
- **AND** exact stored replay precedes current-task eligibility or decision recomputation, while foreign ownership, malformed responses and unconfirmed target state do not imply completion
- **AND** real-filesystem and process tests cover owner/profile/key/URL binding, private file admission, concurrent identity leases and atomic publication faults without touching ordinary runtime data
- **Status:** CLOSED — Genuine durability/ACK/caller/startup regressions are preserved separately from capability/setup reds. Owner binding, filesystem faults and all ten actual SIGKILL/restart histories pass in Root22; independent final proof audit approves. [Closure evidence](../../changes/complete-agent-economy/evidence/mp07-transitions/completion-audit.md).

#### Scenario: T-182 Persist and replay native runtime task intents
- **WHEN** T-181 is red-proven and the MP-01/MP-03 foundations and reviewed native producer fields are available
- **THEN** one caller-owned identity lease and strict atomic JSON journal retain the original operation ID, version, public arguments and validation context before any mutation transport
- **AND** known pending work resumes with exact replay, changed slot terms fail locally, definite rejection never silently allocates a replacement, and storage failures prevent mutation transport
- **AND** the feeder publishes its explicitly configured acceptance window without conflating it with its selection delay or introducing a platform-wide default
- **AND** item-specific restart/fault evidence, independent review and fresh isolated root CI pass before closure, while posting identity, asset/execution/oracle/rebuttal recovery and authoritative discovery remain mapped to their original full packages
- **Status:** CLOSED — Owner-bound atomic journals, exact public SDK replay, typed ACK validation, leased startup/cleanup and explicit feeder windows are implemented and independently verified. Root22 passes all required gates. Posting, execution/assets/oracle/rebuttal/discovery recovery, distributed leases and external rollback detection remain outside this bounded package. [Closure evidence](../../changes/complete-agent-economy/evidence/mp07-transitions/completion-audit.md).

### Requirement: Secure filelock dependency baseline
The completion backlog SHALL track the filelock audit regression discovered by root CI R7 without suppressing its advisories or widening unrelated dependency changes.

#### Scenario: T-193 Record failing filelock security and compatibility evidence
- **WHEN** the installed dependency audit reports the three filelock advisories
- **THEN** retain the failing audit, affected installed/locked versions, upstream fixed-version evidence and consumer constraints before implementation
- **Status:** CLOSED 2026-10-24 — R7 retained the three advisory failures, affected versions and upstream compatibility evidence; full root R8 exited zero with 1,334 unchanged inputs. Evidence: `openspec/changes/complete-agent-economy/evidence/ci-feature-first-2026-10-23/ci-result.md`. Publication remains a separate scope decision.

#### Scenario: T-194 Update filelock without audit exclusions
- **WHEN** T-193 is red-proven and consumer compatibility is established
- **THEN** resolve a verified secure filelock release with uv-generated locks; use no new advisory exclusions; permit only a documented version-scoped Court override with actual consumer compatibility checks
- **AND** all affected audits and consumer checks pass, followed by fresh isolated root `just ci-quiet` exit zero before closure
- **Status:** CLOSED 2026-10-24 — All seven services resolve filelock 4.2.4; audits, consumer checks and full root R8 pass. The exact-version Court override and removal condition are documented. Evidence: `openspec/changes/complete-agent-economy/evidence/ci-feature-first-2026-10-23/ci-result.md`. No publication is claimed.

### Requirement: Usable and truthful justfile entry points
The repository SHALL expose working developer commands without arbitrary manual run identities, terminal-only help assumptions, false health/startup success, or misleading file-write descriptions. Destructive operations SHALL be verified only against disposable resources. Missing benchmark functionality requires an explicit scope decision rather than a fake passing command.

#### Scenario: T-195 Verify developer-command failures
- **WHEN** developer commands are audited against their documented normal use
- **THEN** retain failures for zero-argument demo, headless help, unhealthy status, failed startup dependency handling and mixed existing/missing key administration; preserve required security rejection cases
- **Status:** CLOSED 2026-10-25 — Failing-first command evidence retained; focused regressions and full root CI passed. Evidence: `openspec/changes/complete-agent-economy/evidence/justfile-targets/final-result.json`.

#### Scenario: T-196 Repair developer-command entry points
- **WHEN** T-195 has failing evidence for a concrete command defect
- **THEN** normal demo allocates its internal ID, explicit resume remains available, help works without a terminal, health/startup failures return nonzero, file-write descriptions match behavior, and existing-key permission adoption can precede provisioning of missing keys without replacing identity
- **AND** safe target checks and fresh full CI pass; performance-target handling follows the owner's recorded choice
- **Status:** CLOSED 2026-10-25 — Automatic demo IDs and browser opening, truthful health/startup failures, headless help, safe process selection, existing-key adoption, argument validation, and statistics repairs passed full root CI (exit 0; 1,349 source files unchanged). Unsupported performance commands were removed under the owner's feature-first/basic-testing scope; performance test files were preserved. All seven ordinary services remained healthy after final verification. Evidence: `openspec/changes/complete-agent-economy/evidence/justfile-targets/final-result.json`.

### Requirement: Inspectable economy activity and task documents
The Observatory SHALL link identifiable task and agent activity to existing detail views. Agent profiles SHALL expose all posted and worked jobs through paginated task links. Task lifecycle views SHALL expose exchanged documents with safe viewing or downloading, including retained files on completed tasks through authenticated read-only operator access. Unsigned terminal access SHALL remain unavailable. Demo submissions SHALL use real canned file bytes.

#### Scenario: T-197 Verify activity navigation and document visibility gaps
- **WHEN** a user follows task activity, an agent, a relevant ticker item, or an uploaded document
- **THEN** basic browser and endpoint tests demonstrate the missing navigation and document access before implementation; coverage preserves task binding and safe rendering of untrusted document content
- **Status:** CLOSED 2026-10-25 — Failing-first evidence recorded zero native navigation anchors, missing posted/worked job sections, and missing task documents and browse links before implementation; focused regressions and full root CI passed. Evidence: `openspec/changes/complete-agent-economy/evidence/ui-visibility-2026-10-25/final-result.json`.

#### Scenario: T-198 Connect Observatory activity and task documents
- **WHEN** T-197 has failing evidence
- **THEN** task and agent activity and relevant ticker items navigate to meaningful existing views; poster and worker profiles expose their jobs; task lifecycle documents remain inspectable after completion through authenticated operator access without relaxing unsigned terminal access
- **AND** focused regressions, browser verification, and root CI pass before closure
- **Status:** CLOSED 2026-10-25 — Activity links, paginated posted/worked jobs, and completed-task document access were connected with authenticated operator reads and safe content handling. Live verification covered poster and worker navigation, retained demo bytes and hashes, operator download 200, and unsigned terminal 410; full root CI passed with 1,362 unchanged inputs. Evidence: `openspec/changes/complete-agent-economy/evidence/ui-visibility-2026-10-25/final-result.json`.

### Requirement: Stable live exchange-board charts
Metric refreshes SHALL update existing chart nodes without replaying entrance animations. Empty or zero-valued history SHALL render finite zero-height bars.

#### Scenario: T-199 Reproduce exchange-board refresh flicker
- **WHEN** real landing-page JavaScript receives repeated metric polls
- **THEN** basic browser tests fail if chart nodes are rebuilt or zero history produces invalid bar heights
- **Status:** CLOSED 2026-10-25 — The browser regression failed before implementation with `unchanged=False` and empty zero-history heights, then passed after the repair together with full root CI. Evidence: `openspec/changes/complete-agent-economy/evidence/ui-visibility-2026-10-25/final-result.json`.

#### Scenario: T-200 Preserve exchange-board charts during refresh
- **WHEN** T-199 has failing evidence
- **THEN** polls update values and bar heights in place, retaining chart nodes and limiting entrance animation to initial rendering
- **AND** focused browser regressions and root CI pass before closure
- **Status:** CLOSED 2026-10-25 — Exchange-board refreshes now retain chart nodes, update values and finite bar heights in place, and limit entrance animation to initial rendering; focused chart regressions and full root CI passed. Evidence: `openspec/changes/complete-agent-economy/evidence/ui-visibility-2026-10-25/final-result.json`.

### Requirement: Mount-independent local shutdown discovery
Local shutdown SHALL identify owned service listeners without probing unrelated network filesystems, while retaining process identity verification and refusing ambiguous ownership.

#### Scenario: T-201 Reproduce shutdown discovery warnings
- **WHEN** macOS has an inaccessible network backup mount
- **THEN** regression evidence demonstrates filesystem-scanning listener discovery and covers native socket records, ambiguous discovery, and foreign listeners
- **Status:** CLOSED 2026-10-25 — Failing-first evidence retained actual `lsof` Time Machine mount warnings plus native-discovery and type-test failures before implementation; focused shutdown regressions and full root CI passed. Evidence: `openspec/changes/complete-agent-economy/evidence/ui-visibility-2026-10-25/final-result.json`.

#### Scenario: T-202 Remove unrelated mount inspection from shutdown
- **WHEN** T-201 has failing evidence
- **THEN** macOS listener discovery uses OS socket/process information without hiding mount warnings, and all existing ownership checks remain enforced before signals
- **AND** focused safety checks and root CI pass before closure
- **Status:** CLOSED 2026-10-25 — macOS shutdown discovery now uses native socket and process records while preserving ownership and ambiguity checks. Two owned real shutdowns completed without mount warnings; full root CI passed with 478 runtime, 319 browser, and 113 live E2E tests. Evidence: `openspec/changes/complete-agent-economy/evidence/ui-visibility-2026-10-25/final-result.json`.

### Requirement: Robust background work under dependency restarts
Task Board and the autonomous agents SHALL keep their periodic work alive across an ordinary restart of a dependency, and health SHALL report a dead loop. Source: `docs/plans/2026-10-30-completion-plan.md` items 0.1, 0.2, 0.5.

#### Scenario: T-203 Prove background loops die on a gateway outage
- **WHEN** the DB Gateway is unreachable for several sweep intervals, or Task Board restarts while the feeder runs
- **THEN** failing-first tests show the deadline sweep task ending, Task Board `/health` still reporting ok, the feeder process exiting, and a worker never resuming an accepted job after restart

#### Scenario: T-204 Deliver supervised loops, loop liveness, and worker resume
- **WHEN** T-203 has failing evidence
- **THEN** the deadline sweep survives transport errors, every Task Board loop task restarts after an unexpected exit, `/health` reports `background_loops` and a non-ok status while any loop is dead, feeder loops back off and continue after `httpx` errors, and a restarted worker resumes accepted, submitted, and disputed jobs exactly once with the ruling payout recorded from `worker_pct`
- **AND** focused regressions and root CI pass before closure

### Requirement: Durable adjudication delivery
Court SHALL persist a decision before any settlement or reputation side effect and resume delivery without re-judging. Source: plan items 0.3 and 3.3 (refines T-128/T-129).

#### Scenario: T-205 Prove ruling delivery is not recoverable
- **WHEN** Reputation fails once after Task Board settled a ruling, judging is interrupted, or two triggers race
- **THEN** failing-first tests against real Gateway `operation_id` semantics show a reverted dispute with money paid, a dispute stuck in `judging`, and duplicate judge invocations, and show an identical rebuttal replay returning 409 and a late rebuttal accepted

#### Scenario: T-206 Deliver claim-before-deliver rulings, leases, and rebuttal replay
- **WHEN** T-205 has failing evidence
- **THEN** the decision is claimed atomically before delivery, acknowledgments replay the stored payload, expired judging leases are reclaimed by the periodic trigger, concurrent triggers yield one ruling, identical rebuttals return 200, changed rebuttals 409, late rebuttals are rejected, and judge failures return a generic 502 body
- **AND** focused regressions and root CI pass before closure

### Requirement: Party-bound asset access and error hygiene
Task Board asset reads SHALL require a signed principal that is party to the task, uploads SHALL be bounded by storage quotas and idempotent on retry, and error bodies SHALL not disclose internals. Source: plan items 0.6, 0.8, 2.6 and security findings M1, M6, L4, L5, L6.

#### Scenario: T-207 Prove unauthenticated asset reads and internal error disclosure
- **WHEN** a non-party client lists or downloads a submitted task's files, an upload is retried after a lost response, or a judge provider fails
- **THEN** failing-first tests show 200 for the stranger, a duplicate asset, uploader-chosen content types served back, and provider text in the 502 body

#### Scenario: T-208 Deliver signed asset reads, quotas, idempotent uploads, and fixed error bodies
- **WHEN** T-207 has failing evidence
- **THEN** list and download require a signed poster, assigned worker, platform, or operator token, downloads are served as `application/octet-stream`, configured per-agent and global byte quotas are enforced, an upload idempotency key returns the stored asset, and Court, Gateway health, and UI bid-list errors use fixed messages while UI `/docs` is disabled
- **AND** focused regressions and root CI pass before closure

### Requirement: LAN operation of the stack
The stack SHALL run on one host with agents on any LAN machine, with configured bind hosts, operator funding for any agent, supervised restarts, partial-start cleanup, and cold backup. Source: plan items 1.1 to 1.8.

#### Scenario: T-209 Prove the stack cannot leave loopback
- **WHEN** `server.host` is set to a LAN address in a service config
- **THEN** failing-first tests show the launch recipes and stop script still use the literal loopback host, no recipe funds an arbitrary agent id, a failed start tier leaves earlier tiers running, and no backup or supervision recipe exists

#### Scenario: T-210 Deliver configured bind hosts, funding, supervision, cleanup, and backup
- **WHEN** T-209 has failing evidence
- **THEN** launch and stop read `server.host` from each service's config, identity, central-bank, task-board, and reputation bind `0.0.0.0` while db-gateway, court, and UI stay loopback, `just fund-agent` credits any registered agent idempotently, `just supervise-install` installs launchd or systemd units that restart a killed service, start-all stops its own processes on a tier failure and logs per service, `just backup`/`just restore` round-trip the data paths, tracked UI logs are removed, and reserved display names are rejected at registration
- **AND** the remote-agent kit document and README describe the shipped procedure, and focused regressions and root CI pass before closure

### Requirement: Usable human task pages
The UI SHALL let the operator post repeatedly, find tasks needing action, see errors, and navigate from every agent name and feed item. Source: plan items 0.7, 4.1, 4.3, 4.6, 4.7 (refines T-138/T-139 and T-146/T-147).

#### Scenario: T-211 Prove the task pages block ordinary use
- **WHEN** the operator posts a second task, looks for submissions awaiting review, or reconnects the event stream
- **THEN** failing-first evidence shows the old posting re-submitted, no link to the new task, no job filters or needs-action view, silent accept/approve failures, plain-text agent names, an unused agent feed route, and duplicate events after reconnect

#### Scenario: T-212 Deliver repeat posting, navigation, visible errors, and resumable events
- **WHEN** T-211 has failing evidence
- **THEN** a second Post Task creates a new task and links to it, every nav has Jobs and Post, the list filters by status and by the operator's role with a needs-action panel, action failures render on the page with plain-language recovery copy, balance and escrow are shown on the create form, every agent name is an anchor, the agent page renders its feed live, the stream honours `Last-Event-ID` without duplicates, and paused feeds buffer events
- **AND** focused regressions and root CI pass before closure

### Requirement: Effective commit gate
The commit hook SHALL run a gate that can complete within its timeout. Source: plan "Verification run on 2026-10-30".

#### Scenario: T-213 Prove the commit hook cannot complete from the repository root
- **WHEN** a commit is made with the session directory at the repository root
- **THEN** evidence shows the hook running the root gate, being terminated at 300 s, and the commit proceeding ungated

#### Scenario: T-214 Deliver a staged-path commit gate
- **WHEN** T-213 has evidence
- **THEN** the hook runs the component `ci-quiet` gates for the components touched by the staged paths, exits 0 quickly for documentation-only commits, and blocks on any failure
- **AND** root CI remains the closure gate for every item
