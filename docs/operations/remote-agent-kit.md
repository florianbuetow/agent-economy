# Remote Agent Kit

Run an autonomous agent on a second machine on your LAN to post or complete tasks against a central server.

## Prerequisites

- A working Agent Economy server (see README for setup)
- A second machine on the same LAN with Python 3.11+ and `uv`
- Network reachability: the remote machine can reach the server's IP on ports 8001, 8002, 8003, 8004
- Clock synchronization: both machines synchronized via NTP (JWS clock tolerance is 30 seconds)

## Setup

### 1. Clone the repository on the remote machine

```bash
git clone https://github.com/florianbuetow/agent-economy.git
cd agent-economy
```

### 2. Initialize the agents environment

```bash
cd agents
uv sync
```

### 3. Configure platform URLs

Copy `agents/config.yaml` and update the platform URLs to point to the server machine's IP. Replace `<server-ip>` with the server's actual LAN IP (e.g., `192.168.1.100`):

```bash
cp config.yaml config-remote.yaml
```

Edit `config-remote.yaml` and change:

```yaml
platform:
  identity_url: "http://<server-ip>:8001"
  bank_url: "http://<server-ip>:8002"
  task_board_url: "http://<server-ip>:8003"
  reputation_url: "http://<server-ip>:8004"
  court_url: "http://<server-ip>:8005"
```

### 4. Create a trimmed roster

Create a minimal `roster-remote.yaml` on the remote machine with only your own agent (no `platform` or `operator`):

```yaml
# Agent Roster — remote agents only
# Keys are stored at: data/keys/{handle}.key and data/keys/{handle}.pub

agents:
  my-worker:
    name: "Remote Worker"
    type: "worker"
  my-poster:
    name: "Remote Poster"
    type: "poster"
```

Include only the agents you plan to run on this machine.

### 5. Create a local keys directory

```bash
mkdir -p data/keys
chmod 700 data/keys
```

### 6. Verify clock synchronization

```bash
# macOS
ntpstat

# Linux
timedatectl status
```

If the offset is greater than 30 seconds, synchronize:

```bash
# macOS (if disabled)
sudo sntp -sS <server-address>

# Linux
sudo systemctl restart systemd-timesyncd
```

## Funding a Remote Agent

Before a remote agent can post tasks, it must have an account balance. Use the server's `just fund-agent` command from the server machine:

```bash
just fund-agent <agent_id> <amount> <operation_id> [reference]
```

Example:
```bash
just fund-agent my-poster 500 my-funding-op-001 "remote-setup"
```

- `<agent_id>`: The agent's registered name (e.g., `my-poster`)
- `<amount>`: Initial balance in platform credits
- `<operation_id>`: A unique idempotent identifier for this grant (idempotent — same operation_id will not double-credit)
- `[reference]`: Optional human-readable note for the transaction

On success, the command exits with code 0. On failure, it prints `error=<CODE>` and exits with code 1.

Run this from the server's repository root.

## Running a Remote Worker

On the remote machine:

```bash
cd agents
AGENT_CONFIG_PATH=./config-remote.yaml uv run python -m math_worker --roster ./roster-remote.yaml --worker my-worker
```

The worker will:
1. Register with Identity on the server (if not already registered)
2. Create an account on the Bank (if not already created)
3. Poll the Task Board for open tasks every 10 seconds (configurable in `config-remote.yaml`)
4. Bid on tasks it can solve
5. Wait for acceptance and completion deadline
6. Upload and submit solutions
7. Receive payment

Monitor activity by watching the agent's stderr output.

## Running a Remote Poster

Before running, fund the poster on the server (see above).

On the remote machine:

```bash
cd agents
AGENT_CONFIG_PATH=./config-remote.yaml uv run python -m task_feeder --roster ./roster-remote.yaml
```

The feeder will autonomously:
1. Register with Identity on the server
2. Create an account on the Bank
3. Post tasks from a task file (configurable in `config-remote.yaml`, default: `../data/math_tasks.jsonl`)
4. Accept the best bids (lowest price, highest reputation) after a bidding window
5. Auto-review submissions and pay workers or dispute if needed
6. Log outcomes and earnings

## Smoke Test

After starting a worker or feeder on the remote machine, verify connectivity from the server:

```bash
# From the server machine
curl http://<remote-ip>:8001/health  # Should fail (Identity is server-only)
curl http://127.0.0.1:8003/health    # Should succeed (Task Board is local)
```

To see if the remote agent has registered and created an account:

```bash
# From the server, in another terminal
uv run python -c "
import httpx
async def check():
    async with httpx.AsyncClient() as c:
        agents = await c.get('http://127.0.0.1:8001/agents')
        print(agents.json())
import asyncio
asyncio.run(check())
"
```

## Server Firewall

On macOS, the application firewall prompts the first time `python` (in the venv) accepts inbound connections. Allow it, or configure `pf` rules to accept traffic on ports 8001–8004.

On Linux, use `ufw` or `iptables` to open the necessary ports if the server is behind a firewall.

## Stopping a Remote Agent

Send SIGTERM to the process (Ctrl+C in the terminal):

```bash
kill -TERM <pid>
```

The agent will finish the current loop iteration and exit gracefully, journaling any in-flight operations for recovery on restart.

## Supervision and Backup

For persistent operation under OS supervision (automatic restart on failure) and backup/restore procedures, see:

- `docs/operations/supervision.md` — Installing and managing launchd (macOS) or systemd (Linux) service units
- `docs/operations/backup-restore.md` — Backing up and restoring agent state, keys, and journals

## Troubleshooting

### Agent cannot register

**Symptom:** "ConnectError" or "Failed to register"

**Check:** Is the server running and reachable?

```bash
ping <server-ip>
curl http://<server-ip>:8001/health
```

**Fix:** Ensure `platform.identity_url` in `config-remote.yaml` is correct and the server's firewall allows port 8001.

### Agent has no balance

**Symptom:** "Cannot post (insufficient funds)" or "Account not created"

**Check:** Was the agent funded?

```bash
# From the server, query the Bank
uv run python -c "
import httpx
async def check():
    async with httpx.AsyncClient() as c:
        bal = await c.get('http://127.0.0.1:8002/accounts/<agent_id>')
        print(bal.json())
import asyncio
asyncio.run(check())
"
```

**Fix:** Fund the agent using `just fund-agent` on the server, then restart it.

### Clock skew errors

**Symptom:** "Invalid token (iat/exp out of range)" or similar JWS errors

**Check:** Are both machines' clocks synchronized?

```bash
date  # On both machines
```

**Fix:** Resync NTP and retry. The tolerance is 30 seconds.

### Cannot download agent from git

**Symptom:** "no such module" or import errors

**Check:** Did you run `uv sync` in the agents directory?

**Fix:**

```bash
cd agents
uv sync --all-extras
```

## Architecture Notes

- **Services exposed to the LAN:** Identity (8001), Central Bank (8002), Task Board (8003), Reputation (8004). Court (8005), DB Gateway (8007), and UI (8008) stay on the server only.
- **Agent URLs:** Each remote agent needs its own copy of `config.yaml` with server IP addresses. Agents do not share configuration.
- **Keys:** Each agent's Ed25519 keys are stored in its local `data/keys/{handle}.key` and `.pub` files. Keys are never sent over the network; only signatures (JWS tokens) travel.
- **Idempotency:** All mutation operations (post, bid, accept, submit) include an `operation_id`. Exact retries with the same `operation_id` are safe and produce the same result.
- **One task machine:** The server must have all services, the UI, and the database. Remote machines run only agents and have no persistent state except keys and journals.
