# MP-02 capability design handoff

This is an engineering design for the next package, reviewed against the accepted masterplan and current code. It is not implemented yet and does not decide Q-M1, Q-M2 or Q-M3.

## Credentials

Use five distinct service key handles: `gateway-identity`, `gateway-central-bank`, `gateway-task-board`, `gateway-reputation`, and `gateway-court`. They are service capabilities, not registered economic agents: do not put them in the agent roster or construct them as PlatformAgent. Extend explicit provision/check tooling; normal startup remains load-only. Gateway verifies trusted public keys locally and never queries Identity to authenticate persistence calls, avoiding the bootstrap cycle.

Gateway configuration owns audience, TokenPolicy, trusted KID-to-public-key paths and capability grants. Each caller's existing db_gateway configuration owns its KID, canonical private pair path and explicit TTL. The isolated runtime provisions these pairs under its temporary directory and rewrites all trust/signer paths. Missing or corrupt material fails startup.

## Request binding and transport

Reuse the strict seven-field JWS header. Audience is `db-gateway`; resource is the exact uppercase method and concrete path, excluding query. The payload contains exactly action, body_sha256 and query_sha256. Build the final HTTPX request first and hash its actual serialized body and encoded query bytes; empty input hashes empty bytes. Verify the hashes against the received bytes before any handler effects.

A small shared request signer belongs in service-auth. Shared GatewayClient signs async requests centrally. Task Board, Court and Reputation each have a direct synchronous client and need one common signed request wrapper without changing their response/domain normalization.

## Grants

| Caller | Grants |
| --- | --- |
| gateway-identity | gateway.identity.read, gateway.identity.write |
| gateway-central-bank | gateway.bank.read, gateway.bank.write |
| gateway-task-board | gateway.board.read, gateway.board.write |
| gateway-reputation | gateway.reputation.read, gateway.reputation.write |
| gateway-court | gateway.court.read, gateway.court.write, gateway.board.task.read |
| UI, economic agents and the platform notary | No Gateway capability |

Court's narrow board-task read is live in DisputeDbClient._get_escrow_id while composing a dispute. It grants only GET /board/tasks/{task_id}, not Board lists, bids or assets. Task Board uses domain service APIs for Bank/Court delegation; Court uses Reputation's API for feedback. Do not give those callers direct cross-domain persistence writes.

## Admission

Only exact GET /health is public. Preserve bounded request-body and content-type rejection, then require exactly one Bearer Authorization header. Validate strict structure, trusted KID, original signature and lifetime; bind audience, method/path, route action and body/query digests; require the authenticated caller's capability; then execute existing route/domain validation. Missing/malformed credentials are 401; unknown KID/bad signature/denied grant are 403; malformed JWS is 400; future/expired tokens retain 401; authenticated context/digest mismatch is 400 invalid_payload. Unexpected internal failures are not converted into authentication failures.

Use one fail-closed admission middleware and a route-policy inventory test covering every non-health endpoint. Keep authentication outside synchronous DbWriter. Explicitly verify middleware body replay, encoded paths/queries, duplicate headers and body-bearing DELETE so integrity checks cannot alter handler input or omit a path.

## Test-first slices

1. T-112: real Gateway ASGI negatives for unsigned, wrong-domain, unknown-key, bad signature, expired/future, body/query tampering and duplicate/malformed Bearer inputs. Cover reads and writes for every domain with unchanged state/event assertions; include public health and Court's narrow read grant.
2. Shared signer tests must prove binding to final HTTPX bytes. Preserve existing shared-client response/error assertions.
3. Add Gateway trust configuration, state and middleware with the existing local service-auth dependency only.
4. Wire required caller signer configuration, lifespans and all four transport boundaries (one shared plus three direct clients).
5. Reconcile domain fixtures using distinct temporary service keys; never use an all-powerful fixture credential. Preserve every existing domain assertion and add independent authentication proof.
6. Complete browser stored-content, Host/Origin and exposure guards under the other MP-02 tasks before package closure.

MP-06 retains authority for monetary/ownership/schema invariants. MP-04 retains durable replay receipts: hashes and bounded freshness do not make repeated authenticated operations exactly-once. No unsigned compatibility path, new framework or external dependency is needed.
