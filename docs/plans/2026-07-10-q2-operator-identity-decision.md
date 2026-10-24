# Decision: Dedicated UI-operator identity (Q-2)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-2

## Question

The UI's UserAgent **is** the platform (treasury) identity today. Should the operator be a
distinct economic agent (own keypair/account, e.g. roster handle `operator`), leaving the
platform key for notary ops only?

## Decision

**Yes.** Introduce a distinct `operator` economic agent identity, separate from the platform
(treasury/notary) key.

## Rationale

Separates treasury power from browser actions and makes operator activity attributable in the
economy.

## Consequences

- Blocks WP-08.4.
- The platform key is reserved for notary operations (contract co-signing, platform-signed
  service calls); UI-driven browser actions are attributed to the new `operator` roster handle.
