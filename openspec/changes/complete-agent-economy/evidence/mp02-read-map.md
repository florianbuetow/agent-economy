# MP-02 current gateway boundary map

Read-only preparation while the MP-03 root gate runs. No MP-02 implementation or task completion is claimed.

The masterplan F-M01 and OpenSpec design§2 require authenticated domain-scoped service credentials, request-body integrity/freshness and no unsigned-write compatibility path. The current DB Gateway API spec still describes unauthenticated trusted-host operation and must be reconciled when this breaking change lands. Only GET /health is explicitly public. Gateway remains a leaf: it cannot query Identity to authenticate Identity's own persistence calls.

Current persistence callers:

| Caller | HTTP client boundary | Required domain |
| --- | --- | --- |
| Identity | AgentDbClient → shared service_clients.gateway.GatewayClient | /identity agents reads/writes |
| Central Bank | LedgerDbClient → shared GatewayClient | /bank account/ledger/escrow reads/writes |
| Task Board | TaskDbClient, direct synchronous httpx.Client | /board tasks/bids/assets/status reads/writes |
| Reputation | FeedbackDbClient, direct synchronous httpx.Client | /reputation feedback reads/writes |
| Court | DisputeDbClient, direct synchronous httpx.Client | /court claims/rebuttals/rulings/status reads/writes, plus the existing board task-read use that needs exact callsite confirmation before a grant |
| UI | direct read-only SQLite | Existing documented exception; HTTP browser authority and privacy projections are separate MP02/MP12 work |

Gateway routers under routers/{identity,bank,board,reputation,court}.py currently validate shape and call DbReader/DbWriter without caller authentication. RequestValidationMiddleware checks content type/body size only. Gateway config has no capability/key settings. DbWriter delegates synchronous domain writers over one connection; preserve its single-writer and no-await invariants.

Shared service-auth provides the completed strict JWS profile, explicit TokenPolicy, key provisioning/pair loading and local cryptographic verification. PlatformAgent requires Identity registration and represents the platform notary, so gateway service-capability bootstrap needs a distinct local trust configuration without an Identity/Gateway circular dependency. The existing local public-key primitives can be reused after their exact caller/key ownership rules are specified.

T112 tests must cover unsigned/wrong-domain/tampered/expired access and zero writes/events for representative routes of every domain, plus browser stored-content and Host/Origin gaps. Transport propagation coverage must include the shared GatewayClient AND all three direct sync clients. Existing client request/error, gateway endpoint/concurrency and per-service real-Gateway ASGI fixtures provide relevant controls. No ordinary data or keys were accessed by this map.
