# MP-07 service-auth Task Board producer rollout

The shared `service-auth` Task Board mixin currently signs legacy public mutations without the
caller-owned native command identity and lifecycle version. Task posting also omits the required
poster-selected acceptance window. Downstream callers are intentionally outside this bounded change;
their rollout remains separate and the library cannot claim repository-wide compatibility yet.

The producer contract changes are explicit and local:

- `post_task` requires keyword-only `acceptance_window_seconds`. It validates a strict non-boolean
  positive integer no larger than the SQLite-safe duration range, then includes the exact value in
  the Task Board token. The Central Bank escrow token remains separately signed and bound to the
  generated task ID, poster, amount, deterministic lock operation ID, audience, and resource.
- `cancel_task`, `submit_bid`, `accept_bid`, `submit_deliverable`, `approve_task`, and `dispute_task`
  require keyword-only `operation_id` and `expected_lifecycle_version`. The operation ID is checked
  by the existing canonical service-auth validator. The version is a strict non-boolean integer from
  zero through SQLite's maximum integer. Both exact caller values enter the signed Task Board payload.
- The mixin never generates a mutation operation ID or version, fetches a task to infer one, retries
  with a replacement identity, or persists replay state. Callers retain and resend the same values.
- Validation happens before signing or HTTP request construction. Existing business fields, URLs,
  audiences, resources, agent roles, and response behavior remain unchanged.

Tests are written before implementation. They capture real BaseAgent-signed compact tokens, assert
all six mutation payloads and posting's two distinct context-bound tokens, repeat calls with the same
identity/version to prove exact preservation, and prove invalid or missing native fields cause no
signature or request. The existing MP-03 posting assertion receives only the new explicit valid
window and retains all prior token-binding assertions.

All evidence is retained under `/private/tmp/agent-economy-mp07-sdk-*.log`. Completion of this slice
means the shared producer contract is ready for downstream adoption; it does not mean UI, agents,
tools, Court, or other callers have been migrated.
