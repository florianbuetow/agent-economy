# MP-06 authoritative money guards

Status: Gateway guards implemented; public Central Bank alignment and combined gates remain open.
No T-120/T-121 closure or full-root completion is claimed.

## Authoritative writer

The initial executor retained 12 intended monetary failures and three controls before pausing for
migration repair. The parent took sole ownership of BankWriter and the new guard test module,
expanded coverage, and reproduced 39 intended failures with three controls before implementation.
These expose wrong payer (including a zero poster leg), mismatched issuance, bool/non-integer/range
errors, overflow, lifetime replay, and transaction cleanup failures.

The repair uses the existing canonical `SQLITE_MAX_INTEGER`, validates before SQL, requires exact
initial issuance, checks payer within the settlement transaction, and guards every addition with
`balance <= maximum - amount`. Exact credit and lifetime lock replays are resolved before another
addition/debit. Split credits remain atomic when both legs use the same account. Independent
connections synchronized with a barrier admit only one credit into the final available balance unit.

The first focused run passed 42 tests. The broader Gateway unit run passed 772 and exposed one
existing missing-poster-account 404 precedence failure. Two additional red cases fixed that priority
without changing an existing test. The resulting 45 guard cases plus 60 existing Bank router cases
passed. Independent review: APPROVE, zero findings; fresh 105/105, Ruff/format/Pyright clean.
The normal source-only Mypy gate also passes.

## Actual lock status and large aggregates

The lock response now requires the actual stored `status`, including `released` or `split` on replay.
Three missing-field reds with 42 controls preceded this addition; all 105 focused cases then passed.
The shared Gateway client already passes the response through unchanged. Central Bank is being
aligned separately to consume this field instead of fabricating `locked`.

A further real regression showed `DbReader.total_escrowed()` raising SQLite integer overflow when
two individually valid maximum-sized escrows were locked. One failure and 45 controls preceded the
small change from SQLite SUM to Python integer accumulation, without numeric coercion. The new
test verifies exact totals beyond one account's bound, empty totals, settlement exclusion, financial
conservation and no read-side mutation. Final focused run: 106 passed. Independent review approved
both code extensions. The reviewer also found one normative endpoint-document contradiction;
release/split docs now distinguish missing-account 404 from capacity 409 and include the bounded
failure branches. Final documentation rereview: APPROVE, zero findings.

## Remaining scope

The public Central Bank candidate now consistently rejects booleans and out-of-range values,
preserves safe range errors, retains lifetime replay behavior and consumes actual returned status.
Its initial 16 intended failures were recorded in the executor transcript (tool chunk `47742a`),
not saved as a standalone log. This is actual red evidence, but no persisted red log is claimed.
Later composed validation is archived: 76 tests passed plus clean static checks. The parent
independently ran Bank `just ci-quiet`; session14768 terminated with exit0 and its full log is archived.
No old mock fixtures required changes for the new status field.

The parent's later percentage-precedence hypothesis was incorrect: it relied on stale T-033 API
prose rather than the complete route. The approved MP-03 design and current code authenticate the
strict signature/time profile before domain validation. Actual wrong-signature and expired-token
controls confirmed that order. The new test that expected a boolean percentage to beat invalid
signature was an incorrect expectation, not a product defect; its failing log is retained with
that limitation. The corrected 28-case suite proves invalid signature403, authentic expiry401,
valid-signature percentage errors, and fail-closed missing/invalid Gateway status. No production
authentication change was made. The parent reconciled stale API/auth prose to the approved MP-03
contract and requested independent documentation review.

Durable release/split replay receipts remain MP-04 scope. Gateway CI, current-code live verification
and a fresh unchanged-source root gate follow final reviews. The latest root13 gate predates these
changes.
