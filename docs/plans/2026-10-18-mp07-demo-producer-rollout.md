# MP-07 demo producer rollout: plan and red tests

## Scope and observed contracts

The initial lane added tests and this plan only. After reviewing the red evidence,
the parent authorized the bounded product implementation recorded below. The demo
is an in-memory sequential driver with generated agent keys; this plan does not add
durable intent persistence, automatic retries, resume after restart, or autonomous
scheduling policy.

`demo_replay.clients` currently omits native operation identity on submit-bid,
accept-bid, submit-deliverable, approve, and dispute. Posting omits the acceptance
window. The engine neither observes lifecycle versions nor checks completion
before printing success or assigning workers.

The actual public Board bid response remains flat `BidResponse` (`bid_id`,
`task_id`, `bidder_id`, `amount`, `submitted_at`). Gateway's internal bid result is
nested; the parent confirmed that this lane must not change the public contract.
Other Board mutations return the current task view plus a separate immutable
`operation` envelope whose completed result is nested under `operation.result`.
The engine must not confuse a current task status with completion of its requested
operation, or require replay's current task view to remain at the original phase.

## Proposed ownership and flow

1. Posting steps supply all four explicit durations. Require a positive, exact
   integer `acceptance_window_seconds`, including in `load_scenario` before any
   registration/network activity. Match the SDK's SQLite-safe duration bound and
   reject booleans/coercions. The three existing duration values should also be
   passed explicitly instead of relying on client defaults. Example demo terms:
   bidding 3600, acceptance 300, execution 7200, review 3600 seconds. These are
   explicit example-poster choices, not a platform-wide acceptance policy.
2. Five mutation client functions require keyword-only `operation_id` and
   `expected_lifecycle_version`. Validate canonical op-UUID and exact nonnegative
   SQLite-safe integer version before signing/HTTP. Sign the caller's values
   unchanged. No allocation, default version, or automatic retry belongs in a
   client. The SDK's existing producer validation is the behavioral reference.
3. The engine owns a small per-instance intent map keyed by the loaded scenario
   step's ordinal. Bind each original step object to its ordinal so repeated equal
   YAML steps remain distinct; an explicit re-entry of the same step reuses its
   snapshot. Do not use payload equality or mutable task state as the intent key.
4. For a new intent, resolve actor/task and selected bid, fetch the authoritative
   current task once, validate its exact lifecycle version, generate one operation
   ID, and save an immutable snapshot of the semantic request. The snapshot owns
   resolved task/bid/actor and business arguments as well as ID/version. Save it
   before delivery. New JWT transport metadata may be generated for each delivery.
5. Re-entry of the same engine/step after response loss or pending status resends
   that snapshot; it must not re-read a newer version, reselect a bid, allocate a
   new identity, or change task because a latest-task reference moved. Changed
   semantic terms under the same step identity fail locally before another read
   or mutation. A distinct scenario step is a new intent.
6. Validate public responses against the request: known command, operation ID,
   task ID, source version, result identity and completion state. Claimed, unknown,
   missing or malformed operation/result bodies stop the sequential run with an
   explicit error. They never produce payout/assignment/completion output, update
   worker/dispute tracking, or allow subsequent scenario steps to run. Flat bid
   success must identify the requested task/bidder/amount and real bid ID.
7. Completed mutation results remain separate from current task projection. Use
   the original operation outcome to confirm the attempted command; use current
   task state as current state. Keep the snapshot on ambiguous failure. Existing
   run exception propagation supplies the stop boundary; no new retry budget or
   persistence policy is introduced.

## Later product and fixture edits

Expected product ownership is `tools/src/demo_replay/clients.py`, `engine.py`, and
the explicit posting terms in `tools/scenarios/{quick,full-lifecycle,scale}.yaml`.
Prefer a small tool-local response/intent helper if those files become unwieldy;
do not add a runtime dependency on a service package or duplicate lifecycle rules.

Existing client/token tests must later supply explicit fields before signing while
preserving URL, action, audience, resource, signature, and response assertions.
Engine fixtures must place mutation steps in their scenario list and return valid
native task envelopes; old bare success objects are no longer completion evidence.
Do not alter unrelated feedback/Court/funding behavior or its existing assertions.

## Red proof scope

The new isolated test file exercises real signed client requests through
`httpx.MockTransport` plus engine dispatch with bounded client doubles. It covers
explicit windows, required identities, pre-HTTP input rejection, observed versions,
the public flat bid shape, task-operation nesting, no advancement on incomplete
responses, exact response-loss retry, and local changed-term rejection. Tests call
only temporary files or in-memory keys; no live service or platform key is read.

Missing client keyword parameters are interface-gated failures. Engine assertions
that reach false success/no-version-observation are separate behavioral failures.
No claim of actual Bank/Court effects, restart recovery, or full-goal completion is
made by these mocked producer tests. Record exact logs and failure classification
before handing off for implementation review.

## Frozen pre-implementation evidence

The new test run collected 53 cases: **52 failed, 1 passed**, exit 1, in 1.39
seconds. Sixteen failures are gated by the missing client identity/window keyword
interfaces. Thirty-six reach behavioral assertions: missing scenario validation,
omitted posting terms/version observation, false advancement on incomplete output,
response-loss retry without an original version, or accepted changed terms. The
passing control confirms the actual flat public bid response remains supported.

Log: `/private/tmp/agent-economy-mp07-demo-producer-red.log`.
Frozen test SHA-256:
`a8074bda1ec2f4d9fd281b0de6f888361cc6905cfda9dae8c4550f624ea74fca`.
Ruff and strict Mypy pass for the test file. Six valid task/operation fixtures also
pass the actual Board response schemas, projection validation and envelope
validation; see `/private/tmp/agent-economy-mp07-demo-producer-fixture-validation.log`.
Claimed pure-operation packets are deliberately malformed controls; only financial
and Court commands represent legal pending effects.

Parent review approved implementation after this freeze and confirmed that posting
task-ID generation remains unchanged in this slice. Recoverable posting identity
belongs to MP-05. The prohibition on generated client fields here applies to native
mutation operation IDs and versions. This lane adds no posting-retry guarantee.

## Existing fixture reconciliation before edits

The existing 67 demo tests passed before product changes, recorded in
`/private/tmp/agent-economy-mp07-demo-existing-before.log`. After implementation,
the same suite produced three failures and 64 passes in
`/private/tmp/agent-economy-mp07-demo-existing-drift.log`: two direct posting calls
and the posting URL-dispatch fixture omitted the now-required terms. Supply the
three previous duration values explicitly plus a selected acceptance window. Give
the dispatch fixture its previously implied specification text explicitly. Preserve
all existing URL, signed-token, allocated-task/lock-identity, and response assertions.

The original 53-test red source is preserved byte-for-byte at
`/private/tmp/agent-economy-mp07-demo-producer-red-source.py` with the hash above.
Three additional controls exercise distinct equal step identities, acceptance
retry retaining its original task/bid despite changed live references, and strict
numeric effect types. The latter exposed one actual implementation defect:
dictionary equality accepts boolean `True` as numeric `1`. The final regression
baseline is `/private/tmp/agent-economy-mp07-demo-ownership-final-red.log`, with one
failed assertion and 55 passing controls. Canonical typed JSON comparison repairs
that consumer check without changing any acceptance assertion.

## Final implementation and verification

The five native clients require and sign caller-owned IDs/versions. Posting accepts
four explicit durations. Each example posting now chooses an acceptance window of
300 seconds alongside explicit existing bidding/execution/review durations.

The engine retains immutable JSON snapshots per loaded scenario ordinal, including
the selected actor, task, bid, business terms and observed version. Exact retry does
not reselect a bid or refresh a live reference. Typed public response checks live in
`demo_replay/task_operations.py`; the public bid stays flat, and mutation completion
comes from the separately validated `operation.result`, never from task status alone.

A final regression found that changing a previously attempted native step to a
non-native action could bypass handler-local checks. The pre-fix run in
`/private/tmp/agent-economy-mp07-demo-action-owner-red.log` had one failed assertion
and 56 passing controls. Ownership is now checked before dispatch of any action;
the same step cannot escape its original intent by becoming feedback or another
action. Distinct equal step objects still own separate identities, while aliased
objects require an explicit ordinal when invoked outside the normal run loop.

Initial implementation gates, all exit 0:

- Full tools unit suite: **201 passed**, log
  `/private/tmp/agent-economy-mp07-tools-final-unit.log`.
- Full `just ci-quiet` from `tools`: **201 passed**, with format, lint, mypy,
  Bandit and spelling gates passing; log
  `/private/tmp/agent-economy-mp07-tools-ci-quiet.log`.
- Additional strict mypy over the three changed producer modules and new test:
  `/private/tmp/agent-economy-mp07-demo-strict-final.log`.

Commands use `UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1`; unit tests use
`uv run --no-sync pytest tests/unit -q --tb=short --color=no`. No live services,
provider calls, ordinary runtime data, Git operations, new dependencies, or new
test suppressions were involved. The existing 67 demo tests retained their
assertions; only three posting-input fixtures needed explicit terms. The new test
file then contained 57 cases, while its original 53-case red source remains preserved
separately with the original hash.

Verified SHA-256 values (paths relative to `tools`):

| File | SHA-256 |
| --- | --- |
| `src/demo_replay/clients.py` | `0afdaa40bbc2d816336259106dc669a040ffa3d93690e31509fc8a017437f34a` |
| `src/demo_replay/engine.py` | `924d036ec04b5689fa647f291c624911b5c56eae77b09e533c4018c5d5ca6fc8` |
| `src/demo_replay/task_operations.py` | `43eef687ea857c5960ef325b7ef78433b012d725c1aa1221501459a2ab17f720` |
| `tests/unit/demo_replay/test_mp07_task_operations.py` | `3fcb8775f23385e2f6541daa284a1da4edf0ab9127288eb97ab53bd1487d1bb6` |
| `tests/unit/demo_replay/test_engine.py` | `50d0af2745f6cb05c4aa71c3c644f700cc9d567c9879b60907fde7cd5980b4ad` |
| `tests/unit/demo_replay/test_mp03_outbound_tokens.py` | `d38eaccc0bfda7742c7cb479d34c8bc3de0e5a11bf13d347450072fb2c628b65` |
| `tests/unit/demo_replay/test_mp04_financial_intents.py` | `9f4203217fa39c6bb701f8a6735ff60f89775315052f75b0fe87ecc2a4b56ebb` |
| `scenarios/quick.yaml` | `d736a6509925265ab9731e1f140d16dfa20ec9d83bbf9c00ef724494ce919362` |
| `scenarios/full-lifecycle.yaml` | `fc699005992868f92b311ec847f72bed243a5cc0308a32f4d5a8661a366fad86` |
| `scenarios/scale.yaml` | `b17542bbbf366d827d6746d60d92f41b1e8f8016005e79d8ae1232edd617fa4d` |

Independent review and live/root integration remain outstanding. These mocked
producer checks do not establish actual settlement, Court judging, process-restart
recovery, or recoverable posting identity; MP-05 retains the latter contract.

## Independent review correction and final freeze

Parent review identified a concrete bid-identity gap: public Board bid IDs are
derived as `bid-{operation_id[3:]}`, while the engine initially checked only a
nonempty bid ID. A genuine regression run recorded one failure and 57 passing
controls in `/private/tmp/agent-economy-mp07-demo-bid-identity-red.log` (exit 1).
The consumer now requires the exact derived bid ID. Successful bid doubles derive
their IDs from the submitted operation; the flat-response control preserves its
original equality assertion with an explicitly fixed generated UUID. Public bid
response shape remains unchanged.

Final reviewed-source verification, all terminal exit 0:

- **58 producer tests passed**, log
  `/private/tmp/agent-economy-mp07-demo-reviewed-green.log`.
- **202 tools tests passed through `just ci-quiet`**, including format, lint,
  mypy, security and spelling gates; log
  `/private/tmp/agent-economy-mp07-tools-ci-quiet-reviewed.log`.
- Strict scoped mypy: no issues in four files; log
  `/private/tmp/agent-economy-mp07-demo-strict-reviewed.log`.

The hash table above reflects the final bid-corrected source. All source/test edits
are frozen for the parent's independent full tools verification. No additional
runtime behavior or persistence policy is included in this correction.
