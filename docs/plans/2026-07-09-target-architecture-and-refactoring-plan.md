# Agent Task Economy — Target Architecture & Refactoring Plan

**Date:** 2026-07-09
**Status:** DRAFT — complete except for §9 Open Questions, which require product decisions from Florian before the affected work packages (marked ⚠Q-blocked) are implementable.
**Method:** 17 parallel audit agents (per-service code audits, doc/spec extraction, repo-wide wiring maps) + orchestrator validation of every load-bearing claim against source. Evidence cited as `path:line` (code) or `path § heading` (docs). No claim without a citation. UNVERIFIED items are labeled.
**Supersedes:** `docs/plans/2026-06-12-completion-inventory.md` (folds in its findings, re-verified against the 2026-07-09 codebase).

---

## 0. How to read this document

- **§2 is normative.** It defines the target state the system must reach. Every statement there is either (a) ratified by an existing decision record / canonical spec (cited), or (b) flagged as an open question in §9. Nothing in §2 is invented.
- **§3 is descriptive.** Current state as-built, evidence-cited.
- **§4 is the diff.** Numbered gap register GAP-### linking current → target.
- **§5 is the work.** Phased work packages WP-### with exact files, contracts, test obligations, and verification commands. Each WP references the GAPs it closes and the stable T-IDs from `openspec/specs/completion-backlog/spec.md` where they exist.
- **§9 collects every question the documentation could not answer.** Work packages depending on an answer are marked ⚠Q-blocked(Q-#).

Precedence order used when documents conflict (rationale in §1):

1. Decision records in `docs/plans/2026-06-13-*.md` and ratified decisions in `openspec/specs/delivery-governance/spec.md § Ratified Decisions`
2. `openspec/specs/agent-task-economy/spec.md` (canonical product contract)
3. `openspec/specs/completion-backlog/spec.md` (canonical backlog, stable T-IDs)
4. `docs/specifications/service-api/*` + `service-tests/*` (per-service contracts — stale in places; staleness itself is tracked as gaps)
5. `docs/main/agent-task-economy.md` (vision — authoritative for intent, not for mechanics)
6. `README.md`, `AGENTS.md`/`CLAUDE.md` (currently stale; must be regenerated from this document, never treated as truth)

---

## 1. Sources of truth & governance state (verified firsthand)

### 1.1 The canonical tracker is ambiguous — this must be resolved first

- `openspec/specs/delivery-governance/spec.md § Tracker Migration` declares: "OpenSpec SHALL be the canonical tracker after `tickets.md` and `tickets-test-plans.md` are removed" and "markdown TODO trackers are not reintroduced."
- Yet a repo-root `tickets.md` exists again (created ~2026-06-29, commit `d8e757e`) and **reuses ID `T-001`** for a UI bug, colliding with `openspec/specs/completion-backlog/spec.md § Scenario: T-001 make CI green`. Two different issues now share the ID T-001.
- Project memory/instructions (AGENTS.md) still mandate **beads (`bd`)**, removed in commit `c447396`.

**Consequence for this plan:** T-IDs cited here always mean the openspec backlog IDs. The root `tickets.md` T-001 is referred to as `tickets.md#T-001`. Resolution is WP-01 / Q-1.

### 1.2 Ratified decisions (binding for the target state)

From `openspec/specs/delivery-governance/spec.md § Ratified Decisions` (ratified 2026-06-12) and the two decision records:

| # | Decision | Source |
|---|---|---|
| R1 | Error codes are **snake_case** everywhere; SCREAMING_SNAKE assertions are stale | delivery-governance § Error-code casing (T-020) |
| R2 | Platform-signed operations are verified **locally via PlatformAgent** in central-bank and reputation; remote Identity verification config (`verify_jws_path`, `get_agent_path`) is removed | delivery-governance § Platform auth model (T-021) |
| R3 | The DB Gateway **read API is blessed**; "reads bypass the gateway" claims are stale; all GET routes get documented | delivery-governance § DB Gateway reads (T-022) |
| R4 | **Task Board owns escrow settlement on ruling**; Court does not call Central Bank for splits | delivery-governance § Escrow split ownership (T-023) |
| R5 | Bids carry an **integer `amount`**; proposal-only bid models are stale | delivery-governance § Bid model (T-024) |
| R6 | The frontend/observability service is **`services/ui` on port 8008**; "observatory" service and port 8006 are historical | delivery-governance § UI service naming and port (T-025) |
| R7 | Frontend stays **vanilla JS**; React/TS/Vite plans are SUPERSEDED | `docs/plans/2026-06-13-frontend-stack-decision.md` (T-026) |
| R8 | **Salary distribution is out of scope for v1**; manual platform credit + fund-feeder CLI is the v1 funding mechanism | delivery-governance § Salary distribution (T-090) |
| R9 | **All Court write operations are platform-signed only**; agents never call Court directly; Task Board mediates filings and worker rebuttals; Court reads are public | `docs/plans/2026-06-13-court-trust-model-decision.md` (T-012) |
| R10 | Canonical ports: identity 8001, central-bank 8002, task-board 8003, reputation 8004, court 8005, db-gateway **8007**, ui **8008**; 8006 unused | delivery-governance § Service ports |
| R11 | Failing-test-first for all bug/feature work; `just ci-quiet` exit 0 from repo root is the universal closure gate | delivery-governance § Ticket Closure Gate, § Failing-Test-First Work |

### 1.3 Canonical product contract

`openspec/specs/agent-task-economy/spec.md` (the "baseline spec") is the single richest statement of the target product behavior: canonical lowercase status vocabulary, platform-notary authority, escrow-at-posting, sealed binding bids, on-platform assets, Task-Board-mediated rebuttals via `POST /tasks/{task_id}/rebuttal`, semantic lifecycle events, gateway-owned persistence, UserAgent-backed UI proxy, factory-based agent runtime, and uniform service contracts. §2 below is structured around it.

### 1.4 Known-stale top-level docs (verified 2026-07-09 firsthand)

| Doc | Verified stale content | Tracked as |
|---|---|---|
| `README.md` | "Database Gateway (port 8006)"; "Observatory (port 8007)" as an existing service; `services/observatory/` in the repo tree; `just ci-all`/`ci-all-quiet` recipes that don't exist; "Python 3.11+" | T-060 → WP-02 |
| `AGENTS.md` (symlinked as `CLAUDE.md`) | Five-service architecture block (no db-gateway/ui/agents/service-clients); dependency map ends at port 8005; references `docs/demo-scenarios/` (doesn't exist); mandates beads (`bd`) for all tracking; duplicated "Landing the Plane" section | T-061 → WP-02 |
| `CHANGELOG.md` | 2026-04-13 entry: "five-service … monorepo" | T-063 → WP-02 |

---

## 2. TARGET STATE (normative)

Everything below is normative. Statements are backed by a ratified decision (R#), the canonical baseline spec, or code-verified as-built behavior being promoted to contract; genuinely undecided points reference a Q-# in §9 and bind once answered.

### 2.1 System topology

Eight deployable components: seven FastAPI services + the agent runtime, plus shared libraries.

| Component | Port | Responsibility (target) |
|---|---|---|
| identity | 8001 | Agent registration, public-key roster, JWS verification support |
| central-bank | 8002 | Accounts, ledger + audit log, escrow lock/release/split, platform-credit funding |
| task-board | 8003 | Task lifecycle source of truth, bidding, contracts, asset store, dispute mediation toward Court, escrow settlement on ruling (R4) |
| reputation | 8004 | Spec-quality & delivery-quality feedback, sealed mutual feedback, court-derived feedback |
| court | 8005 | Dispute records, LLM judge panel, rulings; writes platform-signed only (R9) |
| db-gateway | 8007 | Owns the shared SQLite database; serializes all writes; blessed read API (R3); semantic event log |
| ui | 8008 | Observatory pages (vanilla JS, R7) + operator write path via UserAgent proxy |
| agents/ (runtime) | n/a | Feeder, workers (mathbot et al.), factories, roster; the simulation layer |

Outbound-call matrix (target = as-built minus the Q/T deltas noted; ✗ = must not call):

| Caller ↓ / Callee → | identity | central-bank | task-board | reputation | court | db-gateway | LLM provider |
|---|---|---|---|---|---|---|---|
| identity | — | ✗ | ✗ | ✗ | ✗ | writes+reads `/identity/*` | ✗ |
| central-bank | verify-jws (agent ops only, after T-021) | — | ✗ | ✗ | ✗ | `/bank/*` | ✗ |
| task-board | verify-jws (agent ops) | escrow lock/release/split | — | ✗ | file claim + forward rebuttal (platform-signed) | `/board/*` | ✗ |
| reputation | verify-jws (agent ops, after T-021) | ✗ | ✗ | — | ✗ | `/reputation/*` | ✗ |
| court | ✗ (local platform verify) | ✗ (R4 — never; specs to be fixed) | get task context + record_ruling | feedback ×2 | — | `/court/*` + read `/board/tasks/{id}` | judges (litellm) |
| ui | via UserAgent | via UserAgent (treasury) | via UserAgent (proxy) | via UserAgent | via UserAgent | Q-4 (today: direct SQLite read) | ✗ |
| agents/ | register/lookup | accounts/escrow-lock | full lifecycle | feedback | ✗ (R9 — never directly; rebuttal via task-board) | ✗ | workers (AsyncOpenAI) |
| tools/demo_replay | ✓ | ✓ | ✓ | ✓ | ✓ (platform key) | ✗ | ✗ |

Startup order (4 health-gated tiers, as-fixed in commit `624f124`): db-gateway → identity (health + `GET /agents` live) → central-bank ∥ task-board ∥ reputation ∥ court → ui.

### 2.2 Data & persistence architecture

- Single shared SQLite database, **owned exclusively by db-gateway**; domain services never open the DB file directly (`openspec/specs/agent-task-economy/spec.md § Gateway-Owned Persistence`).
- Writes: only through gateway POST/PUT/DELETE routes; every write emits an event row in the same transaction.
- Reads: through the gateway's blessed read API (R3), which is service-agnostic — e.g. Court legitimately reads task context via gateway `GET /board/tasks/{id}` rather than the Task Board API; R3 blesses the read routes without restricting callers. The single exception today is the UI's direct read-only SQLite connection; its target treatment is **Q-4**.
- The gateway stays **unauthenticated by design** ("trusts internal callers" — db-gateway spec § What This Service Does NOT Do). v1 posture: never bind/expose 8007 beyond localhost/the compose network; recorded as a deployment constraint, not a gap.
- Semantic events: task lifecycle transitions emit `task.accepted`, `task.submitted`, `task.approved`, `task.auto_approved`, `task.disputed`, `task.ruled`, `task.cancelled`, `task.expired`; court/reputation flows emit `claim.filed`, `rebuttal.submitted`, `ruling.delivered`, `feedback.revealed` (agent-task-economy § Gateway-Owned Persistence and Semantic Events).

### 2.3 Identity & authentication architecture

- Ed25519 keypairs per agent; every mutating agent action is JWS-signed (compact serialization, `alg: EdDSA`, `kid` = signer's agent id) and verified before applying (agent-task-economy § Signed Identity and Platform Authority).
- **Two-tier verification model.** This resolves the long-standing "local vs Identity-HTTP" spec conflict (June C2): R2 is scoped to *platform operations* (delivery-governance § Platform auth model: "WHEN central-bank or reputation validates **platform operations** THEN local PlatformAgent verification is used").
  - **Agent-signed operations** — task create/cancel, bid, accept, asset upload, submit, approve, dispute, rebuttal-to-Task-Board, `escrow_lock`, balance/transaction reads, feedback submission, self-service zero-balance account creation — are verified via Identity `POST /agents/verify-jws` using the shared `service_clients.IdentityClient`. That endpoint becomes **spec'd** (T-050); the spec'd-but-never-called raw `POST /agents/verify` is retired.
  - **Platform-signed operations** — account creation with non-zero balance, credit, escrow release/split, Task Board `record_ruling`, all Court writes, court-generated feedback — are verified **locally** via `PlatformAgent.validate_certificate()` (the Court pattern; no Identity round trip, survives Identity outages). Central Bank, Reputation, and Task Board migrate their platform-op checks to this path (T-021).
- Court trust model: all Court writes platform-signed only; Task Board is the mediating authorization layer; Court reads public (R9).
- Registration stays open/self-service and unauthenticated (as-built; identity spec scopes authorization to callers). Key revocation/rotation remains out of scope for v1 unless Q-10 decides otherwise (accepted risk, arc42 §11.1.3).
- **UI operator**: as-built, the UI's `UserAgent` *is* the platform identity (`AgentFactory.user_agent()` loads handle `"platform"`, sharing `data/keys/platform.key`), and the `/api/proxy/*` routes carry **no authentication**. Target identity for the operator is **Q-2**; proxy exposure/auth is **Q-3**.

### 2.4 Economic model

- Escrow for the full task reward locks at **posting** via the two-token create: the poster signs both a `create_task` token and a matching `escrow_lock` token; the task never becomes `open` without locked escrow (agent-task-economy § Funds and Escrow Lifecycle). The vision doc's "escrow locks at acceptance" narrative is superseded.
- Settlement — all executed by **Task Board** through Central Bank (R4):
  - approve, or review-timeout auto-approve → `POST /escrow/{id}/release` to the worker (**full reward**);
  - cancel / expiry → release to the poster (refund path; no separate refund primitive exists or is needed);
  - ruling → split: worker `floor(amount × worker_pct / 100)`, poster the remainder; `worker_pct` 0 → release-to-poster, 100 → release-to-worker (`task_manager.py:1301-1315`).
- **Bid `amount` is a competitive signal only in the current contract**: payout is always the full posted reward regardless of the winning bid. Whether the winning bid should become the actual payment (with the difference refunded at acceptance) is **Q-8**.
- Funding: platform-signed `credit`/account-creation-with-balance are the only money sources. **Minting is unbounded by design** (platform = sovereign issuer, no supply cap) — documented posture, not a defect. Feeder funding via the `fund-feeder` CLI; no fees in v1; no salary scheduler in v1 (R8) — the `salary.paid` event label stays reserved for the future mechanism.
- Treasury: the `platform` account seeded with 1,000,000 coins. Bootstrap ownership (today: **UI lifespan** mints it) is **Q-9**.
- Idempotency: credit replay guarded by the partial unique index `(account_id, reference) WHERE type='credit'`; one active escrow per `(payer_account_id, task_id)`.

### 2.5 Task lifecycle state machine

Canonical lowercase statuses (agent-task-economy § Canonical Task Lifecycle): `open`, `accepted`, `submitted`, `approved`, `cancelled`, `disputed`, `ruled`, `expired`.

Target transition table (as-coded today in `task_board_service/services/task_manager.py` + `deadline_evaluator.py`, with the two target deltas marked ▲):

| From → To | Trigger | Mechanism |
|---|---|---|
| ∅ → open | `POST /tasks` (two-token; escrow locked first) | API |
| open → cancelled | poster `cancel` | API (escrow → poster) |
| open → accepted | poster `accept_bid` | API |
| open → expired | bidding deadline passed — ▲ **regardless of bid count** (T-035; today only when `bid_count == 0`, `deadline_evaluator.py:63`) | lazy evaluation |
| accepted → submitted | worker `submit` (requires ≥1 uploaded asset) | API |
| accepted → expired | execution deadline passed (escrow → poster) | lazy evaluation |
| submitted → approved | poster `approve` (escrow → worker) | API |
| submitted → approved | review deadline passed → auto-approve (escrow → worker, event `task.auto_approved`) | lazy evaluation |
| submitted → disputed | poster `dispute` → Task Board synchronously files the platform-signed Court claim, then persists `disputed` | API |
| disputed → ruled | platform `record_ruling` (Task Board settles escrow per `worker_pct`) | API |

- Invalid transitions are rejected with `invalid_status`/409 — never coerced.
- Deadline mechanics: **lazy evaluation** on every read and at the top of each mutating op; there is **no background scheduler**. ▲ Whether a config-driven periodic evaluator is added (so statuses/escrow can't stall when nothing polls, and direct-DB readers like the UI never see stale states) is **Q-5**.
- Escrow side-effects on lazy transitions stay best-effort with `escrow_pending=1` + retry-on-next-read (`escrow_coordinator.py:78-129`).

### 2.6 Dispute & court flow

Target flow (agent-task-economy § Court Dispute Resolution + R9 + R4):

1. Poster disputes a `submitted` task on Task Board (poster-signed).
2. Task Board validates, files the Court claim **platform-signed** with `task_id`, `claimant_id`, `respondent_id`, `claim`, `escrow_id`; task → `disputed`; Court `dispute_id` returned when available.
3. Worker rebuts via Task Board `POST /tasks/{task_id}/rebuttal` (worker-signed to Task Board; platform-signed onward to Court). Ruling may proceed without a rebuttal (null rebuttal context).
4. Ruling: judge panel evaluates → **median** worker payout percentage; Court records ruling on Task Board (platform-signed); Task Board settles escrow via Central Bank; Court records spec/delivery feedback on Reputation; dispute persisted with judge votes.
5. Partial-failure contract: a dispute is never reported fully ruled unless all side effects committed; it remains recoverable for retry (agent-task-economy § Scenario: Ruling failure does not leave partial completion).

- Judge panel: configurable size (must be **odd**), aggregation = **median** `worker_pct`; each judge returns `worker_pct` 0–100 plus written reasoning; votes persisted as a `judge_votes` JSON array. Dev/test default: `provider: mock`, `panel_size: 1`; LM Studio config exists only as comments in `services/court/config.yaml:34-39`. Production judge composition/providers is **Q-13**.
- Claim and rebuttal bodies ≤ 10,000 chars; rebuttal window 24h (config-driven).
- The ambiguity-favors-worker rule is encoded **only** in the LLM judge system prompt (`court_service/services/prompts.py:6`) and is therefore inert under the mock judge (fixed 50%). An operational "vague spec" rubric exists nowhere; both the rubric and production panel composition are **Q-13**.
- Ruling execution contract (openspec § Scenario: Ruling failure): sequence is judges → Task Board `record_ruling` (escrow settles) → Reputation feedback ×2 → persist ruling+votes; on failure the dispute must remain **recoverable for retry** — the current revert-to-`rebuttal_pending` leaves the Task Board ruling already committed, so retry is NOT clean (GAP; T-040).
- **Ruling trigger**: today nothing triggers `POST /disputes/{id}/rule` autonomously (only the scripted demo does); the rebuttal deadline is stored but never enforced by anyone. Who triggers rulings after the rebuttal window is part of **Q-5**.

### 2.7 Reputation & feedback

- Two per-agent scores: specification quality (poster) and delivery quality (worker); vision defaults: both start at 100% and drop only via dispute rulings (`docs/main/agent-task-economy.md § Reputation`).
- Three-tier feedback (Dissatisfied / Satisfied / Extremely Satisfied) + optional text ≤256 chars, bidirectional, **sealed until both submitted** (vision § Reputation; agent-task-economy § Reputation and Feedback).
- Court-generated feedback derives from the worker payout percentage (agent-task-economy § Scenario: Court-generated feedback); it is platform-signed and stored immediately visible (`force_visible`): spec_quality → poster, delivery_quality → worker.
- Reveal semantics: stored sealed (`visible=0`); revealed when the counterparty submits — **atomically** (the production gateway path's TOCTOU race is a gap, GAP-R2) — or lazily once `reveal_timeout_seconds` (86,400s) elapses.
- The Reputation service stores **raw feedback only** ("Feedback is data, not scores" — reputation spec § Core Principles); no numeric aggregation exists anywhere, while the vision promises two scores defaulting to 100% that drop via rulings. Whether score aggregation becomes a Reputation endpoint (so agents can query their own standing, per vision Scenario 2) or stays consumer-side is **Q-12**.

### 2.8 UI / Observatory

- `services/ui` @8008, FastAPI backend + vanilla JS assets (R6, R7).
- Read side: observatory dashboards (ticker, metrics, leaderboard, quarterly report, task views).
- Write side: **operator actions via `/proxy/*` backed by a registered UserAgent** (agent-task-economy § Scenario: UI proxy uses UserAgent) — the "dedicated user for the UI operator". Every proxy route must be specified and integration-tested (T-049).
- Economy phase for a zero-activity economy is **`stalled`** (completion-backlog § Scenario: T-046) — this decides `tickets.md#T-001` in favor of changing the code, not the test. ✅ shipped `9f506c9`. The `stable`/`contracting` boundary for combinations the spec table leaves uncovered is an open spec gap (see the §5.0 correction note), resolved in WP-12 — not by guesswork.
- Pages (target): landing, observatory dashboard, task console (exist today) **plus** a quarterly-report page (T-093) and agent-profile view (T-095) — the backend endpoints already exist; the HTML/JS views do not. The observatory spec's React `/live` route is superseded (R7).
- Read data source: today every UI read is direct read-only SQLite (`aiosqlite`, `mode=ro`); target treatment — keep-and-bless vs migrate to the gateway read API — is **Q-4**.
- Write path: `/api/proxy/*` (post task, accept bid, approve, dispute, get identity) through the UserAgent; every proxy route must be specified and integration-tested (T-049). Operator identity → Q-2; proxy auth → Q-3.

### 2.9 Agent runtime & simulation

- `AgentFactory` from roster handles; `WorkerFactory` for named math-worker profiles validated against `roster.yaml`; feeder funding via the platform-mediated fund-feeder CLI (agent-task-economy § Agent Runtime and UI Proxy).
- Agent loops use canonical lowercase statuses; workers handle disputes via the Task-Board-mediated rebuttal and record **ruled** outcomes with the actual payout — a review-poll timeout must be recorded as `TaskOutcome.TIMEOUT`, never as a full approved win (T-016).
- The feeder runs both loops: feed (post from `data/math_tasks.jsonl`, reward = base + level×increment) and review (approve on answer match, dispute on mismatch). The autonomous dispute chain completes only when a ruling trigger exists (Q-5).
- Worker LLM access (AsyncOpenAI → LM Studio/OpenAI/Mistral profiles) is an external channel outside the platform boundary — accepted by design; keys via the agents config profiles (Q-7 governs the env-var seam). The declared `strands-agents` dependency is unused and is dropped; the LLM-driven `@tool` agent design stays future work.
- Demos are honest: every claimed behavior goes through real APIs (openspec § Honest Demonstrations) — the direct-SQL `tools/seed-economy.sh` is retired.
- Vision Scenario 2 "Text Classification Arena" (Regex Ron / Sklearn Sam / LLM Luna) remains target backlog (T-091).

### 2.10 Shared libraries & uniform service contracts

- `libs/service-commons`: config loading, logging, exceptions/`ServiceError`, `middleware_error_response`, health contract. No consumer may monkeypatch its classes (the court's process-wide `ServiceError.__init__` patch is removed).
- `libs/service-clients`: the only sanctioned HTTP client layer — every exported client has callers or is removed (T-082); clients are **async** (the five hand-rolled synchronous `*_db_client.py` classes that block the event loop are replaced by/aligned with the lib's async clients).
- **PKI moves into a library.** Ed25519 keygen/JWS signing/verification and `PlatformAgent` leave the `agents/` application package for a lib (e.g. `libs/service-auth`), with **one** canonicalization (today: `base_agent/signing.py` [no sort_keys, `typ` header] vs task-board `PlatformSigner` [joserfc, sort_keys, no `typ`] vs a third copy in `tools/demo_replay/wallet.py`). Services then depend on libs only — the current build-time inversion (5 services depending on `agents/`) disappears, as do the blanket `base_agent` mypy overrides.
- Error envelope `{"error","message","details"}` (3 fields) everywhere, snake_case codes (R1); the 2-field variants in the identity/observatory specs are stale.
- Uniform per-service contract: `create_app()` factory, YAML-backed fail-fast config (no defaults; Optional-but-required-at-runtime fields are outlawed — T-034), `GET /health`, pytest markers unit/integration/performance/architecture, byte-identical service justfiles.

### 2.11 Deployment & operations

- Local mode is the primary deployment: `just start-all` (4 health-gated tiers), `just status`, `just stop-all`; e2e via `just test-e2e` (clean-data restart). All services bind 127.0.0.1; the unauthenticated gateway (8007) must never be reachable beyond the trusted host boundary.
- Docker: scope is **Q-6**. If kept, the target requires: per-service docker configs with compose service-name URLs (not 127.0.0.1), a shared volume for `economy.db` (or Q-4's gateway-read migration removing the need), Dockerfiles that copy all editable path deps (`libs/*`, `agents/` until the PKI lib lands), a consistent build-context convention, correct `depends_on` (T-081), and not publishing 8007 to the host.
- CI: `just ci-quiet` = structure check + 7 service CIs + agents CI + cross-service integration + agents e2e; repo-root green is the only done-gate (R11). Target additions: per-service integration suites and `tools/` tests join a CI phase, and the 111-test UI Playwright e2e suite gets a root recipe and a CI phase (today it is wired to nothing). Hosted CI is **Q-14**.
- Guard collections cover db-gateway and ui sources (today omitted) and stop referencing the deleted `DELEGATE.md`.

---

## 3. CURRENT STATE inventory (as of 2026-07-09, branch `refactor`)

Facts marked ✅V were verified directly by the orchestrator (file read); others carry the auditing agent's citation and were plausibility-checked against neighboring evidence.

### 3.1 Topology as-built

Seven services + agent runtime + tools + two shared libs. Actual ports (each from its `config.yaml:10`, mirrored in `docker-compose.yml` and the root justfile port array `(8001 8002 8003 8004 8005 8007 8008)` at `justfile:503,562`):

| Component | Port | Notes |
|---|---|---|
| identity | 8001 | leaf |
| central-bank | 8002 | |
| task-board | 8003 | |
| reputation | 8004 | |
| court | 8005 | judge provider `mock`, panel_size 1; LM Studio config exists only as comments (`services/court/config.yaml:31-39`) |
| db-gateway | 8007 | ✅V — README's "port 8006" is wrong |
| ui | 8008 | ✅V — README's "Observatory (port 8007)" service does not exist anywhere in the repo |
| agents/ | n/a | client runtime; service URLs from `agents/config.yaml:5-9` |

### 3.2 Persistence & data access as-built

- **One live database**: `services/db-gateway/data/economy.db` (WAL, busy_timeout 5000ms), created/owned by db-gateway (`db_gateway_service/core/lifespan.py:33-46`), schema loaded from `docs/specifications/schema.sql` (12 tables + `events`).
- **Domain services write AND read via gateway HTTP**, each through a hand-rolled client: identity `services/agent_db_client.py:22-26`, central-bank `services/ledger_db_client.py:21-24`, task-board `services/task_db_client.py:56-59`, reputation `services/feedback_db_client.py:19-22`, court `services/dispute_db_client.py:18-21`. The shared `libs/service-clients/src/service_clients/gateway.py` `GatewayClient` has **zero importers** — dead code.
- **Court performs a cross-domain read** of task-board data through the gateway (`dispute_db_client.py:71` → `GET /board/tasks/{task_id}`), i.e. it bypasses the Task Board service API for reads.
- **UI bypasses the gateway entirely**: opens the same SQLite file directly, read-only (`ui_service/core/lifespan.py:66-71`, aiosqlite `mode=ro`), path `../db-gateway/data/economy.db` from `services/ui/config.yaml:17-18` ✅V.
- **Dead/legacy storage remnants**: reputation's direct-SQLite `sqlite_feedback_store.py` is not wired into app state (lifespan always builds `FeedbackDbClient`; the store survives only via a legacy shim imported by two unit tests); `database.path` config keys exist but are never read in identity/central-bank/reputation/court (`config.yaml:18` each, 0 src references) — central-bank's ✅V; task-board reads its `database.path` only in a vestigial legacy-`limits:` branch that current config never triggers (`task_board_service/core/lifespan.py:47`); stale generated key `services/task-board/data/platform.pem` orphaned by the current config path.
- **Asset store**: filesystem `data/assets/{task_id}/{asset_id}/` under the task-board service dir (`asset_manager.py:39,44,137-139`), config-driven.

### 3.3 Identity & auth as-built

- Ed25519/JWS everywhere; Identity is the registration/trust root; keys on disk at repo `data/keys/{platform,mathbot,feeder}.{key,pub}` (gitignored), generated by `agents/src/base_agent/signing.py:24-58`.
- **Signature-verification architecture has diverged** (ratified target R2 = local PlatformAgent for platform ops):
  - court: local `PlatformAgent.validate_certificate()` — matches R2 (migration commit `ddeba66`).
  - central-bank: still configured for Identity-HTTP verification — `identity.base_url/get_agent_path/verify_jws_path` live in `services/central-bank/config.yaml:20-24` ✅V; per the codex-task archaeology, its routers still call Identity over HTTP.
  - reputation: reverted to Identity-HTTP verification (`reputation-identity-verification-fix.md`; `reputation_service/core/lifespan.py:62` wires `identity.base_url`).
  - task-board: dual-path `TokenValidator` — prefers `IdentityClient.verify_jws()` over HTTP when configured (it is), local `platform_agent.validate_certificate` only as fallback (`token_validator.py:125-206`). Additionally, the production validator branches on a `_tampered` marker injected by a test helper (`token_validator.py:158,203`) — test scaffolding inside production signature checking.
- central-bank `platform.agent_id: "a-platform-placeholder"` ✅V — a literal placeholder in live config; at runtime `get_platform_agent_id()` prefers the live registered `PlatformAgent.agent_id` and falls back to the config value (`central_bank_service/routers/helpers.py:71-83`), so the placeholder is dead weight that only misleads (removed in WP-03.1).
- **db-gateway has no authentication by design** — "trusts internal callers" (`db-gateway-implementation/README:5`); anyone with network access to 8007 can write arbitrary domain rows.
- **UI UserAgent = platform identity**: `services/ui/config.yaml:31-36` ✅V — `user_agent.agent_config_path: ../../agents/config.yaml` with in-file comment "The user agent shares the platform identity, which is authorized to mint its own balance"; `treasury_balance: 1000000` minted at UI startup (`ui_service/core/lifespan.py:84-91`). There is **no dedicated operator identity**; browser-driven actions are signed as the platform/treasury itself.
- **Env-var usage in production code (3 sites, violating the project's no-env-var rule)**: `CONFIG_PATH` (`libs/service-commons/src/service_commons/config.py:109`, used by docker-compose for all 7 services); court judge `api_key_env` (`court_service/core/lifespan.py:49`); agents `${VAR}` resolution for LLM keys (`agents/src/base_agent/worker_config.py:12-40`). Plus `.env` files for court/agents sourced by `justfile:276`.

### 3.4 UI service as-built

Vanilla-JS multi-page site served by FastAPI: `index.html`, `observatory.html`, `task.html` from git-tracked `services/ui/data/web/` (`.gitignore:99` exception); the spec's agent-profile and quarterly pages have API endpoints but no HTML views. SSE: 1s poll / 15s keepalive / batch 50 (`services/ui/config.yaml:20-23` ✅V). Write path: `/api/proxy/*` (task post, bid accept, approve, dispute, identity) via the platform-keyed UserAgent — **no authentication on these routes**: any HTTP caller reaching 8008 can act as the platform and spend the treasury (`routers/proxy.py`). Known bugs/facts: empty-economy phase emits `idle` vs required `stalled` (`metrics.py:653`, T-046/`tickets.md#T-001`); `stable` phase ignores the spec's dispute<15% ceiling (`metrics.py:658`); `request.max_body_size` config is validated but never enforced; the injectable `_clock` seam covers Python time only — SQL `julianday('now')`/`datetime('now')` in `tasks.py:347,353`/`agents.py:379-499` stay unfrozen in e2e; `list_agents` is N+1 (~10 queries per agent, Python-side sort/paginate, `agents.py:27-215`).

### 3.5 Agent runtime & tools as-built

`agents/roster.yaml` defines 7 handles (`platform`, `alice`, `bob` [vestigial], `mathbot`, `mathbot_openai`, `mathbot_mistral`, `feeder`). Feeder runs feed + review loops concurrently: posts math tasks from `data/math_tasks.jsonl` (reward = 10 + 10×level; deadlines 120/300/120s), then approves on exact-normalized answer match / disputes on mismatch (`task_feeder/review.py:16-63`). Math workers scan every 10s, bid 50–10,000, solve via `AsyncOpenAI` (LM Studio default) and, when disputed, rebut via Task Board then wait for `ruled` — **which never comes autonomously: no agent, service, or scheduler calls `POST /disputes/{id}/rule` outside the scripted demo** (`engine.py` is the sole `trigger_ruling` caller). A worker review-poll timeout is still recorded as a full APPROVED win (`math_worker/loop.py:162-171`, T-016). `just start-mathbot` takes the legacy config path (hardcoded `api_key: "lm-studio"`), silently bypassing the `workers:` profiles. Dead surface: `strands-agents` hard dep with zero imports; `PuppetMaster`/`PuppetAgent`; `TaskOutcome.BID_REJECTED`; `auto_approve_on_error`; bank-mixin `lock_escrow` and platform `release/split_escrow` (never called agent-side). `tools/demo_replay` re-implements signing and all clients (hardcoded URLs `clients.py:18-21,296,418`; zero tests); `tools/seed-economy.sh` writes the DB directly and is wired to nothing; `tools/` has no CI recipe at all.

### 3.6 Deployment as-built

- **Local**: `just start-all` boots in 4 health-gated tiers: db-gateway → identity (health + `GET /agents` to kill the registration race, commit `624f124`) → bank/board/reputation/court in parallel → ui (which then mints the treasury).
- **Docker mode is comprehensively broken** (✅V compose read in full; no `volumes:` key exists anywhere in either compose file):
  1. Four services (identity, central-bank, task-board, court) load their plain `config.yaml` in-container (`docker-compose.yml:9,23,40,76`), whose `db_gateway.url` is `http://127.0.0.1:8007` — each container's **own** loopback under bridge networking → every gateway write connect-fails.
  2. reputation alone uses `docker-config.yaml` (`docker-compose.yml:59` ✅V), which omits the `db_gateway`, `identity`, and `database` sections and still has the retired `logging.format` shape ✅V → fails Pydantic `extra="forbid"`/missing-field or, at best, starts with `db_gateway=None` and dies on first call (`reputation_service/config.py:115` Optional-with-default contradicts its own "no defaults" docstring at `:100-104`).
  3. Image builds: central-bank, task-board, court, reputation, ui Dockerfiles copy only `libs/service-commons` while their pyprojects declare editable path deps on `../../libs/service-clients` and/or `../../agents` → `uv sync --frozen` cannot resolve them (e.g. `services/central-bank/Dockerfile:8` vs `pyproject.toml:57-59`).
  4. db-gateway's Dockerfile diverges from every sibling: `WORKDIR /app`, `COPY pyproject.toml ./` (no repo-root pyproject exists in the root build context), `COPY ../../libs/service-commons` (escapes the build context) ✅V (`services/db-gateway/Dockerfile:3,9,10`).
  5. No shared volume for `economy.db` between db-gateway and ui (`docs/arc42/07 § 7.2.1.6` + compose ✅V) — the UI's direct-read path cannot work in Docker.
  6. The unauthenticated gateway binds `0.0.0.0` in-container and compose **publishes 8007 to the host** (`docker-compose.yml:94-95`) — anyone reaching the host can write arbitrary ledger/task/ruling rows.
- docker-compose `depends_on` gaps (court missing central-bank; db-gateway has none) — T-081; healthcheck URLs duplicate each service's port literal (`docker-compose.yml:11,25,42,61,78,99,113`). No CI exercises Docker at all, so none of this is caught.

### 3.7 Documentation corpus state

- Canonical layer: `openspec/` (3 specs; `openspec/changes/` is empty).
- `docs/codex-tasks/*` (and other generated docs + the delegation guide) were **removed from git tracking** in commit `a791984` (2026-06-21) — they exist on disk only; post-removal edits leave no history. `DELEGATE.md` is gone from the repo root while `AGENTS.md § Delegating Work` still links to it ✅V.
- Two frontend documentation eras: 9 React-era docs carry SUPERSEDED banners (per R7), but two siblings that edit the same React files carry none (`2026-03-01-leaderboard-improvements-design.md`, `2026-03-01-monthly-earnings-and-satisfaction-colors-design.md`).
- `docs/arc42/` is a generated as-built reference (12 sections): internally consistent with the code-level audits above, carrying 27 registered human-input/no-evidence gaps, 8 undated reconstructed ADRs, and one low-confidence lifecycle diagram (`06-runtime-view.md § 6.8`).
- README/AGENTS.md/CHANGELOG staleness itemized in §1.4.
- `docs/diagrams/system-sequence-diagrams.md` is confirmed stale: pre-gateway-migration flows, db-gateway@8006/observatory@8007 port claims, and "Notary" naming for the platform agent.

### 3.8 Component digests (audit highlights not covered above)

- **identity** (≈1.4k LOC): open self-service registration; `POST /agents/verify` (spec'd) has zero callers while `POST /agents/verify-jws` (unspec'd) serves CB/TB/reputation; envelope has 3 fields vs spec's 2; `crypto.algorithm` config is dead (EdDSA hardcoded, `agent_registry.py:172,209`); 49 acceptance shell scripts assert UPPERCASE codes and are wired into no CI; integration/performance test dirs are empty stubs; dead `pyjwt` dep and legacy store shims.
- **central-bank** (≈2.2k LOC): endpoints per spec + self-service zero-balance accounts (code-only, ratified direction); auth precedence inverted on credit/release/split (authz before payload checks, `accounts.py:143`, `escrow.py:102,164` — T-033); in-memory vs gateway store divergence (tx types `debit`/prefixed refs, missing poster==payer guard, differing worker_pct error code — T-030/T-032); zero-amount split credits not skipped (T-031); `/health` 500s if the gateway is down (`health.py:19-21`); unit tests bypass real JWS verification via a payload-decoding mock; the 24-script acceptance suite generates a config that cannot even boot the current service.
- **task-board** (≈5k LOC src): biggest service; `task_manager.py` is a 1,362-line god class (~270-line `create_task`); all Pydantic response models except Health are dead (hand-built dicts; `BidResponse.proposal` contradicts the real `amount` payload); `/tasks/{id}/rebuttal` exists in code only and skips the request-validation middleware (`core/middleware.py:14-23`); Court-unavailable during dispute/rebuttal surfaces as raw 500 (no 502 mapping, no try/except around `platform_agent.file_claim`, `task_manager.py:1072,1175`) while the Central-Bank 502-mapping fix is fully in place; undocumented action aliases `file_dispute`/`submit_ruling`; no contract object exists anywhere.
- **reputation** (≈1.8k LOC): production reveal is a TOCTOU read-then-write via the gateway (`feedback_db_client.py:52-83` vs atomic test-only `sqlite_feedback_store.py:106-152`); court/platform `force_visible` bypass is untested and forces `from_agent_id`=platform; `FeedbackDbClient` (the production store) has zero functional tests; VIS-09 timeout reveal untested (no clock seam); `PlatformIdentityClient` subclass never calls `super().__init__` (latent AttributeError); acceptance shell suite posts unauthenticated bodies (all 51 would fail).
- **court** (≈2.4k LOC): platform-signed-only writes fully adhere to R9; never calls Central Bank (matches R4; court spec/tests-spec stale, `CENTRAL_BANK_UNAVAILABLE` dead); ruling side-effect order TaskBoard→Reputation→persist with court-only revert (T-040); `require_platform_signer` dead (no `kid` assertion); rebuttal deadline stored, never enforced; `__init__.py:10-32` monkeypatches `ServiceError.__init__` process-wide; judges: MockJudge fixed 50%, litellm path never exercised by any test; hardcoded 80/40 feedback cutoffs and 256-char comment cap; deliverables reach judges only as whatever `get_task` returns — no asset-content fetch (UNCLEAR whether judges ever see real deliverable bytes; must be verified in WP-06).
- **db-gateway** (≈3.6k LOC): 1,414-line `DbWriter` for 5 domains; single shared connection, reader reaches into writer's private `_db` (`lifespan.py:49`); schema loaded from `docs/specifications/schema.sql` with errors silently suppressed (`db_writer.py:66-69`); `DELETE /court/rulings/{id}` runs with no transaction and no event; `POST /court/claims/{id}/status` event-optional; idempotent replays return `event_id: 0`/`balance_after: 0`; `constraints` compare-and-set (state enforcement) contradicts the spec's "no business logic"; FK/UNIQUE mapped by substring-matching driver messages; unused `pyjwt`.
- **ui**: §3.4.
- **libs**: service-commons solid but `middleware_error_response(details=None)` violates the lib's own no-defaults stance; JSON log timestamps have minute precision only; service-clients has **no tests** and 5 of 7 clients have zero importers; no nonce/`iat`/`exp`/replay protection anywhere in the JWS layer.
- **agents/tools**: §3.5; additionally the demo `clients.py` feedback contract (signs `role`, has `reveal_feedback`) has drifted from the base SDK (signs `from_agent_id`, no reveal method) — one of the two is wrong against the real Reputation API (verify in WP-09).
- **tests/CI**: root `tests/integration` = in-process gateway write-contract tests (no real cross-service HTTP); the real live-stack suite is `agents/tests/e2e` (58 tests; covers happy path, dispute→ruling chain, auto-approve timeout, platform-auth boundaries, economic invariants) — excluded from `agents` own CI (`-m "not e2e"`) and reached only via root `just test-e2e`; per-service `tests/integration` suites are reached by **no** root CI phase; the 111-test UI Playwright suite is wired to nothing; per-service `test_cross_service_deps.py` still forbid-lists a phantom `observatory_service`; `.claude/settings.json` PreToolUse CI hook references a nonexistent script with a wrong jq path (never fired successfully; F-75).

---

## 4. GAP ANALYSIS (register)

Severity: **P0** = the economy loop or money correctness is broken · **P1** = target-blocking architectural divergence · **P2** = robustness/quality defect · **P3** = hygiene. "Closes via" names the §5 work package (and openspec T-ID where one exists). ⚠Q-blocked items cannot start until the §9 answer lands.

### A. Lifecycle & economic correctness

| ID | Gap (current, evidence) | Target (§2 ref) | Sev | Closes via |
|---|---|---|---|---|
| GAP-A1 | **No component ever triggers rulings**: `POST /disputes/{id}/rule` is called only by the demo engine; a feeder-disputed task with a worker rebuttal waits forever | §2.6 ruling trigger | **P0** | WP-06 ⚠Q-5 |
| ~~GAP-A2~~ | Open tasks with ≥1 bid never expired (`deadline_evaluator.py:63` guarded `bid_count == 0`) → escrow locked forever | §2.5 table ▲ | P1 | **DONE** `a429120` (mutation-checked; two spec-contradicting tests corrected, see the exception record above) |
| GAP-A3 | Deadlines evaluated only lazily on reads; nothing transitions unread tasks; direct-DB readers (UI) see stale states indefinitely | §2.5 ▲ | P1 | WP-05 ⚠Q-5 |
| GAP-A4 | Ruling side-effects non-atomic: Task Board ruling+escrow commit, then failure reverts **court state only** (`ruling_orchestrator.py:285-295`); retry would hit `invalid_status` on re-record | §2.6 recoverable-retry contract | P1 | WP-06 (T-040) |
| ~~GAP-A5~~ | Production sealed-feedback reveal was a TOCTOU read-then-write; the reveal policy now lives inside the gateway's `BEGIN IMMEDIATE` (reverse lookup + both rows flipped + `feedback.revealed` emitted). `force_visible` stays a caller policy flag; "a reverse pair exists" is a fact the gateway derives. | §2.7 atomic reveal | P1 | **DONE** `478153e` (two-connection concurrency probe) |
| GAP-A6 | Court-generated feedback semantics muddy: `force_visible` requires `from_agent_id`=platform to pass signer-match (`routers/feedback.py:175-196`); zero tests exercise the path | §2.7 court feedback | P2 | WP-07 |
| GAP-A7 | Worker records review-poll timeout as full APPROVED earnings (`math_worker/loop.py:162-171`) | §2.9 TIMEOUT outcome | P2 | WP-09 (T-016) |
| GAP-A8 | Rebuttal deadline computed and stored but enforced by nobody (court spec §"does NOT enforce deadlines"; Task Board doesn't either) | §2.6 window | P2 | WP-06 ⚠Q-5 |
| GAP-A9 | UNCLEAR whether judges ever see deliverable **content**: court passes `task_data["deliverables"]` from `get_task` through to prompts (`ruling_orchestrator.py:27-32,118`) and never fetches assets | §2.6 + vision "judges can access everything" | P2 | WP-06 (verify first) |
| GAP-A10 | Bank store divergences: zero-amount split credits written (both stores); in-memory store uses `type:"debit"`/prefixed refs, lacks poster==payer guard, wrong worker_pct error code vs gateway path | §2.4 settlement | P2 | WP-04 (T-030/031/032) |
| GAP-A11 | Economy phase emits `idle` for empty economy vs required `stalled` (`metrics.py:653`); `stable` branch drops the dispute<15% ceiling (`metrics.py:658`) | §2.8 phases | P2 | WP-08 (T-046) — decides `tickets.md#T-001` |
| GAP-A12 | UNVERIFIED residuals from the June inventory: labor bucket `51_to_100` excluding 100; frontend trend string `growing` vs API `increasing`; `title_too_long` custom code | §2.8/§2.5 | P2 | WP-08/WP-05 (T-047/048/037) — re-verify then fix |
| GAP-A13 | Demo integrity: `scale.yaml` leaves a dispute unresolved; demo `reveal_feedback` posts to an endpoint absent from the Reputation API surface (`clients.py:414`); demo/base-SDK feedback contracts drifted | §2.9 honest demos | P2 | WP-09 (verify first) |
| GAP-A14 | Bank auth precedence inverted on credit/release/split (403 before payload validation, `accounts.py:143`, `escrow.py:102,164`) | auth-spec precedence | P2 | WP-03 (T-033) |
| GAP-A15 | **No autonomous component ever accepts a bid** (found 2026-07-10 while auditing T-035's blast radius). The only callers of `accept_bid` are `tools/src/demo_replay/engine.py:236` (the scripted demo) and the UI proxy (`ui_service/routers/proxy.py:42`, i.e. a human clicking). `agents/src/task_feeder/` posts and reviews but has no acceptance path, and `MathWorkerLoop` simply waits in its post-bid phase until poll exhaustion records `BID_TIMEOUT`. So `just start-feeder` + `just start-mathbot` cannot move a task past `open`. Together with GAP-A1 (nothing triggers rulings) this means **the autonomous economy has two missing drivers**, and §8's definition-of-done item 2 is unreachable until both are closed. Before T-035 the stranded task also held its escrow forever; it now expires and refunds the poster, which is an improvement but not a substitute for acceptance. | §2.9: feeder accepts a winning bid | **P0** (blocks the autonomous-economy claim) | new WP ⚠Q-16 |

### B. Auth & security

| ID | Gap | Target | Sev | Closes via |
|---|---|---|---|---|
| GAP-B1 | CB (`routers/helpers.py:47`), TB (`token_validator.py:134`), Reputation (`routers/feedback.py:126`) verify **platform ops** via Identity HTTP; only Court is local | §2.3 two-tier model | P1 | WP-03 (T-021) |
| GAP-B2 | UI proxy = unauthenticated platform-privileged write console; UserAgent shares the platform key and mints/spends the treasury | §2.3/§2.8 | P1 | WP-08 ⚠Q-2,Q-3 |
| GAP-B3 | `_tampered` test-helper marker branch inside production signature validation (`token_validator.py:158,203`) | clean prod code | P2 | WP-03 |
| GAP-B4 | Court asserts platform identity by crypto only; spec'd `kid == platform.agent_id` check absent; `require_platform_signer` is dead code | spec/code align | P3 | WP-06 + WP-12 |
| GAP-B5 | No replay/freshness protection in any JWS (no nonce/`iat`/`exp`); blunted by idempotency+state checks but unstated | explicit posture | P2 | ⚠Q-10 → WP-03 or docs |
| GAP-B6 | Compose publishes the unauthenticated gateway to the host (`0.0.0.0` + `8007:8007`) | §2.11 never expose 8007 | P2 | WP-10 ⚠Q-6 |
| GAP-B7 | `crypto.algorithm` config dead in identity (EdDSA hardcoded, `agent_registry.py:172,209`) | honest config | P3 | WP-11 |
| GAP-B8 | No key revocation/rotation (arc42 risk 11.1.3) | accepted-risk record or scope | P3 | ⚠Q-10 |
| GAP-B9 | **Cross-dispute rebuttal injection** (found by Codex adversarial review 2026-07-10, orchestrator-verified): `submit_rebuttal` validates only that `payload["dispute_id"]` is a non-empty string (`task_manager.py:1154-1156`) before forwarding it platform-signed to Court — a worker with any disputed task can attach a rebuttal to **another** task's dispute (dispute ids are publicly listable), corrupting judge context | Task Board persists the Court `dispute_id` at dispute time and rejects mismatched rebuttals | **P0** | Hotfix H-2 (§5.0) |

### C. Data & persistence

| ID | Gap | Target | Sev | Closes via |
|---|---|---|---|---|
| GAP-C1 | UI opens the gateway's SQLite file directly (read-only); semgrep carries a silent `ui_service` exception to the no-direct-sql rule | §2.2 read rule | P1 | WP-08 ⚠Q-4 |
| GAP-C2 | `DELETE /court/rulings/{id}`: no transaction, no event (`db_writer.py:1407-1414`); `POST /court/claims/{id}/status`: event optional (`:1237-1240`) — both violate "every write includes an event" | §2.2 events | P1 | WP-04 (T-042) |
| GAP-C3 | Idempotent replays return `event_id: 0` / `balance_after: 0` sentinels (`db_writer.py:237,412,536-537`) | real values | P2 | WP-04 (T-043) |
| GAP-C4 | Schema init failures silently suppressed (`contextlib.suppress(sqlite3.OperationalError)`, `db_writer.py:66-69`) | fail-fast | P2 | WP-04 |
| GAP-C5 | Reader consumes writer's private `_db` (`lifespan.py:49`); write serialization rests implicitly on the single-threaded event loop. **Measured 2026-07-10:** two threads sharing one `DbWriter` raise `sqlite3.OperationalError: cannot start a transaction within a transaction` — the gateway is *not* thread-safe. It is correct today only because every route is `async def` and each write method runs to completion without awaiting. Converting any gateway route to `def` (FastAPI would then run it in a threadpool), or introducing an `await` between `BEGIN IMMEDIATE` and `COMMIT`, silently corrupts transactions. | explicit ownership + a connection-per-thread or documented invariant with a test | P2 | WP-04 |
| GAP-C6 | Gateway `constraints` compare-and-set is real state enforcement while the spec claims "no business logic / no reads" | bless + document | P2 | WP-12 (T-022/044) |
| GAP-C7 | FK/UNIQUE mapped by driver-message substring matching (13 sites); `claim_exists` covers the `task_id` UNIQUE too; health size ignores WAL; constraint errors leak table/column internals (SEC-02) | robust mapping | P3 | WP-04 |
| GAP-C8 | Dead storage remnants: reputation `sqlite_feedback_store` + shims; `database.path` keys ×4 services + TB vestigial branch; stale `services/task-board/data/platform.pem`; duplicated in-memory stores src↔tests/fakes | none of it | P3 | WP-11 |
| GAP-C9 | `tools/seed-economy.sh` writes the DB directly, bypassing everything; orphaned from all recipes | retired (§2.9) | P3 | WP-11 |

### D. Deployment & operations

| ID | Gap | Target | Sev | Closes via |
|---|---|---|---|---|
| GAP-D1 | Docker broken six ways (§3.6): loopback gateway URLs, invalid reputation docker-config, uninstallable editable deps in 5 images, divergent db-gateway Dockerfile, no shared DB volume, 8007 published | §2.11 | P1 | WP-10 ⚠Q-6 (T-081 subsumed) |
| GAP-D2 | Env-var seams in production: `CONFIG_PATH` (commons `config.py:109`), court `api_key_env` (`lifespan.py:49`), agents `${VAR}` (`worker_config.py:12-40`) vs the project's no-env-var rule | sanctioned list or replacement | P2 | WP-10 ⚠Q-7 |
| GAP-D3 | No hosted CI (`.github/` absent); quality gates are manual-only | per Q-14 | P2 | WP-10 ⚠Q-14 |
| GAP-D4 | Port literals repeated ~15× in the justfile; compose healthchecks duplicate port literals | single source | P3 | WP-11 |
| ~~GAP-D5~~ | ~~PreToolUse CI hook broken (audit F-75)~~ — **RETRACTED 2026-07-10**: verified `scripts/ci-quiet-hook.sh` exists and `.claude/settings.json` wires `bash scripts/ci-quiet-hook.sh`; it blocked a commit during H-2 while the tree was red. F-75 (sourced from the March retrospectives) is stale. | n/a | — | closed, no work |
| GAP-D6 | Guard collections omit db-gateway/ui sources and protect the deleted `DELEGATE.md` | complete guards | P3 | WP-11 |
| GAP-D7 | Bank `/health` 500s when the gateway is down (`health.py:19-21`) — health must degrade, not die | health contract | P3 | WP-11 |

### E. Client layer & code structure

| ID | Gap | Target | Sev | Closes via |
|---|---|---|---|---|
| GAP-E1 | PKI lives in the `agents/` app package; **5 services build-depend on it**; 3 signing implementations with 2 canonicalizations (`signing.py` vs `platform_signer.py` vs `wallet.py`) | §2.10 PKI lib | P1 | WP-02 |
| GAP-E2 | 4 of 5 gateway db-clients are synchronous `httpx.Client` inside async routers — event-loop stalls under concurrency | async clients | P1 | WP-04 |
| GAP-E3 | 5 of 7 lib clients have zero importers; task-board hand-rolls a 439-line `CentralBankClient` duplicating lib `BankClient`; libs have **zero tests** | §2.10 sanctioned layer | P2 | WP-04 (T-082) |
| GAP-E4 | God units: `task_manager.py` 1,362 LOC (270-line `create_task`), `db_writer.py` 1,414 LOC, `RulingOrchestrator` multi-actor, `metrics.py` 965, `factory.py` 1,005 | bounded decomposition | P2 | WP-11 |
| GAP-E5 | Court monkeypatches `ServiceError.__init__` process-wide (`court_service/__init__.py:10-32`); commons' own `middleware_error_response(details=None)` violates its no-defaults stance | §2.10 | P2 | WP-06 + WP-11 |
| GAP-E6 | Task-board Pydantic response models all dead except Health — responses hand-built/unvalidated; `BidResponse.proposal` contradicts the real `amount` field | enforced schemas | P2 | WP-05 |
| GAP-E7 | Court-unavailable during dispute/rebuttal → raw 500 `internal_error` (no try/except at `task_manager.py:1072,1175`); `/rebuttal` skips the request-validation middleware (`core/middleware.py:14-23`) | 502 mapping + full middleware | P2 | WP-05 |
| GAP-E8 | Dead dependencies/surface: `strands-agents` (no imports), `pyjwt` ×3 services + gateway, `require_platform_signer`, `execute_query_one`, `ProxyTaskResponse`, `TaskOutcome.BID_REJECTED`, `auto_approve_on_error`, agent-side `lock_escrow`/`release_escrow`/`split_escrow`, `PuppetMaster`/`PuppetAgent`, roster `type` field | removed | P3 | WP-09/WP-11 |
| GAP-E9 | Convention drift: identity hand-rolls exception handlers instead of the commons factory; router-layer validation helpers; UI `events.py` bypasses the `DbConn` dependency; duplicated auth preamble across all routers (F-19) | shared `authorize_and_load` + commons handlers | P3 | WP-11 |
| GAP-E10 | UI `list_agents` N+1 (~10 queries/agent, Python-side sort+paginate); GDP history loops 2 queries per bucket | SQL-side aggregation | P2 | WP-08 |
| GAP-E11 | `tools/demo_replay`: hardcoded URLs, no config file, zero tests; `math_task_factory` reaches into private attrs | config-driven + tested | P3 | WP-09 |

### F. Tests & CI

| ID | Gap | Target | Sev | Closes via |
|---|---|---|---|---|
| GAP-F1 | Per-service `integration/`+`performance/` suites are empty stubs (identity, CB, TB, court; gateway has 1 test) and no root CI phase runs any per-service integration suite | real suites, CI-wired | P1 | WP-13 (T-071) |
| GAP-F2 | Acceptance shell suites (identity 49, bank 24, reputation 51 scripts) assert UPPERCASE codes, can't boot current configs, and are wired into nothing | pass against live services with snake_case | P1 | WP-13 (T-070) |
| GAP-F3 | The 111-test UI Playwright e2e suite is reachable from no `just` recipe and gates nothing (why `tickets.md#T-001` stayed open) | root recipe + CI phase | P1 | WP-13 |
| GAP-F4 | Production paths with zero tests: `LedgerDbClient`, `FeedbackDbClient`, litellm judge path, `force_visible`, VIS-09 timeout reveal, fund-feeder CLI entry, all of `demo_replay`; `tools/` has no CI recipe at all | covered | P2 | WP-13 (T-072/075) |
| GAP-F5 | Frozen-acceptance-test rule violated historically: TB bid tests adapted to `amount`; court tests encode the escrow-ownership deviation (RULE-06/16) — resolution is spec-side reconciliation, not test edits | specs updated to ratified reality | P2 | WP-12 (T-023/024 doc side) |
| GAP-F6 | Missing e2e: cancellation escrow refund; Docker smoke; true multi-connection gateway concurrency; startup-order regression | added | P2 | WP-13 (T-073/045) |
| GAP-F7 | Every service's `test_cross_service_deps.py` forbid-lists a phantom `observatory_service` and repeats stale port comments | phantom removed | P3 | WP-11 |

### G. Documentation & governance

| ID | Gap | Target | Sev | Closes via |
|---|---|---|---|---|
| GAP-G1 | Tracker triad: openspec declares itself canonical; root `tickets.md` was reintroduced and **reuses T-001**; AGENTS.md still mandates beads | one tracker | P1 | WP-01 ⚠Q-1 |
| GAP-G2 | README/AGENTS.md/CHANGELOG stale (five-service architecture, wrong ports, phantom observatory, nonexistent `ci-all` recipes, beads sections, deleted `DELEGATE.md` reference, `docs/demo-scenarios/` reference, duplicated sections, Python 3.11+ claim) | §1.4 regeneration | P1 | WP-12 (T-060/061/063/064) |
| GAP-G3 | Service API/auth/test specs materially wrong vs ratified reality: UPPERCASE codes throughout; gateway "no reads"+port 8006; court escrow-split side-effect + `CENTRAL_BANK_UNAVAILABLE`; TB "does not call Court"/"proposal" bids/no pagination; identity "leaf, calls nothing" + unspec'd verify-jws; observatory spec (port 8006, React, read-only) vs ui reality; reputation auth-spec's certificate model; two-field error envelopes; `rebuttal` vs `rebuttal_pending` status literals; port 8006 double-claimed | §2 as the source for a full spec sweep | P1 | WP-12 (T-020/022/023/024/025/038/044/050/051/062) |
| GAP-G4 | **The entire `docs/` tree and `openspec/` are gitignored** (`.gitignore:154,125`, since `a791984`): `git ls-files docs/` = 0 files ✅V. Unversioned: all 22 service specs, **`docs/specifications/schema.sql` (loaded by db-gateway at startup — a runtime dependency)**, both 2026-06-13 decision records, the canonical openspec specs, and this plan. A clean clone loses the DB schema and every contract; combined with GAP-C4 (suppressed schema-load errors) the gateway would start against an empty schema. Scope confirmed by the 2026-07-10 Codex adversarial review | authored contracts (`docs/specifications/`, `docs/plans/`, `openspec/`) tracked; only bulk/generated docs stay ignored | **P1** | Hotfix H-1 (§5.0) + WP-12.4 policy record |
| GAP-G5 | Load-bearing architecture decisions undocumented: escrow-ownership move to Task Board, events Option B (gateway-written), DB-over-HTTP tradeoff, two-tier auth; zero ADR files exist (8 reconstructed, undated) | ADRs written | P2 | WP-12 |
| GAP-G6 | Small-doc staleness: sequence diagrams (pre-gateway, Notary, wrong ports), `scripts/demo/README.md`, two React-era docs missing SUPERSEDED banners, `start.sh` "7 services" vs README "8" | swept | P3 | WP-12 (T-065) |

---

## 5. REFACTORING PLAN (work packages)

Rules of engagement (binding, from `openspec/specs/delivery-governance/spec.md`): every bug/feature item starts with a **failing test** observed failing; an item closes only with its own proof **plus** `just ci-quiet` exit 0 from the repo root; no assertion is weakened to pass. Tests are acceptance tests — behavior changes are reconciled **spec-side** (WP-12), never by editing frozen tests, except where a ratified decision changed the contract.

Sizing: S ≈ ≤½ day · M ≈ 1–2 days · L ≈ 3+ days (single implementer with agent support).

Dependency spine: WP-01 → WP-02 → WP-03 → {WP-04 ∥ WP-05} → {WP-06 ∥ WP-07} → WP-08/WP-09 → WP-10 → WP-11 → WP-12 → WP-13 (final gate) → WP-14. Items marked ⚠Q-# cannot start before that answer; everything else can proceed immediately in order.

### §5.0 Hotfixes (pulled forward, execution started 2026-07-10)

Unblocked items promoted out of their WPs after the Codex adversarial review confirmed two live hazards; each follows failing-test-first and lands with its own verification:

| # | Item | Origin | Scope | Status |
|---|---|---|---|---|
| H-1 | Re-track authored contracts: `.gitignore` `docs/` → `docs/*` + `!docs/specifications/` + `!docs/plans/`; unignore `openspec/`; commit specs/plans/openspec incl. `schema.sql` and this plan | GAP-G4 | `.gitignore`, git index only | **DONE** `02fa5db` (72 files) |
| H-2 | Rebuttal↔dispute binding: `dispute_task` persists Court `dispute_id` on the task (new `board_tasks.dispute_id` column: `schema.sql`, gateway `TASK_UPDATE_COLUMNS` + **idempotent `ALTER TABLE` migration** for existing `economy.db` files, TB status update); `submit_rebuttal` rejects `payload.dispute_id != task.dispute_id` (400) and an unbound dispute (409), forwarding the server-stored id; `dispute_task` fails 502 rather than marking a task disputed without a binding; court-side `kid`/party assertion deferred to WP-06 | GAP-B9 | task-board, db-gateway, schema.sql | **DONE** `e13711f` (mutation-checked) |
| H-3 | Economy phase `stalled` for a zero-activity economy | GAP-A11 (T-046) | services/ui `metrics.py` | **DONE** `9f506c9` |
| H-4 | Worker review-timeout recorded as `TaskOutcome.TIMEOUT`, zero earnings | GAP-A7 (T-016) | agents `math_worker/loop.py`, `history.py` | **DONE** `95fe63e` |
| H-5 | Gateway integrity trio: `delete_ruling` txn+event, `update_claim_status` mandatory event, idempotent replays return real `event_id`/`balance_after`, schema-load suppress removed | GAP-C2/C3/C4 (T-042/043) | db-gateway `db_writer.py`, gateway `routers/court.py`, court `dispute_db_client.py`, `schema.sql` | in progress (rework) |

**H-5 review findings (orchestrator, 2026-07-10) — two carried forward:**

1. **`schema.sql` is not re-runnable.** Its `CREATE TABLE` statements carry no `IF NOT EXISTS`; that is precisely what the `contextlib.suppress(sqlite3.OperationalError)` in `_init_schema` was hiding (GAP-C4). Removing the suppression therefore requires gating the `executescript` on an empty database (`_schema_is_absent()`), plus explicit additive `ALTER TABLE` migrations for any new column. Schema evolution now has a real, if minimal, migration path — previously it had none, and a new column would simply never reach an existing `economy.db`.
2. **Idempotent-replay event ids cannot be recovered by matching event content.** Every client mints a fresh `timestamp` per call, and `escrow_lock`'s event payload embeds a fresh `escrow_id`, while `register_agent` mints a fresh `agent_id` (`agent_db_client.py:37-52`, `ledger_db_client.py:115-125,198-215`). The original `event_id` must instead be **persisted on the row it accompanies** — new nullable `bank_transactions.event_id` and `bank_escrow.event_id` columns — and read back on replay (`register_agent` can key on the existing agent's id, since an agent registers once). Rows written before the migration return `null`, which is the only honest answer for them. *A replay test that reuses the same request body cannot detect this class of bug; replay tests must construct a fresh body the way the real client does.*

**Contract delta for WP-12:** `DELETE /court/rulings/{claim_id}` now requires a JSON body carrying `event`, so that the deletion emits an event in the same transaction (T-042's "every write includes an event"). `db-gateway-service-specs.md` documents it as bodyless. The gateway spec must be updated; Court is the only caller and now sends the body.

**Correction to §2.8 (found while reviewing H-3):** the plan originally called for "restoring the spec's dispute<15% ceiling on `stable`". That is **not implementable as written**: the spec's Economy Phases table (`observatory-service-specs.md § Economy Phases`) states only *sufficient* conditions and leaves combinations uncovered — an increasing trend at a 12% dispute rate matches neither `growing` (needs <10%) nor `contracting` (needs a declining trend **or** >20%), and a flat trend at 15–20% matches nothing either. Adding the ceiling forces those cases into some phase, and no ratified decision or test-spec case says which. The residual therefore stays `stable`, and **defining the uncovered combinations is added to WP-12's spec sweep** (a candidate Q for Florian if the phase taxonomy is user-visible product surface). Only the `stalled` behavior (MET-12/MET-13) was ratified and shipped.

**Frozen-test protocol clarification (established while resolving H-2):** fixture/fake modules (`tests/fakes/*`, `conftest.py`) are test *infrastructure*, not acceptance assertions. Making a mock deterministic or teaching a fake about a new schema column is permitted; adding, removing, weakening, or rewording any `assert` in an existing test file is not, and requires a ratified-decision exception recorded here first.

**Recorded frozen-test exception #1 (T-035, 2026-07-10).** Fixing GAP-A2 required changing two assertions, because both contradicted the frozen *test specification* they were supposed to implement:

| Test | What it asserted | Why it is wrong |
|---|---|---|
| `services/task-board/tests/unit/test_deadline_evaluator.py::test_evaluate_deadline_open_with_bids_not_expired` | an open task with bids stays open past its bidding deadline | Directly encodes GAP-A2. Contradicts `task-board-service-tests.md` **LIFE-03** ("Bidding deadline auto-expires… escrow released back to poster"), which sets no bid-count condition, and ratified openspec **T-035**. |
| `services/task-board/tests/unit/routers/test_bids.py::TestBidAcceptance::test_ba_10_accept_after_bidding_deadline_if_open` | a poster can still accept a bid after the bidding deadline | Contradicts **LIFE-07** ("task is expired, not open" after the bidding deadline) and **LIFE-09** (terminal `EXPIRED` blocks all mutations). It is also **mislabeled**: spec **BA-10** is "Accepting a bid updates `bid_count` correctly" and says nothing about deadlines. The behavior was invented locally and given a spec ID that means something else. |

A third, independent citation settles it: the API spec's own state-transition table (`docs/specifications/service-api/task-board-service-specs.md:158`) reads `| OPEN | EXPIRED | Bidding deadline passes | Escrow released to poster |` — unconditional, with no bid-count qualifier, and the adjacent `OPEN → ACCEPTED` row grants no post-deadline allowance.

**Product consequence, surfaced deliberately:** a poster who lets the bidding deadline pass can no longer accept a bid; the task expires and escrow is refunded to them. That is what T-035 ratifies ("expires it and releases escrow back to the poster") and what LIFE-03/07/09 plus the transition table describe. Previously such a task stayed `open` forever with escrow locked. If this is not the intended product behavior, it is a decision to revisit — but every spec and the ratified backlog say it is.

**Residual risk accepted, not silently absorbed:** `agents/tests/e2e/test_task_board.py:309` posts with `bidding_deadline_seconds=5`, then bids and accepts. Before T-035 a bid made the task immune to bidding-deadline expiry, so the window was irrelevant; now the three localhost round trips must complete inside 5 s or the accept returns 409. Measured margin is roughly three orders of magnitude, and root `just ci-quiet` (which runs this e2e against the live stack) passes, so the test is left untouched rather than edited. If it ever flakes, raise that one setup value — the test's subject is the *execution* deadline, and the 5 s bidding window is incidental to it.

### WP-01 — Governance: one tracker, working gates (S) ⚠Q-1
1. Execute the Q-1 decision; write `docs/plans/2026-07-XX-tracker-decision.md`. If openspec wins (recommended): migrate `tickets.md#T-001` into `openspec/specs/completion-backlog/spec.md` under a fresh stable ID (**T-100**, avoiding the collision), delete `tickets.md`; if tickets.md wins: mark `delivery-governance § Tracker Migration` superseded and renumber the open ticket to a non-colliding ID.
2. Fix the never-working commit gate: `.claude/settings.json` PreToolUse hook → point at the real `scripts/ci-quiet-hook.sh` and the correct jq field, or delete the hook (decide with Q-1's governance answer). Proof: trigger a commit with CI red → blocked.
3. Remove the beads mandate from AGENTS.md tracking sections (full AGENTS rewrite lands in WP-12; the tracker paragraph swaps now so no new work lands in the wrong system).

### WP-02 — PKI extraction into a library (M)
1. Create `libs/service-auth` (pyproject: cryptography, service-commons); move `agents/src/base_agent/signing.py`, `platform.py`, `user_agent.py` logic there; `agents/base_agent` keeps thin re-export shims so the frozen agents tests keep importing `base_agent.*` unchanged.
2. **One canonicalization**: `create_jws` uses `json.dumps(payload, sort_keys=True, separators=(",", ":"))`, header `{"alg":"EdDSA","typ":"JWT","kid":…}`. (Verification is over bytes-as-sent, so in-flight mixed tokens stay valid during rollout.)
3. Delete the two duplicate implementations: `services/task-board/src/task_board_service/clients/platform_signer.py` (task-board signs via the lib) and the crypto half of `tools/src/demo_replay/wallet.py` (imports the lib; its no-heavy-deps rationale dies once the lib is dependency-light).
4. Rewire the 5 service pyprojects from `base-agent` → `service-auth`; drop the `base_agent` mypy ignore overrides; update Dockerfile COPY sets.
5. Tests first: new lib test suite (keygen roundtrip, sign/verify, tamper rejection, cross-canonicalization verify against fixtures captured from the old signers); per-service architecture test forbidding `base_agent` imports under `services/*/src`.
6. Acceptance: `grep -rn "from base_agent" services/*/src` → 0 hits; `just ci-quiet` green.

### WP-03 — Two-tier auth rollout (M) — closes GAP-B1/B3/A14, T-021/T-033/T-034
Per service, in this order (test-first: for each platform op, a failing integration test asserting the op **succeeds while Identity is stopped**; for each agent op, that verification still routes via Identity; plus precedence tests asserting payload-validation errors beat 403):
1. **central-bank**: `routers/helpers.py` — platform ops (`create_account` w/ balance, `credit`, `release`, `split`) verify via `platform_agent.validate_certificate`; agent ops (`escrow_lock`, reads, zero-balance self-account) keep `IdentityClient.verify_jws`. Fix inverted precedence on credit/release/split (`accounts.py:143`, `escrow.py:102,164`). Config: `platform.agent_id` placeholder removed — identity always resolved from `agent_config_path` registration; `db_gateway`/`identity` become non-Optional in `config.py` (T-034).
2. **reputation**: platform/`force_visible` path verifies locally; replace the broken `PlatformIdentityClient(IdentityClient)` inheritance with a narrow `JwsVerifier` Protocol + composition (fixes the latent `super().__init__` bug); `db_gateway` non-Optional.
3. **task-board**: `record_ruling` verifies locally; **delete the `_tampered` test-marker branches** (`token_validator.py:158,203`) and inject a verification strategy at the composition root instead; agent ops unchanged.
4. Acceptance: delivery-governance T-021 proof — grep shows no `verify_jws` call on any platform-op path; live stack with Identity down still accepts platform ops and rejects agent ops cleanly; `just ci-quiet` green.

### WP-04 — Gateway integrity & client-layer consolidation (L) — closes GAP-C2..C7, A10, A14-adjacent stores, E2/E3
1. `delete_ruling`: wrap in `BEGIN IMMEDIATE`, write a `ruling.deleted` event in the same transaction; `update_claim_status`: event becomes mandatory (`db_writer.py:1190-1240,1407-1414`). Failing tests: delete → events row exists; status update without event → 400.
2. Idempotent replays return the original `event_id` and the real `balance_after` (`db_writer.py:237,412,536-537`) (T-043).
3. Remove `contextlib.suppress` around schema load (`db_writer.py:66-69`) — schema failure kills startup.
4. `DbReader` gets the connection via explicit lifespan wiring (no `state.db_writer._db` reach-in); the single-writer/no-await-in-txn invariant gets an ADR (WP-12) and a comment at the connection site.
5. Error mapping via `sqlite3` error codes (`e.sqlite_errorcode`), not message substrings; constraint-violation messages stop leaking table/column internals (SEC-02); `/health` size includes `-wal`/`-shm`.
6. Bank-store parity (T-030/031/032): zero-amount split credits skipped in **both** stores; in-memory store aligns tx `type` vocabulary (`escrow_lock`/`escrow_release`), bare references, poster==payer guard, `invalid_amount` code; add ONE shared contract test-suite executed against both `InMemoryLedgerStore` and `LedgerDbClient` (via in-process gateway) so they can never drift silently again.
7. Client consolidation (T-082, F-04), staged service-by-service: extend `libs/service-clients` (async) to cover the read routes services actually use; each service's sync `*_db_client.py` is replaced by (or rebuilt on) the async lib client; task-board drops its bespoke 439-line `CentralBankClient` for lib `BankClient` + WP-02 signer. End state: zero exported lib clients without importers, zero `httpx.Client(` (sync) under `services/*/src`, and the lib has its own test suite.

### WP-05 — Task Board lifecycle correctness (M) — closes GAP-A2/A3/E6/E7 + T-035/036/037
1. T-035 (failing e2e first): expired evaluation fires **regardless of bid count**; escrow released to poster exactly once (CAS via gateway `constraints`); emits `task.expired`.
2. ⚠Q-5: if the periodic evaluator is ratified — add a lifespan background task (interval from new required config `deadlines.evaluation_interval_seconds`) sweeping open/accepted/submitted (+ disputed once WP-06 lands) through the existing `evaluate_deadline` path; if lazy-only is ratified — record the ADR and route all readers through TB (couples to Q-4).
3. Court-call resilience: typed mapping of connect/timeout/HTTP errors from `file_claim`/`submit_rebuttal` → `502 court_unavailable`; dispute state unchanged on failure (failing unit test with a dead Court first).
4. Add `("POST","/rebuttal")` (and audit the full list) to `_JSON_VALIDATION_ENDPOINTS` (`core/middleware.py:14-23`).
5. Response contracts: align the dead Pydantic models to the real wire shapes (`BidResponse.proposal`→`amount` etc.), wire them as `response_model` (or delete + add explicit contract tests — pick wiring; it's the enforcement the hand-built dicts lack).
6. Remove undocumented action aliases `file_dispute`/`submit_ruling` (`task_manager.py:1009,1193`) (T-036); verify + fix `title_too_long`→`invalid_payload` (T-037).

### WP-06 — Court chain completion (L) — closes GAP-A1/A4/A8/A9/B4 + T-039/040/041 ⚠Q-5, Q-13(docs)
1. **Ruling trigger** per Q-5 (recommended shape: the TB deadline evaluator watches `disputed` tasks; once a rebuttal exists or the rebuttal deadline passes, TB fires a platform-signed `POST /disputes/{id}/rule`, idempotent against `dispute_already_ruled`). Failing e2e first: feeder-disputed task reaches `ruled` with **no demo engine involved** — this is the proof for GAP-A1.
2. **Retry-clean ruling (T-040)**: reorder so every step is idempotent and convergent — judges → persist votes+ruling court-side (recoverable state) → TB `record_ruling` (made idempotent: identical `ruling_id` re-record on an already-ruled task → 200) → Reputation feedback ×2 (409 `feedback_exists` treated as success) → dispute `ruled`. Spec wording updated in WP-12 (retry/compensation, not impossible rollback).
3. Rebuttal-window enforcement on `/rule`: allowed only when a rebuttal exists or the deadline passed; formalize `dispute_not_ready` (spec it, T-039) and resolve the phantom `rebuttal_submitted` status.
4. **Deliverables to judges** (verify-first): determine what `get_task` actually returns; if judges never see content, court fetches the task's assets (`GET /tasks/{id}/assets/{asset_id}`) up to a new required `judges.max_deliverable_bytes` and includes text content in the prompt. Failing test: judge prompt contains the uploaded solution text.
5. De-hardcode: feedback cutoffs 80/40, comment cap 256, MockJudge fixed pct → `config.yaml` (required keys).
6. Remove the process-wide `ServiceError.__init__` monkeypatch (`court_service/__init__.py:10-32`) — fix the legacy 3-arg call sites instead.
7. Judge ops documentation (mock vs LM Studio vs hosted) lands with WP-12 (T-041) once Q-13 answers.

### WP-07 — Reputation integrity (M) — closes GAP-A5/A6 + VIS-09
1. **Atomic reveal server-side**: gateway `POST /reputation/feedback` performs the reverse-pair lookup + dual `visible=1` UPDATE inside its `BEGIN IMMEDIATE` (mutual-reveal policy moves out of the client); `FeedbackDbClient` drops its read-then-write. Failing test first: interleaved mutual submissions against the in-process gateway must both end visible (currently can both stay sealed).
2. Court/platform feedback: spec + tests for the `force_visible` path (platform-signed, `from`=platform, immediate visibility, category semantics per §2.7); today it has zero tests.
3. VIS-09: add an injectable clock seam (mirror `ui_service.services.database._clock`) + failing test for the 24h lazy reveal.

### WP-08 — UI & operator (L) — closes GAP-A11/A12/B2/C1/E10 + T-046/047/048/049/093/095 ⚠Q-2/Q-3/Q-4 for items 4–6
1. Phase fix (unblocked; decides `tickets.md#T-001`): `compute_economy_phase` emits `stalled` when no recent tasks (`metrics.py:653`) and restores the dispute<15% ceiling on `stable` (`metrics.py:658`). The already-written e2e `test_x01…` becomes the failing test that turns green.
2. Verify-then-fix the June residuals: reward bucket includes 100 (T-047), frontend accepts `increasing` (T-048).
3. Enforce `request.max_body_size` (wire the commons middleware) — today declared, never enforced.
4. ⚠Q-2: dedicated operator identity (recommended: new roster handle `operator` with own keypair + zero-balance account; proxy signs as operator; platform key retreats to notary ops only).
5. ⚠Q-3: proxy exposure per answer (minimum: keep 127.0.0.1 bind documented as the boundary; optional shared-secret header from config).
6. ⚠Q-4: read path per answer; note migration to gateway reads requires adding a gateway `GET /events?after_id=` route (none exists today) for SSE polling.
7. Performance: `list_agents` single-query aggregation (kill the ~10-queries-per-agent N+1) and bucketed GDP history (mirror the sparklines `GROUP BY` pattern).
8. Product views: quarterly-report page (T-093), agent-profile + leaderboard/earnings/satisfaction views (T-095) in vanilla JS against existing endpoints.
9. Integration tests for every `/proxy/*` route against a live downstream (T-049 test side; spec side WP-12).

### WP-09 — Agent runtime & demo honesty (M) — closes GAP-A7/A13/E8(agents)/E11 + T-016/017/075
1. T-016 (failing unit first): review-poll timeout records `TaskOutcome.TIMEOUT`, no earnings inflation (`math_worker/loop.py:162-171`).
2. T-017: `MathWorkerLoop` phase-machine unit tests incl. the mutation guard (reverting a status literal to `"BIDDING"` must fail tests).
3. `just start-mathbot` defaults to the profile/factory path (legacy flat-config path retired or explicitly flagged).
4. Drop the unused `strands-agents` dependency; keep the `@tool` design doc as future work.
5. Demo/API drift (verify-first): reconcile `demo_replay/clients.py` feedback contract with the real Reputation API (`role` vs `from_agent_id`; `reveal_feedback` endpoint existence); unknown scenario action becomes a hard error; resolve `scale.yaml`'s dangling dispute.
6. `demo_replay` gets a config file (kills `clients.py:18-21,296,418` hardcoded URLs) + unit tests over step dispatch with mocked clients; delete dead `PuppetMaster`/`PuppetAgent`.
7. `tools/justfile` gains `test`/`ci` recipes; root `just ci` gains a tools phase.
8. Fund-feeder CLI tests: arg parsing, non-positive amount, exit codes (T-075).

### WP-10 — Deployment & policy (L) ⚠Q-6/Q-7/Q-14
1. Docker per Q-6 — full fix means: per-service `docker-config.yaml` with compose service-name URLs + current config schema; a named volume for `economy.db` (unless Q-4 removed the UI's file dependency); Dockerfiles copying all editable path deps (post-WP-02: `libs/*`); db-gateway Dockerfile rebuilt on the common convention; 8007 unpublished (internal network only); `depends_on` completed (T-081); a compose smoke test (up → all healthy → one task lifecycle) as an opt-in CI phase. Descope alternative: delete compose/Dockerfiles and document local-only.
2. Env policy per Q-7: either an ADR sanctioning exactly `CONFIG_PATH` + LLM `api_key_env`/`${VAR}` as the only env seams, or replace `CONFIG_PATH` with an explicit `--config` argument plumbed through justfiles/compose.
3. Hosted CI per Q-14 (e.g. `.github/workflows/ci.yml` running `just ci-quiet` with uv+just setup; mock judges keep it LLM-free).

### WP-11 — Hygiene, dead code, structure (M)
Grouped mechanical items, each landing with the relevant arch/unit test where one is expressible: remove dead deps (`pyjwt` ×4) and dead config (`database.path` ×4, `DeadlinesConfig`/`LimitsConfig`, `crypto.algorithm` or wire it); delete legacy shims (`*_store.py` aliases, identity `agent_store` compat, reputation sqlite store + shim) and the stale `services/task-board/data/platform.pem`; single-source the duplicated in-memory stores into `tests/fakes/`; drop phantom `observatory_service` from all seven `test_cross_service_deps.py`; justfile port variables + compose healthcheck de-duplication; guard collections add db-gateway/ui and drop `DELEGATE.md`; bank `/health` degrades (`503`/flag) instead of 500 when the gateway is down; commons: remove `middleware_error_response` default param (update 6 middlewares), add seconds to the JSON log timestamp; identity adopts the commons exception-handler factory and moves router-layer validation helpers into its services layer; UI `events.py` uses the `DbConn` dependency; delete `tools/seed-economy.sh`; bounded god-class decompositions — `task_manager.py` into per-domain coordinators (creation/bidding/review/ruling), `db_writer.py` into per-domain writers behind the existing routers, keeping router surfaces byte-stable.

### WP-12 — Documentation & specification sweep (L) — after behavior WPs settle; closes GAP-G2..G6, C6, F5 doc-sides
1. Rewrite the per-service API/auth/test specs to §2: snake_case codes everywhere (T-020/062); gateway spec gains the read API, `constraints`, port 8007, event-pairing contract incl. the new delete/status events, stale columns (T-022/044); court spec drops the Central-Bank split + `CENTRAL_BANK_UNAVAILABLE`, gains retry semantics + `dispute_not_ready` + kid policy (T-023/039/041 with Q-13); task-board spec gains `amount` bids + BID-09..12 rework, dispute→Court call, ruling-settles-escrow, pagination, `content_hash`, `/rebuttal` (T-024/038); identity spec gains `verify-jws`, gateway persistence, 3-field envelope, retires raw `/verify` (T-050/051); the observatory spec is replaced by a **ui-service spec** (port 8008, vanilla JS, proxy routes + operator identity per Q-2/3/4) (T-025/049); reputation auth-spec rewritten to the real JWS-token model; `rebuttal` vs `rebuttal_pending` literal unified.
2. Regenerate README/AGENTS.md from this document (T-060/061: seven services + agents + both libs, real ports, real recipes, no beads/DELEGATE.md/demo-scenarios, single Landing-the-Plane block, Python ≥3.12); CHANGELOG count (T-063); service-implementation-guide (T-064); sequence diagrams regenerated from §2 flows; small-doc sweep incl. the two missing SUPERSEDED banners and `scripts/demo/README.md` (T-065).
3. Write the missing ADRs: escrow-settlement ownership (R4), two-tier auth (R2 refinement), events-via-gateway (Option B), DB-over-HTTP gateway tradeoff, tracker decision (Q-1), env-var policy (Q-7), UI read path (Q-4) — and date the 8 reconstructed arc42 ADRs.
4. Record the untracked-generated-docs policy (commit `a791984`): which docs/ subtrees are archival (banner-marked) vs re-tracked.
5. Acceptance greps: zero UPPERCASE error codes in `docs/specifications/`; zero `8006`; zero `bd ` / beads references outside historical archives; zero `observatory` as a live service.

### WP-13 — Test-debt closure & the final gate (L) — closes GAP-F1..F6 + T-045/070/071/072/073
1. Shell acceptance suites: regenerate configs to boot current services, flip assertions to snake_case, wire a root `just test-acceptance` recipe (T-070) — or retire them if WP-12 makes the pytest suites the sole acceptance layer (default: fix identity/CB/reputation suites; they're the only executable spec of record).
2. Real per-service integration suites replacing the empty stubs (T-071), incl. the untested production stores (`LedgerDbClient`, `FeedbackDbClient`) and a litellm `LLMJudge` test via mocked transport; wire per-service `test-integration` into a root CI phase (stack-up pattern from `2a907b1`).
3. Root recipe + CI phase for the UI Playwright suite (111 tests).
4. New e2e: cancellation escrow refund (T-073 second half), autonomous dispute→ruling (from WP-06), true multi-connection gateway concurrency incl. exactly-one-201 duplicate registration (T-045), startup-order regression.
5. Coverage-vs-spec-ID sweep (T-072): every test-spec ID either has a passing pytest reference or is re-specified in WP-12.
6. **Definition-of-done run**: `just ci-quiet` exit 0 from repo root with all new phases included (§8).

### WP-14 — Product tail (vision work, post-target) ⚠Q-8/Q-11/Q-12/Q-15
Text-Classification Arena workers + emergent-specialization assertions (T-091); economy-graph landing animation replanned for vanilla JS (T-094); bid-amount economics if Q-8 ratifies payment-at-bid; contract object if Q-11 ratifies it; reputation score aggregation endpoint if Q-12 ratifies it; dispute-cost/zero-balance/contract-cap mechanics per Q-15. Salary stays deferred (R8).

---

## 6. Backlog reconciliation (openspec T-IDs, verified against 2026-07-09 code)

DONE = verified in code · PARTIAL = code or doc half landed · OPEN = not done · UNVERIFIED = not re-checked this pass (re-verify before working it).

| T-ID | Subject | Status | Evidence / where it lives now |
|---|---|---|---|
| T-001 | CI green | UNVERIFIED (gate rerun = WP-13 §6) | not executed in this analysis pass |
| T-010 | lowercase agent status vocab | **DONE** | loops query `open`/`submitted`/`disputed`/`ruled`; CHANGELOG Unreleased |
| T-011 | Court mixin + rebuttal | **DONE** | `CourtMixin` file_claim(5-arg)/submit_rebuttal/trigger_ruling; TB `/rebuttal` |
| T-012 | trust-model decision | **DONE** | decision record + court code adheres (audit §10) |
| T-013 | dispute→Court handoff | **DONE** | `task_manager.py:1072-1078` synchronous platform `file_claim` |
| T-014 | semantic lifecycle events | **DONE** | `task_db_client.py:13-21,104-109` status→event map incl. `task.auto_approved` |
| T-015 | Court in demo | **DONE** (minor: `scale.yaml` dangling dispute → WP-09) | quick/full-lifecycle rebuttal+ruling steps; commit `2f2ef42` |
| T-016 | timeout ≠ approved | **OPEN** | `math_worker/loop.py:162-171` → WP-09 |
| T-017 | loop/mixin tests | PARTIAL | some loop tests exist; mutation guard absent → WP-09 |
| T-018 | dispute e2e chain | **DONE** | `agents/tests/e2e/test_disputes.py`, `test_court_rulings.py` |
| T-020 | snake_case canonical | PARTIAL | code done; all 22 spec files + 3 shell suites still UPPERCASE → WP-12/13 |
| T-021 | local platform auth | **OPEN** | CB/TB/reputation still Identity-HTTP → WP-03 |
| T-022 | gateway read API blessed | OPEN (doc) | spec still "no reads" → WP-12 |
| T-023 | escrow-split ownership | PARTIAL | code done (TB settles; court never calls bank); both specs + RULE tests stale → WP-12 |
| T-024 | bid amount documented | OPEN (doc) | TB spec still proposal-only → WP-12 |
| T-025 | ui@8008 canonical | OPEN (doc) | README/specs still observatory/8006-8007 → WP-12 |
| T-026 | frontend stack decision | **DONE** (2 banner stragglers → WP-12) | decision record + 9 banners |
| T-030/031/032/033/034 | bank store/auth/config | **OPEN** (T-030 partial: schema CHECK constrains gateway path) | audits §3.8; → WP-03/WP-04 |
| T-035/036/037/038 | TB expiry/aliases/title/spec | OPEN (T-037 UNVERIFIED) | → WP-05 + WP-12 |
| T-039/040/041 | court cleanup/atomicity/judge docs | **OPEN** | → WP-06 + WP-12 |
| T-042/043/044/045 | gateway event/replay/spec/conc | **OPEN** | → WP-04 + WP-12/13 |
| T-046 | stalled phase | **OPEN** | `metrics.py:653` → WP-08 (decides `tickets.md#T-001`) |
| T-047/048 | bucket 100 / trend string | UNVERIFIED | → WP-08 verify-first |
| T-049 | proxy spec'd+tested | **OPEN** | → WP-08 + WP-12 |
| T-050/051 | identity verify-jws/persistence spec | OPEN (doc) | → WP-12 |
| T-060–T-065 | docs sweep | **OPEN** (all verified stale ✅V) | → WP-12 |
| T-070/071/072/073/074/075 | test debt | T-074 largely DONE (semgrep + per-service `test_db_client_isolation`, with the silent ui exception); T-073 PARTIAL (auto-approve e2e exists; cancellation-refund absent); rest **OPEN** | → WP-13 |
| T-081 | compose depends_on | OPEN — subsumed by the 6-defect Docker reality | → WP-10 ⚠Q-6 |
| T-082 | client consolidation | **OPEN** | → WP-04 |
| T-083 | cleanup | PARTIAL (4.4GB copy + root logs gone; stray `agents/test_api_keys.py`, dead configs remain) | → WP-11 |
| T-084 | semgrep rule audit | PARTIAL (rules updated `5f514c1`; no-default-values FP status unverified) | → WP-11 |
| T-085 | gateway health logging | UNVERIFIED | → WP-11 verify |
| T-090 | salary | **DECIDED-DEFERRED** (R8); doc claims still to purge | → WP-12 |
| T-091/093/094/095 | product tail | T-095 PARTIAL (badge metric removed `535c48b`); rest OPEN | → WP-08/WP-14 |
| T-092 | vision open questions recorded | **OPEN** — this document's §9 is the collection; answers become the decision records | → WP-01/§9 |

---

## 7. Risks & sequencing strategy

1. **Money-path changes carry the highest regression risk** (WP-04/05/06). Mitigation: the shared bank-store contract suite (WP-04.6) lands *before* behavior changes; every escrow-touching item is proven by an e2e that inspects ledger rows; `escrow_pending` retry semantics are preserved.
2. **Frozen-acceptance-test discipline vs stale specs.** Several suites already encode deviations (bid `amount`, court escrow). Order matters: behavior WPs implement the *ratified* contract; WP-12 re-issues the specs; WP-13 then reconciles suites. Never edit a frozen test in the same change that alters the behavior it guards.
3. **Two-tier auth rollout can lock everyone out if mis-staged.** WP-03 goes service-by-service behind failing-first integration tests, with Identity-down proofs; WP-02 (single signing lib) must land first so there is exactly one token format in play.
4. **Docker work is high-effort/low-information until Q-6.** Nothing else depends on it; it is deliberately late (WP-10) and skippable by decision.
5. **Context for implementers**: each WP is sized for one focused session/worktree (per AGENTS.md workflow); WP-04 and WP-06 are the two that warrant their own worktrees and intermediate commits.
6. **The final gate is cumulative**: WP-13's full `just ci-quiet` (with the newly wired phases) is the only claim of completion; partial WPs never close their T-IDs (delivery-governance § Ticket Closure Gate).

---

## 8. Definition of done for the target state

All of the following hold simultaneously:

1. `just ci-quiet` exits 0 from the repo root, where CI now includes: structure check, 7 service CIs, agents CI, **tools CI**, per-service integration phase, cross-service gateway tests, agents e2e, **UI Playwright e2e**, and (if Q-6 kept Docker) the compose smoke phase.
2. An **autonomous** economy round completes with zero manual/demo intervention: `just start-all` + `fund-feeder` + feeder + mathbot produce posted→bid→accepted→submitted→{approved | disputed→rebutted→**ruled**} tasks with correct ledger balances and semantic events — verified by the WP-06 e2e. **Two distinct drivers are missing today** and both must land first: nobody accepts bids (GAP-A15, ⚠Q-16) and nobody triggers rulings (GAP-A1, ⚠Q-5). Until then the loop stalls at `open`, and this criterion cannot be met no matter how many other gaps close.
3. Platform operations verify locally in all five services (Identity may be down; platform ops still work); agent operations verify via Identity `verify-jws`; grep proofs per T-021.
4. Every gateway write emits an event in the same transaction (incl. delete/status paths); replays return real values.
5. The UI shows a live economy without seeded SQL, renders `stalled` on an empty DB, and its write path uses the Q-2/Q-3-decided operator identity; every `/proxy/*` route is spec'd and integration-tested.
6. `docs/specifications/**` + README + AGENTS.md describe exactly this system (grep gates from WP-12.5 pass); every §9 answer exists as a decision record; exactly one issue tracker is in use.
7. No dead lib clients, no `base_agent` imports under `services/*/src`, no sync `httpx.Client` in async services, no `_tampered` branch, no `ServiceError` monkeypatch, no phantom `observatory_service` references.

---

## 9. OPEN QUESTIONS for Florian

Answers to these are the only missing inputs; each becomes a decision record (WP-01/WP-12) and unblocks the ⚠-marked items. **Recommendation ≠ assumption** — nothing below was pre-implemented.

**Q-1 — Canonical issue tracker.** `openspec/specs/delivery-governance/spec.md` declares OpenSpec canonical and bans markdown trackers; yet root `tickets.md` was reintroduced (2026-06-29) and its `T-001` collides with backlog T-001; AGENTS.md still mandates beads (removed). Options: (a) OpenSpec canonical, migrate+delete `tickets.md`; (b) `tickets.md` canonical, supersede the openspec governance clause. **Recommendation: (a)** — the backlog's stable T-IDs already live there. *(Note: openspec/ is currently untracked by git — whichever wins should be committed.)* Blocks WP-01.

**Q-2 — Dedicated UI-operator identity.** The UI's UserAgent **is** the platform (treasury) identity today. Should the operator be a distinct economic agent (own keypair/account, e.g. roster handle `operator`), leaving the platform key for notary ops only? **Recommendation: yes** — separates treasury power from browser actions and makes operator activity attributable in the economy. Blocks WP-08.4.

**Q-3 — UI proxy exposure.** `/api/proxy/*` is unauthenticated; anyone reaching 8008 can act as platform (today) / operator (after Q-2) and spend funds. Accept as local-single-user posture (documented), or add a minimal auth (config-driven shared secret)? **Recommendation: document 127.0.0.1-only as the boundary now; add the shared-secret header only if the UI is ever exposed.** Blocks WP-08.5.

**Q-4 — UI read path.** Keep the direct read-only SQLite connection (bless it: schema-coupled, breaks in Docker without a shared volume, needs the semgrep exception made explicit) or migrate UI reads to the gateway read API (consistent single data path, Docker-clean, requires a new gateway `GET /events` route and rewriting ~6 query modules)? **Recommendation: keep direct read-only for v1, formally documented as the single sanctioned exception (observatory pattern), revisit only if Docker (Q-6) is kept.** Blocks WP-08.6, shapes WP-10.

**Q-5 — Lifecycle automation.** Today all deadline transitions are lazy-on-read and **nothing ever triggers court rulings** (GAP-A1) or enforces the rebuttal window. Add a config-driven periodic evaluator in Task Board (recommended: one background loop sweeping deadlines *and* firing platform-signed ruling triggers after the rebuttal window), or stay lazy-only and accept stalled disputes/stale statuses when nothing polls? **Recommendation: periodic evaluator, interval in `config.yaml`.** Blocks WP-05.2, WP-06.1.

**Q-6 — Docker scope for v1.** Docker mode is broken six independent ways (§3.6) and zero CI covers it. Fix fully (≈WP-10.1, substantial) or descope (delete compose/Dockerfiles, document local-only until multi-host need)? **Recommendation: descope now, fix when there's a deployment target** — the local 4-tier flow is the only mode anything actually uses. Blocks WP-10.1.

**Q-7 — Environment-variable policy.** Your no-env-var rule vs three production seams: `CONFIG_PATH` (config-file selection, used by tests+compose), court `api_key_env`, agents `${VAR}` LLM keys (secrets can't live in YAML). Sanction exactly these as documented exceptions (ADR), or replace `CONFIG_PATH` with an explicit `--config` CLI argument everywhere? **Recommendation: sanction the two secret seams; replace `CONFIG_PATH` with `--config` only if you want strictness over churn.** Blocks WP-10.2.

**Q-8 — Bid-amount economics.** Bids carry an integer `amount` (ratified R5) but it has **zero monetary effect**: escrow = full reward at posting, and approval releases the full reward regardless of the winning bid. Should the winning bid become the actual payment (release bid amount to worker + refund difference to poster at settlement), as the vision's undercutting story implies — or stay signal-only for v1? **Recommendation: signal-only for v1 (document loudly); price-forming settlement as a WP-14 economic change with its own tests.** Blocks WP-14 item.

**Q-9 — Treasury bootstrap ownership.** The **UI** mints the 1,000,000-coin treasury at startup (tier 4). Where should genesis live: (a) a `just provision` bootstrap step / extended fund-feeder CLI (recommended), (b) central-bank startup config, (c) keep in UI? **Recommendation: (a)** — explicit, idempotent, service-neutral; also fixes "UI down ⇒ no treasury". Blocks the treasury part of WP-08/WP-11.

**Q-10 — Token/key hardening scope.** No JWS replay protection (no `iat`/`exp`/nonce — blunted by idempotency+state checks) and no key revocation. Accept both as documented v1 risks, or add token expiry (`iat`+`exp`, cheap with WP-02) now? **Recommendation: add `iat`/`exp` in WP-02 while the signing lib is open; defer revocation.**

**Q-11 — Contract object.** The vision promises a three-party co-signed contract at acceptance ("both parties can independently prove the agreement"); nothing of the sort exists — acceptance just sets fields, and the openspec baseline codified that. Build a signed contract artifact, or formally descope it (escrow remains the binding instrument)? **Recommendation: descope for v1 with an explicit decision record; revisit if external parties ever need proof.** Blocks WP-14 item.

**Q-12 — Reputation score aggregation.** The vision defines numeric scores (start 100%, drop via rulings); the service deliberately stores raw feedback only, and no aggregate exists anywhere (agents can't ask "what's my standing?", which Scenario 2's specialization loop needs). Add `GET /reputation/agents/{id}/scores` (service-owned formula), keep consumer-side aggregation, or defer to WP-14 with the arena? **Recommendation: service-owned endpoint, spec'd formula, built in WP-14 together with T-091 (its first real consumer).**

**Q-13 — Production judge panel + vagueness rubric.** Dev default is 1 mock judge (fixed 50% — the ambiguity-favors-worker thesis is currently *inert*); LM Studio config exists only as comments; no doc defines panel size/models for a real run, and no operational rubric for "vague spec" exists (arc42 flags it). What is the intended real-run panel (size, models, same/different providers) and do you want a written vagueness rubric in the judge prompt? *(Vision open question.)* Blocks WP-06.7/WP-12 judge docs.

**Q-14 — Hosted CI.** No `.github/`; every gate is manual. Add a minimal GitHub Actions workflow running `just ci-quiet` on push/PR (mock judges keep it LLM-free), or stay local-only? **Recommendation: add it** — the June inventory already rated this P1 and the repo now has a remote. Blocks WP-10.3.

**Q-16 — Who accepts bids, and on what rule?** (GAP-A15, found 2026-07-10.) No autonomous component accepts a bid today; only the scripted demo and a human in the UI do. For the economy to run on its own, the **poster agent** (the feeder) needs an acceptance loop, and that loop needs a winning-bid rule. The vision implies lowest-price wins ("Bob bids 8 coins, Carol undercuts at 6. Alice accepts Carol" — `docs/main/agent-task-economy.md § Demo Scenario 1`), but nothing is specified about ties, reputation weighting, a reserve price, or how long a poster waits before accepting. What rule should the feeder use, and should it accept as soon as a bid arrives or wait out the bidding window to let competition form? *(The second half of that question matters: accepting the first bid destroys the price competition the whole thesis rests on.)* Blocks the new autonomous-acceptance WP; combined with **Q-5** it also gates §8's definition-of-done item 2.

**Q-15 — Remaining vision economics** *(the unrecorded T-092 set)*: (a) should filing a dispute cost coins? (b) what happens at zero balance — can a broke agent still bid/work? (c) cap concurrent contracts per agent or let reputation regulate? One answer each (or an explicit "defer, out of v1 scope") lets me close T-092 with decision records. **Recommendation: defer all three from v1 mechanics; record as explicit deferrals.**

---

## Appendix A: Documentation staleness ledger

| Doc / group | Action | Via |
|---|---|---|
| `openspec/specs/*` (3) | KEEP — canonical (pending Q-1); commit to git; fold §9 answers in | WP-01/WP-12 |
| `docs/plans/2026-06-13-*` decisions, `2026-06-26` startup-race | KEEP (current) | — |
| `docs/plans/2026-06-12-completion-inventory.md` | SUPERSEDED by this document — add banner | WP-12 |
| `docs/arc42/*` | KEEP as as-built reference; regenerate after WP-06/08; date the 8 ADRs | WP-12 |
| `docs/specifications/service-api/*` + `service-tests/*` (22) | REWRITE per §2 (casing, ports, gateway reads, escrow ownership, bids, identity, ui-replaces-observatory, reputation auth model) | WP-12.1 |
| `docs/specifications/schema.sql` | KEEP (live schema source); document the code-only columns in the gateway spec | WP-12.1 |
| `docs/specifications/{endpoint-error-handling,request-validation}.md` | KEEP casing authority; FIX request-validation (deposit example, loose PKI narrative) | WP-12.1 |
| `README.md`, `AGENTS.md`/`CLAUDE.md`, `CHANGELOG.md` | REGENERATE from this doc (T-060/061/063) | WP-12.2 |
| `docs/diagrams/system-sequence-diagrams.md` | REGENERATE from §2 flows (ports, gateway, Notary→platform) | WP-12.2 |
| `docs/main/agent-task-economy.md` | KEEP as vision; add a "status vs v1" preface (salary deferred, escrow-at-posting, Q-8/Q-11 outcomes) | WP-12.2 |
| React-era plans/mockups (9 banners + 2 stragglers) | ARCHIVE — add the 2 missing SUPERSEDED banners | WP-12.2 |
| `docs/plans/2026-02/03-*` remaining, `docs/codex-tasks/*`, `docs/java/*` | ARCHIVE (historical; untracked since `a791984`) — record the policy | WP-12.4 |
| `docs/plans/events-architecture.md` | ADDENDUM: Option B (gateway-written events) is the decision | WP-12.3 |
| `scripts/demo/README.md`, `tickets.md` | UPDATE / RETIRE per Q-1 | WP-01/WP-12 |

## Appendix B: Evidence base & method

17 parallel audit agents (2026-07-09): per-service code audits (identity, central-bank, task-board, reputation, court, db-gateway, ui — Opus), libs + agents/tools + tests/CI audits (Opus/Sonnet), repo-wide wiring/storage/port/key/env map (Sonnet), and five documentation extractors (target-architecture synthesis, API/test-spec contracts, codex-task archaeology with git-timestamp reconstruction, prior-audit findings register F-01..F-75, arc42 as-built + feature-plan eras). Orchestrator validation: every load-bearing claim used in §1–§4 was spot-checked against source (✅V marks direct file reads: ui/central-bank/db-gateway/reputation configs, db-gateway Dockerfile, reputation docker-config, docker-compose.yml, README, AGENTS.md, tickets.md, openspec specs ×3, vision doc, CHANGELOG, both 2026-06-13 decision records, the 2026-06-12 inventory); one inter-agent conflict (reputation's Docker CONFIG_PATH wiring) was resolved by direct read of `docker-compose.yml:59` against the erring agent. Claims not independently re-verified are attributed to their audit with `path:line` citations; items where evidence was genuinely insufficient are marked UNCLEAR/UNVERIFIED rather than assumed.

**Codex adversarial review (2026-07-10):** two high findings, both orchestrator-verified against source and folded in — (1) the blanket `docs/`/`openspec/` gitignore incl. the runtime `schema.sql` (extends GAP-G4 → H-1; Codex's framing that "the branch adds" the rules was wrong — they predate the branch, commit `a791984`); (2) the cross-dispute rebuttal injection (new GAP-B9 → H-2). No other challenge to the target architecture survived verification.
