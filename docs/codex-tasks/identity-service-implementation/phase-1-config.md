# Phase 1 — Dependencies and Configuration

## Working Directory

All commands run from `services/identity/`.

## Step 1.1: Add cryptography dependency

Edit `pyproject.toml`. Change the `dependencies` list to:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "pydantic>=2.10.0",
    "service-commons",
    "cryptography>=44.0.0",
]
```

Only add the `"cryptography>=44.0.0"` line. Do not change anything else.

## Step 1.2: Extend config.yaml

Replace the **entire** contents of `config.yaml` with:

```yaml
# Identity Service Configuration
# Environment variable overrides use prefix: IDENTITY__

service:
  name: "identity"
  version: "0.1.0"

server:
  host: "0.0.0.0"
  port: 8001
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

database:
  path: "data/identity.db"

crypto:
  algorithm: "ed25519"
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64

request:
  max_body_size: 1572864
```

The `max_body_size` of 1572864 bytes (1.5 MB) allows VER-14's 1 MB payload (~1.4 MB as JSON) but rejects REG-18's ~2 MB payload.

## Step 1.3: Install dependencies

```bash
cd services/identity && just init
```

## Verification

```bash
cd services/identity && uv run python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; print('OK')"
```

Must print `OK`.
