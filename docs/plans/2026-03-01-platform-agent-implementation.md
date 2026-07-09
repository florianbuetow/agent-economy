# Platform Agent & AgentFactory Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create an AgentFactory that returns a PlatformAgent with privileged banking operations and local JWS verification, so any service can instantiate it without knowing key paths.

**Architecture:** AgentFactory wraps `load_agent_config` and returns the right agent class. PlatformAgent extends BaseAgent with privileged methods (create_account, credit_account, release_escrow, split_escrow) and local JWS verification. A `verify_jws` function in `signing.py` enables cryptographic verification without calling the Identity service.

**Tech Stack:** Python 3.12, cryptography (Ed25519), httpx, pytest

**Design doc:** `docs/plans/2026-03-01-platform-agent-design.md`

**Working directory:** `agents/` (all commands run from here)

**Key existing files to read before starting:**
- `agents/src/base_agent/agent.py` — BaseAgent class (what PlatformAgent subclasses)
- `agents/src/base_agent/config.py` — `load_agent_config` and `AgentConfig` (what factory wraps)
- `agents/src/base_agent/signing.py` — Ed25519 key ops and JWS creation (adding verify here)
- `agents/src/base_agent/mixins/bank.py` — BankMixin (regular agent bank methods, for reference)
- `agents/tests/unit/conftest.py` — `sample_config` fixture
- `agents/tests/unit/test_bank_mixin.py` — test patterns to follow

---

### Task 1: Add local JWS verification to signing.py

**Files:**
- Modify: `agents/src/base_agent/signing.py`
- Create: `agents/tests/unit/test_verify_jws.py`

**Step 1: Write the failing tests**

Create `agents/tests/unit/test_verify_jws.py`:

```python
"""Unit tests for local JWS verification."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.signing import create_jws, verify_jws


@pytest.mark.unit
class TestVerifyJws:
    """Tests for verify_jws."""

    def test_verify_valid_token(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        payload = {"action": "test", "value": 42}
        token = create_jws(payload, private_key, kid="a-123")

        result = verify_jws(token, public_key)

        assert result["action"] == "test"
        assert result["value"] == 42

    def test_verify_rejects_wrong_key(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        wrong_key = Ed25519PrivateKey.generate().public_key()
        token = create_jws({"action": "test"}, private_key)

        with pytest.raises(Exception):
            verify_jws(token, wrong_key)

    def test_verify_rejects_tampered_payload(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        token = create_jws({"action": "test"}, private_key)

        parts = token.split(".")
        parts[1] = parts[1] + "x"
        tampered = ".".join(parts)

        with pytest.raises(Exception):
            verify_jws(tampered, public_key)

    def test_verify_rejects_malformed_token(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        with pytest.raises(ValueError, match="Invalid JWS"):
            verify_jws("not.a.valid.token.here", public_key)

        with pytest.raises(ValueError, match="Invalid JWS"):
            verify_jws("onlyonepart", public_key)
```

**Step 2: Run tests to verify they fail**

Run: `cd agents && uv run pytest tests/unit/test_verify_jws.py -v`
Expected: FAIL with "cannot import name 'verify_jws'"

**Step 3: Write minimal implementation**

Add to `agents/src/base_agent/signing.py`:

A `_b64url_decode` helper (inverse of `_b64url_encode`):

```python
def _b64url_decode(data: str) -> bytes:
    """Base64url-decode a string, adding padding as needed."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)
```

And the `verify_jws` function:

```python
def verify_jws(
    token: str,
    public_key: Ed25519PublicKey,
) -> dict[str, object]:
    """Verify a compact JWS token and return the decoded payload.

    Verifies the Ed25519 signature against the provided public key.
    Does NOT call any external service — purely local cryptographic verification.

    Args:
        token: Compact JWS string (header.payload.signature).
        public_key: Ed25519 public key to verify against.

    Returns:
        Decoded payload as a dictionary.

    Raises:
        ValueError: If the token format is invalid.
        cryptography.exceptions.InvalidSignature: If the signature is invalid.
    """
    parts = token.split(".")
    if len(parts) != 3:
        msg = "Invalid JWS format: expected 3 dot-separated parts"
        raise ValueError(msg)

    header_b64, payload_b64, signature_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = _b64url_decode(signature_b64)

    public_key.verify(signature, signing_input)

    payload_bytes = _b64url_decode(payload_b64)
    return json.loads(payload_bytes)
```

**Step 4: Run tests to verify they pass**

Run: `cd agents && uv run pytest tests/unit/test_verify_jws.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
cd agents && git add src/base_agent/signing.py tests/unit/test_verify_jws.py
git commit -m "feat(agents): add local JWS verification to signing module"
```

---

### Task 2: Add platform entry to roster.yaml

**Files:**
- Modify: `agents/roster.yaml`

**Step 1: Write the failing test**

Create `agents/tests/unit/test_roster_platform.py`:

```python
"""Unit tests for platform agent roster entry."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.mark.unit
class TestRosterPlatformEntry:
    """Tests for platform agent in roster."""

    def test_roster_has_platform_entry(self) -> None:
        roster_path = Path(__file__).resolve().parents[2] / "roster.yaml"
        roster = yaml.safe_load(roster_path.read_text())

        assert "platform" in roster["agents"]
        assert roster["agents"]["platform"]["name"] == "Platform"
        assert roster["agents"]["platform"]["type"] == "platform"
```

**Step 2: Run test to verify it fails**

Run: `cd agents && uv run pytest tests/unit/test_roster_platform.py -v`
Expected: FAIL with `KeyError: 'platform'`

**Step 3: Update roster.yaml**

Replace `agents/roster.yaml` with:

```yaml
# Agent Roster — maps handles to names and types
# Keys are stored at: data/keys/{handle}.key and data/keys/{handle}.pub

agents:
  platform:
    name: "Platform"
    type: "platform"
  alice:
    name: "Alice"
    type: "worker"
  bob:
    name: "Bob"
    type: "worker"
```

**Step 4: Run test to verify it passes**

Run: `cd agents && uv run pytest tests/unit/test_roster_platform.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd agents && git add roster.yaml tests/unit/test_roster_platform.py
git commit -m "feat(agents): add platform agent to roster"
```

---

### Task 3: Create PlatformAgent class

**Files:**
- Create: `agents/src/base_agent/platform.py`
- Create: `agents/tests/unit/test_platform_agent.py`

**Step 1: Write the failing tests**

Create `agents/tests/unit/test_platform_agent.py`:

```python
"""Unit tests for PlatformAgent privileged operations."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.config import AgentConfig
from base_agent.platform import PlatformAgent
from base_agent.signing import create_jws

if TYPE_CHECKING:
    pass


@pytest.fixture()
def platform_config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return AgentConfig(
        name="Platform",
        private_key=private_key,
        public_key=public_key,
        identity_url="http://localhost:8001",
        bank_url="http://localhost:8002",
        task_board_url="http://localhost:8003",
        reputation_url="http://localhost:8004",
        court_url="http://localhost:8005",
    )


@pytest.mark.unit
class TestPlatformAgentInit:
    """Tests for PlatformAgent construction."""

    def test_creates_platform_agent(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        assert agent.name == "Platform"
        assert agent.agent_id is None

    def test_repr(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        assert "PlatformAgent" in repr(agent)


@pytest.mark.unit
class TestCreateAccount:
    """Tests for create_account."""

    async def test_create_account_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        response = {"account_id": "a-alice", "balance": 100, "created_at": "2026-01-01T00:00:00Z"}
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.create_account(agent_id="a-alice", initial_balance=100)

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {"action": "create_account", "agent_id": "a-alice", "initial_balance": 100}
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/accounts",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestCreditAccount:
    """Tests for credit_account."""

    async def test_credit_account_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        response = {"tx_id": "tx-1", "balance_after": 200}
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.credit_account(
            account_id="a-alice", amount=100, reference="salary"
        )

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "credit",
                "account_id": "a-alice",
                "amount": 100,
                "reference": "salary",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/accounts/a-alice/credit",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestReleaseEscrow:
    """Tests for release_escrow."""

    async def test_release_escrow_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        response = {"escrow_id": "esc-1", "amount": 50, "status": "released"}
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.release_escrow(
            escrow_id="esc-1", recipient_account_id="a-alice"
        )

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "escrow_release",
                "escrow_id": "esc-1",
                "recipient_account_id": "a-alice",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/escrow/esc-1/release",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestSplitEscrow:
    """Tests for split_escrow."""

    async def test_split_escrow_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        response = {"escrow_id": "esc-1", "worker_amount": 70, "poster_amount": 30}
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.split_escrow(
            escrow_id="esc-1",
            worker_account_id="a-alice",
            poster_account_id="a-bob",
            worker_pct=70,
        )

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "escrow_split",
                "escrow_id": "esc-1",
                "worker_account_id": "a-alice",
                "poster_account_id": "a-bob",
                "worker_pct": 70,
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/escrow/esc-1/split",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestVerifyPlatformJws:
    """Tests for verify_platform_jws."""

    def test_verify_valid_platform_token(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"

        token = agent._sign_jws({"action": "create_account", "agent_id": "a-alice"})
        result = agent.verify_platform_jws(token)

        assert result["action"] == "create_account"
        assert result["agent_id"] == "a-alice"

    def test_verify_rejects_non_platform_token(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)

        other_key = Ed25519PrivateKey.generate()
        token = create_jws({"action": "create_account"}, other_key, kid="a-imposter")

        with pytest.raises(Exception):
            agent.verify_platform_jws(token)
```

**Step 2: Run tests to verify they fail**

Run: `cd agents && uv run pytest tests/unit/test_platform_agent.py -v`
Expected: FAIL with "No module named 'base_agent.platform'"

**Step 3: Write minimal implementation**

Create `agents/src/base_agent/platform.py`:

```python
"""PlatformAgent — privileged agent for platform banking operations."""

from __future__ import annotations

from typing import Any

from base_agent.agent import BaseAgent
from base_agent.signing import verify_jws


class PlatformAgent(BaseAgent):
    """Privileged platform agent for system operations.

    Extends BaseAgent with methods for operations that only the platform
    agent is authorized to perform: creating accounts, crediting funds,
    and managing escrow releases/splits.

    Also provides local JWS verification so services can validate incoming
    platform-signed requests without calling the Identity service.
    """

    async def create_account(self, agent_id: str, initial_balance: int) -> dict[str, Any]:
        """Create an account for an agent in the Central Bank.

        Args:
            agent_id: The agent to create an account for.
            initial_balance: Starting balance for the account.

        Returns:
            Account creation response from Central Bank.
        """
        url = f"{self.config.bank_url}/accounts"
        token = self._sign_jws(
            {"action": "create_account", "agent_id": agent_id, "initial_balance": initial_balance}
        )
        return await self._request("POST", url, json={"token": token})

    async def credit_account(
        self, account_id: str, amount: int, reference: str
    ) -> dict[str, Any]:
        """Credit funds to an account.

        Args:
            account_id: The account to credit.
            amount: Amount to credit (positive integer).
            reference: Reference string for the transaction.

        Returns:
            Credit response from Central Bank.
        """
        url = f"{self.config.bank_url}/accounts/{account_id}/credit"
        token = self._sign_jws(
            {
                "action": "credit",
                "account_id": account_id,
                "amount": amount,
                "reference": reference,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def release_escrow(
        self, escrow_id: str, recipient_account_id: str
    ) -> dict[str, Any]:
        """Release escrowed funds to recipient.

        Args:
            escrow_id: The escrow to release.
            recipient_account_id: Account to receive the funds.

        Returns:
            Release response from Central Bank.
        """
        url = f"{self.config.bank_url}/escrow/{escrow_id}/release"
        token = self._sign_jws(
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def split_escrow(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_pct: int,
    ) -> dict[str, Any]:
        """Split escrowed funds between worker and poster.

        Args:
            escrow_id: The escrow to split.
            worker_account_id: Worker's account.
            poster_account_id: Poster's account.
            worker_pct: Percentage (0-100) going to the worker.

        Returns:
            Split response from Central Bank.
        """
        url = f"{self.config.bank_url}/escrow/{escrow_id}/split"
        token = self._sign_jws(
            {
                "action": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_pct": worker_pct,
            }
        )
        return await self._request("POST", url, json={"token": token})

    def verify_platform_jws(self, token: str) -> dict[str, object]:
        """Verify a JWS token was signed by this platform agent.

        Uses local cryptographic verification against this agent's public key.
        No Identity service round-trip needed.

        Args:
            token: Compact JWS string to verify.

        Returns:
            Decoded payload as a dictionary.

        Raises:
            cryptography.exceptions.InvalidSignature: If the signature is invalid.
            ValueError: If the token format is invalid.
        """
        return verify_jws(token, self._public_key)

    def __repr__(self) -> str:
        registered = f", agent_id={self.agent_id!r}" if self.agent_id else ""
        return f"PlatformAgent(name={self.name!r}{registered})"
```

**Step 4: Run tests to verify they pass**

Run: `cd agents && uv run pytest tests/unit/test_platform_agent.py -v`
Expected: All 8 tests PASS

**Step 5: Commit**

```bash
cd agents && git add src/base_agent/platform.py tests/unit/test_platform_agent.py
git commit -m "feat(agents): add PlatformAgent with privileged banking operations"
```

---

### Task 4: Create AgentFactory class

**Files:**
- Create: `agents/src/base_agent/factory.py`
- Create: `agents/tests/unit/test_factory.py`

**Step 1: Write the failing tests**

Create `agents/tests/unit/test_factory.py`:

```python
"""Unit tests for AgentFactory."""

from __future__ import annotations

from pathlib import Path

import pytest

from base_agent.agent import BaseAgent
from base_agent.factory import AgentFactory
from base_agent.platform import PlatformAgent


@pytest.fixture()
def config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config.yaml"


@pytest.mark.unit
class TestAgentFactory:
    """Tests for AgentFactory."""

    def test_create_agent_returns_base_agent(self, config_path: Path, tmp_path: Path) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        agent = factory.create_agent("alice")
        assert isinstance(agent, BaseAgent)
        assert agent.name == "Alice"

    def test_create_agent_not_platform_agent(self, config_path: Path, tmp_path: Path) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        agent = factory.create_agent("alice")
        assert not isinstance(agent, PlatformAgent)

    def test_platform_agent_returns_platform_agent(
        self, config_path: Path, tmp_path: Path
    ) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        agent = factory.platform_agent()
        assert isinstance(agent, PlatformAgent)
        assert agent.name == "Platform"

    def test_platform_agent_has_privileged_methods(
        self, config_path: Path, tmp_path: Path
    ) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        agent = factory.platform_agent()
        assert hasattr(agent, "create_account")
        assert hasattr(agent, "credit_account")
        assert hasattr(agent, "release_escrow")
        assert hasattr(agent, "split_escrow")
        assert hasattr(agent, "verify_platform_jws")

    def test_unknown_handle_raises(self, config_path: Path, tmp_path: Path) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        with pytest.raises(KeyError):
            factory.create_agent("nonexistent")

    def test_same_handle_same_keys(self, config_path: Path, tmp_path: Path) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        a1 = factory.create_agent("alice")
        a2 = factory.create_agent("alice")
        assert a1.get_public_key_b64() == a2.get_public_key_b64()
```

**Step 2: Run tests to verify they fail**

Run: `cd agents && uv run pytest tests/unit/test_factory.py -v`
Expected: FAIL with "No module named 'base_agent.factory'"

**Step 3: Write minimal implementation**

Create `agents/src/base_agent/factory.py`:

```python
"""AgentFactory — creates agents with keys loaded transparently."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from service_commons.config import get_config_path as resolve_config_path

from base_agent.agent import BaseAgent
from base_agent.config import AgentConfig
from base_agent.platform import PlatformAgent
from base_agent.signing import generate_keypair, load_private_key, load_public_key

if TYPE_CHECKING:
    pass


class AgentFactory:
    """Factory that creates agents with their keys loaded transparently.

    The factory knows where keys and roster are stored. Callers never
    deal with key paths — they just ask for an agent by handle.

    Args:
        config_path: Path to the agents config.yaml. If None, resolved
            via AGENT_CONFIG_PATH env var or default location.
        keys_dir: Override for the keys directory. If None, resolved
            from config.yaml's data.keys_dir.
    """

    def __init__(
        self,
        config_path: Path | None = None,
        keys_dir: Path | None = None,
    ) -> None:
        if config_path is None:
            config_path = resolve_config_path(
                env_var_name="AGENT_CONFIG_PATH",
                default_filename="config.yaml",
            )

        raw = yaml.safe_load(config_path.read_text())
        if not isinstance(raw, dict):
            msg = f"Invalid config file: {config_path}"
            raise ValueError(msg)

        self._config_path = config_path

        # Resolve keys directory
        if keys_dir is not None:
            self._keys_dir = keys_dir.resolve()
        else:
            cfg_keys_dir = Path(raw["data"]["keys_dir"])
            if not cfg_keys_dir.is_absolute():
                cfg_keys_dir = config_path.parent / cfg_keys_dir
            self._keys_dir = cfg_keys_dir.resolve()

        # Load roster
        roster_path = Path(raw["data"]["roster_path"])
        if not roster_path.is_absolute():
            roster_path = config_path.parent / roster_path
        roster_raw = yaml.safe_load(roster_path.read_text())
        if not isinstance(roster_raw, dict):
            msg = f"Invalid roster file: {roster_path}"
            raise ValueError(msg)
        self._roster: dict[str, dict[str, str]] = roster_raw["agents"]

        # Store service URLs
        self._identity_url: str = raw["platform"]["identity_url"]
        self._bank_url: str = raw["platform"]["bank_url"]
        self._task_board_url: str = raw["platform"]["task_board_url"]
        self._reputation_url: str = raw["platform"]["reputation_url"]
        self._court_url: str = raw["platform"]["court_url"]

    def _load_config(self, handle: str) -> AgentConfig:
        """Load an AgentConfig for the given roster handle."""
        if handle not in self._roster:
            msg = f"Agent '{handle}' not found in roster"
            raise KeyError(msg)

        entry = self._roster[handle]

        private_path = self._keys_dir / f"{handle}.key"
        public_path = self._keys_dir / f"{handle}.pub"
        if private_path.exists() and public_path.exists():
            private_key = load_private_key(private_path)
            public_key = load_public_key(public_path)
        else:
            private_key, public_key = generate_keypair(handle, self._keys_dir)

        return AgentConfig(
            name=entry["name"],
            private_key=private_key,
            public_key=public_key,
            identity_url=self._identity_url,
            bank_url=self._bank_url,
            task_board_url=self._task_board_url,
            reputation_url=self._reputation_url,
            court_url=self._court_url,
        )

    def create_agent(self, handle: str) -> BaseAgent:
        """Create a regular agent by roster handle.

        Args:
            handle: Agent handle from roster.yaml (e.g., "alice", "bob").

        Returns:
            A BaseAgent initialized with the agent's keys.

        Raises:
            KeyError: If the handle is not in the roster.
        """
        config = self._load_config(handle)
        return BaseAgent(config)

    def platform_agent(self) -> PlatformAgent:
        """Create the platform agent with privileged operations.

        Returns:
            A PlatformAgent initialized with the platform keypair.

        Raises:
            KeyError: If "platform" is not in the roster.
        """
        config = self._load_config("platform")
        return PlatformAgent(config)
```

**Step 4: Run tests to verify they pass**

Run: `cd agents && uv run pytest tests/unit/test_factory.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
cd agents && git add src/base_agent/factory.py tests/unit/test_factory.py
git commit -m "feat(agents): add AgentFactory for transparent agent creation"
```

---

### Task 5: Update package exports

**Files:**
- Modify: `agents/src/base_agent/__init__.py`

**Step 1: Write the failing test**

Create `agents/tests/unit/test_exports.py`:

```python
"""Unit tests for base_agent package exports."""

from __future__ import annotations

import pytest


@pytest.mark.unit
class TestPackageExports:
    """Tests for top-level imports."""

    def test_import_agent_factory(self) -> None:
        from base_agent import AgentFactory

        assert AgentFactory is not None

    def test_import_platform_agent(self) -> None:
        from base_agent import PlatformAgent

        assert PlatformAgent is not None

    def test_import_base_agent(self) -> None:
        from base_agent import BaseAgent

        assert BaseAgent is not None
```

**Step 2: Run tests to verify they fail**

Run: `cd agents && uv run pytest tests/unit/test_exports.py -v`
Expected: FAIL with "cannot import name 'AgentFactory' from 'base_agent'"

**Step 3: Update exports**

Replace `agents/src/base_agent/__init__.py` with:

```python
"""Base Agent — programmable client for the Agent Task Economy platform."""

from base_agent.agent import BaseAgent
from base_agent.factory import AgentFactory
from base_agent.platform import PlatformAgent

__version__ = "0.1.0"

__all__ = ["BaseAgent", "AgentFactory", "PlatformAgent"]
```

**Step 4: Run tests to verify they pass**

Run: `cd agents && uv run pytest tests/unit/test_exports.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
cd agents && git add src/base_agent/__init__.py tests/unit/test_exports.py
git commit -m "feat(agents): export AgentFactory and PlatformAgent from package"
```

---

### Task 6: Run full CI

**Step 1: Run all tests**

Run: `cd agents && uv run pytest tests/ -v -m "not e2e"`
Expected: All tests PASS (existing + new)

**Step 2: Run full CI**

Run: `cd agents && just ci`
Expected: All checks pass (formatting, linting, type checking, security, spelling, tests)

**Step 3: Fix any CI issues**

If formatting fails: `cd agents && just code-format`, then re-run `just ci`.
If type checking fails: fix type annotations and re-run.

**Step 4: Commit fixes if needed**

```bash
cd agents && git add -A
git commit -m "fix(agents): resolve CI issues"
```
