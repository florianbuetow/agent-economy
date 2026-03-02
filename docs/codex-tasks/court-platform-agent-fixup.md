# Court PlatformAgent Refactor — Codex Fixup Plan

## Context

The Court service has been refactored to use `PlatformAgent` from the `base_agent` SDK instead of custom clients (CentralBankClient, TaskBoardClient, ReputationClient) and a custom PlatformSigner. All production code changes are complete. Test infrastructure (helpers.py, conftest.py) has been updated. 154/155 unit tests pass.

**The only remaining CI failure is 1 pyright issue.** Everything else passes.

## Current State

Run `cd services/court && just ci-quiet` to see: all checks pass EXCEPT `code-lspchecks` (pyright).

The pyright error is:
```
src/court_service/routers/validation.py:79:12 - error: Unnecessary isinstance call;
  "dict[str, object]" is always an instance of "dict[Unknown, Unknown]" (reportUnnecessaryIsInstance)
```

## Task: Fix the pyright error

### File: `services/court/src/court_service/routers/validation.py`

At line 79, there is a redundant isinstance check:
```python
if not isinstance(payload, dict):
    raise ServiceError("INVALID_PAYLOAD", "JWS payload must be a JSON object", 400, {})
```

Pyright knows that `validate_certificate` returns `dict[str, object]`, so this check is always True.

**Fix:** Add a pyright suppression comment to this line:
```python
if not isinstance(payload, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
```

This is a defensive runtime check — the isinstance is still valuable if the mock or real implementation ever returns a non-dict, so suppressing rather than removing is correct.

## Verification

After the fix, run from `services/court/`:
```bash
just ci-quiet
```

ALL checks must pass with zero failures. This includes:
- code-format
- code-style
- code-typecheck (mypy)
- code-lspchecks (pyright)  <-- this is the one that currently fails
- code-security
- code-deptry
- code-spell
- code-semgrep
- code-audit
- unit tests (155/155 must pass)

## Rules

- Use `uv run` for all Python execution, never raw `python` or `pip install`
- Do NOT modify any files other than `services/court/src/court_service/routers/validation.py`
- The fix is a single-line comment addition
- After the fix, run `just ci-quiet` from `services/court/` and report the result
