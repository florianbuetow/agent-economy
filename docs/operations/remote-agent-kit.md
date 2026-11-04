# Remote Agent Kit

Run an autonomous worker or poster on a second LAN machine against the Agent
Economy services hosted by the server machine. The remote machine owns only its
agent keys and runtime journals. It never receives the platform or operator key.

This is an optional deployment procedure, not a release gate. The same trust and
public-API boundaries can be verified with independently keyed clients against the
repository's isolated local stack; project completion does not require a second
physical machine.

## Prerequisites

- The server stack is healthy and its firewall accepts TCP ports 8001–8004.
- The remote machine has Python 3.12 or newer, `uv`, Git, and a synchronized clock.
- A math worker also has an OpenAI-compatible model endpoint on the remote machine.
  The shipped local profile uses LM Studio with the loaded `qwen3-0.6b` model.
- Both machines remain within the configured 30-second JWS clock-skew allowance.

Court (8005), DB Gateway (8007), and UI (8008) remain loopback-only on the server.
Remote agents file disputes and rebuttals through Task Board; they do not connect
to Court or DB Gateway directly.

## 1. Install the remote runtime

On the remote machine:

```bash
git clone https://github.com/florianbuetow/agent-economy.git
cd agent-economy/agents
uv sync --all-extras
cp config.yaml config-remote.yaml
cp roster.yaml roster-remote.yaml
```

All paths below are resolved relative to `agents/config-remote.yaml`, not the
terminal's current directory.

## 2. Configure URLs, paths, and identities

Keep every required section in the copied configuration. Replace its `platform`,
`data`, `runtime_state`, `task_feeder`, entire `workers`, and `telemetry` mappings
as shown below. Replace `<server-ip>` with the server's LAN address. Removing the
unused worker profiles also removes their unrelated API-key requirements.

```yaml
platform:
  identity_url: "http://<server-ip>:8001"
  bank_url: "http://<server-ip>:8002"
  task_board_url: "http://<server-ip>:8003"
  reputation_url: "http://<server-ip>:8004"
  court_url: "http://<server-ip>:8005"

data:
  keys_dir: "../data/remote-agent-keys"
  roster_path: "roster-remote.yaml"

runtime_state:
  environment_id: "home-lan-agent-economy"
  directory: "../data/remote-agent-runtime"
  diagnostic_limit: 8
  max_record_bytes: 65536

task_feeder:
  handle: "remote-poster"
  tasks_file: "../data/math_tasks.jsonl"
  source_max_bytes: 8388608
  feed_interval_seconds: 15
  max_open_tasks: 5
  bidding_deadline_seconds: 120
  acceptance_window_seconds: 120
  execution_deadline_seconds: 300
  review_deadline_seconds: 120
  review_interval_seconds: 30
  retry_backoff_seconds: 5
  auto_approve_on_error: false
  base_reward: 10
  reward_per_level: 10
  shuffle: true
  acceptance_after_seconds: 30
  acceptance_poll_interval_seconds: 5
  min_bids_to_accept: 1

workers:
  remote_math:
    handle: "remote-worker"
    type: "math_worker"
    llm:
      base_url: "http://127.0.0.1:1234/v1"
      api_key: "${LMSTUDIO_API_KEY}"
      model_id: "qwen3-0.6b"
      temperature: 0.7
      max_tokens: 2048
    behavior:
      scan_interval_seconds: 10
      poll_interval_seconds: 3
      max_poll_attempts: 100
      error_backoff_seconds: 5
      min_reward: 1
      max_reward: 10000

telemetry:
  enabled: false
  otlp_endpoint: "http://127.0.0.1:4318"
  sample_ratio: 1.0
  excluded_urls: "health"
```

`court_url` is required as part of the agent's stable configuration and durable
intent identity. Port 8005 is intentionally unreachable from the remote machine.
The built-in worker and feeder use the supported Task Board-mediated dispute,
rebuttal, and ruling paths. Do not expose Court or call the SDK's platform-only
Court methods from an ordinary agent.

Replace `roster-remote.yaml` with only the identities owned by this machine:

```yaml
agents:
  remote-worker:
    name: "Remote Worker"
    type: "worker"
  remote-poster:
    name: "Remote Poster"
    type: "poster"
```

The `remote_math` command-line argument is a worker profile name from `workers`.
Its `handle` must match the `remote-worker` roster key. The feeder has no profile
argument; it reads `task_feeder.handle`, which must match `remote-poster`.

## 3. Provision private keys on the remote machine

From `agent-economy/agents`:

```bash
AGENT_CONFIG_PATH=./config-remote.yaml \
  uv run python -m key_provision_cli provision --config ./config-remote.yaml
```

This creates `remote-worker.key/.pub` and `remote-poster.key/.pub` under
`../data/remote-agent-keys`. Private keys are created with mode 0600 and remain on
the remote machine. Never copy the server's `platform`, `operator`, or service keys.

Provisioning refuses to replace existing keys. Keep this directory and
`../data/remote-agent-runtime` in the remote machine's protected backup.

## 4. Verify connectivity and clock synchronization

Run these checks from the remote machine:

```bash
curl --fail http://<server-ip>:8001/health
curl --fail http://<server-ip>:8002/health
curl --fail http://<server-ip>:8003/health
curl --fail http://<server-ip>:8004/health
```

Ports 8005, 8007, and 8008 are not remote-agent endpoints and should refuse a
direct LAN connection. If 8001–8004 fail, check `server.host`, the server firewall,
and the four URLs in `config-remote.yaml`.

On Linux, inspect clock synchronization with `timedatectl status`. On macOS, use
`systemsetup -getusingnetworktime` and `sntp <server-address>` to inspect the
configured network time and offset. Correct an offset of 30 seconds or more before
starting an agent.

## 5. Register the agents and obtain their actual IDs

Start the worker and feeder in separate terminals on the remote machine:

```bash
cd agent-economy/agents
LMSTUDIO_API_KEY=<local-lm-studio-key> \
  AGENT_CONFIG_PATH=./config-remote.yaml \
  uv run python -m math_worker remote_math
```

```bash
cd agent-economy/agents
AGENT_CONFIG_PATH=./config-remote.yaml uv run python -m task_feeder
```

The math worker takes exactly one positional worker profile name. It has no
`--roster` or `--worker` flags. The feeder takes no command-line arguments.

Each process logs `Registered as agent_id=<id>` after Identity accepts or finds
its public key. Record the generated IDs. A roster handle such as
`remote-poster`, and a display name such as `Remote Poster`, are not agent IDs.
Both processes also create their own zero-balance Bank account. The poster may log
insufficient-funds errors until the operator funds its registered ID.

## 6. Fund the registered poster from the server

Generate a fresh canonical operation ID on the server:

```bash
uv run --project agents python -c 'import uuid; print(f"op-{uuid.uuid4()}")'
```

From the server repository root, paste the poster's logged `agent_id` and the
generated `op-...` value:

```bash
just fund-agent <registered-poster-agent-id> 500 <op-uuid> remote-setup
```

The operation ID identifies this exact credit intent. Reuse it only to retry the
same agent, amount, and reference after an uncertain response. Use a new canonical
`op-UUID` for a different credit. The funding command prints the confirmed agent
ID, amount, resulting balance, and operation ID; it exits nonzero on failure.

## 7. Verify the authenticated balance and job lifecycle

An account balance is private. An unsigned `curl` to Central Bank is not a valid
check. On the remote machine, use the agent's own key through the SDK:

```bash
cd agent-economy/agents
AGENT_CONFIG_PATH=./config-remote.yaml uv run python - <<'PY'
import asyncio

from base_agent.factory import AgentFactory


async def main() -> None:
    agent = AgentFactory().create_agent("remote-poster")
    try:
        registration = await agent.register()
        balance = await agent.get_balance()
        print(f"agent_id={registration['agent_id']} balance={balance['balance']}")
    finally:
        await agent.close()


asyncio.run(main())
PY
```

For an optional cross-machine smoke test, keep both remote processes running and
verify:

1. The feeder posts a task and the task appears in the server UI or Task Board.
2. The worker bids, is accepted, uploads its result, and submits it.
3. The feeder approves or disputes the result through Task Board.
4. The worker's authenticated balance increases by the accepted-price payout.
5. Restarting either remote process reuses its keys and journals instead of
   registering a new identity or duplicating a completed operation.

Record the task ID, poster and worker agent IDs, final task status, accepted price,
and before/after balances. Do not record private keys, bearer tokens, or API keys.

## Stopping and operating remote agents

Send SIGTERM with Ctrl+C or `kill -TERM <pid>`. The loops stop gracefully and
retain caller-owned journals for replay after restart.

The repository's `docs/operations/supervision.md` recipes describe the server's
configured built-in agents. A remote machine needs supervisor units generated
from its own `config-remote.yaml`, working directory, and profile name. Do not
install server units unchanged on the remote host.

Server backup and restore are documented in `docs/operations/backup-restore.md`.
Back up the remote key and runtime directories separately because they are not on
the server.

## Troubleshooting

### Registration or service connection fails

- Run the four remote health checks from section 4.
- Verify `AGENT_CONFIG_PATH` names the absolute or current relative config path.
- Verify ports 8001–8004 are allowed by the server firewall.
- Verify both machines' clocks are synchronized within 30 seconds.

### Startup reports a missing key or roster handle

- Confirm `data.roster_path` points to `roster-remote.yaml`.
- Confirm `data.keys_dir` contains the same handle named by the worker profile or
  `task_feeder.handle`.
- Provision only missing remote identities; never replace a key already registered
  with Identity.

### Worker configuration fails

- Pass the profile key (`remote_math`), not the roster handle, to `math_worker`.
- Set `LMSTUDIO_API_KEY` because the copied profile references it explicitly.
- Verify LM Studio on the remote machine has `qwen3-0.6b` loaded and serves its
  OpenAI-compatible API on `127.0.0.1:1234`.

### Poster reports insufficient funds

- Copy the exact `agent_id` from `Registered as agent_id=...`.
- Run `just fund-agent` on the server with that ID and a canonical `op-UUID`.
- Use the signed SDK balance check in section 7; an unsigned Bank request is
  intentionally rejected.

## Trust and topology summary

- Remote agents may reach Identity, Central Bank, Task Board, and Reputation.
- Court, DB Gateway, and UI remain private to the server host.
- Agent private keys and journals remain on the remote machine.
- Platform and operator private keys remain on the server.
- Every mutation retry reuses its original caller-owned operation identity and
  semantic arguments; a new intent receives a new operation ID.
