# Phase 8 — Tests

## Working Directory

```
services/reputation/
```

---

## Test Strategy

Tests cover the full specification from both `reputation-service-tests.md` (51 test cases) and `reputation-service-auth-tests.md` (39 test cases). Tests are organized into unit, integration, architecture, and performance directories.

All tests must:
- Be marked with `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.performance`
- Use `asyncio_mode = "auto"` (configured in `pyproject.toml`)
- Use `httpx.AsyncClient` with `ASGITransport` for endpoint tests
- Mock the Identity service (never call a live Identity service in unit tests)
- Use temporary databases (`:memory:` or `tmp_path`) for isolation

---

## File 1: `tests/conftest.py`

Minimal root conftest. Import shared fixtures.

---

## File 2: `tests/helpers.py`

Shared test utilities:

- `make_jws_token(signer_agent_id, payload)` — creates a fake JWS compact serialization (three base64-separated parts). Does NOT do real Ed25519 signing — tests mock the Identity service response instead.
- `make_mock_identity_client(verify_response)` — creates a mock `IdentityClient` that returns the given response dict from `verify_jws()`.

---

## File 3: `tests/unit/conftest.py`

Auto-clear settings cache and app state between tests:

```python
@pytest.fixture(autouse=True)
def _clear_caches():
    clear_settings_cache()
    reset_app_state()
    yield
    clear_settings_cache()
    reset_app_state()
```

---

## File 4: `tests/unit/test_config.py`

Test spec: verify `Settings` loads from `config.yaml` correctly.

- Settings load successfully with valid config
- All 7 config sections are accessible
- Service name is `"reputation"`, port is `8004`
- Settings are cached (same object on repeated calls)

---

## File 5: `tests/unit/test_feedback_service.py`

Test the business logic layer (`services/feedback.py`) directly, no HTTP involved.

### Validation Tests (from spec: FB-09 to FB-16, FB-25)

- `INVALID_FIELD_TYPE` — integer, boolean, list, dict values for required fields
- `MISSING_FIELD` — absent, `None`, and empty string `""` for each required field
- `SELF_FEEDBACK` — `from_agent_id == to_agent_id`
- `INVALID_CATEGORY` — value not in `{spec_quality, delivery_quality}`
- `INVALID_RATING` — value not in `{dissatisfied, satisfied, extremely_satisfied}`
- `COMMENT_TOO_LONG` — exceeds configured max length
- Validation order is correct (INVALID_FIELD_TYPE before MISSING_FIELD before SELF_FEEDBACK, etc.)

### `submit_feedback` Tests

- Valid submission returns `FeedbackRecord`
- `DuplicateFeedbackError` from store returns `ValidationError("FEEDBACK_EXISTS", ..., 409)`

### `is_visible` Tests

- `visible=True` record → always visible
- `visible=False` record, elapsed < timeout → not visible
- `visible=False` record, elapsed >= timeout → visible (lazy timeout reveal)

### Query Function Tests

- `get_feedback_by_id` returns `None` for sealed records
- `get_feedback_for_task` filters out sealed records
- `get_feedback_for_agent` filters out sealed records

---

## File 6: `tests/unit/test_persistence.py`

Test the SQLite `FeedbackStore` directly.

### Core Tests

- Insert and retrieve a single feedback record
- Duplicate insert raises `DuplicateFeedbackError`
- Same agents, different task → allowed
- Reverse pair → allowed

### Mutual Reveal Tests (from spec: VIS-01 to VIS-08)

- Single feedback is sealed (`visible=0`)
- After reverse pair insert, both records become visible (`visible=1`)
- Reveal is per-pair: alice→bob on task_1 does not reveal carol→bob on task_1
- Reveal on task_1 does not affect task_2

### Persistence Tests

- FeedbackStore with file-backed database survives close + reopen
- Count includes sealed records

### Config and State Reset

- Test with custom database paths
- Multiple init/close cycles work correctly

---

## File 7: `tests/unit/test_store_robustness.py`

Edge cases for `FeedbackStore`:

- `DuplicateFeedbackError` is a domain exception (not `sqlite3.IntegrityError`)
- ROLLBACK safety: if ROLLBACK itself fails, exception still propagates
- Thread safety: concurrent inserts from multiple threads

---

## File 8: `tests/unit/routers/conftest.py`

Router test fixtures:

- `app` fixture — creates app via `create_app()` with test config
- `client` fixture — `httpx.AsyncClient` with `ASGITransport`
- Mock identity client injection (patch `state.identity_client`)
- Helper functions for creating valid JWS tokens and feedback payloads

---

## File 9: `tests/unit/routers/test_health.py`

Test spec: HEALTH-01 to HEALTH-03.

- `GET /health` returns 200 with `status`, `uptime_seconds`, `started_at`, `total_feedback`
- `total_feedback` is exact count
- `uptime_seconds` increases over time

---

## File 10: `tests/unit/routers/test_feedback.py`

Test spec: FB-01 to FB-25, VIS-01 to VIS-09, READ-01 to READ-05, TASK-01 to TASK-02, AGENT-01 to AGENT-03.

### Submission Tests (FB-01 to FB-25)

- Valid feedback (delivery_quality, spec_quality) returns 201
- All three ratings accepted
- Comment variations: omitted, null, empty, Unicode
- Comment at max length accepted; one over rejected
- Duplicate feedback rejected (409 FEEDBACK_EXISTS)
- Same task reverse direction allowed
- Same agents different task allowed
- Self-feedback rejected (400 SELF_FEEDBACK)
- Invalid rating, invalid category
- Missing fields, null fields, wrong field types
- Malformed JSON, wrong content type
- Mass-assignment resistance (extra fields ignored)
- Oversized request body (413)
- Concurrent duplicate race safety

### Visibility Tests (VIS-01 to VIS-09)

- Sealed feedback not returned by task/agent queries
- Both directions → both revealed
- Second submission returns `visible: true`
- Sealed feedback returns 404 on direct lookup
- Revealed feedback returns 200 on direct lookup
- Timeout reveals sealed feedback (requires short timeout config)

### Lookup Tests (READ-01 to READ-05)

- Revealed feedback lookup returns full record
- Non-existent feedback returns 404
- Malformed/path-traversal IDs return 404 (no leakage)
- SQL injection returns 404 or empty arrays
- Idempotent reads

### HTTP Misuse Tests (HTTP-01)

- Wrong methods on all endpoints return 405 METHOD_NOT_ALLOWED

### Security Tests (SEC-01 to SEC-03)

- Error envelope consistency
- No internal error leakage
- Feedback IDs match `fb-<uuid4>` pattern

---

## File 11: `tests/unit/routers/test_feedback_auth.py`

Test spec: AUTH-01 to AUTH-16, PREC-01 to PREC-07, VJWS-01 to VJWS-09, PUB-01 to PUB-04, SEC-AUTH-01 to SEC-AUTH-03.

All submissions go through JWS. The mock `IdentityClient` is injected to simulate verification responses.

### JWS Token Validation (AUTH-01 to AUTH-08)

- Valid JWS → 201
- Missing `token` field → 400 INVALID_JWS
- `token` is null → 400 INVALID_JWS
- `token` is not a string (int, list, dict, bool) → 400 INVALID_JWS
- `token` is empty → 400 INVALID_JWS
- Malformed JWS (not 3 parts) → 400 INVALID_JWS
- Tampered JWS (mock returns FORBIDDEN) → 403 FORBIDDEN
- Unregistered agent (mock returns FORBIDDEN) → 403 FORBIDDEN

### Payload Validation (AUTH-09 to AUTH-11)

- Missing `action` → 400 INVALID_PAYLOAD
- Wrong `action` value → 400 INVALID_PAYLOAD
- `action` is null → 400 INVALID_PAYLOAD

### Signer Matching (AUTH-12 to AUTH-14)

- Signer matches `from_agent_id` → 201
- Signer does NOT match → 403 FORBIDDEN
- Signer impersonates non-existent agent → 403 FORBIDDEN

### Identity Service Unavailability (AUTH-15 to AUTH-16)

- Identity service down → 502 IDENTITY_SERVICE_UNAVAILABLE
- Identity service returns unexpected response → 502 IDENTITY_SERVICE_UNAVAILABLE

### Public Endpoints (PUB-01 to PUB-04)

- GET endpoints work without any token

### Error Precedence (PREC-01 to PREC-07)

- Content-Type before token validation
- Body size before token validation
- JSON parsing before token validation
- Token validation before payload validation
- Payload action before signer matching
- Signer matching before feedback field validation
- Identity unavailability before payload validation

### Existing Validations Through JWS (VJWS-01 to VJWS-09)

- All feedback validation rules work through JWS payload
- SELF_FEEDBACK, INVALID_CATEGORY, INVALID_RATING, COMMENT_TOO_LONG
- Duplicate detection, mutual reveal, extra fields ignored
- Concurrent duplicate race

### Cross-Cutting Security (SEC-AUTH-01 to SEC-AUTH-03)

- Error envelope consistency for auth errors
- No internal leakage in auth failures
- Token reuse across actions rejected

---

## File 12: `tests/unit/routers/test_identity_error_remapping.py`

Test that the `IdentityClient` correctly remaps errors:

- Verification failures (`INVALID_JWS`, `AGENT_NOT_FOUND`) → 403 FORBIDDEN
- Infrastructure failures (connection, timeout) → 502 IDENTITY_SERVICE_UNAVAILABLE

---

## File 13: `tests/integration/conftest.py`

Integration test fixtures. Minimal.

---

## File 14: `tests/integration/test_endpoints.py`

Multi-endpoint workflow tests:

- Submit sealed feedback → verify not visible → submit reverse → verify both visible
- Agent feedback visibility per-agent filtering
- Full JWS flow with mock identity client

---

## File 15: `tests/architecture/test_architecture.py`

pytestarch import rules verifying dependency structure.

---

## File 16: `tests/performance/conftest.py`

Performance test config (stub for future benchmarks).

---

## Verification

```bash
just ci-quiet
```

This runs the full CI pipeline: formatting, linting, type checking (mypy + pyright), security scanning, spell checking, custom semgrep rules, and ALL tests. The service is complete only when this passes with zero failures.
