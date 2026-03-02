# Identity Service: SQLite → DB Gateway Migration

## Goal

Remove the identity service's dependency on a local SQLite database. All reads and writes go through the DB Gateway HTTP API instead.

## Current State

The identity service has 4 layers:

1. **AgentStore** — sync SQLite class with 4 operations: `insert`, `get_by_id`, `list_all`, `count`
2. **AgentRegistry** — business logic (validation, crypto). Calls AgentStore methods directly (sync)
3. **GatewayClient** — async HTTP client that fires-and-forgets writes to db-gateway after the local write succeeds
4. **Router** — thin async HTTP handlers that call registry, then gateway client

Data flows like this today:

```
Router → AgentRegistry → AgentStore (SQLite) → local agents.db
                          ↘ GatewayClient (fire-and-forget) → db-gateway → economy.db
```

The local SQLite is the source of truth. The db-gateway only gets a best-effort copy.

## Target State

```
Router → AgentRegistry → GatewayAgentStore (httpx) → db-gateway → economy.db
```

The db-gateway becomes the single source of truth. No local SQLite. No dual writes.

## What Changes

### DB Gateway (new read endpoints)

The db-gateway currently only has write endpoints. We need 3 GET endpoints:

- `GET /identity/agents/{agent_id}` — full agent record including public_key
- `GET /identity/agents` — list all agents, with optional `?public_key=...` filter
- `GET /identity/agents/count` — agent count

Plus a `DbReader` class alongside the existing `DbWriter` to handle reads.

### Identity Service

1. **New: `IdentityStorageInterface`** (Protocol) — defines the async contract:
   - `insert(name, public_key) -> dict`
   - `get_by_id(agent_id) -> dict | None`
   - `list_all() -> list[dict]`
   - `count() -> int`

2. **New: `GatewayAgentStore`** — implements `IdentityStorageInterface` using `httpx.AsyncClient`:
   - `insert` → POST /identity/agents (with event metadata)
   - `get_by_id` → GET /identity/agents/{agent_id}
   - `list_all` → GET /identity/agents
   - `count` → GET /identity/agents/count
   - Maps HTTP errors to domain exceptions (409 → DuplicateAgentError, etc.)

3. **Modified: `AgentRegistry`** — methods become async (since the store is now async)

4. **Modified: Router** — `await` registry calls (already async handlers, just need `await`)

5. **Removed: `GatewayClient`** — no longer needed (GatewayAgentStore replaces it)

6. **Removed: dual-write logic** in router — no more fire-and-forget post-write sync

7. **Kept but renamed: `AgentStore` → `SqliteAgentStore`** — kept as reference/fallback, not wired in

### Config Changes

- Remove `database.path` from identity service config (no local SQLite)
- `db_gateway.url` becomes required (not optional)

## Error Mapping

| DB Gateway HTTP Status | Identity Service Behavior |
|----------------------|--------------------------|
| 200/201 | Success, return data |
| 404 | Return `None` (agent not found) |
| 409 | Raise `DuplicateAgentError` |
| 5xx | Raise `ServiceError` (gateway unavailable) |

## Testing Strategy

- Unit tests: mock httpx responses, verify correct endpoints called
- Integration tests: run db-gateway in-process via ASGI transport, identity service calls it

## Out of Scope

- Other services' gateway migration (bank, task-board, reputation, court)
- Schema divergence fixes (separate issue zpby)
- Constraint validation in db-gateway writes (separate issue 2g1b)
