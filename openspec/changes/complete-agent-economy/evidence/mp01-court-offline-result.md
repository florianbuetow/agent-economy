# MP-01 mock Court offline startup repair

## Diagnosis

- Root CI03 retained Court log:
  `/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-8b00f1507ad843d0ae12d3e733fd29af-1fcj3anv/logs/court.log`.
- LiteLLM attempted to fetch
  `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`
  during import, retried after DNS failure, and only then fell back to its bundled map.
- Installed primary source
  `services/court/.venv/lib/python3.13/site-packages/litellm/litellm_core_utils/get_model_cost_map.py`
  documents `LITELLM_LOCAL_MODEL_COST_MAP=True` at its module header and checks it in
  `get_model_cost_map` before remote fetch.

## Red proof

- `/tmp/agent-economy-mp01-court-offline-red.log`, exit `1`.
- The subprocess loaded a temporary `sitecustomize` HTTP guard that recorded and rejected
  every external `httpx.get`, so no real external request occurred.
- Generated mock Court import attempted the LiteLLM GitHub metadata URL, creating the marker
  and failing the no-external-traffic assertion.

## Repair

- Added a generated-service environment boundary in `scripts/isolated_test_runtime.py`.
- Only the generated Court service receives `LITELLM_LOCAL_MODEL_COST_MAP=True`, and only
  after its generated judge list validates as nonempty and entirely `provider: mock`.
- General phase commands and all other services do not receive the flag.
- Manual/production Court configuration is unchanged. Real-model evaluation remains a
  separate gate and is not converted to the bundled-map mock profile.

## Green proof

- Offline guarded import: `/tmp/agent-economy-mp01-court-offline-green.log`, exit `0`,
  1 passed. The guard marker remained absent, proving no external HTTP attempt.
- Complete runtime regressions: `/tmp/agent-economy-mp01-court-runtime.log`, exit `0`,
  17 passed in 11.21s.
- Actual `just test-e2e`: `/tmp/agent-economy-mp01-court-e2e.log`, exit `0`,
  71 passed in 27.21s.
- Retained green Court log:
  `/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-869323400cfa452e9f3f98858da1a514-9fivmsfl/logs/court.log`.
  It starts immediately with Uvicorn/Court startup and contains no LiteLLM metadata fetch or
  `raw.githubusercontent.com` entry.
- Ruff check/format: `/tmp/agent-economy-mp01-court-ruff-check.log` and
  `/tmp/agent-economy-mp01-court-ruff-format.log`, both exit `0`.
- Targeted Pyright stats: `/tmp/agent-economy-mp01-court-pyright.log`, exit `0`, exactly
  2 files checked, 0 errors/warnings.
- Exact tmux sessions `mp01-court-runtime` and `mp01-court-e2e` exited; exact post-run checks
  reported no tmux server/session remaining.

## Scope

- Modified only `scripts/isolated_test_runtime.py` and
  `tests/runtime/test_isolated_test_runtime.py`.
- No readiness timeout increase, skipped tests, model assertion changes, production provider
  changes, dependencies, Git mutations, or root CI run.
