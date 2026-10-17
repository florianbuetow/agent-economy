# MP-04 SDK receipt validation

Status: implemented and independently approved as a bounded consumer slice. T-116/T-117 remain
OPEN. This is not historical-operation support, lifecycle atomicity, or whole-repository CI proof.

## Shared boundary

The Bank's existing strict parser now lives in
`libs/service-auth/src/service_auth/financial_responses.py`. The Bank's Gateway adapter, account
and escrow routers, in-memory store, and SDK share its models and operation ID validation. The
shared library already depended on Pydantic; no dependency or compatibility shim was introduced.

Platform issuance, credit, release and split, plus the agent's direct lock, validate each native
receipt against the submitted ID, kind, canonical business hash, source, result, ordered legs and
current projection. Zero-account creation validates its current account projection, including the
legitimate case where a concurrent credit has made the balance positive. Funding commands cannot
print success before the SDK accepts the receipt, and their cleanup runs on rejection.

Strict models reject boolean receipt versions and nonpositive event IDs. Required identity/time
strings and present nullable identity/time strings are nonempty valid UTF-8. Zero split legs keep
null transaction and balance fields. Lock replay can carry a terminal current projection without
changing its original receipt.

## Red and green evidence

All commands ran through `uv run` with frozen dependencies and the isolated review UV cache.
Logs below are local evidence under `/private/tmp/`; they are not repository-wide release reports.

| Evidence | Outcome |
| --- | --- |
| `agent-economy-mp04-shared-parser-before.log` | Existing Bank parser: 32 passed before extraction |
| `agent-economy-mp04-shared-parser-after.log` | Parser, memory and public Bank/Gateway contracts: 59 passed after extraction |
| `agent-economy-mp04-sdk-receipt-validation-red-final.log` | 57 genuine malformed-response failures (`DID NOT RAISE`), 10 truthful controls passed |
| `agent-economy-mp04-sdk-shared-parser-first.log` | After SDK wiring: 65 passed; boolean version and zero event ID still failed |
| `agent-economy-mp04-sdk-receipt-validation-green.log` | Strict integer repair: 67 passed |
| `agent-economy-mp04-financial-intent-sdk-red.log` | Six obsolete outbound transport fixtures failed validation, three controls passed; this is fixture reconciliation, not product-red proof |
| `agent-economy-mp04-sdk-text-validation-red.log` | 12 genuine empty/invalid-text failures, one existing rejection control passed |
| `agent-economy-mp04-sdk-text-validation-green.log` | Final SDK validation, outbound signing and funding suites: 105 passed |

The SDK negative tests use actual SDK methods with the transport replaced. Both funding CLI
negatives pass through the real `PlatformAgent.credit_account`; they assert no success output/log
and that clients close. Fixture hashes are computed independently. Supplemental zero-account tests
were added after parser wiring and are not claimed as historical red proof.

Independent final review approved the parser, SDK and these tests with no unresolved findings.
It also confirmed that Gateway tests again load the production-configured schema file. A separate
review of the 13 Bank test reconciliations approved them after restoration of the timezone-aware
`committed_at` assertion. Their parent focused repair check passed 44 tests; the restored Gateway
schema fixture check passed 67 tests.

Final parent component gates after SDK text validation:

- Auth `just ci-quiet`: exit 0, 216 tests; `agent-economy-mp04-auth-sdk-final-ci.log`.
- Agents `just ci-quiet`: exit 0, 386 passed, three credential skips and 94 E2E deselections;
  `agent-economy-mp04-agents-sdk-final-ci.log`. Skips are not real-provider proof; this component
  command does not replace the root E2E gate.
- Service-clients `just ci-quiet`: exit 0, 96 tests;
  `agent-economy-mp04-service-clients-parent-final-ci.log`.
- Central Bank `just ci-quiet`: exit 0, including strict Pyright, Semgrep, architecture and unit
  checks; `agent-economy-mp04-bank-parent-final-ci.log`. The earlier sandbox attempt failed before
  Semgrep scanning because it could not read the host trust store; the host-access rerun passed
  without suppressing or changing the check.
- Task Board `just ci-quiet`: exit 0, including the reconciled 481-case unit suite, architecture,
  strict Pyright and security gates; `agent-economy-mp04-task-board-parent-ci.log`.
- Parent Bank in-process integration: 16 passed, including all 13 new public replay proofs;
  `agent-economy-mp04-bank-integration-parent-green.log`.
- Tools/demo `just ci-quiet`: parent exit 0, 136 tests;
  `agent-economy-mp04-tools-parent-ci.log`. Demo funding now requires an explicit scenario reference,
  derives a stable intent ID, validates the native receipt and prints its balance/ID. All 16 shipped
  funding steps specify references; task posting derives the lock intent. Seven genuine reds and
  one control preceded the changes; independent review approved the seven-file slice. No durable
  posting recovery is claimed.

The separate `service_clients.BankClient` now binds lock/release/split and aliases through the same
parser. Lock semantics are parsed from the submitted token only after a successful Bank response;
this does not authenticate it or replace Bank authorization. Invalid successes become explicit
502 dependency errors. Seven genuine red cases plus three truthful controls preceded the change;
independent review approved it. Source SHA-256 is
`2f6ef580d9ac85737d609ac29cef8062496fb5468cf750e3d99396b211ea6f9c`.

Native Bank router logs now use immutable receipt IDs, source references and ordered leg facts,
including split percentage from the receipt result. Four genuine logging reds and two controls
preceded the repair. Parent's six logging cases pass and independent review approves the slice.

## Source identities

| File | SHA-256 |
| --- | --- |
| `service_auth/financial_responses.py` | `834fc36aa982a0b54e74a4b16d43442da0861a2f4a294c3cc5589296b4bbe5bb` |
| `service_auth/platform.py` | `51a491018163556dce87340ba43797e8cb54a7e5367e7ad5f0221bd25bf12208` |
| `service_auth/mixins/bank.py` | `2da793e72a46315382e866daccc1f2e95aa5efd1449f62b131587515396cb384` |
| `agents/tests/unit/test_mp04_sdk_receipt_validation.py` | `6b6a741c77ded90183a36a204ae65c16ad16b5102d9b1eab1c704ac3f703db13` |
| `agents/tests/unit/test_mp04_financial_intent_sdk.py` | `126bd95d71eca060f2516d3e04b84e710547bb56165551d5d0c205746bd31d4c` |

## Remaining integration

Task Board's pre-reconciliation unit baseline was 215 failed / 253 passed, recorded in
`agent-economy-mp04-task-board-current-unit.log`. Its obsolete financial fixtures/signatures are
reconciled while preserving every underlying assertion. Independent review approved the six
existing test-file changes; its focused rerun passed 119 cases. A genuine same-ruling replay gap
was independently identified: changed percentages or summary text were accepted under the stored
ruling ID. New domain tests captured 11 genuine failures and two controls, then 13 passes after
the repair (`agent-economy-mp04-ruling-replay-red.log` and `-green.log`). Independent review approved
the ruling slice. The financial-intent fixture now computes a genuine canonical hash and models
a truthful settled projection; the combined intent/ruling suite passes 23 tests. Task Board
component CI and independent review of its existing-test reconciliation now pass. This does not
replace its still-required cross-service and live-runtime integration validation.

Historical observations, remaining integration/runtime callers and the fresh isolated root gate
are still required. The historical continuation contract is in
`docs/plans/2026-10-17-mp04-historical-observations.md`; new tests are being written before production
support. Root15 remains the last successful whole-repository gate.
