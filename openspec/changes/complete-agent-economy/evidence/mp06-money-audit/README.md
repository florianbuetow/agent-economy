# MP-06 read-only financial audit

`audit_money(connection)` returns frozen financial findings and explicit totals; it never authorizes
migration or modifies the supplied database. It checks stored SQLite money types, signs and signed
64-bit bounds, supported transaction/escrow vocabulary and nonempty TEXT identifiers without identity
coercion. It reconciles account ledger nets and global issuance against balances plus locked escrow
using Python integers, including totals larger than SQLite's aggregate range.

Every escrow is linked to its exact payer/task/amount lock leg, release legs are bound by escrow ID
and checked against status/count/sum, and existing Board tasks must match escrow task/payer/reward.
Bank-only escrows without Board tasks remain valid because locking precedes posting. No lifecycle
status equivalence or worker-recipient policy is invented. Two positive split legs must include the
escrow payer; one full worker payout, one payer refund and two payer-directed legs remain valid.

Initial new-API red proof consisted of ten failures inside collected test bodies. It is not described
as historical behavioral proof. Review then found aggregate-balanced linkage corruption; eleven new
behavioral failures and thirteen controls were captured before repair. A final balanced wrong-payer
split produced one failure with 31 controls before its narrow repair.

Independent full review approved the final audit with no open findings: **32 focused cases and 90
combined schema/audit cases passed**, with Ruff, mypy, Pyright and Bandit clean. The parent separately
reran the 90 cases; its retained log records the result. Read-only tests cover connection state,
rows, database bytes and backup sentinels. Invalid linkage explicitly makes dependent reconciliation
unavailable rather than substituting zero.

This is an admission prerequisite only. The actual migration command, authoritative writer guards,
full item-specific acceptance and the fresh integrated root gate remain required before T-121 closes.
