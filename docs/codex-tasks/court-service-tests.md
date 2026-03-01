# Court Service — Test Implementation Plan

This plan is for implementing the Court service test suite BEFORE any service implementation code is written. The tests must be syntactically valid, CI-compliant, and expected to fail (since the service is not yet implemented).

---

## Files to Read First

1. `AGENTS.md` — project conventions, code style, testing rules
2. `docs/specifications/service-tests/court-service-tests.md` — 75 test cases across 10 categories
3. `docs/specifications/service-tests/court-service-auth-tests.md` — 33 test cases across 6 categories
4. `docs/specifications/service-api/court-service-specs.md` — API specification
5. `docs/specifications/service-api/court-service-auth-specs.md` — authentication specification

Reference implementations for test patterns:
- `services/central-bank/tests/` — conftest fixtures, JWS helpers, mock patterns
- `services/reputation/tests/` — feedback test patterns

---

## Working Directory

```
services/court/
```

---

## Test Inventory

108 test cases total (75 + 33):

| Category | IDs | Count | File |
|----------|-----|-------|------|
| File Dispute | FILE-01 to FILE-17 | 17 | `test_disputes.py` |
| Submit Rebuttal | REB-01 to REB-10 | 10 | `test_disputes.py` |
| Trigger Ruling | RULE-01 to RULE-19 | 19 | `test_disputes.py` |
| Get Dispute | GET-01 to GET-05 | 5 | `test_disputes.py` |
| List Disputes | LIST-01 to LIST-06 | 6 | `test_disputes.py` |
| Health | HLTH-01 to HLTH-04 | 4 | `test_health.py` |
| HTTP Method Misuse | HTTP-01 | 1 | `test_disputes.py` |
| Cross-Cutting Security | SEC-01 to SEC-03 | 3 | `test_disputes.py` |
| Judge Panel Config | JUDGE-01 to JUDGE-05 | 5 | `test_config.py` |
| Dispute Lifecycle | LIFE-01 to LIFE-05 | 5 | `test_disputes.py` |
| Platform JWS Validation | AUTH-01 to AUTH-16 | 16 | `test_disputes.py` |
| Public Endpoints | PUB-01 to PUB-03 | 3 | `test_disputes.py` |
| Identity Dependency | IDEP-01 to IDEP-03 | 3 | `test_disputes.py` |
| Token Replay | REPLAY-01 to REPLAY-02 | 2 | `test_disputes.py` |
| Error Precedence | PREC-01 to PREC-06 | 6 | `test_disputes.py` |
| Auth Cross-Cutting | SEC-AUTH-01 to SEC-AUTH-03 | 3 | `test_disputes.py` |

---

## Files to Create

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Shared config (docstring only) |
| `tests/unit/conftest.py` | Auto-clear settings cache + app state |
| `tests/unit/test_config.py` | JUDGE-01 to JUDGE-05 + config loading tests |
| `tests/unit/routers/conftest.py` | App fixture, client, JWS helpers, all mocks |
| `tests/unit/routers/test_health.py` | HLTH-01 to HLTH-04 |
| `tests/unit/routers/test_disputes.py` | All remaining test IDs (95 tests) |
| `tests/integration/conftest.py` | Stub |
| `tests/performance/conftest.py` | Stub |

---

## Implementation Sequence

### Step 1: Fixture Infrastructure

Write the conftest files first. These must compile and pass lint even without the service implementation.

**Key challenge**: The test fixtures import from `court_service.*` modules that don't exist yet. To make tests CI-compliant before implementation:
- The `app` fixture in `tests/unit/routers/conftest.py` imports `create_app`, `lifespan`, etc.
- These imports will fail at runtime (expected — tests should fail)
- But they must pass static analysis (ruff, mypy)

**Approach**: Since the court service has only empty `__init__.py` files and no actual modules, the imports will be unresolvable. The tests should be written with the correct imports and will fail with `ImportError` when run — this is acceptable per the workflow ("tests are expected to fail").

For `ruff check` and `ruff format` to pass, the imports must be syntactically valid even if the modules don't exist. This is fine — ruff doesn't resolve imports.

For `mypy` and `pyright` — these will flag missing modules. Use `# type: ignore[import-not-found]` on imports from `court_service` that don't exist yet, OR skip type checking on test files (check if the pyright config already excludes tests).

### Step 2: Config Tests (`test_config.py`)

JUDGE-01 through JUDGE-05 test startup validation. These test the `JudgesConfig` validator directly by constructing `Settings` objects with various invalid configs and asserting `ValidationError` is raised.

Also add standard config loading tests: valid config loads, missing sections raise, extra keys raise (due to `extra="forbid"`).

### Step 3: Health Tests (`test_health.py`)

HLTH-01 through HLTH-04. Straightforward — GET /health returns 200, correct schema, counts are accurate, POST returns 405.

### Step 4: Dispute Endpoint Tests (`test_disputes.py`)

This is the bulk of the work — 95 test cases organized into classes:

```python
class TestFileDispute:          # FILE-01 to FILE-17
class TestSubmitRebuttal:       # REB-01 to REB-10
class TestTriggerRuling:        # RULE-01 to RULE-19
class TestGetDispute:           # GET-01 to GET-05
class TestListDisputes:         # LIST-01 to LIST-06
class TestHTTPMethods:          # HTTP-01
class TestCrossCuttingSecurity: # SEC-01 to SEC-03
class TestDisputeLifecycle:     # LIFE-01 to LIFE-05
class TestPlatformJWS:          # AUTH-01 to AUTH-16
class TestPublicEndpoints:      # PUB-01 to PUB-03
class TestIdentityDependency:   # IDEP-01 to IDEP-03
class TestTokenReplay:          # REPLAY-01 to REPLAY-02
class TestErrorPrecedence:      # PREC-01 to PREC-06
class TestAuthSecurity:         # SEC-AUTH-01 to SEC-AUTH-03
```

Every test method must:
- Be marked with `@pytest.mark.unit`
- Have a docstring referencing the test ID (e.g., `"""FILE-01: Happy path ..."""`)
- Follow the three-part structure from the test spec: Setup, Action, Expected

### Step 5: Stubs

`tests/integration/conftest.py` and `tests/performance/conftest.py` — docstring only.

---

## Verification

```bash
just ci-quiet
```

Expected outcomes:
- `ruff check` — passes (syntactically valid)
- `ruff format --check` — passes (correctly formatted)
- `pytest` — all tests FAIL (service not implemented) or SKIP (import errors)
- Type checking may show errors for unresolvable imports — this is acceptable

The key requirement: the test files are **syntactically valid and CI-compliant** even though they fail at runtime. If `ruff`, `codespell`, and `semgrep` pass, the tests are ready for the implementation phase.
