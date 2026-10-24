# MP-07 agent SDK fixture reconciliation

## Scope

The public agent compatibility package consumes the shared `service-auth` Task Board mixin. Its
unit fixtures still exercised the pre-MP-07 API: posting omitted the required owner-selected
acceptance window, six native mutations omitted caller-owned operation identity/version, and mutation
fixtures mocked decoded dictionaries through `_request` after the SDK moved those commands to raw
HTTP response parsing.

This is a test-only contract reconciliation. It changes no production code, runtime policy,
configuration or OpenSpec state. Read, asset and posting methods still use `_request` and return body
dictionaries. Only the six native mutation commands return the frozen public response containing the
actual HTTP status and decoded body.

## Captured baseline

The original source hashes are recorded in
`/private/tmp/agent-economy-mp07-agent-sdk-fixtures-original-sha256.log`. The unmodified focused run is
`/private/tmp/agent-economy-mp07-agent-sdk-fixtures-baseline.log`: 13 cases collected, with eight
failures and five passes. The failures occurred before the prior behavioral assertions:

- two posting cases lacked `acceptance_window_seconds`;
- cancel, submit-bid, accept-bid, submit-deliverable, approve and dispute lacked `operation_id` and
  `expected_lifecycle_version`.

The five passing read/asset cases establish that their body-only `_request` contract remains intact.
This baseline is a superseded-caller contract, not eight independently observed product defects.

## Reconciliation

- Both posting tests now provide and assert an explicit acceptance window.
- The six mutation tests provide one canonical operation ID and explicit source lifecycle version,
  and assert both fields remain in the signed payload beside every prior business field.
- Mutation fixtures return attached `httpx.Response` objects through `_request_raw`, with status 201
  for bid submission and status 200 plus completed operation state/effect metadata for the other five
  commands.
- Mutation results assert the actual status and complete decoded body. Each original business response
  field is independently retained inside `result.body`.
- Every prior audience, resource, payload business field and request URL/body assertion remains.
- Modified agent clients close in `finally` blocks so assertion or transport failures do not leak
  their real client.

No tests were skipped, suppressed, merged or replaced with a weaker representation. Pending 202
behavior and malformed-success handling remain covered by the frozen shared-library SDK contract;
these compatibility tests retain their original successful-command scenarios.

## Verification boundary

Run the two focused files and their unchanged read/asset controls, plus scoped Ruff, format and
codespell. Probe Mypy and Pyright without hiding diagnostics: this project excludes tests from its
configured type-check gates, while the not-yet-reconciled production callers are outside this file
ownership. Archive exact logs and final source hashes. Shared SDK, UI, runtime journal, component CI
and root CI remain outside this bounded reconciliation and must not be inferred from its green result.

## Captured result

The reconciled focused run passes all 13 original cases. Ruff and format pass for both files, and
codespell passes for both files and this note. Direct strict Mypy invocation on the test files retains
six pre-existing test/package diagnostics: four missing `py.typed` import diagnostics, the original
untyped `sample_config` parameter, and the original unannotated empty `tasks_response`. The five
helper type errors initially exposed by that optional probe were repaired by accepting a covariant
read-only mapping.

Configured Pyright excludes tests and reports five production call sites that still omit the new
operation identity/version. Those call sites belong to the separately active runtime-intent
implementation and were not edited here. These diagnostics are preserved as downstream work; they
do not contradict the green focused fixture reconciliation.
