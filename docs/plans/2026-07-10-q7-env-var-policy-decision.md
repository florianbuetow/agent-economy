# Decision: Environment-variable policy (Q-7)

**Date:** 2026-07-10
**Status:** RATIFIED
**Ratified by:** plan §5.-1 Step E1 — the §9 recommendation adopted as-is (no owner override)
**Source question:** docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §9 Q-7

## Question

The project's no-env-var rule conflicts with three production seams: `CONFIG_PATH` (config-file
selection, used by tests and compose), court `api_key_env`, and agents `${VAR}` LLM keys (secrets
cannot live in YAML). Sanction exactly these as documented exceptions (ADR), or replace
`CONFIG_PATH` with an explicit `--config` CLI argument everywhere?

## Decision

**Sanction exactly three env seams as documented exceptions to the no-env-var rule:**

1. `CONFIG_PATH` (config-file selection) — kept because the uvicorn `--factory` startup pattern
   cannot take CLI arguments, and Docker is descoped by Q-6 anyway.
2. Court `judges.api_key_env`.
3. Agents `${VAR}` resolution for LLM secrets — secrets must not live in YAML.

The `--config` CLI-argument replacement for `CONFIG_PATH` is explicitly **not** adopted: churn
outweighs strictness for v1.

## Rationale

Three seams are unavoidable: `CONFIG_PATH` because `uvicorn --factory` has no CLI-argument path
for config selection and Docker (its other justification) is descoped; the two secret seams
because secrets cannot be committed to YAML config files. All other configuration remains
explicit, from `config.yaml`, per the project's no-env-var rule.

## Consequences

- Blocks WP-10.2.
- An ADR documenting these three sanctioned exceptions lands in WP-10.2/WP-12.3.
- No other environment-variable seam is permitted; any new one must go through the same
  ratification process.
