# Reputation Service — Test Implementation Plan

This plan is for implementing the Reputation service test suite. It covers two phases:

1. **Main spec tests** (51 tests) — core feedback functionality, visibility, lookups, health, HTTP, and security
2. **Auth spec tests** (39 tests) — JWS authentication, payload validation, signer matching, Identity dependency, error precedence, and existing validations through JWS

The main spec tests are implemented as bash acceptance tests. The auth spec tests are implemented as Python pytest unit tests (since they require mocking the Identity service for JWS verification).

Tests must be syntactically valid, CI-compliant, and expected to fail if the service is not yet implemented.

---

## Files to Read First

1. `AGENTS.md` — project conventions, code style, testing rules
2. `docs/specifications/service-tests/reputation-service-tests.md` — 51 test cases across 8 categories
3. `docs/specifications/service-tests/reputation-service-auth-tests.md` — 39 test cases across 8 categories
4. `docs/specifications/service-api/reputation-service-specs.md` — API specification
5. `docs/specifications/service-api/reputation-service-auth-specs.md` — authentication specification

Reference implementations for test patterns:
- `services/identity/tests/acceptance/` — bash acceptance test patterns (helpers.sh, run_all.sh, test scripts)
- `services/central-bank/tests/` — JWS helper patterns, conftest fixtures, identity mock patterns

---

## Working Directory

```
services/reputation/
```

---

## Test Inventory

90 test cases total (51 + 39):

### Part 1: Main Spec Tests (bash acceptance — 51 tests)

| Category | IDs | Count | File(s) |
|----------|-----|-------|---------|
| Feedback Submission | FB-01 to FB-25 | 25 | `test-fb-01.sh` to `test-fb-25.sh` |
| Visibility / Sealed | VIS-01 to VIS-09 | 9 | `test-vis-01.sh` to `test-vis-09.sh` |
| Feedback Lookup | READ-01 to READ-05 | 5 | `test-read-01.sh` to `test-read-05.sh` |
| Task Feedback | TASK-01 to TASK-02 | 2 | `test-task-01.sh` to `test-task-02.sh` |
| Agent Feedback | AGENT-01 to AGENT-03 | 3 | `test-agent-01.sh` to `test-agent-03.sh` |
| Health | HEALTH-01 to HEALTH-03 | 3 | `test-health-01.sh` to `test-health-03.sh` |
| HTTP Misuse | HTTP-01 | 1 | `test-http-01.sh` |
| Cross-Cutting Security | SEC-01 to SEC-03 | 3 | `test-sec-01.sh` to `test-sec-03.sh` |

### Part 2: Auth Spec Tests (Python pytest — 39 tests)

| Category | IDs | Count | File |
|----------|-----|-------|------|
| JWS Token Validation | AUTH-01 to AUTH-08 | 8 | `test_feedback_auth.py` |
| JWS Payload Validation | AUTH-09 to AUTH-11 | 3 | `test_feedback_auth.py` |
| Authorization (Signer Matching) | AUTH-12 to AUTH-14 | 3 | `test_feedback_auth.py` |
| Identity Service Unavailability | AUTH-15 to AUTH-16 | 2 | `test_feedback_auth.py` |
| GET Endpoints Remain Public | PUB-01 to PUB-04 | 4 | `test_feedback_auth.py` |
| Error Precedence | PREC-01 to PREC-07 | 7 | `test_feedback_auth.py` |
| Existing Validations Through JWS | VJWS-01 to VJWS-09 | 9 | `test_feedback_auth.py` |
| Auth Cross-Cutting Security | SEC-AUTH-01 to SEC-AUTH-03 | 3 | `test_feedback_auth.py` |

---

## Files to Create

### Part 1: Bash Acceptance Tests

| File | Purpose |
|------|---------|
| `tests/acceptance/helpers.sh` | Shared test helpers (http_*, assert_*, convenience functions) |
| `tests/acceptance/run_all.sh` | Sequential test runner |
| `tests/acceptance/test-fb-01.sh` to `test-fb-25.sh` | Feedback submission tests |
| `tests/acceptance/test-vis-01.sh` to `test-vis-09.sh` | Visibility / sealed feedback tests |
| `tests/acceptance/test-read-01.sh` to `test-read-05.sh` | Feedback lookup tests |
| `tests/acceptance/test-task-01.sh` to `test-task-02.sh` | Task feedback query tests |
| `tests/acceptance/test-agent-01.sh` to `test-agent-03.sh` | Agent feedback query tests |
| `tests/acceptance/test-health-01.sh` to `test-health-03.sh` | Health endpoint tests |
| `tests/acceptance/test-http-01.sh` | HTTP method misuse tests |
| `tests/acceptance/test-sec-01.sh` to `test-sec-03.sh` | Cross-cutting security tests |

### Part 2: Python Unit Tests

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Shared config (docstring only) |
| `tests/unit/conftest.py` | Auto-clear settings cache + app state |
| `tests/unit/routers/conftest.py` | App fixture, client, JWS helpers, Identity mock |
| `tests/unit/routers/test_health.py` | HEALTH-01 to HEALTH-03 (pytest mirror) |
| `tests/unit/routers/test_feedback.py` | Main spec tests via pytest (optional — bash is authoritative) |
| `tests/unit/routers/test_feedback_auth.py` | AUTH-01 to AUTH-16, PUB-01 to PUB-04, PREC-01 to PREC-07, VJWS-01 to VJWS-09, SEC-AUTH-01 to SEC-AUTH-03 (39 tests) |
| `tests/integration/conftest.py` | Stub |
| `tests/performance/conftest.py` | Stub |

---

## Implementation Sequence

### Phase 1: Bash Acceptance Tests — helpers.sh

Create `tests/acceptance/helpers.sh` modeled after the Identity service's `helpers.sh` but adapted for the Reputation service.

**Configuration:**
```bash
BASE_URL="${REPUTATION_BASE_URL:-http://localhost:8004}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
```

**Constants:**
```bash
FEEDBACK_ID_PATTERN='^fb-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
UUID4_PATTERN='^a-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
TASK_UUID4_PATTERN='^t-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
ISO8601_PATTERN='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
```

**Include all standard functions from Identity's helpers.sh:**
- Colors, test state tracking, output functions (test_start, step, test_end)
- HTTP functions (http_post, http_post_raw, http_post_content_type, http_post_file, http_get, http_method)
- Assertion functions (assert_status, assert_json_eq, assert_json_exists, assert_json_not_exists, assert_json_matches, assert_json_true, assert_json_false, assert_json_array_min_length, assert_json_gt, assert_error_envelope, assert_body_not_contains, assert_equals, assert_not_equals)

**Add convenience helpers:**

```bash
# Generate a random agent ID
random_agent_id() {
    echo "a-$(uuidgen | tr '[:upper:]' '[:lower:]')"
}

# Generate a random task ID
random_task_id() {
    echo "t-$(uuidgen | tr '[:upper:]' '[:lower:]')"
}

# Submit feedback with required fields
submit_feedback() {
    local task_id="$1"
    local from_id="$2"
    local to_id="$3"
    local category="${4:-delivery_quality}"
    local rating="${5:-satisfied}"
    local comment="${6}"
    local body
    if [ -n "$comment" ]; then
        body=$(jq -nc --arg tid "$task_id" --arg fid "$from_id" --arg tid2 "$to_id" \
            --arg cat "$category" --arg rat "$rating" --arg com "$comment" \
            '{task_id:$tid, from_agent_id:$fid, to_agent_id:$tid2, category:$cat, rating:$rat, comment:$com}')
    else
        body=$(jq -nc --arg tid "$task_id" --arg fid "$from_id" --arg tid2 "$to_id" \
            --arg cat "$category" --arg rat "$rating" \
            '{task_id:$tid, from_agent_id:$fid, to_agent_id:$tid2, category:$cat, rating:$rat}')
    fi
    http_post "/feedback" "$body"
}
```

### Phase 2: Bash Acceptance Tests — run_all.sh

Create `tests/acceptance/run_all.sh` following the Identity service pattern.

Key points:
- Banner: "Reputation Service Acceptance Tests" with $BASE_URL
- Prerequisites: curl, jq
- Do NOT start the service — tests assume it's running
- Explicit ordered TESTS array (NOT auto-discovered):

```bash
TESTS=(
    "test-fb-01.sh"
    "test-fb-02.sh"
    "test-fb-03.sh"
    "test-fb-04.sh"
    "test-fb-05.sh"
    "test-fb-06.sh"
    "test-fb-07.sh"
    "test-fb-08.sh"
    "test-fb-09.sh"
    "test-fb-10.sh"
    "test-fb-11.sh"
    "test-fb-12.sh"
    "test-fb-13.sh"
    "test-fb-14.sh"
    "test-fb-15.sh"
    "test-fb-16.sh"
    "test-fb-17.sh"
    "test-fb-18.sh"
    "test-fb-19.sh"
    "test-fb-20.sh"
    "test-fb-21.sh"
    "test-fb-22.sh"
    "test-fb-23.sh"
    "test-fb-24.sh"
    "test-fb-25.sh"
    "test-vis-01.sh"
    "test-vis-02.sh"
    "test-vis-03.sh"
    "test-vis-04.sh"
    "test-vis-05.sh"
    "test-vis-06.sh"
    "test-vis-07.sh"
    "test-vis-08.sh"
    "test-vis-09.sh"
    "test-read-01.sh"
    "test-read-02.sh"
    "test-read-03.sh"
    "test-read-04.sh"
    "test-read-05.sh"
    "test-task-01.sh"
    "test-task-02.sh"
    "test-agent-01.sh"
    "test-agent-02.sh"
    "test-agent-03.sh"
    "test-health-01.sh"
    "test-health-02.sh"
    "test-health-03.sh"
    "test-http-01.sh"
    "test-sec-01.sh"
    "test-sec-02.sh"
    "test-sec-03.sh"
)
```

- Loop, print test name, run, fail-fast on failure
- Summary with total/passed/elapsed time

### Phase 3: Bash Acceptance Tests — All 51 Test Scripts

Every test script follows this skeleton:

```bash
#!/bin/bash
# test-<id>.sh — <TEST_ID>: <description>
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "<TEST_ID>" "<description from test spec>"

step "<description>"
<commands>

step "<next step>"
<assertions>

test_end
```

Read `docs/specifications/service-tests/reputation-service-tests.md` for the EXACT expected behavior of each test. Every status code, every error code, every assertion described there must be implemented. Do not skip any assertions. Do not add assertions that are not in the spec.

**FEEDBACK SUBMISSION TESTS (test-fb-01.sh to test-fb-25.sh):**

test-fb-01.sh — FB-01: Submit valid feedback (delivery quality)
  step: Generate ALICE_ID, BOB_ID, TASK_ID using random_agent_id / random_task_id
  step: submit_feedback "$TASK_ID" "$ALICE_ID" "$BOB_ID" "delivery_quality" "satisfied" "Good work"
  assert: status 201, json_exists .feedback_id .task_id .from_agent_id .to_agent_id .category .rating .comment .submitted_at .visible
  assert: json_matches .feedback_id "$FEEDBACK_ID_PATTERN"
  assert: json_matches .submitted_at "$ISO8601_PATTERN"
  assert: json_false .visible

test-fb-02.sh — FB-02: Submit valid feedback (spec quality)
  step: Generate IDs, submit with category "spec_quality", rating "extremely_satisfied"
  assert: status 201, json_eq .category "spec_quality", json_eq .rating "extremely_satisfied"

test-fb-03.sh — FB-03: Submit feedback without comment field
  step: Submit with comment omitted from JSON
  assert: status 201, json_eq .comment "null"

test-fb-04.sh — FB-04: Submit feedback with null comment
  step: Submit with "comment": null
  assert: status 201, json_eq .comment "null"

test-fb-05.sh — FB-05: Submit feedback with empty comment
  step: Submit with "comment": ""
  assert: status 201, json_eq .comment ""

test-fb-06.sh — FB-06: Duplicate feedback rejected
  step: Submit once, assert 201
  step: Submit identical again
  assert: status 409, json_eq .error "FEEDBACK_EXISTS"

test-fb-07.sh — FB-07: Same task, reverse direction allowed
  step: Submit (alice→bob), save FB1_ID
  step: Submit (bob→alice), save FB2_ID
  assert: status 201 for both, not_equals FB1_ID FB2_ID

test-fb-08.sh — FB-08: Same agents, different task allowed
  step: Submit (task1, alice→bob), assert 201
  step: Submit (task2, alice→bob), assert 201

test-fb-09.sh — FB-09: Self-feedback rejected
  step: Submit with from_agent_id == to_agent_id
  assert: status 400, json_eq .error "SELF_FEEDBACK"

test-fb-10.sh — FB-10: Comment exceeding max length rejected
  step: Generate 257-char comment, submit
  assert: status 400, json_eq .error "COMMENT_TOO_LONG"

test-fb-11.sh — FB-11: Comment at exactly max length accepted
  step: Generate 256-char comment, submit
  assert: status 201

test-fb-12.sh — FB-12: Invalid rating value
  step: Submit with rating "excellent"
  assert: status 400, json_eq .error "INVALID_RATING"

test-fb-13.sh — FB-13: Invalid category value
  step: Submit with category "timeliness"
  assert: status 400, json_eq .error "INVALID_CATEGORY"

test-fb-14.sh — FB-14: Missing required fields (one at a time)
  step: Omit each of task_id, from_agent_id, to_agent_id, category, rating separately
  assert: status 400, json_eq .error "MISSING_FIELD" for each

test-fb-15.sh — FB-15: Null required fields
  step: Submit with all required fields set to null
  assert: status 400, json_eq .error "MISSING_FIELD"

test-fb-16.sh — FB-16: Wrong field types
  step: Submit with task_id:123, from_agent_id:true, to_agent_id:[], category:42, rating:{}
  assert: status 400, json_eq .error "INVALID_FIELD_TYPE"

test-fb-17.sh — FB-17: Malformed JSON body
  step: http_post_raw "/feedback" '{"task_id":"x","from'
  assert: status 400, json_eq .error "INVALID_JSON"

test-fb-18.sh — FB-18: Wrong content type
  step: http_post_content_type "/feedback" "text/plain" '{"task_id":"x"}'
  assert: status 415, json_eq .error "UNSUPPORTED_MEDIA_TYPE"

test-fb-19.sh — FB-19: Mass-assignment resistance
  step: Submit valid feedback with extra fields: feedback_id, submitted_at, visible, is_admin
  assert: status 201
  assert: feedback_id is NOT the injected value
  assert: submitted_at is NOT "1999-01-01T00:00:00Z"

test-fb-20.sh — FB-20: Concurrent duplicate feedback race
  step: Prepare two identical requests, fire both with & and wait
  step: Sort status codes, assert "201\n409"
  step: Verify loser's error is FEEDBACK_EXISTS

test-fb-21.sh — FB-21: All three rating values accepted
  step: Submit 3 feedbacks with different task_ids, each with dissatisfied/satisfied/extremely_satisfied
  assert: all return 201 with correct rating echoed

test-fb-22.sh — FB-22: Oversized request body
  step: Create ~2MB JSON body, submit via http_post_file
  assert: status 413, json_eq .error "PAYLOAD_TOO_LARGE"

test-fb-23.sh — FB-23: Duplicate with different rating still rejected
  step: Submit (task1, alice→bob, satisfied), assert 201
  step: Submit (task1, alice→bob, extremely_satisfied, different category)
  assert: status 409, json_eq .error "FEEDBACK_EXISTS"

test-fb-24.sh — FB-24: Unicode characters in comment
  step: Submit with comment containing emoji, CJK, accented chars
  assert: status 201, comment preserved exactly

test-fb-25.sh — FB-25: Empty string agent IDs rejected
  step: Submit with from_agent_id:"", to_agent_id:"", task_id:"" separately
  assert: status 400, json_eq .error "MISSING_FIELD" for each

**VISIBILITY TESTS (test-vis-01.sh to test-vis-09.sh):**

test-vis-01.sh — VIS-01: Single-direction feedback is sealed
  step: Submit (alice→bob), then GET /feedback/task/{task_id}
  assert: status 200, feedback array is empty

test-vis-02.sh — VIS-02: Both directions reveals both
  step: Submit (alice→bob), then (bob→alice)
  step: GET /feedback/task/{task_id}
  assert: status 200, feedback array length == 2

test-vis-03.sh — VIS-03: Second submission returns visible=true
  step: Submit (alice→bob) — assert visible is false
  step: Submit (bob→alice) — assert visible is true

test-vis-04.sh — VIS-04: Sealed feedback returns 404 on direct lookup
  step: Submit (alice→bob), capture feedback_id
  step: GET /feedback/{feedback_id}
  assert: status 404, json_eq .error "FEEDBACK_NOT_FOUND"

test-vis-05.sh — VIS-05: Revealed feedback returns 200 on direct lookup
  step: Submit both directions, capture feedback_id
  step: GET /feedback/{feedback_id}
  assert: status 200

test-vis-06.sh — VIS-06: Agent feedback query excludes sealed
  step: Submit (alice→bob) only
  step: GET /feedback/agent/{bob}
  assert: status 200, feedback array empty

test-vis-07.sh — VIS-07: Agent feedback query includes revealed
  step: Submit both directions
  step: GET /feedback/agent/{bob}
  assert: status 200, feedback array length >= 1

test-vis-08.sh — VIS-08: Revealing does not affect other tasks
  step: Submit both for task1, submit only alice→bob for task2
  step: GET /feedback/task/{task2}
  assert: feedback array is empty (task2 still sealed)

test-vis-09.sh — VIS-09: Timeout reveals sealed feedback
  NOTE: Requires short reveal_timeout_seconds config (e.g., 2 seconds).
  step: Submit (alice→bob) only, wait 3 seconds
  step: GET /feedback/task/{task_id}
  assert: feedback array length == 1

**FEEDBACK LOOKUP TESTS (test-read-01.sh to test-read-05.sh):**

test-read-01.sh — READ-01: Lookup revealed feedback
test-read-02.sh — READ-02: Non-existent feedback (404 FEEDBACK_NOT_FOUND)
test-read-03.sh — READ-03: Malformed feedback ID (404, no leakage)
test-read-04.sh — READ-04: SQL injection in path parameters
test-read-05.sh — READ-05: Idempotent read

**TASK/AGENT/HEALTH/HTTP/SECURITY TESTS:**

test-task-01.sh — TASK-01: No feedback for task (200, empty array)
test-task-02.sh — TASK-02: Visible feedback appears in task query (200, 2 entries)
test-agent-01.sh — AGENT-01: No feedback about agent (200, empty array)
test-agent-02.sh — AGENT-02: Feedback from multiple tasks (200, 2 entries)
test-agent-03.sh — AGENT-03: Feedback BY agent not included
test-health-01.sh — HEALTH-01: Health schema correct
test-health-02.sh — HEALTH-02: Total feedback count exact
test-health-03.sh — HEALTH-03: Uptime monotonic
test-http-01.sh — HTTP-01: Wrong methods on all routes (405)
test-sec-01.sh — SEC-01: Error envelope consistency
test-sec-02.sh — SEC-02: No internal error leakage
test-sec-03.sh — SEC-03: Feedback IDs are opaque fb-<uuid4>

After all scripts: `chmod +x tests/acceptance/*.sh`

### Phase 4: Python Unit Test Infrastructure

#### `tests/conftest.py`

Docstring-only file. No fixtures at this level.

#### `tests/unit/conftest.py`

```python
@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Reset settings singleton between tests."""
    from reputation_service.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
```

#### `tests/unit/routers/conftest.py`

This is the primary fixture file for Python tests. Must provide:

1. **JWS helper fixtures:**
   - `make_keypair()` — returns `(private_key_hex, public_key_b64)` using `Ed25519PrivateKey.generate()`
   - `alice_keypair`, `bob_keypair`, `carol_keypair` — fixture keypairs for test agents
   - `make_jws(private_hex, agent_id, payload_dict)` — creates JWS compact token using `joserfc`
   - `tamper_jws(token)` — alters the payload portion after signing

2. **Agent ID fixtures:**
   - `alice_agent_id`, `bob_agent_id`, `carol_agent_id` — fixed UUIDs in `a-<uuid4>` format
   - `make_agent_id()` — generates random `a-<uuid4>` strings
   - `make_task_id()` — generates random `t-<uuid4>` strings

3. **Mock fixtures:**
   - `mock_identity_verify_jws` — patches the Identity service client to return success. Must extract `agent_id` from JWS `kid` header and return it as the verified signer.
   - `mock_identity_verify_jws_tampered` — returns failure (signature mismatch)
   - `mock_identity_verify_jws_unregistered` — returns failure (agent not found)
   - `mock_identity_unavailable` — raises connection error
   - `mock_identity_unexpected_response` — returns HTTP 500 with non-JSON body

4. **App + client fixture:**
   - `app` — creates the FastAPI app via `create_app()` with test config (short reveal_timeout_seconds, Identity service URL pointing to mock)
   - `client` — `httpx.AsyncClient` or `TestClient` bound to the app

5. **Feedback submission helper:**
   - `submit_feedback_jws(client, make_jws, signer_keypair, signer_id, payload_overrides)` — constructs a valid feedback JWS and POSTs it to `/feedback`
   - `submit_and_reveal(client, make_jws, alice_keypair, alice_id, bob_keypair, bob_id, task_id)` — submits both directions to reveal feedback

### Phase 5: Auth Tests (`test_feedback_auth.py`)

39 test cases organized into classes:

```python
class TestJWSTokenValidation:       # AUTH-01 to AUTH-08 (8 tests)
class TestJWSPayloadValidation:     # AUTH-09 to AUTH-11 (3 tests)
class TestSignerMatching:           # AUTH-12 to AUTH-14 (3 tests)
class TestIdentityUnavailability:   # AUTH-15 to AUTH-16 (2 tests)
class TestPublicEndpoints:          # PUB-01 to PUB-04 (4 tests)
class TestErrorPrecedence:          # PREC-01 to PREC-07 (7 tests)
class TestExistingValidationsViaJWS: # VJWS-01 to VJWS-09 (9 tests)
class TestAuthSecurity:             # SEC-AUTH-01 to SEC-AUTH-03 (3 tests)
```

**JWS Token Validation (AUTH-01 to AUTH-08):**
- AUTH-01: Valid JWS submits feedback (201)
- AUTH-02: Missing token field (400 INVALID_JWS)
- AUTH-03: Null token (400 INVALID_JWS)
- AUTH-04: Non-string token types — integer, array, object, boolean (400 INVALID_JWS for each)
- AUTH-05: Empty string token (400 INVALID_JWS)
- AUTH-06: Malformed JWS — not three-part, two-part, four-part (400 INVALID_JWS for each)
- AUTH-07: Tampered JWS payload (403 FORBIDDEN)
- AUTH-08: JWS signed by unregistered agent (403 FORBIDDEN)

**JWS Payload Validation (AUTH-09 to AUTH-11):**
- AUTH-09: Missing action in payload (400 INVALID_PAYLOAD)
- AUTH-10: Wrong action value (400 INVALID_PAYLOAD)
- AUTH-11: Null action (400 INVALID_PAYLOAD)

**Authorization / Signer Matching (AUTH-12 to AUTH-14):**
- AUTH-12: Signer matches from_agent_id (201)
- AUTH-13: Signer != from_agent_id — impersonation (403 FORBIDDEN)
- AUTH-14: Signer impersonates non-existent agent (403 FORBIDDEN)

**Identity Service Unavailability (AUTH-15 to AUTH-16):**
- AUTH-15: Identity service is down (502 IDENTITY_SERVICE_UNAVAILABLE)
- AUTH-16: Identity service returns unexpected response (502 IDENTITY_SERVICE_UNAVAILABLE)

**Public Endpoints (PUB-01 to PUB-04):**
- PUB-01: GET /feedback/{id} requires no auth (200)
- PUB-02: GET /feedback/task/{task_id} requires no auth (200)
- PUB-03: GET /feedback/agent/{agent_id} requires no auth (200)
- PUB-04: GET /health requires no auth (200)

**Error Precedence (PREC-01 to PREC-07):**
- PREC-01: Content-Type before token validation (415 not 400)
- PREC-02: Body size before token validation (413 not 400)
- PREC-03: JSON parsing before token validation (INVALID_JSON not INVALID_JWS)
- PREC-04: Token validation before payload validation (INVALID_JWS not INVALID_PAYLOAD)
- PREC-05: Action validation before signer matching (INVALID_PAYLOAD not FORBIDDEN)
- PREC-06: Signer matching before feedback field validation (FORBIDDEN not INVALID_RATING)
- PREC-07: Identity unavailable before payload validation (502 not 400)

**Existing Validations Through JWS (VJWS-01 to VJWS-09):**
These verify that standard feedback validation rules still apply when data comes inside a JWS payload.
- VJWS-01: Missing feedback fields in JWS payload (400 MISSING_FIELD)
- VJWS-02: Invalid rating in JWS payload (400 INVALID_RATING)
- VJWS-03: Invalid category in JWS payload (400 INVALID_CATEGORY)
- VJWS-04: Self-feedback in JWS payload (400 SELF_FEEDBACK)
- VJWS-05: Comment too long in JWS payload (400 COMMENT_TOO_LONG)
- VJWS-06: Duplicate feedback via JWS (409 FEEDBACK_EXISTS)
- VJWS-07: Mutual reveal through JWS — submit both directions, verify visibility
- VJWS-08: Extra fields in JWS payload ignored (201)
- VJWS-09: Concurrent duplicate via JWS race safe (one 201, one 409)

**Auth Cross-Cutting Security (SEC-AUTH-01 to SEC-AUTH-03):**
- SEC-AUTH-01: Error envelope consistency for all auth error codes
- SEC-AUTH-02: No internal error leakage in auth failures
- SEC-AUTH-03: Cross-service token replay rejected (escrow_lock action on feedback endpoint → 400 INVALID_PAYLOAD)

### Phase 6: Stubs

`tests/integration/conftest.py` and `tests/performance/conftest.py` — docstring only.

---

## Test Conventions

### Bash tests

Every `.sh` file must:
- Start with `#!/bin/bash`
- Be made executable with `chmod +x`
- Source `helpers.sh`
- Use `test_start`, `step`, `test_end` for structured output
- Use `assert_*` functions for all validations
- Fail fast on assertion failure (via `set -e` in helpers.sh)

### Python tests

Every test method must:
- Be marked with `@pytest.mark.unit`
- Have a docstring referencing the test ID (e.g., `"""AUTH-01: Valid JWS submits feedback."""`)
- Follow the three-part structure: **Setup**, **Action**, **Expected**
- Use the conftest fixtures and helpers
- Assert exact status codes and error codes as specified in the test spec

For concurrent race condition tests (FB-20, VJWS-09):
- Use `asyncio.gather()` or threading to send parallel requests
- Assert one succeeds and one fails with the expected conflict error

---

## Verification

```bash
just ci-quiet
```

Expected outcomes:
- `ruff check` — passes (syntactically valid)
- `ruff format --check` — passes (correctly formatted)
- `pytest` — tests FAIL (service not implemented) or ERROR (import errors)
- `codespell` — passes
- Bash scripts — syntactically valid (bash -n check)
- Type checking may show errors for unresolvable imports — this is acceptable

The key requirement: all test files are **syntactically valid and CI-compliant** even though they fail at runtime.
