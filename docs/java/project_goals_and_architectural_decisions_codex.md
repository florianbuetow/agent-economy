# Java Rewrite: Project Goals and Architectural Decisions

## Scope and Source of Truth
This design is anchored to the existing docs specifications:
- `docs/main/agent-task-economy.md`
- `docs/specifications/schema.sql`
- `docs/specifications/service-api/*.md`
- `docs/specifications/service-tests/*.md`

The goal is a Java monolith that preserves behavior while replacing inter-service HTTP with in-process calls.

## Spec Conflicts Noted (Need Resolution)
These conflicts exist inside `docs/specifications/*` today; any implementation (microservices or monolith) must pick a consistent interpretation:
- Task Board spec/test require `escrow_pending` in task responses, but `docs/specifications/schema.sql` has no `board_tasks.escrow_pending` column.
- Identity spec defines `POST /agents/register` returning a server-generated `agent_id`, while the DB Gateway spec models identity writes as caller-supplied IDs (and other service auth specs assume an Identity `POST /agents/verify-jws` endpoint that is not described in the Identity API spec).
- Court API/auth specs model `/disputes/*` and `disp-...` IDs, while `docs/specifications/schema.sql` defines `court_claims` / `court_rebuttals` / `court_rulings` and the DB Gateway spec models court writes as `/court/claims`, `/court/rebuttals`, `/court/rulings`.

## Executive Decision
Proceed with a monolith rewrite if we preserve these invariants:
1. Strong domain boundaries in code (Identity, Bank, Board, Reputation, Court, Event Feed).
2. SQLite-first concurrency discipline (single writer, concurrent readers, atomic write+event transactions).
3. API compatibility for Task Board auth/state behavior where the current specs are explicit.

## Functional Delta vs Current Specs (Explicit)
This section answers: "If we build what this doc describes, does final functionality differ from `docs/specifications/*`?"

Intentional differences:
- **External API surface is reduced.** The current specs define externally reachable services for Identity, Central Bank, Reputation, Court, and DB Gateway. This monolith proposes exposing only Task Board (agent-facing), SSE Event Feed (observability-facing), and health. Everything else becomes in-process modules. This is a compatibility break for any client that expects to call those other service APIs directly.
- **No scheduled salary distribution.** The Central Bank spec and the schema’s event payload reference include salary concepts (e.g. `salary.paid`). This monolith explicitly does not mint/distribute money on a schedule; only one-time bootstrap funding (feeder funded, others start at 0).
- **`escrow_pending` becomes a no-op.** The Task Board API spec mentions `escrow_pending` and a "Central Bank unreachable" retry loop during lazy deadline evaluation. In a single-process monolith (Bank module + Board module share one DB transaction), that unreachable case does not exist. For spec/test compatibility, Task Board responses still include `escrow_pending: false`.
- **DB Gateway API is removed.** The DB Gateway spec requires "caller constructs events" and every mutating write includes an `event` object. In the monolith, the persistence layer takes the gateway’s role internally; we preserve the *pairing* invariant (domain write + event write in one transaction) while avoiding an external gateway network hop.

Where we keep behavior equivalent (even if implementation differs):
- **JWS verification is local.** Instead of calling an Identity HTTP endpoint to verify JWS, the monolith verifies Ed25519/JWS locally using the stored public key for the `kid`.

## Hard Compatibility Constraints
These are mandatory to avoid behavioral drift from the current specs.

### Authentication Contract Compatibility
- Keep **JWS compact** token model for Task Board operations.
- Keep existing token delivery patterns: POST endpoints use token in JSON body; asset upload and conditional bid listing in OPEN state use a Bearer token header.
- Do **not** introduce a replacement custom auth header as the only mechanism.

### Task Creation Two-Token Compatibility
- Preserve the Task Board `POST /tasks` contract: client supplies both `task_token` and `escrow_token`.
- Preserve the cross-validation semantics: `task_id` must match between tokens and `escrow_token.amount` must equal `task_token.reward`.
- Preserve the trust boundary semantics: Task Board verifies `task_token`; escrow lock authorization is verified by the Bank module (equivalent to the Central Bank service role), not by the Task Board.

### Task Board Read Auth Compatibility
- Keep Task Board GET behavior as specified: `GET /health`, `GET /tasks`, `GET /tasks/{id}`, and assets endpoints are public; `GET /tasks/{id}/bids` is authenticated only while task is OPEN, public otherwise.

### Deadline Semantics Compatibility
- Keep deadline handling **lazy on reads** for task lifecycle transitions.
- Do **not** enforce task deadlines via background cron/scheduler logic.
- Preserve atomicity/idempotency for concurrent deadline evaluation.

### Event and Write Atomicity Compatibility
- Every domain write that should emit an event must persist domain row(s) and event row in one transaction.
- Keep `events.event_id` as monotonic cursor used by SSE resume/replay semantics.
- Preserve the schema’s `events` row shape (`event_source`, `event_type`, `summary`, `payload`) and keep event creation non-optional for mutating writes.

## Goals
- Preserve economy rules and API semantics.
- Expose externally: Task Board API (agent-facing) and Event Feed API (observability-facing).
- Keep the job injector external (modeled as a normal agent).
- Keep other components internal method-call modules.
- Keep SQLite `economy.db` as the only source of truth.
- Keep implementation lean and testable.

## Non-Goals
- No distributed deployment in v1.
- No event broker in v1.
- No broad async orchestration graph.
- No ORM-heavy abstraction layers that hide SQL/state transitions.

## Stack (Minimal)
- Java 21 (virtual threads).
- Javalin (explicit routing, easy SSE).
- SQLite JDBC + HikariCP (write pool size 1, read pool size N).
- Jackson + SLF4J/Logback.

## Architecture Overview
- Single process.
- Componentized domains: `identity`, `bank`, `board`, `reputation`, `court`, `events`.
- Shared persistence module for SQLite access and transaction orchestration.
- External APIs limited to Task Board + Event Feed + Health.

## Architecture Diagram (ASCII)

```text
      +---------------------------+          +---------------------------+
      |       Agent Clients       |          | Observability Dashboard   |
      | (signed Task Board calls) |          |      (SSE consumer)       |
      +-------------+-------------+          +-------------+-------------+
                    |                                      |
                    | signed HTTP                          | SSE connect/reconnect
                    v                                      | (Last-Event-ID resume)
                    |                                      v
      +---------------------------+
      | External Feeder Agent     |
      | (normal agent poster;     |
      |  injects jobs only)       |
      +-------------+-------------+
                    |
                    | signed HTTP (same as any poster)
                    v
+------------------------ Java Monolith (Single Process) ------------------------+
|                                                                                 |
|  +-------------------------------+     +-------------------------------------+  |
|  | Task Board API (HTTP)         |     | Event Feed API (SSE endpoint)      |  |
|  | + Auth/JWS Verification       |     | /api/events/stream                  |  |
|  +---------------+---------------+     +-------------------+-----------------+  |
|                  |                                     |                         |
|                  v                                     | replay from DB on connect|
|  +--------------------------------------------------------------------------+   |
|  | Domain Components                                                        |   |
|  |                                                                          |   |
|  |   Identity      Central Bank      Task Board      Reputation             |   |
|  |                                                                          |   |
|  |   Court                                                                  |   |
|  +-----------+-----------+------------+-------------+------------+----------+   |
|              |           |            |             |            |              |
|              +-----------+------------+-------------+------------+--------------+
|                                   all read/write through persistence            |
|                                                                                 |
|               +---------------------------+---------------------------+         |
|               | Persistence Layer (SQLite)                           |         |
|               | - Read path: concurrent read pool                    |         |
|               | - Write path: single serialized write lane           |         |
|               | - Persistence enforces event pairing                 |         |
|               | - Each write transaction does:                       |         |
|               |     1) domain row(s)                                 |         |
|               |     2) matching `events` row                         |         |
|               |   in the SAME transaction                            |         |
|               | - On COMMIT: publish EventRecord to EventStreamHub   |         |
|               +---------------------------+---------------------------+         |
|                                           |                                     |
|                                           | live push (after commit)            |
|                                           v                                     |
|                               +---------------------------+                     |
|                               | EventStreamHub (in-memory)|                     |
|                               +-------------+-------------+                     |
|                                             |                                   |
|                                             | subscribe                          |
|                                             v                                   |
|                                Event Feed API pushes to SSE clients             |
+-------------------------------------------|-------------------------------------+
                                            v
                              +-------------+-------------+
                              |      economy.db           |
                              |   (SQLite WAL mode)       |
                              +---------------------------+
```

### Event Generation Flow
- Events are triggered from inside the persistence layer.
- Services request domain mutations; domain modules provide an `EventSpec`, and persistence executes the domain SQL and inserts the corresponding `events` row.
- The persistence layer inserts the `events` row in the same transaction as the domain write.
- If transaction fails, both domain write and event write are rolled back.

### Event Delivery Path (Trigger -> Listener)
1. A service requests a domain mutation (create task, submit bid, lock escrow, etc.).
2. Persistence writes domain row(s) and the matching `events` row atomically.
3. After successful commit, persistence publishes the committed `EventRecord` to `EventStreamHub`.
4. The SSE handler subscribes to `EventStreamHub` and pushes committed events to connected clients immediately.
5. On connect/reconnect, the SSE handler first replays from SQLite using `Last-Event-ID`, then resumes live push.

### SSE Catch-up Rule
- The SSE endpoint must be correct even if in-memory live push fails.
- On connect/reconnect: replay from SQLite using `Last-Event-ID`, then switch to live push.

## Threading and Concurrency Model

### Request Handling
- Use request-per-virtual-thread.
- Keep request code synchronous and readable.

### Database Access
- Reads: concurrent (read-only pool).
- Writes: serialized via a single-connection write pool (`maxPoolSize=1`) and explicit transactions (`BEGIN IMMEDIATE` semantics).

### Thread Offloading Rules
- Do not offload JWS verification by default.
- Do not offload JSON parsing/serialization.
- Keep background workers bounded and explicit (SSE/event push mechanics; optional judge worker pool).

### Answer to "one thread per request + transactions?"
- Yes: one virtual thread per request.
- No: not one independent SQLite writer transaction in parallel per request.
- Use virtual-thread isolation + controlled single-writer policy.

## Authentication and Trust Boundaries

### Shared Security Package
- Introduce a shared package used by all modules that need signing or signed-message verification.
- This package is not tied to a single domain service; it is reusable infrastructure.
- Keep it small: `JwsTokenVerifier`, `JwsTokenSigner`, `SignedMessageAuthenticator`, `KeyMaterialLoader`.

### External Boundary
- Verify agent/platform JWS at external Task Board endpoints.
- Validate action and payload according to endpoint contract.
- Apply same error precedence model as current specs.

### Internal Boundary
- After successful auth, convert to internal principal context.
- Internal component calls do not re-authenticate.

### Response Signing (Defer)
- Not required to meet current specs; defer until there is a concrete response-signature spec.

## Persistence Model
Keep a thin DB layer equivalent in guarantees to the current DB gateway intent:
- Domain services decide behavior and state transitions.
- Persistence layer owns SQL execution and transaction scope.
- Persistence enforces paired event writes for mutating operations in the same transaction.
- Constraint violations are mapped to stable domain error codes.

### Mutating Write Contract
- Service calls a domain-specific persistence writer method (analogous to the DB gateway surface: `create_task`, `lock_escrow`, `submit_bid`, etc.).
- Persistence performs: domain mutation SQL; insertion of the matching `events` row (event content is provided by the domain module as an `EventSpec`); commit; publish of the committed `EventRecord` to `EventStreamHub`.
- Persistence returns the domain result and (when needed) the generated `event_id`.

## API Surface (External)

### Task Board API
Expose task lifecycle operations and related reads. Preserve:
- signed write operations,
- conditional auth on bids listing during OPEN,
- lazy deadline transitions on reads.

### Event Feed API
- Event feed is **SSE-only**:
- `GET /api/events/stream`
- Resume support uses standard SSE `Last-Event-ID` with monotonic `event_id`.
- Events are emitted in ascending `event_id` order.

## Feeder Design (External, Job Injection Only)
Feeder is not part of the monolith.

### Feeder As a Normal Agent
- The feeder is modeled as a regular agent that posts tasks through the Task Board API.
- It signs `task_token` as `poster_id` and provides an agent-signed `escrow_token` to lock escrow (same flow as any other poster).
- The monolith does not run salary distribution and does not mint money on a schedule.

### Initial Balances (Bootstrapped Once)
- All agents start with 0 except the feeder agent, which starts funded.
- Bootstrap procedure:
  1. Register feeder agent in Identity.
  2. Create feeder bank account with a positive initial balance (platform-initialized).
  3. Create other agents' bank accounts with `initial_balance = 0`.

## Proposed Project Layout

```text
java-agent-economy/
  build.gradle.kts
  settings.gradle.kts
  README.md
  config/
    application.yml
    schema.sql
  src/
    main/
      java/
        com/agenteconomy/
          AgentEconomyApplication.java
          shared/
            security/
              JwsTokenVerifier.java
              JwsTokenSigner.java
              SignedMessageAuthenticator.java
              KeyMaterialLoader.java
          api/
            taskboard/
              TaskBoardController.java
            events/
              EventFeedController.java
            health/
              HealthController.java
            middleware/
              AuthInterceptor.java
              PrincipalContext.java
          domain/
            identity/...
            bank/...
            board/...
            reputation/...
            court/...
            events/
              EventRecord.java
              EventStreamHub.java
          persistence/
            sqlite/
              SqliteDataSourceFactory.java
              SqlitePragmas.java
              TransactionRunner.java
              SqlErrorMapper.java
          common/...
    test/
      java/
        com/agenteconomy/
          unit/...
          integration/...
          performance/...
```

## Key Classes (Minimum)
- `persistence.sqlite.TransactionRunner`: runs `BEGIN IMMEDIATE` write transactions and publishes committed events to `EventStreamHub`.
- `events.EventStreamHub`: in-memory pub/sub for committed events.
- `api.events.EventFeedController`: SSE endpoint that replays from SQLite on connect, then subscribes to `EventStreamHub`.
- `api.middleware.AuthInterceptor`: validates JWS where required by spec.
- `shared.security.*`: reusable JWS verify/sign helpers.

## Testing Strategy
- Unit tests per domain service.
- Integration tests against real SQLite schema.
- Concurrency tests: duplicate bid races; lazy deadline race idempotency; idempotent credit/escrow behaviors.
- Contract tests for Task Board auth/token delivery rules.
- Event feed cursor/SSE tests.
- No internal feeder tests (feeder is external).

## Migration Plan
1. Build shared persistence + schema bootstrap.
2. Port Identity + Bank modules.
3. Port Task Board and external endpoints with auth compatibility.
4. Port Reputation + Court internal modules.
5. Add Event Feed (SSE with Last-Event-ID resume).
6. Run external feeder agent against Task Board API.
7. Run compatibility suite against existing documented behavior.

## Final Recommendation
Proceed with the Java monolith rewrite using:
- componentized domain modules,
- external API limited to Task Board + Event Feed,
- virtual-thread request isolation,
- controlled single-writer SQLite policy,
- strict compatibility with JWS auth delivery and lazy deadline semantics from existing specs.

This gives better maintainability and performance without over-engineering or behavior drift.
