# ADR: Events Written at the Gateway (Option B — Addendum)

**Date:** 2026-07-13
**Status:** Accepted — addendum to `docs/plans/events-architecture.md`
**Source:** `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §2.2, Appendix A
("`docs/plans/events-architecture.md` | ADDENDUM: Option B (gateway-written events) is the
decision | WP-12.3")

## Context

`docs/plans/events-architecture.md` already documents the `events` table's schema, its event
types by service, and its emission points in detail — that document remains the source of truth
for those. It was written against an "Option A vs. Option B" framing (each service opens
`economy.db` directly and INSERTs its own events, vs. a brand-new central HTTP event-ingest
service) and recommended Option A, direct per-service SQLite writes, on the grounds that the
economy runs single-host and services "already share the same DB path configuration."

That framing predates the target architecture ratified in §2.2: **domain services never open the
DB file directly** — db-gateway is the sole owner of `economy.db`, and all writes are already
serialized through its POST/PUT/DELETE routes. Under that architecture, `events-architecture.md`'s
literal "Option A" (each service opens SQLite itself) is no longer available, and its "Option B"
(a new, separate ingest service) is unnecessary scope — the gateway already is the system's single
writer process.

## Decision

**Every db-gateway write emits its semantic event row in the same transaction as the write, at
the gateway layer** — not left to each domain service to emit independently, and not routed
through a separate event-ingest service. This is the plan's "Option B (gateway-written events)."
Concretely: `task.accepted`, `task.submitted`, `task.approved`, `task.auto_approved`,
`task.disputed`, `task.ruled`, `task.cancelled`, `task.expired`, `claim.filed`,
`rebuttal.submitted`, `ruling.delivered`, `feedback.revealed`, etc. are emitted by the gateway
route handler that performs the corresponding state mutation, inside the same `BEGIN`/`COMMIT`.

For the full event-type table and per-service emission-point inventory, see
`docs/plans/events-architecture.md` — this addendum does not restate it.

## Consequences

- Domain services carry no event-emission logic or `EventWriter` client; they get a correct,
  same-transaction event row "for free" from every gateway write, with no risk of a service
  committing a state change but forgetting (or failing) to log its event.
- `events-architecture.md`'s "Implementation Sketch" (a shared `EventWriter` in
  `libs/service-commons`, called by each service) is superseded — the sketch assumed
  direct-SQLite-writing services, which the target architecture no longer has.
- Consistent with the DB-over-HTTP gateway ADR (`2026-07-13-adr-db-gateway-tradeoff.md`): the
  gateway's single shared connection is also the natural place to guarantee event/write
  atomicity, since it is the only process ever holding a write transaction.
- The eight reconstructed arc42 ADRs under `docs/arc42/` remain undated and are out of scope for
  this documentation sweep; arc42 regeneration is deferred to a later pass.
