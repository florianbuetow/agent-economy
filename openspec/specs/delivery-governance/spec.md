## Purpose

This spec is the canonical replacement for the process and verification rules formerly stored in `tickets.md` and `tickets-test-plans.md`. It defines how completion work is selected, proven, and closed. Ticket IDs remain stable and are referenced by `openspec/specs/completion-backlog/spec.md`.

## Requirements

### Requirement: Ticket Closure Gate
Every backlog item SHALL remain open until its item-specific proof plan passes, full repository CI passes, and evidence is recorded.

#### Scenario: Close a ticket
- **WHEN** a ticket is marked complete
- **THEN** its own proof steps from the completion backlog have been executed and produced the expected result
- **AND** `just ci-quiet` has exited 0 from the repository root with structure checks, all seven service CIs, agents, integration tests, and e2e tests passing
- **AND** the actual command output proving the item-specific proof and full CI is recorded in the closing commit, PR, or session notes

#### Scenario: Proof fails
- **WHEN** any proof step or `just ci-quiet` fails
- **THEN** the ticket remains open or in progress
- **AND** the next attempt fixes forward and re-runs the complete item proof plus the universal gate
- **AND** no assertion, proof step, or test plan is weakened merely to pass

### Requirement: Failing-Test-First Work
Bug and feature work SHALL prove the gap before implementing the fix.

#### Scenario: Bug or feature implementation starts
- **WHEN** a bug or feature ticket is selected
- **THEN** a new test or acceptance proof is added first
- **AND** the test is run and observed failing against the current implementation
- **AND** only then is the implementation changed

#### Scenario: Tests are added
- **WHEN** tests are added for ticket work
- **THEN** new pytest tests carry `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.performance`
- **AND** existing acceptance tests are not edited unless an explicit recorded decision changes the contract

### Requirement: Execution Queue
Work SHALL proceed in the dependency-aware queue order unless a recorded decision explicitly changes the order.

#### Scenario: Phase B decision sprint
- **WHEN** a fresh queue run starts
- **THEN** T-092 is handled first
- **AND** the decisions for T-020, T-021, T-022, T-023, T-024, and T-025 are recorded without closing the implementation tickets early

#### Scenario: Phase C quick wins
- **WHEN** Phase B is complete
- **THEN** quick wins are attempted in this order: T-048, T-046, T-047, T-037, T-016, T-085, T-045, T-063, T-075

#### Scenario: Phase D mechanical service fixes
- **WHEN** quick wins are complete
- **THEN** mechanical service fixes are attempted in this order: T-010, T-030, T-031, T-032, T-033, T-035, T-039, T-042, T-043, T-014, T-036

#### Scenario: Phase E auth and config architecture
- **WHEN** mechanical fixes are complete
- **THEN** architecture fixes are attempted in this order: T-021, T-034, T-083, T-081, T-084

#### Scenario: Phase F Court lifecycle chain
- **WHEN** auth/config work is complete
- **THEN** Court lifecycle work is attempted in this order: T-011, T-013, T-023, T-040, T-017, T-018, T-015

#### Scenario: Phase G spec and docs sweep
- **WHEN** behavior matches reality
- **THEN** spec and documentation work is attempted in this order: T-024, T-050, T-051, T-022, T-044, T-025, T-049, T-041, T-020, T-060, T-061, T-062, T-064, T-065, T-038

#### Scenario: Later phases
- **WHEN** code and specs agree
- **THEN** test debt is attempted in this order: T-070, T-072, T-071, T-073, T-074
- **AND** refactoring T-082 is attempted after Phase F
- **AND** product/vision work is attempted last in this order: T-090, T-093, T-095, T-094, T-091

### Requirement: Ratified Decisions
The implementation SHALL follow the decisions ratified on 2026-06-12 without re-asking.

#### Scenario: Error-code casing
- **WHEN** specs, tests, acceptance suites, or code refer to machine-readable errors
- **THEN** snake_case is canonical
- **AND** SCREAMING_SNAKE assertions are removed or migrated

#### Scenario: Platform auth model
- **WHEN** platform-signed operations are verified in central-bank or reputation
- **THEN** local PlatformAgent verification is canonical
- **AND** remote Identity verification paths such as stale `verify_jws_path` or `get_agent_path` config are removed where no longer required

#### Scenario: DB Gateway reads
- **WHEN** services or specs describe DB Gateway access
- **THEN** the read API is blessed and documented
- **AND** implemented GET routes are not removed merely because early specs claimed reads bypassed the gateway

#### Scenario: Escrow split ownership
- **WHEN** a Court ruling reaches Task Board
- **THEN** Task Board owns escrow settlement on ruling
- **AND** Court specs do not claim Court directly calls Central Bank for the split

#### Scenario: Bid model
- **WHEN** bids are documented or validated
- **THEN** integer `amount` bidding is canonical
- **AND** proposal-only bid models are stale

#### Scenario: UI service naming and port
- **WHEN** docs, specs, compose files, or tests name the frontend/observability service
- **THEN** `services/ui` on port 8008 is canonical
- **AND** phantom `observatory` service or port 8006 references are stale unless explicitly marked historical

#### Scenario: Frontend stack
- **WHEN** frontend features are implemented from existing plans
- **THEN** vanilla JavaScript is canonical per `docs/plans/2026-06-13-frontend-stack-decision.md`
- **AND** React-targeting plans or mockups remain superseded unless a new decision reopens them

#### Scenario: Salary distribution
- **WHEN** v1 docs describe agent salaries
- **THEN** salaries are future/out-of-scope
- **AND** manual platform credit plus the fund-feeder CLI remain the v1 funding mechanism

### Requirement: Environment and Tooling Preconditions
Ticket proofs SHALL account for the runtime environment required by the service or test.

#### Scenario: Live stack proof
- **WHEN** a proof runs against live services
- **THEN** `just stop-all`, `just start-all`, and `just status` are run first
- **AND** all expected services are healthy before interpreting test failures

#### Scenario: Court proof
- **WHEN** a live Court proof or e2e test is run for T-011, T-013, T-015, or T-018
- **THEN** the Court judge provider is deterministic, typically `provider: "mock"` in `services/court/config.yaml`
- **OR** LM Studio is available on `localhost:1234` with `LMSTUDIO_API_KEY` set

#### Scenario: Arena proof
- **WHEN** T-091 is attempted
- **THEN** real LLM API keys satisfy `agents/tests/integration/test_llm_api_keys.py`

#### Scenario: Signed payload proof
- **WHEN** curl or shell acceptance proofs need signed payloads
- **THEN** payloads are generated through `agents/src/base_agent/`, `PlatformAgent`, `AgentFactory`, roster/config in `agents/`, or per-service acceptance helpers

#### Scenario: Service ports
- **WHEN** docs, tests, or proofs refer to local service ports
- **THEN** the canonical ports are identity 8001, central-bank 8002, task-board 8003, reputation 8004, court 8005, db-gateway 8007, and ui 8008
- **AND** port 8006 is treated as unused unless a new service is explicitly introduced

### Requirement: Tracker Migration
OpenSpec SHALL be the canonical tracker after `tickets.md` and `tickets-test-plans.md` are removed.

#### Scenario: New work is discovered
- **WHEN** work is discovered after this migration
- **THEN** it is added to an OpenSpec change or spec with a stable identifier and proof obligation
- **AND** markdown TODO trackers are not reintroduced

#### Scenario: Existing T-IDs are referenced
- **WHEN** existing backlog items are discussed in commits, PRs, or sessions
- **THEN** their T-IDs remain stable and refer to `openspec/specs/completion-backlog/spec.md`
