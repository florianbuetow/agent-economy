# A8 – Deployment / LAN readiness

Goal: one home-LAN host runs all services; any LAN computer registers agents and posts/takes tasks; single instance; some auto-recovery; later kind.

Bottom line: LAN access for agents is a small change, but it is not config-only today. The bind host is hardcoded in the justfile and in the stop script's process matcher. No supervisor exists. Containers are viable only as one all-in-one image, because services share a filesystem.

## 1. LAN access change set

### What blocks LAN today
- **Bind host is hardcoded, and `server.host` is dead config.** Every `_launch-*` recipe passes `--host 127.0.0.1` (justfile:226,229,232,235,238,248,251). The per-service `run` recipes do the same (services/*/justfile:119). `server.host` in each config.yaml is read only by the UI validator (services/ui/src/ui_service/config.py:226-234). No service uses it to bind.
- **The stop script matches the literal host.** `_service_invocation_kind` requires argv tail `("--host","127.0.0.1","--port",…)` (scripts/stop_agent.py:509). Binding anywhere else makes `stop-all` refuse to recognize the process. It must take the configured host.
- **The UI is loopback-locked in code, not only in config.**
  - config.py:128 and :146 reject non-loopback `allowed_hosts` and `allowed_origins`.
  - config.py:226-234 rejects a non-loopback `server.host`.
  - middleware.py:92-95 returns 403 on `/api/proxy/*` for non-loopback clients.
  - Q-3 (docs/plans/2026-07-10-q3-proxy-exposure-decision.md) requires a shared-secret header on `/api/proxy/*` before any non-loopback exposure.
- **Clock skew is tight.** JWS tolerates 30 s (identity/db-gateway/agents config `clock_skew_seconds: 30`, TTL 300 s). Remote machines need NTP. JWS `aud` is a logical service name such as `"central-bank"` (services/central-bank/src/central_bank_service/routers/accounts.py:119), so the hostname does not matter for signatures.
- **No CORS concerns.** Agents are non-browser clients, and only the UI serves browsers.
- **Stale comments.** "Environment variable overrides use prefix: UI__ / AGENT__" (services/ui/config.yaml:2, agents/config.yaml:2) is false. The only env seams are `CONFIG_PATH`, `AGENT_CONFIG_PATH` and secrets (service_commons/config.py:113; Q-7 ADR).

Hardcoded `localhost`/`127.0.0.1` in non-test code otherwise appears only in scripts/demo/*, scripts/isolated_test_runtime.py (test harness, fine) and comments. No code change is needed in the services' URL handling.

### Which services need LAN exposure
| Service | LAN? | Why |
|---|---|---|
| identity 8001 | yes | Remote agents register and fetch agents. |
| central-bank 8002 | yes | Agents create accounts and read balances/transactions. |
| task-board 8003 | yes | Post, bid, submit, approve, dispute, assets. |
| reputation 8004 | yes | Agents submit and read feedback. |
| court 8005 | optional | `/disputes/file` is platform-signed only (court routers/disputes.py:78-84), and Task Board reaches it locally. Expose only for read endpoints. |
| db-gateway 8007 | **no** | Single writer, private. Stays 127.0.0.1. |
| ui 8008 | optional | Read-only dashboard needs code changes (above). The operator proxy must stay loopback-only. |

### Inter-service URLs: no change needed on the server
Server-side callers keep `localhost`:
- task-board identity/bank `base_url`
- reputation/bank `identity.base_url`
- all `db_gateway.url`
- agents/config.yaml `platform.*`, which Court and the UI proxy also consume (court core/lifespan.py:128; ui services/proxy.py:319)

Only remote agent machines need different URLs. Their single place is `platform.*_url` in their own agents/config.yaml, selected via `AGENT_CONFIG_PATH`, which is already sanctioned. The project has no single "public base URL" setting, and none is needed.

### Smallest change set (respects the Q-7 no-new-env-seam rule)
1. Make `server.host` authoritative. `_launch-*` and the service `run` recipes read `server.host` from the service's effective config instead of literal `127.0.0.1`, for example via a tiny `uv run` helper that prints it. `stop_agent.py` uses the same value. No new flag and no default.
2. LAN mode is a config edit. Set `server.host: "0.0.0.0"` in identity, central-bank, task-board and reputation config.yaml, plus court if wanted. Prefer this over a `start-all --lan` flag: a flag would be a second source of truth, which the "all config from config.yaml" rule forbids.
   - If a toggle is still wanted, the consistent form is `CONFIG_PATH=config.lan.yaml` per service. It duplicates whole files, so it is not recommended.
   - Health checks in start-all/status keep using localhost (justfile:271 etc.), which still works with 0.0.0.0.
3. Remote-agent kit, documented and config-only:
   - repo clone, `uv`, `just init` in agents/
   - an agents/config.yaml copy with `platform.*_url = http://<server-ip>:800x`
   - a trimmed roster with no `platform` or `operator` handle
   - its own `keys_dir`
   - NTP
4. Funding for remote posters. Accounts open at 0, and non-platform callers cannot set an `initial_balance` (central-bank routers/accounts.py:155-156). The only funding recipes are `fund-feeder` and `provision`, which are hardwired to feeder/operator (justfile:495,515). The server operator needs a `just fund-agent <agent_id> <amount> <operation_id>` that runs on the server with the platform key. Without it, a remote machine can take tasks but cannot post them.
5. A LAN UI is optional and a separate decision. It needs three code changes: relax validators config.py:128/146/226, keep middleware.py:92 (loopback proxy), and amend Q-3. Either ratify that the loopback-client gate replaces the shared secret, or add the secret.
6. Host firewall. On macOS, the application firewall prompts for or blocks inbound connections to the venv python. Allow it, or use pf.

## 2. Process supervision

### Today
- `start-all` (justfile:254-373) runs a key preflight. It then starts 4 tiers (db-gateway, identity plus a `/agents` gate, the 4 economy services in parallel, then UI) via `just _launch-x &` and waits on `/health`.
- It is idempotent: `start_unless_healthy` skips services that are already up.
- There are no pid files. Children are orphaned when the script exits, and their stdout/stderr stay attached to the invoking terminal, with no redirection. Closing that terminal can cause write errors on stdout.
- File logs also go to services/*/data/logs (service_commons/logging.py:125-134, stdout plus a daily file).
- `stop-all` delegates to `scripts/stop_agent.py`, which identifies processes by uid, cwd, venv interpreter, exact argv and effective `CONFIG_PATH`.
- `status` runs health curls.
- `start-feeder`/`start-mathbot` are `&` background launches (justfile:440-447).
- **No supervisor, no restart-on-crash, and no systemd/launchd docs** (grep finds none). A crashed service stays down until someone runs `just start-all` again.

### What already supports restart (noted only; recovery logic is audited elsewhere)
- Dependents register their platform agent with Identity at startup and exit on failure ("Application startup failed. Exiting.", comment at justfile:288-293). A restart loop therefore converges without explicit ordering.
- Task Board cancels and re-creates its recovery loops (financial-effect recovery, posting recovery, deadline sweep) in lifespan (task_board_service/core/lifespan.py:222-232).
- The agent runtime keeps caller journals under data/agent-runtime.
- The shared `fcntl` maintenance lock (service_commons/maintenance_lock.py) is released on process death.

### Smallest option
Use one OS-native unit per service, generated from a single template by a just recipe (`just supervise-install` / `supervise-uninstall`).
- **Host is macOS (current machine):** a launchd LaunchAgent plist per service.
  - `WorkingDirectory=services/<svc>`
  - `ProgramArguments` identical to `_launch-*`, so stop_agent still recognizes it
  - `KeepAlive={SuccessfulExit=false}`, `ThrottleInterval=5`
  - `StandardOutPath` under data/logs
  - `EnvironmentVariables` only for the Court secret
- **Host is Linux:** a systemd user unit template `ate@.service` with `Restart=on-failure`, `RestartSec=3` and `After=` ordering db-gateway, then identity.

Caveat: under a supervisor, `stop-all` fights the respawn. Stopping must go through `launchctl bootout` or `systemctl stop`. The supervise recipes must own stop/start in that mode, and the docs must say so. Add the feeder and mathbot units the same way if the economy should run unattended.

A shell restart loop is rejected because stop_agent.py would not recognize its parent. supervisord/honcho is rejected because it adds a dependency for no gain over launchd/systemd.

## 3. Container / kind readiness

**Verdict:** Q-6 remains correct for a per-service image plus compose. The LAN goal does not need containers at all. When kind is pursued, use **option (b): one all-in-one image running all 7 services, deployed as one Pod with one PVC**. Per-service images or Pods (a, c) are blocked by the shared-filesystem coupling below. Fixing that is architecture work, not packaging.

### 12-factor status
| Aspect | Status |
|---|---|
| Config | Files via `CONFIG_PATH` (sanctioned). A ConfigMap can mount config.yaml. No env overrides exist, which is consistent with Q-7. |
| Secrets | Court `api_key_env`, agents `${VAR}`, injectable from a k8s Secret. Court `.env` is sourced only by `_launch-court` (justfile:240-248). |
| Paths | All relative and cwd-dependent (`../../data/keys`, `../../data/maintenance.lock`, `../db-gateway/data/economy.db`, `../../agents/config.yaml`). This works only if the repo layout is preserved inside the image. |
| Data dir | Mixed with code. The UI's tracked frontend lives in `services/ui/data/web` (13 tracked files), so a volume mounted over services/ui/data hides the frontend. 14 log files under services/ui/data/logs are tracked in git, which is a hygiene bug. |
| Venvs | 7 service venvs plus agents/tools, each `uv sync`. The image is large but buildable. |
| Ports and health | Fixed ports; `/health` on every service works for liveness. Readiness for Identity should also hit `/agents` (justfile:300-307). |
| Logs | Go to stdout as well as files. |
| Shutdown | uvicorn handles SIGTERM, and lifespan cancels background tasks. |
| Bind | Inside a container, binding must be 0.0.0.0. This hits the same justfile/stop_agent blocker as LAN. Published-port traffic arrives from the bridge IP, so the UI proxy returns 403 (non-loopback client) and the Host allowlist rejects. Only in-pod curl works. |

### Blockers for any container path
1. Bind host is hardcoded (justfile and stop_agent.py:509).
2. UI loopback validators and middleware (above).
3. The frontend sits inside a data dir, and runtime data is spread across 4+ directories with no single data root. A single `data_root` would need a config restructure or several volume mounts.
4. Keys: `data/keys` holds platform, operator and 5 gateway signing keys. It must be provisioned before first start (`just provision-keys`) and persisted, so use a PVC or Secret. It is not image content.
5. Start ordering: in one container, start-all already handles it. Across pods, use restart backoff.

## 4. Co-location constraints

These components MUST run on the same host or kernel, and share one filesystem:
- **db-gateway + UI.** The UI opens `economy.db` directly read-only via aiosqlite (ui core/lifespan.py:47). SQLite WAL across hosts or over NFS is unsafe.
- **db-gateway + task-board + UI.** All three hold an `fcntl` maintenance lock on `data/maintenance.lock` (each core/lifespan.py). flock semantics need a local filesystem.
- **All platform services + data/keys.** central-bank, task-board, reputation, court, identity and UI read private keys from `../../data/keys`, and the db-gateway reads the gateway `.pub` files there.
- **Court, UI, task-board, central-bank, reputation + agents/config.yaml and roster.yaml.** All read them via `agent_config_path: ../../agents/config.yaml`.
- **task-board + its asset store** (`services/task-board/data/assets`). The UI fetches assets over HTTP through task-board (proxy.py:319), so the UI has no direct disk dependency on assets.
- **Treasury and funding CLIs** need the platform key, so they run on the server.

These can be remote: any agent process (feeder, math workers, custom agents), given a URL config, its own keys, NTP and HTTP reachability to identity, bank, task-board and reputation.

## 5. Backup / restore and data dir

### Live state
| Path | Contents | Criticality |
|---|---|---|
| `services/db-gateway/data/economy.db` (+ `-wal`, `-shm`) | The whole ledger and economy | Critical |
| `data/keys/` | Platform, operator, gateway and worker private keys | Critical, irreplaceable |
| `services/task-board/data/assets/` | Deliverables | Important |
| `data/agent-runtime/` | Agent journals | Matters for in-flight agent ops |
| `services/court/.env` | Secrets | Restore by hand |
| `services/*/data/logs`, `data/logs` | Logs | Optional |

The task-board config also names `data/task-board.db` (config.yaml:18). Lifespan uses its parent directory (lifespan.py:73-74), and that directory currently contains no `.db` file.

### Existing tooling
The recovery tooling (docs/operations/recovery-artifacts.md; db_gateway_service/recovery_cli) targets isolated disposable runtimes. It drains owned processes before producing an inventory, so it is not an online backup of the main runtime.

### Smallest safe practice
- **Cold and consistent (recommended):** run `just stop-all` (or bootout under a supervisor), tar the five paths above, then `just start-all`. This takes seconds on a home LAN.
- **Online DB-only:** `sqlite3 services/db-gateway/data/economy.db ".backup <file>"`. The SQLite backup API is WAL-safe. Assets and keys copied at a different instant may not match the DB snapshot exactly.
- **Restore:** stop, put the files back in place, check `just check-keys`, start.

## Work items (ordered)
1. **Make `server.host` authoritative.** `_launch-*`, service `run` and stop_agent.py read host from the effective config. Done when `just start-all` then `just stop-all` succeed with `server.host: 0.0.0.0` on identity, and `lsof` shows `*:8001`.
2. **LAN config.** Set `0.0.0.0` on identity, bank, task-board and reputation; keep db-gateway, UI and court on 127.0.0.1. Done when a second LAN machine gets `curl http://<ip>:8003/health` ok and `:8007` is refused.
3. **Remote-agent kit doc.** Cover the config copy, trimmed roster, keys and NTP. Done when a math worker on a second machine registers, bids and gets paid end to end against the server.
4. **`just fund-agent`.** It credits any agent_id with an operation_id on the server. Done when a remote poster can post a task after funding.
5. **Supervision recipes.** `just supervise-install/uninstall` installs launchd plists on macOS or systemd units on Linux. Done when `kill -9` of the task-board pid is followed by a healthy restart within 10 s, with no manual action.
6. **Backup recipe.** `just backup <dest>` does stop, tar of the 5 paths, then start. Done when a restore into a fresh checkout, followed by `start-all`, shows the same balances.
7. **Hygiene.** Untrack `services/ui/data/logs/*` and `.DS_Store`, and fix the stale `UI__`/`AGENT__` comments. Done when `git ls-files 'services/*/data/logs'` is empty.
8. **Optional LAN UI.** Requires an owner decision on Q-3, then relaxing the ui config.py validators while the loopback-only proxy stays. Done when the dashboard loads from another machine and `/api/proxy/*` from that machine returns 403.
9. **Optional kind.** All-in-one image plus a single-Pod manifest with a PVC for the data paths. Move the UI frontend out of `services/ui/data` first. Done when a kind cluster serves `/health` for all 7 services via a NodePort and survives pod deletion with data intact.
