# MP-07 SDK status-preservation tests

## Scope

The six caller-owned native Task Board SDK commands currently decode a success response through
`BaseAgent._request` and return only its JSON body. That erases the HTTP status which distinguishes a
completed command from a claimed external effect. The durable runtime intent journal needs the actual
status before it can classify a response as completed, pending, malformed or uncertain.

This test-only slice defines the public SDK prerequisite. Production implementation belongs to the
next ticket and must add one shared `service_auth.task_board_responses` parser. It must not add parallel
methods, compatibility flags, hidden retries or runtime access to private SDK methods.

## Observable contract

- `submit_bid` returns a frozen `TaskBoardCommandResponse(status_code=201, body=<object>)`.
- `accept_bid` and `submit_deliverable` return status 200 only.
- `cancel_task`, `approve_task` and `dispute_task` return status 200 for completed operations or 202
  for claimed external effects.
- Every command retains its existing explicit `operation_id`, `expected_lifecycle_version`, business
  fields, audience and resource in the signed token.
- Unexpected 2xx statuses, invalid JSON, non-object bodies, missing operation state and contradictions
  between status and operation state raise public `TaskBoardCommandContractError`. The error exposes
  the actual `status_code` and a nonempty `reason`.
- A claimed body paired with 200 and a completed body paired with 202 are contradictions. A claimed
  pure operation (`effect_kind=none`) is also invalid.
- Non-2xx responses continue to raise `httpx.HTTPStatusError` with the original response status and
  body. Transport timeouts continue to propagate as transport exceptions.

## Test map

`libs/service-auth/tests/unit/test_mp07_task_board_status.py` uses the real `BaseAgent` and
`httpx.MockTransport` to cover:

1. Public constructible response/error types and frozen response fields.
2. Nine allowed status/method combinations, including all six commands and every pending-capable
   command, with signed-token assertions.
3. Six method-specific unexpected-success-status cases.
4. Invalid JSON, non-object bid/mutation bodies, and missing operation-envelope cases.
5. Claimed/200, completed/202 and pure-pending contradictions.
6. All six methods preserving typed 409 responses and all six preserving `ReadTimeout`.

The missing public module is imported inside its own test so it cannot prevent collection. Every
other case exercises the current real client independently. Controls for existing HTTP errors and
timeouts are expected to pass before implementation; status-preservation and strict-success cases are
expected to fail for the missing capability.

## Verification boundary

Record a targeted red run plus Ruff, format, Mypy and codespell checks for this new test. Do not modify
production code, existing tests, configuration, OpenSpec state or Git history in this slice. Freeze
the test and plan hashes before assigning implementation. Component and root CI remain downstream
gates and are not claimed here.

## Captured red evidence

The final pre-implementation run collected 36 cases with no skips or warnings: 24 failed and 12
passed. The 24 failures are one missing public-module case, nine discarded-status response cases, six
accepted unexpected-success-status cases, five malformed-success cases, and three status/body
contradictions. The 12 controls are the six commands preserving `HTTPStatusError` and the same six
preserving `ReadTimeout`; they all passed.

The authoritative red log is
`/private/tmp/agent-economy-mp07-sdk-status-red-final.log`. Scoped Ruff, format, Mypy and codespell
logs use the same prefix and each exited zero. This is capability evidence against the current SDK,
not a claim that the implementation or any broader T-181 recovery behavior is complete.

## Parent implementation and signing-fixture reconciliation

The typed response and shared transport parser now pass all36 new cases. The old signing-only
fixture returned `200 {status: ok}` for every command; stricter transport admission exposed seven
fixture failures before their unchanged signed-token assertions. Its method-specific mock now returns
flat201 bid or200 completed state/effect fields. These are transport fixtures, not full immutable
operation or real settlement evidence. Every test definition/assertion remains unchanged. The original
source is preserved as `/private/tmp/agent-economy-mp07-sdk-status-producer-before.py`.
Combined status/signing controls pass68; the full shared-library unit suite passes379. Full source
Mypy and scoped Ruff/format pass. The parser checks actual status, object shape and state consistency;
caller-bound identity/hash/result/effect verification remains required in the journal and UI.
Consumer migration and independent review remain pending; this is not full root CI or ticket closure.
