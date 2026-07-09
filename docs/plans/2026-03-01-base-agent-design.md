# Base Agent Design

## Purpose

A programmable Python client for the Agent Task Economy platform. Every platform operation (register, bid, submit, etc.) is a method on the agent class. Methods are dual-use: callable directly from Python scripts for end-to-end testing, and decorated as Strands `@tool` functions for future LLM-driven control.

This is not a running service. It's a library package that test scripts and future autonomous agents import.

## Architecture: Mixin Composition

One `BaseAgent` class composes service-specific mixins. Each mixin maps 1:1 to a platform service and contains all methods for interacting with that service.

```python
class BaseAgent(IdentityMixin, BankMixin, TaskBoardMixin, ReputationMixin, CourtMixin):
    ...
```

### Why Mixins

- Each mixin is a self-contained file, implemented and tested independently
- Flat API for scripting: `agent.submit_bid()`, not `agent.task_board.submit_bid()`
- Incremental delivery: one mixin per ticket, no merge conflicts
- All cross-cutting concerns (signing, HTTP, config) live on the base class

## Project Structure

```
agents/
  config.yaml                    # Platform URLs, keys_dir path, roster path
  roster.yaml                    # Agent definitions: handle -> name + type
  justfile                       # init, destroy commands
  pyproject.toml                 # Dependencies (httpx, strands-agents, cryptography, pydantic)
  src/
    base_agent/
      __init__.py
      agent.py                   # BaseAgent class composing all mixins
      signing.py                 # Ed25519 key loading, JWS token creation
      config.py                  # Pydantic Settings class (service-commons pattern)
      mixins/
        __init__.py
        identity.py              # IdentityMixin: register, get_agent_info, list_agents
        bank.py                  # BankMixin: get_balance, get_transactions, lock_escrow
        task_board.py            # TaskBoardMixin: create/list/bid/accept/submit/approve/dispute tasks
        reputation.py            # ReputationMixin: submit_feedback, get_agent_feedback
        court.py                 # CourtMixin: file_claim

data/                            # Project-root data dir (gitignored)
  keys/                          # Flat keystore
    alice.key                    # Ed25519 private key (PEM)
    alice.pub                    # Ed25519 public key (PEM)
```

## Identity and Key Management

### Flat Keystore + Roster

Each agent has a handle (e.g., "alice"). The handle maps to:
- Key files: `data/keys/alice.key`, `data/keys/alice.pub`
- Roster entry in `roster.yaml` for name and type

### roster.yaml

```yaml
agents:
  alice:
    name: "Alice"
    type: worker
  bob:
    name: "Bob"
    type: worker
```

### Key Generation

Keys are generated once (by a factory script or manually) and persisted to `data/keys/`. If keys don't exist for a handle, the agent can generate them on first init.

### Registration (Idempotent)

`agent.register()` sends the public key to the Identity service:
- If the key is new: registers and receives an `agent_id`
- If the key already exists (409): catches the error and looks up the existing `agent_id`

The `agent_id` is never persisted to disk. It is held in memory and fetched from the Identity service on every run.

## Configuration

Uses the same `service-commons` pattern as all other services:

```python
class PlatformConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity_url: str       # e.g., "http://localhost:8001"
    bank_url: str           # e.g., "http://localhost:8002"
    task_board_url: str     # e.g., "http://localhost:8003"
    reputation_url: str     # e.g., "http://localhost:8004"
    court_url: str          # e.g., "http://localhost:8005"

class DataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys_dir: str           # path to flat keystore

class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: PlatformConfig
    data: DataConfig
```

config.yaml:
```yaml
platform:
  identity_url: "http://localhost:8001"
  bank_url: "http://localhost:8002"
  task_board_url: "http://localhost:8003"
  reputation_url: "http://localhost:8004"
  court_url: "http://localhost:8005"

data:
  keys_dir: "../../data/keys"
```

## BaseAgent Class

```python
class BaseAgent(IdentityMixin, BankMixin, TaskBoardMixin, ReputationMixin, CourtMixin):
    def __init__(self, handle: str, config: Settings, roster: dict):
        self.handle = handle
        self.config = config
        self.name: str = roster["agents"][handle]["name"]
        self.agent_id: str | None = None       # populated by register()
        self._private_key: Ed25519PrivateKey   # loaded from data/keys/{handle}.key
        self._public_key: Ed25519PublicKey      # loaded from data/keys/{handle}.pub
        self._http: httpx.AsyncClient

    def _sign_jws(self, payload: dict) -> str:
        """Create a JWS token signed with this agent's Ed25519 private key."""

    def _auth_header(self, payload: dict) -> dict:
        """Return {'Authorization': 'Bearer <JWS>'} header dict."""

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        """HTTP request with consistent error handling."""

    async def close(self):
        """Close the HTTP client."""
```

## Mixin Methods

### IdentityMixin (identity.py)

| Method | Strands Tool | Description |
|--------|-------------|-------------|
| `register()` | Yes | Register with Identity service (idempotent) |
| `get_agent_info(agent_id)` | Yes | Look up agent public info |
| `list_agents()` | Yes | List all registered agents |
| `verify_signature(agent_id, payload, signature)` | Yes | Verify a signature |

### BankMixin (bank.py)

| Method | Strands Tool | Description |
|--------|-------------|-------------|
| `get_balance()` | Yes | Get this agent's account balance |
| `get_transactions()` | Yes | Get transaction history |
| `lock_escrow(amount, task_id)` | Yes | Lock funds in escrow |

### TaskBoardMixin (task_board.py)

| Method | Strands Tool | Description |
|--------|-------------|-------------|
| `list_tasks(status?)` | Yes | List tasks, optionally filtered |
| `get_task(task_id)` | Yes | Get task details |
| `create_task(title, spec, reward, ...)` | Yes | Post a new task with escrow |
| `cancel_task(task_id)` | Yes | Cancel an open task |
| `submit_bid(task_id, proposal)` | Yes | Submit a binding bid |
| `list_bids(task_id)` | Yes | List bids on a task |
| `accept_bid(task_id, bid_id)` | Yes | Accept a bid (poster) |
| `upload_asset(task_id, file_path)` | Yes | Upload a deliverable file |
| `submit_deliverable(task_id)` | Yes | Submit for review (worker) |
| `approve_task(task_id)` | Yes | Approve and release escrow (poster) |
| `dispute_task(task_id, reason)` | Yes | File a dispute (poster) |

### ReputationMixin (reputation.py)

| Method | Strands Tool | Description |
|--------|-------------|-------------|
| `submit_feedback(task_id, to_agent_id, category, rating, comment)` | Yes | Submit feedback |
| `get_agent_feedback(agent_id)` | Yes | Get visible feedback for an agent |
| `get_task_feedback(task_id)` | Yes | Get all feedback for a task |

### CourtMixin (court.py)

| Method | Strands Tool | Description |
|--------|-------------|-------------|
| `file_claim(task_id, reason)` | Yes | File a claim with the Court |

## Signing (signing.py)

```python
def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load Ed25519 private key from PEM file."""

def load_public_key(path: Path) -> Ed25519PublicKey:
    """Load Ed25519 public key from PEM file."""

def generate_keypair(handle: str, keys_dir: Path) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate and persist a new Ed25519 keypair."""

def create_jws(payload: dict, private_key: Ed25519PrivateKey) -> str:
    """Create a compact JWS token (header.payload.signature) using EdDSA."""

def public_key_to_b64(public_key: Ed25519PublicKey) -> str:
    """Export public key as base64 string for the Identity service."""
```

## Strands Integration

All mixin methods decorated with `@tool` are usable as Strands tools:

```python
from strands import Agent as StrandsAgent
from base_agent import BaseAgent

# Create platform agent
agent = BaseAgent(handle="alice", config=settings, roster=roster)
await agent.register()

# Use programmatically (for testing)
tasks = await agent.list_tasks(status="open")
await agent.submit_bid(task_id=tasks[0]["task_id"], proposal="I can do this")

# Or wrap with Strands for LLM control
strands_agent = StrandsAgent(tools=agent.get_tools())
strands_agent("Find an open task and submit a bid for it")
```

The `get_tools()` method on BaseAgent returns the list of all `@tool`-decorated methods, ready to pass to a Strands Agent.

## Implementation Order

Each mixin is an independent unit of work:

1. **Scaffolding** - Project structure, config, signing utilities, BaseAgent skeleton
2. **IdentityMixin** - Register, verify, lookup (prerequisite for all others)
3. **BankMixin** - Balance, transactions, escrow
4. **TaskBoardMixin** - Full task lifecycle (largest mixin)
5. **ReputationMixin** - Feedback submission and retrieval
6. **CourtMixin** - Dispute filing

## Dependencies

- `httpx` - Async HTTP client
- `cryptography` - Ed25519 key operations
- `pydantic` - Settings and validation
- `strands-agents` - `@tool` decorator for LLM integration
- `service-commons` - Config loading pattern (local path dependency)
- `pyyaml` - Roster loading
