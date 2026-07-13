# ADR: Sanctioned Environment-Variable Seams

**Date:** 2026-07-13
**Status:** Accepted (ratified 2026-07-10; formalized here as a standard ADR)
**Full decision record:** `docs/plans/2026-07-10-q7-env-var-policy-decision.md` (Q-7)
**Source:** `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §9 Q-7

## Context

Project policy bans environment variables for configuration ("never assume any default values
anywhere"; "all config must come from `config.yaml` or environment variables" is itself a
narrower carve-out than blanket env-var use) in favor of explicit, type-safe `config.yaml`
settings. That rule conflicts with three seams already in production use:

1. `CONFIG_PATH` — selects which config file a service loads at startup.
2. Court's `judges.api_key_env` — names the environment variable holding an LLM provider's API key.
3. Agents' `${VAR}` interpolation for LLM secrets in profile configs.

Secrets structurally cannot live in a committed YAML file, and `uvicorn --factory` (the app
factory pattern all services use) has no CLI-argument path for selecting a config file before the
app object is constructed.

## Decision

**Sanction exactly these three env-var seams as documented exceptions to the no-env-var rule; no
other seam is permitted without going through the same ratification process:**

1. `CONFIG_PATH` — kept because `uvicorn --factory` cannot take CLI arguments, and Docker (the
   other historical justification for `CONFIG_PATH`) is descoped for v1 by Q-6 anyway.
2. Court `judges.api_key_env` — names, not stores, the secret; the actual key value stays in the
   environment, never in `config.yaml`.
3. Agents' `${VAR}` resolution for LLM provider secrets (LM Studio/OpenAI/Mistral profiles) — same
   reasoning: secrets cannot be committed.

The alternative of replacing `CONFIG_PATH` with an explicit `--config` CLI argument everywhere
was considered and **explicitly not adopted**: the churn of rewiring every service's startup
command outweighs the marginal strictness gain for v1.

## Consequences

- All other configuration remains explicit and `config.yaml`-sourced, per the project's fail-fast,
  no-hardcoded-defaults rules.
- Any newly proposed env-var seam requires a ratified decision record before it can be added —
  this ADR is the reference point for that bar.
- This closes the WP-10.2 blocker; the ADR is cross-referenced from WP-12.3's documentation sweep.
- The eight reconstructed arc42 ADRs under `docs/arc42/` remain undated and are out of scope for
  this documentation sweep; arc42 regeneration is deferred to a later pass.
