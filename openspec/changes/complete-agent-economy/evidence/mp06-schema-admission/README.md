# MP-06 schema slice: work in progress

The first schema candidate passed 34 focused tests, but independent review requested changes.
Its author's initial red was an unlogged missing-module collection failure, not behavioral proof.
The earlier retained T-120 proof covers actual unknown/partial-schema admission failures; the new
candidate does not inherit proof for every added scenario.

The parent then added `test_schema_review_regressions.py` and reproduced **nine behavioral failures**
against the candidate before repair:

- Missing or future metadata rows still classify as current.
- Changed CHECK/default string literals, conflict policy and FK deferral still match current.
- A commented-out balance check accepts a negative balance yet classifies as constrained current.
- A block-comment-prefixed COMMIT leaves a metadata table after failed bootstrap.
- A close failure masks the primary schema-admission error.

The frozen-pass review also required canonical current-v1 column order (while preserving both known
legacy layouts), effective connection-setting validation and primary-error preservation on rollback.
The expanded pre-repair suite recorded 23 failures and 34 controls. After repair, all 58 focused
cases passed, including the unchanged nine parent regressions; the separate compatibility/architecture
selection passed 74 cases. Independent full re-review approved the schema slice with no open issues.

The parent independently ran schema, review regressions and the reconciled writer/lock suites:
**83 passed** in 2.03 seconds. This approval covers classification, the two supported legacy layouts,
transactional fresh bootstrap and connection readiness. It does not prove explicit migration,
financial command guards, or the integrated root gate.

## Reconciliation of five older test expectations

The MP-06 contract was recorded in the Gateway API/test specifications before reconciliation.
Two valid maintenance-lock fixtures now write current schema bytes to their isolated configured
path; all exclusion and cleanup assertions are unchanged.

Three old tests expected partial legacy tables to be silently altered at startup. That behavior is
explicitly superseded by MP-06's complete-schema/verified-migration requirement. Their replacements
require startup rejection, unchanged DB bytes and columns, preserved ledger rows, and stable repeated
rejection. Fresh-schema and existing read/write controls remain. This does not prove explicit
v0-to-v1 migration; that entry point and its acceptance cases are still required.

The parent first ran the three affected test files: **25 passed**, with Ruff clean. See the retained
`agent-economy-mp06-schema-fixture-reconciliation.log`. Neither task 5.2 nor 5.3 is closed.
