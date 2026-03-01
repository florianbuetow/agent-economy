# BankMixin Implementation Plan

> **For Codex:** Read this plan top-to-bottom. Execute phases in order. TDD: write tests first, verify they fail, then implement.

**Goal:** Implement `BankMixin` with three agent-facing methods (`get_balance`, `get_transactions`, `lock_escrow`) and write e2e tests that exercise them against live identity + central bank services.

**Architecture:** Follow the exact same mixin pattern as `IdentityMixin` in `agents/src/base_agent/mixins/identity.py`. Uses `_BankClient` Protocol for type safety, `_auth_header()` for Bearer auth, `_sign_jws()` for body tokens, and `_request()` / `_request_raw()` for HTTP calls.

---

## Phase 1: Write Unit Tests for BankMixin (TDD)

**Files:**
- Create: `agents/tests/unit/test_bank_mixin.py`

**Read first:**
- `agents/tests/unit/test_identity_mixin.py` — exact pattern to follow
- `agents/tests/unit/conftest.py` — `sample_config` fixture
- `docs/specifications/service-api/central-bank-service-specs.md` — endpoint contracts

**Step 1: Create `agents/tests/unit/test_bank_mixin.py`**

Write these test classes, all marked `@pytest.mark.unit`:

### TestGetBalance
- `test_get_balance_returns_account`: Mock `_request` to return `{"account_id": "a-123", "balance": 100, "created_at": "2026-01-01T00:00:00Z"}`. Set `agent.agent_id = "a-123"`. Call `agent.get_balance()`. Assert:
  - Result matches mock return value
  - `_request` called with `"GET"`, `"{bank_url}/accounts/a-123"`, `headers=agent._auth_header({"action": "get_balance", "account_id": "a-123"})`

### TestGetTransactions
- `test_get_transactions_returns_list`: Mock `_request` to return `{"transactions": [{"tx_id": "tx-1", "type": "credit", "amount": 50, "balance_after": 50, "reference": "initial_balance", "timestamp": "2026-01-01T00:00:00Z"}]}`. Set `agent.agent_id = "a-123"`. Call `agent.get_transactions()`. Assert:
  - Result is the list (unwrapped from `{"transactions": [...]}`)
  - `_request` called with `"GET"`, `"{bank_url}/accounts/a-123/transactions"`, `headers=agent._auth_header({"action": "get_transactions", "account_id": "a-123"})`

### TestLockEscrow
- `test_lock_escrow_success`: Mock `_request` to return `{"escrow_id": "esc-1", "amount": 10, "task_id": "T-123", "status": "locked"}`. Set `agent.agent_id = "a-123"`. Call `agent.lock_escrow(amount=10, task_id="T-123")`. Assert:
  - Result matches mock return value
  - `_request` called with `"POST"`, `"{bank_url}/escrow/lock"`, `json={"token": <any string>}`

For `lock_escrow`, the token is a JWS created by `_sign_jws({"action": "escrow_lock", "agent_id": "a-123", "amount": 10, "task_id": "T-123"})`. Since we can't predict the exact JWS string, mock `_sign_jws` to return a known string `"test-jws-token"`, then assert `_request` was called with `json={"token": "test-jws-token"}`.

All tests must:
- Use `sample_config` fixture from conftest
- Create `BaseAgent(config=sample_config)`, set `agent.agent_id = "a-123"`
- Mock the appropriate methods with `AsyncMock`
- Call `await agent.close()` at end

**Step 2: Run tests — verify they FAIL**

```bash
cd agents && uv run pytest tests/unit/test_bank_mixin.py -v
```

Expected: `AttributeError` — `get_balance`, `get_transactions`, `lock_escrow` don't exist yet.

---

## Phase 2: Implement BankMixin

**Files:**
- Modify: `agents/src/base_agent/mixins/bank.py`

**Read first:**
- `agents/src/base_agent/mixins/identity.py` — exact pattern to follow
- `agents/src/base_agent/agent.py` — `_auth_header()`, `_sign_jws()`, `_request()`

**Implementation:**

```python
"""Central Bank mixin — account balance, transactions, escrow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


class _BankClient(Protocol):
    config: AgentConfig
    agent_id: str | None

    def _sign_jws(self, payload: dict[str, object]) -> str: ...

    def _auth_header(self, payload: dict[str, object]) -> dict[str, str]: ...

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]: ...


class BankMixin:
    """Methods for interacting with the Central Bank service (port 8002)."""

    async def get_balance(self: _BankClient) -> dict[str, Any]:
        """Get this agent's account balance."""
        url = f"{self.config.bank_url}/accounts/{self.agent_id}"
        headers = self._auth_header({
            "action": "get_balance",
            "account_id": self.agent_id,
        })
        return await self._request("GET", url, headers=headers)

    async def get_transactions(self: _BankClient) -> list[dict[str, Any]]:
        """Get this agent's transaction history."""
        url = f"{self.config.bank_url}/accounts/{self.agent_id}/transactions"
        headers = self._auth_header({
            "action": "get_transactions",
            "account_id": self.agent_id,
        })
        response = await self._request("GET", url, headers=headers)
        return cast("list[dict[str, Any]]", response["transactions"])

    async def lock_escrow(self: _BankClient, amount: int, task_id: str) -> dict[str, Any]:
        """Lock funds in escrow for a task."""
        url = f"{self.config.bank_url}/escrow/lock"
        token = self._sign_jws({
            "action": "escrow_lock",
            "agent_id": self.agent_id,
            "amount": amount,
            "task_id": task_id,
        })
        return await self._request("POST", url, json={"token": token})
```

**Step 3: Run tests — verify they PASS**

```bash
cd agents && uv run pytest tests/unit/test_bank_mixin.py -v
```

Expected: All 3 tests pass.

**Step 4: Run full CI**

```bash
cd agents && just ci-quiet
```

Expected: All tests pass, no lint/type/format errors.

**Step 5: Commit**

```bash
cd agents && git add src/base_agent/mixins/bank.py tests/unit/test_bank_mixin.py
git commit -m "feat(agents): implement BankMixin with get_balance, get_transactions, lock_escrow"
```

---

## Phase 3: Write E2E Tests for Bank

**Files:**
- Modify: `agents/tests/e2e/conftest.py` — add bank health check, platform helper fixture
- Create: `agents/tests/e2e/test_bank.py`

**Read first:**
- `agents/tests/e2e/conftest.py` — existing identity health check pattern
- `agents/tests/e2e/test_identity.py` — existing e2e test pattern
- `docs/specifications/service-api/central-bank-service-specs.md` — endpoint contracts

### Important context for e2e bank tests

The central bank requires a **platform agent** to create accounts and credit them. The platform agent_id is configured in `services/central-bank/config.yaml` as `"a-platform-placeholder"`.

For e2e tests, the conftest must:
1. Check that both identity AND central bank services are healthy
2. Register a "platform" agent with the identity service
3. Update the bank's in-memory platform_agent_id OR accept that the platform_agent_id is pre-configured

**Simplest approach:** The e2e conftest creates a `PlatformHelper` class that:
- Generates its own keypair and registers with identity
- Makes raw HTTP calls to the bank's platform-only endpoints using JWS tokens signed with its key
- The bank service must be started with `CENTRAL_BANK__PLATFORM__AGENT_ID` set to this agent's ID

Since we can't control the bank's config at test time, the e2e test should:
1. Register the platform agent → get its `agent_id`
2. Try to create an account via the bank — if the bank rejects with FORBIDDEN, skip with a clear message about configuring the platform_agent_id

**BUT** — a simpler approach is better: use `httpx` to read the bank's health endpoint, and have the test conftest work with whatever platform_agent_id the bank is configured with. The test needs a **pre-registered platform agent whose agent_id matches the bank's config**. This is a deployment concern, not a test concern.

**Recommended approach for the conftest:**

```python
BANK_URL = "http://localhost:8002"

@pytest.fixture(scope="session", autouse=True)
def _require_bank_service() -> None:
    """Abort test session if central bank is not running."""
    try:
        response = httpx.get(f"{BANK_URL}/health", timeout=3.0)
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        pytest.exit(f"Central bank not running at {BANK_URL}: {exc}", returncode=1)
```

For the platform operations (create_account, credit), create a helper class in the conftest that takes a private key and agent_id and makes signed requests to the bank:

```python
class PlatformHelper:
    """Helper for platform-only bank operations in e2e tests."""

    def __init__(self, agent_id: str, private_key: Ed25519PrivateKey) -> None:
        self.agent_id = agent_id
        self._private_key = private_key
        self._http = httpx.AsyncClient()

    def _sign(self, payload: dict[str, object]) -> str:
        return create_jws(payload, self._private_key, kid=self.agent_id)

    async def create_account(self, target_agent_id: str, initial_balance: int) -> dict[str, Any]:
        token = self._sign({
            "action": "create_account",
            "agent_id": target_agent_id,
            "initial_balance": initial_balance,
        })
        resp = await self._http.post(f"{BANK_URL}/accounts", json={"token": token})
        resp.raise_for_status()
        return resp.json()

    async def credit_account(self, account_id: str, amount: int, reference: str) -> dict[str, Any]:
        token = self._sign({
            "action": "credit",
            "account_id": account_id,
            "amount": amount,
            "reference": reference,
        })
        resp = await self._http.post(f"{BANK_URL}/accounts/{account_id}/credit", json={"token": token})
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        await self._http.aclose()
```

The fixture:

```python
@pytest.fixture(scope="session")
async def platform() -> AsyncGenerator[PlatformHelper, None]:
    """Register a platform agent and return a helper for platform operations."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    pub_b64 = public_key_to_b64(public_key)

    # Register with identity service
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{IDENTITY_URL}/agents/register",
            json={"name": "E2E Platform", "public_key": f"ed25519:{pub_b64}"},
        )
        resp.raise_for_status()
        platform_agent_id = resp.json()["agent_id"]

    helper = PlatformHelper(platform_agent_id, private_key)
    try:
        yield helper
    finally:
        await helper.close()
```

**IMPORTANT:** The bank service must be started with the platform agent_id matching the registered platform agent. Since the agent_id is generated at registration time, the e2e tests need a setup script or the bank must be restarted after platform registration. For simplicity, the e2e test can **skip** if the platform account creation fails with 403.

### E2E Test: `test_bank.py`

```python
@pytest.mark.e2e
class TestBankE2E:
    async def test_balance_and_transactions(self, agent_config, platform):
        """Register agent, create account, credit it, check balance and transactions."""
        agent = BaseAgent(config=agent_config)
        try:
            # 1. Register agent with identity service
            await agent.register()
            assert agent.agent_id is not None

            # 2. Platform creates account with initial balance
            account = await platform.create_account(agent.agent_id, initial_balance=100)
            assert account["balance"] == 100

            # 3. Agent checks own balance
            balance = await agent.get_balance()
            assert balance["balance"] == 100
            assert balance["account_id"] == agent.agent_id

            # 4. Platform credits the account
            await platform.credit_account(agent.agent_id, amount=50, reference="e2e_test_credit")

            # 5. Agent checks updated balance
            balance2 = await agent.get_balance()
            assert balance2["balance"] == 150

            # 6. Agent checks transaction history
            txns = await agent.get_transactions()
            assert len(txns) >= 2  # initial_balance + credit
            types = [t["type"] for t in txns]
            assert "credit" in types
        finally:
            await agent.close()

    async def test_lock_escrow(self, agent_config, platform):
        """Register agent, fund account, lock escrow."""
        agent = BaseAgent(config=agent_config)
        try:
            await agent.register()
            assert agent.agent_id is not None

            # Setup: create and fund account
            await platform.create_account(agent.agent_id, initial_balance=100)

            # Lock escrow
            escrow = await agent.lock_escrow(amount=30, task_id="T-e2e-test")
            assert escrow["amount"] == 30
            assert escrow["task_id"] == "T-e2e-test"
            assert escrow["status"] == "locked"
            assert escrow["escrow_id"].startswith("esc-")

            # Balance should be reduced
            balance = await agent.get_balance()
            assert balance["balance"] == 70

            # Transaction history should include escrow_lock
            txns = await agent.get_transactions()
            lock_txns = [t for t in txns if t["type"] == "escrow_lock"]
            assert len(lock_txns) == 1
            assert lock_txns[0]["amount"] == 30
        finally:
            await agent.close()
```

**Step 6: Run e2e tests (requires running services)**

```bash
cd agents && uv run pytest tests/e2e/test_bank.py -v -m e2e
```

**Step 7: Run full CI (unit tests only, e2e excluded)**

```bash
cd agents && just ci-quiet
```

**Step 8: Commit**

```bash
cd agents && git add tests/e2e/conftest.py tests/e2e/test_bank.py
git commit -m "test(agents): add e2e tests for BankMixin against live services"
```
