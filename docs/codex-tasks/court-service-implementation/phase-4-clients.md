# Phase 4 — HTTP Clients and Platform Signer

## Working Directory

```
services/court/
```

All files in this phase live under `src/court_service/services/`. These are infrastructure components — no business logic. Each client wraps a single upstream service and maps transport errors to `ServiceError` exceptions.

---

## File 1: `src/court_service/services/identity_client.py`

Follow the Central Bank's `IdentityClient` pattern closely.

### Constructor

- `base_url: str`, `verify_jws_path: str`
- Creates `httpx.AsyncClient(base_url=..., timeout=10.0)`

### Method: `verify_jws(token: str) -> dict[str, Any]`

- POST to `verify_jws_path` with `{"token": token}`
- On `httpx.HTTPError` → raise `ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)`
- On 200 with `valid: true` → return full response body `{"valid": True, "agent_id": "...", "payload": {...}}`
- On 200 with `valid: false` → raise `ServiceError("FORBIDDEN", "JWS signature verification failed", 403)`
- On non-200 → propagate error body if parseable, otherwise raise `ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)`

### Method: `close()`

- `await self._client.aclose()`

### Key Difference from Central Bank

The Court's IdentityClient does NOT need `get_agent()` — it only verifies JWS tokens. The Court never looks up agents directly.

---

## File 2: `src/court_service/services/platform_signer.py`

This is a NEW component with no direct Central Bank equivalent. The Court signs outgoing requests to downstream services using the platform's Ed25519 private key.

### Constructor

- `private_key_path: str`, `platform_agent_id: str`
- Load Ed25519 private key from file at startup:
  ```python
  from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
  from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

  with open(private_key_path, "rb") as f:
      key_data = f.read()
  # Support both PEM and raw 32-byte formats
  ```
- Convert to JWK format for `joserfc`:
  ```python
  from joserfc.jwk import OKPKey
  raw_private = private_key.private_bytes_raw()
  raw_public = private_key.public_key().public_bytes_raw()
  jwk_dict = {
      "kty": "OKP", "crv": "Ed25519",
      "d": base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
      "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
  }
  self._key = OKPKey.import_key(jwk_dict)
  self._agent_id = platform_agent_id
  ```

### Method: `sign(payload: dict) -> str`

- Creates JWS compact token:
  ```python
  from joserfc import jws
  protected = {"alg": "EdDSA", "kid": self._agent_id}
  payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
  return jws.serialize_compact(protected, payload_bytes, self._key, algorithms=["EdDSA"])
  ```
- Returns the compact JWS token string

### Design Note

The PlatformSigner is synchronous — key loading happens once at startup, signing is CPU-bound but fast (< 1ms). No need for async.

---

## File 3: `src/court_service/services/task_board_client.py`

HTTP client for Task Board interactions. Two operations.

### Constructor

- `base_url: str`, `signer: PlatformSigner`
- Creates `httpx.AsyncClient(base_url=..., timeout=10.0)`

### Method: `get_task(task_id: str) -> dict[str, Any]`

Used during dispute filing to fetch task data (spec, deliverables, title, reward, parties).

- GET to `/tasks/{task_id}`
- On `httpx.HTTPError` → raise `ServiceError("TASK_BOARD_UNAVAILABLE", ..., 502)`
- On 200 → return response body
- On 404 → raise `ServiceError("TASK_NOT_FOUND", ..., 404)`
- On other errors → raise `ServiceError("TASK_BOARD_UNAVAILABLE", ..., 502)`

### Method: `record_ruling(task_id: str, ruling_payload: dict) -> None`

Called after ruling to notify Task Board. Platform-signed.

- Sign the ruling payload with `self._signer.sign({...})`
- POST to `/tasks/{task_id}/ruling` with `{"token": signed_token}`
- On `httpx.HTTPError` → raise `ServiceError("TASK_BOARD_UNAVAILABLE", ..., 502)`
- On non-2xx → raise `ServiceError("TASK_BOARD_UNAVAILABLE", ..., 502)`

### Method: `close()`

---

## File 4: `src/court_service/services/central_bank_client.py`

HTTP client for Central Bank escrow operations. One operation.

### Constructor

- `base_url: str`, `signer: PlatformSigner`
- Creates `httpx.AsyncClient(base_url=..., timeout=10.0)`

### Method: `split_escrow(escrow_id: str, worker_account_id: str, poster_account_id: str, worker_pct: int) -> dict[str, Any]`

- Sign the payload: `{"action": "escrow_split", "escrow_id": escrow_id, "worker_account_id": worker_account_id, "poster_account_id": poster_account_id, "worker_pct": worker_pct}`
- POST to `/escrow/{escrow_id}/split` with `{"token": signed_token}`
- On `httpx.HTTPError` → raise `ServiceError("CENTRAL_BANK_UNAVAILABLE", ..., 502)`
- On 200 → return response body
- On non-200 → raise `ServiceError("CENTRAL_BANK_UNAVAILABLE", ..., 502)`

### Method: `close()`

---

## File 5: `src/court_service/services/reputation_client.py`

HTTP client for Reputation feedback submission. One operation.

### Constructor

- `base_url: str`, `signer: PlatformSigner`
- Creates `httpx.AsyncClient(base_url=..., timeout=10.0)`

### Method: `submit_feedback(feedback_payload: dict) -> dict[str, Any]`

Called twice per ruling: once for spec quality (poster), once for delivery quality (worker).

- Sign the payload with `self._signer.sign(feedback_payload)`
- POST to `/feedback` with `{"token": signed_token}`
- On `httpx.HTTPError` → raise `ServiceError("REPUTATION_SERVICE_UNAVAILABLE", ..., 502)`
- On 201 → return response body
- On non-201 → raise `ServiceError("REPUTATION_SERVICE_UNAVAILABLE", ..., 502)`

### Method: `close()`

---

## General Patterns for All Clients

1. Constructor stores the base URL and creates `httpx.AsyncClient(base_url=..., timeout=10.0)`
2. Transport errors (`httpx.HTTPError`) always map to the service-specific `502` error code
3. `close()` calls `await self._client.aclose()`
4. Outgoing clients (Task Board, Central Bank, Reputation) take a `PlatformSigner` and sign payloads before sending
5. No retry logic — if a call fails, it fails. The ruling orchestrator handles rollback.

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run python -c "from court_service.services.identity_client import IdentityClient; print('OK')"
uv run python -c "from court_service.services.platform_signer import PlatformSigner; print('OK')"
```
