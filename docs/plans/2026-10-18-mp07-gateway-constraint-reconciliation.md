# MP-07 Gateway constraint-test reconciliation

## Scope and baseline

This lane updates only `tests/unit/test_constraints.py` and
`tests/unit/test_t072_constraints_gaps.py`. The frozen native Board HTTP fixture is reused
read-only. Production, shared conftest and the fixture itself stay unchanged.

- Original `test_constraints.py` SHA-256:
  `5ee9e2165ae5f2190744cd42576c5967846be789414ee334300e02e0ff73b1c5`.
- Original `test_t072_constraints_gaps.py` SHA-256:
  `0a47e177b803d59b772b0a41ade1d543aed09b3b09ee08d9cf16741d8066991e`.
- Byte-identical copies:
  `/private/tmp/agent-economy-mp07-gateway-test_constraints-before.py` and
  `/private/tmp/agent-economy-mp07-gateway-test_t072_constraints_gaps-before.py`.
- Focused baseline: `/private/tmp/agent-economy-mp07-gateway-constraints-baseline.log` —
  13 failed. Eleven stop at the missing acceptance-window publication field; the two direct
  generic status cases receive the reviewed `native_operation_required` conflict.

## Per-case mapping

### `test_constraints.py`

1. Valid task constraint becomes a valid native bid acceptance with explicit `open` status and
   lifecycle version 0. It must return 200 and persist the accepted worker, bid, fixed reward and
   offer amount.
2. Violated task constraint becomes a stale native lifecycle version. It must return
   `task_version_conflict`, expose expected/actual version and current status, and write nothing.
3. The constraint error-envelope test observes the same native conflict details instead of the
   retired generic table/column vocabulary.
4. “No constraints” is superseded by mandatory native preconditions: omitting
   `expected_lifecycle_version` is a structural `invalid_payload` with no mutation.
5. An empty legacy `constraints` field is an unknown native top-level field and is rejected as
   `invalid_payload` with no mutation.
6. Direct Bank release remains supported. Repair only its timing-v2 publication prerequisite and
   preserve the receipt, current escrow and stored-status assertions.
7. Cross-table bid constraints become receiver-owned native state admission. After a real native
   acceptance, a later bid with the current version but required `open` status must fail as
   `invalid_status`, with no bid, operation, event or counter mutation.

### `test_t072_constraints_gaps.py`

1. CAS-04 now proves structural precedence: an injected legacy `constraints` field is rejected as
   `invalid_payload` before missing-task lookup, with no write.
2. CAS-05 sends a valid native command and preserves the domain `task_not_found` outcome.
3. CAS-09 keeps the supported asset cross-table constraint. Its non-open prerequisite is created
   through real bid/accept operations; the rejected asset leaves the database unchanged.
4. CAS-10 keeps supported Court claim-status constraints. It creates a real submitted task,
   claims a dispute, persists the matching Court claim, finalizes the dispute, then proves the
   constrained rebuttal and Board timestamp projection commit together.
5. CAS-07 remains a direct Bank receipt-ownership proof and preserves the true winning operation.
6. CAS-11 moves invalid constraint-type validation to the still-supported asset endpoint and
   proves structural rejection without mutation.

## Verification boundary

After each edit, run the focused tests. Once frozen, run both files together, their directly
adjacent native/rebuttal/financial contract tests, Ruff, format check, codespell, diff check and
final hashes. This scoped evidence does not replace the full Gateway or root CI gates.

Final scoped evidence after the two test files were frozen:

- `/private/tmp/agent-economy-mp07-gateway-constraints-final.log`: 13 passed.
- `/private/tmp/agent-economy-mp07-gateway-constraints-broader.log`: 69 passed across the two
  reconciled files plus native write retirement, rebuttal projection/linkage, native financial
  receipts and receipt current-view contracts.
- `/private/tmp/agent-economy-mp07-gateway-constraints-ruff.log`: Ruff exit 0.
- `/private/tmp/agent-economy-mp07-gateway-constraints-format.log`: two files already formatted.
- `/private/tmp/agent-economy-mp07-gateway-constraints-codespell.log`: codespell exit 0.
- `/private/tmp/agent-economy-mp07-gateway-constraints-final-sha256.log`: final scoped hashes.
