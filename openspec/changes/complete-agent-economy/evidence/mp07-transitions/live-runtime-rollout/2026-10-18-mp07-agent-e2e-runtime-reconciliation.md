# MP-07 Agent E2E Runtime Reconciliation

## Scope

Reconcile `test_unattended_economy.py` and `test_autonomous_acceptance.py` with the
MP-07 native mutation runtime. The production loops now require a registered SDK
client bound to an entered `FileTaskIntentStore` and a shared
`RuntimeTaskCommands` instance. Existing lifecycle assertions remain the release
contract: autonomous lowest-bid acceptance, approval and full payout, plus the
unattended approval and dispute/ruling paths with escrow-backed payout checks.

The older E2E fixture contract is superseded. Constructing `AcceptanceLoop`,
`ReviewLoop`, or `MathWorkerLoop` with only an SDK client is no longer a valid
representation of production startup, and Task Board mutation calls must supply
an operation ID and expected lifecycle version and consume
`TaskBoardCommandResponse`.

## Baseline Evidence

Original fixture bytes were preserved under
`/private/tmp/mp07-e2e-runtime-reconcile/` before edits:

- `test_unattended_economy.py.baseline` — SHA-256
  `84ec81ca5360f818492711100c84189726f226d53169f54dfac809e9e230f9c3`
- `test_autonomous_acceptance.py.baseline` — SHA-256
  `bfd1abcc091005bbca15f04ab0fe92ae2a2483a59755c44803759c7b679df01d`

Disposable live-stack baseline outcomes:

- Autonomous acceptance: failed before posting because `post_task()` required the
  explicit `acceptance_window_seconds` field.
- Unattended economy: failed during rig construction because
  `AcceptanceLoop.__init__()` required `commands`.

The first reconciled live runs then exposed a product boundary defect in both
tests: the acceptance loop tried to freeze its intent from the summary returned by
`list_tasks`, which omits `escrow_id`. `acceptance_intent()` rejected the incomplete
fact set, so the autonomous task remained open and both unattended tasks timed out.
The assertions were kept intact while the implementation owner repaired the loop
to fetch and identity-check the full task detail before freezing the intent.

After that repair, autonomous acceptance and submission reached the approval
boundary. Approval released escrow successfully, but Task Board attempted a second
operation finalization and returned `effect_evidence_conflict` (409). This remains
a product defect owned outside this fixture reconciliation; the approved-state and
full-payout assertions remain unchanged.

## Reconciled Contract

- Runtime configuration is explicit and points at each test's temporary directory.
- Journal owners are derived only after the real E2E clients are registered.
- The feeder shares one entered owner-bound command journal across acceptance and
  review; each math worker receives its own entered owner-bound journal.
- Journal leases remain held while background loops are cancelled and awaited and
  while all SDK and deterministic LLM clients are closed.
- Cleanup scope begins before the first client acquisition. Each acquired client is
  tracked immediately; all close operations are awaited even when peers fail, the
  journal is released last, and collected cleanup failures are re-raised.
- Autonomous setup/completion mutations supply stable operation IDs and lifecycle
  versions, verify their source status, and assert typed command responses.
- The unattended test still forbids direct approval, dispute, and ruling shortcuts;
  its real loops must prove approval, dispute, rebuttal, ruling, payout, and escrow
  behavior.

## Verification

Both files were run through `scripts/isolated_test_runtime.py` with disposable
DB Gateway, Identity, Central Bank, Task Board, Reputation, and Court services.
The test command was `uv run --no-sync --project agents pytest <path> -q -x`.

- Pre-repair autonomous red runtime:
  `/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-2de66e81442a467f84bd397611bb69eb-r7usmy3l`.
  One test failed at the accepted-state assertion because the incomplete summary
  projection could not build the acceptance intent.
- Pre-repair unattended red runtime:
  `/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-06a5702992f74a56bf5eec36a00425d4-nz_whh4x`.
  One test failed after 152.08 seconds at the existing approved-state assertion;
  both tasks repeatedly reached the same missing-`escrow_id` product boundary.
- Post-acceptance-repair autonomous red runtime:
  `/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-b267fd711664474f930c83acbcd44ac4-2ey04s4q`.
  Acceptance and submission succeeded; approval failed with 409
  `effect_evidence_conflict` after escrow release succeeded.
- Post-format confirmation runtime:
  `/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-475a3da26ba2443e82e3c32f49db4962-euak0k5j`.
  The same approval defect reproduced: one test failed in 0.28 seconds.

Each runtime's service evidence is under its `logs/` directory. Static fixture
verification passes: Ruff lint, Ruff format check, Pyright (zero errors), and
`git diff --check`. The focused cleanup regression command
`uv run --no-sync pytest tests/unit/test_mp07_e2e_cleanup.py -q` passes two tests.
A fresh green live run is pending the approval finalization product repair. The gap
is not hidden with skips, weaker assertions, or mocked mutation effects.

Current source hashes after fixture reconciliation and cleanup hardening:

- `test_unattended_economy.py` —
  `5834c7e12a8084940ac619653e3acd8672e4edf4e596623f8a06e66932b79d2c`
- `test_autonomous_acceptance.py` —
  `3408090b13d28963ed049ab7a0b53817c55f770347c1d491f87826fbe99c9496`
- `test_mp07_e2e_cleanup.py` —
  `3ae0867c015abc11f75a637750e4690cca36f7171678e1f3adc63fe0e3c4a815`
