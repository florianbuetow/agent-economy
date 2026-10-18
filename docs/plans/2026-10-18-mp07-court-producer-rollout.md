# MP-07 Court-to-Task-Board ruling producer rollout

Court currently fetches the Task Board task once in `trigger_ruling`, then passes that snapshot into
`RulingOrchestrator.finish_ruling`. The snapshot now carries `lifecycle_version`, but Court's
`record_ruling` payload does not forward it and has no native operation identity. Task Board therefore
rejects the legacy payload before it can claim a ruling transition.

The bounded producer contract is:

- Validate `task_data.lifecycle_version` as a strict non-boolean integer from zero through SQLite's
  maximum before judge/provider evaluation, Task Board delivery, Reputation feedback, or local
  ruling persistence.
- Bind `expected_lifecycle_version` to that already-fetched snapshot. The orchestrator must not fetch
  the task again, silently refresh a stale version, fabricate zero, or retry under a newer version.
- Use one canonical native delivery identity for the current claim:
  `operation_id_for("court-ruling", (dispute_id,))`. Repeating delivery for the same claim preserves
  that exact identity. The delivery operation ID is distinct from `ruling_id`.
- Preserve current ruling linkage: `ruling_id` remains the dispute/claim ID because Task Board's
  MP-07 contract requires `task.dispute_id`, Court claim ID, and ruling ID to agree.
- Include the exact operation ID and snapshot version in the platform-signed Task Board payload,
  retaining the existing audience, resource, worker percentage, summary, task ID, and platform KID.

The deterministic operation ID gives current delivery and acknowledgment retries one stable native
identity. It does not make verdict computation durable. Today Court evaluates judges before Task
Board delivery and persists the Court ruling only after Task Board and Reputation side effects. A
crash can therefore cause judges to run again and produce a different percentage or summary under
the same operation ID. Persisting an immutable verdict/attempt before delivery, plus convergent
Task Board and feedback recovery, remains MP-10 work.

## Task Board acknowledgment contract

`PlatformAgent.record_ruling` returns the public Task Board mutation response: a flat current task
projection plus a separate immutable `operation` envelope. A successful HTTP response or top-level
`status: ruled` is not proof that this attempt completed. Court must validate the acknowledgment
before Reputation feedback or local ruling persistence.

For the operation envelope, require:

- protocol version 1, the expected deterministic operation ID, exact task ID, and command
  `record_ruling`;
- `completed` operation state, `succeeded` effect state, and Bank effect kind consistent with the
  percentage (`bank_release` to the poster for a zero award, `bank_release` to the worker for a full
  award, and `bank_split` for a partial award);
- source status `disputed`, source lifecycle version equal to the fetched snapshot, target status
  `ruled`, and target lifecycle version exactly source plus one;
- effect operation ID and effect result/receipt reference equal to the expected operation ID;
- one positive event ID and a non-null immutable result whose event ID matches the envelope;
- immutable result task/command/status/version, ruling ID, worker percentage, summary, receipt
  reference, and reward consistent with the attempted verdict.

The flat current task projection must identify the same task but is not the immutable command result.
Court must not use mutable top-level lifecycle version or summary as a substitute for validating the
original result, and it must reject an unrelated operation even if the top-level task looks ruled.
The suite keeps the ordinary success fixture at the real terminal `ruled` projection and version 4.
A separately named synthetic boundary control varies the projection version and summary only to
prove that validation reads the immutable operation result. That synthetic projection is not claimed
as reachable lifecycle history: `ruled` is terminal in the current Task Board state machine.

Claimed/pending envelopes, malformed bodies, foreign identities, mismatched versions or verdict
facts, missing/incorrect receipt reference, and event mismatch produce a typed
`502 invalid_task_board_ack`. Court retains the current revert-to-rebuttal-pending behavior. Judges
have already produced the attempted verdict at this point, but feedback and local ruling persistence
must not run.

`PlatformAgent` raises `httpx.HTTPStatusError` for Task Board error responses. Court currently maps
every such response to `502 task_board_unavailable`, which loses an authenticated native stale-write
conflict. This bounded rollout must parse the standard error envelope for a recognized
`409 task_version_conflict` and preserve its code, status, and details while retaining the same
revert behavior. Admission requires the standard error/message/details envelope and the exact
Gateway detail fields: strict integer expected/actual lifecycle versions, the expected version bound
to this attempt, and a nonempty current status. Malformed bodies or details, unrelated error codes,
and other upstream failures keep the existing dependency-error mapping here; the broader
cross-service HTTP error adapter remains MP-20 work. The red suite exercises this through a real
`PlatformAgent` and `httpx.MockTransport`, so the assertion covers the signed wire request and actual
`HTTPStatusError` path rather than a directly injected `ServiceError`. Separate malformed and
unrelated typed-409 controls prove this narrow recognition does not turn arbitrary Task Board
conflicts into local Court errors.

No reusable service-auth facility currently validates Task Board operation envelopes; its strict
response validators cover financial envelopes only. Importing Task Board service models would invert
service boundaries. The proposed production placement is therefore one narrow module-level validator
beside `RulingOrchestrator` (or a small Court-local sibling), validating only this ruling
acknowledgment shape with plain mappings and the existing canonical operation-ID helper. It should
not reproduce the entire Task Board command validator or introduce a parallel general-purpose schema.

No provenance is reconstructed from historical `ruled` rows, and the producer change must not turn
an already-ruled record into a fabricated native operation. These tests cover current in-memory
attempt delivery only: exact payload fields, validation before external calls, stable identity on a
same-snapshot retry, refusal to refresh a stale version, strict completed acknowledgment, and typed
stale-conflict preservation. They do not claim durable crash recovery.

Implementation now lives in `RulingOrchestrator` with a narrow Court-local acknowledgment helper.
The orchestrator validates the fetched lifecycle version before judge evaluation, binds one
deterministic native operation identity and source version into the signed request, admits only the
exact completed ruling result and settlement intent, and preserves only a fully validated native
stale-version conflict. Court's existing fixtures now return native acknowledgments instead of the
retired `{status: ok}` shape.

Evidence is retained under `/private/tmp/agent-economy-mp07-court-*.log`. MP-10 still owns durable
verdict/feedback recovery, and MP-20 still owns the general cross-service HTTP error adapter.

## Fixture and integration boundary

Court unit fixtures synthesize a contract-valid native acknowledgment bound to each attempted
ruling. Unconfigured Task Board reads and ruling writes now fail explicitly instead of returning an
empty task or `{status: ok}`. These unit fixtures prove Court's producer and consumer behavior; they
do not claim a persisted Task Board or Bank effect.

The Court integration fixture now persists a real Gateway history: open publication with an explicit
acceptance window, native bid, acceptance, worker asset, native submission, pending dispute, real
Court claim, and native dispute finalization at one fixed Gateway clock. The final ruling delivery is
still a mocked Task Board boundary because this per-service suite does not run Task Board or Central
Bank orchestration. The integration test therefore asserts that the Gateway task remains `disputed`,
escrow remains locked, and no ruling operation or Bank receipt was fabricated. Persisted ruling
settlement remains covered by cross-service Task Board tests and the root end-to-end gate.
