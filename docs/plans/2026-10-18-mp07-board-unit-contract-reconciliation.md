# MP-07 remaining Board unit contract fixtures

The parent full unit run collected636 and ended629 passed /7 failed (handle4633, exit1).
All214 scoped source and202 ordinary log hashes remained unchanged. Raw log, snapshots and four
original test sources are preserved in evidence/mp07-transitions/unit-fixture-reconciliation.

Five failures arise before their original observations: two valid config cases and two MP03 key/
signer startup cases omit deadline_evaluation.actor_id; the MP06 active maintenance fixture also
omits that attribute. Add an explicit fixture actor only; preserve original key/signer/maintenance
assertions. The extra-field negative config must include the actor too, so its intended invalid
field remains the cause. These are fixture-contract reconciliations, not new product defects.

Two response wiring tests expect TaskResponse for mutation routes. The reviewed native API requires
TaskMutationResponse, which retains task fields and requires an operation envelope. Update only the
six mutation route type expectations and import; keep creation, read, bid, asset, field assertions.
The existing native projection/router cases already verify envelope bodies and pending HTTP status.

Run the four affected files plus explicit deadline-actor negative controls first, then full units.
No defaults, skips or relaxed original assertions are introduced; canonical package closure still
requires the remaining proof matrix and fresh root CI.
