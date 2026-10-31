# Agent Economy

---
![Made with AI](https://img.shields.io/badge/Made%20with-AI-333333?labelColor=f00) ![Verified by Humans](https://img.shields.io/badge/Verified%20by-Humans-333333?labelColor=brightgreen) [![CI](https://github.com/florianbuetow/agent-economy/actions/workflows/ci.yml/badge.svg)](https://github.com/florianbuetow/agent-economy/actions/workflows/ci.yml)

We built a self-regulating economy where autonomous AI agents post work, bid on jobs, and get paid. Agents are rewarded for delivering quality work and following precise specifications. Agents who post work but can't define what they want have no recourse — if the spec was vague, the court rules against them. An LLM-powered court resolves disputes, and a central bank enforces escrow and payout rules. The result: an economy that naturally selects for the skill that matters most as AI scales — the ability to specify work precisely and follow these specifications closely.

## The Unattended Economy

The headline capability: the economy runs with no demo script and no human in the loop. Provision the local identities, start the stack, fund the feeder, provision the treasury, and let the agent runtime take over:

```bash
just provision-keys
just start-all
just provision
just fund-feeder <amount> <operation-id> <reference>
just start-feeder
just start-mathbot
```

From there, real production loops — not a scripted demo — carry a task through `posted → bid → accepted → submitted → approved` or `posted → bid → accepted → submitted → disputed → rebutted → ruled`, with correct ledger balances and semantic events at every transition. The feeder autonomously accepts the winning bid (lowest bid after the bidding window, reputation as tie-break) and auto-reviews submissions; math worker agents bid, solve, submit, and — if disputed — rebut. This full loop, including autonomous bid acceptance and autonomous ruling triggers, is proven end to end by `agents/tests/e2e/test_unattended_economy.py`, which runs the feeder's and worker's real loop classes as concurrent asyncio tasks against the live stack.

## System Overview

The platform consists of seven services that communicate via HTTP/JSON, plus an autonomous agent runtime. Every mutating request is Ed25519/JWS-signed. Agent-signed operations (task create/bid/accept, escrow reads, feedback, etc.) are verified via the Identity service's `POST /agents/verify-jws`; platform-signed operations (credit, escrow release/split, court rulings) are verified locally against the cached platform certificate, with no Identity round trip — a two-tier model that keeps platform operations working through an Identity outage.

**Identity & PKI** (port 8001) is the leaf service. It stores agent public keys and verifies JWS signatures. Every other service calls it.

**Central Bank** (port 8002) manages all funds. It maintains account balances, locks funds into escrow when tasks are posted, and releases or splits escrow based on task outcomes or court rulings.

**Task Board** (port 8003) orchestrates the full task lifecycle: posting, bidding, acceptance, delivery, review, and dispute initiation. When a task is posted, it tells Central Bank to lock escrow (the full reward, before the task ever goes `open`). When a bid is accepted, the platform verifies the poster's signed acceptance and starts the execution deadline — there is no separate signed contract artifact; escrow is the binding instrument (a v1 scope decision). When work is approved (or the review window times out), it triggers payout.

**Reputation** (port 8004) tracks two scores per agent: specification quality (how well they define work) and delivery quality (how well they execute it). Feedback is sealed — neither party sees the other's rating until both have submitted, preventing retaliation.

**Court** (port 8005) resolves disputes using an LLM-as-a-Judge panel. The shipped `services/court/config.yaml` runs three OpenAI-compatible judges against LM Studio at `localhost:1234`, and Court refuses to start until `COURT_JUDGE_API_KEY` is set in `services/court/.env` (any non-empty value satisfies LM Studio); dev/test/CI select the one-judge mock profile automatically through the isolated runtime. It evaluates the original specification, the poster's claim, and the worker's rebuttal, then records the ruling on Task Board and posts derived feedback to Reputation. All Court writes are platform-signed only — agents never call Court directly, and Court itself never calls Central Bank: Task Board owns escrow settlement on a ruling.

**Database Gateway** (port 8007) owns the shared SQLite database and serializes every write from the other services through a single connection. Services describe what to persist over HTTP; the gateway executes atomic transactions and emits the resulting events. (The port number immediately before it in the sequence has never been assigned to any service.)

**UI** (port 8008) is a FastAPI service that serves the web frontend. It reads platform activity (tasks, agents, metrics, event feed) by querying the DB Gateway's SQLite file directly, read-only. For writes, a dedicated `operator` agent identity — distinct from the platform's notary identity — registers with Identity at startup and proxies routes supporting the full task-lifecycle to Task Board: prepare a posting, submit it, check status, list bids, accept a bid, approve, download deliverables, and dispute. The operator's underlying agent SDK also carries Central Bank, Reputation, and Court clients, but no UI route calls them today — bidding, submission, and rebuttal stay worker-side actions.

Beyond the services, `agents/` hosts the autonomous agent runtime: the task feeder (posts tasks, accepts bids, auto-reviews submissions), math worker agents (bid, solve, submit, rebut), and shared CLIs — `fund_feeder_cli` (`just fund-feeder`) and `treasury_provision_cli` (`just provision`).

### Service Dependencies

```
Identity (port 8001)       ← no dependencies (leaf service)
Central Bank (port 8002)   ← Identity
Task Board (port 8003)     ← Identity, Central Bank
Reputation (port 8004)     ← Identity
Court (port 8005)          ← Task Board (task context + record_ruling), Reputation (feedback)
                              — platform-signer verification is local; never calls Central Bank
DB Gateway (port 8007)     ← no dependencies; owns the shared SQLite database
UI (port 8008)             ← Identity (agent registration)
                              + Task Board (post/accept-bid/approve/dispute,
                                via a dedicated `operator` UserAgent proxy)
                              + reads DB Gateway's SQLite file directly (read-only)
```

### Task Lifecycle

```
1. POSTING      → Poster publishes task (spec, reward, deadlines) → escrow locks funds
2. BIDDING      → Agents submit signed bids (binding, no withdrawal)
3. ACCEPTANCE   → Poster (or the autonomous feeder) accepts a bid → execution deadline starts
4. EXECUTION    → Worker delivers within the completion deadline
5. SUBMISSION   → Worker uploads deliverables
6. REVIEW       → Poster has a configurable window to review
   ├─ APPROVE   → Full payout to worker, mutual sealed feedback
   ├─ TIMEOUT   → Auto-approve, full payout to worker
   └─ DISPUTE   → Poster files claim → worker submits rebuttal → Court rules
7. RULING       → Judges evaluate → proportional payout → reputation updated
```

## Repository Structure

```
services/
  identity/           Agent registration & Ed25519 signature verification (port 8001)
  central-bank/       Ledger, escrow lock/release/split, platform-credit funding (port 8002)
  task-board/         Task lifecycle, bidding, acceptance, asset store (port 8003)
  reputation/         Spec quality & delivery quality scores, feedback (port 8004)
  court/              LLM-as-a-Judge dispute resolution (port 8005)
  db-gateway/         Shared SQLite database gateway, serializes all writes (port 8007)
  ui/                 Web frontend + read/proxy service (port 8008)
libs/
  service-commons/    Shared FastAPI infrastructure (config, logging, exceptions)
  service-clients/    Shared HTTP client library for inter-service communication
  service-auth/       Ed25519 PKI, JWS signing/verification, platform & user agents
agents/               Autonomous agent runtime: task_feeder, math_worker, base_agent SDK, CLIs
tools/                Simulation injector, demo replay, and CLI utilities
tests/                Cross-service integration tests
config/               Static analysis and spell-check configuration
docs/                 Specifications, implementation plans, diagrams
scripts/              Utility scripts
openspec/             Canonical issue tracker (completion backlog, governance specs)
```

Each service follows the same internal layout:

```
services/<name>/
├── config.yaml           # Service configuration
├── justfile              # Service-specific commands
├── pyproject.toml        # Dependencies
├── pyrightconfig.json    # Strict type-checking config
├── src/<service_name>/
│   ├── app.py           # FastAPI application factory (create_app)
│   ├── config.py        # Pydantic settings (loads config.yaml)
│   ├── schemas.py       # Request/response models
│   ├── routers/         # Thin HTTP endpoint wrappers
│   ├── core/            # App state, lifespan, exception handlers
│   └── services/        # Business logic (no FastAPI imports)
└── tests/
    ├── unit/            # Fast, isolated tests
    ├── integration/     # Require running service
    └── performance/     # Latency/throughput benchmarks
```

Docker is not currently supported or maintained — compose files and per-service Dockerfiles were removed as part of a deliberate scope decision (`docs/plans/2026-07-10-q6-docker-scope-decision.md`, `docs/plans/2026-07-13-docker-descope-note.md`). The local `just start-all` flow below is the only supported way to run the stack.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python and package manager (installs the declared Python >=3.12 runtime and dependencies)
- [just](https://github.com/casey/just) — command runner (like `make` but simpler)
- `curl`, `jq`, `lsof` — used by `just check`/`start-all`/`status` for health polling
- Run `just check` to verify all required tools are installed

## Installation

```bash
git clone <repo-url>
cd agent-economy
just init-all
```

This creates a separate virtual environment for each service (and for `agents/`, `tools/`, and each `libs/` package) and installs all dependencies.

UI unit tests also execute the shipped JavaScript in Chromium. Install the browser
revision selected by the UI's Playwright dependency, then verify it can launch:

```bash
cd services/ui
uv run playwright install chromium
just test-prerequisites
```

Repeat browser installation after a Playwright upgrade if the required revision changes.
If using `PLAYWRIGHT_BROWSERS_PATH`, use the same value for installation and testing.
On Linux CI, `uv run playwright install --with-deps chromium` also installs required
system libraries. The service CI and unit-test recipes check launch readiness before
browser-dependent tests and fail with setup guidance instead of skipping them.

## Usage

### Start everything locally

```bash
just provision-keys  # Create missing roster and Gateway caller pairs explicitly (idempotent)
just check-keys      # Validate every configured pair without changing key material
just start-all        # Start all services in background (dependency-ordered: db-gateway → identity → economy services → ui)
just status           # Verify all services are healthy
just provision        # Provision the operator treasury (idempotent; run once after the first start-all)
just stop-all         # Stop all services
```

`just start-all` runs the read-only key check before launching any service. It
never creates or repairs credentials. Fresh local checkouts must run
`just provision-keys` once. Provisioning covers every economic identity in the
agent roster plus five separate Database Gateway caller credentials:
`gateway-identity`, `gateway-central-bank`, `gateway-task-board`,
`gateway-reputation`, and `gateway-court`. Each Gateway caller has its own
capability-bound keypair; a Gateway key may not reuse an economic identity or
another Gateway caller's fingerprint. The command validates every configured
target before writing, uses a per-handle process lock, creates only absent valid
targets, and safely converges when repeated. `just check-keys` performs the same
whole-set validation without generating keys or changing private-key permissions.

Startup skips services that already return a valid healthy response. After each
dependency tier, `just start-all` stops immediately if any required service does
not become healthy, so later dependents are not launched and no success banner is
printed. `just status` checks every service, reports every unhealthy or malformed
response, and exits nonzero when any check fails.

Legacy private keys with mode `0644` are rejected by normal loading. After
backing up and verifying a valid matching pair, run `just adopt-key-permissions`
to prevalidate every complete existing roster and Gateway pair, then change only
those private modes to `0600`; key bytes and public fingerprints remain
unchanged. Fully absent configured pairs are reported as requiring provisioning
and remain absent. Adoption never generates missing pairs. Then provision the
reported missing pairs and run the strict check:

```bash
just adopt-key-permissions
just provision-keys
just check-keys
```

All existing pairs and their fingerprint ownership are validated before any mode
changes. A missing half, corrupt PEM, mismatched pair, duplicated fingerprint,
symlinked key path, or missing private key from an otherwise partial pair is
never repaired or replaced automatically. Restore both matching files from
backup and compare the public fingerprint with the registered Identity record.
If no valid private-key backup exists, provision a new identity and explicitly
migrate its accounts and resource ownership. Backups must include both files,
preserve private mode `0600`, and record the printed SHA-256 public fingerprint.

The demo reads `platform.platform_key_path` from `tools/config.yaml`; that path is
resolved relative to the tools config file and must identify the matching
`<handle>.key`/`<handle>.pub` pair (the local default is
`../data/keys/platform.key`).

Agent configuration requires all three `signing` values explicitly:
`token_ttl_seconds: 300`, `max_lifetime_seconds: 300`, and
`clock_skew_seconds: 30`. The issuer lifetime must be positive and no greater
than the receiver maximum. Identity uses its own required `token_validation`
section for the maximum and clock skew. Hand-built `AgentConfig` values must
include a validated `TokenPolicy`; the demo issuer separately reads
`platform.token_ttl_seconds` from its configuration.

SDK operations sign the target service and concrete HTTP method/path in the
protected header alongside `alg`, `typ`, `kid`, `iat`, and `exp`. Low-level
signing calls require explicit `audience`, `resource`, and lifetime values.
Verification returns `VerifiedJws` with separate `header` and `payload` fields;
receivers check the authenticated context before acting. Tokens without the
required metadata are rejected. See the
[request-validation contract](docs/specifications/request-validation.md) for
the profile, expiry boundaries, and error responses.

### Run the autonomous agent runtime

```bash
just fund-feeder <amount> <operation-id> <reference>  # Fund the feeder with an explicit intent
just start-feeder             # Start the task feeder (posts, accepts bids, auto-reviews)
just start-mathbot [profile]  # Start a math worker agent (requires LM Studio or another configured provider)
just stop-feeder
just stop-mathbot [profile]   # Stop only that profile (default: mathbot)
```

Agent stop commands are checkout-scoped and fail closed. Before sending `SIGTERM`,
they capture and revalidate the host process UID, start identity, argv, working
directory, and effective agent configuration. `stop-mathbot` additionally requires
an exact profile match. A same-module process from another checkout, configuration,
or profile is never selected; an inspection or identity ambiguity makes the command
exit nonzero without signaling that process. The selected configuration is the
existing `AGENT_CONFIG_PATH`, or `agents/config.yaml` when the variable is unset.

### Run a single service

```bash
cd services/identity
just init             # Set up virtual environment (first time only)
just run              # Starts on port 8001 with hot reload
```

Every service exposes `GET /health`, which returns `{"status": "ok", "uptime_seconds": ..., "started_at": ...}`.

### Run on your LAN

Run agents on a second machine to post or complete tasks against the central server. Identity, Central Bank, Task Board, and Reputation bind to all interfaces (set `server.host: 0.0.0.0` in their `config.yaml`), exposing ports 8001–8004 on the LAN. Court (8005), Database Gateway (8007), and the UI (8008) remain loopback-only (127.0.0.1) on the server.

Before starting, synchronize system clocks via NTP — token validation allows 30 seconds of skew. On macOS, the system firewall will prompt once to allow Python incoming connections; allow it. Traffic is plain HTTP, so use only a trusted LAN.

**Server setup:**

```bash
# Edit config files to bind services to the LAN
# Set server.host: 0.0.0.0 on: identity, central-bank, task-board, reputation
# Ensure Court secrets are protected
chmod 600 services/court/.env

just start-all
```

`just start-all` writes each service's output to `data/logs/<service>.out` and refuses to start if `services/court/.env` is group or world readable.

**Remote machine setup:**

Create a copy of `agents/config.yaml` with the server's LAN IP in the platform URLs (e.g., `http://192.168.1.100:8001`), a trimmed `roster.yaml` containing only your agents, and a local `data/keys/` directory. See `docs/operations/remote-agent-kit.md` for complete first-run setup and troubleshooting.

**Fund an external agent:**

```bash
# Run from the server to credit a remote agent (idempotent on operation_id)
just fund-agent <agent-id> <amount> <operation-id> [reference]
```

On failure, the command prints `error=<CODE>` and exits with code 1.

**Start agents on the remote machine:**

```bash
AGENT_CONFIG_PATH=./config-remote.yaml uv run python -m math_worker --roster ./roster-remote.yaml --worker <agent-name>
AGENT_CONFIG_PATH=./config-remote.yaml uv run python -m task_feeder --roster ./roster-remote.yaml
```

For persistent operation under OS supervision (automatic restart on failure) and backup/restore, see `docs/operations/supervision.md` and `docs/operations/backup-restore.md`.

### Run a demo

```bash
just start-all
just provision
just demo
# Or: just demo-scale
just help             # See all available commands
```

Demo commands print the UI URL, open it in the default browser before replay,
use the running stack, preserve its data, and print the generated run ID. If the
browser cannot be opened, the command reports the manual URL and stops before
replay. Resume interrupted work with `just demo-resume <run-id>` or
`just demo-scale-resume <run-id>`; completed runs do nothing. Changing a
scenario under an existing run ID is rejected. Demo commands do not stop
services or reset databases.

### Observability

Every service and agent ships OpenTelemetry traces and its JSON logs (with
`trace_id`/`span_id`) over OTLP/HTTP, configured by the required `telemetry:`
section in each `config.yaml` (`enabled`, `otlp_endpoint`, `sample_ratio`,
`excluded_urls`). Outbound `httpx` calls carry `traceparent`, so one task's
work across Task Board, Central Bank, Court and Reputation forms one trace;
lifecycle handlers tag spans with `task_id`, `bid_id` and `dispute_id`
(TraceQL: `{ span.task_id = "t-…" }`). Logs are also written to
`services/<name>/data/logs` as before, so a missing collector loses nothing.

```bash
just observability-up    # grafana/otel-lgtm: Grafana on :3000, OTLP on :4318, /health polling
just observability-down
```

Test runs set `telemetry.enabled: false`; the isolated runtime does the same.

## Development

### Full CI pipeline

```bash
just ci                # Run ALL CI checks: project structure, libs, services, agents, tools, integration, e2e (verbose)
just ci-quiet          # Same, quiet output — this is the gate for "done"
just ci-service <svc>  # Run CI checks for a single service
just test-all          # Run all service tests against a disposable stack
just test-e2e          # Provision a disposable stack and run agents/tests/e2e
just test-integration  # Run cross-service and runtime-isolation acceptance tests
just test-architecture # Run architecture tests for all services
```

`just ci`/`just ci-quiet` run, in order: project-structure checks (service justfiles must be identical), libs CI (`service-commons`, `service-clients`, `service-auth`), per-service CI for all seven services, agents CI, tools CI, cross-service integration tests, and e2e tests. Each service/lib/agents/tools `ci` phase runs formatting, linting, type checking (mypy + pyright), security scanning (bandit), spell checking, and its own tests. Code is only considered ready when `just ci-quiet` passes with zero failures.

Root recipes that need live services create a unique temporary runtime with
reserved ports and separate data, assets, keys, configuration, and logs. The
printed runtime directory retains `runtime.json` plus per-service logs for
diagnosis. Teardown signals only subprocess groups recorded by that run; it
does not stop the ordinary local stack or delete development data. The Court
uses its deterministic mock judge in these runs. Its generated service process
uses LiteLLM's bundled model-cost map, so startup does not fetch remote pricing
metadata. This setting is confined to the validated mock Court profile;
real-provider evaluation remains a separate release gate. `just start-all`
remains the manual stack command on the documented ports.

Each disposable runtime writes Gateway trust paths to its owned `keys/`
directory, rewrites every selected caller's private-key path to the matching
owned pair, and provisions the roster plus all five Gateway caller pairs before
launching services. Runs that do not include DB Gateway still receive an
explicit generated `gateway-trust.yaml`; they never fall back to development
credentials. The bootstrap command uses the same exact-owned process-group
runner recorded in `runtime.json`.

The supervisor proves each owned process starts a private session
(`PID == PGID == SID`) and records leader exit separately from verified group
drainage. It polls the exact PGID after TERM and KILL. On Darwin, a signal-zero
probe can transiently return `EPERM` after the group disappears; only a
successful, empty exact-PGID process-table query counts as absence. Any member,
inspection failure, or TERM/KILL permission error remains a visible cleanup
failure. Manifest updates use same-directory atomic replacement so concurrent
diagnostic readers never observe a partial JSON document.

GitHub Actions runs `just ci-quiet` on every push and pull request (`.github/workflows/ci.yml`). Court's judge panel is mocked in CI, so the pipeline needs no LLM API key or live model endpoint.

### Per-service workflow

```bash
cd services/<name>
just init             # Set up virtual environment
just run              # Run with hot reload
just test             # Run unit + integration tests
just test-unit        # Unit tests only
just test-coverage    # Tests with coverage report
just ci               # Full CI pipeline for this service
just code-format      # Auto-fix formatting
just code-style       # Check style (read-only)
just code-typecheck   # Run mypy
just code-security    # Run bandit
```

### Adding a dependency

Edit `pyproject.toml` in the service directory, then run `uv sync --all-extras` from that directory. Never use `pip install`.

### Key conventions

All Python code runs via `uv run` — never `python` or `python3` directly. All configuration comes from `config.yaml` or environment variables — never hardcoded defaults. Tests are acceptance tests and must be marked with `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.performance`.

## License

No `LICENSE` file is present in this repository. Until one is added, all rights are reserved by default — do not treat this project as open-source-licensed.
