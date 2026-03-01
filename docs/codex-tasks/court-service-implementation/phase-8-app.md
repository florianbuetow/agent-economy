# Phase 8 — Application Assembly

## Working Directory

```
services/court/
```

---

## File 1: `src/court_service/app.py`

Follow the Central Bank `app.py` pattern exactly.

### `create_app() -> FastAPI`

1. Load settings: `get_settings()`
2. Create FastAPI app: `FastAPI(title=..., version=..., lifespan=lifespan)`
3. Register exception handlers: `register_exception_handlers(app)`
4. Include routers:
   - `health.router` with `tags=["Operations"]`
   - `disputes.router` with `tags=["Disputes"]`
5. Add middleware: `RequestValidationMiddleware` with `max_body_size` from settings
6. Return app

Middleware is added LAST (after routers) — this is important because ASGI middleware wraps the app, so it processes requests before routers.

---

## Verification

Start the service and smoke-test the health endpoint:

```bash
just run
```

In another terminal:

```bash
curl -s http://localhost:8005/health | python3 -m json.tool
```

Expected response:

```json
{
    "status": "ok",
    "uptime_seconds": ...,
    "started_at": "...",
    "total_disputes": 0,
    "active_disputes": 0
}
```

Also verify 405 on wrong methods:

```bash
curl -s -X POST http://localhost:8005/health | python3 -m json.tool
```

Expected: `{"error": "METHOD_NOT_ALLOWED", ...}`

Stop the service when done.

### Troubleshooting

- **Import errors on startup**: Ensure all `__init__.py` files are in place and exporting correctly
- **Config validation fails**: Check that `config.yaml` has all required sections and that `platform.agent_id` and `platform.private_key_path` are set
- **Database directory error**: The lifespan creates the parent directory, but the path must be writable
- **PlatformSigner fails**: The `private_key_path` must point to a valid Ed25519 private key file. For local development, generate one:
  ```bash
  uv run python -c "
  from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
  from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
  key = Ed25519PrivateKey.generate()
  with open('data/platform.key', 'wb') as f:
      f.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
  print('Key written to data/platform.key')
  "
  ```
  Then set `platform.private_key_path: "data/platform.key"` in `config.yaml`.
