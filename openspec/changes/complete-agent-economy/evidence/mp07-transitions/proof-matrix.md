# MP-07 proof coverage and remaining work

Production baseline: a6af0be. This matrix describes test evidence, not completed implementation.
All new lifecycle code remains unimplemented. T-122/T-123 stay open until their own full gates.
Final parent logs and source hashes are in final-baseline-proof-index.json. Board:24 exercised
failures/19 controls; Gateway:3 assertions for2 absent capabilities/2 controls. All handles terminal.

| Obligation | Current evidence | Remaining proof |
| --- | --- | --- |
| Competing acceptance, accept/cancel, approve/dispute | Atomic three-case parent red; all competing actions succeed | Green serial outcomes through final native commands |
| Submit/expiry and review actions/autoapproval | Three deterministic parent reds at exact equality | Gateway clock/version predicates and real settlement evidence |
| Accept/acceptance expiry | Final typed-error parent red; independent review approved | Native version conflict, no losing assignment/event |
| Bid at cutoff, bid after acceptance, independent bidders | One parent late-bid red; two passing controls | Gateway transaction clock; bid admission during active claim |
| Explicit poster window | Eleven final typed-error parent assertion reds and two controls; review approved | Deliberate stale counter and overflow cases, green producer behavior |
| Public bid/accept replay, changed identity, stale version | Five parent reds, including lost committed responses | Claim/finalization response loss and pending effect replay |
| Native Gateway operation route | Signed real-writer control and missing route/policy red; repaired source approved | Later claim/replay/authority/finalization assertions are unexecuted |
| Authoritative finalization | Reviewed persisted-evidence contract; prepared boundary scenarios | Matched Bank/Court completion, absence versus mismatch, winner receipt, concurrency, stable blocked conflict |
| Valid v2 migration | Valid fixture control and missing migration API red | Preservation assertions have not executed; source/backup/live storage/ownership proofs remain required |
| Invalid migration histories | Required rejection sequence is prepared, fails at absent API; review approved | Exact source-version, cutoff, bid/worker and Court-linkage paths not yet reached |
| Legacy pending effects/observations | Three parent source-history/backup and three provenance-validator controls pass; exact contract reviewed | Migration API absent; migrated assertions unexecuted; refund/ruling legacy kinds still need proofs |
| Court loss/retry and ruling linkage | Contract only | Delayed first claim versus retry; exact parties/reason; ruling task/dispute linkage; unrelated actor rejection |
| Other writers under claim ownership | Contract only | Assets, bids, rebuttals and phase writes cannot bypass native claim or unresolved legacy observation |

## Fixture evolution required for the native producer

Existing reds deliberately use current interfaces and do not fail at absent v3 imports/columns.
When the producer lands, preserve archived original proof and reconcile setup explicitly:

- Supply explicit acceptance windows and operation/version fields to old valid fixture requests.
- Inject the SAME task-domain clock into the Gateway transaction clock. Do not retain a Board-only
  fake time while the Gateway samples a different real time, or weaken exact cutoff assertions.
- Recording Bank/Court boundaries prove attempted calls today. Finalization requires persisted
  effect evidence. Replace those bounded success doubles with signed real Gateway effect adapters
  or real service fixtures before expecting native transitions to finalize; retain losing-call
  assertions and exact histories. Never add a production bypass for a test double's success body.
- Label direct Gateway/SQL fixture preparation separately from public journey proof. The Core
  release lane still requires complete human/external-agent workflows through public interfaces.
- Missing interfaces are counted as missing capabilities, not as independent proof that every
  assertion later in the scenario has run. No required case is skipped or xfailed.

Root19 is the last full green baseline. A full root gate on intentionally red tests is not a
completion gate; run targeted baseline proofs now, then fresh affected and root gates after fixes.
