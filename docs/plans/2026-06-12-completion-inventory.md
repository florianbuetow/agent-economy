# Project Completion Inventory — 2026-06-12

Inventory of work remaining to bring Agent Task Economy to its documented target state.
Produced by comparing every spec/plan/task document against the actual code (16 parallel
audit agents + a full `just ci-quiet` ground-truth run). Evidence is cited as `file:line`
in the per-section details; UNVERIFIED items are marked as such.

---

## A. Current state snapshot (ground truth, 2026-06-12)

| Fact | State |
|---|---|
| `just ci-quiet` | **RED.** Fails in Phase 1 at reputation `code-audit`: starlette 0.52.1 has PYSEC-2026-161 (fix: 1.0.1). task-board + ui service CI and all integration/e2e phases never ran in this pass. |
| Branch | `feat/central-bank-audit-log`, not merged to `main`, **no git remote configured** |
| Uncommitted work | Staged: deletion of entire `.beads/` directory. Unstaged: dependency pin changes in 5 services' `pyproject.toml`/`uv.lock` (looks like a partial CVE-remediation follow-up). Untracked: `CHANGELOG.md`, `pick-up.md`, `combined-service-logs.txt` |
| Issue tracking | **Broken.** `bd` reports "no beads database found"; `.beads/` is staged for deletion. CLAUDE.md/AGENTS.md still mandate bd for all tracking. |
| Services | 7 exist (identity 8001, central-bank 8002, task-board 8003, reputation 8004, court 8005, db-gateway **8007**, ui **8008**). No `observatory` service exists; port 8006 is unused. |
| `demo/agent-economy/` | 4.4 GB gitignored nested copy of the repo sitting in the working tree |

---

## B. Critical path: make the real lifecycle work end-to-end

These are the `pick-up.md` claims — **all five were independently CONFIRMED with evidence**.
Until these are fixed, the autonomous agents cannot actually participate in the economy and
the UI shows zeros against live data.

| # | Item | Pri | Evidence |
|---|---|---|---|
| B1 | **Agent loops use a stale uppercase status vocabulary.** `list_tasks(status="BIDDING")`/`"SUBMITTED"` return nothing; acceptance/approval/dispute/ruling detection never fires. | P0 | `agents/src/math_worker/loop.py:183,256,266,319,323,384`, `agents/src/task_feeder/loop.py:130`, `agents/src/task_feeder/review.py:63` vs `task_manager.py:32-34` (lowercase `open/accepted/submitted/approved/cancelled/disputed/ruled/expired`) |
| B2 | **Court mixin is incompatible with the Court API; no rebuttal exists.** `file_claim()` signs with the agent key (Court requires platform-signed) and omits `respondent_id`/`escrow_id`. `CourtMixin` has no `submit_rebuttal`. math_worker calls `file_claim` where it needs a rebuttal and has no `dispute_id`. Requires an explicit trust-model decision (platform mediates vs Court accepts agent-signed rebuttals). | P0 | `agents/src/base_agent/mixins/court.py:23-34`, `math_worker/loop.py:353`, `court/routers/disputes.py:79,83-87,130` |
| B3 | **Task Board dispute → Court handoff doesn't exist.** `POST /tasks/{id}/dispute` docstring says "send to Court" but only sets local status; TaskManager has no Court client at all. Decide: auto-file via platform-signed call, or document dispute filing as a separate platform step. | P1 | `routers/tasks.py:179`, `task_manager.py:986-1077` |
| B4 | **Lifecycle transitions emit generic `task.updated`.** UI/metrics filter on `task.accepted/submitted/approved/disputed/ruled` → all live counts are zero. `seed-economy.sh` fakes the specific events directly in SQL, which masks the bug in demos. | P1 | `task_db_client.py:145-174` (always `"task.updated"`, line 160) vs `ui/services/metrics.py:748-864`, `ui/data/web/assets/shared.js:291-297`, `tools/seed-economy.sh:418-531` |
| B5 | **Demo never exercises Court.** Both `quick.yaml` and `full-lifecycle.yaml` end at `action: dispute`; the replay engine has no Court actions and `clients.py` has no Court client/URL. Either scope the demo honestly or add file/rebuttal/ruling actions. | P2 | `tools/scenarios/quick.yaml:146-150`, `full-lifecycle.yaml:148-152`, `engine.py:107-131`, `clients.py:17-19` |

Supporting fixes discovered alongside:

- math_worker records review TIMEOUT as a full APPROVED win, inflating earnings history — `loop.py:166-170` (P2)
- No unit tests exist for `MathWorkerLoop`'s phase state machine; `test_court_mixin.py` asserts the *broken* payload (P1)
- No cross-service integration test covers dispute → Court → ruling → Task Board → Reputation (root `tests/integration/` tests each gateway domain in isolation) (P2)

---

## C. Cross-cutting decisions needed (one decision each, then mechanical work)

| # | Decision | Context | Pri |
|---|---|---|---|
| C1 | **Error-code casing: declare snake_case canonical and update all specs + shell acceptance suites** (or revert the code). A deliberate migration to snake_case happened (`docs/codex-tasks/error-code-casing-migration.md`, DONE), but every test spec still mandates SCREAMING_SNAKE and the identity/central-bank/reputation **shell acceptance scripts still assert uppercase** — they contradict the pytest suites and would fail against the live services. | All 7 services | P1 |
| C2 | **Auth architecture: local PlatformAgent verification vs Identity-service HTTP round-trip.** Auth specs (central-bank, reputation, court) say verification is local with no network call. Court does it locally; central-bank still calls `identity_client.verify_jws()` over HTTP (`central-bank/routers/helpers.py:36-47`, leftover from `central-bank-platform-agent-cleanup.md`, PARTIAL); reputation uses the remote path whenever the `identity:` config section is present (it is). | central-bank, reputation, specs | P1 |
| C3 | **DB Gateway reads: spec says "reads bypass the gateway entirely"; implementation has 27 GET endpoints and all services read through it.** Either bless the read API in the spec (and document all 27 routes) or remove it. | db-gateway spec | P1 |
| C4 | **Who executes the escrow split on a ruling?** Court spec says Court calls Central Bank split; Court actually delegates to Task Board's `record_ruling`, which performs escrow ops the Task Board spec says it must NOT do. `CENTRAL_BANK_UNAVAILABLE` is specified but unreachable; tests RULE-06/RULE-16 assert the delegated behavior. Pick one model, update the other side's spec + tests. | court, task-board | P1 |
| C5 | **Bids: `amount` (code, matches the product vision's competitive-price bidding) vs `proposal` text (task-board spec, which says "bids do not include an amount").** Code matches `docs/main/agent-task-economy.md` (Bob bids 8, Carol undercuts at 6) — the spec looks stale; update spec + BID-09..12 test cases accordingly. | task-board spec | P2 |
| C6 | **Observatory naming/port: spec+README say `observatory` @8006/8007; reality is `services/ui` @8008.** services/ui substantially implements the observatory spec (plus extra endpoints + write-capable `/proxy/*` routes that violate its read-only principle). Rename or re-document; spec the extras (sparklines, feed, earnings, proxy writes) or remove them. | ui/observatory | P2 |
| C7 | **React/TS/Vite frontend plans vs vanilla-JS reality.** 8+ plan docs (observatory frontend, economy graph, quarterly report page, leaderboard improvements, monthly earnings chart, NYSE theme) all target a React app that was never built. Decide: archive those plans and continue vanilla JS, or commit to the React build pipeline. | ui frontend | P2 |

---

## D. Per-service concrete bugs (independent of the C-decisions)

### central-bank
- Escrow-lock transaction written as `type: "debit"` instead of `"escrow_lock"`; references are prefixed (`escrow_lock:{task_id}` etc.) where specs require bare ids — breaks ESC-03/REL-03/SPL-07 (`in_memory_ledger_store.py:222,225,276,340-362`) — P1
- Zero-amount split credits are not skipped (violates `amount > 0` constraint) — P1
- In-memory store missing `poster_account_id == payer_account_id` guard (SPL-15) and uses wrong error code for out-of-range `worker_pct` — divergence between in-memory and DB-backed stores — P1
- Auth check order inverted for `GET /accounts/{id}` (403 before payload validation; spec says payload first) — P2
- `identity.*` and `db_gateway` config fields typed Optional but required at runtime (violates fail-fast rule; same issue in identity and reputation services) — P2

### task-board
- OPEN tasks with ≥1 bid **never expire** after the bidding deadline (`deadline_evaluator.py:63` guards on `bid_count == 0`) → escrow stuck forever — P1
- Undocumented action aliases `file_dispute`/`submit_ruling` accepted (`task_manager.py:1002,1094`) — P2
- `title_too_long` custom error code instead of spec'd `invalid_payload` — P2
- Undocumented `offset/limit` pagination on GET /tasks; `content_hash` in asset responses not in main spec — P3

### court
- `dispute_not_ready` error code and `rebuttal_submitted` status are not in the spec; dead branch — P2
- Ruling side-effects are not atomic (Task Board can be updated, then Reputation fails, dispute reverts) — spec promises rollback; needs compensation pattern or weaker spec wording — P2
- LLM judges are real LiteLLM calls pointed at a local LM Studio endpoint (`config.yaml:30-36`); fine for dev, but no documented production judge config — P3

### db-gateway
- `DELETE /court/rulings/{claim_id}` commits without writing an event — violates "every write includes an event" — P1
- Idempotent replays return `event_id: 0` and `balance_after: 0` instead of real values (`db_writer.py:237,408-413,534-538`) — P2
- Undocumented endpoints: `POST /court/claims/{id}/status`, the entire 27-route read API (see C3) — P2
- CONC-02 test accepts two 201s where spec requires exactly one 201 + one 409 — P3

### ui
- Economy phase emits `"idle"`; spec + frontend expect `"stalled"` (`services/metrics.py:640`) — P1
- Labor-market bucket `51_to_100` queries `BETWEEN 51 AND 99`, dropping reward=100 (`services/metrics.py:526`) — P1
- `observatory.js:42` checks `taskCreationTrend === 'growing'` but API emits `"increasing"` — trend arrow never renders — P2
- `/proxy/*` write endpoints have no spec and no tests — P2

### identity
- `POST /agents/verify-jws` fully implemented + tested but absent from both spec docs — spec-gate it — P1
- Spec still describes a local-SQLite leaf service; reality is gateway-backed with outbound HTTP — spec rewrite — P2

---

## E. Documentation debt (out-of-date docs found, as predicted)

| Doc | Problem | Pri |
|---|---|---|
| `README.md` | db-gateway port wrong (8006→8007); phantom `observatory` service @8007; `ui` port missing (8008); `libs/service-clients` missing; **`just ci-all`/`ci-all-quiet` recipes don't exist** (`ci`/`ci-quiet`); Python "3.11+" but all services require ≥3.12; system-diagram TODO; License TBD | P1 |
| `AGENTS.md` / `CLAUDE.md` | Say "five services", architecture block omits db-gateway, ui, `agents/`, `libs/service-clients`; reference nonexistent `docs/demo-scenarios/`; dependency map ends at 8005; "never edit" rule omits service-clients; duplicated "Landing the Plane" block | P1 |
| Service API/auth specs (all) | Stale on: error casing (C1), local-auth model (C2), `platform.public_key_path` vs actual `agent_config_path`, missing `db_gateway`/`logging.directory` config keys, SQLite-vs-gateway persistence | P2 |
| `docs/specifications/service-api/db-gateway-service-specs.md` | port 8006, "no reads" principle, missing columns (`bid_count`, `escrow_pending`, `content_hash`) | P2 |
| `MEMORY.md` (Claude project memory) | "8006 (observatory)" is wrong → db-gateway 8007, ui 8008 | P2 |
| `CHANGELOG.md` (untracked) | "five-service monorepo" → seven | P3 |
| `docs/service-implementation-guide.md` | Naming table missing db-gateway/ui; no `architecture/` test dir; no `middleware.py` pattern | P3 |
| `scripts/demo/README.md` | Describes a superseded `start.sh` demo flow; canonical is `just demo`/`demo-scale` | P3 |
| `docs/diagrams/system-sequence-diagrams.md` | Pre-dates identity-gateway migration, proxy router, live task wiring | P3 |
| `docs/plans/events-architecture.md` | Recommends Option A (EventWriter in commons); reality implemented Option B (db-gateway) — record the decision | P3 |
| `docs/mockups/nyse-theme-*` | Target a nonexistent React app — archive or rewrite (see C7) | P3 |
| `pick-up.md` | Once section-B work lands, fold into bd issues / delete from root | P3 |

---

## F. Test debt

- **Integration/performance test stubs are empty (1-line files)** in identity, central-bank, db-gateway (and others) — the spec'd acceptance behavior is only covered by shell scripts that currently assert the *wrong* (uppercase) error codes — P1
- Missing pytest coverage vs test specs (counts from the audits): identity ~34/48 behaviors lack pytest coverage; central-bank ~59/114; reputation ~20 cases (incl. VIS-09 reveal-timeout, READ-03/04 injection cases); task-board 8 cases; court is the outlier at effectively full coverage (104/104) — P2
- Five per-service `test_gateway_constraint.py` files and per-service `test_no_direct_db.py` pytest enforcement were planned but never written (semgrep rule exists) — P2
- No e2e for auto-approve-on-review-timeout or task-cancellation escrow refund — P2
- `agents/`: no `test_loop.py` for math_worker; `fund_feeder_cli` untested — P2
- Observatory/ui backend has integration tests but no unit router tests and is not organized against the observatory test-spec IDs — P3

---

## G. Infrastructure & repo hygiene

| Item | Detail | Pri |
|---|---|---|
| Fix starlette CVE | Bump starlette to ≥1.0.1 in reputation (and check all venvs) — this is what's breaking CI **today**; finish/commit the half-done dependency-pin changes sitting unstaged in 5 services | P0 |
| Resolve issue-tracker state | `.beads/` is staged for deletion and `bd` is dead, while AGENTS.md/CLAUDE.md/hooks still mandate beads. Either restore beads or remove the mandate + hooks. This inventory should land wherever that decision goes. | P1 |
| Land the branch | Commit or drop the unstaged dep changes, merge `feat/central-bank-audit-log`, configure a remote (README links github.com/florianbuetow/agent-economy), push | P1 |
| Hosted CI | No `.github/` exists; minimum: run `just ci-quiet` on push/PR | P1 |
| docker-compose | court `depends_on` missing central-bank; db-gateway/ui have no `depends_on`; reputation alone uses a separate `docker-config.yaml` | P2 |
| Dead code in `libs/service-clients` | 5 of 7 client modules (Bank/Gateway/Court/Reputation/TaskBoard) have zero callers; task-board hand-rolls a 440-line `CentralBankClient` instead | P2 |
| Cleanup | 4.4 GB `demo/agent-economy/`; `combined-service-logs.txt` at root; stray `agents/test_api_keys.py` (duplicates an integration test); dead `database.path` in identity config; dead `get_agent_path` in reputation config | P3 |
| Semgrep `no-default-values` rule | Likely false-positives on Pydantic/FastAPI defaults; interacts badly with `no-noqa` rule | P3 |

---

## H. Vision-level gaps (target state from docs/main/agent-task-economy.md & README)

- **Salary distribution** is named in the vision, README and CLAUDE.md ("salary distribution" per iteration) — no salary endpoint/scheduler exists in central-bank; funding is manual via credit + `fund-feeder` CLI. Decide whether v1 needs it or docs should stop claiming it. (P2)
- **Demo Scenario 1 "Content Studio"** (3 agents / 5 tasks incl. both dispute outcomes): partially realized by `quick.yaml`/`full-lifecycle.yaml` but without Court (see B5). **Demo Scenario 2 "Text Classification Arena"** (Regex Ron / Sklearn Sam / LLM Luna, emergent specialization over 10 rounds): not implemented — only math_worker variants exist. (P3)
- The vision's **"Open Questions"** (dispute filing cost, sealed bids, zero-balance behavior, concurrent-contract caps, judge panel composition) remain undecided and unrecorded — convert to tracked decisions. (P3)
- `docs/java/*` describe a candidate Java rewrite, but the codex variant documents **real spec conflicts** (court endpoint naming vs gateway tables, `escrow_pending` column) that apply to the Python system — fold those into the spec-reconciliation work (C3/C4). (P2)

---

## I. Suggested execution order

1. **Unblock CI** — starlette bump, finish the dep-pin commit (G/P0)
2. **Settle tracker + branch state** — beads decision, commit, remote, push (G/P1)
3. **Critical-path lifecycle wiring** — B1→B4 with the C2/C4 decisions made first; add the missing dispute→ruling e2e
4. **Cross-cutting reconciliation** — C1 casing sweep (code-or-spec), C3 gateway reads, C5 bids, C6 observatory naming
5. **Per-service D-bugs** in priority order
6. **Docs sweep (E)** in one pass once the decisions above are fixed
7. **Test debt (F)**, then **hygiene (G P2-P3)**, then **vision items (H)**

---

*Sources: 16 parallel audit reports (per-service spec-vs-code gap analyses for identity,
central-bank, task-board, reputation, court, db-gateway, ui/observatory; agents/, demo/tooling,
root docs, plans A/B/C, codex-tasks dated/undated, libs/infra) + `just ci-quiet` run on
2026-06-12 + manual repo-state inspection. Full agent reports are in the session transcript.*
