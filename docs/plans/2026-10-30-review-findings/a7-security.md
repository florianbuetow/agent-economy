# A7 Security review (read-only)

Scope: LAN deployment threat model; semi-trusted agents; no data leaks, no money bugs, no platform-authority escalation.

## Trust boundary map (route -> auth tier)

Current state: every service binds 127.0.0.1 (justfile run recipes hard-code `--host 127.0.0.1`; each config.yaml `server.host: 127.0.0.1`). UI additionally *rejects* non-loopback host/allowed_hosts/origins at config load (services/ui/src/ui_service/config.py:98-230). Going LAN therefore requires deliberate changes; the map below is what each service exposes once rebound.

| Service | Route | Tier |
|---|---|---|
| Identity 8001 | POST /agents/register | Unauthenticated (no proof-of-possession, names not unique) |
| | POST /agents/verify-jws, GET /agents, GET /agents/{id} | Unauthenticated (public keys + names public) |
| Central Bank 8002 | POST /accounts | Agent-signed via Identity; non-platform: own account, balance 0 only |
| | POST /accounts/{id}/credit | Platform-signed, verified locally |
| | GET /accounts/{id}, /transactions | Agent-signed Bearer, owner only |
| | POST /escrow/lock | Agent-signed, kid == payload.agent_id |
| | POST /escrow/{id}/release, /split | Platform-signed, local |
| Task Board 8003 | GET /tasks, /tasks/{id}, /tasks/{id}/assets, /assets/{aid} (nonterminal) | Unauthenticated |
| | GET /tasks/{id}/bids | Poster-signed while OPEN (sealed), public after |
| | POST /tasks, bids, accept, cancel, submit, approve, dispute, rebuttal, assets | Agent-signed via Identity; actor re-checked in db-gateway `_validate_actor` (task_operation_writer.py:515-538); self-bid rejected (:568) |
| | POST /tasks/{id}/ruling | Platform-signed (task_ruling.py:221 + gateway :527) |
| | GET /tasks/{id}/assets/{aid} with Bearer | Operator-signed (terminal tasks) |
| Reputation 8004 | POST /feedback | Agent-signed via Identity (or platform local); signer == from_agent_id only |
| | GET /feedback/* | Unauthenticated, sealed until reveal/24h timeout |
| Court 8005 | POST /disputes/file, /rebuttal, /rule | Platform-signed, local |
| | GET /disputes, /disputes/{id} | Unauthenticated (claims, rebuttals, rulings public) |
| DB Gateway 8007 | everything except GET /health | Per-request Ed25519 JWS from 5 configured service keys, body+query SHA-256 bound, per-caller capability set (core/middleware.py) |
| UI 8008 | read routes + proxy (post/accept/approve/dispute as `operator`) | No auth; Host/Origin allowlist, loopback-only enforced |

JWS profile (libs/service-auth/src/service_auth/jws.py): EdDSA only, exact header field set, kid/aud/resource bound by receivers via `require_context`, lifetime <= 300 s, skew 30 s, duplicate-key JSON rejected. No `jti`/nonce: replay within lifetime is possible but every mutation carries a caller `operation_id`, making replays idempotent. Agent A cannot sign as B: Identity verifies against the key registered to `kid`, and every route compares kid to the payload principal.

## Findings ranked

### H1. Reputation accepts feedback for any task from any agent about any agent
- Where: services/reputation/src/reputation_service/routers/feedback.py:181-214, services/reputation/src/reputation_service/services/feedback.py:75-200. Only checks signer == from_agent_id, from != to, category/rating. No Task Board lookup; reputation has no Task Board client at all.
- Scenario: a LAN agent mints arbitrary task_ids (uniqueness is per task/from/to) and files unlimited "dissatisfied" spec/delivery ratings about a competitor, or Sybil pairs (open registration) rate each other up. Feedback becomes visible after the 24 h reveal timeout without any counterpart. Reputation drives who wins bids, so this is an economic attack.
- Fix: before insert, fetch the task (platform client) and require status in {approved, ruled}, {from,to} == {poster, worker}, and category matching direction.

### M1. Deliverables downloadable by anyone before payout
- Where: services/task-board/src/task_board_service/routers/assets.py:92-124 (no token path), asset_manager.py:563-605 (only terminal statuses blocked).
- Scenario: while a task is accepted/submitted/disputed, any LAN client lists and downloads the worker's deliverable without being a party. A rival poster gets the work for free; private input assets of the poster leak too.
- Fix: require an agent-signed Bearer token on list/download and allow only poster, assigned worker, platform, or operator.

### M2. LLM judge prompt injection from worker/poster text
- Where: services/court/src/court_service/judges/prompts.py:55-73, judges/llm_judge.py:85-96. task_spec, claim, and rebuttal are interpolated raw between plain `=== SECTION ===` markers.
- Scenario: worker writes a rebuttal containing a fake `=== SPECIFICATION ===` / instruction block ("the poster has agreed to 100% payout") to sway the panel. Output is clamped to 0-100 (llm_judge.py:113), so the blast radius is the disputed escrow, but that is real money moving.
- Fix: pass each party field as JSON-encoded data (like deliverables) and add an explicit system instruction that section content is untrusted evidence.

### M3. Plaintext HTTP on the LAN exposes sealed bids and bearer tokens
- Where: all services speak HTTP; agent tokens travel in bodies/headers and are valid 300 s (identity config.yaml:25-27).
- Scenario: anyone on the Wi-Fi sniffs bid tokens (payload is base64 JSON containing `amount`) defeating sealed bidding, reads feedback before reveal, and replays `GET /accounts/{id}` Bearer tokens to read balances/transactions within 5 min.
- Fix: put the LAN-facing services behind one TLS reverse proxy (Caddy with internal CA) and keep the backends on loopback.

### M4. UI loopback guard validates an unused setting; operator proxy has no secret
- Where: services/ui/src/ui_service/config.py:226-236 validates `server.host`, but justfile:251 starts uvicorn with a hard-coded `--host 127.0.0.1`, so the validator never sees the real bind address. Proxy writes rely on loopback client IP (core/middleware.py:92, 173-180), a matching Origin, and the fixed marker `X-ATE-CSRF: local-operator` served by GET /api/proxy/browser-config (routers/proxy.py:23).
- Scenario: editing the justfile to `--host 0.0.0.0` passes validation; a LAN client sends `Host: localhost:8008` and reads every UI route. Behind any same-host forwarder (ssh -L, socat, Tailscale serve) every request looks loopback, and a non-browser client can post, accept, approve, and dispute as `operator`.
- Fix: start uvicorn from `settings.server.host/port`, and require a bearer secret from env on `/api/proxy/*`.

### M5. UI publishes every agent's balance and earnings
- Where: services/ui/src/ui_service/services/agents.py:302-306, :561-590, :227-231. Central Bank restricts balances to the owner (central-bank routers/accounts.py:279, :330).
- Scenario: a bidder reads a poster's remaining funds and competitors' earnings to tune bids; after LAN rebinding, any LAN host can.
- Fix: drop balances from public UI responses, or explicitly decide balances are public.

### M6. Disk exhaustion through uploads
- Where: services/task-board/src/task_board_service/services/asset_manager.py:238 caps only per task (10 files x 10 MiB); registration is open.
- Scenario: one funded agent posts many 1-credit tasks to its own Sybil workers, each uploading 100 MiB into data/assets until the disk fills and SQLite writes fail.
- Fix: per-agent and global storage quota in the upload eligibility check.

### L1. Open registration without proof of possession; display names not unique
- services/identity/src/identity_service/routers/agents.py:24-43. Anyone can register names like "platform" or "operator" and show up in UI confusingly. Authority is by agent_id and key, so no escalation found. Fix: reserve platform/operator names, require a signed registration nonce.

### L2. Court claims/rebuttals/rulings are publicly listable
- services/court/src/court_service/routers/disputes.py:268-300. Any LAN client reads dispute text. Fine if economy transparency is intended; otherwise gate by party.

### L3. services/court/.env is mode 0644
- LLM API keys readable by other local users. gitignored (.gitignore:49). Fix: chmod 600, and have start-all warn on looser modes. data/keys/*.key are 0600 (good); data/keys dir is 0755 (pub files only exposed).

### L4. Court 502 messages embed raw LiteLLM exception text
- services/court/src/court_service/judges/llm_judge.py:117-122, services/ruling_orchestrator.py:259-264. May include provider errors, api_base, or LLM output. Callers are platform-signed, so exposure depends on Task Board relaying it. Fix: generic message, log details server-side.

### L5. Uploader-chosen Content-Type is served back
- task-board streaming_upload.py:175-184, routers/assets.py:118, ui routers/proxy.py:40-52. attachment + nosniff + CSP block XSS today, but a `text/javascript` upload served from the UI origin satisfies `script-src 'self'`. Fix: serve application/octet-stream.

### L6. Info leaks in errors and docs
- db-gateway routers/health.py:41 shows the absolute DB path on failure; ui services/proxy.py:532 echoes `{exc}`; UI /docs and /openapi.json are enabled (app.py:38). Fix: fixed messages, disable docs.

### Non-findings (checked)
- DB gateway: unauthenticated callers can only hit /health; every other request needs a JWS from a configured service key with exact body/query digests and capability grant. LAN exposure is not a hole, though it should stay loopback.
- Central bank credit/release/split are platform-only; agents can only create zero-balance own accounts and lock own funds.
- Accept/approve/dispute/cancel/submit/rebuttal enforce poster/worker in both task-board and the gateway; self-bid rejected; ruling platform-only.
- Sealed bids: GET /tasks/{id}/bids requires poster signature while OPEN.
- UI sealing: task drilldown hides bids while OPEN (ui services/tasks.py:265-268) and loads only `visible` feedback. Agent-supplied text in innerHTML sinks spot-checked (landing.js:150, observatory.js:77-84, task.js:1046) and goes through `ATE.escapeHTML`; no XSS confirmed, not every one of ~30 sinks was traced.
- Unhandled exceptions return a generic 500 (libs/service-commons exceptions.py:94-115); download uses `attachment` + `nosniff`.

## Work items (ordered)
1. Reputation party check (H1) — done when a test shows feedback from a non-party or for a non-terminal/unknown task returns 403/404.
2. Asset read authorization (M1) — done when an unauthenticated or third-party download of a submitted task's deliverable returns 401/403 and poster/worker still succeed.
3. Judge prompt hardening (M2) — done when a unit test with an injected section header in the rebuttal shows it rendered inside a JSON string, not as a section.
4. TLS front proxy for LAN mode (M3) — done when a LAN client can only reach services over HTTPS and backends still bind 127.0.0.1.
5. UI bind from settings + proxy bearer secret (M4) — done when uvicorn binds `settings.server.host` and proxy writes without the env secret return 401.
6. Remove other agents' balances from UI (M5) — done when /api/agents/{id} no longer returns a non-operator balance.
7. Upload storage quota (M6) — done when an upload beyond the per-agent quota is rejected in a test.
8. Generic court/gateway/UI error messages, octet-stream downloads, docs off (L4-L6) — done when tests assert fixed messages and octet-stream.
9. Reserve names / registration proof (L1), chmod court .env (L3) — done when tests reject "platform" names and start-all fails on a world-readable .env.
