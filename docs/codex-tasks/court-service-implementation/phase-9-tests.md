# Phase 9 — Tests

## Working Directory

```
services/court/
```

Write all test files. The test specification documents (`court-service-tests.md` and `court-service-auth-tests.md`) are the source of truth — implement every test case listed there.

---

## Test Architecture

### File Structure

```
tests/
├── conftest.py                     # Shared config (minimal docstring)
├── unit/
│   ├── conftest.py                 # Auto-clear settings cache + app state
│   ├── test_config.py              # Config loading and validation tests
│   ├── test_dispute_service.py     # DisputeService unit tests
│   ├── test_judges.py              # Judge system unit tests
│   └── routers/
│       ├── conftest.py             # App fixture, client, JWS helpers, all mocks
│       ├── test_health.py          # Health endpoint tests
│       └── test_disputes.py        # All dispute endpoint tests
├── integration/
│   └── conftest.py                 # Stub (docstring only)
└── performance/
    └── conftest.py                 # Stub (docstring only)
```

---

## Fixture Strategy

### `tests/unit/conftest.py`

Autouse fixture that clears settings cache and resets app state before and after each test. Same pattern as Central Bank:

```python
@pytest.fixture(autouse=True)
def _clear_caches():
    clear_settings_cache()
    reset_app_state()
    yield
    clear_settings_cache()
    reset_app_state()
```

### `tests/unit/routers/conftest.py`

This is the heaviest fixture file. It must provide:

**Ed25519 Key Helpers:**
- `_generate_keypair()` — returns `(Ed25519PrivateKey, formatted_public_key_string)`
- `make_jws_token(private_key, agent_id, payload)` — creates a compact JWS token using `joserfc`
- `platform_keypair` fixture
- `non_platform_keypair` fixture (for testing non-platform signer rejection)

**App Fixture** (`app`):
- Writes a temporary `config.yaml` to `tmp_path` with all required sections
- Sets `CONFIG_PATH` env var
- Generates a temporary Ed25519 key file for `platform.private_key_path`
- Creates the app via `create_app()`
- Runs the lifespan
- **Replaces all external clients with mocks:**
  - `state.identity_client` → `AsyncMock` (controls JWS verification responses)
  - `state.task_board_client` → `AsyncMock` (controls task fetch responses)
  - `state.central_bank_client` → `AsyncMock` (controls escrow split responses)
  - `state.reputation_client` → `AsyncMock` (controls feedback responses)
  - `state.judge_panel` → list of mock judges (controls vote responses)
- Yields the app, then cleans up

**Client Fixture** (`client`):
```python
@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

**Identity Mock Setup Helper:**
```python
def setup_identity_mock(state, *, valid=True, agent_id=PLATFORM_AGENT_ID):
    """Configure the mock identity client to return verification results."""
    if valid:
        async def mock_verify(token):
            # Decode JWS parts to extract header/payload (no crypto verification)
            parts = token.split(".")
            header = json.loads(base64url_decode(parts[0]))
            payload = json.loads(base64url_decode(parts[1]))
            return {"valid": True, "agent_id": header["kid"], "payload": payload}
        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify)
    else:
        state.identity_client.verify_jws = AsyncMock(
            side_effect=ServiceError("FORBIDDEN", "...", 403, {})
        )
```

**Judge Mock Helper:**
```python
def setup_judge_mock(state, *, worker_pct=70, reasoning="Test reasoning"):
    """Configure mock judges to return fixed votes."""
    mock_judge = AsyncMock()
    mock_judge.evaluate = AsyncMock(return_value=JudgeVote(
        judge_id="judge-0", worker_pct=worker_pct,
        reasoning=reasoning, voted_at="2026-01-01T00:00:00Z",
    ))
    state.judge_panel = [mock_judge]
```

**Task Board Mock Helper:**
```python
def setup_task_board_mock(state, *, task_exists=True):
    """Configure mock task board client."""
    if task_exists:
        state.task_board_client.get_task = AsyncMock(return_value={
            "task_id": "t-...", "title": "Test Task", "spec": "Do X",
            "deliverables": ["file.txt"], "reward": 100,
            "poster_id": "a-poster", "worker_id": "a-worker",
        })
    else:
        state.task_board_client.get_task = AsyncMock(
            side_effect=ServiceError("TASK_NOT_FOUND", "...", 404, {})
        )
```

### Platform Agent ID Constant

```python
PLATFORM_AGENT_ID = "a-platform-test-id"
```

This must match the value in the temporary `config.yaml` written by the `app` fixture.

---

## Test File Mapping to Spec

### `test_config.py`

Tests from JUDGE-01 through JUDGE-05 (startup validation):
- Valid config loads successfully
- Even panel size raises `ValidationError`
- Zero panel size raises `ValidationError`
- Panel size mismatch (config says 3, but only 1 judge defined) raises
- Duplicate judge IDs raise
- Missing required sections raise

### `test_dispute_service.py`

Direct unit tests of `DisputeService` methods without going through HTTP. Test the SQLite operations, state machine transitions, uniqueness constraints.

### `test_judges.py`

Unit tests for the judge system:
- `JudgeVote` dataclass construction
- `DisputeContext` dataclass construction
- `LLMJudge.evaluate()` with mocked `litellm.acompletion` response — verify it parses JSON correctly
- `LLMJudge.evaluate()` with LiteLLM error → raises `ServiceError("JUDGE_UNAVAILABLE", ...)`
- `LLMJudge.evaluate()` with invalid JSON response → raises `JUDGE_UNAVAILABLE`
- `LLMJudge.evaluate()` with out-of-range `worker_pct` → raises `JUDGE_UNAVAILABLE`

### `test_health.py`

Tests HLTH-01 through HLTH-04:
- GET /health returns 200 with correct schema
- `total_disputes` and `active_disputes` counts are accurate
- POST /health returns 405

### `test_disputes.py`

This is the largest test file. It covers all test IDs from both spec documents:
- FILE-01 through FILE-17 (file dispute)
- REB-01 through REB-10 (submit rebuttal)
- RULE-01 through RULE-19 (trigger ruling)
- GET-01 through GET-05 (get dispute)
- LIST-01 through LIST-06 (list disputes)
- HTTP-01 (method not allowed — all 14 combinations)
- SEC-01 through SEC-03 (cross-cutting security)
- LIFE-01 through LIFE-05 (dispute lifecycle integration)
- AUTH-01 through AUTH-16 (platform JWS validation)
- PUB-01 through PUB-03 (public endpoints)
- IDEP-01 through IDEP-03 (identity service dependency)
- REPLAY-01 through REPLAY-02 (cross-operation token replay)
- PREC-01 through PREC-06 (error precedence)
- SEC-AUTH-01 through SEC-AUTH-03 (auth cross-cutting security)

Organize into test classes:
```python
@pytest.mark.unit
class TestFileDispute:
    """FILE-01 through FILE-17."""
    ...

@pytest.mark.unit
class TestSubmitRebuttal:
    """REB-01 through REB-10."""
    ...

@pytest.mark.unit
class TestTriggerRuling:
    """RULE-01 through RULE-19."""
    ...
```

### Integration and Performance Stubs

Both `conftest.py` files are minimal docstring-only stubs, matching the Central Bank pattern.

---

## Key Testing Patterns

### Testing Ruling Rollback

For tests like RULE-14 (judge unavailable), RULE-15 (Central Bank unavailable), RULE-16 (Reputation unavailable):

1. Set up a dispute in `rebuttal_pending` status
2. Configure the relevant mock to raise `ServiceError`
3. Call POST `/disputes/{id}/rule`
4. Assert 502 response
5. **Verify rollback**: GET the dispute, assert status is still `rebuttal_pending`, not `judging` or `ruled`

### Testing Error Precedence

For PREC tests, send requests that would trigger MULTIPLE errors, and verify only the highest-precedence error is returned:

```python
async def test_content_type_checked_before_token(self, client):
    """PREC-01: 415 beats INVALID_JWS."""
    response = await client.post(
        "/disputes/file",
        content=b"not json",
        headers={"content-type": "text/plain"},
    )
    assert response.status_code == 415
```

### Testing Token Replay

For REPLAY tests, create a valid JWS token with one action and send it to a different endpoint:

```python
async def test_rebuttal_token_rejected_on_file(self, client, platform_keypair):
    """REPLAY-01: submit_rebuttal token rejected on /disputes/file."""
    token = make_jws_token(platform_keypair[0], PLATFORM_AGENT_ID, {
        "action": "submit_rebuttal",
        "dispute_id": "disp-xxx",
        "rebuttal": "test",
    })
    response = await client.post("/disputes/file", json={"token": token})
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_PAYLOAD"
```

---

## Verification

```bash
just ci-quiet
```

This runs the full CI pipeline: formatting, linting, type checking (mypy + pyright), security scanning, spell checking, semgrep, and all tests. Every test must pass. This is the only gate that matters.
