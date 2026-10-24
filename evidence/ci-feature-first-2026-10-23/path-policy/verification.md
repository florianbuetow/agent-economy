# Repository PATH policy verification

## Scope

- `config/semgrep/no-path-overrides.yml` defines four `ERROR` rules for Python
  environment writes, subprocess literal environments, pytest monkeypatching,
  and shell PATH assignments.
- `just code-semgrep-path-policy` runs the rule fixtures and scans first-party
  agents, libraries, services, tools, root tests, scripts, and Git hooks.
- Both `just ci` and `just ci-quiet` invoke the gate before their component CI.

## Evidence

1. Baseline: the pre-existing Semgrep rules scanned the new PATH fixtures with
   exit code 0 and `0 findings`; the unsafe patterns were not previously
   enforced.
2. Rule tests: `semgrep --test` passed `3/3` Python rules and `1/1` Bash rules.
3. Enforcement proof: a temporary untracked
   `tests/runtime/path_policy_probe.py` containing
   `os.environ["PATH"] = "/tmp/path-policy-probe"` made the real root recipe
   exit 1 with one blocking
   `agent-economy.no-python-path-environment-overrides` finding. The probe was
   then deleted.
4. Clean gate: `TMPDIR=/private/tmp just code-semgrep-path-policy` exited 0,
   ran four rules on 1,387 first-party Python/Bash targets, and reported zero
   findings.
5. CI wiring: the root `ci` and `ci-quiet` recipes both call
   `just code-semgrep-path-policy` in Phase 0b.

## Static-analysis boundary

The rules detect the prohibited direct syntax. Static analysis cannot infer a
PATH replacement hidden behind dynamically constructed mappings, reflection,
or helper calls whose bodies are unavailable; runtime process-isolation tests
remain complementary evidence.
