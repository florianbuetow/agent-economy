# ADR: UI Keeps Its Direct Read-Only SQLite Connection

**Date:** 2026-07-13
**Status:** Accepted (ratified 2026-07-10)
**Full decision record:** `docs/plans/2026-07-10-q4-ui-read-path-decision.md` (Q-4)
**Source:** `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §2.2, §2.8, §9 Q-4

## Context

Per §2.2, gateway-owned persistence means domain services never open `economy.db` directly — all
reads and writes go through db-gateway (R3 blesses the gateway's read API). The UI is the one
existing exception: today every UI read is a direct read-only SQLite connection (`aiosqlite`,
`mode=ro`), bypassing db-gateway entirely. Two paths were open: bless this as a permanent,
documented exception (schema-coupled, would break in Docker without a shared volume, needs an
explicit semgrep exception), or migrate UI reads to the gateway read API (one consistent data
path, Docker-clean, but requires a new gateway `GET /events` route and rewriting roughly six query
modules).

## Decision

**Keep the direct read-only SQLite connection for v1**, formally documented as the single
sanctioned exception to gateway-owned persistence (the "observatory pattern"). The connection
lives in `services/ui/src/ui_service/core/lifespan.py` (`aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True)`)
and is enforced two ways: `config/semgrep/no-direct-sql.yml` carves out `src/ui_service/` from the
no-direct-sql rule with an inline comment citing this decision record, and
`services/ui/tests/architecture/test_readonly_connection.py` asserts the connection URI actually
carries `mode=ro`.

## Rationale

The direct read-only path is the cheaper, already-working option, and its only real liability —
breaking without a shared volume in a containerized deployment — is moot: Q-6 descopes Docker for
v1 entirely. Migrating six query modules and adding a new gateway route buys nothing under a
local-only deployment model.

## Consequences

- The UI is the **only** component permitted to open `economy.db` directly; every other read
  (including Court's task-context reads) goes through the gateway's blessed read API.
- This decision is effectively final for the v1 timeframe and is revisited only if Docker
  deployment returns to scope (which would also require revisiting Q-6).
- Blocks/shapes: WP-08.6 (UI), WP-10 (deployment).
- The eight reconstructed arc42 ADRs under `docs/arc42/` remain undated and are out of scope for
  this documentation sweep; arc42 regeneration is deferred to a later pass.
