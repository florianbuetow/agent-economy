# MP-07 legacy escrow helper retirement

Current production inventory finds one caller of `release_task_escrow`: failed publication
compensation in TaskCreationCoordinator. The ruling-derived and flag-based retry helpers have no
production callers. Native lifecycle coordinators deliver the Gateway-owned financial intent through
`release_escrow`/`split_escrow`, then finalize from persisted evidence.

Retain raw release/split transport behavior and its exact caller-supplied operation identity. Replace
the broad provisional helper with `release_posting_compensation(task_id, escrow_id, poster_id)`.
It retains the existing deterministic `task-settlement` / `posting_compensation` identity and source
reference, so this cleanup does not change the current compensation receipt contract. General
ambiguous/same-poster posting recovery remains MP-05.

Remove unused ruling-derived helpers and status/flag-based retries. EscrowCoordinator no longer
owns task storage or a logger; its constructor takes only the Bank client. Update its real and test
construction sites mechanically. Do not add compatibility shims or reintroduce generic task writes.
The sweep also covers the compensation mock in `test_mp07_creation_window.py`; its current
window-validation cases do not reach rollback, so that stale interface would otherwise evade them.

Before production edits, retain the untouched three-consumer baseline and the converted test
interface failures. Preserve all raw transport success/error cases. Reconcile old retry cases to
actual native pending ownership, persisted settlement, exact replay and unsupported/unowned-state
no-effect controls. Preserve financial identity separation and ruling replay/changed-term/invalid
percentage protection through real Gateway histories. Arbitrary terminal dictionaries and fabricated
same-ID release/split success are not valid native evidence.

After the three consumers use real persistence or narrowly scoped transport mocks, delete the
unused dictionary store and correct stale references describing it as the unit-test implementation.
Retired generic client methods are a separate cleanup surface; Gateway already rejects those writes.
Run the converted groups, native recovery/router compensation controls, architecture checks and
service quality gates. Independent review and the fresh root gate remain required before MP-07 closes.
