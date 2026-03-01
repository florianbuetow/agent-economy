# Phase 1 — Dependencies and Configuration

## Working Directory

```
services/reputation/
```

---

## File 1: `pyproject.toml`

Add this runtime dependency to the existing `dependencies` list:

```
httpx>=0.28.0
```

- `httpx` — async HTTP client for Identity service communication (JWS verification)

The `service-commons` editable path dependency should already be present from scaffolding. `fastapi`, `uvicorn`, and `pydantic` should also already be present.

---

## File 2: `config.yaml`

Replace the current minimal config with the full reputation-specific configuration. All sections are required — the service must fail to start if any are missing.

### Required Sections

**`service`** — name: `"reputation"`, version: `"0.1.0"`

**`server`** — host: `"0.0.0.0"`, port: `8004`, log_level: `"info"`

**`logging`** — level: `"INFO"`, format: `"json"`

**`identity`** — Identity service connection:
- `base_url: "http://localhost:8001"`
- `verify_jws_path: "/agents/verify-jws"`
- `timeout_seconds: 10`

**`request`** — Request validation:
- `max_body_size: 1048576` — 1 MB

**`database`** — SQLite persistence:
- `path: "data/reputation.db"`

**`feedback`** — Feedback submission rules:
- `reveal_timeout_seconds: 86400` — 24 hours before sealed feedback auto-reveals
- `max_comment_length: 256` — maximum comment length in Unicode codepoints

### Reference

Follow the same YAML formatting conventions as `services/central-bank/config.yaml` — two-space indent, quoted string values, comment at the top with the environment variable prefix (`REPUTATION__`).

### Key Differences from Central Bank

- No `platform` section — the Reputation service has no platform-only operations
- No `central_bank`, `task_board`, or `reputation` sections — no outgoing service calls except to Identity
- Has `feedback` section with domain-specific settings (reveal timeout, comment length)
- `identity` section includes `timeout_seconds` (configurable, not hardcoded)

---

## Verification

```bash
just init
uv run python -c "import httpx; print('OK')"
```

Expected: import succeeds, no errors. The lock file regenerates cleanly.
