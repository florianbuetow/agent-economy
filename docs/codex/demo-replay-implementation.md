# Demo Replay System — Complete Implementation Plan for Codex Agent

## CRITICAL INSTRUCTIONS — READ BEFORE STARTING

1. **DO NOT USE GIT.** There is no git in this project. No `git add`, `git commit`, `git push`, or any git commands whatsoever.
2. **Use `uv run` for ALL Python execution.** Never use `python`, `python3`, or `pip install`.
3. **Do NOT modify existing test files.** If you need new tests, create new files.
4. **Do NOT modify files in `libs/service-commons/`.** Changes there affect all services.
5. **Follow the exact file paths and code provided.** Do not improvise or deviate.
6. **After each tier, run the verification commands exactly as specified.**
7. **Work from the project root directory:** `/Users/flo/Developer/github/agent-economy`

## Project Context

You are building a demo replay engine for the Agent Task Economy platform. This engine reads YAML scenario files and executes them against the live service stack (Identity, Central Bank, Task Board, etc.) to produce real-time SSE events visible in the UI.

**Key reference files (READ these first):**
- `AGENTS.md` — Project conventions, architecture, code style rules
- `docs/plans/2026-03-02-demo-replay-design.md` — Full design document
- `docs/plans/2026-03-02-demo-replay-plan.md` — Implementation plan with code
- `agents/src/base_agent/signing.py` — Ed25519 signing code to copy from
- `agents/src/base_agent/mixins/task_board.py` — Task Board API contracts
- `agents/src/base_agent/mixins/bank.py` — Bank API contracts
- `agents/src/base_agent/mixins/identity.py` — Identity API contracts
- `agents/src/base_agent/platform.py` — Platform agent privileged operations

---

## TIER 1: Dependencies and Package Configuration

### What to do
Edit `tools/pyproject.toml` to add the `cryptography` dependency and register the `demo_replay` package.

### File: `tools/pyproject.toml`

Open this file. Make these TWO changes:

**Change 1:** Add `"cryptography>=44.0.0",` to the `dependencies` list. The result should look like:

```toml
dependencies = [
    "httpx>=0.28.0",
    "openai>=1.0.0",
    "pydantic>=2.10.0",
    "pyyaml>=6.0.0",
    "rich>=13.0.0",
    "cryptography>=44.0.0",
]
```

**Change 2:** Add `"src/demo_replay"` to the hatch wheel packages. The result should look like:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/math_task_factory", "src/demo_replay"]
```

### Verification for Tier 1

Run these commands in order:

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv sync --all-extras
```

Expected: Clean install, no errors. The cryptography package should be installed.

Then verify:

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv run python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; print('cryptography OK')"
```

Expected output: `cryptography OK`

---

## TIER 2: Create wallet.py (Ed25519 + JWS Signing)

### What to do
Create the `demo_replay` package with `__init__.py` and `wallet.py`.

### File 1: `tools/src/demo_replay/__init__.py`

Create this file with exactly this content:

```python
"""Demo replay engine — execute YAML scenarios against live services."""
```

### File 2: `tools/src/demo_replay/wallet.py`

Create this file with exactly this content:

```python
"""Ed25519 key management and JWS token creation for demo agents.

Extracted from agents/src/base_agent/signing.py to avoid pulling in
heavy agent dependencies (strands-agents, openai).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode bytes without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a fresh Ed25519 keypair (in-memory only, no disk persistence)."""
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def _public_key_b64(public_key: Ed25519PublicKey) -> str:
    """Export public key as base64 (standard, not URL-safe) of raw 32 bytes."""
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def create_jws(
    payload: dict[str, object],
    private_key: Ed25519PrivateKey,
    kid: str,
) -> str:
    """Create a compact JWS token: base64url(header).base64url(payload).base64url(sig)."""
    header: dict[str, str] = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input)
    sig_b64 = _b64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


@dataclass
class DemoAgent:
    """In-memory agent with keypair, used during demo replay."""

    handle: str
    name: str
    private_key: Ed25519PrivateKey = field(repr=False)
    public_key: Ed25519PublicKey = field(repr=False)
    agent_id: str | None = None

    @classmethod
    def create(cls, handle: str, name: str) -> DemoAgent:
        """Create a new demo agent with a fresh keypair."""
        private_key, public_key = _generate_keypair()
        return cls(
            handle=handle,
            name=name,
            private_key=private_key,
            public_key=public_key,
        )

    def public_key_string(self) -> str:
        """Return 'ed25519:<base64>' format expected by Identity service."""
        return f"ed25519:{_public_key_b64(self.public_key)}"

    def sign_jws(self, payload: dict[str, object]) -> str:
        """Sign a payload as a compact JWS token."""
        if self.agent_id is None:
            msg = f"Agent '{self.handle}' must be registered before signing"
            raise RuntimeError(msg)
        return create_jws(payload, self.private_key, kid=self.agent_id)

    def auth_header(self, payload: dict[str, object]) -> dict[str, str]:
        """Create an Authorization: Bearer header with a signed JWS."""
        return {"Authorization": f"Bearer {self.sign_jws(payload)}"}
```

### Verification for Tier 2

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv run python -c "
from demo_replay.wallet import DemoAgent
a = DemoAgent.create('test', 'Test Agent')
print(f'public_key: {a.public_key_string()}')
print(f'handle: {a.handle}')
print(f'name: {a.name}')
print('wallet.py OK')
"
```

Expected: Prints the public key string, handle, name, and `wallet.py OK`.

---

## TIER 3: Create clients.py (Async HTTP Wrappers)

### What to do
Create `tools/src/demo_replay/clients.py` with async httpx wrappers for each service endpoint.

### File: `tools/src/demo_replay/clients.py`

Create this file with exactly this content:

```python
"""Async HTTP clients for each platform service.

Thin wrappers matching the API contracts in agents/src/base_agent/mixins/.
Each function takes a DemoAgent and the relevant parameters, signs the
request, and returns the parsed JSON response.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from demo_replay.wallet import DemoAgent

# Default service URLs (same as agents/config.yaml)
IDENTITY_URL = "http://localhost:8001"
BANK_URL = "http://localhost:8002"
TASK_BOARD_URL = "http://localhost:8003"


async def register_agent(
    client: httpx.AsyncClient,
    agent: DemoAgent,
    identity_url: str = IDENTITY_URL,
) -> dict[str, Any]:
    """Register an agent with the Identity service. Sets agent.agent_id on success."""
    url = f"{identity_url}/agents/register"
    payload = {"name": agent.name, "public_key": agent.public_key_string()}
    resp = await client.post(url, json=payload)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    agent.agent_id = data["agent_id"]
    return data


async def create_account(
    client: httpx.AsyncClient,
    platform: DemoAgent,
    agent_id: str,
    bank_url: str = BANK_URL,
) -> dict[str, Any]:
    """Create a bank account for an agent (platform-signed)."""
    url = f"{bank_url}/accounts"
    token = platform.sign_jws(
        {"action": "create_account", "agent_id": agent_id, "initial_balance": 0}
    )
    resp = await client.post(url, json={"token": token})
    if resp.status_code == 409:
        return {"status": "already_exists"}
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def credit_account(
    client: httpx.AsyncClient,
    platform: DemoAgent,
    account_id: str,
    amount: int,
    bank_url: str = BANK_URL,
) -> dict[str, Any]:
    """Credit funds to an agent's account (platform-signed)."""
    url = f"{bank_url}/accounts/{account_id}/credit"
    reference = f"demo_fund_{uuid.uuid4().hex[:8]}"
    token = platform.sign_jws(
        {
            "action": "credit",
            "account_id": account_id,
            "amount": amount,
            "reference": reference,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def post_task(
    client: httpx.AsyncClient,
    poster: DemoAgent,
    title: str,
    spec: str,
    reward: int,
    task_board_url: str = TASK_BOARD_URL,
    bidding_deadline_seconds: int = 3600,
    execution_deadline_seconds: int = 7200,
    review_deadline_seconds: int = 3600,
) -> dict[str, Any]:
    """Post a new task to the Task Board. Returns response including task_id."""
    url = f"{task_board_url}/tasks"
    task_id = f"t-{uuid.uuid4()}"
    task_token = poster.sign_jws(
        {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": poster.agent_id,
            "title": title,
            "spec": spec,
            "reward": reward,
            "bidding_deadline_seconds": bidding_deadline_seconds,
            "execution_deadline_seconds": execution_deadline_seconds,
            "review_deadline_seconds": review_deadline_seconds,
        }
    )
    escrow_token = poster.sign_jws(
        {
            "action": "escrow_lock",
            "task_id": task_id,
            "amount": reward,
            "agent_id": poster.agent_id,
        }
    )
    resp = await client.post(
        url, json={"task_token": task_token, "escrow_token": escrow_token}
    )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def submit_bid(
    client: httpx.AsyncClient,
    bidder: DemoAgent,
    task_id: str,
    amount: int,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Submit a bid on a task."""
    url = f"{task_board_url}/tasks/{task_id}/bids"
    token = bidder.sign_jws(
        {
            "action": "submit_bid",
            "task_id": task_id,
            "bidder_id": bidder.agent_id,
            "amount": amount,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def list_bids(
    client: httpx.AsyncClient,
    poster: DemoAgent,
    task_id: str,
    task_board_url: str = TASK_BOARD_URL,
) -> list[dict[str, Any]]:
    """List bids for a task (poster-signed auth header)."""
    url = f"{task_board_url}/tasks/{task_id}/bids"
    headers = poster.auth_header(
        {
            "action": "list_bids",
            "task_id": task_id,
            "poster_id": poster.agent_id,
        }
    )
    resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    bids: list[dict[str, Any]] = data["bids"]
    return bids


async def accept_bid(
    client: httpx.AsyncClient,
    poster: DemoAgent,
    task_id: str,
    bid_id: str,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Accept a bid on a task."""
    url = f"{task_board_url}/tasks/{task_id}/bids/{bid_id}/accept"
    token = poster.sign_jws(
        {
            "action": "accept_bid",
            "task_id": task_id,
            "bid_id": bid_id,
            "poster_id": poster.agent_id,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def upload_asset(
    client: httpx.AsyncClient,
    worker: DemoAgent,
    task_id: str,
    filename: str,
    content: bytes,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Upload a file asset for a task."""
    url = f"{task_board_url}/tasks/{task_id}/assets"
    headers = worker.auth_header({"action": "upload_asset", "task_id": task_id})
    resp = await client.post(
        url, headers=headers, files={"file": (filename, content)}
    )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def submit_deliverable(
    client: httpx.AsyncClient,
    worker: DemoAgent,
    task_id: str,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Submit deliverables for review."""
    url = f"{task_board_url}/tasks/{task_id}/submit"
    token = worker.sign_jws(
        {
            "action": "submit_deliverable",
            "task_id": task_id,
            "worker_id": worker.agent_id,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def approve_task(
    client: httpx.AsyncClient,
    poster: DemoAgent,
    task_id: str,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Approve a submitted task."""
    url = f"{task_board_url}/tasks/{task_id}/approve"
    token = poster.sign_jws(
        {
            "action": "approve_task",
            "task_id": task_id,
            "poster_id": poster.agent_id,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def dispute_task(
    client: httpx.AsyncClient,
    poster: DemoAgent,
    task_id: str,
    reason: str,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Dispute a submitted task."""
    url = f"{task_board_url}/tasks/{task_id}/dispute"
    token = poster.sign_jws(
        {
            "action": "dispute_task",
            "task_id": task_id,
            "poster_id": poster.agent_id,
            "reason": reason,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]
```

### Verification for Tier 3

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv run python -c "
from demo_replay.clients import register_agent, post_task, submit_bid, approve_task, dispute_task
print('clients.py imports OK')
"
```

Expected: `clients.py imports OK`

---

## TIER 4: Create engine.py (Scenario Loader + Step Executor)

### What to do
Create `tools/src/demo_replay/engine.py` — the core replay engine.

### File: `tools/src/demo_replay/engine.py`

Create this file with exactly this content:

```python
"""Scenario engine — loads YAML, executes steps sequentially with delays."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import yaml
from rich.console import Console
from rich.panel import Panel

from demo_replay import clients
from demo_replay.wallet import DemoAgent

console = Console()


def load_scenario(path: Path) -> dict[str, Any]:
    """Load and validate a YAML scenario file."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        msg = f"Invalid scenario file: {path}"
        raise ValueError(msg)
    for required in ("name", "agents", "steps"):
        if required not in raw:
            msg = f"Scenario missing required key: {required}"
            raise ValueError(msg)
    return raw


class ReplayEngine:
    """Execute a demo scenario against live services."""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
        self.default_delay: float = float(scenario.get("default_delay", 2.0))
        self.agents: dict[str, DemoAgent] = {}
        self.platform: DemoAgent | None = None
        # task tracking: poster_handle -> most recent task_id
        self._latest_task: dict[str, str] = {}
        # named refs: ref_name -> task_id
        self._refs: dict[str, str] = {}
        # bid tracking: task_id -> list of bid responses
        self._bids: dict[str, list[dict[str, Any]]] = {}
        # worker_handle -> task_id (set when bid is accepted)
        self._worker_task: dict[str, str] = {}

    def _resolve_task_id(self, step: dict[str, Any], agent_handle: str) -> str:
        """Resolve task_id from explicit task_ref, poster's latest, or worker assignment."""
        if "task_ref" in step:
            ref = step["task_ref"]
            if ref not in self._refs:
                msg = f"Unknown task_ref: {ref}"
                raise ValueError(msg)
            return self._refs[ref]
        if agent_handle in self._latest_task:
            return self._latest_task[agent_handle]
        if agent_handle in self._worker_task:
            return self._worker_task[agent_handle]
        msg = f"No task found for agent '{agent_handle}' and no task_ref specified"
        raise ValueError(msg)

    async def run(self) -> None:
        """Execute all scenario steps."""
        name = self.scenario["name"]
        console.print(Panel(f"[bold cyan]{name}[/bold cyan]", title="Demo Replay"))

        # Create agent objects (keypairs generated in memory)
        for agent_def in self.scenario["agents"]:
            handle = agent_def["handle"]
            display_name = agent_def["name"]
            self.agents[handle] = DemoAgent.create(handle, display_name)

        # Create platform agent for funding operations
        self.platform = DemoAgent.create("platform", "Platform")

        async with httpx.AsyncClient(timeout=30.0) as http:
            # Register platform agent first
            console.print("[dim]Registering platform agent...[/dim]")
            await clients.register_agent(http, self.platform)
            console.print(
                f"  [green]Platform registered:[/green] {self.platform.agent_id}"
            )

            total = len(self.scenario["steps"])
            for i, step in enumerate(self.scenario["steps"], 1):
                action = step["action"]
                delay = float(step.get("delay", self.default_delay))

                console.print(
                    f"\n[bold][{i}/{total}][/bold] [yellow]{action}[/yellow]"
                )
                await self._execute_step(http, step)
                console.print(f"  [dim]waiting {delay}s...[/dim]")
                await asyncio.sleep(delay)

        console.print(Panel("[bold green]Demo complete![/bold green]"))

    async def _execute_step(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        """Dispatch a single step to the appropriate handler."""
        action = step["action"]
        match action:
            case "register":
                await self._do_register(http, step)
            case "fund":
                await self._do_fund(http, step)
            case "post_task":
                await self._do_post_task(http, step)
            case "bid":
                await self._do_bid(http, step)
            case "accept_bid":
                await self._do_accept_bid(http, step)
            case "upload_asset":
                await self._do_upload_asset(http, step)
            case "submit_deliverable":
                await self._do_submit_deliverable(http, step)
            case "approve":
                await self._do_approve(http, step)
            case "dispute":
                await self._do_dispute(http, step)
            case _:
                console.print(f"  [red]Unknown action: {action}[/red]")

    async def _do_register(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        handle = step["agent"]
        agent = self.agents[handle]
        result = await clients.register_agent(http, agent)
        console.print(
            f"  [green]Registered {agent.name}[/green] -> {result['agent_id']}"
        )

        # Also create bank account via platform agent
        assert self.platform is not None
        assert agent.agent_id is not None
        await clients.create_account(http, self.platform, agent.agent_id)
        console.print(f"  [green]Bank account created[/green] for {agent.name}")

    async def _do_fund(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        handle = step["agent"]
        amount = int(step["amount"])
        agent = self.agents[handle]
        assert self.platform is not None
        assert agent.agent_id is not None
        result = await clients.credit_account(
            http, self.platform, agent.agent_id, amount
        )
        console.print(
            f"  [green]Funded {agent.name}[/green] +{amount} coins"
            f" (balance: {result.get('balance_after', '?')})"
        )

    async def _do_post_task(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        poster_handle = step["poster"]
        poster = self.agents[poster_handle]
        result = await clients.post_task(
            http,
            poster,
            title=step["title"],
            spec=step.get("spec", step["title"]),
            reward=int(step["reward"]),
        )
        task_id = result["task_id"]
        self._latest_task[poster_handle] = task_id
        if "ref" in step:
            self._refs[step["ref"]] = task_id
        console.print(
            f"  [green]Task posted:[/green]"
            f" \"{step['title']}\" for {step['reward']} coins -> {task_id}"
        )

    async def _do_bid(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        bidder_handle = step["bidder"]
        bidder = self.agents[bidder_handle]
        # Resolve task: check task_ref, then poster, then any latest task
        if "task_ref" in step:
            task_id = self._refs[step["task_ref"]]
        elif "poster" in step:
            task_id = self._latest_task[step["poster"]]
        else:
            # Find most recent task from any poster
            task_id = list(self._latest_task.values())[-1]
        amount = int(step["amount"])
        result = await clients.submit_bid(http, bidder, task_id, amount)
        bid_id = result.get("bid_id", "?")
        self._bids.setdefault(task_id, []).append(result)
        console.print(
            f"  [green]{bidder.name} bid {amount} coins[/green]"
            f" on task -> bid_id={bid_id}"
        )

    async def _do_accept_bid(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        poster_handle = step["poster"]
        poster = self.agents[poster_handle]
        bidder_handle = step["bidder"]
        task_id = self._resolve_task_id(step, poster_handle)

        # Find the bid_id for this bidder
        bids = await clients.list_bids(http, poster, task_id)
        bidder = self.agents[bidder_handle]
        bid_id: str | None = None
        for bid in bids:
            if bid.get("bidder_id") == bidder.agent_id:
                bid_id = bid["bid_id"]
                break
        if bid_id is None:
            console.print(f"  [red]No bid found from {bidder_handle}[/red]")
            return

        await clients.accept_bid(http, poster, task_id, bid_id)
        # Track which task this worker is assigned to
        self._worker_task[bidder_handle] = task_id
        console.print(
            f"  [green]{poster.name} accepted {bidder.name}'s bid[/green]"
            " -> contract formed"
        )

    async def _do_upload_asset(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        worker_handle = step["worker"]
        worker = self.agents[worker_handle]
        task_id = self._resolve_task_id(step, worker_handle)
        filename = step.get("filename", "deliverable.txt")
        content_str = step.get("content", "Demo deliverable content")
        content = content_str.encode() if isinstance(content_str, str) else content_str
        await clients.upload_asset(http, worker, task_id, filename, content)
        console.print(f"  [green]{worker.name} uploaded[/green] '{filename}'")

    async def _do_submit_deliverable(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        worker_handle = step["worker"]
        worker = self.agents[worker_handle]
        task_id = self._resolve_task_id(step, worker_handle)
        await clients.submit_deliverable(http, worker, task_id)
        console.print(
            f"  [green]{worker.name} submitted deliverables[/green] for review"
        )

    async def _do_approve(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        poster_handle = step["poster"]
        poster = self.agents[poster_handle]
        task_id = self._resolve_task_id(step, poster_handle)
        await clients.approve_task(http, poster, task_id)
        console.print(
            f"  [green]{poster.name} approved task[/green] -> payout released!"
        )

    async def _do_dispute(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        poster_handle = step["poster"]
        poster = self.agents[poster_handle]
        task_id = self._resolve_task_id(step, poster_handle)
        reason = step.get("reason", "Deliverable does not meet specification")
        await clients.dispute_task(http, poster, task_id, reason)
        console.print(
            f"  [green]{poster.name} disputed task:[/green] \"{reason}\""
        )
```

### Verification for Tier 4

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv run python -c "
from demo_replay.engine import ReplayEngine, load_scenario
print('engine.py imports OK')
"
```

Expected: `engine.py imports OK`

---

## TIER 5: Create __main__.py (CLI Entry Point)

### What to do
Create the CLI entry point at `tools/src/demo_replay/__main__.py`.

### File: `tools/src/demo_replay/__main__.py`

Create this file with exactly this content:

```python
"""CLI entry point for the demo replay engine.

Usage::

    cd tools/
    uv run python -m demo_replay scenarios/quick.yaml
    uv run python -m demo_replay scenarios/scale.yaml --delay 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from demo_replay.engine import ReplayEngine, load_scenario


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a demo scenario against live services.",
    )
    parser.add_argument(
        "scenario",
        type=Path,
        help="Path to the YAML scenario file.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Override default delay between steps (seconds).",
    )
    return parser.parse_args()


def main() -> None:
    """Parse args, load scenario, run replay."""
    args = _parse_args()

    scenario_path: Path = args.scenario
    if not scenario_path.exists():
        print(f"Error: scenario file not found: {scenario_path}", file=sys.stderr)
        sys.exit(1)

    scenario = load_scenario(scenario_path)

    if args.delay is not None:
        scenario["default_delay"] = args.delay

    engine = ReplayEngine(scenario)
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
```

### Verification for Tier 5

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv run python -m demo_replay --help
```

Expected: Shows help text with `scenario` positional arg and `--delay` option.

---

## TIER 6: Create quick.yaml Scenario

### What to do
Create `tools/scenarios/` directory and `quick.yaml` scenario file.

First create the directory:

```bash
mkdir -p /Users/flo/Developer/github/agent-economy/tools/scenarios
```

### File: `tools/scenarios/quick.yaml`

Create this file with exactly this content:

```yaml
# Quick demo: 3 agents, 1 happy-path task + 1 dispute (~25 seconds)

name: "Quick Demo — Task Lifecycle"
description: >
  Demonstrates the full task lifecycle with three agents.
  Act 1: Register agents and fund accounts.
  Act 2: Happy path — post task, bid, accept, submit, approve.
  Act 3: Dispute path — post task, bid, accept, submit, dispute.
default_delay: 2.0

agents:
  - handle: alice
    name: "Alice (Poster)"
  - handle: bob
    name: "Bob (Worker)"
  - handle: carol
    name: "Carol (Worker)"

steps:
  # ── Act 1: Setup ──────────────────────────────────────────
  - action: register
    agent: alice
    delay: 1.0

  - action: register
    agent: bob
    delay: 1.0

  - action: register
    agent: carol
    delay: 1.0

  - action: fund
    agent: alice
    amount: 5000
    delay: 1.5

  - action: fund
    agent: bob
    amount: 1000
    delay: 1.0

  - action: fund
    agent: carol
    amount: 1000
    delay: 1.5

  # ── Act 2: Happy path ────────────────────────────────────
  - action: post_task
    poster: alice
    ref: login_task
    title: "Implement login page"
    spec: "Build a responsive login form with email and password fields, client-side validation, error states, and a forgot-password link. Must be accessible (WCAG 2.1 AA)."
    reward: 200
    delay: 3.0

  - action: bid
    bidder: bob
    task_ref: login_task
    amount: 180

  - action: bid
    bidder: carol
    task_ref: login_task
    amount: 190
    delay: 2.5

  - action: accept_bid
    poster: alice
    bidder: bob
    task_ref: login_task
    delay: 2.5

  - action: upload_asset
    worker: bob
    task_ref: login_task
    filename: "login-page.html"
    content: |
      <!DOCTYPE html>
      <html lang="en">
      <head><title>Login</title></head>
      <body>
        <form id="login" aria-label="Sign in">
          <label for="email">Email</label>
          <input id="email" type="email" required />
          <label for="password">Password</label>
          <input id="password" type="password" required />
          <button type="submit">Sign In</button>
          <a href="/forgot-password">Forgot password?</a>
        </form>
      </body>
      </html>
    delay: 2.0

  - action: submit_deliverable
    worker: bob
    task_ref: login_task
    delay: 3.0

  - action: approve
    poster: alice
    task_ref: login_task
    delay: 3.0

  # ── Act 3: Dispute path ──────────────────────────────────
  - action: post_task
    poster: alice
    ref: api_task
    title: "Design REST API specification"
    spec: "Design a complete RESTful API spec for a todo application. Must include all CRUD endpoints (GET, POST, PUT, PATCH, DELETE), pagination, error responses, and authentication headers."
    reward: 150
    delay: 3.0

  - action: bid
    bidder: carol
    task_ref: api_task
    amount: 140
    delay: 2.5

  - action: accept_bid
    poster: alice
    bidder: carol
    task_ref: api_task
    delay: 2.5

  - action: upload_asset
    worker: carol
    task_ref: api_task
    filename: "api-spec.md"
    content: |
      # Todo API Specification
      ## Endpoints
      - GET /todos - List all todos (paginated)
      - POST /todos - Create a new todo
      - GET /todos/:id - Get a single todo
      - DELETE /todos/:id - Delete a todo
      ## Authentication
      - Bearer token in Authorization header
    delay: 2.0

  - action: submit_deliverable
    worker: carol
    task_ref: api_task
    delay: 3.0

  - action: dispute
    poster: alice
    task_ref: api_task
    reason: "Specification is incomplete: missing PUT and PATCH endpoints for updating todos, no error response schemas defined, and no pagination parameters documented."
```

### Verification for Tier 6

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv run python -c "
import yaml
from pathlib import Path
d = yaml.safe_load(Path('scenarios/quick.yaml').read_text())
print(f'Name: {d[\"name\"]}')
print(f'Agents: {len(d[\"agents\"])}')
print(f'Steps: {len(d[\"steps\"])}')
print('quick.yaml OK')
"
```

Expected: Shows 3 agents, ~18 steps, and `quick.yaml OK`.

---

## TIER 7: Create scale.yaml Scenario

### File: `tools/scenarios/scale.yaml`

Create this file with exactly this content:

```yaml
# Scaled demo: 10 agents, 5 task waves, multiple lifecycles (~60 seconds)

name: "Scaled Economy Demo"
description: >
  Demonstrates a bustling micro-economy with 10 agents, multiple concurrent
  tasks, competitive bidding, approvals, and disputes.
default_delay: 1.5

agents:
  - handle: acme_corp
    name: "ACME Corp (Poster)"
  - handle: buildbot
    name: "BuildBot Labs (Poster)"
  - handle: nova
    name: "Nova Systems (Poster)"
  - handle: atlas
    name: "Atlas (Developer)"
  - handle: beacon
    name: "Beacon (Developer)"
  - handle: cipher
    name: "Cipher (Developer)"
  - handle: delta
    name: "Delta (Developer)"
  - handle: echo_dev
    name: "Echo (Designer)"
  - handle: flux
    name: "Flux (DevOps)"
  - handle: genesis
    name: "Genesis (Full-Stack)"

steps:
  # ── Phase 1: Bootstrap agents ─────────────────────────
  - action: register
    agent: acme_corp
    delay: 0.5

  - action: register
    agent: buildbot
    delay: 0.5

  - action: register
    agent: nova
    delay: 0.5

  - action: register
    agent: atlas
    delay: 0.5

  - action: register
    agent: beacon
    delay: 0.5

  - action: register
    agent: cipher
    delay: 0.5

  - action: register
    agent: delta
    delay: 0.5

  - action: register
    agent: echo_dev
    delay: 0.5

  - action: register
    agent: flux
    delay: 0.5

  - action: register
    agent: genesis
    delay: 0.5

  # ── Phase 2: Fund accounts ────────────────────────────
  - action: fund
    agent: acme_corp
    amount: 10000
    delay: 0.3

  - action: fund
    agent: buildbot
    amount: 8000
    delay: 0.3

  - action: fund
    agent: nova
    amount: 6000
    delay: 0.3

  - action: fund
    agent: atlas
    amount: 500
    delay: 0.3

  - action: fund
    agent: beacon
    amount: 500
    delay: 0.3

  - action: fund
    agent: cipher
    amount: 500
    delay: 0.3

  - action: fund
    agent: delta
    amount: 500
    delay: 0.3

  - action: fund
    agent: echo_dev
    amount: 500
    delay: 0.3

  - action: fund
    agent: flux
    amount: 500
    delay: 0.3

  - action: fund
    agent: genesis
    amount: 500
    delay: 1.0

  # ── Phase 3: Wave 1 — ACME posts two tasks ───────────
  - action: post_task
    poster: acme_corp
    ref: auth_system
    title: "Build authentication system"
    spec: "Implement JWT-based auth with login, register, and password reset. Include rate limiting and brute-force protection."
    reward: 500
    delay: 2.0

  - action: post_task
    poster: acme_corp
    ref: dashboard_ui
    title: "Design admin dashboard"
    spec: "Create a responsive admin dashboard with user management table, activity charts, and system health indicators."
    reward: 300
    delay: 2.0

  # Competitive bidding on auth_system
  - action: bid
    bidder: atlas
    task_ref: auth_system
    amount: 450
    delay: 0.8

  - action: bid
    bidder: beacon
    task_ref: auth_system
    amount: 480
    delay: 0.8

  - action: bid
    bidder: genesis
    task_ref: auth_system
    amount: 420
    delay: 1.5

  # Bidding on dashboard
  - action: bid
    bidder: echo_dev
    task_ref: dashboard_ui
    amount: 280
    delay: 0.8

  - action: bid
    bidder: delta
    task_ref: dashboard_ui
    amount: 260
    delay: 1.5

  # ACME accepts bids
  - action: accept_bid
    poster: acme_corp
    bidder: genesis
    task_ref: auth_system
    delay: 2.0

  - action: accept_bid
    poster: acme_corp
    bidder: echo_dev
    task_ref: dashboard_ui
    delay: 2.0

  # ── Phase 4: Wave 2 — BuildBot and Nova post ─────────
  - action: post_task
    poster: buildbot
    ref: ci_pipeline
    title: "Set up CI/CD pipeline"
    spec: "Configure GitHub Actions with build, test, lint, and deploy stages. Include Docker image building and staging deployment."
    reward: 400
    delay: 1.5

  - action: post_task
    poster: nova
    ref: api_gateway
    title: "Implement API gateway"
    spec: "Build a lightweight API gateway with rate limiting, request routing, API key validation, and request/response logging."
    reward: 600
    delay: 1.5

  - action: bid
    bidder: flux
    task_ref: ci_pipeline
    amount: 380
    delay: 0.8

  - action: bid
    bidder: cipher
    task_ref: ci_pipeline
    amount: 350
    delay: 0.8

  - action: bid
    bidder: atlas
    task_ref: api_gateway
    amount: 550
    delay: 0.8

  - action: bid
    bidder: beacon
    task_ref: api_gateway
    amount: 520
    delay: 1.5

  - action: accept_bid
    poster: buildbot
    bidder: flux
    task_ref: ci_pipeline
    delay: 2.0

  - action: accept_bid
    poster: nova
    bidder: atlas
    task_ref: api_gateway
    delay: 2.0

  # ── Phase 5: Deliveries ──────────────────────────────
  # Genesis delivers auth system
  - action: upload_asset
    worker: genesis
    task_ref: auth_system
    filename: "auth-system.py"
    content: "# JWT auth implementation with bcrypt password hashing\n# Rate limiting via sliding window counter\n# Brute-force protection with exponential backoff"
    delay: 1.5

  - action: submit_deliverable
    worker: genesis
    task_ref: auth_system
    delay: 2.0

  - action: approve
    poster: acme_corp
    task_ref: auth_system
    delay: 2.5

  # Echo delivers dashboard
  - action: upload_asset
    worker: echo_dev
    task_ref: dashboard_ui
    filename: "dashboard-mockup.html"
    content: "<html><body><h1>Admin Dashboard</h1><div class='grid'>User table, charts, health</div></body></html>"
    delay: 1.5

  - action: submit_deliverable
    worker: echo_dev
    task_ref: dashboard_ui
    delay: 2.0

  - action: approve
    poster: acme_corp
    task_ref: dashboard_ui
    delay: 2.5

  # Flux delivers CI pipeline
  - action: upload_asset
    worker: flux
    task_ref: ci_pipeline
    filename: "ci-pipeline.yml"
    content: "name: CI\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps: [checkout, test, lint, deploy]"
    delay: 1.5

  - action: submit_deliverable
    worker: flux
    task_ref: ci_pipeline
    delay: 2.0

  - action: approve
    poster: buildbot
    task_ref: ci_pipeline
    delay: 2.5

  # Atlas delivers API gateway — but Nova disputes!
  - action: upload_asset
    worker: atlas
    task_ref: api_gateway
    filename: "api-gateway.py"
    content: "# Simple proxy with rate limiting\n# Missing: API key validation, request logging"
    delay: 1.5

  - action: submit_deliverable
    worker: atlas
    task_ref: api_gateway
    delay: 2.0

  - action: dispute
    poster: nova
    task_ref: api_gateway
    reason: "Deliverable is missing two required features: API key validation and request/response logging. The spec explicitly required both."
    delay: 2.0

  # ── Phase 6: Wave 3 — More tasks ─────────────────────
  - action: post_task
    poster: buildbot
    ref: monitoring
    title: "Set up monitoring and alerting"
    spec: "Configure Prometheus metrics, Grafana dashboards, and PagerDuty alerting for all production services."
    reward: 350
    delay: 1.5

  - action: bid
    bidder: flux
    task_ref: monitoring
    amount: 330
    delay: 0.8

  - action: bid
    bidder: delta
    task_ref: monitoring
    amount: 300
    delay: 1.0

  - action: accept_bid
    poster: buildbot
    bidder: delta
    task_ref: monitoring
    delay: 2.0

  - action: upload_asset
    worker: delta
    task_ref: monitoring
    filename: "monitoring-config.yaml"
    content: "# Prometheus scrape configs\n# Grafana dashboard definitions\n# PagerDuty integration"
    delay: 1.5

  - action: submit_deliverable
    worker: delta
    task_ref: monitoring
    delay: 2.0

  - action: approve
    poster: buildbot
    task_ref: monitoring
```

### Verification for Tier 7

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv run python -c "
import yaml
from pathlib import Path
d = yaml.safe_load(Path('scenarios/scale.yaml').read_text())
print(f'Name: {d[\"name\"]}')
print(f'Agents: {len(d[\"agents\"])}')
print(f'Steps: {len(d[\"steps\"])}')
print('scale.yaml OK')
"
```

Expected: Shows 10 agents, ~55 steps, and `scale.yaml OK`.

---

## TIER 8: Add Just Targets to Root Justfile

### What to do
Add `demo` and `demo-scale` targets to the root `justfile`, and update `tools/justfile`.

### File 1: Root `justfile` (at `/Users/flo/Developer/github/agent-economy/justfile`)

Make these changes:

**Change 1:** In the `help` recipe, AFTER the line:
```
    @printf "  \033[0;37mjust generate-tasks    \033[0;34m Generate math tasks to data/math_tasks.jsonl\033[0m\n"
```

Add these lines:
```
    @echo ""
    @printf "\033[1;33mDemo\033[0m\n"
    @printf "  \033[0;37mjust demo             \033[0;34m Run quick demo (3 agents, ~25s)\033[0m\n"
    @printf "  \033[0;37mjust demo-scale       \033[0;34m Run scaled demo (10 agents, ~60s)\033[0m\n"
```

**Change 2:** AFTER the `generate-tasks` recipe and BEFORE the `# --- Docker ---` comment, add these two new recipes:

```just
# --- Demo ---

# Run quick demo (3 agents, 1 task lifecycle + 1 dispute, ~25s)
demo:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Quick Demo ===\033[0m\n"
    printf "\n"

    printf "Stopping services...\n"
    just stop-all 2>/dev/null || true

    printf "Wiping databases...\n"
    rm -f data/economy.db data/economy.db-wal data/economy.db-shm
    for svc in services/*/; do
        rm -f "$svc"data/*.db "$svc"data/*.db-wal "$svc"data/*.db-shm
    done

    printf "Starting services...\n"
    just start-all

    printf "\n"
    printf "\033[0;34m--- Running quick scenario ---\033[0m\n"
    printf "\n"
    cd tools && uv run python -m demo_replay scenarios/quick.yaml
    exit_code=$?

    printf "\n"
    if [ $exit_code -eq 0 ]; then
        printf "\033[0;32m✓ Demo complete — UI at http://localhost:8008\033[0m\n"
    else
        printf "\033[0;31m✗ Demo failed (exit code: %d)\033[0m\n" "$exit_code"
    fi
    printf "\n"

# Run scaled demo (10 agents, multiple task waves, ~60s)
demo-scale:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Scaled Economy Demo ===\033[0m\n"
    printf "\n"

    printf "Stopping services...\n"
    just stop-all 2>/dev/null || true

    printf "Wiping databases...\n"
    rm -f data/economy.db data/economy.db-wal data/economy.db-shm
    for svc in services/*/; do
        rm -f "$svc"data/*.db "$svc"data/*.db-wal "$svc"data/*.db-shm
    done

    printf "Starting services...\n"
    just start-all

    printf "\n"
    printf "\033[0;34m--- Running scale scenario ---\033[0m\n"
    printf "\n"
    cd tools && uv run python -m demo_replay scenarios/scale.yaml
    exit_code=$?

    printf "\n"
    if [ $exit_code -eq 0 ]; then
        printf "\033[0;32m✓ Demo complete — UI at http://localhost:8008\033[0m\n"
    else
        printf "\033[0;31m✗ Demo failed (exit code: %d)\033[0m\n" "$exit_code"
    fi
    printf "\n"
```

### File 2: `tools/justfile` (at `/Users/flo/Developer/github/agent-economy/tools/justfile`)

Replace the empty `simulate` recipe (line 40-41) with:

```just
# Run a demo scenario (pass scenario file as argument)
simulate scenario:
    @echo ""
    uv run python -m demo_replay {{scenario}}
    @echo ""
```

Also update the help section. Replace line 16:
```
    @printf "  \033[0;37mjust simulate         \033[0;34m Run the simulation injector\033[0m\n"
```
with:
```
    @printf "  \033[0;37mjust simulate <file>  \033[0;34m Run a demo scenario YAML file\033[0m\n"
```

### Verification for Tier 8

```bash
cd /Users/flo/Developer/github/agent-economy && just help 2>&1 | head -30
```

Expected: Should show the Demo section with `just demo` and `just demo-scale` listed.

---

## FINAL VERIFICATION

After ALL tiers are complete, run these final checks:

### Check 1: All files exist

```bash
cd /Users/flo/Developer/github/agent-economy
ls -la tools/src/demo_replay/__init__.py
ls -la tools/src/demo_replay/__main__.py
ls -la tools/src/demo_replay/wallet.py
ls -la tools/src/demo_replay/clients.py
ls -la tools/src/demo_replay/engine.py
ls -la tools/scenarios/quick.yaml
ls -la tools/scenarios/scale.yaml
```

### Check 2: Module imports work

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv run python -c "
from demo_replay.wallet import DemoAgent
from demo_replay.clients import register_agent, post_task, submit_bid, accept_bid, approve_task, dispute_task
from demo_replay.engine import ReplayEngine, load_scenario
print('All imports OK')
"
```

### Check 3: CLI works

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv run python -m demo_replay --help
```

### Check 4: Scenarios are valid YAML

```bash
cd /Users/flo/Developer/github/agent-economy/tools && uv run python -c "
import yaml
from pathlib import Path
for f in ['scenarios/quick.yaml', 'scenarios/scale.yaml']:
    d = yaml.safe_load(Path(f).read_text())
    print(f'{f}: {len(d[\"agents\"])} agents, {len(d[\"steps\"])} steps')
print('All scenarios valid')
"
```

### Check 5: Just targets exist

```bash
cd /Users/flo/Developer/github/agent-economy && just --list 2>&1 | grep -E 'demo|simulate'
```

Expected: Should show `demo`, `demo-scale` in root justfile.

---

## IMPORTANT REMINDERS

- **DO NOT USE GIT.** No git commands at all.
- **Use `uv run` for ALL Python execution.** Never use `python`, `python3`, or `pip install`.
- **Run verifications after EACH tier** before moving to the next.
- **If a verification fails, fix it before proceeding.**
- **Do NOT modify any files outside of `tools/` and the root `justfile`.**
