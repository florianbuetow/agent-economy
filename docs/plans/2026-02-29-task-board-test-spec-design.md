# Task Board Test Specification — Design

## Decision

Write a unified test specification (`task-board-service-tests.md`) covering all 15 endpoints, authentication, authorization, escrow integration, deadline enforcement, and cross-cutting concerns in a single document.

## Why Unified

The Reputation service split its tests into core + auth because auth was a later addition to an already-specified service. The Task Board has auth deeply intertwined with every mutating endpoint from day one (two-token creation, sealed bids, platform-signed ruling). Splitting would create excessive cross-referencing with no clarity benefit.

## Structure

Tests are organized by endpoint (Approach A), matching the API spec layout. Cross-cutting concerns that span multiple endpoints are grouped at the end.

17 categories, 171 tests total:

- Categories 1–12: One category per endpoint or endpoint group
- Category 13: Lifecycle / deadline tests (full flows, lazy evaluation, terminal states)
- Category 14: Health
- Category 15: HTTP method misuse
- Category 16: Error precedence (10 tests verifying documented error ordering)
- Category 17: Cross-cutting security (envelope, leakage, ID formats, replay, injection)

## Key Coverage Areas

- **Two-token task creation**: 27 tests covering token validation, cross-token matching, escrow integration, and rollback
- **Sealed bids**: 8 tests for conditional authentication on GET /bids
- **Lazy deadline evaluation**: 6 tests covering all three deadline types, concurrent safety, and terminal state immunity
- **State machine**: 4 tests verifying that each status only allows its valid operations
- **Platform-signed operations**: 12 ruling tests covering platform-only auth, worker_pct boundaries, and double-rule prevention
- **Error precedence**: 10 tests verifying the 13-level error precedence from the auth spec

## Output

`docs/specifications/service-tests/task-board-service-tests.md`
