# Recovery, supervision, and operations campaign results

Source revision: `35cf0e3a5fcfd3c6a92d6c2e305bdfd711c7aa16` plus the
concurrent uncommitted dispute/UI work recorded by the parent lane. Executed on
2026-11-04 using disposable pytest fixtures and `IsolatedRuntime` roots only.
No normal service, key, database, upload, or OS supervisor was mutated.

The sequence disposition is deliberately stricter than the aggregate test
result. A nearby passing test is listed as partial evidence rather than being
used to overstate coverage.

Canonical coverage ticket T-229 owns the new Gateway-outage, real telemetry,
full-stack isolation, multistate restore, and unattended-workload tests. The
conditional implementation ticket T-230 is not needed: the only apparent trace
failure was reproduced as a collector batch-timing error and resolved by waiting
for the exact correlation predicate; no production behavior was changed.

## Dispositions

| ID | Disposition | Executed evidence and remaining boundary |
| --- | --- | --- |
| REC-01 | PASS | `recovery-cuts.log`: 33 process-cut cases pass for bid, accept, submit, approve, dispute, rebuttal, ruling trigger, upload, feedback, file claim, cancellation, and financial finalization. Evidence is operation-specific: rebuttal recovery reconciles authoritative projection and is not claimed as receipt-grade generic replay. Posting restart evidence separately covers committed publication across process loss. |
| REC-02 | PASS | `identity-outage.log`, `identity-bank.log`, `identity-reputation.log`, and `identity-startup.log`: 17 + 6 + 46 + 1 checks pass. Agent-signed admission returns typed unavailability while platform-local ruling/credit/reputation paths retain their local-verification contract; dependency startup recovers after Identity becomes available. |
| REC-03 | PASS | `bank-recovery.log`: 11 checks pass through a live disposable Gateway. Missing receipt, lost Bank acknowledgment, precommit failure, deadline refund/approval, and recovery-loop convergence preserve pending/source state and complete exactly once after recovery. |
| REC-04 | PASS | `gateway-live-outage.log` stops Gateway in a complete disposable stack. A safe direct UI read remains available; registration, credit, and publication do not report success while persistence is unavailable; pre-restart row counts remain unchanged. Timed-out operations are treated as unknown: after restart, exact registration/credit/publication retries reconcile to one agent, one credit, and one admitted posting intent. `gateway-offline.log` and `gateway-contracts.log` cover all persistence namespaces and fail-closed response parsing. |
| REC-05 | PASS | Strict Task Board parser tests pass in `gateway-contracts.log`. `ui-dependency-contracts.log` proves the UI preserves exact typed 4xx/5xx public envelopes, sanitizes malformed 502 bodies without leaking their marker, projects the authoritative dispute deadline, and renders before/equality/after/missing/rebutted states correctly. Transport/reset behavior remains covered by the existing proxy suites exercised by UI CI. |
| REC-06 | BLOCKED | Existing strict response parsers fail closed (`gateway-contracts.log`). A proactive UI/Task Board version handshake is proposed behavior and has no ratified contract. Owner decision required before requiring a handshake. |
| REC-07 | PASS | `operation-identity.log`: 27 checks pass. Exact owner replay returns the original result; changed terms, cross-command reuse, malformed IDs, other callers, and stale lifecycle versions conflict without a second effect. |
| SUP-01 | BLOCKED | Current `build_units` always includes seven services plus feeder and mathbot. A service-only supervision scope does not exist and requires owner approval. No host unit was installed. |
| SUP-02 | BLOCKED | Depends on SUP-01 and a ratified restart bound. Generated launchd/systemd units do encode restart behavior, but no service-only Court crash-restart contract exists. |
| SUP-03 | BLOCKED | Depends on SUP-01. Intentional stop versus crash restart semantics require a service-only supervisor contract. |
| SUP-04 | BLOCKED | Current one-shot `start-all` and installed supervision are separate modes; delegation/fail-fast mixed-mode policy is proposed and needs owner approval. |
| SUP-05 | BLOCKED | Dispute exact recovery is covered elsewhere, but automatic Court restart depends on the unimplemented service-only scope. |
| SUP-06 | BLOCKED | Restart/backoff rendering exists, but a service-only flapping/status contract and ratified bounds do not. |
| SUP-07 | BLOCKED | `supervision.log`: 38 checks pass for argv, working directory, environment loading, restart directives, unit lifecycle, and ownership-safe stops. The policy for refreshing changed Court `.env` content is unresolved and requires an owner decision. |
| OPS-01 | PASS | `multistate-restore.log` creates open, accepted, submitted, disputed, real expired-lease `judging`, and approval-settlement-pending tasks in one disposable runtime. Canonical recovery preparation stops/drains the source and creates a full identity/config/key/Court/environment inventory; the recovery bundle is restored with fresh ports, paths, maintenance identity, and process ownership into a new `IsolatedRuntime`. Frozen bytes survive, judging and pending settlement recover, every other task resumes via supported actions, exact command replays add no effect, final balances match accepted-price outcomes, and the money audit is clean. |
| OPS-02 | PASS | `multistate-restore.log` streams an assigned worker's authenticated multipart upload, pauses after the body prefix and first bytes, then releases the remainder exactly when canonical recovery preparation begins stopping the full stack. The anchored stopped snapshot contains either one fully ready/public asset after HTTP 201 or no ready/public asset after an aborted request—never partial public success. Canonical inventory and backup then complete with `snapshot_preserved`. |
| OPS-03 | PASS | `operations.log`: the live artifact-restore sequence removes original source/runtime directories, restores into a fresh target, verifies database and exact deliverable bytes, and preserves submitted work without original source files. |
| OPS-04 | PASS | `real-telemetry.log` proves real Identity → DB Gateway propagation and request-marker redaction. The full dispute sequence drives UI proxy filing, assigned-worker rebuttal, automatic Court ruling, Task Board callback, Bank settlement, and Reputation feedback. It polls for exact correlated filing/ruling spans within the configured batch-export bound rather than accepting unrelated startup spans. The ruling trace contains Court/Task Board/Bank/Reputation/Gateway, authoritative task/dispute attributes connect asynchronous phases, and settlement is 30/70. Private claim/rebuttal text, actual signed bearer token, assigned worker private-key bytes, and uploaded-file bytes are all absent. The earlier immediate-read red was a collector timing error, retained in `trace-correlation-red.log`; no production fix was needed. |
| OPS-05 | PASS | `real-telemetry.log` runs actual disposable Identity and DB Gateway services against 404, timeout, and connection-drop OTLP collectors. Each exporter failure is visible in owned service logs. Registration commits, both services restart, the original agent remains authoritative, and a second registration succeeds; telemetry failure cannot roll back, duplicate, or halt business writes. |
| OPS-06 | PASS | `recovery-inventory.log`: digest-bound manifests, changed source/config bytes, malformed manifests, and unverified restores fail closed; they cannot be adopted or cited as verified evidence. |
| OPS-07 | PASS | `operations.log` proves owned cleanup and an unrelated listener; `full-stack-isolation.log` runs two complete seven-service stacks with disjoint ports, database, keys, asset root, maintenance lock, configs, logs, and process groups. Each stack performs an independent task/ledger/asset mutation. Runtime manifests and exact artifact bytes remain isolated; stopping one drains only its processes while the other stays healthy and can retrieve its task and asset. |
| ECO-05 | BLOCKED | Individual restart/fault components are covered by REC-01/03/07, but no single sequence combines all listed failures and proves one final task/ruling/settlement. A deterministic compound harness is required before this current-contract scenario can pass. |
| ECO-06 | PASS | `unattended.log` passes production loops for approved and disputed→rebutted→ruled tasks. `unattended-expiry.log` adds two more production-loop outcomes: one task receives no eligible bid and expires/refunds; another is accepted by a worker whose deterministic solver stalls, then execution expiry refunds the poster and pays the worker zero. |
| ECO-07 | PASS | `valid-workloads.log` concurrently publishes eight valid tasks spanning repeated titles, Unicode, a 10,000-character specification, contradictory/mixed-category instructions, one-credit and 50,000-credit rewards, then publishes a ninth task with ten exact input files. All task IDs and artifacts remain distinct. This records accepted structural limits only; it makes no routing, fairness, learning, or semantic-quality claim. |

## Executed command groups

All commands used the normal repository environments with `--no-sync`; where
pytest needed disposable loopback listeners, the sandbox was explicitly opened
only for those owned test processes.

| Evidence file | Scope | Result |
| --- | --- | --- |
| `identity-outage.log` | Task Board Identity outage and local platform ruling | 17 passed |
| `identity-bank.log` | Bank two-tier Identity outage | 6 passed |
| `identity-reputation.log` | Reputation two-tier Identity outage | 46 passed |
| `identity-startup.log` | Real dependent startup before/after Identity | 1 passed |
| `bank-recovery.log` | Bank receipt/failure/deadline recovery | 11 passed |
| `operation-identity.log` | replay, cross-command IDs, Gateway parity | 27 passed |
| `gateway-offline.log` | all Gateway persistence namespaces unreachable | 5 passed |
| `gateway-contracts.log` | fail-closed reads, operation responses, sweep recovery | 44 passed |
| `supervision.log` | unit generation, argv parity, install roundtrip, safe stops | 38 passed |
| `operations.log` | backup/restore, live restore, dual stacks, owned cleanup | 28 passed |
| `recovery-inventory.log` | preparation, inventory, adoption and digest checks | 44 passed |
| `recovery-cuts.log` | process cuts and operation-specific replay/recovery | 33 passed |
| `unattended.log` | unattended approved and disputed/ruled paths | 1 passed |
| `full-stack-isolation.log` | two concurrent complete disposable stacks | 1 passed |
| `gateway-live-outage.log` | full-stack Gateway loss and exact reconciliation | 1 passed |
| `real-telemetry.log` | real service registration/dispute traces and exporter faults | 5 passed |
| `real-dispute-trace.log` | real asynchronous dispute trace correlation | 1 passed |
| `unattended-expiry.log` | no-bid and accepted-worker expiry | 1 passed |
| `multistate-restore.log` | simultaneous lifecycle-state backup/restore/recovery | 1 passed |
| `ui-dependency-contracts.log` | typed dependency errors and dispute waiting states | 10 passed |
| `valid-workloads.log` | valid long/mixed/bursty tasks and ten input files | 1 passed |

## Non-product setup failures

The first attempts were not scenario executions: the restricted uv cache could
not create a lock and the sandbox denied local `socket.bind`. They are retained
as setup diagnostics only. Successful reruns used `/tmp/ate-campaign-uv-cache`,
existing locked environments, and disposable loopback permission. No scenario is
marked failed because of those environment restrictions.

