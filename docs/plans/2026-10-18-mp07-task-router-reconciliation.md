# MP-07 task router test reconciliation

## Plan recorded before edits

Scope is `services/task-board/tests/unit/routers/test_tasks.py` only. The generic
signer, shared fixtures, production code, and other router suites remain unchanged.
The full untouched 76-case baseline is retained in
`/private/tmp/agent-economy-mp07-task-router-before.log`.

## Explicit contract reconciliation

1. Each direct creation payload supplies an explicit positive acceptance window
   before signing. Preserve invalid actor, task ID, reward, deadline, and missing
   field cases by making all unrelated fields valid. Paired escrow payloads supply
   their canonical task-lock operation identity, including intentional cross-token
   mismatch fixtures; no signer helper inserts or repairs missing fields.
2. Direct cancellation payloads supply a new operation ID and the currently
   observed lifecycle version before signing. Wrong-action cases bind their token
   to the actual endpoint so action validation, rather than an unrelated resource
   mismatch, is exercised.
3. The already-cancelled negative test represents a new cancellation intent against
   a cancelled task and retains `409 invalid_status`. Add exact replay of the
   original operation/payload/version as a separate step: it must return the stored
   success and produce no second refund or event.
4. The old short-deadline cancellation test never advanced past its deadline. Keep
   its positive before-cutoff behavior under an explicit frozen clock, and add
   actual at/after-cutoff rejection cases. Native cancellation of a zero-bid task
   at its bidding cutoff returns `409 cutoff_reached` without releasing escrow or
   changing status. Deadline expiry remains a separate authorized transition.
5. Preserve creation/query/error/effect assertions. Strengthen cancellation refund
   checks with real receipt/ledger/event evidence, and retain no-effect checks for
   rejected commands. Test-owned mock accessors may validate fixture types for
   static analysis; no production error or test assertion is suppressed.

The fixed clock applies only within the bounded cancellation request tests; no
periodic task or asyncio sleep is run under it. Independent review and root CI
remain outstanding after these focused tests.

## Verification evidence

The untouched baseline finished with **30 failed, 46 passed** (exit 1). The
converted suite passed **78 tests**, exit 0, in 43.10 seconds: all original 76
cases remain represented, and two explicit cutoff-boundary cases were added.
The formerly mislabeled CAN-09 now names its actual before-cutoff success behavior.
CAN-05 retains new-intent rejection and additionally proves exact replay.

Command, run from `services/task-board`:

```sh
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 uv run --no-sync pytest tests/unit/routers/test_tasks.py -q --tb=short --color=no
```

Passing log: `/private/tmp/agent-economy-mp07-task-router-first.log`.
Ruff and Mypy passed; Pyright reported zero errors and warnings. Mypy used the
local service/shared-library source roots through `MYPYPATH`.

- `/private/tmp/agent-economy-mp07-task-router-ruff-first.log`
- `/private/tmp/agent-economy-mp07-task-router-mypy-first.log`
- `/private/tmp/agent-economy-mp07-task-router-pyright-first.log`
- `/private/tmp/agent-economy-mp07-task-router-format-check.log`

Verified source SHA-256 for `test_tasks.py`:
`d7a1c8f996d6f155921b33fa965f277ed368b7c016c911f6e0187b0525477934`.

This is test-contract adoption, not a new production-fix claim. No shared fixture,
generic signer, production module, or other test file changed in this slice.
No skips, suppressions, or expected failures were introduced. Independent review,
remaining fixture adoption, and fresh root CI remain separate gates.
