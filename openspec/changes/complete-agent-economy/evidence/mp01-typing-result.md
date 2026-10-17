# MP-01 typing repair evidence

## Scope

- Modified only `scripts/isolated_test_runtime.py` and `tests/runtime/test_isolated_test_runtime.py`.
- Preserved process cleanup behavior, the debugger-owned zombie/reaping regression, and all existing assertions.
- No Git mutations, root CI, dependency changes, or other file edits.

## Changes

- Declared binary service log handles as `BinaryIO`.
- Added typed process and command ownership records with `TypedDict`.
- Replaced object-to-int conversions with typed record fields.
- Validated YAML top-level and nested mapping boundaries before mutation, preserving generated config behavior.
- Added typed JSON manifest validation in runtime tests before indexing records.
- Used an external targeted Pyright configuration with the repository root and DB Gateway environment explicitly resolved.

## Type-check evidence

- Existing red proof: `/tmp/mp01-process-race-pyright.log` — 26 errors.
- Environment setup attempt: `/tmp/agent-economy-mp01-pyright-attempt1.log` — absolute include paths were ignored because Pyright resolves includes relative to the external config; the resulting broad `/tmp` scan is not annotation evidence.
- Correctly rooted intermediate proof: `/tmp/agent-economy-mp01-pyright-attempt2.log`, `.exit` = `1` — one real optional-string assertion error remained.
- Final config: `/tmp/agent-economy-mp01-pyrightconfig.json`.
- Final Pyright: `/tmp/agent-economy-mp01-pyright-final.log`, `.exit` = `0` — 0 errors, 0 warnings.
- Final Pyright stats: `/tmp/agent-economy-mp01-pyright-stats.log`, `.exit` = `0` — `Found 2 source files`, `Total files checked: 2`, 0 errors.

## Static and behavior evidence

- Ruff check: `/tmp/agent-economy-mp01-typing-ruff-check.log`, `.exit` = `0`.
- Ruff format check: `/tmp/agent-economy-mp01-typing-ruff-format.log`, `.exit` = `0`.
- Runtime regression suite: `/tmp/agent-economy-mp01-typing-runtime.log`, `.exit` = `0` — 16 passed in 9.63s.
- Dedicated host session: `mp01-typing-runtime`; exact post-run session check reported no tmux server/session remaining.
- `git diff --check -- scripts/isolated_test_runtime.py tests/runtime/test_isolated_test_runtime.py` exited 0.
