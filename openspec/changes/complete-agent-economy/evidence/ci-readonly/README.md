# T-173/T-174: CI validates without rewriting source

Root attempt 12 first exposed this issue in actual CI: its auto-format stage rewrote three recovery
fixture files and returned success. Exact before/after hashes and the reconstruction/AST comparison
are retained in `../mp06-recovery-foundation/root-ci-12-format-proof.json`.

The new `tests/runtime/test_ci_readonly.py` executes copied service CI recipes inside disposable
directories. Real Ruff from each service environment checks the temporary source; unrelated gates
are stubbed. This is a test of recipe behavior, not a substitute for the full pipeline.

Before the CI correction, all 14 malformed-input cases failed as intended: both `ci` and `ci-quiet`
auto-corrected input and returned 0. The 21 controls passed (explicit formatting and valid input).
After removing auto-formatting from both CI modes in all seven service justfiles, all 35 cases passed.
The explicit `code-format` command remains available, and `code-style` already performs read-only
lint and formatting checks.

The parent independently reviewed the recipe delta and harness, then reran all 35 cases successfully
in 26.46 seconds. Ruff and project-structure checks passed; all seven service justfiles remain identical.
No assertion was removed or weakened. No source/data outside the disposable harness is formatted
by these tests.

Command from the repository root:

```sh
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 \
PYTHONPATH=/Users/flo/Developer/github/agent-economy:/Users/flo/Developer/github/agent-economy/tests \
uv run --directory services/db-gateway pytest \
  /Users/flo/Developer/github/agent-economy/tests/runtime/test_ci_readonly.py -q
```

Task 2.9 is red-proven. Task 2.10 and both canonical tickets remain open until the next integrated
root gate passes with unchanged source hashes. The focused harness does not claim that unrelated
stubbed quality gates ran.
