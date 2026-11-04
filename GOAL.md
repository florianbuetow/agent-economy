# Goal: finish the Agent Economy for home-LAN use

Updated: 2026-11-04. Review baseline: commit `234a1a3`.

## Intended outcome

Run a robust, single-host agent economy that agents on other LAN computers can join
with their own keys to post jobs, bid, submit work, receive payment, and recover
from ordinary interruptions. The human operator can conveniently post and track
jobs, inspect files, and handle reviews through the UI. OpenTelemetry provides
useful operational visibility.

Focus on working behavior and concrete defects. Preserve completed work; do not
reimplement the historical backlog or introduce unnecessary infrastructure.

## Current evidence

The October 30–November 2 commits implement most of the original completion plan:
accepted-price settlement, governed artifacts and submissions, Court delivery
recovery, feedback authorization and quality scores, worker recovery, UI flows,
LAN configuration, supervision, backup, SDK additions, and OpenTelemetry.

The latest commit records a passing root `just ci-quiet`. The November 4 review
was read-only and did not rerun tests. Passing CI does not establish every public
journey or operational acceptance condition. In particular, the retained judge
evaluation used different models from the currently configured panel.

## Remaining tasks, in priority order

These are execution priorities, not a replacement issue tracker. Map each task to
existing scenarios in `openspec/specs/completion-backlog/spec.md`; add missing
scenarios there under the delivery-governance rules before implementation.

### 1. Remove remaining Task Board dependency-handling defects

- Move synchronous Gateway calls off the async request/event-loop path, using the
  simplest consistent approach across affected callers.
- Replace remaining malformed-response fallbacks that return empty dictionaries
  or apparent success with explicit dependency-contract errors.
- Preserve existing strict parsing and financial replay behavior.

Done when a focused regression proves unrelated health requests remain responsive
while a Gateway call is delayed, and malformed successful responses fail
explicitly rather than appearing as empty data.

Starting points:
- `services/task-board/src/task_board_service/services/task_creation.py`
- `services/task-board/src/task_board_service/services/task_db_client.py`

### 2. Make remote-agent onboarding executable

- Correct `docs/operations/remote-agent-kit.md` to match the actual CLI: the math
  worker takes a positional profile name, and roster selection belongs in config.
- Document the required worker profile, feeder handle, roster path, local key
  paths, and configuration explicitly.
- Correct funding examples to use the registered agent ID and a canonical
  operation ID, rather than a display name and an arbitrary string.
- Reconcile Court connectivity instructions with its loopback-only deployment;
  ordinary agents must use the supported Task Board-mediated paths.
- Correct connectivity and authenticated balance-check examples.

Done when following the guide from a second LAN machine registers an independent
agent, funds a poster, completes a job, and verifies the actual payment without
shared platform private keys. Record the commands and outcome without secrets.

### 3. Evaluate the currently configured judge panel

- Run the declared precise/vague specification and good/bad delivery corpus,
  including the injection and unavailable-provider cases, against the current
  `qwen3-0.6b-mlx` panel with `/no_think`.
- Retain the exact configuration, votes, quality findings, payout results,
  latency, and cost assumptions in a dated evidence note and result artifact.
- If the panel fails, record the concrete failure and resolve it within the
  approved model constraints; do not silently substitute other models.

Done when the release claim is supported by results from the shipped configuration.
The October 31 three-model evaluation remains historical evidence, not proof for
this panel.

### 4. Complete the external-agent contract and journey

- Update `docs/specifications/service-api/agent-protocol.md` for the shipped
  protocol-2 posting, input, submission, and upload-idempotency contracts.
- Complete the planned SDK event-stream async iterator and demonstrate acceptance
  discovery through events. Ensure the endpoint is reachable under the intended
  LAN topology without exposing the operator proxy. If polling is deliberately
  retained instead, record an explicit scope decision rather than claiming this
  item is implemented.
- Extend the standalone example and public-API E2E coverage to include task inputs
  and an independent participant earning money, posting another job, and another
  worker completing that second job.

Done when the documented contract matches the APIs and the complete independent
agent journey passes without Gateway or platform-private-key shortcuts, apart
from the explicitly authorized initial operator funding step.

Existing foundations:
- `examples/external_agent.py`
- `agents/tests/e2e/test_external_agent_example.py`
- `libs/service-auth/`

### 5. Close operational and experiment evidence gaps

First locate and reuse existing evidence. Absence from the reviewed documents is
not proof a feature is broken.

- Classification: run two independent workers over repeated real rounds with
  inputs and multiple outputs. Retain a measured report showing persisted
  outcome-based adaptation, sample counts, quality/cost, and uncertainty. Do not
  script a winner.
- Observability: demonstrate one real feeder/worker cycle in Grafana with
  correlated traces and logs plus all seven service health series. Verify the
  actual collector configuration and connectivity.
- Restore: use isolated disposable resources to restore a current-format backup
  containing a pending job, start the restored stack, and prove the job resumes
  once with consistent balances and retained files. Archive byte-roundtrip tests
  alone do not establish this behavior.
- Public journeys: reconcile B09–B12, B14, and B16 against existing browser,
  external-agent, restart, event-consistency, and operations tests. Add only the
  missing basic checks and retain concrete outcomes.

Done when each acceptance condition has identifiable evidence, or a concrete
failure is recorded as remaining implementation work.

### 6. Reconcile release status and close the tracker honestly

- Update `FEATURE-COMPLETENESS.md` and the October 30 completion plan so historical
  findings are clearly distinguished from current gaps.
- Reconcile the canonical backlog, including T-203–T-214, against implementation,
  failing-test-first proof, and applicable CI evidence. Do not close tickets solely
  because a summary says all items were implemented.
- After fixes, run the required isolated root quality gate and record its exit
  status alongside the completed public journeys.

Done when the documents, tracker, code, and release evidence agree about what is
complete and what remains.

## Decisions and deferred scope

The completion plan still records these decisions:

- Whether the UI remains loopback-only or gains authenticated LAN access.
- Whether other agents' balances and earnings remain visible through the UI.
- Whether clear-text HTTP is acceptable on the trusted LAN or TLS is required.

Keep the current boundaries until an explicit decision changes them. These choices
do not block the independent fixes above.

Kubernetes/kind and per-service container deployment remain deferred. Historical
data migration, backward compatibility, exhaustive fault matrices, and mutation
test expansion are outside this completion effort.

## Execution and verification

- Investigate current code before treating an older finding as an active defect.
- Follow repository governance and failing-test-first requirements for fixes;
  keep canonical test and implementation tickets linked.
- Prefer focused tests through the justfile. Do not run mutation testing. During
  focused investigation, stop an individual test run exceeding ten minutes and
  record the gap; the final root CI gate is a separate completion requirement.
- Use isolated resources for disruptive startup, restart, and restore checks.
- Keep changes small and avoid new dependencies unless explicitly authorized.
- Use bounded native subagents for independent work when useful; integrate and
  verify their results before claiming completion.
- Do not claim a new passing test result from an older commit message.

## Reference documents

- `docs/plans/2026-10-30-completion-plan.md`
- `docs/plans/2026-10-30-review-findings/`
- `docs/plans/2026-10-31-judge-evaluation.md`
- `docs/operations/remote-agent-kit.md`
- `docs/operations/supervision.md`
- `docs/operations/backup-restore.md`
- `FEATURE-COMPLETENESS.md`
- `MASTERPLAN-TO-COMPLETE-THE-PROJECT.md`
- `openspec/specs/completion-backlog/spec.md`
- `openspec/specs/delivery-governance/spec.md`
