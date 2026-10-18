# MP-07 math-worker native-intent tests

## Scope

This test-only T-181/T-182 slice defines how `MathWorkerLoop` consumes the final reviewed
`RuntimeTaskCommands` coordinator. The worker loop must require that coordinator as its fourth
constructor dependency. The tests use a real `FileTaskIntentStore`, real coordinator, deterministic
Ed25519 identity, registered agent ID and exact service URLs. Public SDK mutation methods are faked;
private SDK requests and Gateway access are forbidden.

The test suite does not change task selection, bid fallback, reward, solution or dispute policy. It
does not claim full MP-17 recovery: task discovery, answer/asset checkpoints, remote commitment
discovery, rebuttals and receipt-derived earnings remain open. In particular, uploading a solution
before the first durable submission intent leaves the accepted MP-09/17 crash-before-intent asset gap.

## Required behavior

- `MathWorkerLoop(agent, llm, config, commands)` requires `RuntimeTaskCommands` explicitly.
- `_phase_bidding(task_id)` performs journal lookup first. A stored bid replays its exact amount,
  operation ID and expected lifecycle version after store reopen without fetching the task, balance or
  a new LLM decision. Only a missing bid slot reads the current task, uses its explicit lifecycle
  version, applies the existing bid policy, persists the frozen terms, then invokes the public SDK.
- `_phase_submitting(task_id, solution)` performs journal lookup first. A stored submission replays
  without uploading or reading current task state. Only a missing slot uploads the solution, reads the
  accepted task terms/version, persists a native submission intent, then calls the public SDK. The
  pending record must already exist when either public mutation transport begins.
- Local persistence/fsync failure stops mutation transport. It does not allocate a replacement
  operation identity or fall back to an unjournaled SDK call.
- Pending, blocked and attention-required bid intents never enter acceptance polling. Only a completed
  bid supplies its validated bid ID to that phase.
- Pending, blocked and attention-required submission intents never enter review polling or earnings
  accounting. Only completed submission can advance.
- Each worker cycle gives known unfinished intents one replay attempt before filtered open-task
  discovery, so a lost acknowledgment is retried without requiring process restart.

## Test structure

One interface case owns the expected current constructor failure. A test-only compatibility helper
injects the coordinator into the legacy three-argument loop for all downstream cases, so they execute
and produce independent behavior evidence instead of repeating the same constructor `TypeError`.

`agents/tests/unit/math_worker/test_mp07_native_intents.py` covers:

1. Required four-argument constructor.
2. Bid lost-ack reopen/replay and absent-slot prepare/persist-before-send.
3. Storage failure preventing bid transport.
4. Submission reopen/replay without upload and absent-slot upload/read/persist/send ordering.
5. Four bid-state phase gates and four submission-state phase/earnings gates.
6. One known-pending replay pass before new filtered discovery.

## Verification boundary

Capture the genuine focused red and classify the constructor failure once. Run scoped Ruff, format,
Mypy and codespell and freeze source/log hashes. Existing worker tests, complete agent CI and root CI
remain parent gates after implementation. No existing test, product source, config, dependency, Git
state or ordinary runtime artifact is modified here.

## Captured red evidence

The focused pre-implementation run collects 16 cases: the deterministic real-store/coordinator
binding control passes and 15 worker cases fail. One case exclusively owns the missing required
constructor dependency and the `_phase_bidding(task_id)` interface. The downstream compatibility
harness then exposes independent current behavior:

- stored bid replay calls the LLM instead of looking up the slot first;
- a new bid uses the unversioned public call and never persists the caller-owned identity;
- injected journal fsync failure is irrelevant to the old unjournaled bid transport;
- a stored submission uploads again instead of replaying;
- a new submission uses the old unversioned public call;
- four bid-state cases share one result-model and phase-gating gap;
- four submission-state cases share one result-model, review and earnings-gating gap;
- cycle discovery runs without the required one-pass unfinished-intent replay.

The four state variants in each gate are coverage of one behavior boundary, not four independent root
causes. No private SDK request is used: those methods are assertion-raising mocks. All store and
coordinator construction, deterministic public-key binding, pending-record creation and reopen probes
complete successfully before the worker-specific assertions fail.

## Strict reward review regression

The reviewed worker integration must not coerce a Task Board reward before creating a binding bid.
`_phase_bidding` requires the task's `reward` to be an exact non-boolean positive integer no greater
than SQLite's signed 64-bit maximum. Boolean, string, float, overflow, zero, negative and missing
values fail with `invalid_input` immediately after the current-task read. They must not fetch balance,
call the LLM, allocate a UUID, create a bid slot or invoke the public SDK.

Scanning applies the same shape admission before configured reward-range comparison. A malformed
candidate is excluded without coercion and without aborting the scan; a valid candidate beside it
remains eligible and is the only malformed/valid pair shown to the selection LLM. This preserves the
existing configured min/max filter and valid-reward bidding policy without adding a default.

`agents/tests/unit/math_worker/test_mp07_native_intent_regressions.py` adds eight bidding-boundary
cases and four mixed-candidate scanning cases. It reuses the frozen deterministic public-agent,
real-store and coordinator fixtures. The frozen 16-case worker-intent suite remains unchanged and
continues to provide the valid-reward policy and end-to-end integration controls.

The focused reward baseline collects 12 cases: 11 fail and the SQLite-overflow scanning case passes
as a configured-range control. All eight bidding cases fail: seven coercible or range-invalid rewards
reach forbidden balance/LLM decision work, while a missing reward raises raw `TypeError`. In scanning,
boolean and float rewards are included in the selection prompt, a numeric string aborts comparison,
and the overflow candidate is correctly excluded while the valid candidate remains selectable. These
are executed behavior failures; real store creation and the empty-slot assertion succeed before each
worker boundary is exercised.
