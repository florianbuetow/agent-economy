# Completion acceptance evidence — 2026-11-04

> Follow-up: the owner-selected Qwen3.6 profile subsequently passed all five
> panel cases, and a fresh root gate passed. See
> [the completed model evaluation](../2026-11-04-qwen36-judge-evaluation.md).
> The small-model limitations below describe this earlier verification snapshot.

Baseline: `155a63f`. This record supports GOAL.md; canonical ticket state remains
in `openspec/specs/completion-backlog/spec.md`. The code gate passed; the overall goal remains unmet because real judge quality failed.

| Goal requirement | Current evidence | Status / next check |
| --- | --- | --- |
| 1. Task Board responsiveness and strict reads | Failing-first evidence; held-request health, cancellation/context, financial replay and read checks pass; independent review approved | Verified; full root CI passed |
| 2. Remote onboarding | Guide corrected against actual CLI/config; independent public-agent input/earn-then-post journey passes locally | Local proof; no second-machine requirement |
| 3. Current judge panel | `../2026-11-04-judge-evaluation.md` and raw JSON; current Qwen3-0.6B failed vague-good-faith and injection cases | Unmet: bounded general repairs failed; requested model and original prompt retained |
| 4. External contract and independent journey | Example now handles inputs; two-agent earn-then-post E2E and protocol checks pass; independent review approved | Verified with full root CI; new event iterator excluded |
| 5a. Classification adaptation | Real scanner defect and prompt ID defect repaired; final isolated run:8tasks,13bids,4approved,3ruled,1expired, journal-derived adaptation for both workers | Measured limited sample retained; no general superiority claim |
| 5b. Telemetry | Real Task Board→Gateway request shares trace ID in structured logs; all seven isolated health endpoints ok | Local correlation verified; no collector/Grafana installation claimed or required |
| 5c. Restore | Actual restore permission defect repaired; restored submitted job retains files/balances and settles once on replay; six checks pass | Verified with full root CI; independent review approved |
| 5d. Public journeys B09–B12, B14, B16 | Real browser poster-with-input and worker journeys, independent earn-then-post, trace correlation and pending-job restore pass; existing restart/SSE suites remain in root gate | Public checks and complete integrated gate passed |
| 6. Release and tracker reconciliation | Existing tracker remains open | Root gate and reviews passed; judge-readiness tickets remain open |

## Strict Gateway reads (T-148 / T-149)

Command, from `services/task-board`:

```text
uv run pytest tests/unit/test_gateway_read_contracts.py -q --tb=short
```

Red run before production edits: exit 1, 24 failed, 1 passed. See
`gateway-reads-red.log.gz`. Malformed JSON/non-object responses became empty successes;
missing or invalid asset lists were accepted; boolean, negative, string and
fractional counts were coerced, and null counts raised incidental TypeError.
The legitimate empty/zero case passed.

An attempted repeat was blocked by uv cache sandbox access (exit 2); this was an
environment failure, not application evidence. The retained log comes from the
successful execution of the regression with cache access, still before edits.

Green focused command:

```text
uv run pytest tests/unit/test_gateway_read_contracts.py tests/unit/test_mp09_asset_storage_projection.py tests/unit/test_mp07_gateway_store_parity.py -q --tb=short
```

Exit 0: **43 passed in 7.86 seconds**. This covers the new read regressions plus
existing real Gateway storage-path and lifecycle parity tests. It does not prove
async responsiveness or the entire release gate. Subsequent edits require the
affected checks to run again.

## Review corrections and scope correction

Owner correction: no second physical machine, SSH, or new agent event-stream API
is required. The unaccepted event-feed draft and its tests were removed; current
polling remains supported. Local independent-client checks establish application
behavior; physical cross-machine connectivity is not a release gate.

Independent strict-read review found an unintended nullable-hash contract change,
missing ordinary task fields, and raw dependency error bodies. Before correction,
`uv run --directory services/task-board pytest tests/unit/test_gateway_read_contracts.py -q --tb=short`
exited 1 with **5 failed, 26 passed** (`gateway-review-red.log.gz`).

The repair preserves nullable asset metadata (downloads retain their existing
integrity check), validates required ordinary task fields, and uses the existing
structured dependency-error boundary for those reads.

Verification from `services/task-board`:

```text
uv run pytest tests/unit/test_gateway_read_contracts.py tests/unit/test_mp09_asset_storage_projection.py tests/unit/test_mp07_gateway_store_parity.py tests/unit/test_mp07_task_operation_client.py -q --tb=short
```

Exit 0: **53 passed in 8.47 seconds**. Ruff check and format check passed for the
two strict-read production files and the new regression file.

An earlier broad investigative `just test-unit` was stopped with SIGINT after
about eight minutes at 19% progress to avoid exceeding the ten-minute investigation
limit. Exit 2 is an interrupted run, not a passing gate. Its output remains at
`/tmp/agent-economy-goal-2026-11-04/task-board-unit.log`. The final integrated root
CI gate is still required and will be allowed to finish.

The repository no-defaults rule requires every executor choice to be explicit.
The Task Board operation helpers therefore require `store_executor` at every
internal and test call. Existing tests were reconciled only by passing
`store_executor=None`; their inputs, assertions, and observable behavior were
not changed. No compatibility wrapper or default parameter remains.

## Current-format operational restore (T-150 / T-151)

Added one real public-API continuation check using the existing isolated runtime:
create and submit a job, stop only that runtime's services, archive current files
with `scripts.backup`, restore them, make the original files unavailable, restart,
download the same bytes, and approve the same operation twice. Assertions require
worker payment90, unused-budget refund10, and one payout transaction.

First run exited1 in8.82 seconds: Task Board startup rejected the restored asset
root because it was no longer mode0700. `restore-startup-red.log.gz` retains the
startup failure. Python's safe tar data filter discards directory permissions;
the repair reapplies archived owner-only permissions after safe extraction.

Verification from repository root:

```text
PYTHONPATH="$PWD:$PWD/tests" uv run --directory services/db-gateway pytest "$PWD/tests/runtime/test_current_backup_resume.py" "$PWD/tests/runtime/test_backup.py" -q --tb=short
```

Exit0: **6 passed in13.01 seconds**. One existing pytest marker warning was emitted
for `test_backup.py` under the Gateway test configuration. This is public pending
submission recovery plus existing archive tests, not historical-data migration.
No ordinary runtime was stopped or modified. Final integrated CI remains pending.

## Protocol documentation

Corrected the existing protocol document for protocol-2 input publication,
idempotency behavior, governed submission and receipt-based settlement. No routes
were added. Independent review corrected two raw-HTTP/SDK distinctions: input
idempotency headers are optional on HTTP but required by SDK; bid read auth is
required while open and optional afterward. Existing protocol documentation tests:
**18 passed**. Spell check passed.

## Responsiveness and live trace correlation

The async repair's focused tests hold a real `/tasks` router's Gateway call while
`/health` must complete before that call is released. Separate checks cover sweep
progress, cancellation draining, and ContextVar propagation through both default
and explicit executors. The executor boundary initially lost context; the live
probe retained in `telemetry-local-red.json` showed unrelated Gateway trace IDs.
The fix copies the request context into the worker thread.

Rerun command from repository root:

```text
uv run --project services/db-gateway /tmp/agent-economy-goal-2026-11-04/verify_log_correlation.py
```

Exit0. `telemetry-local.json` records all seven isolated services healthy and the
same trace ID on the Task Board and DB Gateway structured log entries for a real
HTTP request. `verify_log_correlation.py` retains the small reproducible probe.
This proves local structured-log correlation; it does not claim a Grafana viewer
or OTLP collector was installed or verified. No new deployment was introduced.

Async worker verification (parent root gate still outstanding): responsiveness
4passed; concurrent duplicate-bid/deadline plus responsiveness6passed; relevant
lifecycle/assets/inputs84passed, then affected governed/input26passed after
refinement. `just code-style`, `just code-typecheck`, `just code-lspchecks` passed.
An exploratory full unit run was stopped after22passes with no failures; this is
not a replacement for the final gate.

## Independent participant example

Input helper regression: 3failed before implementation, 3passed after. Existing
example plus new input economic-cycle E2E: 2passed against an isolated stack. Two
ordinary identities are used; only the first gets a platform grant. The second
earns200 then posts an input-bearing job for150; the first completes it. Final
balances450+50 conserve the original500 grant. Manifest bytes/hash are verified,
and poster review considers output files rather than concatenating inputs.
Independent review approved the example and backup repair; its own rerun reported
3input unit checks and6backup/restore checks passing.

## Measured classification result

The final frozen-source run is recorded in
`../2026-11-04-classification-runtime-evaluation.md`; the exact-ID prompt diagnosis
and raw model responses are in `../2026-11-04-classification-llm-prompt-diagnostic.md`.
The discovery bug's unit regression failed before repair, then passed; the prompt
ID regression likewise failed first. The full classification unit set now has28
passing tests. The final real run finished in148.037 seconds:8tasks,13bids,
13finished journal records,4approved,3ruled,1expired. The Qwen worker had one
selected job, directly approved and paid21. Keyword outcomes comprised three
direct approvals and three positive partial mock-Court rulings, not six approvals.
Both workers' recorded outcomes changed per-type bids. The sample is small and
uses deterministic mock Court for the economic mechanics; it does not repair or
replace the separately failing real Court judge evaluation.

## Root attempt 2 findings

The run was interrupted for a separately reproduced live UI-worker failure; its
captured unit output also exposed a real deadline regression (500 or internal
transition_pending instead of409 cutoff_reached). Deadline and user transitions
now share the same ordered mutation executor, and authorized open-task acceptance
runs the existing deterministic deadline evaluator before claiming acceptance.
Exact cutoff, acceptance/expiry races and related bid checks pass; final combined
set19passed in55.09seconds. Style, mypy, Pyright and Semgrep pass. Independent
review is pending before the next integrated gate.

An artifact-maintenance cancellation test separately failed because a raw
asyncio.to_thread call returned cancellation while its worker continued against
an already-closed HTTP client. Maintenance now uses the same draining worker
boundary. Existing posting/financial recovery helpers already drain their workers
and were left intact.

The live UI proxy rejected a valid governed protocol-2 submission, despite the
Task Board committing it. Backend version validation is being reconciled with
that current contract, including operator/manifest binding. The same hardcoded
version check also exists in browser task.js; that consumer is being repaired
and browser-tested before any complete UI-worker claim.

## Final focused UI worker verification before root attempt 3

`ui-worker-live.md` retains the real proxy502, browser missing-submit-button, and
wrong-public-asset-ID404 failures before their repairs. The final task projection
uses canonical public asset IDs, includes ready/archived input/output metadata,
and excludes private storage keys. Backend and browser bind protocol-2 results to
the saved/configured operator and frozen manifest; other commands remain protocol1.

The real Chromium task page fetched the asset ID from the UI drilldown, clicked
its rendered View-as-text control, displayed the exact9output bytes, clicked
Submit, displayed SUBMITTED without recovery controls, and reached approved after
the independent poster reviewed it. Acceptedprice60; operator+60/poster-60;
combined balance conserved. All owned services were cleaned up.

Independent parent command, from services/ui:

```text
uv run pytest tests/integration/test_tasks.py tests/integration/test_governed_artifact_projection.py tests/integration/test_protocol2_submit_proxy.py tests/unit/test_protocol2_submit_browser.py -q --tb=short
```

Exit0: **26passed in13.20seconds**. The worker's broader focused set35passed;
UI style, mypy, Pyright and Semgrep passed. Fullroot attempt3 remains the required
integrated gate, not replaced by these checks.

The historical separate-host/network-namespace wording in T-140 was reconciled
with the owner's explicit 2026-11-04 correction: independent keys and public URLs
are required; a second physical host, SSH and a separate network namespace are not.
No new environment requirement is introduced by the local verification procedure.

During root attempt3's source freeze, the explicit browser poster path also passed
without source changes: actual form input upload44bytes, independent worker with
zero starting balance downloads the input, bids60, operator accepts in Chromium,
worker submits9output bytes, browser previews the exact file using its own returned
asset ID and approves. Final approved, operator-60/worker+60, conserved funds.
The first temporary probe confused UI label ACTIVE with backend status accepted;
that was a probe assertion correction, not a product failure or changed behavior.
All owned processes were cleaned. Full details are in `ui-worker-live.md`.

## Final integrated gate and remaining limit

The complete root `just ci-quiet` exited **0** at **2026-11-04 04:36:54 UTC**.
`root-ci-result.json` records the terminal result and confirms all **1,497**
recorded source/config/test inputs were unchanged. Full output is retained in
`root-ci-r6.log.gz`; the exit status is in `root-ci-r6.exit`.

All service gates passed, along with 503 per-service integration checks,
1,009 agent checks (three existing optional provider-key skips), 334 tool checks,
495 cross-service/runtime checks, 319 browser checks, and 115 agent E2E checks.

The requested Qwen3-0.6B Court profile still fails the predeclared judgment cases.
The original prompt is retained after unsuccessful bounded general repairs;
no model substitution, corpus weakening, or payout override was introduced.
The overall goal is therefore **not achieved**. The software repairs and public
journeys are verified; the real judge-readiness criterion remains unmet.

Historical tickets are closed only where this pass supplies their proof and the
root gate. Broader old tickets are not blanket-closed from this CI result; in
particular, the Task Board slice of MP-20 is verified without claiming that every
shared-library client contract has been repaired or audited.
