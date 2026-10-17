# MP-04 historical observations checkpoint

Status: historical implementation and bounded reviews pass; root integration gate still open after
native checkpoint `96e3922`. T-116/T-117 and tasks 6.2–6.4 remain open. No new root CI success or
complete MP-04 closure is claimed.

The concrete contract is `docs/plans/2026-10-17-mp04-historical-observations.md`. Historical
ownership stays null; a new attempt has its own ID/hash but is never persisted as an alias.
The existing cutover markers and hashes are verified inside the financial transaction. New
settlements of historical locks still require valid exclusive ownership before changing money.

## SDK, clients and funding

- Corrected SDK history baseline: four valid positive failures and 77 controls. The initial
  released fixture with two payout transactions was invalid and its earlier failure is excluded.
- One-leg historical split repair: one genuine positive red, then green. Splits allow one or two
  nonzero transactions; releases exactly one. The former one-leg negative was an invalid contract
  assumption and was replaced with zero/three-ID rejection cases.
- Nullable-time repair: four genuine positive reds after real migration admission was verified.
  Historical terminal null times and locked non-null times remain exact. Native rules are separate.
- Client lock and demo credit boundaries each had one genuine valid-observation red. They now
  use the shared union parser; settlement/issuance success remains native-only.
- Feeder, treasury and demo commands reject validated observations before claiming new funding.
  They use typed model fields; the two agent commands close both clients before propagating errors.
- Independent review approved the SDK/client/funding slice. Parent component gates passed:
  auth: 306 tests; shared clients: 104; agents: 388 (three credential skips, 94 E2E deselections);
  tools: 144.
  These gates preceded the additional contextual native-settlement repair described below.

Relevant logs under `/private/tmp/`:

- `agent-economy-mp04-legacy-sdk-red-corrected.log`
- `agent-economy-mp04-legacy-split-one-red.log`
- `agent-economy-mp04-legacy-nullable-time-red.log`
- `agent-economy-mp04-service-client-legacy-red.log`
- `agent-economy-mp04-demo-legacy-credit-red.log`
- `agent-economy-mp04-auth-history-parent-ci.log`
- `agent-economy-mp04-clients-history-parent-ci.log`
- `agent-economy-mp04-agents-history-parent-ci.log`
- `agent-economy-mp04-tools-history-parent-ci.log`

## Bank propagation and public proof

Bank credit/lock now return validated observations and preserve exact typed historical conflict
and corruption details. Route and adapter audit logs distinguish null historical ownership from
the caller's attempt. Native lock audit logs use the winning transaction ID. Issuance/release/split
still require native success. Independent review approved the five-file Bank slice; 50 focused
tests and parent Bank component CI passed (`agent-economy-mp04-bank-history-ci.log`).

Evidence limits: the original 11 Bank failures included two invalid lock fixtures (their transaction
reference incorrectly used escrow ID instead of task ID) and one duplicate-log-capture harness
failure. Only eight original failures are valid product reds. The incidental native audit-ID
correction has no valid focused pre-fix red proof and cannot independently satisfy that closure rule.
Corrected fixtures and capture now pass; these limitations must remain explicit in final review.

The new public integration file builds a real v1 SQLite database, creates and verifies a backup,
migrates to v2, then calls Bank HTTP through the production adapter and signed Gateway ASGI into
real SQLite. Identity verification is mocked and documented; Gateway signing is real.

This public proof found a real commit-then-response-error defect: native settlement of an old
escrow with empty `created_at` committed, then the native response model rejected its current
projection. Parent reproduced the failure before repair and observed the durable receipt, released
escrow, recipient leg and matching ledger transaction. Evidence:

- `agent-economy-mp04-native-on-history-http-red.log`
- `agent-economy-mp04-native-on-history-committed-proof.json`
- Owned retained database:
  `/private/tmp/agent-economy-history-http-red.yuOJ4v/test_native_settlement_preserv0/gateway.db`

The contextual repair preserves original historical creation time for native release/split
projections. Native lock creation times and native commit/resolution times remain strict. Direct
SDK red: two failed /93 passed; final shared suite: 95 passed; auth CI: 311 passed. Eight public
history cases and 13 existing native public cases passed; independent review approved the source
and public proof.

The old agent SDK blanket empty-release-time negative is explicitly reconciled to invalid-type
rejection, with release/split historical-time positives retained. Its initial contract-mismatch
run was one failed /89 passed, then 92 passed (`agent-economy-mp04-agent-created-time-contract-*.log`).
This is a contract correction, not a new product-red claim.

Task Board supplemental public posting tests validate full legacy envelopes first, then prove
502 rejection without a task row or compensation/release/split of historical funds. Two new cases
plus two native controls pass; no historical red is claimed for this already-correct policy guard.

## Gateway implementation review

The approved Gateway baseline was19 genuine reds /3 controls. The first implementation passed27
historical tests, 111 and 57 related regressions, and component CI. Its independent source review
found two HIGH integrity gaps that block approval:

1. Natural-key lookup normalized raw transaction/escrow IDs with `str()` before verification,
   misclassifying BLOB IDs as missing ownership instead of failed integrity.
2. Historical lock transaction verification omitted overlapping native-leg ownership, both for
   observations and admission of a new settlement.

Both HIGHs are now repaired and independently approved. Five genuine review reproductions failed
before repair (`agent-economy-mp04-history-review-high-red.log`). Final history: 32 passed; native/router: 52
pass; final Gateway component CI passes (`agent-economy-mp04-history-gateway-ci-final3.log`). Raw IDs
are checked after attestation without coercion; lock observations and settlement admission reject
overlapping native transaction ownership. No frozen schema or migration changed.

## Remaining work

Parent full cross-service integration now passes 26 tests with isolated host ports
(`agent-economy-mp04-cross-integration-parent-final.log`); full Bank integration passes 24
(`agent-economy-mp04-bank-all-public-parent-final.log`). The four existing root integration test
reconciliations are independently approved.

Root16 `just ci-quiet` exited1 (parent session63802), with log
`/private/tmp/agent-economy-mp04-root16-ci.log`. Every library and service component CI passed.
Per-service integration stopped the root pipeline because Task Board and Court valid setup omitted
required operation IDs. Both were narrow fixture failures before their original lifecycle assertions.
Parent reconciled explicit IDs and the Task Board lock mock (now returning its actual seeded
Gateway receipt); focused suites pass 2+1 and their style checks pass. Independent review approved
both fixture changes without removing any original assertion.

Before/after snapshots contain the same929 source/config hashes with no differences. The isolated
runtime `f74286162c254e0a9d14c8dd026a2a6e` recorded drained groups and no cleanup errors; a fresh host
signal-0 check found all9 groups absent (`agent-economy-mp04-root16-cleanup.json`). Do not rerun or
poll terminal root16. Its later agents/tools/runtime/browser/economy phases were not reached.
Continue remaining validation and then run a new root gate. Root15 remains the last full success.

The separate live economy baseline (`just test-e2e`) exited1: 70 failed, 19 passed and five setup
errors, predominantly old calls omitting required SDK financial operation IDs. Its log is
`/private/tmp/agent-economy-mp04-economy-baseline.log`; the exact ten owned groups are drained and
absent (`agent-economy-mp04-economy-baseline-cleanup.json`). The assigned E2E test owner is reconciling
only documented API changes while preserving every original behavior assertion. Those edits are
not yet verified or approved. A separate UI browser run is active as parent session87724, log
`/private/tmp/agent-economy-mp04-ui-e2e.log`.
All remaining Core and Vision packages, owner policy decisions, and real-provider proof retain
their original scope.
