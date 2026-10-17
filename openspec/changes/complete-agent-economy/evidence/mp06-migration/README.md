# MP-06 migration implementation evidence

Status: implementation, independent reviews, component CI and final live proof have passed;
root14 is running against 886 frozen source/config files. T-120/T-121 and tasks 5.2–5.5 remain
open until that gate is verified. The latest completed root gate is root13. No
commit, push, branch switch, merge, rebase, or cherry-pick has been performed.

## Candidate and test-first evidence

The candidate public API is `migrate_v0_to_v1(inventory, *, backup_root, backup_id, work_root,
busy_timeout_ms)`. It freshly enters backup/source authority, audits the verified read-only clone,
then performs the fixed v0-to-v1 rebuild. The schema metadata keeps its approved five fields;
the migration plan checksum belongs in external evidence. Artifact audit v1 remains unchanged.

`agent-economy-mp06-migration-red.log` retains the initial 16 failures. These are API-absence
proof, with two legacy-fixture foreign-key setup failures; they are not 16 demonstrated unsafe
migration behaviors. The fixture setup was corrected separately. The initial candidate passes
40 dedicated tests and 152 combined migration/evidence/audit/schema tests. Further behavioral
review regressions are required before accepting the candidate.

The parent CLI has 14 genuine missing-command failures followed by 24 passing new and existing
CLI cases (`agent-economy-mp06-migration-cli-{red,green}.log`). It requires all five explicit inputs,
delegates once, emits redacted structured outcomes, and never turns commit uncertainty into exit0.
Independent CLI review: APPROVE, zero findings; independent fresh 24/24 and Ruff passed.

The public evidence-write API has initial missing-API reds, then a genuine embedded-NUL
validation regression. Final independent review: APPROVE, zero findings; 24 evidence plus 114
recovery regressions passed, with Ruff/format/source-mypy/Pyright/Bandit clean. It writes an
exclusive 0600 file inside the already leased 0700 retained scratch, checks authority around I/O,
syncs and reads back, and returns a stable visible path after closure. No extra clone copy or
scratch deletion is introduced. Darwin clone paths obtained through F_GETPATH remain stable;
Linux descriptor-backed clone paths require their descriptor to remain live.

## Live workload proof

The parent added `tests/runtime/test_migration_live_workload.py` without changing existing tests.
It creates actual signed public API receipts in an isolated seven-service runtime: registration,
account, task, bid, acceptance, asset upload and submission. After stopping owned processes, the
fixture rebuilds the disposable database into frozen supported v0 while independently comparing
all values, SQLite storage types, rowids and event sequence. This is a controlled legacy-layout
fixture, not evidence of upgrading a historical deployed installation.

The test runs the actual CLI in an owned subprocess, verifies exact v1 data and backup provenance,
restores the immutable pre-migration backup into a separate exact-v0 target, and checks unchanged
source keys/assets. It restarts the source stack with the same keys and identities, reads the
submitted task and asset through signed APIs, approves it, and checks the exact 100-credit payout.
The restored target is inspected offline; this does not close MP-21 operational restoration.

- Live1: fixture setup failed before migration because the frozen schema re-enabled foreign keys.
  The initial log is retained honestly; it is not migration red proof.
- Live2: corrected fixture, one test passed in 10.36s.
- Live3: final explicit read-connection closure and assertion helpers, one test passed in 10.82s.
- Independent live-test review: APPROVE, zero findings; Ruff/format clean and evidence reviewed.
- Six unique before/after runtime manifests are archived. `live-cleanup.json` records 43 distinct
  owned groups across the three runs, all drained and absent in a fresh host process check.

## Open review findings

The migration transaction review requested changes despite 40 passing cases:

1. Rollback/close failures need explicit cleanup-uncertainty reporting and proven writer disposal
   before releasing maintenance authority; an exception alone does not retain a context manager.
2. Writer-open and read-only close failures need normalized, primary-preserving error handling.
3. Legacy fixtures need non-null bid, event, dispute and rebuttal links plus exact metadata-hash
   assertions to prove all rebuilt paths and both legacy column orders.

These findings were repaired with behavioral regression evidence and independent approval. Later
combined-fault review also fixed post-commit close plus evidence failures so the original cleanup
failure, observation, cause and safe codes survive. Final migration review found zero issues.

## Final component and live checkpoint

Gateway component CI first caught prohibited type suppressions in the explicit legacy-mode SQLite
connection and then a prohibited default argument on `MigrationError`. Both failed logs are retained.
The parent removed suppressions by selecting explicit `autocommit=True` and SQL BEGIN/COMMIT/ROLLBACK,
with native SQL rollback fallback and native close. A new behavioral probe first demonstrated why
DB-API commit is insufficient in that mode, then verified a single real SQL commit and fresh v1.
The commit-then-error fixture and three retained-connection fixtures were adjusted to the configured
mode without weakening their assertions. An intermediate three-case failure was fixture mode
rejection, not failure of native disposal; its log remains available.

`MigrationError` details are now required at all call sites. No typing suppression, default-mode
dependence or default diagnostic mapping remains. Independent reviews approved the mode and the
mechanical signature repair. Final migration plus mode tests: 54 passed; adding CLI contracts:
68 passed. Focused Semgrep reports zero findings. Parent Gateway `just ci-quiet` session3567
terminated with exit0; the final full component log is archived here.

Startup health and Gateway/public Bank monetary guards are independently approved, with evidence
in the startup suites and `../mp06-money-guards/`. Final live4 ran after all migration and money
changes: one test passed in11.32s. Its immutable backup restoration and signed continuation use
the same original identities and assets. The updated cleanup report covers eight unique manifests
and60 distinct process groups from four proof runs, all drained and absent. Root14 is the required
fresh combined gate; no full MP-06 ticket closure is claimed before its terminal evidence.
