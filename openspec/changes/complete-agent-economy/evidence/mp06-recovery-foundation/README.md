# Recovery foundation checkpoint

This directory records intermediate MP-06 evidence. Checklist progress is now 27/117 after adding
the read-only CI regression pair. The artifact
foundation is implemented and independently approved, but task 5.2 stays open until the migration
admission tests prove that a missing or invalid backup prevents a writable source connection.
Restored service startup and full operational recovery belong to the later recovery work.

## Current verified foundation

- The final recovery engine passed 79 focused recovery/read-race tests and 516 Gateway unit tests.
  Independent review approved the complete create/verify/finalize/restore implementation with no
  unresolved findings. The parent independently ran Gateway component CI successfully; see
  `mp06-gateway-fd-parent-ci.log`.
- Inventory capture, target bindings, preparation, CLI/runtime integration, restored read-only
  adoption and the operations guide were independently approved. Preparation uses the recorded
  existing maintenance lock and drained owned processes. A failed workload command can still
  produce recovery evidence while retaining its nonzero exit status.
- `mp06-live-fd-final.log` records a passing actual seven-service roundtrip. Public SDK calls create
  a funded submitted task and uploaded asset. After backup, the test makes the original run's
  data/assets/keys/configs unavailable, restores to a fresh target, and verifies the same agent IDs,
  balance, submitted task, locked escrow and exact asset bytes. Read-only adoption leaves the
  restored target's bytes, modes, inodes, sizes and modification times unchanged.
- `live-fd-final-runtime.json`, `live-fd-final-inventory.json` and `live-fd-final-cleanup.json` preserve
  the final run's evidence. All nine recorded process groups were drained and absent in a fresh host
  process check. No ordinary runtime was stopped or modified for this proof.
- Verification keeps artifact integrity, snapshot preservation, identity completeness, migration
  eligibility and operational recovery separate. Migration eligibility and operational recovery
  remain false in this foundation. The baseline schema's `unknown_schema` audit finding remains
  visible; it is not silently converted into migration approval.

## Root gate and fixture reconciliation

Root attempt 11 exited 1 at the Central Bank unit suite: 174 passed and seven setup errors. The
generated positive Gateway fixture lacked the newly required `recovery` section. Its 846 selected
source/config hashes were unchanged during the run; see `root-ci-11-*` and `root-ci-11.log`.

The narrow correction adds explicit fixture-owned absolute `maintenance_lock_path` and `backup_root`
values to six valid templates: the Bank ledger contract, root integration helper, Identity/Bank/Court
integration conftests and Identity's separate live concurrency fixture. It changes no assertion,
negative fixture or production setting. The parent reproduced seven setup errors and seven passing
controls before the Bank correction, then all 14 cases passed. Both logs are retained here. The
parent's Bank component CI exited 0 after correction.

The integration executor observed the same missing-section error before editing Identity's fixture,
then passed Identity endpoints (6), Bank endpoints (3), Court integration (1), Identity concurrency
(1) and helper cleanup (5). Those outputs exist in the agent transcript, not separate log files.
Independent review approved all six fixture changes and reran Bank parity (14), cleanup (5) and
Ruff successfully. The fresh root gate supplies the integrated persisted green evidence.

Root attempt 12 exited 0: all component gates, 165 cross-service/runtime tests, 288 browser tests
and 92 economy E2E tests passed. Three existing credential-dependent skips remain outside real
provider proof. All 19 recorded groups from the two CLI-managed stacks were drained and absent.
The 858-file snapshot explicitly includes the UI source assets under `services/ui/data/web`.

The before/after comparison found three fixture files changed by CI's automatic formatter.
`root-ci-12-format-proof.json` proves each change consists only of quote normalization in the two
new recovery f-string expressions: reversing those exact quotes reproduces the recorded before
hash, and the Python ASTs are identical. The full test pass is valid evidence for behavior, but this
run does not satisfy the unchanged-source gate. T-173/T-174 record the required CI correction:
remove automatic formatting from CI, retain explicit formatting and the existing read-only style
check, prove malformed input fails without rewriting it, then rerun the integrated root gate.

## Earlier evidence and provenance

- Contract review:27 behavioral failures demonstrated unsafe path IDs, contradictory key/agent
  mappings, lock-resource topology, and missing capture boundaries; fixes yield121 focused passes
  and153 service-commons CI passes.
- Held maintenance-lock handle:28 focused passes; independent review found no blockers. Its initial
  red is a missing API, so it is not evidence that every final replacement/exception scenario was
  independently reproduced against the former implementation.
- Configuration field catalog:8 passes against actual generated service/agents configs, relative
  consumer anchors, dynamic entries, strict types and immutable captured origins. Initial red is
  an absent module. Independent review approved the catalog.
- Runtime task input:one behavioral red showed the generated config pointing to ordinary repository
  input. It now points to a run-owned, explicitly unprovisioned file. Three additional behavioral
  reds exposed invalid maintenance preflight bypassing the established owned-log error contract.
  The combined runtime/maintenance/catalog suite passes36 cases after those repairs.
- Registry capture:10 passes cover exact key/ID evidence, duplicate names, registration races,
  invalid responses and outages. Initial red is an absent module. Later live integration and
  independent approval are recorded above.
- Recovery CLI:10 cases pass after adopting the required maintenance identity contract.
- Parent independently reran service-commons CI:153 passed, exit0. Gateway CI passed on the host
  after a sandbox-only Semgrep certificate-store failure. These gates precede the current engine
  repair, so they are not acceptance evidence for later edits.
- A live seven-service disposable runtime registered identities and provisioned its own treasury,
  stopped, and produced an artifact-verified, identity-complete backup using the generated inventory.
  Eight recorded process groups were absent in a fresh host check. This is backup proof only;
  restore and operational recovery remain unverified.
- Second engine review found eight defects, including source-lock recreation,
  post-verification payload mutation, trusted state claims, private restore permissions, and partial
  schema preservation. These were repaired and reviewed, as were later publication, restored
  evidence, mode and descriptor-cleanup findings. The final approval above supersedes that review.

All changes remain local and uncommitted. No Git history operation was used.
