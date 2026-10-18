# MP-07 writer retirement and rebuttal projection

Native task operations now own lifecycle mutations. The remaining generic bid/status endpoints
must reject known unversioned writes with `native_operation_required`, preserving active native
and unresolved legacy owner precedence. Reject unknown/malformed update shapes explicitly.
All rejection paths must roll back and release their SQLite transaction so native work can continue.

Task publication is the sole initial-state bootstrap: status open, zero bids, no pending escrow
projection or dispute, lifecycle version0 and no accepted/submitted terms. Those zero/null values
are derived domain invariants. Supplied incompatible values fail before any task/sidecar/event write.

The last generic Board write is the rebuttal observation timestamp. Move that factual projection
into the Court rebuttal transaction, then remove Task Board's second post-Court write and retire the
generic status path completely. No new route or operation identity is required.

The Court writer derives task and parties from the persisted claim, checks native/legacy ownership,
and requires an exactly linked disputed task and responding worker. It rejects duplicate/ambiguous
rebuttal history and conflicting preexisting Board markers. In one transaction, it inserts the Court
rebuttal, projects its actual submitted_at into the Board row, applies existing Court constraints
and emits the single Court rebuttal event. Board lifecycle_version is unchanged. Any failure rolls
back all rows and events. Court state-machine fencing and receipt-grade rebuttal replay remain MP-10.

Tests precede each change: raw-writer/publication bypasses, reusable transactions after rejection,
correct atomic projection, owner/party/state rejection, conflicting history and injected rollback.
Existing native bid/accept/review/ruling and deadline tests remain controls. Unit history fixtures
may prepare stored facts directly when an obsolete public writer is removed, with that setup
explicitly separated from public workflow proof and all behavior assertions retained.

Sequence: fix the independently reproduced transaction leak; implement and verify Court projection;
remove the second Board rebuttal write; reject/delete the obsolete generic bid/status implementation;
reconcile affected callers and fixtures; finish original race/migration/recovery and root gates.
No MP-07 task closes from this bounded work alone.

## Fixture and review reconciliation

The three unit command-history builders in `test_mp07_operation_regressions.py`,
`test_mp07_court_operation_contract.py` and `test_mp07_pure_finalization.py` previously seeded a
bid through the generic writer. They now explicitly construct the same historical bid, counter
and event rows without introducing an extra native operation. Behavior assertions remain intact.
The shared signed HTTP setup in `routers/test_mp07_task_operation_boundary.py` instead submits a
real native bid and requires its completed receipt. Its downstream transition and guard assertions
remain intact. The initial eight failures at that retired setup route are fixture reconciliation,
not additional product-red proof.

Independent Court review found two further admission/acknowledgment gaps. Two new unit cases
proved that changed frozen Court reason/deadline facts were accepted; a new live case proved
malformed acknowledgment JSON escaped after the atomic commit. Admission now compares both frozen
facts and the Board bridge maps JSONDecodeError narrowly to the existing Court-unavailable error.
The live bridge helpers are public test helpers for reuse by the decode test; their assertions
and original red log are retained. This does not add receipt-grade rebuttal replay or judging fences.
