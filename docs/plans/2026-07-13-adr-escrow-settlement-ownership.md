# ADR: Task Board Owns Escrow Settlement on Ruling

**Date:** 2026-07-13
**Status:** Accepted (ratified R4 — `openspec/specs/delivery-governance/spec.md § Escrow split ownership`, T-023)
**Source:** `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §2.1 (outbound-call matrix), §2.4, §2.6

## Context

When a dispute is ruled, the judge panel's `worker_pct` (0–100) must translate into an actual
split of the escrowed funds between worker and poster. Two services are involved in a ruling:
Court (owns the dispute record and the judge panel) and Central Bank (owns the ledger and the
escrow primitive itself). Someone has to bridge the two: either Court calls Central Bank directly
once it has computed `worker_pct`, or the settlement is delegated to whichever service already
owns the task's escrow across its whole lifecycle.

Task Board already performs every other escrow settlement in the system: it releases escrow to
the worker on approve/auto-approve, and refunds the poster on cancel/expiry. Court, by contrast,
is scoped by the court trust-model decision (`docs/plans/2026-06-13-court-trust-model-decision.md`,
R9) to be a pure, platform-signed adjudicator — it never calls agents directly and its only
write surface is dispute/claim/ruling records.

## Decision

**Task Board owns escrow settlement on ruling. Court never calls Central Bank.** The target
outbound-call matrix (§2.1) marks Court → Central Bank as `✗ (R4 — never; specs to be fixed)`.

The retry-clean ruling sequence is:

1. Judge panel evaluates the claim/rebuttal → median `worker_pct`.
2. Court persists the ruling (judge votes as a JSON array).
3. Court calls Task Board's platform-signed `record_ruling`
   (`task_board_service/services/task_ruling.py`, since the WP-11 decomposition of the former
   `task_manager.py` god class), which **settles escrow** via Central Bank's split endpoint:
   `worker_amount = total_amount * worker_pct // 100` to the worker, remainder to the poster —
   the floor division itself executes in Central Bank
   (`central_bank_service/services/ledger_db_client.py:314`, mirrored in
   `in_memory_ledger_store.py:340`), not in Task Board; Task Board's role is deciding *that* and
   *when* the split happens and supplying the ruled `worker_pct`. `worker_pct` 0 → full
   release-to-poster, 100 → full release-to-worker.
4. Court records spec/delivery feedback on Reputation (×2 — poster and worker).
5. Partial-failure contract: a dispute is never reported fully ruled unless all side effects
   committed; it must remain recoverable for retry.

## Rejected alternative

**Court calling Central Bank directly to execute the split.** Rejected because:

- It would duplicate escrow-settlement logic that Task Board already owns for every other
  settlement path (approve, auto-approve, cancel, expiry), creating two code paths for the same
  `worker_pct`-split invariant.
- It breaks Court's design purpose as a pure adjudicator (R9): Court's only fiscal authority
  would become inconsistent with its "writes platform-signed only, no direct agent calls, no
  ledger ownership" scope.
- Task Board is the task lifecycle's single source of truth for its own escrow; splitting that
  ownership across two services removes the one place that can reason about a task's full
  money lifecycle.

## Consequences

- Court's implementation must never hold a Central Bank client; this is enforced structurally by
  the outbound-call matrix and verified in code review (`services/court` audit: "never calls
  Central Bank (matches R4)").
- Task Board's `record_ruling` is the only code path that executes a ruling-driven escrow split.
- **Retry safety (T-040, closed `6aba0e2`):** the ruling sequence is retry-clean end-to-end —
  Court persists votes+ruling first (recoverable state), Task Board's `record_ruling` is
  idempotent (an identical `ruling_id` re-recorded on an already-ruled task returns 200 rather
  than an error), and a `409 feedback_exists` from Reputation on retry is treated as success. A
  partial failure therefore leaves the dispute recoverable for retry without re-executing any
  step that already committed. This ADR fixes *ownership*; the retry-safety mechanics live in the
  WP-06 court spec's ruling-execution contract.
- The eight reconstructed arc42 ADRs under `docs/arc42/` remain undated and are out of scope for
  this documentation sweep; arc42 regeneration is deferred to a later pass.
