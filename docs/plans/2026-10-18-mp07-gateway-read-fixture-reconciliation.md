# MP-07 Gateway read/status fixture reconciliation

## Scope

Reconcile task-publication setup in these existing DB Gateway router suites with the
MP-07 native timing contract:

- `test_reputation.py`
- `test_reputation_reads.py`
- `test_t072_reputation_reads_gaps.py`
- `test_court_status_update_integrity.py`
- `test_t072_court_status_and_ruling_delete_gaps.py`

The Court status and ruling-delete endpoints remain Court-domain operations. This
reconciliation does not route them through Board lifecycle operations or change any
product behavior.

## Untouched baseline

The source snapshots and SHA-256 hashes are stored under
`/private/tmp/agent-economy-mp07-gateway-read-fixtures-before*`. The targeted baseline
is `/private/tmp/agent-economy-mp07-gateway-read-fixtures-baseline.log`:

- 28 tests collected
- 17 failed and 11 passed
- every failure followed a `400 missing_field` response from `POST /board/tasks`
- downstream feedback writes then failed foreign-key checks and Court claims were absent

The fixtures predate the required `acceptance_window_seconds` publication field. They
also declare `created_at` as `2026-02-28T10:15:00Z` and a one-day bidding duration,
while supplying the inconsistent cutoff `2026-03-01T10:00:00Z`.

## Change

For each local `_create_task` fixture:

1. supply an explicit poster-selected `acceptance_window_seconds` value;
2. make `bidding_deadline` equal `created_at + bidding_deadline_seconds`;
3. retain and assert the task-creation response is `201` before returning fixture IDs;
4. preserve every existing feedback, read, Court-status, integrity, and ruling-delete
   assertion unchanged.

No shared helper, application source, product semantics, dependency, skip, or suppression
is added.

## Verification

Run the complete five-file target, then Ruff and the formatter check for the five owned
files. Compare the resulting diff against the untouched snapshots to confirm that only
fixture publication inputs and setup-success assertions changed.

## Result

- Targeted baseline: 17 failed, 11 passed. Every failure was downstream of invalid task
  setup; no target-domain assertion was reached for those cases.
- Reconciled target: 28 passed in 1.87 seconds.
- Ruff: all checks passed.
- Ruff formatter check: all five files already formatted.
- Snapshot comparison: each file changed only its local task-publication response capture,
  explicit acceptance window, corrected bidding cutoff and setup-success assertion.
- Limitation: this bounded proof does not certify the rest of DB Gateway or the repository;
  those broader gates remain part of the active MP-07 and root verification work.
