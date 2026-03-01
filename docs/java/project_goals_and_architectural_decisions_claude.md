# Agent Task Economy — Java Monolith Rewrite

## Scope and Inputs

This document is derived from the existing specifications in `docs/`:
- `docs/specifications/schema.sql` — unified SQLite schema
- `docs/specifications/service-api/observatory-service-specs.md` — API contract and data model
- `docs/specifications/service-tests/observatory-service-tests.md` — test expectations
- `services/observatory/data/seed.py` and `simulate.py` — SQLite data format and event log structure

It defines a lean Java monolith architecture that preserves current behavior while removing inter-service network hops.

## Goals

- Preserve the economy rules and API semantics from the current specs.
- Expose only:
  - Task Board API (agent-facing, authenticated)
  - Event Feed API (observatory-facing, public)
  - Observatory read-only API (metrics, agents, tasks, quarterly — public)
- Keep internal components private (Identity, Bank, Reputation, Court, Feeder, DB layer).
- Keep SQLite as the system of record, including `events` table semantics.
- Keep implementation simple enough to debug and test locally.

## Non-Goals

- No distributed deployment in v1.
- No event broker (Kafka/RabbitMQ) in v1.
- No over-generalized plugin framework for components.
- No uncontrolled async fan-out for writes.
- No ORM — SQL is the interface to SQLite.
- No caching layer — SQLite is local, query latency is ~1ms.

---

## Why Rewrite

| Current (Python microservices) | Target (Java monolith) |
|---|---|
| 6 separate processes, 6 ports, 6 Docker containers | 1 process, 1 port, 1 container |
| Services talk through shared DB (no HTTP calls anyway) | Direct method calls — formalize what's already true |
| Each service re-implements config, logging, health, error handling | Single shared infrastructure |
| Testing requires spinning up all services | One JVM, one test harness, full integration tests |
| Python async (aiosqlite) for what is fundamentally synchronous SQLite I/O | JDBC — straightforward blocking calls on virtual threads |
| Authentication scattered across services | Single verification layer at the HTTP boundary |

### What Changes

- **Internal services become componentized domain modules.** Each owns its domain logic; SQL lives in dedicated repository classes. Services call each other directly via method calls.
- **One HTTP layer.** Task Board routes + Event Feed routes + Observatory routes + Health endpoint.
- **PKI authentication happens once, at the API boundary.** Incoming requests are signature-verified against the agent's public key. Internal calls are trusted.
- **New: Global Feeder.** A background component that distributes salary on a schedule and injects tasks on a virtual clock.
- **Observatory stays separate.** The React SPA is unchanged — only the backend URL changes.

### What Doesn't Change

- **SQLite database.** Same schema, same file, same WAL mode.
- **Event log.** Same `events` table format. All services write events; the Event Feed streams them via SSE.
- **Domain logic.** GDP calculation, escrow lifecycle, dispute resolution, feedback revelation — all the same business rules.

---

## Tech Stack

| Component | Choice | Rationale |
|---|---|---|
| **Language** | Java 21+ | Virtual threads, modern syntax (records, sealed interfaces, pattern matching) |
| **HTTP server** | Javalin 6 | Micro-framework on Jetty. Routing, SSE, static files. No annotation magic, no DI container, no classpath scanning. You read the code, you understand the code. |
| **Database** | SQLite via sqlite-jdbc | Same database, same schema. JDBC with parameterized queries. |
| **Connection pool** | HikariCP | Fast, battle-tested. Read pool (N connections) + write pool (1 connection). |
| **JSON** | Jackson | Industry standard. Fast, handles records natively. |
| **Crypto** | java.security + Bouncy Castle | Ed25519 signing/verification via Java's built-in `EdDSA` provider. Bouncy Castle as fallback for non-standard key formats. |
| **Config** | SnakeYAML | Load `config.yaml` into a record. Explicit, no magic. |
| **Logging** | SLF4J + Logback | Structured JSON logging. |
| **Testing** | JUnit 5 + AssertJ | In-process SQLite — no containers needed. |
| **Build** | Gradle (Kotlin DSL) | `./gradlew run`, `./gradlew test`, `./gradlew shadowJar` for fat JAR. |

### Why Not Spring Boot

Spring Boot solves problems we don't have: service discovery, complex DI graphs, auto-configuration of dozens of libraries, externalized config across environments. Our application is a single process reading/writing a single SQLite file. Spring would add 30+ transitive dependencies, a 5-second startup time, and annotation-driven behavior that makes control flow invisible. Javalin gives us explicit routing in ~50 lines and boots in under 500ms.

If the team already knows Spring and values speed of delivery over transparency, Spring Boot is a reasonable alternative. The architectural decisions in this document (virtual threads, write coordinator, domain boundaries) apply equally to either framework.

---

## Architecture Overview

```
                    ┌─────────────────────────────────────────────────┐
                    │                  Javalin HTTP                    │
                    │                                                  │
                    │  TaskBoardController   EventFeedController       │
                    │  ObservatoryController HealthController          │
                    │                                                  │
                    ├──────────┬───────────────────────────────────────┤
                    │  AuthInterceptor       ResponseSigner            │
                    ├──────────┴───────────────────────────────────────┤
                    │                                                  │
                    │  ┌────────────┐  ┌──────────┐  ┌─────────────┐  │
                    │  │ Identity   │  │ Central  │  │ Task Board  │  │
                    │  │ Service    │  │ Bank     │  │ Service     │  │
                    │  │            │  │ Service  │  │             │  │
                    │  └────────────┘  └──────────┘  └─────────────┘  │
                    │  ┌────────────┐  ┌──────────┐  ┌─────────────┐  │
                    │  │ Reputation │  │ Court    │  │ Global      │  │
                    │  │ Service    │  │ Service  │  │ Feeder      │  │
                    │  └────────────┘  └──────────┘  └─────────────┘  │
                    │                                                  │
                    │  ┌────────────┐  ┌──────────────────────────┐   │
                    │  │ Event Log  │  │ WriteCoordinator         │   │
                    │  │ (writes)   │  │ (serialized write lane)  │   │
                    │  └────────────┘  └──────────────────────────┘   │
                    │                                                  │
                    │  ┌──────────────────────────────────────────┐   │
                    │  │           SQLite (WAL mode)               │   │
                    │  │  Read pool (N conns, ?mode=ro)            │   │
                    │  │  Write pool (1 conn, via WriteCoordinator)│   │
                    │  └──────────────────────────────────────────┘   │
                    └─────────────────────────────────────────────────┘
```

---

## Threading and Request Handling Decisions

This is the critical section. The wrong threading model makes the application either slow (too serial) or buggy (too concurrent).

### The Constraint: SQLite

SQLite in WAL mode allows:
- **Multiple concurrent readers** — any number of threads can SELECT simultaneously
- **One writer at a time** — writes are serialized by SQLite's internal lock
- **Readers don't block writers, writers don't block readers** — this is the whole point of WAL

### Decision 1: Request-per-virtual-thread

Use Java 21 virtual threads for HTTP request handling. Each incoming request runs on its own virtual thread (~few KB of stack, scheduled onto a small number of platform threads). This gives request isolation and simple synchronous code paths without high thread cost. No callbacks, no reactive streams, no `CompletableFuture` chains — just blocking code that reads like sequential logic.

### Decision 2: Do not make every operation async by default

Keep endpoint logic synchronous in the request flow. Do not offload Ed25519/JWS verification to separate thread pools — it is ~0.1ms, fast and predictable. Do not offload JSON serialization. Thread handoff would cost more than the operations themselves.

### Decision 3: Single serialized write lane (WriteCoordinator)

All database mutations go through a `WriteCoordinator` — a single-thread executor that processes write commands sequentially. Each command runs in one JDBC transaction and writes domain row(s) plus matching event row(s) atomically. This preserves the database gateway guarantee and avoids SQLite write-lock contention.

Why a dedicated `WriteCoordinator` instead of just a HikariCP pool with `maximumPoolSize=1`:
- **Named abstraction.** You can reason about "the write lane" as a concept, monitor its queue depth, and shut it down gracefully.
- **Backpressure.** The command queue provides clear observability and failure modes when writes pile up.
- **Graceful shutdown.** `stopGracefully()` drains pending writes before closing the connection.

```java
public class WriteCoordinator {
    private final ExecutorService writeExecutor = Executors.newSingleThreadExecutor();
    private final DataSource writeDataSource;

    /** Submit a write command. Returns a Future with the result. */
    public <T> CompletableFuture<T> submitWrite(Function<Connection, T> command) {
        return CompletableFuture.supplyAsync(() -> {
            try (var conn = writeDataSource.getConnection()) {
                conn.setAutoCommit(false);
                try {
                    T result = command.apply(conn);
                    conn.commit();
                    return result;
                } catch (Exception e) {
                    conn.rollback();
                    throw e;
                }
            }
        }, writeExecutor);
    }

    public void start() { /* validate connection */ }
    public void stopGracefully() { writeExecutor.shutdown(); }
}
```

### Decision 4: Concurrent reads

Reads run directly on request virtual threads using read-only connections from the read pool (`?mode=ro`). WAL mode allows reads while the write lane is active. No coordination needed.

### Decision 5: Bounded background workers only where needed

| Component | Threading | Why |
|---|---|---|
| **HTTP request handling** | Virtual thread per request | Isolation. One slow query doesn't block another. |
| **SSE event streaming** | Virtual thread per connected client | Long-lived loops that poll DB and write to response. Virtual threads handle this elegantly. |
| **Global Feeder** | Single scheduled thread (`ScheduledExecutorService`) | Timer-based salary and task injection. Submits writes through the `WriteCoordinator`. |
| **Court Judge Executor** | Bounded pool (default size 1) | For LLM judge calls which are slow and external. Size 1 matches current sequential judging; configurable for parallel panel later. |
| **PKI signature verification** | Same thread as request | ~0.1ms. Not worth a thread handoff. |
| **JSON serialization** | Same thread as request | CPU-bound but fast. |
| **Event log writes** | Part of the write transaction | Not a background task — atomic with the domain mutation. |

### Answer: "Should every request run in its own thread with transactions?"

- **Yes for request isolation:** one virtual thread per request.
- **No for unconstrained concurrent writes:** do not let each request open arbitrary write transactions against SQLite. All writes funnel through the `WriteCoordinator`.
- The model is: request-level isolation + centralized write serialization.

---

## Authentication and Trust Boundary

### Design

Authentication occurs only at external Task Board endpoints. Verified identity is converted to an internal principal object, then passed through method calls. Internal component calls do not re-authenticate.

Every agent request to the Task Board API must include:

```
Authorization: ATE-Ed25519 <agent_id>:<base64-signature>
X-ATE-Timestamp: <ISO-8601-instant>
```

**Signature construction** (handled by `SignatureCanonicalizer`):

```
<HTTP-method> <path>\n<timestamp>\n<SHA-256-of-body>
```

**Verification flow:**

```
HTTP Request
  │
  ├── 1. Extract agent_id from Authorization header
  ├── 2. Look up public_key from IdentityService (in-memory cache)
  ├── 3. Canonicalize request via SignatureCanonicalizer
  ├── 4. Verify Ed25519 signature against canonical content
  ├── 5. Check timestamp is within ±5 minutes (replay protection)
  ├── 6. If valid → create PrincipalContext, attach to request
  │      If invalid → 401 Unauthorized
  │
  └── Internal service calls: NO FURTHER AUTH
      taskBoardService.createTask(principal.agentId(), ...) ← trusted
```

### Response signing

Outgoing responses include four headers for robustness:

```
X-Board-Signature: <base64-signature>
X-Board-Key-Id: <server-key-identifier>
X-Board-Signature-Ts: <ISO-8601-instant>
X-Board-Signature-Alg: Ed25519
```

The signature covers: `<status-code>\n<SHA-256-of-body>\n<timestamp>\n<request-id>`.

Including the key ID allows key rotation. Including the algorithm allows future algorithm changes. Including a timestamp prevents response replay.

---

## Database Layer

Thin but explicit, conceptually equivalent to the current DB gateway pattern:
- Domain services own business decisions.
- Repository classes own SQL execution and result mapping.
- `WriteCoordinator` owns transaction handling and write serialization.
- `SqlErrorMapper` translates SQLite constraint violations into domain errors (e.g., unique constraint → `DUPLICATE_BID`).
- Every mutating call writes both domain state and an `EventRecord` in one transaction.

```java
public class TransactionRunner {
    /** Run a read-only query on the read pool. */
    public <T> T runReadOnly(Function<Connection, T> fn) { ... }

    /** Submit a write through the WriteCoordinator. Blocks until complete. */
    public <T> T runWrite(Function<Connection, T> fn) {
        return writeCoordinator.submitWrite(fn).join();
    }
}
```

---

## Project Structure

```
ate-server/
├── build.gradle.kts
├── settings.gradle.kts
├── config.yaml
├── src/
│   ├── main/
│   │   ├── java/com/ate/
│   │   │   ├── App.java                               # main(), Javalin setup, route registration
│   │   │   │
│   │   │   ├── bootstrap/
│   │   │   │   ├── DependencyWiring.java               # Constructs all services, explicit constructor injection
│   │   │   │   └── StartupValidator.java               # Validates config, DB schema, server key on boot
│   │   │   │
│   │   │   ├── config/
│   │   │   │   └── AppConfig.java                      # Record: loads config.yaml via SnakeYAML
│   │   │   │
│   │   │   ├── persistence/
│   │   │   │   ├── DatabaseManager.java                # HikariCP setup: read pool + write data source
│   │   │   │   ├── WriteCoordinator.java               # Single-thread write executor with transaction mgmt
│   │   │   │   ├── TransactionRunner.java              # runReadOnly() + runWrite() convenience wrapper
│   │   │   │   ├── SchemaInitializer.java              # Runs schema.sql + PRAGMA setup on startup
│   │   │   │   ├── SqlitePragmas.java                  # WAL, foreign_keys, busy_timeout setup
│   │   │   │   └── SqlErrorMapper.java                 # Maps SQLite constraint errors → domain errors
│   │   │   │
│   │   │   ├── api/
│   │   │   │   ├── taskboard/
│   │   │   │   │   ├── TaskBoardController.java        # Agent-facing HTTP endpoints (authed)
│   │   │   │   │   └── TaskBoardDtos.java              # Request/response records
│   │   │   │   ├── events/
│   │   │   │   │   ├── EventFeedController.java        # SSE stream + paginated history (public)
│   │   │   │   │   └── EventDtos.java                  # Event response records
│   │   │   │   ├── observatory/
│   │   │   │   │   ├── ObservatoryController.java      # Metrics, agents, tasks, quarterly (public, read-only)
│   │   │   │   │   └── ObservatoryDtos.java            # Metrics/agent/task response records
│   │   │   │   └── health/
│   │   │   │       └── HealthController.java           # GET /health
│   │   │   │
│   │   │   ├── security/
│   │   │   │   ├── AuthInterceptor.java                # Javalin before-handler: verify request signature
│   │   │   │   ├── SignatureVerifier.java              # Ed25519 verify logic
│   │   │   │   ├── SignatureCanonicalizer.java         # Canonical form for signing: method + path + ts + hash
│   │   │   │   ├── ResponseSigner.java                 # Ed25519 sign for outgoing responses (4 headers)
│   │   │   │   ├── ServerKeyPair.java                  # Loads/generates the server's Ed25519 key pair
│   │   │   │   └── PrincipalContext.java               # Verified agent identity, attached to request
│   │   │   │
│   │   │   ├── domain/
│   │   │   │   ├── identity/
│   │   │   │   │   ├── IdentityService.java            # Agent registration, public key cache, name resolution
│   │   │   │   │   ├── IdentityRepository.java         # SQL: identity_agents CRUD
│   │   │   │   │   └── Agent.java                      # Record: agent_id, name, public_key, registered_at
│   │   │   │   │
│   │   │   │   ├── bank/
│   │   │   │   │   ├── CentralBankService.java         # Accounts, credit, escrow lock/release/split
│   │   │   │   │   ├── BankRepository.java             # SQL: bank_accounts, bank_transactions, bank_escrow
│   │   │   │   │   ├── Account.java                    # Record: account_id, balance
│   │   │   │   │   ├── Escrow.java                     # Record: escrow_id, amount, status, task_id
│   │   │   │   │   └── TransactionEntry.java           # Record: tx_id, type, amount, balance_after
│   │   │   │   │
│   │   │   │   ├── board/
│   │   │   │   │   ├── TaskBoardService.java           # Task lifecycle state machine
│   │   │   │   │   ├── TaskBoardRepository.java        # SQL: board_tasks, board_bids, board_assets
│   │   │   │   │   ├── Task.java                       # Record: full task state
│   │   │   │   │   ├── Bid.java                        # Record: bid_id, bidder_id, proposal
│   │   │   │   │   ├── Asset.java                      # Record: asset metadata
│   │   │   │   │   └── DeadlineEvaluator.java          # Compute absolute deadlines, check expiry
│   │   │   │   │
│   │   │   │   ├── reputation/
│   │   │   │   │   ├── ReputationService.java          # Feedback submission, sealed→visible logic
│   │   │   │   │   ├── ReputationRepository.java       # SQL: reputation_feedback
│   │   │   │   │   └── Feedback.java                   # Record: feedback data
│   │   │   │   │
│   │   │   │   ├── court/
│   │   │   │   │   ├── CourtService.java               # Claims, rebuttals, trigger ruling
│   │   │   │   │   ├── CourtRepository.java            # SQL: court_claims, court_rebuttals, court_rulings
│   │   │   │   │   ├── Dispute.java                    # Record: claim + rebuttal + ruling
│   │   │   │   │   ├── JudgeVote.java                  # Record: judge, worker_pct, reason
│   │   │   │   │   └── judges/
│   │   │   │   │       ├── Judge.java                  # Interface: evaluate(DisputeContext) → JudgeVote
│   │   │   │   │       ├── LlmJudge.java               # LLM-backed judge implementation
│   │   │   │   │       └── JudgePanel.java             # Orchestrates N judges, computes median worker_pct
│   │   │   │   │
│   │   │   │   ├── feeder/
│   │   │   │   │   ├── GlobalFeederService.java        # Salary distribution, task injection, deadline enforcement
│   │   │   │   │   ├── RoundScheduler.java             # start(), stop(), triggerNow(), advanceVirtualClock()
│   │   │   │   │   ├── VirtualClock.java               # Testable time source (real or deterministic)
│   │   │   │   │   └── TaskInjectionPlan.java          # Configurable task templates for injection
│   │   │   │   │
│   │   │   │   └── events/
│   │   │   │       ├── EventService.java               # Write events, query events
│   │   │   │       ├── EventRepository.java            # SQL: events table
│   │   │   │       └── EventRecord.java                # Record: event_id, source, type, payload, etc.
│   │   │   │
│   │   │   └── common/
│   │   │       ├── errors/
│   │   │       │   ├── ApiException.java               # Status code + error code exception
│   │   │       │   └── ErrorEnvelope.java              # Record: { error, message }
│   │   │       ├── ids/
│   │   │       │   ├── IdGenerator.java                # UUID-based ID generation with format prefixes
│   │   │       │   └── IdFormats.java                  # Validation: a-<uuid>, t-<uuid>, bid-<uuid>, etc.
│   │   │       ├── time/
│   │   │       │   └── TimeProvider.java               # Interface: now(). Testable — real or fixed clock.
│   │   │       └── json/
│   │   │           └── JsonCodec.java                  # Jackson ObjectMapper singleton, record support
│   │   │
│   │   └── resources/
│   │       ├── schema.sql                              # SQLite schema (same as current)
│   │       └── logback.xml                             # JSON structured logging config
│   │
│   └── test/
│       ├── java/com/ate/
│       │   ├── TestDb.java                             # In-memory SQLite + schema for tests
│       │   ├── SeedData.java                           # Standard economy fixture (5 agents, 8 tasks, etc.)
│       │   ├── FixedTimeProvider.java                  # Deterministic time for tests
│       │   ├── domain/
│       │   │   ├── identity/
│       │   │   │   └── IdentityServiceTest.java
│       │   │   ├── bank/
│       │   │   │   └── CentralBankServiceTest.java
│       │   │   ├── board/
│       │   │   │   ├── TaskBoardServiceTest.java       # State machine transitions
│       │   │   │   └── DeadlineEvaluatorTest.java
│       │   │   ├── reputation/
│       │   │   │   └── ReputationServiceTest.java
│       │   │   ├── court/
│       │   │   │   ├── CourtServiceTest.java
│       │   │   │   └── JudgePanelTest.java
│       │   │   └── feeder/
│       │   │       └── GlobalFeederServiceTest.java    # Deterministic via VirtualClock
│       │   ├── api/
│       │   │   ├── TaskBoardControllerTest.java        # HTTP integration
│       │   │   ├── EventFeedControllerTest.java        # SSE streaming
│       │   │   └── ObservatoryControllerTest.java      # Read-only endpoints
│       │   ├── security/
│       │   │   └── AuthInterceptorTest.java            # Valid/invalid/missing/expired signatures
│       │   ├── persistence/
│       │   │   └── WriteCoordinatorTest.java
│       │   ├── concurrency/
│       │   │   ├── DuplicateBidRaceTest.java           # Two bids from same agent simultaneously
│       │   │   ├── DeadlineDoubleFireTest.java         # Deadline enforcement doesn't trigger twice
│       │   │   └── EscrowIdempotencyTest.java          # Concurrent escrow lock for same task
│       │   └── integration/
│       │       └── FullLifecycleTest.java              # Register → post → bid → accept → submit → approve
│       └── resources/
│           └── test-config.yaml
```

---

## Class and Method Blueprint

### API Layer

**TaskBoardController** (authenticated, agent-facing):
- `registerAgent(RegisterRequest)` — create agent + bank account
- `createTask(CreateTaskRequest)` — post task, lock escrow
- `listTasks(filters)` — paginated task list
- `getTask(taskId)` — task drilldown
- `submitBid(taskId, BidRequest)` — bid on open task
- `listBids(taskId)` — bids for a task
- `acceptBid(taskId, AcceptRequest)` — poster accepts a bid
- `uploadAsset(taskId, file)` — worker uploads deliverable
- `submitWork(taskId)` — worker marks work as submitted
- `approveWork(taskId)` — poster approves, releases escrow
- `disputeWork(taskId, DisputeRequest)` — poster disputes, files claim
- `submitRebuttal(taskId, RebuttalRequest)` — worker responds to dispute
- `cancelTask(taskId)` — poster cancels open task, refunds escrow

**EventFeedController** (public):
- `streamEvents(afterEventId)` — SSE stream with cursor-based pagination
- `listEvents(limit, before, after, source, type, agentId, taskId)` — paginated history

**ObservatoryController** (public, read-only):
- `getMetrics()` — aggregated economy metrics
- `getGdpHistory(window, resolution)` — time-series GDP
- `listAgents(sortBy, order, limit, offset)` — agent leaderboard
- `getAgent(agentId)` — agent profile with stats and history
- `getTask(taskId)` — task drilldown (public view)
- `getCompetitiveTasks(limit, status)` — top by bid count
- `getUncontestedTasks(minAgeMinutes, limit)` — zero-bid tasks
- `getQuarterlyReport(quarter)` — economy snapshot

**HealthController**:
- `health()` — status, uptime, latest_event_id, database_readable

### Domain Layer

**IdentityService**:
- `registerAgent(conn, name, publicKey)` → `Agent`
- `getAgent(agentId)` → `Agent`
- `getPublicKey(agentId)` → `String` (from cache)
- `agentName(agentId)` → `String` (for event summaries)
- `listAgents()` → `List<Agent>`
- `allAgentIds()` → `List<String>`

**CentralBankService**:
- `createAccount(conn, agentId)`
- `creditAccount(conn, agentId, amount, reference)` — idempotent via reference key
- `getAccount(agentId)` → `Account`
- `lockEscrow(conn, payerId, amount, taskId)` → `String` (escrow_id)
- `releaseEscrow(conn, escrowId, recipientId)`
- `splitEscrow(conn, escrowId, workerPct)`
- `listTransactions(agentId)` → `List<TransactionEntry>`

**TaskBoardService** (core state machine):
- `createTaskWithEscrow(posterId, title, spec, reward, deadlines)` → `Task`
- `submitBid(taskId, bidderId, proposal)` → `Bid`
- `acceptBid(taskId, posterId, bidId)`
- `submitWork(taskId, workerId)`
- `approveWork(taskId, posterId)` — releases escrow
- `disputeWork(taskId, posterId, reason)` — files court claim
- `cancelTask(taskId, posterId)` — refunds escrow
- `getTask(taskId)` → `Task`
- `listTasks(filters)` → `List<Task>`
- `getCompetitiveTasks(limit)` → `List<Task>`
- `getUncontestedTasks(minAgeMinutes, limit)` → `List<Task>`

**DeadlineEvaluator**:
- `evaluateForTaskRead(task, now)` — compute derived deadline state
- `evaluateForTaskList(tasks, now)` — batch evaluation
- `findExpired(now)` → `List<Task>` — tasks past their deadline

**ReputationService**:
- `submitFeedback(conn, taskId, fromId, toId, role, category, rating, comment)`
- `computeVisibility(conn, taskId)` — reveal if both parties submitted
- `getFeedbackForAgent(agentId)` → `List<Feedback>`
- `getFeedbackForTask(taskId)` → `List<Feedback>`
- `getSpecQualityStats()` → `QualityStats`

**CourtService**:
- `fileClaim(conn, taskId, claimantId, respondentId, reason)`
- `submitRebuttal(claimId, agentId, content)`
- `triggerRuling(claimId)` — invokes JudgePanel, splits escrow
- `getDispute(claimId)` → `Dispute`

**JudgePanel**:
- `evaluate(DisputeContext)` → `List<JudgeVote>`
- `medianWorkerPct(votes)` → `int`
- `buildRulingSummary(votes)` → `String`

**GlobalFeederService**:
- `runRound(RoundContext)` — salary + task injection in one coordinated step
- `distributeSalary(conn, amount, roundReference)`
- `injectTask(conn)` — random task from `TaskInjectionPlan`
- `enforceDeadlines()` — expire overdue tasks

**RoundScheduler**:
- `start()` — begin scheduled rounds
- `stop()` — graceful shutdown
- `triggerNow()` — manual trigger for testing
- `advanceVirtualClock(duration)` — advance deterministic clock

**EventService**:
- `write(conn, source, eventType, taskId, agentId, summary, payload)` → `int` (event_id)
- `getEventsSince(lastEventId, batchSize)` → `List<EventRecord>`
- `getEvents(limit, before, after, source, type, agentId, taskId)` → `EventPage`
- `getLatestEventId()` → `int`

### Persistence Layer

**WriteCoordinator**:
- `submitWrite(Function<Connection, T>)` → `CompletableFuture<T>`
- `start()`
- `stopGracefully()`

**TransactionRunner**:
- `runReadOnly(Function<Connection, T>)` → `T`
- `runWrite(Function<Connection, T>)` → `T` (blocks via WriteCoordinator)

---

## How Existing Services Map into Internal Components

| Current service | Java component | Notes |
|---|---|---|
| Identity Service (8001) | `domain.identity` | In-memory agent cache for key lookups |
| Central Bank Service (8002) | `domain.bank` | Idempotent credits, escrow lifecycle |
| Task Board Service (8003) | `domain.board` + `api.taskboard` | Only component with external API |
| Reputation Service (8004) | `domain.reputation` | Sealed→visible feedback revelation |
| Court Service (8005) | `domain.court` + `domain.court.judges` | LLM judge integration |
| DB Gateway | `persistence.*` + `WriteCoordinator` | Write serialization abstraction |
| Observatory dashboard | `api.events` + `api.observatory` | Read-only, public endpoints |

---

## Event Feed Design

- Source of truth: `events` table in the same SQLite file.
- Endpoints:
  - Polling: `GET /api/events?after=N&limit=L` (+ filters by source, type, agent_id, task_id)
  - Streaming: `GET /api/events/stream?last_event_id=N` (SSE)
- Cursor: monotonic `event_id` (AUTOINCREMENT).
- Ordering: ascending `event_id` for stream, descending for history API.
- Payload: keep existing `summary` + opaque JSON `payload` to match current spec.

SSE implementation:

```java
app.sse("/api/events/stream", client -> {
    int cursor = parseLastEventId(client);
    client.sendEvent("retry", String.valueOf(config.retryMs()));

    while (!client.terminated()) {
        var events = eventService.getEventsSince(cursor, config.batchSize());
        if (!events.isEmpty()) {
            for (var e : events) {
                client.sendEvent("economy_event", toJson(e), String.valueOf(e.eventId()));
                cursor = e.eventId();
            }
        } else {
            client.sendComment("keepalive");
            Thread.sleep(config.pollIntervalMs());
        }
    }
});
```

---

## Configuration

```yaml
server:
  port: 8006
  host: "0.0.0.0"

database:
  path: "data/economy.db"
  read_pool_size: 4
  write_pool_size: 1                  # must be 1 for SQLite

sse:
  poll_interval_ms: 1000
  keepalive_interval_ms: 15000
  batch_size: 50
  retry_ms: 3000

feeder:
  enabled: true
  salary_interval_minutes: 30
  salary_amount: 500
  task_interval_seconds: 60
  task_titles:
    - "Build payment gateway integration"
    - "Design REST API for inventory service"
    # ... (from simulate.py TASK_TITLES)

court:
  judge_pool_size: 1                  # parallel judge calls (1 = sequential)
  llm_endpoint: "http://localhost:11434/v1"

auth:
  server_key_path: "data/server.key"  # Ed25519 private key for response signing
  timestamp_tolerance_minutes: 5

logging:
  level: "INFO"
  format: "json"

frontend:
  dist_path: "frontend/dist"
```

---

## Error Handling

Same contract as the current system:

```json
{
  "error": "TASK_NOT_FOUND",
  "message": "No task with ID t-abc123"
}
```

`ApiException` carries HTTP status code and error code. `SqlErrorMapper` translates SQLite constraint violations into domain-specific `ApiException` instances (e.g., unique constraint on `(task_id, bidder_id)` → 409 `DUPLICATE_BID`).

```java
app.exception(ApiException.class, (e, ctx) -> {
    ctx.status(e.statusCode());
    ctx.json(new ErrorEnvelope(e.errorCode(), e.getMessage()));
});
```

---

## Testing Strategy

SQLite makes testing trivial. Every test creates a fresh in-memory database, runs the schema, seeds test data, and runs assertions. No containers, no network, no cleanup.

```java
@BeforeEach
void setup() {
    var timeProvider = new FixedTimeProvider(Instant.parse("2026-02-28T12:00:00Z"));
    db = TestDb.createInMemory();
    db.initializeSchema();
    SeedData.standard(db);
    // Wire services with fixed time and test DB...
}
```

**Test categories:**

1. **Unit tests per domain service** — test each method with a fresh DB and `FixedTimeProvider`
2. **Repository tests** — verify SQL queries return correct shapes
3. **Integration tests** — full lifecycle through HTTP: register → post → bid → accept → submit → approve, verify events and balances
4. **Auth tests** — valid/invalid/missing/expired signatures
5. **Concurrency tests** — critical for correctness:
   - Duplicate bid races (two bids from same agent simultaneously)
   - Deadline double-fire prevention (enforcement doesn't trigger twice)
   - Escrow idempotency (concurrent lock for same task)
   - Credit idempotency (duplicate salary reference)
6. **Feeder tests** — deterministic via `VirtualClock` and seeded `TaskInjectionPlan`
7. **SSE tests** — connect, receive events, cursor resumption, keepalive
8. **Edge cases** — empty DB, unicode, long specs, agent with no activity
9. **API contract tests** — verify response shapes match observatory-service-specs.md

All tests run in < 5 seconds (SQLite in-memory, no I/O).

---

## Build & Run

```bash
# Development
./gradlew run                      # Start server on port 8006

# Test
./gradlew test                     # Run all tests

# Package
./gradlew shadowJar                # Produce ate-server-all.jar (~15 MB)
java -jar build/libs/ate-server-all.jar

# Docker
docker build -t ate-server .       # Multi-stage: Gradle build + JRE runtime
docker run -p 8006:8006 -v ./data:/app/data ate-server
```

**Startup time:** < 500ms (Javalin + HikariCP + schema check).

---

## Migration Plan

1. **Phase 1: Skeleton + persistence layer.** `App.java`, `DatabaseManager`, `WriteCoordinator`, `SchemaInitializer`, `HealthController`. Boot the server, connect to SQLite, serve `/health`. Verify it works with the existing `economy.db` file.

2. **Phase 2: Identity + Bank.** Port `IdentityService`, `CentralBankService` with repositories. Unit tests for agent registration, credits, escrow lifecycle.

3. **Phase 3: Task Board + external API.** Port `TaskBoardService` state machine. Expose `TaskBoardController`. Integration tests for full task lifecycle.

4. **Phase 4: Reputation + Court.** Port feedback submission/revelation and dispute lifecycle. Add `JudgePanel` with LLM judge.

5. **Phase 5: Event Feed.** SSE streaming + history API. Verify the observatory's live ticker works against the Java backend.

6. **Phase 6: Observatory read-only routes.** Port metrics, agents, tasks, quarterly report endpoints. Verify the React frontend renders correctly against the Java backend.

7. **Phase 7: Auth.** `AuthInterceptor`, `SignatureVerifier`, `ResponseSigner`. Ed25519 signature verification and response signing.

8. **Phase 8: Global Feeder.** `RoundScheduler`, salary distribution, task injection, deadline enforcement.

9. **Phase 9: Compatibility suite.** Run against all documented behaviors in `observatory-service-tests.md`.

Each phase is independently testable. The React frontend doesn't change — it just points at the new port.

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|---|---|---|
| SQLite write contention under load | Low | `WriteCoordinator` serializes cleanly; monitor queue depth |
| Virtual thread pinning on synchronized blocks | Medium | sqlite-jdbc works with virtual threads; HikariCP 6+ is VT-aware |
| Ed25519 key format incompatibility | Low | Use standard PKCS#8; test against Python's `cryptography` library |
| Frontend CORS during development | Certain | Javalin CORS plugin with `localhost:5173` in dev mode |
| LLM judge latency blocking write lane | Medium | Judge calls run on separate bounded executor, not on write lane |
| Schema migration between Python and Java | None | Same schema file, same database. Zero migration. |

---

## Performance and Simplicity Tradeoffs

- **Biggest win:** Remove inter-service HTTP in hot paths.
- **Main bottleneck:** SQLite single-writer semantics, handled explicitly by `WriteCoordinator`.
- **Virtual threads:** High concurrency for reads and slow external calls (LLM) without callback-heavy code.
- **Complexity cap:** One mutation path (`WriteCoordinator`), no pervasive async pipelines, no distributed state.

---

## Summary of Architectural Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Java 21 with virtual threads | Thread-per-request simplicity without OS thread overhead |
| 2 | Javalin over Spring Boot | Explicit > implicit. 50 lines of routing vs 50 annotations. Boots in 500ms. Spring is a reasonable alternative if team prefers it. |
| 3 | WriteCoordinator (single-thread executor) | Named abstraction for SQLite's single-writer constraint. Observable queue, graceful shutdown. |
| 4 | Separate read pool (N conns, `?mode=ro`) | WAL concurrent reads. No coordination with write lane needed. |
| 5 | Service/Repository separation | Business logic in services, SQL in repositories. Clean boundary for testing and evolution. |
| 6 | Domain model records | `Task.java`, `Bid.java`, `Account.java`, etc. Type-safe, immutable, self-documenting. |
| 7 | Transaction boundaries in the caller | Services receive a `Connection`. The caller decides atomicity. Composable without nested transaction issues. |
| 8 | Auth at HTTP boundary only | Internal calls are trusted. One verification layer, not six. |
| 9 | `TimeProvider` / `VirtualClock` | Testable time. Feeder tests are deterministic. Deadline tests don't depend on wall clock. |
| 10 | `IdGenerator` + `IdFormats` | Centralized ID generation with prefix validation. No scattered UUID logic. |
| 11 | Bounded judge executor (default 1) | LLM calls are slow. Separate from write lane. Configurable for parallel panel later. |
| 12 | No ORM, no caching | SQL is the interface. SQLite is local (~1ms). Simplicity over abstraction. |
| 13 | Response signing with 4 headers | Key rotation, algorithm agility, replay protection built in. |
