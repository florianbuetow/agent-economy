# Live UI journey verification — 2026-11-04

## Current result: final frozen source

The latest poster and worker browser journeys both **passed with exit 0** after
all final UI changes, including the narrow home layout repair, accessible posting
errors, and output filtering that preserves role-unspecified files. Every UI
HTML/JavaScript file and shared CSS has matching SHA-256 hashes in
`source-release-start.sha256` and `source-release-end.sha256` across both runs.
This section supersedes earlier source-window caveats below.

- `poster-release.log`: visible posting/acceptance/review/approval, exact 44-byte
  input and 9-byte output, accepted price 60, approved, conserved -60/+60 balances,
  and no incorrect court attribution in the payment status.
- `worker-release.log`: visible Jobs navigation/bid/input download/output upload/
  preview/submission, exact 33-byte input and 9-byte output, approved after the
  independent poster action, conserved +60/-60 balances, zero page errors.
- The retained five PNG screenshots now come from these final frozen-source runs.
  `poster.py` and `worker.py` remain the exact harnesses used.
- Existing recovery proof remains `recovery-final.log`, exit 0, 1 passed in
  37.69s. Its action logic is unchanged; the final root gate also reruns that test.
  Recovery was not redundantly rerun in this final bounded pass.

Commands were the poster/worker runtime commands below with artifacts roots
`/tmp/ate-ui-journey-validation/poster-release-runtime` and
`/tmp/ate-ui-journey-validation/worker-release-runtime`, respectively. Both ran
from repository root, retained the normal PATH and cleaned up their owned
services. No product files were changed by this verification lane. There are no
unresolved live-journey findings. Final root CI is reported by the owning lane.

## Historical verification sequence

Independent verification lane; no product source edits. Tested working tree based
on `5d4a35de7774a0ba548bde36dd6d7bfb9ee742e7` with the UI work in progress.
`source-start.sha256` and `source-end.sha256` record relevant source boundaries.
The UI owner continued orientation wording/identity guards and responsive layout
adjustments during this window; these passes prove the exercised actions, not a
frozen final visual tree. Final integrated CI remains the owning lane's gate.

## Results

| Check | Exit | Evidence |
| --- | --- | --- |
| Poster browser journey | 0 | `poster.log`, `poster-approved.png` |
| Worker browser journey | 0 | `worker.log`, `worker-accepted.png`, `worker-submitted.png` |
| Existing lost-response/two-tab conservation regression | 0 | `recovery.log`: 1 passed in 28.76s |

Poster: home → visible Post link → publish job with exact 44-byte input → visible
posting result link → accept independently signed 60-credit bid → preview exact
9-byte worker output → approve. Authoritative state was `approved`, accepted
price 60, operator delta -60, worker delta +60, and aggregate balance unchanged.
Independent worker actions used the existing SDK as allowed by the brief.

Worker: home → visible Jobs link → task link → browser binding bid → independent
poster accepts → browser downloads exact 33-byte input → browser uploads and
previews exact 9-byte output → browser submits. No recovery control remained and
no JavaScript page errors fired. Independent poster downloaded the exact output
and approved; authoritative state was `approved`, accepted price 60, operator
delta +60, poster delta -60, and aggregate balance unchanged.

Recovery: unchanged existing test loses the committed publication response,
reloads and retries the same saved operation from two tabs, then confirms status.
Its assertions preserve one task, one escrow, the same request bodies and
completion markers, and the original conserved balance change.

These are automated agent browser walkthroughs, not recruited user testing.
Screenshots retain the browser's final scroll position; they are interaction
artifacts, not substitutes for the separate responsive/keyboard/contrast review.
The dispute/rebuttal branch was not rerun here; existing fixture/regression
coverage remains the separate evidence for unchanged Court behavior.

## Commands

All commands used the normal PATH. Only `UV_CACHE_DIR=/tmp/ate-ui-uv-cache` was
set to permit sandbox cache writes. Local socket/browser permissions were granted
for the isolated harness. Each runtime's normal `finally` cleanup stopped only
its own processes; ordinary services and runtime data were untouched.

Working directory: repository root.

```sh
UV_CACHE_DIR=/tmp/ate-ui-uv-cache uv run --directory services/db-gateway /Users/flo/Developer/github/agent-economy/scripts/isolated_test_runtime.py --artifacts-root /tmp/ate-ui-journey-validation/poster-runtime --provision -- uv run --project services/ui /tmp/ate-ui-journey-validation/poster.py
UV_CACHE_DIR=/tmp/ate-ui-uv-cache uv run --directory services/db-gateway /Users/flo/Developer/github/agent-economy/scripts/isolated_test_runtime.py --artifacts-root /tmp/ate-ui-journey-validation/worker-runtime --provision -- uv run --project services/ui /tmp/ate-ui-journey-validation/worker.py
```

Working directory: `services/ui`.

```sh
UV_CACHE_DIR=/tmp/ate-ui-uv-cache uv run pytest tests/e2e/test_mp05_live_posting_conservation.py -q --basetemp=/tmp/ate-ui-journey-validation/recovery
```

The retained `poster.py` and `worker.py` are exact copies of the successful
harnesses. To reproduce elsewhere, pass their absolute paths to the same runtime
commands. Poster currently writes its disposable input to
`/tmp/agent-economy-goal-2026-11-04/browser-poster-input.txt`; create that directory
and `/tmp/ate-ui-journey-validation` first. All three screenshot paths are also
explicitly under `/tmp/ate-ui-journey-validation`.

## Setup failures resolved

- Sandbox socket reservation was denied; authorized isolated execution resolved it.
- The old `--project agents` harness command lacked Playwright. The existing UI
  environment supplies both Playwright and the SDK; no dependency was added.
- The first expanded worker harness looked for task links on home. It was corrected
  to follow the visible Jobs navigation before selecting the task. This was a
  harness navigation error, not an application defect.

## Frozen-source rerun

After the input/output distinction repair and independent review fixes, all three
commands were rerun with `poster-final-runtime`, `worker-final-runtime` and
`recovery-final` artifact locations, respectively. All exited 0; recovery reported
**1 passed in 37.69s**. `source-final-start.sha256` and
`source-final-end.sha256` cover every UI HTML/JavaScript file and shared CSS and
are byte-identical. Logs are `poster-final.log`, `worker-final.log`, and
`recovery-final.log`. Exact byte counts, ±60 balance deltas and approved final
states match the earlier passing runs. Updated screenshots show the input no
longer counted as a delivered output.

The additional `poster-outcome-orientation.png` revealed a separate presentation
error: an ordinary approved task with budget 100 and accepted price 60 described
its split escrow as a court ruling. There was no dispute. This was escalated to
the UI owner for a copy correction and a bounded poster rerun. The economic
assertions passed; the incorrect copy is not accepted as verified UX.

## Payment wording correction verified

The UI owner corrected split-payment wording: a court attribution now requires
an actual dispute ruling; ordinary approval explains worker payment and refund
of the remainder. The phase heading copy also changed to “What happens now”.
No action or recovery semantics changed. The bounded poster journey was rerun
with an additional assertion that `.payment-status` does not contain “court”
on ordinary approval. It passed with **exit 0**, exact 44-byte input and 9-byte
output, accepted price 60, final state `approved`, and conserved -60/+60 balances.

Command: the poster command above, with artifacts root
`/tmp/ate-ui-journey-validation/poster-payment-runtime` and output
`poster-payment.log`. `source-payment-start.sha256` and
`source-payment-end.sha256` are byte-identical across all UI HTML/JS/shared CSS.
The retained `poster.py`, `poster-approved.png`, and
`poster-outcome-orientation.png` now represent this latest passing run. The
worker and recovery passes remain applicable to their unchanged action paths.
No unresolved live-journey finding remains in this verification lane.
