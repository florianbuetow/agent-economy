# Decision: Token/key hardening scope (Q-10)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-10

## Question

No JWS replay protection exists (no `iat`/`exp`/nonce — blunted by idempotency and state checks)
and no key revocation. Accept both as documented v1 risks, or add token expiry (`iat`+`exp`, cheap
with WP-02) now?

## Decision

**Add `iat`/`exp` to JWS tokens in WP-02**, while the signing library is already open for changes.
**Defer key revocation.**

## Rationale

Token expiry is cheap to add while WP-02 is already touching the signing library; key revocation
is a larger mechanism (roster management, revocation lists/checks) not justified for this pass.
Replay risk without `iat`/`exp` is already blunted by existing idempotency and state checks, which
is why revocation alone can be deferred safely.

## Consequences

- No work package in §5 is explicitly named as blocked by this question in §9; it shapes WP-02's
  JWS token implementation directly (adds `iat`/`exp` fields and their validation).
- Follow-up: key revocation remains an explicitly documented v1 risk, to be revisited in a future
  work package if the threat model changes.
