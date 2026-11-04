# Agent Economy completion result — 2026-11-04

> Later owner update: Court now uses `qwen/qwen3.6-35b-a3b`; all five panel cases
> and a fresh root CI run passed. Publication is pending the permitted Git window.
> The small-model findings below describe the earlier completion snapshot. See
> [the new model evaluation](2026-11-04-qwen36-judge-evaluation.md) for current results.

At the earlier checkpoint, software repairs passed root CI but the Qwen3-0.6B
Court profile failed judgment-quality cases. That model limitation was subsequently
resolved by the owner's model change, as recorded above.

## Delivered and verified

- Task Board storage calls run off the event loop, preserve trace context, drain
  on cancellation, and serialize mutations consistently with deadline transitions.
  Malformed task/asset/count reads fail explicitly; valid nullable metadata is
  preserved.
- UI task pages display governed input/output files with their correct public IDs.
  Proxy and browser validation accept operator-bound protocol-2 submissions.
  Real browser poster and worker journeys preview the actual bytes, submit/review,
  approve, and conserve funds at the accepted price.
- The external example handles input attachments and verifies their hashes. An
  independently keyed worker earns money and posts a second job through public APIs.
- Current-format restore preserves private directory modes. A restored submitted
  job retains its files and balances and settles exactly once under approval replay.
- Classification workers fetch task details before reading specifications, and the
  LLM prompt names actual input IDs. A real eight-task run recorded thirteen bids,
  both workers receiving payment, and persisted bid adaptation. Partial mock-Court
  awards and the small sample are explicitly distinguished from model accuracy.
- Remote-agent and protocol instructions match the shipped CLI/API. Local binding
  checks preserve four LAN-facing services and three loopback-only services.
  No second machine, SSH setup, new agent stream, deployment platform, or dependency
  was introduced.

## Verification

Root `just ci-quiet` exited **0** at **2026-11-04 04:36:54 UTC**, using the normal
PATH and existing isolated integration/E2E runtimes. All **1,497** recorded
source/config/test inputs were unchanged during the run.

| Gate | Result |
| --- | --- |
| Seven service CI gates | Passed |
| Per-service integration | 503 passed |
| Agents | 1,009 passed; 3 existing optional provider-key skips |
| Tools | 334 passed |
| Cross-service/runtime | 495 passed |
| Browser | 319 passed |
| Agent end-to-end | 115 passed |

Independent review covered the substantive runtime, strict-read, UI, example,
restore and classification changes. Regression failures were retained before the
repairs. Fixture corrections preserved assertions and business inputs; the manual
ruling test's timer isolation retains separate autonomous-ruling coverage.

Evidence: [acceptance record](2026-11-04-completion-evidence/acceptance.md),
[gate result](2026-11-04-completion-evidence/root-ci-result.json),
[full gate log](2026-11-04-completion-evidence/root-ci-r6.log.gz),
[UI journeys](2026-11-04-completion-evidence/ui-worker-live.md),
[classification run](2026-11-04-classification-runtime-evaluation.md).

## Earlier unmet criterion (resolved by the later model change)

The actual `qwen3-0.6b` Court panel with `/no_think` underpaid the vague good-faith
case and awarded full payment after the injected rebuttal in the unchanged declared
corpus. Bounded general prompt repairs also failed. The original production prompt
and requested model remain configured; no expected result was weakened and no
model was substituted.

This is not a transport or JSON-parsing failure. A green code gate does not establish
judge readiness. See [real evaluation](2026-11-04-judge-evaluation.md) and
[repair attempts](2026-11-04-judge-prompt-repair-attempts.md).

The canonical backlog records completed verified scopes and leaves judge readiness
open. Broader historical tickets are not blanket-closed: MP-20's Task Board portion
is verified, without claiming a new audit or repair of every shared-library client.
