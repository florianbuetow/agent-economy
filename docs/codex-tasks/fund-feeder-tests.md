Read these files FIRST before doing anything:
1. AGENTS.md — project conventions, architecture, testing rules
2. agents/pyproject.toml — dependencies, test config, linting rules
3. agents/src/base_agent/signing.py — Ed25519 key management and JWS token creation
4. agents/src/base_agent/platform.py — PlatformAgent with credit_account() method
5. agents/src/base_agent/mixins/identity.py — IdentityMixin.register() method
6. agents/src/base_agent/mixins/bank.py — BankMixin.create_account(), get_balance()
7. agents/src/base_agent/agent.py — BaseAgent class
8. agents/src/base_agent/config.py — AgentConfig and load_agent_config()
9. agents/src/base_agent/factory.py — AgentFactory
10. agents/config.yaml — agent config (service URLs, key paths, feeder settings)
11. agents/roster.yaml — agent roster (platform, feeder handles)
12. agents/tests/e2e/conftest.py — existing e2e test fixtures (platform_agent, make_funded_agent)

After reading ALL files, implement the following. Execute Phase 1 through Phase 3 in order. Do NOT skip phases.

All NEW test files go in: agents/tests/e2e/
Do NOT modify any existing test files. Only create new files.
Use `uv run` for all Python execution — never use raw python, python3, or pip install.
Do NOT implement the fund-feeder script itself. Only create tests.
Tests are pytest-based async integration tests using the agents package.

## Background

The fund-feeder script (`tools/fund-feeder.sh` — does NOT exist yet) will be a shell script that:
1. Ensures all services are running
2. Registers the feeder agent if not already registered (via Identity service)
3. Creates a bank account for the feeder if not already created
4. Uses the platform agent to transfer funds to the feeder via the Central Bank credit endpoint
5. Prints a summary (agent_id, funded amount, resulting balance)
6. Exits 0 on success, 1 on failure
7. Accepts funding amount as a CLI argument (no hardcoded default)

Since the fund-feeder.sh script does not exist yet, we write **pytest-based e2e tests** that verify
the _behaviors_ the script must implement. These tests exercise the same service API calls
that the script will make, using the existing `base_agent` Python SDK. The tests will PASS
against the running services, validating that the API workflows are correct. When the actual
shell script is later implemented, these tests serve as the behavioral specification.

## Important API Details

### How funding works (Central Bank credit endpoint)
- POST /accounts/{account_id}/credit
- Request body: `{"token": "<JWS>"}`
- JWS payload: `{"action": "credit", "account_id": "<id>", "amount": <int>, "reference": "<string>"}`
- Signed by the **platform agent** (only platform can credit)
- Reference must be unique per (account_id, reference) pair for idempotency
- Returns: `{"tx_id": "...", "balance_after": <int>}`

### How registration works (Identity service)
- POST /agents/register with `{"name": "Task Feeder", "public_key": "ed25519:<base64>"}`
- Returns 201 with agent_id on first call, 409 if public key already registered
- The feeder agent handles 409 by looking up existing agent_id

### How account creation works (Central Bank)
- POST /accounts with JWS token signed by platform agent
- JWS payload: `{"action": "create_account", "agent_id": "<id>", "initial_balance": 0}`
- Returns 201 on first call, 409 if account already exists

### Keys
- Platform keys: `data/keys/platform.key` and `data/keys/platform.pub`
- Feeder keys: `data/keys/feeder.key` and `data/keys/feeder.pub`
- Keys are Ed25519 in PEM format

=== PHASE 1: Test File ===

Create file: agents/tests/e2e/test_fund_feeder.py

This file contains all acceptance tests for the fund-feeder workflow.
Use `@pytest.mark.e2e` marker on every test.
Use `pytest.mark.asyncio` is auto-configured (asyncio_mode = "auto" in pyproject.toml), so do NOT add it manually.

### Fixtures (at top of file, before tests)

1. `feeder_agent` (async fixture, function scope):
   - Use AgentFactory with config_path pointing to agents/config.yaml
   - Call `factory.create_agent("feeder")` to create a feeder BaseAgent
   - Call `await agent.register()` (handles both 201 and 409 idempotently)
   - yield the agent
   - Call `await agent.close()` in teardown

2. `platform` (async fixture, function scope):
   - Use AgentFactory with config_path pointing to agents/config.yaml
   - Call `factory.platform_agent()` to get a PlatformAgent
   - Call `await platform.register()` (handles both 201 and 409)
   - yield the platform agent
   - Call `await platform.close()` in teardown

3. `funded_feeder` (async fixture, function scope, depends on feeder_agent and platform):
   - Create feeder bank account via platform: `await platform.create_account(agent_id=feeder_agent.agent_id, initial_balance=0)`
     - Wrap in try/except httpx.HTTPStatusError, ignore 409 (account exists)
   - Credit the feeder with 500 coins: `await platform.credit_account(account_id=feeder_agent.agent_id, amount=500, reference="fund_feeder_test_setup")`
     - Wrap in try/except for idempotency (409 with PAYLOAD_MISMATCH is ok to ignore if same amount)
   - yield feeder_agent

### Tests (implement ALL of these)

#### Test 1: test_feeder_registration_is_idempotent
"""Registering the feeder agent twice yields the same agent_id."""
- Create feeder agent using AgentFactory, register it
- Note the agent_id
- Create another feeder agent using AgentFactory with same config, register it
- Assert both agent_ids are the same
- Close both agents

#### Test 2: test_feeder_account_creation
"""Platform agent can create a bank account for the feeder."""
- Use platform and feeder_agent fixtures
- Create account: `await platform.create_account(agent_id=feeder_agent.agent_id, initial_balance=0)`
  - May get 409 if account already exists from previous test run — that's fine, handle it
- Verify the account exists by checking balance (get_balance needs the feeder to check its own balance)
  - Use feeder_agent.get_balance() to verify the account exists
  - Assert result contains "account_id" and "balance" keys

#### Test 3: test_platform_can_credit_feeder
"""Platform agent can credit funds to the feeder's account."""
- Use funded_feeder fixture (which already has 500 coins)
- Use platform fixture
- Generate a unique reference: `f"test_credit_{uuid4().hex[:8]}"`
- Credit 100 coins: `result = await platform.credit_account(account_id=funded_feeder.agent_id, amount=100, reference=reference)`
- Assert result contains "tx_id" and "balance_after" keys
- Assert result["balance_after"] is an integer

#### Test 4: test_credit_idempotency
"""Crediting with the same reference and amount is idempotent."""
- Use funded_feeder and platform fixtures
- Generate a unique reference
- Credit 50 coins with that reference
- Note the tx_id from the first call
- Credit 50 coins with the same reference again
- Assert second call returns the same tx_id (idempotent replay)

#### Test 5: test_credit_requires_positive_amount
"""Crediting with zero or negative amount fails."""
- Use funded_feeder and platform fixtures
- Try to credit 0 coins — expect httpx.HTTPStatusError with status 400
- Try to credit -10 coins — expect httpx.HTTPStatusError with status 400

#### Test 6: test_feeder_balance_reflects_credits
"""After crediting, the feeder's balance increases by the credited amount."""
- Use funded_feeder and platform fixtures
- Get initial balance: `before = await funded_feeder.get_balance()`
- Generate a unique reference
- Credit 200 coins
- Get new balance: `after = await funded_feeder.get_balance()`
- Assert after["balance"] == before["balance"] + 200

#### Test 7: test_funding_summary_fields
"""The credit response contains tx_id and balance_after for summary output."""
- Use funded_feeder and platform fixtures
- Generate a unique reference
- Credit some amount
- Assert response has keys: "tx_id", "balance_after"
- Assert "tx_id" starts with "tx-"
- Assert "balance_after" >= the credited amount

#### Test 8: test_credit_without_account_fails
"""Crediting a non-existent account fails with 404."""
- Use platform fixture
- Try to credit a fake account_id like "a-00000000-0000-0000-0000-000000000000"
- Expect httpx.HTTPStatusError with status 404

#### Test 9: test_feeder_agent_uses_ed25519_signing
"""The feeder agent's JWS tokens use Ed25519 signing."""
- Use feeder_agent fixture
- Create a JWS token: `token = feeder_agent._sign_jws({"action": "test", "data": "hello"})`
- Split the token on "." — should have 3 parts (header.payload.signature)
- Decode the header (base64url decode the first part, parse JSON)
- Assert header["alg"] == "EdDSA"

#### Test 10: test_fund_feeder_full_workflow
"""End-to-end: register feeder, create account, fund, verify balance."""
- Create fresh factory from agents/config.yaml
- Create feeder agent, register it
- Create platform agent, register it
- Platform creates account for feeder (handle 409)
- Platform credits feeder with 1000 coins using reference "fund_feeder_e2e_test"
  - Handle the case where this reference was already used (idempotent replay or unique ref)
- Feeder checks balance
- Assert balance >= 1000
- Print summary to stdout: agent_id, funded amount, balance (mimics what the script will do)
- Close both agents

### Imports needed
```python
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from base_agent.agent import BaseAgent
from base_agent.factory import AgentFactory
from base_agent.platform import PlatformAgent
```

=== PHASE 2: Verify Tests Run ===

From the agents/ directory, run:
```bash
cd agents && uv run pytest tests/e2e/test_fund_feeder.py -v --tb=short 2>&1 | head -80
```

The tests will only pass if all 5 services (identity, bank, task-board, reputation, court) are running.
If services are not running, the tests will be skipped with a clear error message (the conftest.py
fixtures check service health on session start).

If services ARE running, all 10 tests should PASS.
If services are NOT running, report which services are missing and stop.

Do NOT try to start services yourself. If they are not running, just note it and stop.

=== PHASE 3: Verify CI Compliance ===

From the agents/ directory, run:
```bash
cd agents && uv run ruff check tests/e2e/test_fund_feeder.py && uv run ruff format --check tests/e2e/test_fund_feeder.py
```

Fix any formatting or linting errors. Common issues:
- Import ordering (ruff will flag this)
- Line length > 100 characters
- Unused imports
- Missing type annotations on fixtures (not required for test functions but required for fixture return types if strict)

After fixing, re-run the check to confirm compliance.

Then commit with message:
```
test: add e2e acceptance tests for fund-feeder workflow

Tests verify: feeder registration idempotency, account creation,
platform credit operations, credit idempotency, balance tracking,
Ed25519 JWS signing, and full funding workflow.

These tests define the behavioral specification for tools/fund-feeder.sh
which will be implemented in a follow-up ticket (agent-economy-96r).
```

Do NOT push to remote. Only commit locally.
