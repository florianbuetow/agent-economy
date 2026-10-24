# MP-07 event and atomicity fixture reconciliation

Repair explicit acceptance-window/coherent-cutoff prerequisites in the event and claim-uniqueness
suites. Preserve all event task/agent, unique-claim-versus-task classification, money atomicity,
concurrency, HTTP misuse and security assertions. Link the direct-write task fixture to its actual
escrow task ID instead of generating an unrelated task ID.

ATOM-05 must still prove an actual foreign-key failure rolls back a rebuttal, claim status and now
its Board timestamp. A nonexistent worker is rejected by authoritative admission before inserts,
so that old invalid input no longer reaches the transactional failure boundary. Prepare real native
bid/accept/asset/submit/dispute plus matching persisted Court claim/finalization. Send a valid worker
rebuttal, and inject the fault at event insertion by invoking the real event writer with an unknown
actor. SQLite itself must raise the FK failure. In the injection assert the rebuttal row, projected
Board timestamp and changed claim status are present inside the uncommitted transaction. Afterwards
retain the original FK/error/status/count assertions and strengthen to full database snapshot equality.

This is a deliberate transaction fault probe, not a claim that a normal caller can bypass worker
admission or control normalized event identity. Keep original2026 times coherent with explicit
business-clock steps10:30 acceptance,11:30 submission,12:00 dispute,13:00 rebuttal. No product code,
ordinary runtime data, fabricated receipt/operation history, or suppression changes.
