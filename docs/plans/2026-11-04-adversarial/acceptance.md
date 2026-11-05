# Adversarial campaign acceptance record

Completed: 2026-11-05 02:08 Europe/Amsterdam  
Base revision: `35cf0e3a5fcfd3c6a92d6c2e305bdfd711c7aa16`

## Assessment outcome

All 86 planned sequences have an evidence-backed disposition:

| Disposition | Count |
| --- | ---: |
| PASS | 75 |
| BLOCKED | 10 |
| FAIL | 1 |
| NOT RUN | 0 |

This is a completed adversarial assessment, not a systemic production-readiness
claim. A passing aggregate suite does not resolve a failed scenario or turn a
proposed policy into an implemented guarantee.

## Final integrated gate

The final isolated root `just ci-quiet` run exited 0. Its retained raw output is
[`root-ci-final.log`](root-ci-final.log), with exit status and integrity hash in
[`final-root-result.json`](final-root-result.json). All 1,808 inventoried source
inputs remained unchanged during the successful run. The terminal gate includes:

- UI integration: 344 passed.
- Agents: 1,013 passed, 3 existing skips, plus the final selected 124 live E2E
  cases passed.
- Tools: 334 passed.
- Cross-runtime integration: 504 passed.
- UI browser: 367 passed.
- Live E2E: 124 passed in 112.40 seconds.
- Final terminal line: `Full CI passed (libs + services + agents + tools +
  integration + ui-e2e + e2e)`.

The exact integrated source inventory and SHA-256 values are in
[`final-source-r2.json`](final-source-r2.json). Lane-specific raw commands,
results and manifests are retained in [`economics/`](economics/),
[`recovery/`](recovery/) and [`disputes/`](disputes/).

## Product defects found and repaired

- Valid typed Task Board 5xx envelopes were mislabeled as malformed by the UI
  proxy. The proxy now preserves valid 4xx/5xx envelopes and still sanitizes
  malformed bodies without forwarding internal markers.
- Disputed-task UI copy incorrectly implied Court was already judging while the
  worker rebuttal window remained open. The authoritative rebuttal deadline now
  reaches the UI; poster and worker states distinguish worker wait, deadline
  expiry, recorded rebuttal and Court wait.
- Pending action rerenders lost meaningful keyboard focus and presented normal
  progress with error semantics. Pending feedback is neutral `role=status`,
  failures are `role=alert`, and focus moves to the recovery endpoint.
- Submitted-work UI omitted the frozen-file boundary and supported recovery.
  It now explains that files cannot be replaced and directs posters/workers to
  the existing approve/dispute flow without inventing revision support.
- A saved pending intent could be reused by a different action or different bid:
  delayed approve→dispute resent approve, and bid-2 resent bid-1. Conflicting
  controls now disable synchronously, distinct intents are rejected, exact
  retry remains explicit, and failure restores each control's prior eligibility.
- Approval confirmation previously called the offered reward the payment. It now
  distinguishes accepted-price payment from the unused reward returned to the
  poster.

Each repaired slice has retained failing-first proof, focused green checks,
independent review, component CI and the final root exit 0. Canonical tickets
T-223 through T-228, T-231/T-232 and T-237/T-238 are closed on that evidence.

## Coverage completed without a production defect

- T-229 completed owned Gateway-outage and telemetry fault coverage. Writes
  failed honestly or reconciled exactly; correlated traces omitted secrets; 404,
  timeout and dropped telemetry exporters did not change business outcomes.
  T-230 is retired as NOT APPLICABLE because no current-contract production
  defect was demonstrated.
- T-233 completed multi-state restore, the combined ECO-05 interruption chain
  and unattended approved/disputed/expired outcomes. T-234 is retired as NOT
  APPLICABLE because the compound sequences passed without a production repair.

## Confirmed failure

- **IDN-06:** a math-only worker can select an unsupported coding task, and its
  parser can freeze an invalid one-byte `7` output. Current admission filters do
  not enforce a capability taxonomy. This remains a real capability-selection
  gap tracked by T-239; the campaign did not invent a routing policy.

## Blocked decisions

The ten BLOCKED sequences are explicit decision dependencies, not unexecuted
tests:

| ID | Blocking decision |
| --- | --- |
| IDN-05 | Define key rotation, continuity, revocation and migration semantics. |
| AST-06 | Choose opaque upload-key replay or content-bound changed-byte rejection; T-235/T-236 remain open. |
| REC-06 | Decide whether proactive UI/Task Board version-readiness negotiation is a supported contract. |
| SUP-01 | Decide whether supervision supports a service-only scope with agents stopped. |
| SUP-02 | Ratify Court crash-restart behavior and restart bounds under that scope. |
| SUP-03 | Define intentional stop versus crash-restart semantics. |
| SUP-04 | Define interaction between installed supervision and one-shot `start-all`/`stop-all`. |
| SUP-05 | Define automatic Court restart as part of dispute recovery. |
| SUP-06 | Ratify flap backoff, unhealthy/restarting status and bounds. |
| SUP-07 | Define environment/config refresh semantics for supervised units. |

T-235/T-236 remain open for upload replay semantics. T-239 remains open for the
capability, key-rotation, compatibility and supervision policy decisions. No
host supervisor was installed and no ordinary live task, key, database or upload
was used as campaign acceptance evidence.

## Review resolution and limits

Independent review findings were resolved before the final gate: worker rebuttal
eligibility is strict at the deadline boundary; malformed typed responses do not
leak internals; pending UI state is not styled or announced as an error; failure
recovery preserves pre-existing control eligibility; approval copy reflects the
accepted-price rule; Court process-cut tests use bounded cleanup; and the ECO-05
compound path proves one final ruling and settlement.

The real-model Court corpus produced 15/15 valid votes across five cases, while
deterministic mock panels were used only for state-machine and recovery oracles.
The campaign does not claim fairness, learned adaptation, universal task
suitability, proactive version negotiation or supervised production operation.
