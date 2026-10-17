# MP-04 implementation checkpoint

Status: in progress. Task 6.1's bounded baseline is red-proven; T-116 and T-117 remain OPEN.
No receipt persistence, schema upgrade, caller migration or integrated success is claimed here.
Root15 remains the last successful whole-repository gate, before these new red tests.

## Independently reproduced baseline

New file: `services/central-bank/tests/integration/test_mp04_replay_baseline.py`.
Both owner and parent ran it through public Bank HTTP, production LedgerDbClient, signed Gateway
HTTP, actual writer and isolated SQLite. The eight intended failures are retained in
`baseline-owner-red.log` and `baseline-parent-red.log`; three unchanged integration controls pass
in `baseline-controls-green.log`. Parent session 7948 is terminal exit 1 (expected red).

- Release and 0/50/100 percent split commit before a transport deliberately loses the successful
  Bank reply. Database status, balances and positive ledger/event rows prove commitment. Exact
  retry returns 409 on the baseline, violating convergence.
- Split 0 uses an identified worker with no Bank account. Required future receipt retains its
  zero logical leg with null transaction/balance; two logical legs are distinguished from the
  number of actual positive transactions.
- Reusing one operation ID for changed credit or lock intent moves money again. Required
  assertions demand operation_id_conflict, the true winning ID and an unchanged complete
  financial/event/receipt snapshot.
- Settled lock replay lacks the required immutable receipt, while its existing fresh status
  projection is correct. The test compares receipt identity separately from current state.
- Successful release lacks a native receipt. Required identifiers must join to actual SQLite
  facts and the request SHA-256 must equal an independently calculated canonical business object.

Independent critic re-review: APPROVED for this bounded slice, no remaining false-red or
weakening. Earlier provisional logs are not the approved proof: they included an invalid
whole-body lock equality expectation, which was corrected before implementation. This approval
does not cover untested issuance, legacy history, concurrency, migration or rollback behavior.

## Canonical request support

New `financial_requests.py` provides strict canonical operation ID validation, fixed per-kind
business fields, sorted compact UTF-8 request hashing and strict stored-request parsing. It is
not yet wired into authoritative commands. The public failing proof above preceded this support
code. `request-contract-green.log` contains 47 passing independent vectors/type/range/Unicode/
unknown-field/version tests. Ruff, format, mypy and Pyright/codespell checks are being retained
with the final source review; no full feature completion follows from helper tests.

## Remaining work and ownership

The concrete financial and v2 schema contracts are in `docs/plans/2026-10-16-mp04-receipt-contract.md`
and `docs/plans/2026-10-16-mp04-schema-v2.md`. Separate Gateway identity reds and sidecar/audit
proofs are in progress. Migration, native writer integration, typed public/shared responses,
consumer identity propagation, opposing-command races and atomic rollback proof remain required.
Startup will not admit v2 writes until receipt ownership is enforced. Preserve frozen v1 and
its independently proven v0-to-v1 migration. Finish independent review and fresh root CI before
closing T-116/T-117. Goal remains active; checklist progress is 37/117.
