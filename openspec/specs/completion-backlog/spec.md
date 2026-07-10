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
