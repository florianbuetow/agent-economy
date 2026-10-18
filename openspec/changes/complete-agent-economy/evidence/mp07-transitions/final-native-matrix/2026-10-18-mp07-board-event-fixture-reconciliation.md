# MP-07 Bank event fixtures in Task Board integration proofs

Production Bank events use `central-bank`. The corrected native finalizer now
requires that exact source. Several Task Board integration helpers still emitted
the older `bank` value while otherwise exercising real Gateway persistence.

The first targeted post-correction run passed submission, then failed approval
at effect finalization before its injected-response-loss assertion. This was a
fixture/producer contract mismatch; it is not another product red. Seven files
then changed ten positive event fields to the real producer value. The combined
native integration run reached 55 passes and eight remaining failures, all
using the `_event("bank", ...)` helper in `test_mp07_native_deadlines.py`.
Its four positive event calls were corrected as well. No outcome, race, money,
version, event-count, or recovery assertion was changed.

All eight original files and SHA-256 hashes are preserved in
`/private/tmp/agent-economy-mp07-board-event-fixtures-before/`, with per-file
replacement counts in `index.json`. No negative event-source control changed.

Evidence:

- `/private/tmp/agent-economy-mp07-board-event-fixture-red.log`
- `/private/tmp/agent-economy-mp07-board-native-matrix.log` (55 pass, 8 fixture failures)
- `/private/tmp/agent-economy-mp07-board-deadline-event-green.log`

This fixture repair is separate from the second live Bank/Gateway product
mismatch: real Bank release/split events may have `task_id=null`. Authoritative
task ownership comes from the escrow referenced by the receipt; receipt audit
must preserve that strict binding and reject any contradictory non-null event
task. That repair and fresh live/root verification remain separately tracked.
