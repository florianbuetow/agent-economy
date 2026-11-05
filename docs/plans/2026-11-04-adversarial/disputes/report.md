# Adversarial campaign: disputes, Court, UI Operator, and incident chain

Baseline commit: `35cf0e3a5fcfd3c6a92d6c2e305bdfd711c7aa16` plus the
integrated campaign working tree in `source-manifest.sha256`. Every mutating
campaign run used repository-owned disposable storage, ports, keys, and agents.
The ordinary live incident was diagnostic context only and is not an acceptance
oracle.

## Retained executions

| Evidence | Exact command / cwd | Exit and result |
| --- | --- | --- |
| `task-board-disputes.log` | `UV_CACHE_DIR=/tmp/ate-adversarial-uv-cache uv run pytest tests/unit/routers/test_court_resilience.py tests/integration/test_mp07_native_court_handoff.py tests/integration/test_mp07_atomic_task_transitions.py::test_concurrent_approve_and_dispute_have_one_serial_winner tests/unit/routers/test_rebuttals.py tests/unit/test_wp06_ruling_trigger.py tests/unit/routers/test_two_tier_ruling.py tests/unit/routers/test_adversarial_ruling_prices.py tests/unit/routers/test_wp06_record_ruling_idempotent.py -q` from `services/task-board` | 0; 34 passed in 68.59s |
| `court-disputes.log` | `UV_CACHE_DIR=/tmp/ate-adversarial-uv-cache uv run pytest tests/unit/routers/test_rebuttal_replay_deadline.py tests/integration/test_ruling_durability.py tests/unit/routers/test_wp06_retry_clean.py tests/unit/test_ruling_orchestrator.py tests/unit/test_dispute_store.py tests/unit/test_evidence_bundle.py tests/unit/test_vagueness_rubric.py tests/unit/test_adversarial_panel_recovery.py -q` from `services/court` | 0; 41 passed in 3.71s |
| `court-process-restart.log` | `UV_CACHE_DIR=/tmp/ate-adversarial-uv-cache uv run pytest tests/integration/test_adversarial_process_restart.py -q` from `services/court` | 0; two literal SIGKILL cuts passed in 4.43s |
| `court-redaction.log` | `UV_CACHE_DIR=/tmp/ate-adversarial-uv-cache uv run pytest tests/unit/test_error_redaction_and_asset_auth.py -q` from `services/court` | 0; provider errors and asset reads expose no secrets |
| `ui-operator.log` | `UV_CACHE_DIR=/tmp/ate-adversarial-uv-cache uv run pytest` with native-proxy browser, posting journal, agent, observatory, task-lifecycle, cross-page, usability, and then-current adversarial UI files from `services/ui` | 0; 315 passed in 205.80s |
| `ui-adversarial-additions.log` | `uv run pytest -q tests/unit/test_adversarial_mutation_activation_browser.py tests/unit/test_adversarial_stale_acceptance_browser.py tests/unit/test_adversarial_lifecycle_reflow_browser.py tests/unit/test_dispute_waiting_state_browser.py tests/unit/test_submission_correction_guidance_browser.py tests/unit/test_dispute_deadline_projection.py tests/unit/routers/test_task_board_dependency_errors.py` from `services/ui` | 0; 34 passed in 9.65s on final focused source |
| `identity-capability-gap.log` | `UV_CACHE_DIR=/tmp/ate-adversarial-uv-cache uv run pytest tests/unit/math_worker/test_adversarial_capability_gap.py -q` from `agents` | 0; one test proves the current gap |
| `real-model-corpus.json` | `uv run python -m court_service.judge_eval --out /private/tmp/adversarial-judge-eval.json` from `services/court` | 0; 15/15 valid votes, five panel medians inside bands; closed-port provider probe returned generic `judge_unavailable` |
| `eco08.log`, `eco08-result.json` | `UV_CACHE_DIR=/tmp/ate-adversarial-uv-cache PYTHONPATH=. uv run --project agents python tests/adversarial/incident_chain.py --artifacts-root /private/tmp/ate-eco08 --result docs/plans/2026-11-04-adversarial/disputes/eco08-result.json` from repository root | 0; one claim, three votes, worker 0, poster refund 100, exact one-byte output |
| `eco05.log`, `eco05-result.json` | `UV_CACHE_DIR=/tmp/ate-adversarial-uv-cache PYTHONPATH=. uv run --project agents python tests/adversarial/compound_recovery.py --artifacts-root /private/tmp/ate-eco05 --result docs/plans/2026-11-04-adversarial/disputes/eco05-result.json` from repository root | 0; one compound task, explicit 60-credit winner, one asset/claim/ruling, three votes, worker 0/refund 100 |
| `ui-ci.log` | `just ci-quiet` from `services/ui` | 0; style, type checks, security checks, dependency checks, spelling, semgrep, audit, unit tests, architecture tests, and strict language-server checks all passed. |

Raw-log and result SHA-256 values are retained alongside this report. The
real-model run used the declared `qwen/qwen3.6-35b-a3b` panel and unchanged
five-case corpus. ECO-08 deliberately used three deterministic mock judges at
0%; its purpose was state-machine/economic recovery, not model-quality evidence.

## Sequence dispositions

| ID | Disposition | Whole-sequence oracle and evidence |
| --- | --- | --- |
| IDN-06 | **FAIL — confirmed G gap** | `identity-capability-gap.log` deterministically presents a coding and numeric job: the math worker can select the coding job because admission filters only reward and trusts the LLM choice; `parse_solution` freezes `ANSWER: 7`. A capability taxonomy/routing policy remains an owner decision. |
| DSP-01 | **PASS** | Task Board outage tests preserve one claimed pending operation, submitted state, locked escrow, and typed `court_unavailable`; exact replay after recovery creates one claim. UI proxy tests preserve the typed 502 instead of calling it malformed. |
| DSP-02 | **PASS** | Lost Court acknowledgment test persists the actual claim ID/deadline and exact retry finalizes one claim/event without a second delivery. |
| DSP-03 | **PASS** | Deterministic approve/dispute barrier yields one serial winner; losing operation cannot move task state or money. |
| DSP-04 | **PASS** | Assigned worker succeeds; poster/rival signed IDs fail before Court forwarding. Identical rebuttal replay returns the same record; changed and duplicate content conflict. Display names never enter the signed authorization predicate, so same-name/different-key follows the rival-ID result. |
| DSP-05 | **PASS** | API projection test carries Task Board's exact rebuttal deadline through authoritative read/local claim enrichment. Browser tests name the worker/deadline before rebuttal and Court after rebuttal; ruling terminology is not shown early. |
| DSP-06 | **PASS** | Trigger tests prove `dispute_not_ready` inside the window and eligibility at equality; browser clock tests switch from worker-wait to Court-wait at the same strict boundary. One trigger wins and failed triggers retry. |
| DSP-07 | **PASS** | Browser test renders HTML/script/system text as text with no image/request execution. Evidence bundle tests keep case sections as untrusted JSON. Injected real-model rebuttal median was 10%, and redaction tests keep provider/key text out of returned errors. |
| DSP-08 | **PASS** | Before/equality/after/missing/recorded browser cases and signed Task Board/Court tests prove the worker control exists only during a valid open window; equality/late/nonworker attempts fail. |
| CRT-01 | **PASS** | Rebuttal immediately enables the ruling trigger; concurrent triggers admit one lease while Task Board remains disputed until callback. |
| CRT-02 | **PASS** | New panel test makes judge 1 succeed, judge 2 timeout, and forbids judge 3. No vote/decision survives, status returns to rebuttal-pending, and retry collects exactly `[0,33,100]` once. |
| CRT-03 | **PASS** | Stored-decision durability and retry-clean tests lose delivery acknowledgment, replay the immutable ruling, skip rejudging, and retain one ruling/vote set/settlement/reputation pair. |
| CRT-04 | **PASS** | Public Task Board payout matrix asserts accepted price 60 with 0/50/100 gives worker/refund `0/100`, `30/70`, `60/40`; ruling replay/opposing terms cannot alter the winner. |
| CRT-05 | **PASS** | Court selects median 33 from `[0,33,100]`. Public settlement asserts price 61 pays/refunds `20/80` at 33% and `40/60` at 67%, with exact floor and conservation. |
| CRT-06 | **PASS** | Fresh real-model artifact has all 15 votes: medians precise-good 100, precise-bad 0, vague-good-faith 90, vague-no-effort 5, injected rebuttal 10. Ambiguity favored good-faith work; unrelated/non-delivery did not; reasoning cites case evidence. |
| CRT-07 | **PASS** | Literal subprocess runs production `RulingOrchestrator`/`DisputeDbClient`, signals deterministic pre-decision or post-vote barrier, then receives SIGKILL. A fresh recovery path reclaims the durable Gateway lease and produces one ruling and one settlement; post-vote recovery calls no judge. This component-process cut proves durable Court semantics; ECO-08 separately proves full owned Court service stop/start. |
| UIO-01 | **PASS** | Two browser pages accept different bids from lifecycle version 3. One explicit 60-credit bid wins, stale tab receives typed conflict, refresh/dismiss removes stale controls, and both show frozen worker/price. |
| UIO-02 | **PASS** | Posting-journal browser suite loses responses, navigates/reloads, recovers saved immutable terms, and proves repeated submit/status checks never allocate a second publication. |
| UIO-03 | **PASS** | Typed Court outage test plus native saved-intent browser suite preserve the exact operation through dependency failure and explain pending/error recovery; malformed 5xx bodies remain sanitized. |
| UIO-04 | **PASS** | 315-case browser run exercises loading, unavailable, empty, pending, action-required, success, conflict, and terminal task/agent/observatory states with text/roles as well as color. New pending notices use neutral `role=status`; failures use `role=alert`. |
| UIO-05 | **PASS** | Cross-page tests plus eight-phase adversarial test exercise direct links, back/forward, reload, and 720×450 (200% equivalent) for every lifecycle phase; orientation/status/controls stay visible without horizontal overflow. |
| UIO-06 | **PASS** | Deterministic delayed-network barriers rapidly activate nine mutation types. Every case emits one POST, disables all conflicting controls, moves focus to a neutral pending status, then moves focus/semantics to the failure recovery endpoint. Approve-then-dispute and two-different-bid cases prove a later click cannot replay the first intent; upload/submit cases prove cross-action gating, and failure restores each control's prior eligibility. Posting journal separately pins repeated Post behavior. |
| UIO-07 | **PASS** | Frozen-submission UI explicitly says files cannot be replaced, poster may approve/dispute, and the platform does not verify worker suitability. Unsupported correction controls stay absent; the copy discloses the current limitation without promising the unratified IDN-06 admission policy. |
| ECO-08 | **PASS** | One disposable owned stack reproduced the chain: math parser froze one-byte `7`; Court outage produced an unknown timeout and one pending operation; exact replay created one claim; UI API exposed worker deadline; honest rebuttal enabled three deterministic 0% votes; final balances poster 1000/worker 0, payment 0/refund 100, one disputed and one ruled event, exact output bytes. Browser rendering and real-model quality are proven separately by DSP-05/UIO and CRT-06 evidence. |

## Delegated compound sequence

| ID | Disposition | Whole-sequence oracle and evidence |
| --- | --- | --- |
| ECO-05 | **PASS** | One owned stack combined lost publication acknowledgment with exact status/replay, concurrent equal bids and stale competing acceptance, same-key worker process restart, duplicate idempotent upload, Court outage and exact dispute replay, honest rebuttal, a deterministic second-judge timeout with no partial votes, full Court service stop/restart, and ruling retry. It ended with one task, one selected 60-credit worker, one asset, one claim, one three-vote ruling, payment 0/refund 100, and truthful final UI. The reusable pytest wrapper passed in 24.52s. |

## Product findings and limits

- The only assigned confirmed product gap is IDN-06: there is no hard capability
  admission contract. UIO-07 discloses that limitation without inventing policy.
- The historical live incident independently converged to the same 0/100 outcome,
  but it is excluded from campaign acceptance because it used ordinary runtime
  data.
- The real-model corpus required about 18 minutes and one unavailable-provider
  negative probe; it was not rerolled or cherry-picked.
- Parent owns GOAL.md disposition updates, canonical ticket closure after final
  root CI, integration review, commit, and publication.
