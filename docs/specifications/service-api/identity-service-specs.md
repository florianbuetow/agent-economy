# Identity & PKI Service — API Specification

## Purpose

The Identity service is the trust anchor for the Agent Task Economy. It binds agent identities to public keys and provides JWS signature verification for all other services. It is architecturally a leaf/root-of-trust service: it calls no other domain service (central-bank, task-board, reputation, court) — its only outbound dependency is the DB Gateway, which owns the shared database.

## Core Principles

- **Identity = public key.** An agent's unique identity is its public key. Display names are metadata, not identifiers.
- **Private keys never leave the agent.** The service only ever stores public keys. Agents generate keypairs locally and register only the public half.
- **Proof of identity = a valid Ed25519/EdDSA signature.** An agent proves it is who it claims to be by signing with its private key. Other services verify agent-signed operations by presenting a compact JWS token to `POST /agents/verify-jws`.
- **Replay protection beyond token expiry is the caller's responsibility.** Identity enforces the `exp` claim on JWS tokens that carry one (see Token Expiry below), but does not otherwise interpret payload contents or enforce nonces.

## Service Dependencies

```
Identity (port 8001)
  └── DB Gateway (port 8007) — agent record persistence (writes + reads /identity/*)
```

Identity is a **leaf service** from every other service's perspective. Central-bank, task-board, and reputation all call *into* Identity (`POST /agents/verify-jws`, `GET /agents/{agent_id}`) to verify agent-signed operations; Identity never calls back out to any of them, and never calls Court. Its only outbound HTTP dependency is the DB Gateway.

## Key Algorithm

**Ed25519 / EdDSA** (RFC 8032)

- 32-byte public keys, 64-byte signatures
- 128-bit security level
- Deterministic signatures — no nonce required
- The algorithm is **hardcoded**, not configurable. Registration validates keys as Ed25519 (`cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PublicKey`), and JWS verification is pinned to `alg=EdDSA`. There is no `crypto.algorithm` config key — `config.yaml`'s `crypto` section only carries `public_key_prefix`, `public_key_bytes`, and `signature_bytes`, and the config schema rejects unknown keys (`extra="forbid"`). A token or key claiming any other algorithm is rejected.

## Data Model

### Agent Record

| Field           | Type     | Description                                       |
|-----------------|----------|---------------------------------------------------|
| `agent_id`      | string   | System-generated unique identifier (`a-<uuid4>`)  |
| `name`          | string   | Display name (not unique — multiple agents may share a name) |
| `public_key`    | string   | Ed25519 public key, format: `ed25519:<base64>`    |
| `registered_at` | datetime | ISO 8601 timestamp of registration                |

### Persistence

Identity holds no local database. Agent records are persisted through the **DB Gateway** (port 8007), the single service that owns the shared SQLite database. Identity is a domain service that reads and writes agent records over HTTP via a gateway client; it never opens a database file directly. Registration, lookup, listing, and counting are all gateway round-trips.

Earlier revisions of this service read a local SQLite path from a `database.path` config key. That key is gone: `identity_service.config.Settings` has no `database` section, `config.yaml` carries no `database:` block, and the config schema rejects unknown top-level keys (`extra="forbid"`). The only storage-related setting today is `db_gateway.url`/`db_gateway.timeout_seconds`.

### Uniqueness Constraint

The **public key** is the uniqueness constraint, enforced by the DB Gateway. If two concurrent registration requests arrive with the same public key, only one succeeds; the other receives `409 public_key_exists`.

Agent names have no uniqueness constraint.

---

## Endpoints

### POST /agents/register

Register a new agent identity. Open and self-service — no authentication is required to call this endpoint.

**Request:**
```json
{
  "name": "Alice",
  "public_key": "ed25519:<base64>"
}
```

**Response (201 Created):**
```json
{
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "name": "Alice",
  "public_key": "ed25519:<base64>",
  "registered_at": "2026-02-20T10:30:00Z"
}
```

**Errors:**

| Status | Code                  | Description                          |
|--------|-----------------------|---------------------------------------|
| 400    | `missing_field`       | Required field (`name` or `public_key`) absent or `null` |
| 400    | `invalid_field_type`  | A required field is not a string      |
| 400    | `invalid_name`        | `name` is empty or whitespace-only    |
| 400    | `invalid_public_key`  | Key does not start with the configured prefix, is not valid base64, decodes to the wrong length, is all-zero, or is not a valid Ed25519 point |
| 400    | `invalid_json`        | Request body is malformed JSON        |
| 409    | `public_key_exists`   | This public key is already registered |
| 413    | `payload_too_large`   | Request body exceeds the configured max size |
| 415    | `unsupported_media_type` | `Content-Type` is not `application/json` |

**Concurrency:** Uniqueness is enforced by the DB Gateway, so concurrent registrations with the same key result in one success and one 409.

---

### POST /agents/verify-jws

Verify a compact JWS token and return its decoded payload. This is the live, documented signature-verification endpoint — it is the one called by other services (central-bank, task-board, reputation) through the shared `IdentityClient` (`libs/service-clients/src/service_clients/identity.py`) whenever an agent-signed operation needs verifying.

**Request:**
```json
{
  "token": "<base64url(header)>.<base64url(payload)>.<base64url(signature)>"
}
```

The `token` field is a compact JWS: `header.payload.signature`, all three parts base64url-encoded. The protected header must include `alg: "EdDSA"` and `kid: "<agent_id>"`. The header may optionally include `iat` (issued-at, Unix seconds) and `exp` (expiry, Unix seconds) — see Token Expiry below. The payload is an arbitrary JSON object; the calling service is responsible for interpreting its contents (e.g. checking an `action` field).

**Verification procedure:**
1. Parse the token into its three parts; reject if not exactly three dot-separated segments
2. Decode and parse the protected header; require `alg == "EdDSA"` and a string `kid`
3. Look up the agent's public key by `kid` (the `agent_id`)
4. Verify the EdDSA signature over `header_b64.payload_b64` against the stored public key
5. If the signature is authentic and the header carries an `exp` claim, reject the token if `exp` is in the past (see Token Expiry)
6. Decode the payload as JSON and return it

**Response (200 OK — valid):**
```json
{
  "valid": true,
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "payload": { "action": "escrow_lock", "amount": 10 }
}
```

**Response (200 OK — signature mismatch, not an error):**
```json
{
  "valid": false,
  "reason": "signature mismatch"
}
```

**Errors:**

| Status | Code             | Description                                                        |
|--------|------------------|----------------------------------------------------------------------|
| 400    | `missing_field`  | `token` is absent or `null`                                          |
| 400    | `invalid_field_type` | `token` is not a string                                          |
| 400    | `invalid_jws`    | Token is not a valid 3-part compact JWS, header is unparsable, `alg` is not `EdDSA`, `kid` is missing/non-string, or the decoded payload is not a JSON object |
| 400    | `invalid_json`   | Request body is malformed JSON                                       |
| 401    | `token_expired`  | The signature is authentic but the header `exp` claim is in the past |
| 404    | `agent_not_found`| No agent with the `kid` from the token header                        |
| 415    | `unsupported_media_type` | `Content-Type` is not `application/json`                      |

#### Token Expiry (`iat` / `exp`)

Tokens carry optional `iat` (issued-at) and `exp` (expiry) claims in the **protected JWS header** — not the payload — so that signed operation payloads stay byte-identical regardless of whether expiry is stamped. A signer (`libs/service-auth/src/service_auth/signing.py::create_jws`) stamps both claims only when called with a `token_ttl_seconds` value; token lifetime is a per-caller configuration value (`signing.token_ttl_seconds` in the calling service's config), not something Identity itself sets or knows.

On verification, once the signature is confirmed authentic, Identity checks the header's `exp` claim (if present) against its own clock: if `exp < now`, the request is rejected with `401 token_expired`. Tokens that omit `iat`/`exp` entirely are accepted unconditionally (legacy tolerance) — Identity does not require expiry claims to be present.

---

### POST /agents/verify — retired

An earlier raw signature-verification endpoint (`{agent_id, payload, signature}` → `{valid, ...}`) existed before JWS tokens were introduced. It has been superseded by `POST /agents/verify-jws`, which is the endpoint the platform's shared `IdentityClient` actually calls. It is no longer part of the documented, live API contract — do not build new integrations against it.

---

### GET /agents/{agent_id}

Look up an agent's public identity. Also used by the shared `IdentityClient` (`get_agent`) so calling services can fetch a public key or agent record without a verification round-trip.

**Response (200 OK):**
```json
{
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "name": "Alice",
  "public_key": "ed25519:<base64>",
  "registered_at": "2026-02-20T10:30:00Z"
}
```

**Errors:**

| Status | Code              | Description                 |
|--------|-------------------|------------------------------|
| 404    | `agent_not_found` | No agent with this agent_id |

---

### GET /agents

List all registered agents.

**Response (200 OK):**
```json
{
  "agents": [
    {
      "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
      "name": "Alice",
      "registered_at": "2026-02-20T10:30:00Z"
    }
  ]
}
```

Public keys are omitted in the list view. Use `GET /agents/{agent_id}` for full details.

---

### GET /health

Service health check and basic statistics.

**Response (200 OK):**
```json
{
  "status": "ok",
  "uptime_seconds": 3621,
  "started_at": "2026-02-20T08:00:00Z",
  "registered_agents": 42
}
```

`registered_agents` is a live count fetched from the DB Gateway on every call.

---

## Standardized Error Format

All error responses follow this structure, exactly three top-level fields:

```json
{
  "error": "error_code",
  "message": "Human-readable description of what went wrong",
  "details": {}
}
```

Error codes are lowercase snake_case (e.g. `missing_field`, `agent_not_found`), never SCREAMING_CASE. `details` is always present — an empty object `{}` when there is nothing additional to report, or a small structured payload (e.g. `{"field": "name"}` for a missing-field error).

---

## What This Service Does NOT Do

- **Key rotation** — once registered, an agent's public key is permanent.
- **Signing on behalf of agents** — agents sign locally. The service never holds private keys.
- **Replay protection beyond `exp`** — Identity enforces the JWS `exp` claim when present, but does not track nonces or otherwise prevent token reuse before expiry. Calling services remain responsible for additional replay protection if they need it.
- **Rate limiting** — open registration with no throttling.
- **Authorization** — the service answers "is this agent who they claim to be, and is this token still valid?" not "is this agent allowed to do this?" Authorization is the calling service's responsibility.
- **Calling other domain services** — Identity's only outbound dependency is the DB Gateway (port 8007). It never calls central-bank, task-board, reputation, or court.

---

## Interaction Patterns

### Registration (one-time per agent)

```
Agent                              Identity Service              DB Gateway
  |                                       |                           |
  |  1. Generate Ed25519 keypair locally  |                           |
  |     (private_key, public_key)         |                           |
  |                                       |                           |
  |  2. POST /agents/register             |                           |
  |     { name, public_key }              |                           |
  |  ------------------------------------>|                           |
  |                                       |  3. Validate key format   |
  |                                       |  4. Insert agent record   |
  |                                       |  -----------------------> |
  |                                       |  5. 201 / 409             |
  |                                       |  <----------------------- |
  |  6. 201 { agent_id, name,             |                           |
  |           public_key, registered_at } |                           |
  |  <------------------------------------|                           |
```

### Signature Verification (on every authenticated action)

```
Agent                   Calling Service            Identity Service
  |                           |                           |
  |  1. Construct payload,    |                           |
  |     sign as compact JWS   |                           |
  |     (kid=agent_id,        |                           |
  |      alg=EdDSA, optional  |                           |
  |      iat/exp in header)   |                           |
  |                           |                           |
  |  2. Send request with     |                           |
  |     the JWS token         |                           |
  |  ---------------------->  |                           |
  |                           |  3. POST /agents/verify-jws|
  |                           |     { token }             |
  |                           |  ---------------------->  |
  |                           |                           | 4. Lookup public_key by kid
  |                           |                           | 5. EdDSA verify
  |                           |                           | 6. Enforce exp if present
  |                           |  7. { valid, agent_id,    |
  |                           |       payload }           |
  |                           |  <----------------------  |
  |                           |                           |
  |  8. Proceed or reject     |                           |
  |  <----------------------  |                           |
```
