# Parent independent demo producer review

Parent inspected clients, engine intent ownership, response helper and tests. One finding:
flat bid acknowledgment accepted any nonempty bid ID, although public Board derives bid ID from
operation ID. Author added a genuine failing foreign-ID control before requiring that exact ID;
success doubles now follow the public derivation. Original flat-shape assertion is preserved.
Parent independently ran full tools just ci-quiet after the fix: handle34492, exit0,202 tests pass.

Validated scope: explicit posting terms, strict caller-supplied native operation identities,
engine-owned immutable per-step retry snapshots, same-task/bid/actor binding, no advancement on
pending/unknown/foreign bodies, exact operation hash/effect/result checks, and tracking idempotence.
Producer tests are mocked public HTTP boundaries, not real Bank/Court settlement evidence. This
component does not claim posting recovery, durable restart, automatic retry, or full root CI.
