# Schema implementation handoff

Read-only repo mapping identified the existing extension points. No production edits have landed.

- schema.py owns SchemaState, resource selection, exact catalog classification and metadata audit.
  V3 must be additive; preserve frozen current_v1.sql and current_v2.sql byte-for-byte.
- DbWriter initialization admits/bootstrap-checks current schema, integrity and receipt ownership.
  Add task-contract/operation audit here; recognize older schemas as explicit migration-required.
- migration.py delegates v1-to-v2 to receipt_migration.py. Add a separate v2-to-v3 path preserving
  the proven backup binding, maintenance lock, transaction, rollback/uncertain commit, observation
  and evidence semantics. Never chain migrations at startup.
- recovery_cli/__main__.py owns explicit subcommands, dispatch and exact verified-result admission.
  The new path must require inventory, backup root/ID, work root and busy timeout explicitly.
- Add a frozen current_v3.sql and narrowly scoped sidecar/migration resource. Update the configured
  current bootstrap resource only with the native producer and consumer rollout accounted for.
- Bootstrap SQL validation currently allows table/index creation, metadata and financial-cutover
  seeds. Any new seed shape needs an explicit positive/negative validator proof.

Historical migration proofs must keep testing their original target and preservation contracts.
Tests asserting which version is CURRENT at service startup need explicit contract reconciliation;
do not broadly rewrite all v2 assertions or leave obsolete future-version assumptions unexamined.
The MP-07 tests already separate valid source/backup controls from unexecuted post-capability
preservation/classification assertions. Financial receipt ownership and its cutover attestation
remain unchanged through the task migration.

Use docs/plans/2026-10-17-mp07-transition-contract.md and proof-matrix.md for actual requirements.
This mapping grants no ticket closure and does not replace pending failure/authority/Court proofs.
