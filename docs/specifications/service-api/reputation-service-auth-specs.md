# Reputation Service — Authentication Specification

## Purpose

This document specifies how the Reputation service authenticates `POST /feedback` submissions. It supersedes an earlier version of this document that described a bespoke "certificate" model (`PlatformAgent.validate_certificate(request_payload, certificate) -> bool` over a payload+certificate request shape). That model was never implemented; it has been rewritten to describe the real, shipped design: a **two-tier JWS model**, shared in shape with every other authenticated write in the system.

## Motivation

Without authentication, any caller can submit feedback impersonating any agent. This makes reputation data worthless — a malicious caller can inflate their own reputation or sabotage competitors. Authentication ensures that only the actual agent (or the platform itself, for court-generated feedback) can submit feedback in its own name.

---

## Authentication Model: Two Tiers

Verified against `reputation_service/routers/feedback.py` (`_select_verifier`, `_token_kid`), `reputation_service/services/protocol.py` (`JwsVerifier` Protocol), `reputation_service/services/platform_identity_client.py` (`PlatformJwsVerifier`), and `reputation_service/core/lifespan.py`.

**Tier 1 — Agent operations** (ordinary agent-to-agent feedback): verify **remotely**, via Identity's `POST /agents/verify-jws`.
- `POST /feedback` where the JWS token's signer is *not* the service's own configured platform agent.

**Tier 2 — Platform operations** (`force_visible` feedback, i.e. court-generated ruling feedback): verify **locally**, with no Identity round-trip, so they keep working while Identity is down.
- `POST /feedback` where the JWS token's signer *is* the service's own configured platform agent (its `kid` matches `state.platform_agent.agent_id`).

**Tier 0 — Public, unauthenticated:**
- `GET /feedback/{feedback_id}`, `GET /feedback/task/{task_id}`, `GET /feedback/agent/{agent_id}`, `GET /health`.

### Why GET endpoints stay public

Visible feedback is public data by design — any agent or consumer can query it to inform bidding strategy, task acceptance, or dispute context. Sealed feedback is already protected by returning 404. Adding auth to reads would add complexity with no security benefit.

### How the tier is selected

`_select_verifier` reads the **unverified** `kid` from the JWS header (base64url-decoding the header segment without checking the signature) purely to decide *which* verifier to hand the token to:

```python
if platform_agent is not None and platform_verifier is not None and _token_kid(token) == platform_agent.agent_id:
    return state.platform_verifier
return state.identity_client
```

This is a routing hint only, never a trust decision by itself — whichever verifier is selected then performs real cryptographic verification. A token whose header merely *claims* `kid == platform_agent.agent_id` but is not actually signed by the platform's private key is routed to `PlatformJwsVerifier`, which will reject it (its `validate_certificate` call fails signature verification against the platform's own key). It cannot fall through to Identity and be accepted as an ordinary agent either, because the payload's `from_agent_id` would then have to equal the platform's id for the signer-match check to pass, and Identity has no such agent registered under that id in the normal case. There is no cross-tier confusion attack: verification, not routing, is what admits or rejects a request.

### `IdentityConfig` is optional

`Settings.identity: IdentityConfig | None = None`. When `identity` is absent from `config.yaml`, `lifespan.py` sets `state.identity_client = state.platform_verifier` — i.e. **every** caller, including ordinary agents, is verified through the local platform verifier in that configuration. In the real deployed `config.yaml`, `identity` is present and populated, so this fallback is a documented-but-currently-unexercised code path (no test in the current suite constructs a config without an `identity` section) — noted here because it is real, verified code, not speculation.

---

## Composition, not inheritance: the `JwsVerifier` Protocol

Both verifiers — the remote one (Identity) and the local one (platform) — implement the same narrow structural interface, `reputation_service/services/protocol.py::JwsVerifier`:

```python
class JwsVerifier(Protocol):
    async def verify_jws(self, token: str) -> dict[str, Any]:
        """Return {"valid": bool, "agent_id": str, "payload": dict}."""
        ...
    async def close(self) -> None: ...
```

- **Tier 1 implementation:** `service_clients.identity.IdentityClient` — an async HTTP client, `verify_jws` POSTs `{"token": token}` to Identity's `verify_jws_path` and returns Identity's JSON response.
- **Tier 2 implementation:** `reputation_service.services.platform_identity_client.PlatformJwsVerifier` — holds a *provider callable* for the platform agent (`Callable[[], PlatformAgent | None]`, injected as `lambda: state.platform_agent`), not the agent itself, and not an `IdentityClient` subclass. `verify_jws` decodes the header locally, calls `platform_agent.validate_certificate(token)` (from `libs/service-auth`, a purely local Ed25519 verification against the platform's own public key — no network call), and returns the same `{"valid", "agent_id", "payload"}` shape.

`routers/feedback.py` depends only on the `JwsVerifier` Protocol, never on either concrete class — it can call `verify_jws`/`close` on whichever verifier `_select_verifier` returns without knowing which one it got.

### Why composition — the bug this replaced

Before this rollout (WP-03), Reputation's local verifier was `PlatformIdentityClient(IdentityClient)` — a subclass. Its `__init__` never called `super().__init__()`, so the inherited `IdentityClient` instance never got its HTTP client (`httpx.AsyncClient`) or configured paths set up. Any code path that happened to invoke an inherited `IdentityClient` method on a `PlatformIdentityClient` instance would hit unset attributes — a latent `AttributeError` waiting for the right call shape to trigger it. This was a real, if not previously exercised, bug: an inheritance relationship that implied "is-a `IdentityClient` plus local verification" when the actual need was "does the same three-method job as one, locally." The fix (`PlatformJwsVerifier`) drops the inheritance entirely: it implements the `JwsVerifier` Protocol standalone, holding only what it needs (a platform-agent provider), with no unset base-class state to fall into.

---

## JWS Token Format

Verified against `libs/service-auth/src/service_auth/signing.py`.

- **Compact serialization**, three dot-separated base64url segments: `header.payload.signature`.
- **Header**: `{"alg": "EdDSA", "typ": "JWT", "kid": "<signer-agent-id>"}`, plus `iat`/`exp` (Unix seconds) when the signer's `token_ttl_seconds` is configured — omitted entirely (no expiry enforced) when it is not. A token with an expired `exp` fails verification (`TokenExpiredError`, mapped to `valid: False` → `403 forbidden` by the router, same as a bad signature). Tokens without `iat`/`exp` are always accepted (legacy tolerance).
- **Payload**: an arbitrary JSON object — for this endpoint, the feedback fields plus `action: "submit_feedback"`.
- **Canonicalization**: exactly one, system-wide — `json.dumps(obj, sort_keys=True, separators=(",", ":"))` — applied to both header and payload before signing. Verification checks the signature against the token bytes exactly as received (never re-canonicalized), so tokens from any signer using this one canonicalization verify correctly.
- **Signature**: Ed25519, over `f"{header_b64}.{payload_b64}".encode("ascii")`.

## Request Format

`POST /feedback` takes a single wrapper field:

```json
{
  "token": "<jws-compact-serialization>"
}
```

The JWS payload, before signing:

```json
{
  "action": "submit_feedback",
  "task_id": "t-550e8400-e29b-41d4-a716-446655440000",
  "from_agent_id": "a-alice-uuid",
  "to_agent_id": "a-bob-uuid",
  "category": "delivery_quality",
  "rating": "satisfied",
  "comment": "Good work"
}
```

### Key design decisions

- **`from_agent_id` remains in the payload** and must equal the token's verified signer (see Authorization Rules). This is how the router proves "the caller is who they claim to be" without a separate `agent_id`-vs-`from_agent_id` cross-check against a third source.
- **`action` field is required**, fixed to `"submit_feedback"`. This prevents a token minted for a different operation (e.g. an `escrow_lock` token from Central Bank) from being replayed against this endpoint — the payload shape alone is not sufficient to distinguish operations, so the `action` discriminator is checked explicitly.
- There is no separate `certificate` field — the JWS token *is* the certificate; header, payload, and signature travel together as one compact string.

---

## Authorization Rules

Verified against `routers/feedback.py::submit_feedback_endpoint`, in the exact order the code checks them:

1. **Token extraction** — `token` must be present, non-null, a string, non-empty, and a three-part (`.`-separated) compact serialization. Any failure → `400 invalid_jws`.
2. **Verification** — the selected verifier (Identity or local platform) authenticates the token. Failure (bad signature, unknown signer, expired token) → `403 forbidden`. Infrastructure failure while calling Identity (connection error, timeout, malformed 200 response) → `502 identity_service_unavailable` (`IdentityClient`/`BaseServiceClient` maps `httpx.ConnectError`/`httpx.TimeoutException`/other `httpx.HTTPError` and malformed-response cases to this; it is distinct from a genuine verification rejection).
3. **Payload shape** — the verified payload must be a JSON object → else `400 invalid_payload`.
4. **Signer identity present** — the verifier must have returned a non-empty `agent_id` (i.e. the token header had a `kid`) → else `400 invalid_jws` ("Token header is missing kid"). Note this check runs *after* verification succeeds, so a token that fails verification is always rejected with `403` before this is ever reached.
5. **`action` check** — payload's `action` must equal `"submit_feedback"` → else `400 invalid_payload`.
6. **`from_agent_id` present in payload** → else `400 invalid_payload`.
7. **Signer matching** — the verified signer id must equal the payload's `from_agent_id` → else `403 forbidden`. An agent can only submit feedback in its own name; the platform can only submit feedback "from" itself.
8. **`force_visible` decision** — `is_platform = (state.platform_agent is not None and signer_agent_id == state.platform_agent.agent_id)`. Because step 7 already forced `signer_agent_id == from_agent_id_in_payload`, this is equivalent to checking whether the payload's `from_agent_id` is the platform's own agent id. When true, the submission is stored immediately visible regardless of counterpart state (see API spec, "Court / Platform-Generated Feedback").
9. **Business validation** — the ordinary feedback field rules (`missing_field`, `invalid_category`, `invalid_rating`, `self_feedback`, `comment_too_long`, `feedback_exists`) apply identically to both tiers; the auth tier only decides *who* is allowed to claim a given `from_agent_id` and whether the result is force-visible, not which field values are acceptable.

There is no separate "platform-only" endpoint — `force_visible` is purely a consequence of *who signed the token*, expressed through the single `POST /feedback` route.

---

## Agent Existence Verification

`from_agent_id` is proven cryptographically by JWS signature verification — Reputation never does a separate Identity lookup to confirm an agent exists before accepting its feedback (the signature itself is sufficient proof of key possession; Identity's `verify-jws` endpoint is what resolves a `kid` to a registered agent for Tier 1).

The service does **not** verify that `to_agent_id` exists. It accepts any non-empty string. Feedback about a non-existent agent is inert (no one queries it). Task Board already validated both agents exist when managing the task lifecycle; Reputation trusts that upstream validation, the same way it trusts upstream `task_id` validity.

---

## Error Codes

All errors use the standard three-field envelope (`{"error", "message", "details"}`), snake_case codes.

| Status | Code                          | When                                                         |
|--------|-------------------------------|--------------------------------------------------------------|
| 400    | `invalid_jws`                 | `token` missing, null, non-string, empty, not three-part compact serialization, or verified-but-missing `kid` |
| 400    | `invalid_payload`             | Payload is not a JSON object, missing `action`, `action` != `"submit_feedback"`, or missing `from_agent_id` |
| 403    | `forbidden`                   | Verification failed (bad signature, unknown/expired token), or signer does not match `from_agent_id` |
| 502    | `identity_service_unavailable`| Identity unreachable, timed out, or returned a malformed response — Tier 1 only; Tier 2 has no network dependency and cannot produce this code |

### Notes on error mapping

- `403 forbidden` covers two distinct failure causes (bad signature vs. signer/payload mismatch) with one generic message, deliberately — the message never distinguishes "unregistered agent" from "signature mismatch" from "impersonation attempt," to avoid leaking which one an attacker hit.
- `502 identity_service_unavailable` only arises on the agent-op (Tier 1) path. This is the entire point of the two-tier design: a platform/`force_visible` submission verifies with zero network calls and so cannot produce this error, and continues to succeed while Identity is down (verified: `tests/unit/routers/test_two_tier_feedback_auth.py::test_platform_feedback_succeeds_when_identity_down`). An ordinary agent submission made while Identity is down fails cleanly with `502`, not a crash or a silent bypass (`test_agent_feedback_fails_cleanly_when_identity_down`).

---

## Configuration

Verified against `services/reputation/config.yaml` and `reputation_service/config.py`. The earlier version of this document invented a `platform.public_key_path`/`platform.private_key_path` pair that does not exist in the real config schema.

```yaml
platform:
  agent_config_path: "../../agents/config.yaml"

identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
```

- `platform.agent_config_path` is a path to the shared roster config (`agents/config.yaml`), resolved relative to the service's own config file when not absolute. At startup, `AgentFactory(config_path=...).platform_agent()` constructs the `PlatformAgent`, which then `await`s `register()` before being stored in `AppState`. This is the *only* platform-auth configuration the service needs — there are no separate PEM key-path settings; key material lives wherever the roster config's `AgentFactory` resolves it from.
- `identity` (all four sub-keys) is present in the real deployed config but is **optional** at the `Settings` level (`IdentityConfig | None = None`) — see "IdentityConfig is optional" above for the fallback behavior when it is omitted.
- `request.max_body_size` lives in its own `request:` section (not nested under `feedback:`), matching the Central Bank / Task Board pattern, and is what `RequestValidationMiddleware` reads for the 413 check.

---

## Infrastructure

### PlatformAgent

Instantiated once during `lifespan.py` startup from the `platform.agent_config_path` roster config, stored in `AppState.platform_agent`, and wrapped by a `PlatformJwsVerifier` (composition, described above) stored in `AppState.platform_verifier`. `PlatformAgent.validate_certificate(token)` performs local Ed25519 verification of a JWS token against the platform's **own** public key — this is how Reputation authenticates tokens it expects to have been signed by itself-as-platform (e.g., forwarded by Court, which signs with the shared platform key). It is a synchronous, local, no-network operation. `close()` on the agent is called during shutdown; `PlatformJwsVerifier.close()` itself is a no-op (nothing to release).

### Request Validation Middleware

Content-Type and body-size checks run in an ASGI `RequestValidationMiddleware` (`core/middleware.py`) ahead of routing, for `POST /feedback` specifically (`_JSON_POST_ENDPOINTS`). It returns `415 unsupported_media_type` for a non-`application/json` Content-Type (or a duplicated Content-Type header, which is rejected as `400 bad_request`), and `413 payload_too_large` for a body exceeding `request.max_body_size`, both before the request reaches the router or any JWS handling.

---

## What This Specification Does NOT Cover

- **Rate limiting** — no throttling on authenticated or unauthenticated endpoints.
- **Replay protection beyond `action` scoping and optional `exp`** — a valid, unexpired token for `submit_feedback` can be replayed until the target `(task_id, from_agent_id, to_agent_id)` triple already has a stored row, at which point it fails with `409 feedback_exists`. There is no nonce or single-use enforcement independent of that uniqueness constraint.
- **A separate platform-only endpoint** — `force_visible` is a signer-derived property of the one `POST /feedback` route, not a distinct route or method.

---

## Interaction Patterns

### Authenticated Feedback Submission (agent op, Tier 1)

```
Worker                          Reputation Service           Identity
  |                                    |                         |
  |  1. Build payload:                |                         |
  |     { action: submit_feedback,    |                         |
  |       task_id, from_agent_id,     |                         |
  |       to_agent_id, category,      |                         |
  |       rating, comment }           |                         |
  |  2. Sign with own Ed25519 key     |                         |
  |     -> JWS token                  |                         |
  |                                    |                         |
  |  3. POST /feedback {token}        |                         |
  |  --------------------------------->|                         |
  |                                    | 4. kid != platform.id   |
  |                                    |    -> route to Identity |
  |                                    | 5. POST /agents/verify-jws ->|
  |                                    |                         | 6. Verify signature,
  |                                    |                         |    resolve kid -> agent
  |                                    | <-----------------------|
  |                                    | 7. signer == from_agent_id?
  |                                    | 8. Validate fields       |
  |                                    | 9. Store (sealed unless mutual)
  |  10. 201 { feedback_id,           |                         |
  |            visible: false }        |                         |
  |  <---------------------------------|                         |
```

### Court-Generated Feedback (platform op, Tier 2 — Identity down)

```
Court (as platform agent)      Reputation Service
  |                                    |
  |  1. Sign with the PLATFORM's      |
  |     own Ed25519 key -> JWS token  |
  |                                    |
  |  2. POST /feedback {token}        |
  |  --------------------------------->|
  |                                    | 3. kid == platform.agent_id
  |                                    |    -> route to PlatformJwsVerifier
  |                                    | 4. validate_certificate(token)
  |                                    |    (local, no network call)
  |                                    | 5. signer == from_agent_id == platform.id
  |                                    | 6. is_platform -> force_visible = True
  |                                    | 7. Store, immediately visible
  |  8. 201 { visible: true }          |
  |  <---------------------------------|

  (Identity being completely unreachable does not affect this path at all.)
```

### Impersonation Attempt

```
Mallory                         Reputation Service
  |                                    |
  |  Signs a valid JWS with her OWN    |
  |  private key, but sets             |
  |  from_agent_id: alice in the       |
  |  payload                           |
  |                                    |
  |  POST /feedback {token}            |
  |  --------------------------------->|
  |                                    |  Verification succeeds (signature is
  |                                    |  genuinely Mallory's) -> signer =
  |                                    |  mallory's own id
  |                                    |  signer != from_agent_id (alice)
  |                                    |  -> 403 forbidden
  |  403 { error: forbidden }          |
  |  <---------------------------------|
```
