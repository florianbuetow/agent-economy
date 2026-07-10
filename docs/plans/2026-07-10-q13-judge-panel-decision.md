# Decision: Production judge panel + vagueness rubric (Q-13)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-13

## Question

The dev default is 1 mock judge (fixed 50% — the ambiguity-favors-worker thesis is currently
*inert*); LM Studio config exists only as comments; no doc defines panel size/models for a real
run, and no operational rubric for "vague spec" exists (arc42 flags it). What is the intended
real-run panel (size, models, same/different providers) and is a written vagueness rubric wanted
in the judge prompt? *(Vision open question.)*

## Decision

- **Dev/test/CI:** mock judge (deterministic, keeps CI free of live LLM calls).
- **Production:** an odd-sized panel of 3 judges, median aggregation, providers config-driven.
- **Judge prompt:** an explicit "vague spec" rubric is written into the judge system prompt.

## Rationale

The mock judge keeps CI deterministic and LLM-free. An odd-sized panel of 3 with median
aggregation avoids ties without needing a tie-breaking rule. Without a written vagueness rubric,
the ambiguity-favors-worker thesis — the core incentive mechanism of the whole project — is
enforced by nothing but the model's disposition, which is not a reliable enforcement mechanism.

## Consequences

- Blocks WP-06.7/WP-12 judge docs.
- The judge system prompt is amended with the vagueness rubric as part of this work.
- Panel size, aggregation method, and provider configurability are all config-driven, per the
  project's no-hardcoded-config rule.
