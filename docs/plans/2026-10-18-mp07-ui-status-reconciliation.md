# MP-07 UI native-command status reconciliation

## Scope

The native Task Board SDK now returns `TaskBoardCommandResponse(status_code, body)` for
acceptance, approval, and dispute commands. The UI proxy must validate the body against its
existing immutable-operation contract and forward the observed HTTP status. It must not infer a
status from `operation_state` or accept status/body contradictions.

## Failing-first evidence

The focused pre-implementation run is preserved at
`/private/tmp/agent-economy-mp07-ui-status-red-final.log`.

- 11 cases executed with no skips.
- 7 failed on the missing typed-response/status-forwarding behavior.
- 4 status/body contradiction cases already failed closed because the old UI rejected the typed
  response wholesale; those cases still guard the more precise implementation.
- An earlier run that failed before reaching the proxy because app state was not initialized was
  discarded as fixture failure and is not counted as product evidence.

## Implementation contract

- The service proxy accepts only `TaskBoardCommandResponse` for native mutations.
- Existing strict operation and current-task-view validation runs against `response.body`.
- Completed operations require the observed status `200`; externally pending claimed operations
  require `202`.
- SDK `TaskBoardCommandContractError` is converted to the UI's sanitized operation-specific `502`
  response without exposing the SDK reason.
- Router handlers return `JSONResponse` with the validated body and the exact observed status.
- The previous `_set_mutation_status` inference helper is removed.

## Existing-test reconciliation

Two pre-existing UI fixtures returned bare dictionaries from fake native SDK calls. Their original
response bodies, call-capture assertions, malformed-body assertions, and security assertions are
unchanged. The fakes now wrap those same bodies in `TaskBoardCommandResponse` with explicit `200`
or `202` status, matching the real SDK boundary.

## Verification

- Focused new plus existing unit contract: 44 passed.
- Affected integration fixtures: 24 passed.
- Full UI unit suite outside the macOS Chromium sandbox: 152 passed.
- Scoped Ruff, format, Mypy, and Pyright checks pass.

This is component evidence only. The parent lane still owns independent full UI CI and root CI.
