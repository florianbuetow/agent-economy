# Court Service — Authentication Specification

## Purpose

This document specifies how the Court service authenticates operations using JWS tokens verified **locally** via `PlatformAgent.validate_certificate()` — no Identity service round trip. The Court has the simplest authentication model in the system: all write operations must be signed and headered by the platform agent, and all read operations are public.

## Motivation

The Court is an internal service. Agents never interact with it directly — the Task Board orchestrates all dispute operations on behalf of agents. The Task Board files claims on behalf of posters, submits rebuttals on behalf of workers, and (once something triggers it — see "What This Specification Does NOT Cover") calls `/rule` after the rebuttal window closes or a rebuttal arrives.

Platform-only authentication prevents any agent from directly filing a dispute, submitting a rebuttal, or triggering a ruling. All agent authentication happens at the Task Board layer; the Court trusts that the Task Board has already verified the agent's identity before forwarding a platform-signed request.

---

## Authentication Model

### Two Tiers of Operations

**Platform-signed operations** — require a JWS token whose protected header `kid` equals the platform agent's registered id, and whose signature verifies locally against the platform agent's public key:

| Endpoint | Description |
|----------|-------------|
| `POST /disputes/file` | File a new dispute (Task Board acts on behalf of poster) |
| `POST /disputes/{dispute_id}/rebuttal` | Submit worker's rebuttal (Task Board acts on behalf of worker) |
| `POST /disputes/{dispute_id}/rule` | Trigger the judge panel |

**Public operations** — no authentication:

| Endpoint | Description |
|----------|-------------|
| `GET /disputes/{dispute_id}` | Full dispute details including votes and ruling |
| `GET /disputes` | List disputes with optional filters |
| `GET /health` | Health check |

### Why Verification Is Local, Not an Identity Round Trip

Per the project's two-tier verification model (`docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §2.3), **platform-signed operations are verified locally** via the `PlatformAgent` the Court loaded at startup — the Court holds the platform's own keys and can check a signature claiming to be the platform without calling out to Identity. This is the Court's original pattern, now the project-wide standard for platform operations: no Identity dependency, survives Identity outages.

No Court authentication route makes an Identity HTTP request. Shared token failures use the uniform `invalid_jws`, `token_expired`, `token_not_yet_valid`, or `forbidden` mapping rather than an Identity-unavailable error.

### The `kid == platform` Authorization Rule

Cryptographic validity alone is not sufficient authorization. Every platform-signed write asserts **both**:

1. strict local verification returns an authenticated `VerifiedJws(header, payload)`, **and**
2. the authenticated `header.kid` equals `platform_agent.agent_id` exactly.

The `kid` used for authorization comes only from the authenticated typed header. An unverified header decode may assist parsing, but it cannot supply signer identity or a payload-only success result.

**Required MP-03 verification order:**

1. Verify compact form, exact typed header and payload object, signature, and lifetime policy. Malformed profile is `400 invalid_jws`; future issue time is `401 token_not_yet_valid`; expiry is `401 token_expired`; signature mismatch is `403 forbidden`.
2. Require authenticated `header.kid == platform_agent.agent_id` → `403 forbidden` on mismatch.
3. Require `header.aud == service.name`, exact concrete `header.resource`, and the endpoint's payload `action` → `400 invalid_payload` on mismatch.
4. Validate existing domain payload fields → `400 invalid_payload`.
5. Apply domain-specific rules.

**Signature/kid verification happens before `action` validation.** A token signed by a non-platform key is rejected with `403 forbidden` regardless of what its `action` field says — the signature check *is* the signer check under local verification, and it runs first. (An earlier draft of this document and its accompanying test spec described the opposite order — "action checked before platform signer verification" — that wording is stale; see `services/court/tests/unit/routers/test_disputes.py::test_prec_05_action_checked_before_signer`, whose docstring now reads "Signature verification rejects rogue signer before action check.")

### Why All Reads Are Public

Dispute data, votes, and rulings are public by design — transparency supports market trust. No authentication is required on any `GET` endpoint.

---

## JWS Token Format

### JWS Header

```json
{
  "alg": "EdDSA",
  "typ": "JWT",
  "kid": "<platform_agent_id>",
  "iat": 1792051200,
  "exp": 1792051500,
  "aud": "court",
  "resource": "POST /disputes/<concrete-dispute-id>/rule"
}
```

- The header has exactly `alg`, `typ`, `kid`, `iat`, `exp`, `aud`, and `resource`.
- `alg="EdDSA"`, `typ="JWT"`; `kid`, `aud`, and `resource` are non-blank strings.
- `iat`/`exp` are JSON integers excluding booleans, with `exp > iat` and lifetime no greater than the configured positive maximum.
- `aud` equals Court's configured `service.name`; `resource` equals the concrete uppercase `METHOD /path` handled.
- `kid` equals the registered platform agent id. Tokens without expiry claims or with extra header fields are `400 invalid_jws`.

### JWS Payload

The payload remains the existing domain object and includes string `action`. `aud` and `resource` stay in the protected header; similarly named domain fields grant no authority. Missing/wrong-type action or wrong authenticated audience, resource, or action is `400 invalid_payload` before any Court effect.

### Action Values

| Action | Endpoint |
|--------|----------|
| `file_dispute` | `POST /disputes/file` |
| `submit_rebuttal` | `POST /disputes/{dispute_id}/rebuttal` |
| `trigger_ruling` | `POST /disputes/{dispute_id}/rule` |

---

## Token Delivery

All three POST endpoints use body-token delivery, no other shape:

```json
{ "token": "<JWS compact token>" }
```

GET endpoints take no token. There are no header-token endpoints and no multi-token endpoints.

---

## Authentication Flow

```
Task Board                     Court Service
  |                                  |
  |  1. Construct JWS payload        |
  |     { action, ...fields }        |
  |  2. Sign with platform Ed25519   |
  |     private key                  |
  |     Exact protected header with  |
  |       platform kid, court aud,   |
  |       concrete resource/lifetime |
  |  3. POST /disputes/file          |
  |     { "token": "eyJ..." }        |
  |  ===============================>|
  |                                  |  4. Strict local verification
  |                                  |     -> VerifiedJws, or rejects
  |                                  |  5. Check authenticated kid
  |                                  |  6. Check aud/resource/action
  |                                  |  8. Validate payload fields
  |                                  |  9. Execute operation
  |  10. Response                    |
  |  <===============================|
```

---

## Authorization Rules

1. **Signer must be the platform agent.** Strict local verification proves the signature, then the authenticated header `kid` must equal `platform_agent.agent_id`. Either authorization failure returns `403 forbidden`.
2. **No agent-level authorization checks.** Unlike the Task Board or Reputation, the Court verifies only the platform identity — the Task Board is responsible for ensuring the correct agent authorized the underlying operation before it ever reaches the Court. The one exception is the rebuttal endpoint's optional `respondent_id` cross-check (see the API spec) — a defense against a corrupted forward, not agent-level auth.
3. **No ownership checks on reads.** GET endpoints are public.

---

## Request Format

### JWS Payload: File Dispute

```json
{
  "action": "file_dispute",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "claimant_id": "a-alice-uuid",
  "respondent_id": "a-bob-uuid",
  "claim": "The worker did not implement email validation as specified.",
  "escrow_id": "esc-770e8400-e29b-41d4-a716-446655440000"
}
```

### JWS Payload: Submit Rebuttal

```json
{
  "action": "submit_rebuttal",
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000",
  "rebuttal": "The specification did not define a specific email format."
}
```

`dispute_id` in the payload must match the `{dispute_id}` URL path parameter (`400 invalid_payload` on mismatch). Forwarding a `respondent_id` in the payload is optional; if present it is cross-checked against the dispute's recorded respondent (`403 forbidden` on mismatch).

### JWS Payload: Trigger Ruling

```json
{
  "action": "trigger_ruling",
  "dispute_id": "disp-990e8400-e29b-41d4-a716-446655440000"
}
```

`dispute_id` in the payload must match the `{dispute_id}` URL path parameter.

---

## Error Codes

### Authentication Errors

| Status | Code                          | When |
|--------|-------------------------------|------|
| 400    | `invalid_jws`                 | Non-object request body or signed payload, compact/header form, decoded signature length other than 64 bytes, exact header fields/types/constants, or lifetime bound is invalid |
| 400    | `invalid_payload`             | Action is missing/non-string, authenticated audience/resource/action mismatches, required payload fields are missing, or payload `dispute_id` differs from the URL |
| 401    | `token_expired`               | Authentic token satisfies `now >= exp + clock_skew_seconds` |
| 401    | `token_not_yet_valid`          | Authentic token satisfies `iat > now + clock_skew_seconds` |
| 403    | `forbidden`                   | Local `validate_certificate()` rejects the signature, `kid != platform_agent.agent_id`, or (rebuttal only) a forwarded `respondent_id` mismatch |

### Error Precedence (first match wins)

1. `415 unsupported_media_type` — wrong `Content-Type` (checked by `RequestValidationMiddleware`, before the route handler runs)
2. `413 payload_too_large` — body exceeds `request.max_body_size`
3. `400 invalid_json` — malformed JSON body
4. `400 invalid_jws` — missing/malformed `token` field
5. Strict local result: `400 invalid_jws`, `401 token_expired`, `401 token_not_yet_valid`, or `403 forbidden`
6. `403 forbidden` — authenticated `kid != platform_agent.agent_id`
7. `400 invalid_payload` — audience/resource/action mismatch, invalid payload fields, or URL/body `dispute_id` mismatch
8. Domain-specific errors (`dispute_not_found`, `dispute_already_exists`, `invalid_dispute_status`, `rebuttal_already_submitted`, `dispute_already_ruled`, `dispute_not_ready`, `task_not_found`, etc.)
9. `502` errors from downstream services (`task_board_unavailable`, `reputation_service_unavailable`, `judge_unavailable`)

**A wrong-signer token with a wrong `action` still returns `403 forbidden`, not `400 invalid_payload`** — signature verification runs before `action` is ever read. This corrects an earlier draft of this document (and its test spec's former `PREC-05`), which claimed the opposite order.

---

## Configuration

### Platform Agent

The Court's **own** `config.yaml` carries only:

```yaml
platform:
  agent_config_path: "../../agents/config.yaml"
```

`platform.agent_id` and `platform.private_key_path` also exist as fields on the `PlatformConfig` Pydantic model (both optional, `agent_id` defaults to `""`), but **neither is read anywhere in `court_service/core/lifespan.py`**. The actual key-loading path is:

1. `AgentFactory(config_path=<resolved agent_config_path>)` reads `agents/config.yaml`.
2. That file's `data.keys_dir` and `data.roster_path` point to the shared key store and `roster.yaml`.
3. `factory.platform_agent()` loads the `"platform"` roster entry's Ed25519 keypair (generating one on first run if absent) and constructs a `PlatformAgent`.
4. `await platform_agent.register()` registers with Identity and returns the platform's `agent_id`, which is then written back onto `settings.platform.agent_id` in memory (not persisted to the YAML file).

Do not describe `platform.private_key_path`/`platform.public_key_path` as the live key-loading mechanism in Court's own config — they are not consumed. There is no `platform.public_key_path` field on `PlatformConfig` at all.

All of `agent_config_path`'s resolution chain (agents `config.yaml` → keys dir → roster) is required; a missing or invalid file at any step raises at startup.

Token configuration is also required and explicit. Initial YAML values are signer TTL 300 seconds, admission maximum 300 seconds, and skew 30 seconds. TTL and maximum lifetime must be positive; skew must be nonnegative. Invalid or missing values fail startup.

---

## Infrastructure

### PlatformAgent

Constructed during `lifespan()` startup via `AgentFactory(...).platform_agent()`, then registered (`await platform_agent.register()`) and stored on `AppState.platform_agent`. Provides (from `libs/service-auth/src/service_auth/platform.py`):

- strict local verification returns `VerifiedJws(header, payload)` under an explicit `TokenPolicy`. It verifies the original compact bytes and makes no external call.
- `get_task(task_id)` — `GET {task_board_url}/tasks/{task_id}` (unauthenticated read against the Task Board service, not the DB Gateway).
- `record_ruling(task_id, payload)` — signs `payload` as the platform and `POST`s to `{task_board_url}/tasks/{task_id}/ruling`.
- `submit_platform_feedback(payload)` — signs `payload` as the platform and `POST`s to `{reputation_url}/feedback`.

### Dependencies

From `services/court/pyproject.toml`: `httpx` (async client for Task Board/Reputation calls, plus the DB Gateway's sync client in `DisputeDbClient`), `cryptography` (Ed25519), `joserfc`-family JWS handling (via `libs/service-auth`), `litellm` (real judges only).

---

## Interaction Patterns

### Platform Files Dispute

```
Task Board                     Court Service
  |  1. Sign JWS as platform: { action: file_dispute, ... }
  |  2. POST /disputes/file { "token": "eyJ..." }
  |  ===============================>|
  |                                  |  3. Strict local verification
  |                                  |     -> VerifiedJws
  |                                  |  4. Assert authenticated kid
  |                                  |  5. Validate aud/resource/action
  |                                  |  6. Validate payload fields
  |                                  |  8. Create dispute record
  |  9. 201 { dispute }              |
  |  <===============================|
```

### Certificate Verification Failure

```
Task Board                     Court Service
  |  POST /disputes/file { "token": "eyJ..." }
  |  ===============================>|
  |                                  |  validate_certificate() raises InvalidSignature
  |  403 { error: "forbidden" }      |
  |  <===============================|
```

### Correct Signature, Wrong `kid`

```
Task Board                     Court Service
  |  POST /disputes/file { token signed by platform key but header kid != platform_agent_id }
  |  ===============================>|
  |                                  |  signature verification succeeds
  |                                  |  authenticated kid != platform_agent.agent_id
  |  403 { error: "forbidden" }      |
  |  <===============================|
```

### Non-Platform Agent Attempt

```
Rogue Agent                    Court Service
  |  Signs JWS with own key (not the platform key)
  |  POST /disputes/file { "token": "eyJ..." }
  |  ===============================>|
  |                                  |  validate_certificate() raises InvalidSignature
  |                                  |  (signed with the wrong private key entirely)
  |  403 { error: "forbidden" }      |
  |  <===============================|
```

---

## Token Expiry

`iat` and `exp` are required. The shared policy requires positive `max_lifetime_seconds`, nonnegative `clock_skew_seconds`, `exp > iat`, and a bounded lifetime. At the exact boundaries, `iat == now + skew` is accepted, `iat == now + skew + 1` is `401 token_not_yet_valid`, `now == exp + skew - 1` is accepted, and `now == exp + skew` is `401 token_expired`. Initial explicit YAML values are 300 seconds for signer TTL and admission maximum, and 30 seconds skew.

## Token Replay Considerations

The `action` field prevents cross-operation replay. Same-operation replay is mitigated by domain constraints:

- **File dispute replay:** `task_id` uniqueness → `409 dispute_already_exists`.
- **Submit rebuttal replay:** one rebuttal per dispute → `409 rebuttal_already_submitted`.
- **Trigger ruling replay:** one ruling per dispute → `409 dispute_already_ruled`; a replay attempted while the dispute is `judging` (concurrently in progress) → `409 dispute_not_ready` (see the reentrancy guard in the API spec).

Nonce-based replay protection is out of scope — a same-signature token replayed within its 300s TTL and before the domain-level constraint above fires would be accepted again. Full replay protection (nonces) is future work.

---

## What This Specification Does NOT Cover

- **Agent-level authentication.** The Task Board handles it before calling the Court.
- **Rate limiting.**
- **Nonce-based replay protection.** Token expiry (`iat`/`exp`, 300s TTL) exists system-wide since WP-02/Q-10 — see "Token Expiry" above.
- **Outgoing token signing details for Task Board/Reputation calls** — covered by those services' own auth specs.
- **Persistence details** (schema, migration) — covered by the DB Gateway spec and the main Court API spec.
- **Who triggers `POST /disputes/{id}/rule` after the rebuttal window closes.** The Court enforces the window when asked (`dispute_not_ready` if asked too early) but does not run a scheduler of its own. Target ownership of that trigger is tracked outside this document (Q-5 in the target-architecture plan).
