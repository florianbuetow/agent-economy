# MP-07 Gateway Court router fixture reconciliation

## Scope

Reconcile these DB Gateway Court router suites with the MP-07 task publication and
native rebuttal provenance contracts:

- `test_court.py`
- `test_court_reads.py`
- `test_t072_court_reads_gaps.py`

Court claim, claim-status, ruling and ruling-deletion operations remain Court-domain
operations. This work does not route those operations through Board lifecycle commands or
change product semantics.

## Untouched baseline

Source snapshots and SHA-256 hashes are stored under
`/private/tmp/agent-economy-mp07-gateway-court-router-before*`. The targeted baseline is
`/private/tmp/agent-economy-mp07-gateway-court-router-baseline.log`:

- 42 tests collected
- 24 failed and 18 passed
- every failure followed a `400 missing_field` response from `POST /board/tasks`
- subsequent claims failed their task foreign key, hiding the real Court behavior

Each local task fixture omits the required poster-selected
`acceptance_window_seconds`. It also declares `created_at` as
`2026-02-28T10:15:00Z` and a one-day bidding duration while supplying the inconsistent
cutoff `2026-03-01T10:00:00Z`.

## Stage 1: publication setup

For each local `_create_task` fixture:

1. supply an explicit `acceptance_window_seconds` value;
2. make `bidding_deadline` equal `created_at + bidding_deadline_seconds`;
3. retain and assert the task-creation response is `201` before returning fixture IDs;
4. rerun the complete three-file target to expose any independent Court contract failures.

All existing Court behavior assertions remain unchanged in this stage.

## Stage 2: rebuttal provenance

A successful Court rebuttal now requires real persisted Board history: publication, native
bid, acceptance, submission and dispute, followed by the matching persisted Court claim.
Use the approved native Board history helper only after its API and ownership are frozen.
Do not simulate status/provenance with raw SQL, generic status writes or fabricated operation
rows. Preserve existing negative error assertions unless the native contract establishes a
different earlier validation precedence; record any such change explicitly before editing.

Stage 1 leaves 34 passing tests and eight rebuttal failures. Seven are in
`test_court.py`; one is the successful rebuttal read in
`test_t072_court_reads_gaps.py`. The successful cases currently file a Court claim against
an otherwise-open task, so authoritative rebuttal admission correctly returns
`409 invalid_status`.

The Stage 2 fixtures will hold only the Gateway business clock at
`2026-02-28T12:00:00Z` while leaving the JWT clock real. They will use the frozen
`tests.fakes.native_board_history` HTTP primitives with the existing 2026 task and parties
to persist bid, acceptance, asset, submission, pending dispute, matching Court claim and
dispute finalization before submitting a rebuttal.

Two legacy negative expectations conflict with the new authoritative admission order:

- an unknown `claim_id` is rejected as `404 claim_not_found` before any foreign-key insert;
- an existing disputed claim submitted by someone other than its worker is rejected as
  `403 forbidden` before any foreign-key insert.

Those cases will be renamed and updated to assert the explicit admission errors. This is a
recorded contract-precedence reconciliation, not assertion weakening. The valid, optional
claim-status, duplicate, atomicity, parser, read and all non-rebuttal Court assertions stay
unchanged.

## Verification

Run the complete three-file target after each stage. Run Ruff, the formatter check and a
snapshot diff for the owned files. This bounded proof does not replace the complete DB
Gateway or isolated root quality gates.

## Result

- Untouched target: 24 failed, 18 passed; all failures followed invalid task publication.
- Stage 1: 8 failed, 34 passed; every remaining failure was a rebuttal without disputed
  Board provenance.
- Final target after native history reconciliation: 42 passed in 3.09 seconds.
- Native Court/rebuttal controls: 24 passed across rebuttal projection, claim linkage and
  Court operation contract suites.
- Ruff: all checks passed; all three owned files are formatted.
- The frozen native history helper remained byte-identical at SHA-256
  `290f136b3e186c1609309d933cec2aa6fcb646ac29b8339d43d57eaad291d133`.
- No application source, shared fixture, product semantics, dependency, skip or suppression
  changed. The complete Gateway and isolated root gates remain pending.
