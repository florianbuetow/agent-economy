# MP-07 Court router test reconciliation

## Plan recorded before edits

Scope is the seven assigned Court-facing router test modules. The shared router
fixture and its persisted external-effect helpers are parent-owned and remain
untouched here. The initial run collected 46 cases: 22 failed and 24 passed, exit 1,
in `/private/tmp/agent-economy-mp07-court-router-before.log`.

Failures originate from missing explicit operation fields, success-only Court
mocks without persisted claims, obsolete unrestricted status updates, arbitrary
ruling IDs, and legacy row-matching replay expectations. They are fixture/contract
reconciliation evidence; they do not establish new production defects.

## Reconciled contracts

1. Direct signed mutation payloads carry a caller-owned operation ID and expected
   lifecycle version before signing. Negative action, actor, field, percentage and
   resource-binding cases provide all unrelated valid fields so the intended
   rejection branch remains exercised. Replay retains the original full payload.
2. Successful Court handoff callbacks persist actual chosen claim IDs and exact
   task/actor/reason/escrow facts through `gateway_effects.persist_claim`. Existing
   handoff argument assertions remain. Rebuttal success uses the actual stored
   claim and real atomic rebuttal writer; its Court status remains
   `rebuttal_pending`, matching the current Court service contract.
3. Transport and malformed-acknowledgment failures without committed Court facts
   remain typed 502 errors. Dispute business status stays submitted, while the
   already claimed native operation exposes pending recovery ownership. Rebuttal
   failures leave both the task and Court rebuttal facts unchanged.
4. A success-shaped acknowledgment without persisted claim evidence returns a
   pending operation (202), never a completed dispute. The old missing-ID test
   becomes an authoritative-evidence test: no dispute ID, Court row, lifecycle
   completion event, or financial effect is invented. Actual acknowledgment decode
   exceptions remain distinct 502 cases; committed evidence can still finalize
   despite a lost or malformed acknowledgment.
5. Cross-task rebuttal binding uses two genuinely persisted claims. The missing
   stored-dispute negative case uses explicitly labeled corruption SQL in its
   owned fixture database; no ordinary raw lifecycle writer is expected to permit
   that corruption.
6. Native ruling IDs match the stored Court claim. Exact replay preserves operation
   ID, version, ruling identity, percentage, and summary, producing one settlement
   and one event. Changed terms under that operation ID return
   `409 operation_id_conflict` without changes. A new ruling attempt on an already
   ruled task retains typed invalid-status rejection with no additional effect.

Authorization/field/binding/resilience assertions remain intact or gain direct
persisted no-effect checks. No generic signer mutates payloads, no implicit IDs or
windows are introduced, and no actual Court judging is claimed by these fixtures.
Independent review and root CI remain separate completion gates.

## Verification

The final combined run passed **54 cases**, exit 0, in 32.72 seconds. This retains
the original 46 cases and adds two acknowledgment-decode failure cases, one
committed-claim/lost-acknowledgment case, and five changed-term replay cases.
No skips or expected failures were added.

Run from `services/task-board`:

```sh
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 uv run --no-sync pytest tests/unit/routers/test_court_resilience.py tests/unit/routers/test_dispute_court_handoff.py tests/unit/routers/test_rebuttals.py tests/unit/routers/test_rebuttal_dispute_binding.py tests/unit/routers/test_disputes.py tests/unit/routers/test_wp06_record_ruling_idempotent.py tests/unit/routers/test_t072_disputes_gaps.py -q --tb=short --color=no
```

Log: `/private/tmp/agent-economy-mp07-court-router-final-green.log`.
Ruff and formatting pass; Mypy reports no issues in seven files and Pyright reports
zero errors/warnings. Type checking uses actual checked fixture mock/store types,
not new suppressions. The parent repaired shared-fixture diagnostics separately;
these seven files do not modify that shared fixture.

Static logs:

- `/private/tmp/agent-economy-mp07-court-router-ruff-final.log`
- `/private/tmp/agent-economy-mp07-court-router-format-final.log`
- `/private/tmp/agent-economy-mp07-court-router-mypy-final.log`
- `/private/tmp/agent-economy-mp07-court-router-pyright-final.log`

Verified SHA-256 values (paths relative to `services/task-board/tests/unit/routers`):

| File | SHA-256 |
| --- | --- |
| `test_court_resilience.py` | `9503ddda00484ea36dc5351ace33d044592e42bc19b890fd1b168653f417e8bb` |
| `test_dispute_court_handoff.py` | `6fbc0c25d8c0fb87a4a556dbfd854f4b98f243f8099231539144b2226981d8fe` |
| `test_rebuttals.py` | `c36b541d1c4041ba3e7149fddb0c5c78d42904fb7e7f959ebf7cc0d9bb3beee4` |
| `test_rebuttal_dispute_binding.py` | `13dee44bf7590b621e2c74962a1f752b69b0636f5fe1d91a34617dc7bc296d64` |
| `test_disputes.py` | `f6625e246502d9121d13d20c8fc89d9787bd58f2b2347ddafe91d68f220f3243` |
| `test_wp06_record_ruling_idempotent.py` | `3cd1fa8ce311ecc13000929c4622afc4df32060bfd75bae10bde4a34f3e766cc` |
| `test_t072_disputes_gaps.py` | `5f4034f0ddedb205f2c4d4f0e2efb6dd742cd3658854576a10e31c343602fce9` |

The shared Court rebuttal callback was reconciled by the parent to the actual
`DisputeDbClient.update_rebuttal` producer: it persists the rebuttal and keeps the
claim in `rebuttal_pending`. The existing router status assertions remain intact.
Actual Court judging, other test consumers, independent review, and full root CI
remain outside this bounded result.
