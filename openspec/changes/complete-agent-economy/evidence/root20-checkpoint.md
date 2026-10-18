# Root20 — fixture startup failure, no closure

The fresh root `just ci-quiet` run stopped in Central Bank unit tests. Parent
session84140 exited1. The root log and exit file are retained under
`/private/tmp/agent-economy-root-ci-20.*` pending archival.

Project structure, shared-library CI and Identity component CI passed. Central
Bank reached270 passing tests, one failure and seven setup errors. All eight
failures came from the same positive Gateway contract fixture omitting required
`board_authority`; they did not reach their original ledger/cleanup assertions.

All1059 source/config hashes and202 ordinary-log hashes were byte-identical
before and after the gate. The comparison is
`/private/tmp/agent-economy-root-ci-20-comparison.json`. The source freeze ended
only after terminal status and hash capture. No commit or push occurred.

The parent repair adds an explicit generated fixture platform key and deadline
actor to that positive config, and makes its unused log directory explicitly
temporary. Production configuration remains strict. Original source bytes are
preserved at `/private/tmp/agent-economy-root20-bank-fixture-before.py`, SHA-256
`85d63298f41a15b5ef53dfdd7f333ef53988dc1d7faf88382c50c09abf00c7da`.
No ledger or cleanup assertion changed.

A read-only inventory also identified missing authority in three positive
Identity/Bank integration startup fixtures. Those were not reached by root20;
they are being reproduced and reconciled before the next full run. Do not
attribute their future failures to this gate.

The complete goal and MP-07 tickets remain open. Root20 provides a failed gate
and unchanged-input evidence, not release or package-completion proof.
