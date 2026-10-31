# ADR: DB-over-HTTP Gateway

**Date:** 2026-07-13
**Status:** Accepted (§2.2; extends the reconstructed arc42 ADR-04, see note below)
**Source:** `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §2.2, §2.11;
GAP-C5 (§4.C)

> The 8 reconstructed arc42 ADRs in `docs/arc42/09-architecture-decisions.md` (including ADR-04,
> "Shared SQLite Database with Write Serialization via DB Gateway") are **not** touched or
> re-dated by this batch of ADRs — arc42 regeneration is out of scope here. This ADR and its
> siblings supersede/extend ADR-04's content going forward; formal reconciliation is deferred to
> a future arc42 regeneration pass (per Appendix A: "`docs/arc42/*` — KEEP as as-built reference;
> regenerate after WP-06/08; date the 8 ADRs"). The eight reconstructed arc42 ADRs remain undated
> and out of scope for this documentation sweep.

## Context

Five domain services (identity, central-bank, task-board, reputation, court) plus the UI all need
access to one shared SQLite database (`data/economy.db`). SQLite's file-level write lock does not
allow safe concurrent writes from multiple OS processes, and several operations (escrow locking,
task-status transitions, escrow splits, sealed feedback reveal) require cross-domain ordering
guarantees that only a single writer can provide. A shared schema and a shared, append-only
`events` log are also needed across all five domains.

## Decision

**Domain services talk to db-gateway (port 8007) over HTTP/JSON — for writes always, and for
reads via the gateway's blessed read API (R3) — rather than opening the SQLite file directly.**
The single sanctioned exception is the UI's direct read-only SQLite connection
(`docs/plans/2026-07-13-adr-ui-read-path.md`, Q-4).

The gateway holds **one shared SQLite connection** and is the sole owner of `economy.db`
(WAL journal mode, 5,000 ms busy timeout). This requires a **single-writer, no-await-in-transaction
invariant**: `DbWriter` methods (and, since WP-11's decomposition, the per-domain writer classes
`IdentityWriter`/`BankWriter`/`BoardWriter`/`ReputationWriter`/`CourtWriter`) must stay
synchronous-internally, with no `await` inside a transaction body, so that no other coroutine can
interleave a second write onto the same connection mid-transaction. This invariant was previously
implicit and unguarded — GAP-C5 found the gateway was measurably not thread-safe as a result
(2026-07-10) — and is now enforced by:

- explicit `DbWriter.connection` wiring: `services/db-gateway/src/db_gateway_service/core/lifespan.py`
  wires `state.db_reader = DbReader(db=state.db_writer.connection)` through a public accessor,
  rather than `DbReader` reaching into `DbWriter`'s private `_db` attribute;
- a static AST/coroutine guard test,
  `services/db-gateway/tests/architecture/test_gap_c5_single_writer_invariant.py`, that fails on
  any `async def` on a `DbWriter` method or any `await` inside `db_writer.py`;
- its WP-11 extension, `services/db-gateway/tests/architecture/test_db_writer_domains_no_await_invariant.py`,
  which re-applies the identical scan to the five per-domain writer modules and
  `db_writer_helpers.py` once the domain SQL moved out of the single `db_writer.py` facade.

The gateway stays **unauthenticated by design** ("trusts internal callers"); v1 posture is to
never bind or expose 8007 beyond localhost/the compose network — a deployment constraint (§2.11),
not a gap.

## Tradeoff

- **Cost:** an HTTP hop and JSON (de)serialization on every DB access from every service; the
  gateway becomes a single point of failure for all writes; services must resolve and depend on
  the gateway's availability at startup (health-gated startup tier).
- **Benefit:** SQLite's single-writer constraint is honored *explicitly and safely* rather than
  accidentally (or violated outright by concurrent processes); one canonical schema
  (`docs/specifications/schema.sql`) and one place semantic events are guaranteed correct
  (`2026-07-13-adr-events-via-gateway.md`); and every write gets the same transactional
  correctness properties without each of five services re-implementing SQLite concurrency
  handling.

## Consequences

- Any new domain-service code path that touches persistence must go through a gateway route, not
  a local SQLite connection — a direct-DB-access finding outside the UI's sanctioned exception is
  a defect, not a style choice.
- The static guard tests on `DbWriter` and the domain writer modules are a hard CI gate:
  introducing `async def` on a writer method or an `await` inside a writer module fails CI, by
  design — "the gateway is not thread-safe by design, and that's enforced, not accidental" is a
  tested invariant, not an implicit hazard.
- Docker deployment (if ever revived, Q-6 currently descopes it) must not publish 8007 to the
  host — this constraint is unchanged by this ADR.
