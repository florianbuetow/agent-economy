# A3 — Runtime, human UI, ops (G13–G20)

Audited at `802fe37` (branch `refactor`). Code reading plus one throwaway probe script (scratchpad, no repo edits).

## Summary table

| G-id | Status | One-line |
|---|---|---|
| G13 human poster UI | PARTIAL | Post, real sealed bid amounts, accept, approve, dispute, retained-file download exist; no input attach, cancel, feedback, balance/funding readiness or insufficient-funds guidance. |
| G14 human worker UI | MISSING | No UI route or JS for operator bid, input download as worker, output upload, submit, rebuttal; only a read-only payment badge exists. |
| G15 SDK / external onboarding | PARTIAL | SDK exposes the full method set (bid/upload/submit/rebut/cancel/feedback/posting); no own-key onboarding doc and no second independent agent earn-then-post proof. |
| G16 agent runtime durability | PARTIAL | Mutation intents and feeder postings are journaled and replayed; worker job phase, solution, rebuttal and history are in memory, and an accepted job is never resumed after restart or poll timeout. |
| G17 SSE/metrics/clients | PARTIAL | SSE reconnect replays duplicates; GDP sums posted reward; clients turn malformed 2xx bodies into `{}`/`[]`/`0`; Task Board uses a sync httpx client in async paths. |
| G18 non-math workload | MISSING | Only `math_worker`; no classification worker/task, no outcome-based adaptation (worker history is in-memory and only logged). |
| G19 ops | PARTIAL | Tiered `start-all` with readiness gates, `status`, `stop-all`, `provision`; no cleanup of already-started tiers on failure; backup/restore exists only for the isolated disposable runtime with no `just` recipe; startup-minting docs already corrected. |
| G20 docs vs shipped | PARTIAL | Task Board and UI API specs match routes; README UI paragraph undercounts proxy routes; no release proof run. |
| Auto-recovery | BUG | Gateway outage permanently kills Task Board's deadline sweep and posting recovery loops; a Task Board restart kills the whole task-feeder process. |

## Evidence per gap

### G13 human poster UI

Proxy routes in `services/ui/src/ui_service/routers/proxy.py`:
- Present: browser-config `:23`, identity `:33`, asset download `:40`, posting prepare/submit/status `:55,:69,:79`, bid list `:89`, accept `:104`, approve `:120`, dispute `:134`.
- Absent: cancel, feedback, asset upload (inputs attach), balance/account. Task Board has `POST /tasks/{id}/cancel` and the SDK has `cancel_task`, `submit_feedback`, `upload_asset`, `get_balance`, so only UI wiring is missing.
- Bid amounts are real: `task.js:204` renders `b.amount` from the poster-authorized bid list (`proxy.py:89-101`).
- Review/download: `task.js:375` builds `/api/proxy/tasks/{id}/assets/{asset_id}` links. Approve/dispute buttons `task.js:264-290`, wired at `task.js:662-667`.
- No balance or insufficient-funds handling: no `balance`/`insufficient` reference in `task-posting.js` or `task.js`.
- Contract card shows `task.reward` as "Reward" (`task.js:225`), never the accepted bid amount.

### G14 human worker UI

No bid, upload, submit or rebuttal endpoint under `/api/proxy` (`proxy.py` full listing above). `task.js:778` only wires `.btn-accept-bid, #btn-approve, #btn-dispute-show, #btn-submit-dispute`. Rebuttal is display-only (`task.js:277-281`). Payment status badge exists read-only (`task.js:65-94`). README `:39` confirms "bidding, submission, and rebuttal stay worker-side actions".

### G15 SDK

`libs/service-auth/src/service_auth/agent.py:29` `BaseAgent` exposes: accept_bid, approve_task, cancel_task, create_account, dispute_task, download_asset, file_claim, get_balance, get_task_posting, list_bids, submit_bid, submit_deliverable, submit_feedback, submit_rebuttal, submit_worker_rebuttal, submit_task_posting, upload_asset, and others. No onboarding guide for an externally generated key was found under `docs/`. No test runs a second independent agent through earn-then-post.

### G16 agent runtime

Persisted:
- Task-command intents per `(task_id, slot)` in `FileTaskIntentStore`; `replay_unfinished` re-sends only `pending`/`blocked` slots (`agents/src/base_agent/runtime_task_commands.py:180-191`, `:194`).
- Feeder posting records; review map rebuilt from them (`agents/src/task_feeder/loop.py:79-84`, `__main__.py:139-141`). Feeder review therefore resumes after restart, and the review operation id is deterministic per lifecycle version (`task_feeder/review.py:77`).

In memory only:
- Worker phase machine and solution: locals inside `MathWorkerLoop._cycle` (`agents/src/math_worker/loop.py:106-192`).
- `AgentHistory()` (`loop.py:71`), used only in the shutdown log (`loop.py:92-96`).
- Rebuttal is a direct SDK call, not an intent (`loop.py:366-374`).

Resume after restart: no. `_cycle` calls `replay_unfinished` and then `_phase_scanning`, which lists only `status="open"` tasks (`loop.py:108-111`, `:206`). Nothing lists `status="accepted", worker_id=me` or `submitted`/`disputed` tasks owned by the worker.

Feeder price and quality:
- Winner rule is lowest amount with tie-break on delivery quality (`task_feeder/acceptance.py:73-93`).
- The quality score is computed client-side from raw feedback records (`acceptance.py:57-70`, `:143-150`), not a service-owned score.
- Cross-reference for the money auditor: accept freezes `reward: task["reward"]` alongside `bid_amount` (`services/task-board/src/task_board_service/services/task_bidding.py:300-306`), and settlement reads `accepted_reward` (`services/task-board/src/task_board_service/services/task_operations.py:78`). The accepted bid price appears to have no payout effect.

### G17 SSE, metrics, clients

- SSE reconnect duplicates: server resumes only from the `?last_event_id=` query (`services/ui/src/ui_service/routers/events.py:79-90`) and ignores the `Last-Event-ID` header. The client builds the URL once (`services/ui/data/web/assets/shared.js:325-327`), so native auto-reconnect replays from the original cursor. `observatory.js:89-98` `addFeedEvent` does not dedupe by id. While paused, events are dropped and not refetched (`observatory.js:90`).
- GDP uses posted `reward` for approved tasks and `reward * worker_pct / 100` for ruled tasks (`services/ui/src/ui_service/services/metrics.py:39-52`). This is consistent with current settlement on `accepted_reward`, but not with any bid-price payout.
- Fake-success fallbacks in clients:
  - `libs/service-clients/src/service_clients/base.py:118-128`: a non-JSON or non-object 2xx body becomes `{}`.
  - `gateway.py:155-157`: `list_agents` then returns `[]`.
  - `gateway.py:164`: `count_agents` returns `0` on a missing key.
  - `gateway.py:139`, `:220`: register and other writes return `{}` as success.
  - `services/task-board/src/task_board_service/services/task_db_client.py:159-166`: `_json` swallows every exception to `{}`.
  - `services/ui/src/ui_service/services/public_events.py:51-58`: malformed payloads become `{}`. This is lower risk because it is display only.
- Swallowed errors in the worker: `_get_balance` returns `0` on any error (`math_worker/loop.py:483-490`), and the rebuttal failure is logged and ignored (`loop.py:371-372`).
- Blocking I/O in async paths:
  - Task Board's `TaskDbClient` is a sync `httpx.Client` (`task_db_client.py:127`).
  - It is called directly from async code such as `task_creation.py:269` and `deadline_evaluator.py:278`. Only 2 `to_thread` uses exist in Task Board, against about 47 direct store calls.
  - UI `spa_fallback` uses sync `read_text`/`read_bytes` for static files (`services/ui/src/ui_service/app.py:96,136,144`). This is minor.

### G18 non-math workload

`agents/src` contains only base_agent, math_worker, task_feeder, and the CLIs. `grep -i classif` hits only error-classification code. No adaptation logic exists.

### G19 ops

- `justfile:254-372` `start-all`: tiered, health-gated with 60 x 0.3 s. Identity gets an extra `/agents` readiness gate. On failure it `exit 1`s and leaves already-launched tiers running, with no cleanup. `start_unless_healthy` reuses any healthy process on the port, possibly a stale build. Background `just _launch-*` output is not redirected to a log.
- `stop-all` (`:417-438`) and `status` (`:556-621`) count failures and exit nonzero. `provision` (`:515-530`) is explicit and idempotent.
- Backup/restore: `db_gateway_service.recovery_cli backup create|verify|restore` exists (`docs/operations/recovery-artifacts.md:60-110`). It is scoped to the disposable `scripts/isolated_test_runtime.py` runtime, and there is no `just backup`/`restore` for the normal `data/` stack.
- Stale startup-minting claims: none found. `ui-service-specs.md:51,333`, the `justfile:511-514` comment and `services/ui/config.yaml:66` all state that startup does not mint. FEATURE-COMPLETENESS G19's "stale minting" note is itself stale.

### G20 docs vs shipped

- `docs/specifications/service-api/task-board-service-specs.md` endpoint headings match the Task Board routers one to one.
- `ui-service-specs.md:148-157` matches `proxy.py`.
- `README.md:39` says the UI "proxies exactly four task-lifecycle actions". The UI also proxies bid listing, asset download and the posting prepare/status reconcile routes. This is a minor stale doc.
- `observatory-service-specs.md` is clearly marked SUPERSEDED, which is fine.

## Logic bugs / robustness problems

1. **Deadline sweep dies permanently on a gateway outage.** In `services/task-board/src/task_board_service/services/deadline_evaluator.py:275-284`, `self._store.list_tasks(...)` sits outside the per-task `try`. A raw `httpx.ConnectError` from the sync client (`task_db_client.py:154`) ends the task. There is no done-callback or restart in `core/lifespan.py:145-150`. Verified with a probe that ran `run_periodic_sweep` against a store raising `ConnectError`: the task finished with `ConnectError` after 1 call. Scenario: db-gateway restarts, after which review timeouts, auto-approve and dispute-to-Court triggers stop until Task Board restarts.
2. **Posting recovery dies the same way.** `services/task-board/src/task_board_service/services/posting_recovery.py:115-122` catches only `ServiceError` and `PlatformHttpError`. `run_batch` (`:66`) lets a raw `httpx` transport error escape. `financial_effect_recovery.py:317-326` catches `Exception` and survives.
3. **A Task Board restart kills the task feeder.** `agents/src/task_feeder/__main__.py:94-98` runs the loops in an `asyncio.TaskGroup`. `AcceptanceLoop.run` (`acceptance.py:234-244`) and `ReviewLoop.run` (`review.py:118-121`) call `replay_unfinished`/`list_tasks` without a try. One `httpx.ConnectError` cancels the group and the process exits.
4. **A worker abandons accepted jobs.** `math_worker/loop.py:265-301` stops polling after `max_poll_attempts` while the binding bid is still pending. Any exception mid-cycle (`loop.py:86-89`) or a process restart loses the job. The next cycle scans only open tasks, so a later acceptance is never executed and the poster's escrow waits for the execution deadline.
5. **Solve failure leaves an accepted job unexecuted.** `loop.py:145-156` records ERROR and returns without submitting or retrying.
6. **Upload is outside the journal.** `loop.py:326-329` uploads inside `prepare()` before the intent is created. A crash between upload and `store.create` re-uploads on retry, which duplicates assets. The feeder then reviews `assets[-1]` (`review.py:104`).
7. **Wrong ruling payout.** `loop.py:420` reads `worker_payout`, which `TaskResponse` does not have (`services/task-board/src/task_board_service/schemas.py`, which has `worker_pct`). It falls back to the full reward, so every ruling is recorded as won with full payout. Disputed records use `bid_amount=0` (`loop.py:385`).
8. **SSE duplicate replay on reconnect** and event loss while paused (see G17).
9. **Fake-success client parsing** turns contract violations into empty successes (see G17).
10. **Sync gateway client on the Task Board event loop.** Every slow gateway call stalls all Task Board requests and loops.

## Recommended work items (ordered)

1. **Supervise Task Board background loops.** Catch transport errors around `list_tasks`/`run_batch` and add a done-callback that logs and restarts. Done when a test with the gateway down for N intervals shows the sweep and posting recovery resume after it returns.
2. **Make the feeder survive dependency restarts.** Wrap each loop iteration and back off on `httpx.HTTPError`/`ServiceError`. Done when a test with Task Board raising `ConnectError` once shows all three feeder loops keep running.
3. **Resume assigned worker jobs.** On each cycle, list `accepted`/`submitted`/`disputed` tasks where `worker_id == me` before scanning open tasks, and persist the solution and upload state. Done when a worker killed after acceptance, restarted, submits and is paid.
4. **Keep polling for acceptance until the task leaves `open`,** rather than stopping after a fixed attempt count. Done when a late acceptance after the poll budget is still executed.
5. **Journal rebuttal as an intent and fix ruling payout** to use `worker_pct`. Done when restart-after-dispute files exactly one rebuttal and history records the correct payout.
6. **Use strict client parsing.** A 2xx response with a non-object or missing-field body raises a typed contract error in `service_clients/base.py`, `gateway.py` and `task_db_client._json`. Done when unit tests show malformed 2xx responses rejected.
7. **Move Task Board to an async gateway client** or wrap every store call in `to_thread`. Done when a slow-gateway test shows `/health` stays responsive during a slow store call.
8. **Fix SSE resume.** Honor the `Last-Event-ID` header server-side, or reconnect client-side with the current cursor, and dedupe by `event_id`. Done when a forced reconnect produces no duplicate feed rows.
9. **Add the missing human poster controls (G13):** inputs upload, cancel, feedback, a balance display and insufficient-funds messaging. Done when a UI e2e test posts with an attachment, cancels one task and leaves feedback on another.
10. **Add operator-as-worker controls (G14):** bid, input download, output upload, submit and rebuttal. Done when the operator completes a feeder task end to end through the UI.
11. **Clean up partial starts.** On a tier failure, `start-all` stops the processes it launched and writes per-service logs. Done when an injected Court start failure leaves no listening ports.
12. **Add `just backup`/`just restore`** for the normal `data/` stack, or document that only the isolated runtime is supported. Done when a restore-then-`start-all` resumes a pending task.
13. **Feeder quality score.** Use a Reputation-owned score endpoint instead of client-side averaging. Done when acceptance reads a single service score.
14. **Non-math workload (G18):** classification tasks and worker with persisted adaptation. Done when a measured multi-round report includes sample counts.
15. **Update README:** fix the `README.md:39` proxy description and drop the stale-minting note from FEATURE-COMPLETENESS G19.
