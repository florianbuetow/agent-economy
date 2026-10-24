# MP-07 runtime integration follow-ups

These T-181/T-182 repairs keep the full Core and Vision goal open. No package or
ticket closes from the component evidence below.

## Public identity contracts

The local journal's UUID version/variant restriction was narrower than the
public SDK and Gateway canonical operation-ID contract. The shared validator
accepts any lowercase canonical `op-UUID`; it does not require UUID versions 1–5.
The journal now uses `service_auth.financial_responses.validate_operation_id`,
preserving its own `RuntimeIntentError` exception boundary. New tests captured
8 failures and 8 passing controls before this repair, then all 16 passed.
They cover persistence/completion/reopen and foreign transition ownership as
well as rejection of noncanonical spellings. Original model bytes are retained
at `/private/tmp/agent-economy-mp07-runtime-operation-model-before.py`.

Task Board's published task-ID admission uses a case-insensitive hexadecimal
UUID shape without version-bit restrictions. Journal task-ID admission now
accepts that shape while preserving exact spelling and using full matching to
reject trailing newlines. It does not normalize or reinterpret remote IDs.
Existing digest filenames hash exact task/slot JSON, so two case-distinct remote
IDs have distinct filenames even on case-insensitive filesystems. New tests
captured 5 failures and 6 passing controls before the repair. Combined journal,
ACK, reopen, identity and coordinator controls then passed 223 cases. This does
not close the independent MP-09 server asset-path obligations.

Both identity repairs received independent review approval. Raw red/green logs
use `/private/tmp/agent-economy-mp07-runtime-{operation-id,task-id,identities}-*`.

## Acceptance summary versus full detail

The reconciled live autonomous-acceptance and unattended-economy tests exposed
a product failure: public task-list summaries omit `escrow_id`, but acceptance
prepared its immutable validation context from that summary. No acceptance
could be sent, leaving both real-loop tasks open. This is distinct from the
earlier required-constructor fixture failures.

Fresh acceptance preparation now obtains the full public task detail after bid
eligibility and selection, verifies exact task identity, and freezes its source
version and complete financial facts. Existing stored intents still bypass
all preparation reads. Missing details, foreign task details, and non-open
details cannot publish a local intent or send acceptance.

Six new unit regressions all failed before this repair. Afterward the first
positive assertion incorrectly looked for `reward` in frozen facts, whose
documented field is `accepted_reward`; that test-field correction is explicit
and is not claimed as another product defect. The full feeder unit suite then
passed 103 cases. Logs: `/private/tmp/agent-economy-mp07-acceptance-snapshot-*`.

Existing fixture changes are limited to supplying explicit full open-task
details in the two native-intent positives and the legacy acceptance helper.
The original event assertions retain bid/reputation ordering and now assert
the added detail read before transport. The saved-intent no-read assertions
are unchanged. Originals are retained as
`/private/tmp/agent-economy-mp07-{acceptance-before-detail,feeder-intents-before-detail,feeder-acceptance-before-detail}.py`.

Before these follow-ups, independent parent `agents/just ci-quiet` exited 0:
732 passed, 3 pre-existing credential-gated provider tests skipped, 94 live E2E
tests deselected. The scoped source/test hash lists were identical. This gate
does not certify the later edits or the real provider profile. Fresh agent and
isolated root gates remain required after integration settles.
