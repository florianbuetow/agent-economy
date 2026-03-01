# Self-Service Bank Account Creation

> Read AGENTS.md FIRST for project conventions.
> Use `uv run` for all Python execution. Never use raw python or pip install.
> Do NOT modify any existing test files in tests/.
> After EACH task, run: `cd services/central-bank && just test`
> Skip `just ci-quiet` — I will run it on my machine. Just run `just test`.
> Commit after each task with a descriptive message.

## Background

Currently `POST /accounts` on the Central Bank is platform-only. Registered agents cannot create their own bank accounts — only the platform agent can. This means agents that register with the Identity service but don't have a platform agent provision their account will fail at their first economic operation (posting tasks, checking balance, etc.).

The fix: allow any agent that has registered with the Identity service to create its own bank account with a zero balance. The platform retains the exclusive ability to set non-zero initial balances and credit accounts.

## Important Files to Read First

1. `AGENTS.md` — project conventions
2. `services/central-bank/src/central_bank_service/routers/accounts.py` — the endpoint to modify
3. `services/central-bank/src/central_bank_service/routers/helpers.py` — `require_platform`, `verify_jws_token`, `get_platform_agent_id`
4. `services/central-bank/tests/unit/routers/conftest.py` — test fixtures: `make_jws_token`, `PLATFORM_AGENT_ID`, `_generate_keypair`
5. `services/central-bank/tests/unit/routers/test_accounts.py` — existing tests (especially `_setup_identity_mock_for_platform` and `test_create_account_non_platform_forbidden`)
6. `agents/src/base_agent/mixins/bank.py` — BankMixin to extend
7. `agents/src/base_agent/platform.py` — PlatformAgent.create_account for reference
8. `agents/src/task_feeder/__main__.py` — startup flow to update
9. `agents/src/math_worker/__main__.py` — startup flow to update
10. `agents/tests/e2e/test_agent_startup.py` — e2e tests to update

---

## Task 1: Relax authorization on `POST /accounts`

**Problem:** `POST /accounts` calls `require_platform()` on line 43 of `accounts.py`, which rejects all non-platform callers with 403. We need to allow registered agents to create their own accounts with balance 0.

**File to modify:** `services/central-bank/src/central_bank_service/routers/accounts.py`

**Steps:**

1. In the `create_account` function, replace line 43:

```python
require_platform(verified["agent_id"], get_platform_agent_id())
```

With this conditional logic:

```python
caller_agent_id = verified["agent_id"]
is_platform = caller_agent_id == get_platform_agent_id()
```

2. After the existing payload validation block (after the `initial_balance` validation around line 68, BEFORE the "Verify agent exists in Identity service" block), add these checks for non-platform callers:

```python
if not is_platform:
    # Non-platform callers can only create their own account
    if agent_id != caller_agent_id:
        raise ServiceError(
            "FORBIDDEN",
            "Agents can only create their own account",
            403,
            {},
        )
    # Non-platform callers must use initial_balance of 0
    if initial_balance != 0:
        raise ServiceError(
            "FORBIDDEN",
            "Only the platform can set a non-zero initial balance",
            403,
            {},
        )
```

3. Update the function docstring from `"Create a new account for an agent. Platform-only."` to `"Create a new account for an agent."`. Also update the comment on line 23 from `# === POST /accounts — Create Account (Platform-only) ===` to `# === POST /accounts — Create Account ===`.

4. Do NOT change anything else in the function. The identity verification, ledger call, logging, and response remain exactly the same.

5. Run `cd services/central-bank && just test` — all existing tests should still pass. The key existing test `test_create_account_non_platform_forbidden` has a non-platform agent trying to create an account for `"a-victim"` (a different agent), so it still gets 403 from the new `agent_id != caller_agent_id` check.

6. Commit: `feat(central-bank): allow registered agents to create own zero-balance accounts`

---

## Task 2: Add unit tests for self-service account creation

**Problem:** No tests cover the new self-service account creation path.

**File to create:** `services/central-bank/tests/unit/routers/test_self_service_account.py`

Do NOT modify any existing test files.

**Steps:**

1. Create the test file. Use the same test infrastructure from `conftest.py`: import `PLATFORM_AGENT_ID`, `make_jws_token`, and use the `client`, `agent_keypair`, `platform_keypair` fixtures.

2. Create a helper function `_setup_identity_mock_for_agent` similar to `_setup_identity_mock_for_platform` from `test_accounts.py`, but configured for a regular (non-platform) agent. It must:
   - Mock `verify_jws` to decode the JWS token parts and return `{"valid": True, "agent_id": <from header kid>, "payload": <decoded payload>}`
   - Mock `get_agent` to return the agent info (or None if testing agent-not-found)

3. Write these tests, all marked with `@pytest.mark.unit`:

**Test class: `TestAgentSelfServiceAccountCreation`**

```
test_agent_creates_own_account_with_zero_balance:
    - Agent signs JWS with kid=<own-agent-id>, payload: {action: "create_account", agent_id: <own-agent-id>, initial_balance: 0}
    - Identity mock returns valid=True, agent_id=<own-agent-id>
    - Identity mock get_agent returns the agent
    - POST /accounts with {"token": jws}
    - Assert 201
    - Assert response contains account_id and balance == 0

test_agent_cannot_create_account_for_another_agent:
    - Agent signs JWS with kid=<own-agent-id>, payload: {action: "create_account", agent_id: "a-someone-else", initial_balance: 0}
    - Identity mock returns valid=True, agent_id=<own-agent-id>
    - POST /accounts with {"token": jws}
    - Assert 403
    - Assert response error == "FORBIDDEN"

test_agent_cannot_create_account_with_nonzero_balance:
    - Agent signs JWS with kid=<own-agent-id>, payload: {action: "create_account", agent_id: <own-agent-id>, initial_balance: 100}
    - Identity mock returns valid=True, agent_id=<own-agent-id>
    - POST /accounts with {"token": jws}
    - Assert 403
    - Assert response error == "FORBIDDEN"

test_agent_duplicate_account_returns_409:
    - Agent creates own account (first call succeeds with 201)
    - Agent tries again with same payload
    - Assert 409
    - Assert response error == "ACCOUNT_EXISTS"

test_agent_not_found_in_identity_returns_404:
    - Agent signs JWS with kid=<own-agent-id>, payload: {action: "create_account", agent_id: <own-agent-id>, initial_balance: 0}
    - Identity mock returns valid=True but get_agent returns None
    - POST /accounts with {"token": jws}
    - Assert 404
    - Assert response error == "AGENT_NOT_FOUND"

test_platform_still_creates_accounts_with_balance (regression guard):
    - Platform signs JWS with kid=PLATFORM_AGENT_ID, payload: {action: "create_account", agent_id: "a-test-agent", initial_balance: 500}
    - Identity mock configured for platform (use _setup_identity_mock_for_platform pattern)
    - POST /accounts with {"token": jws}
    - Assert 201
    - Assert response balance == 500
```

4. Run `cd services/central-bank && just test` — all tests (old and new) should pass.

5. Commit: `test(central-bank): add self-service account creation tests`

---

## Task 3: Add `create_account()` to BankMixin

**Problem:** Only `PlatformAgent` has a `create_account()` method. Regular agents (`BaseAgent`) have no way to create their own bank account.

**File to modify:** `agents/src/base_agent/mixins/bank.py`

**Steps:**

1. Add the following method to the `BankMixin` class, before the existing `get_balance` method:

```python
async def create_account(self: _BankClient) -> dict[str, Any]:
    """Create a zero-balance bank account for this agent.

    The agent must be registered first (agent_id must be set).
    Calls POST /accounts with a self-signed JWS token.
    The Central Bank verifies the agent's identity before creating the account.

    Returns:
        Account creation response with account_id, balance, and created_at.

    Raises:
        httpx.HTTPStatusError: On failure (e.g., 409 if account already exists).
    """
    url = f"{self.config.bank_url}/accounts"
    token = self._sign_jws(
        {
            "action": "create_account",
            "agent_id": self.agent_id,
            "initial_balance": 0,
        }
    )
    return await self._request("POST", url, json={"token": token})
```

2. Verify the `_BankClient` protocol already has the needed methods (`config`, `agent_id`, `_sign_jws`, `_request`). It does — no protocol changes needed.

3. Run `cd agents && uv run pytest tests/unit/ -v` to verify nothing breaks.

4. Commit: `feat(agents): add create_account to BankMixin for self-service account creation`

---

## Task 4: Update agent startup flows

**Problem:** Both `task_feeder/__main__.py` and `math_worker/__main__.py` call `register()` but never create a bank account. Agents need to call `create_account()` after registration.

**Files to modify:**
- `agents/src/task_feeder/__main__.py`
- `agents/src/math_worker/__main__.py`

**Steps for task_feeder/__main__.py:**

1. Add `import httpx` to the imports at the top of the file (after the existing `import sys` line).

2. After the `await agent.register()` line (currently line 45) and the logger.info line (line 46), add:

```python
    # Create bank account (idempotent — 409 if already exists)
    try:
        await agent.create_account()
        logger.info("Bank account created for agent_id=%s", agent.agent_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            logger.info("Bank account already exists for agent_id=%s", agent.agent_id)
        else:
            raise
```

**Steps for math_worker/__main__.py:**

1. Add `import httpx` to the imports at the top of the file (after the existing `import sys` line).

2. After the `await agent.register()` line (currently line 46) and the logger.info line (line 47), add the exact same block:

```python
    # Create bank account (idempotent — 409 if already exists)
    try:
        await agent.create_account()
        logger.info("Bank account created for agent_id=%s", agent.agent_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            logger.info("Bank account already exists for agent_id=%s", agent.agent_id)
        else:
            raise
```

3. Run `cd agents && uv run pytest tests/unit/ -v` to verify nothing breaks.

4. Commit: `feat(agents): add bank account creation to agent startup flow`

---

## Task 5: Update e2e tests

**Problem:** The e2e tests in `agents/tests/e2e/test_agent_startup.py` currently demonstrate the bug (agents without accounts fail). Now that agents can self-provision accounts, update these tests to verify the fixed behavior.

**File to modify:** `agents/tests/e2e/test_agent_startup.py`

**Steps:**

1. Replace the entire content of the test file with tests that verify the FIXED behavior. The new tests should:

**test_registered_agent_can_create_own_account:**
- Create a BaseAgent with a fresh keypair
- Register with Identity
- Call `agent.create_account()` (the new BankMixin method)
- Assert it succeeds (no exception)
- Call `agent.get_balance()` and assert balance == 0

**test_registered_agent_can_post_task_after_account_creation:**
- Create a BaseAgent, register, create_account
- Use the platform_agent fixture to credit the agent with funds
- Post a task
- Assert the task was created successfully (status == "open")

**test_create_account_is_idempotent:**
- Create a BaseAgent, register, create_account (succeeds)
- Call create_account again
- Assert it raises httpx.HTTPStatusError with status 409
- Verify get_balance still works (account is fine)

**test_unregistered_agent_cannot_create_account:**
- Create a BaseAgent with a fresh keypair but do NOT register
- Manually set agent_id to a fake value
- Call create_account
- Assert it raises httpx.HTTPStatusError (JWS verification will fail because the agent doesn't exist in Identity)

2. Keep using the same imports and URL constants from the existing file. Use the `platform_agent` fixture from conftest.py where needed for funding.

3. All tests must be marked with `@pytest.mark.e2e`.

4. NOTE: These tests require all services running. They won't be run during `just test` but can be run manually with `cd agents && uv run pytest tests/e2e/test_agent_startup.py -v`.

5. Commit: `test(agents): update e2e tests for self-service account creation`

---

## Summary

Execute tasks 1 through 5 in order. Each task builds on the previous.

- After tasks 1-2: run `cd services/central-bank && just test`
- After tasks 3-5: run `cd agents && uv run pytest tests/unit/ -v`
- Do NOT run `just ci-quiet` — I will run it myself.
- Do NOT run e2e tests — they require running services, I will run them myself.
