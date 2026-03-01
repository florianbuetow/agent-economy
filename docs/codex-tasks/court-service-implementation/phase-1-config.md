# Phase 1 — Dependencies and Configuration

## Working Directory

```
services/court/
```

---

## File 1: `pyproject.toml`

Add these runtime dependencies to the existing `dependencies` list:

```
httpx>=0.28.0
cryptography>=44.0.0
joserfc>=1.0.0
litellm>=1.0.0
pyyaml>=6.0.0
```

- `httpx` — async HTTP client for all inter-service communication (Identity, Task Board, Central Bank, Reputation)
- `cryptography` — Ed25519 key loading for PlatformSigner
- `joserfc` — JWS token creation for outgoing platform-signed requests
- `litellm` — LLM provider abstraction for the judge panel
- `pyyaml` — YAML config file loading (if not already present via service-commons)

The `service-commons` editable path dependency should already be present from scaffolding.

---

## File 2: `config.yaml`

Replace the current minimal config with the full court-specific configuration. All sections are required — the service must fail to start if any are missing.

### Required Sections

**`service`** — name: `"court"`, version: `"0.1.0"`

**`server`** — host, port (8005), log_level

**`logging`** — level, format

**`database`** — `path: "data/court.db"`

**`identity`** — Identity service connection:
- `base_url: "http://localhost:8001"`
- `verify_jws_path: "/agents/verify-jws"`

**`task_board`** — Task Board service connection:
- `base_url: "http://localhost:8003"`

**`central_bank`** — Central Bank service connection:
- `base_url: "http://localhost:8002"`

**`reputation`** — Reputation service connection:
- `base_url: "http://localhost:8004"`

**`platform`** — Platform agent identity:
- `agent_id: ""` — must be non-empty at runtime; empty placeholder triggers validation error
- `private_key_path: ""` — path to Ed25519 private key file for signing outgoing requests

**`disputes`** — Dispute configuration:
- `rebuttal_deadline_seconds: 86400` — 24 hours

**`judges`** — Judge panel configuration:
- `panel_size: 1` — must be odd, >= 1, and equal to `len(judges)`
- `judges:` — list of judge objects, each with `id`, `model`, `temperature`

Example judge entry:
```yaml
judges:
  panel_size: 1
  judges:
    - id: "judge-0"
      model: "gpt-4o"
      temperature: 0.3
```

**`request`** — Request validation:
- `max_body_size: 1048576` — 1 MB

### Reference

Follow the same YAML formatting conventions as `services/central-bank/config.yaml` — two-space indent, quoted string values, comment at the top with the environment variable prefix (`COURT__`).

---

## Verification

```bash
just init
uv run python -c "import httpx; import cryptography; import joserfc; import litellm; print('OK')"
```

Expected: all imports succeed, no errors. The lock file regenerates cleanly.
