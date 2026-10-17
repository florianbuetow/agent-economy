# Request Validation Specification

## Overview

Every authenticated agent or platform operation uses an Ed25519 compact JWS.
The signature authenticates the exact protected-header and payload bytes sent by
the signer. It does not encrypt either object.

The system has two verification paths:

- **Agent-signed operations** call Identity's `POST /agents/verify-jws` through
  the shared `IdentityClient` because the receiving service does not hold the
  agent's public key.
- **Platform-signed operations** verify locally with the provisioned platform
  public key. They do not make an Identity HTTP request. Local admission also
  requires the authenticated header `kid` to equal the registered platform
  agent id.

Central Bank's `POST /accounts` remains an Identity-verified exception for both
self-service and platform-funded creation because that route also checks agent
existence through Identity.

## Strict Compact-JWS Profile

A token has three base64url segments:

```text
base64url(protected-header).base64url(payload).base64url(signature)
```

Verification checks the Ed25519 signature over the original
`protected-header.payload` bytes. A verifier must not reserialize either JSON
object before checking the signature.

### Protected header

The protected header is an exact object with these seven fields and no others:

```json
{
  "alg": "EdDSA",
  "typ": "JWT",
  "kid": "a-550e8400-e29b-41d4-a716-446655440000",
  "iat": 1792051200,
  "exp": 1792051500,
  "aud": "central-bank",
  "resource": "POST /escrow/lock"
}
```

| Field | Required type and rule |
| --- | --- |
| `alg` | Exact string `EdDSA` |
| `typ` | Exact string `JWT` |
| `kid` | Non-blank string naming a registered identity |
| `iat` | JSON integer, with booleans explicitly rejected |
| `exp` | JSON integer, with booleans explicitly rejected; must be greater than `iat` |
| `aud` | Exact non-blank service name expected by the receiver |
| `resource` | Exact uppercase HTTP method, one space, and concrete origin-free request path |

The initial configured service names are `central-bank`, `task-board`,
`reputation`, and `court`. `resource` contains no host or query string. The
receiver constructs the expected audience and concrete `METHOD /path` from its
own route and route parameters; it never trusts the token to choose the target.

Missing, extra, null, wrongly typed, or wrongly valued protected-header fields
make the profile malformed. There is no production compatibility mode for
headers without expiry claims.

### Payload

The payload remains the existing JSON object containing `action` and the
operation's domain fields:

```json
{
  "action": "escrow_lock",
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "amount": 250,
  "task_id": "t-4ccac437-c944-44ff-8af2-1d00597679cf"
}
```

`aud` and `resource` belong only in the protected header. Every receiver requires
payload `action` to be a string and checks it against the action allowed by that
endpoint. A missing or wrongly typed action is `400 invalid_payload`.

An action-only payload is valid when the protected resource binds the subject,
including Central Bank balance and transaction-history reads. There is no
minimum-two-field rule and no invented domain field. Payload fields named `aud`,
`resource`, or like other authentication metadata never authorize a target;
only the verified protected header supplies audience and resource. Party,
amount, state, and other domain authorization checks remain separate.

## Lifetime Policy

The shared `TokenPolicy` requires:

- `max_lifetime_seconds > 0`
- `clock_skew_seconds >= 0`
- `exp > iat`
- `exp - iat <= max_lifetime_seconds`

At integer time `now`:

- `iat > now + clock_skew_seconds` is `401 token_not_yet_valid`.
- `iat == now + clock_skew_seconds` is accepted.
- `now >= exp + clock_skew_seconds` is `401 token_expired`.
- `now == exp + clock_skew_seconds - 1` is accepted.

The initial deployment configuration is explicit YAML: signer TTL 300 seconds,
admission maximum 300 seconds, and clock skew 30 seconds. These values are not
code defaults. Signer TTL and admission maximum must be positive; clock skew
must be nonnegative. A signer cannot create a token without a TTL, and every
verifier enforces the configured maximum lifetime.

## Verified Result

Shared verification returns one typed value:

```text
VerifiedJws(header=JwsProtectedHeader(...), payload={...})
```

Both `header` and `payload` are authenticated. There is no payload-only wrapper,
legacy `None` TTL, or no-expiry result. Domain-only tests may mock a complete,
typed `VerifiedJws`, but such mocks do not prove authentication behavior.

Identity's successful response is:

```json
{
  "valid": true,
  "agent_id": "a-550e8400-e29b-41d4-a716-446655440000",
  "protected_header": {
    "alg": "EdDSA",
    "typ": "JWT",
    "kid": "a-550e8400-e29b-41d4-a716-446655440000",
    "iat": 1792051200,
    "exp": 1792051500,
    "aud": "central-bank",
    "resource": "POST /escrow/lock"
  },
  "payload": {"action": "escrow_lock", "amount": 250}
}
```

`agent_id` must equal `protected_header.kid`. Response binding compares JSON
values without Python-style coercion: `true`, `1`, and `1.0` are distinct.
After `valid:true`, the shared client also parses the originally requested token
and type-strictly binds its authenticated header and payload to the response. If
that requested token is invalid or the values differ, the response is a malformed
upstream success mapped to `502 identity_service_unavailable`.

Signature mismatch retains
Identity's existing `200 {"valid": false, "reason": "signature mismatch"}`
wire result. The shared Identity client validates every field in a successful
response and returns `VerifiedJws`; a malformed success response is an invalid
upstream response mapped through the existing `502 identity_service_unavailable`
path. It must never fabricate an authenticated header or payload.

## Receiver Admission Order

Before any domain effect, the receiver:

1. verifies the compact profile, signature, and lifetime policy;
2. for local platform routes, checks `header.kid` against the registered
   platform agent id;
3. compares `header.aud` with its configured `service.name`;
4. compares `header.resource` with the concrete uppercase `METHOD /path` it is
   currently handling;
5. requires string `payload.action` and checks it against the action allowed by
   the endpoint; and
6. applies the route's existing domain validation and authorization.

The public error mapping is uniform:

| Failure | Result |
| --- | --- |
| Non-object request body or signed payload, malformed compact/header representation, decoded signature length other than 64 bytes, exact header field/type/constant failure, or lifetime bound failure | `400 invalid_jws` |
| Authentic token issued beyond allowed future skew | `401 token_not_yet_valid` |
| Authentic token at or after `exp + skew` | `401 token_expired` |
| Valid-length signature mismatch or local platform `kid` mismatch | `403 forbidden` at the receiving service; Identity retains `valid: false` for signature mismatch |
| Missing/wrong-type action or authenticated wrong audience, resource, or action | `400 invalid_payload` |
| Identity transport failure or malformed Identity success response | `502 identity_service_unavailable` |

Every rejection occurs before a write, payout, state transition, or other domain
effect.

## Token Transport

- Mutating routes carry the compact token in the documented JSON-body field.
- GET routes without a request body carry it as
  `Authorization: Bearer <compact-jws>`.
- Asset upload uses its documented authorization-header transport.

Transport does not change the strict profile or verification rules.

## Examples

For Task Board to release escrow, it signs a payload containing
`action: "escrow_release"` and domain fields. The protected header contains
`aud: "central-bank"` and the concrete resource
`POST /escrow/<escrow_id>/release`. Central Bank verifies the token locally,
checks the platform `kid`, audience, resource, and action, then performs its
existing escrow authorization. Identity is not contacted.

For a worker to submit a bid, the worker signs the existing bid payload. The
protected header contains `aud: "task-board"` and
`resource: "POST /tasks/<task_id>/bids"`. Task Board sends the token to Identity,
validates the complete returned `VerifiedJws`, checks audience, resource, action,
and bidder authorization, and only then records the bid.

## MP-03 Rollout Status

This document is the reconciled MP-03 contract. Implementation and verification
are pending until the shared verifier, Identity wire/client path, official
signers, and every real local adapter pass the acceptance scenarios in the
matching service-test specifications. Existing implementation behavior must not
be cited as proof of this contract until that rollout is complete.
