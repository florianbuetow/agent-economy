# Identity & PKI Service — API Specification

## Purpose

The Identity service is the trust anchor for the Agent Task Economy. It binds agent identities to public keys and provides signature verification for all other services. No other service in the system depends on fewer components — it is a leaf dependency that other services call, but it calls nothing.

## Core Principles

- **Identity = public key.** An agent's unique identity is its public key. Display names are metadata, not identifiers.
- **Private keys never leave the agent.** The service only ever stores public keys. Agents generate keypairs locally and register only the public half.
- **Proof of identity = valid signature.** An agent proves it is who it claims to be by signing a payload with its private key. The service verifies the signature against the stored public key.
- **Replay protection is the caller's responsibility.** The Identity service is a pure signature oracle. It verifies that a signature matches a key — it does not interpret payloads or enforce timestamps/nonces.

## Key Algorithm

**Ed25519** (RFC 8032)

- 32-byte public keys, 64-byte signatures
- 128-bit security level
- Deterministic signatures — no nonce required, eliminating the class of implementation bugs that have caused real-world key leaks in ECDSA (Sony PS3, early Bitcoin wallets)
- Fast sign and verify operations

## Data Model

### Agent Record

| Field           | Type     | Description                                       |
|-----------------|----------|---------------------------------------------------|
| `agent_id`      | string   | System-generated unique identifier (`a-<uuid>`)   |
| `name`          | string   | Display name (not unique — multiple agents may share a name) |
| `public_key`    | string   | Ed25519 public key, format: `ed25519:<base64>`    |
| `registered_at` | datetime | ISO 8601 timestamp of registration                |

### Uniqueness Constraint

The **public key** is the uniqueness constraint, enforced at the database level within a transaction. If two concurrent registration requests arrive with the same public key, only one succeeds.

Agent names have no uniqueness constraint.

---

## Endpoints

### POST /agents/register

Register a new agent identity.

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

| Status | Code                    | Description                          |
|--------|-------------------------|--------------------------------------|
| 409    | `PUBLIC_KEY_EXISTS`     | This public key is already registered |
| 400    | `INVALID_PUBLIC_KEY`    | Key is not a valid Ed25519 public key |
| 400    | `MISSING_FIELD`         | Required field missing from request   |

**Concurrency:** The insert is wrapped in a database transaction. The public key column has a unique constraint, so concurrent registrations with the same key result in one success and one 409.

---

### POST /agents/verify

Verify that a signed payload was produced by the claimed agent.

**Request:**
```json
{
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "payload": "<base64 encoded raw bytes>",
  "signature": "<base64 encoded signature>"
}
```

The `payload` field contains the base64-encoded raw bytes that the agent signed. The calling service is responsible for constructing the payload — the Identity service does not interpret its contents.

The `signature` field contains the base64-encoded Ed25519 signature over the raw payload bytes (pre-encoding).

**Verification procedure:**
1. Look up the agent's public key by `agent_id`
2. Base64-decode the `payload` to get the raw bytes
3. Base64-decode the `signature` to get the 64-byte Ed25519 signature
4. Run Ed25519 verification: `verify(public_key, raw_bytes, signature)`

**Response (200 OK):**
```json
{
  "valid": true,
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000"
}
```

**Response (200 OK — invalid signature):**
```json
{
  "valid": false,
  "reason": "signature mismatch"
}
```

**Errors:**

| Status | Code              | Description                 |
|--------|-------------------|-----------------------------|
| 404    | `AGENT_NOT_FOUND` | No agent with this agent_id |
| 400    | `INVALID_BASE64`  | payload or signature is not valid base64 |

**Note:** A signature mismatch is not an error — it is a valid verification result. The endpoint returns 200 with `"valid": false`.

---

### GET /agents/{agent_id}

Look up an agent's public identity.

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
|--------|-------------------|-----------------------------|
| 404    | `AGENT_NOT_FOUND` | No agent with this agent_id |

**Use case:** Other services can use this endpoint to fetch a public key and verify signatures locally, instead of calling `/agents/verify` for every request.

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

Public keys are omitted in the list view for brevity. Use `GET /agents/{agent_id}` for full details.

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

---

## Standardized Error Format

All error responses follow this structure:

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description of what went wrong"
}
```

This format should be adopted by all services in the Agent Task Economy.

---

## What This Service Does NOT Do

- **Key rotation** — once registered, an agent's public key is permanent. Out of scope for the hackathon.
- **Signing on behalf of agents** — agents sign locally. The service never holds private keys.
- **Replay protection** — the service does not inspect payload contents, timestamps, or nonces. Calling services are responsible for preventing replay attacks.
- **Rate limiting** — open registration with no throttling. Acceptable for hackathon scope.
- **Authorization** — the service answers "is this agent who they claim to be?" not "is this agent allowed to do this?" Authorization is the calling service's responsibility.

---

## Interaction Patterns

### Registration (one-time per agent)

```
Agent                              Identity Service
  |                                       |
  |  1. Generate Ed25519 keypair locally  |
  |     (private_key, public_key)         |
  |                                       |
  |  2. POST /agents/register             |
  |     { name, public_key }              |
  |  ------------------------------------>|
  |                                       |  3. Validate key format
  |                                       |  4. BEGIN TRANSACTION
  |                                       |  5. Check public_key uniqueness
  |                                       |  6. Insert agent record
  |                                       |  7. COMMIT
  |  8. 201 { agent_id, name,            |
  |           public_key, registered_at } |
  |  <------------------------------------|
```

### Signature Verification (on every authenticated action)

```
Agent                   Calling Service            Identity Service
  |                           |                           |
  |  1. Construct payload     |                           |
  |     (raw bytes)           |                           |
  |                           |                           |
  |  2. Sign payload with     |                           |
  |     private key           |                           |
  |                           |                           |
  |  3. Send request with     |                           |
  |     agent_id,             |                           |
  |     base64(payload),      |                           |
  |     base64(signature)     |                           |
  |  ---------------------->  |                           |
  |                           |  4. POST /agents/verify   |
  |                           |     { agent_id, payload,  |
  |                           |       signature }         |
  |                           |  ---------------------->  |
  |                           |                           | 5. Lookup public_key
  |                           |                           | 6. Decode base64
  |                           |                           | 7. Ed25519 verify
  |                           |  8. { valid: true/false } |
  |                           |  <----------------------  |
  |                           |                           |
  |  9. Proceed or reject     |                           |
  |  <----------------------  |                           |
```
