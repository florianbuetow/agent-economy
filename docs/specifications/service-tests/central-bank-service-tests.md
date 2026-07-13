# Central Bank Service — Production Release Test Specification

## Purpose

This document is the release-gate test specification for the Central Bank Service.
It is intentionally strict and unambiguous:

- Every negative case has one required status code and one required error code.
- Every failing response must use the standard error envelope.
- Any behavior not listed here is out of scope for release sign-off.

This document focuses only on core functionality and endpoint abuse resistance.
Nice-to-have tests are intentionally excluded. Authentication/authorization concerns
(JWS verification mechanics, the two-tier local-vs-Identity verification split, and
error precedence) are specified separately in `central-bank-service-auth-tests.md`.

---

## Required API Error Contract (Normative for Release)

All failing responses must be JSON in this format:

```json
{
  "error": "error_code",
  "message": "Human-readable description",
  "details": {}
}
```

Error codes are lowercase `snake_case` (R1). Required status/error mappings:

| Status | Error Code                  | Required When |
|--------|------------------------------|---------------|
| 400    | `invalid_jws`               | JWS token is malformed, missing, empty, or not a string |
| 400    | `invalid_json`              | Request body is not valid JSON or not a JSON object |
| 400    | `invalid_payload`           | JWS payload missing required fields, wrong `action`, or wrong field type for a payload field |
| 400    | `invalid_amount`            | Amount, balance, or `worker_pct` is not a valid integer in the required range |
| 400    | `payload_mismatch`          | JWS payload field does not match URL parameter, or duplicate credit reference with different amount, or `poster_account_id` does not match escrow payer |
| 402    | `insufficient_funds`        | Escrow lock would cause negative balance |
| 403    | `forbidden`                 | Agent accessing another's account, non-platform agent doing platform-only work, or JWS signature verification failed |
| 404    | `account_not_found`         | No account with this ID |
| 404    | `agent_not_found`           | Agent does not exist in the Identity service |
| 404    | `escrow_not_found`          | No escrow with this ID |
| 405    | `method_not_allowed`        | Unsupported HTTP method on a defined route |
| 409    | `account_exists`            | Account already created for this agent |
| 409    | `escrow_already_resolved`   | Escrow has already been released or split |
| 409    | `escrow_already_locked`     | Escrow already locked for this task with a different amount |
| 413    | `payload_too_large`         | Request body exceeds configured `request.max_body_size` |
| 415    | `unsupported_media_type`    | `Content-Type` is not `application/json` for JSON endpoints |
| 502    | `identity_service_unavailable` | Cannot reach the Identity service for JWS verification or agent lookup (never on `credit`/`escrow_release`/`escrow_split`, which verify locally) |
| 503    | `service_not_ready`         | The ledger, Identity client, or platform agent is not initialized, or (on `GET /health`) the DB Gateway is unreachable |

---

## Test Data Conventions

- `jws(signer, payload)` constructs a valid JWS compact token signed by the given agent's Ed25519 private key with the given JSON payload. The JWS header is `{"alg": "EdDSA", "typ": "JWT", "kid": "<signer_agent_id>"}`.
- `tampered_jws(signer, payload)` constructs a JWS token where the signature does not match the payload (e.g., the payload was modified after signing).
- `platform` refers to the registered platform agent — whichever agent the Central Bank's own `PlatformAgent` (loaded via `platform.agent_config_path`) is running as. There is no `platform.agent_id` config value to reference.
- `alice`, `bob`, `carol` are regular agents registered in the Identity service, each with their own Ed25519 keypair. Unless a test's Setup explicitly creates an account for an agent, assume no account exists yet — `create_account` may be called by the agent itself (self-service) or by `platform`.
- All account IDs equal the agent's `agent_id` (e.g., `a-<uuid4>`).
- Transaction IDs match `tx-<uuid4>`.
- Escrow IDs match `esc-<uuid4>`.
- All timestamps are valid ISO 8601.
- All monetary amounts are positive integers unless explicitly testing zero or negative values.

---

## Category 1: Account Creation (`POST /accounts`)

`POST /accounts` has two modes, both verified via Identity's `POST /agents/verify-jws` (see the `create_account` exception in `central-bank-service-auth-specs.md`): **self-service** (any registered agent creates their own account at zero balance) and **platform-funded** (the platform creates an account for any agent at any non-negative balance).

### ACC-01 Platform creates account with positive initial balance
**Setup:** Register agent `alice` in the Identity service. Generate platform JWS.
**Action:** `POST /accounts` with body `{"token": jws(platform, {"action": "create_account", "agent_id": alice, "initial_balance": 50})}`.
**Expected:**
- `201 Created`
- Body includes `account_id`, `balance`, `created_at`
- `account_id` equals `alice`'s agent ID
- `balance` is `50`
- `created_at` is valid ISO 8601 timestamp

### ACC-02 Platform creates account with zero initial balance
**Setup:** Register agent `bob` in the Identity service.
**Action:** `POST /accounts` with body `{"token": jws(platform, {"action": "create_account", "agent_id": bob, "initial_balance": 0})}`.
**Expected:**
- `201 Created`
- `balance` is `0`

### ACC-03 Initial balance greater than zero creates credit transaction
**Setup:** Create account for `alice` with `initial_balance: 50` (platform-funded).
**Action:** Query `alice`'s transaction history.
**Expected:**
- Transaction list contains exactly 1 entry
- Transaction `type` is `"credit"`
- Transaction `amount` is `50`
- Transaction `balance_after` is `50`
- Transaction `reference` is `"initial_balance"`

### ACC-04 Duplicate account is rejected
**Setup:** Create account for `alice`.
**Action:** `POST /accounts` with `{"token": jws(platform, {"action": "create_account", "agent_id": alice, "initial_balance": 10})}`.
**Expected:**
- `409 Conflict`
- `error = account_exists`

### ACC-05 Agent not found in Identity service
**Action:** `POST /accounts` with `{"token": jws(platform, {"action": "create_account", "agent_id": "a-nonexistent-uuid", "initial_balance": 10})}` where the agent does not exist in the Identity service.
**Expected:**
- `404 Not Found`
- `error = agent_not_found`

### ACC-06 Non-platform signer creating another agent's account is rejected
**Setup:** Register agent `alice` with her own keypair.
**Action:** `POST /accounts` with `{"token": jws(alice, {"action": "create_account", "agent_id": "a-someone-else", "initial_balance": 0})}`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### ACC-07 Negative initial balance is rejected
**Action:** `POST /accounts` with `{"token": jws(platform, {"action": "create_account", "agent_id": alice, "initial_balance": -1})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_amount`

### ACC-08 Non-integer initial balance is rejected
**Action:** `POST /accounts` with `{"token": jws(platform, {"action": "create_account", "agent_id": alice, "initial_balance": 10.5})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_amount`

### ACC-09 Missing agent_id in JWS payload
**Action:** `POST /accounts` with `{"token": jws(platform, {"action": "create_account", "initial_balance": 10})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### ACC-10 Missing initial_balance in JWS payload
**Action:** `POST /accounts` with `{"token": jws(platform, {"action": "create_account", "agent_id": alice})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### ACC-11 Wrong action in JWS payload
**Action:** `POST /accounts` with `{"token": jws(platform, {"action": "credit", "agent_id": alice, "initial_balance": 10})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### ACC-12 Malformed JSON body
**Action:** `POST /accounts` with body `{not valid json`.
**Expected:**
- `400 Bad Request`
- `error = invalid_json`

### ACC-13 Missing token field in body
**Action:** `POST /accounts` with body `{"nottoken": "something"}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

### ACC-14 Tampered JWS token
**Action:** `POST /accounts` with `{"token": tampered_jws(platform, {"action": "create_account", "agent_id": alice, "initial_balance": 10})}`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### ACC-15 Self-service: agent creates their own zero-balance account
**Setup:** Register agent `alice` in the Identity service. No account exists for her yet.
**Action:** `POST /accounts` with `{"token": jws(alice, {"action": "create_account", "agent_id": alice, "initial_balance": 0})}` — signed by Alice herself, not the platform.
**Expected:**
- `201 Created`
- `account_id` equals `alice`'s agent ID
- `balance` is `0`

### ACC-16 Self-service: agent requesting a non-zero balance for their own account is rejected
**Setup:** Register agent `alice` in the Identity service.
**Action:** `POST /accounts` with `{"token": jws(alice, {"action": "create_account", "agent_id": alice, "initial_balance": 100})}`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### ACC-17 Self-service duplicate account is rejected
**Setup:** Agent `alice` self-creates her own zero-balance account (ACC-15).
**Action:** `POST /accounts` again with the same self-service token (or a fresh equivalent one).
**Expected:**
- `409 Conflict`
- `error = account_exists`

---

## Category 2: Credit (`POST /accounts/{account_id}/credit`)

### CR-01 Valid credit increases balance
**Setup:** Create account for `alice` with `initial_balance: 100`.
**Action:** `POST /accounts/{alice}/credit` with `{"token": jws(platform, {"action": "credit", "account_id": alice, "amount": 50, "reference": "salary_round_1"})}`.
**Expected:**
- `200 OK`
- Body includes `tx_id` and `balance_after`
- `tx_id` matches `tx-<uuid4>`
- `balance_after` is `150`

### CR-02 Multiple credits accumulate correctly
**Setup:** Create account for `alice` with `initial_balance: 0`.
**Action:** Credit `alice` with `amount: 30, reference: "bonus_1"`, then `amount: 20, reference: "bonus_2"`.
**Expected:**
- First credit: `balance_after` is `30`
- Second credit: `balance_after` is `50`

### CR-03 Idempotent credit returns same tx_id
**Setup:** Create account for `alice` with `initial_balance: 0`. Credit `alice` with `amount: 25, reference: "salary_round_1"` and capture `tx_id`.
**Action:** Send the identical credit request again (`amount: 25, reference: "salary_round_1"`).
**Expected:**
- `200 OK`
- Returned `tx_id` matches the original `tx_id`
- `balance_after` matches the original `balance_after`
- Balance is not double-credited

### CR-04 Duplicate reference with different amount is rejected
**Setup:** Credit `alice` with `amount: 25, reference: "salary_round_1"`.
**Action:** Credit `alice` with `amount: 30, reference: "salary_round_1"`.
**Expected:**
- `400 Bad Request`
- `error = payload_mismatch`

### CR-05 Account not found
**Action:** `POST /accounts/a-nonexistent-uuid/credit` with valid platform JWS.
**Expected:**
- `404 Not Found`
- `error = account_not_found`

### CR-06 Non-platform signer is rejected
**Setup:** Create account for `alice`.
**Action:** `POST /accounts/{alice}/credit` with `{"token": jws(alice, {"action": "credit", "account_id": alice, "amount": 10, "reference": "self_credit"})}`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### CR-07 Zero amount is rejected
**Action:** `POST /accounts/{alice}/credit` with `{"token": jws(platform, {"action": "credit", "account_id": alice, "amount": 0, "reference": "zero_credit"})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_amount`

### CR-08 Negative amount is rejected
**Action:** `POST /accounts/{alice}/credit` with `{"token": jws(platform, {"action": "credit", "account_id": alice, "amount": -10, "reference": "neg_credit"})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_amount`

### CR-09 Missing reference in JWS payload
**Action:** `POST /accounts/{alice}/credit` with `{"token": jws(platform, {"action": "credit", "account_id": alice, "amount": 10})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### CR-10 Payload account_id mismatch with URL
**Setup:** Create accounts for `alice` and `bob`.
**Action:** `POST /accounts/{alice}/credit` with `{"token": jws(platform, {"action": "credit", "account_id": bob, "amount": 10, "reference": "mismatch"})}`.
**Expected:**
- `400 Bad Request`
- `error = payload_mismatch`

### CR-11 Wrong action in JWS payload
**Action:** `POST /accounts/{alice}/credit` with `{"token": jws(platform, {"action": "create_account", "account_id": alice, "amount": 10, "reference": "wrong_action"})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

---

## Category 3: Balance Query (`GET /accounts/{account_id}`)

### BAL-01 Get own balance
**Setup:** Create account for `alice` with `initial_balance: 100`.
**Action:** `GET /accounts/{alice}` with header `Authorization: Bearer jws(alice, {"action": "get_balance", "account_id": alice})`.
**Expected:**
- `200 OK`
- Body includes `account_id`, `balance`, `created_at`
- `account_id` equals `alice`'s agent ID
- `balance` is `100`
- `created_at` is valid ISO 8601 timestamp

### BAL-02 Balance reflects credits and escrow locks accurately
**Setup:** Create account for `alice` with `initial_balance: 100`. Credit `alice` with `amount: 50`. Lock escrow of `amount: 30` from `alice`.
**Action:** `GET /accounts/{alice}` with valid bearer token.
**Expected:**
- `200 OK`
- `balance` is `120` (100 + 50 - 30)

### BAL-03 Account not found
**Action:** `GET /accounts/a-nonexistent-uuid` with bearer token signed by a valid agent whose account does not exist at that ID.
**Expected:**
- `404 Not Found`
- `error = account_not_found`

### BAL-04 Wrong agent accessing another's account
**Setup:** Create accounts for `alice` and `bob`.
**Action:** `GET /accounts/{alice}` with header `Authorization: Bearer jws(bob, {"action": "get_balance", "account_id": alice})`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### BAL-05 Wrong action in JWS payload
**Setup:** Create account for `alice`.
**Action:** `GET /accounts/{alice}` with header `Authorization: Bearer jws(alice, {"action": "get_transactions", "account_id": alice})`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### BAL-06 Payload account_id mismatch with URL
**Setup:** Create accounts for `alice` and `bob`.
**Action:** `GET /accounts/{alice}` with header `Authorization: Bearer jws(alice, {"action": "get_balance", "account_id": bob})`.
**Expected:**
- `400 Bad Request`
- `error = payload_mismatch`

### BAL-07 Missing Bearer token
**Setup:** Create account for `alice`.
**Action:** `GET /accounts/{alice}` with no `Authorization` header.
**Expected:**
- `400 Bad Request`
- `error = invalid_jws`

---

## Category 4: Transaction History (`GET /accounts/{account_id}/transactions`)

### TX-01 History ordered by timestamp ASC then tx_id ASC
**Setup:** Create account for `alice` with `initial_balance: 100`. Credit `alice` twice with different references.
**Action:** `GET /accounts/{alice}/transactions` with valid bearer token.
**Expected:**
- `200 OK`
- Body includes `transactions` array
- Transactions are ordered by `timestamp` ascending, then `tx_id` ascending
- First transaction is the `initial_balance` credit

### TX-02 Empty list for account with zero initial balance
**Setup:** Create account for `alice` with `initial_balance: 0`.
**Action:** `GET /accounts/{alice}/transactions` with valid bearer token.
**Expected:**
- `200 OK`
- `transactions` is an empty array `[]`

### TX-03 History includes credit, escrow_lock, and escrow_release types
**Setup:** Create account for `alice` with `initial_balance: 100`. Lock escrow of `amount: 30` from `alice`. Release escrow to `alice` (as recipient).
**Action:** `GET /accounts/{alice}/transactions` with valid bearer token.
**Expected:**
- `200 OK`
- `transactions` array contains entries with `type` values `"credit"`, `"escrow_lock"`, and `"escrow_release"`

### TX-04 Each transaction has all required fields
**Setup:** Create account for `alice` with `initial_balance: 50`.
**Action:** `GET /accounts/{alice}/transactions` with valid bearer token.
**Expected:**
- `200 OK`
- Each transaction object contains exactly: `tx_id`, `type`, `amount`, `balance_after`, `reference`, `timestamp`
- `tx_id` matches `tx-<uuid4>`
- `amount` is a positive integer
- `balance_after` is a non-negative integer
- `timestamp` is valid ISO 8601

### TX-05 Account not found
**Action:** `GET /accounts/a-nonexistent-uuid/transactions` with bearer token signed by a valid agent whose account does not exist at that ID.
**Expected:**
- `404 Not Found`
- `error = account_not_found`

### TX-06 Wrong agent accessing another's transactions
**Setup:** Create accounts for `alice` and `bob`.
**Action:** `GET /accounts/{alice}/transactions` with header `Authorization: Bearer jws(bob, {"action": "get_transactions", "account_id": alice})`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### TX-07 Wrong action in JWS payload
**Setup:** Create account for `alice`.
**Action:** `GET /accounts/{alice}/transactions` with header `Authorization: Bearer jws(alice, {"action": "get_balance", "account_id": alice})`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

---

## Category 5: Escrow Lock (`POST /escrow/lock`)

### ESC-01 Valid escrow lock
**Setup:** Create account for `alice` with `initial_balance: 100`.
**Action:** `POST /escrow/lock` with `{"token": jws(alice, {"action": "escrow_lock", "agent_id": alice, "amount": 30, "task_id": "T-001"})}`.
**Expected:**
- `201 Created`
- Body includes `escrow_id`, `amount`, `task_id`, `status`
- `escrow_id` matches `esc-<uuid4>`
- `amount` is `30`
- `task_id` is `"T-001"`
- `status` is `"locked"`

### ESC-02 Balance decreases by lock amount
**Setup:** Create account for `alice` with `initial_balance: 100`. Lock escrow of `amount: 30`.
**Action:** Query `alice`'s balance.
**Expected:**
- `balance` is `70`

### ESC-03 Escrow lock creates escrow_lock transaction with task_id as reference
**Setup:** Create account for `alice` with `initial_balance: 100`. Lock escrow of `amount: 30` for `task_id: "T-001"`.
**Action:** Query `alice`'s transaction history.
**Expected:**
- Transaction list includes an entry with `type: "escrow_lock"`, `amount: 30`, `reference: "T-001"`, `balance_after: 70`

### ESC-04 Insufficient funds
**Setup:** Create account for `alice` with `initial_balance: 10`.
**Action:** `POST /escrow/lock` with `{"token": jws(alice, {"action": "escrow_lock", "agent_id": alice, "amount": 50, "task_id": "T-002"})}`.
**Expected:**
- `402 Payment Required`
- `error = insufficient_funds`

### ESC-05 Account not found
**Action:** `POST /escrow/lock` with `{"token": jws(alice, {"action": "escrow_lock", "agent_id": alice, "amount": 10, "task_id": "T-003"})}` where `alice` has no account.
**Expected:**
- `404 Not Found`
- `error = account_not_found`

### ESC-06 Agent locking another's funds
**Setup:** Create accounts for `alice` and `bob`.
**Action:** `POST /escrow/lock` with `{"token": jws(bob, {"action": "escrow_lock", "agent_id": alice, "amount": 10, "task_id": "T-004"})}` (signed by `bob` but claims `alice`'s agent_id).
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### ESC-07 Idempotent lock returns same escrow_id
**Setup:** Create account for `alice` with `initial_balance: 100`. Lock escrow with `amount: 30, task_id: "T-005"` and capture `escrow_id`.
**Action:** Send the identical escrow lock request again (`amount: 30, task_id: "T-005"`).
**Expected:**
- `201 Created`
- Returned `escrow_id` matches the original `escrow_id`
- Balance is not double-debited

### ESC-08 Duplicate task_id with different amount
**Setup:** Lock escrow for `alice` with `amount: 30, task_id: "T-006"`.
**Action:** Lock escrow for `alice` with `amount: 50, task_id: "T-006"`.
**Expected:**
- `409 Conflict`
- `error = escrow_already_locked`

### ESC-09 Zero amount is rejected
**Action:** `POST /escrow/lock` with `{"token": jws(alice, {"action": "escrow_lock", "agent_id": alice, "amount": 0, "task_id": "T-007"})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_amount`

### ESC-10 Negative amount is rejected
**Action:** `POST /escrow/lock` with `{"token": jws(alice, {"action": "escrow_lock", "agent_id": alice, "amount": -10, "task_id": "T-008"})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_amount`

### ESC-11 Missing task_id in JWS payload
**Action:** `POST /escrow/lock` with `{"token": jws(alice, {"action": "escrow_lock", "agent_id": alice, "amount": 10})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### ESC-12 Missing agent_id in JWS payload
**Action:** `POST /escrow/lock` with `{"token": jws(alice, {"action": "escrow_lock", "amount": 10, "task_id": "T-009"})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### ESC-13 Wrong action in JWS payload
**Action:** `POST /escrow/lock` with `{"token": jws(alice, {"action": "credit", "agent_id": alice, "amount": 10, "task_id": "T-010"})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

---

## Category 6: Escrow Release (`POST /escrow/{escrow_id}/release`)

In production this endpoint is called by the Task Board (platform-signed), on approval, review-timeout auto-approve, cancel/expiry (refund to poster), or after a ruling with `worker_pct` of 0 or 100. These tests exercise the endpoint directly.

### REL-01 Valid full release to recipient
**Setup:** Create accounts for `alice` (poster, `initial_balance: 100`) and `bob` (worker, `initial_balance: 0`). Lock escrow of `amount: 50` from `alice` for `task_id: "T-100"`. Capture `escrow_id`.
**Action:** `POST /escrow/{escrow_id}/release` with `{"token": jws(platform, {"action": "escrow_release", "escrow_id": escrow_id, "recipient_account_id": bob})}`.
**Expected:**
- `200 OK`
- Body includes `escrow_id`, `status`, `recipient`, `amount`
- `status` is `"released"`
- `recipient` equals `bob`'s account ID
- `amount` is `50`

### REL-02 Recipient balance increases by escrow amount
**Setup:** Same as REL-01. Release escrow to `bob`.
**Action:** Query `bob`'s balance.
**Expected:**
- `balance` is `50`

### REL-03 Release creates escrow_release transaction on recipient
**Setup:** Same as REL-01. Release escrow to `bob`.
**Action:** Query `bob`'s transaction history.
**Expected:**
- Transaction list includes an entry with `type: "escrow_release"`, `amount: 50`, `reference` equal to the `escrow_id`, `balance_after: 50`

### REL-04 Escrow not found
**Action:** `POST /escrow/esc-nonexistent-uuid/release` with valid platform JWS.
**Expected:**
- `404 Not Found`
- `error = escrow_not_found`

### REL-05 Already resolved escrow
**Setup:** Lock escrow from `alice`, release it to `bob`.
**Action:** Attempt to release the same escrow again.
**Expected:**
- `409 Conflict`
- `error = escrow_already_resolved`

### REL-06 Non-platform signer is rejected
**Setup:** Lock escrow from `alice`. Capture `escrow_id`.
**Action:** `POST /escrow/{escrow_id}/release` with `{"token": jws(alice, {"action": "escrow_release", "escrow_id": escrow_id, "recipient_account_id": bob})}`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### REL-07 Recipient account not found
**Setup:** Lock escrow from `alice`. Capture `escrow_id`.
**Action:** `POST /escrow/{escrow_id}/release` with `{"token": jws(platform, {"action": "escrow_release", "escrow_id": escrow_id, "recipient_account_id": "a-nonexistent-uuid"})}`.
**Expected:**
- `404 Not Found`
- `error = account_not_found`

### REL-08 Payload escrow_id mismatch with URL
**Setup:** Lock escrow from `alice`. Capture `escrow_id`.
**Action:** `POST /escrow/{escrow_id}/release` with `{"token": jws(platform, {"action": "escrow_release", "escrow_id": "esc-different-uuid", "recipient_account_id": bob})}`.
**Expected:**
- `400 Bad Request`
- `error = payload_mismatch`

### REL-09 Missing recipient_account_id in JWS payload
**Setup:** Lock escrow from `alice`. Capture `escrow_id`.
**Action:** `POST /escrow/{escrow_id}/release` with `{"token": jws(platform, {"action": "escrow_release", "escrow_id": escrow_id})}`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

---

## Category 7: Escrow Split (`POST /escrow/{escrow_id}/split`)

In production this endpoint is called by the Task Board (platform-signed) after a Court ruling — Court never calls the Central Bank directly (R4).

### SPL-01 Even 50/50 split
**Setup:** Create accounts for `alice` (poster, `initial_balance: 1000`) and `bob` (worker, `initial_balance: 0`). Lock escrow of `amount: 500` from `alice` for `task_id: "T-200"`. Capture `escrow_id`.
**Action:** `POST /escrow/{escrow_id}/split` with `{"token": jws(platform, {"action": "escrow_split", "escrow_id": escrow_id, "worker_account_id": bob, "worker_pct": 50, "poster_account_id": alice})}`.
**Expected:**
- `200 OK`
- Body includes `escrow_id`, `status`, `worker_amount`, `poster_amount`
- `status` is `"split"`
- `worker_amount` is `250`
- `poster_amount` is `250`

### SPL-02 Uneven 80/20 split with floor math
**Setup:** Lock escrow of `amount: 500` from `alice`. Capture `escrow_id`.
**Action:** Split with `worker_pct: 80`.
**Expected:**
- `200 OK`
- `worker_amount` is `400` (floor(500 * 80 / 100))
- `poster_amount` is `100` (500 - 400)

### SPL-03 Worker gets all (100/0 split)
**Setup:** Lock escrow of `amount: 100` from `alice`. Capture `escrow_id`.
**Action:** Split with `worker_pct: 100`.
**Expected:**
- `200 OK`
- `worker_amount` is `100`
- `poster_amount` is `0`
- No transaction row is written on the poster's account for this split (zero-amount leg skipped)

### SPL-04 Poster gets all (0/100 split)
**Setup:** Lock escrow of `amount: 100` from `alice`. Capture `escrow_id`.
**Action:** Split with `worker_pct: 0`.
**Expected:**
- `200 OK`
- `worker_amount` is `0`
- `poster_amount` is `100`
- No transaction row is written on the worker's account for this split (zero-amount leg skipped)

### SPL-05 Odd amount with floor rounding
**Setup:** Lock escrow of `amount: 101` from `alice`. Capture `escrow_id`.
**Action:** Split with `worker_pct: 33`.
**Expected:**
- `200 OK`
- `worker_amount` is `33` (floor(101 * 33 / 100) = floor(33.33) = 33)
- `poster_amount` is `68` (101 - 33)

### SPL-06 Both account balances updated correctly after split
**Setup:** Create accounts for `alice` (poster, `initial_balance: 200`) and `bob` (worker, `initial_balance: 10`). Lock escrow of `amount: 100` from `alice`. Split with `worker_pct: 60`.
**Action:** Query balances for both `alice` and `bob`.
**Expected:**
- `alice`'s balance is `140` (200 - 100 + 40)
- `bob`'s balance is `70` (10 + 60)

### SPL-07 Split creates escrow_release transactions on both accounts
**Setup:** Lock escrow of `amount: 100` from `alice`. Split with `worker_pct: 60` between `bob` (worker) and `alice` (poster). Capture `escrow_id`.
**Action:** Query transaction histories for both `alice` and `bob`.
**Expected:**
- `bob`'s transactions include an `escrow_release` entry with `amount: 60` and `reference` equal to the `escrow_id`
- `alice`'s transactions include an `escrow_release` entry with `amount: 40` and `reference` equal to the `escrow_id`

### SPL-08 Escrow status changes to split
**Setup:** Lock escrow from `alice`. Split it.
**Action:** Inspect the split response.
**Expected:**
- `status` is `"split"`

### SPL-09 Escrow not found
**Action:** `POST /escrow/esc-nonexistent-uuid/split` with valid platform JWS.
**Expected:**
- `404 Not Found`
- `error = escrow_not_found`

### SPL-10 Already resolved escrow
**Setup:** Lock escrow from `alice`, release it to `bob`.
**Action:** Attempt to split the same escrow.
**Expected:**
- `409 Conflict`
- `error = escrow_already_resolved`

### SPL-11 Non-platform signer is rejected
**Setup:** Lock escrow from `alice`. Capture `escrow_id`.
**Action:** `POST /escrow/{escrow_id}/split` with `{"token": jws(alice, {"action": "escrow_split", "escrow_id": escrow_id, "worker_account_id": bob, "worker_pct": 50, "poster_account_id": alice})}`.
**Expected:**
- `403 Forbidden`
- `error = forbidden`

### SPL-12 worker_pct greater than 100
**Setup:** Lock escrow from `alice`. Capture `escrow_id`.
**Action:** Split with `worker_pct: 101`.
**Expected:**
- `400 Bad Request`
- `error = invalid_amount`

### SPL-13 worker_pct less than 0
**Setup:** Lock escrow from `alice`. Capture `escrow_id`.
**Action:** Split with `worker_pct: -1`.
**Expected:**
- `400 Bad Request`
- `error = invalid_amount`

### SPL-14 Non-integer worker_pct
**Setup:** Lock escrow from `alice`. Capture `escrow_id`.
**Action:** Split with `worker_pct: 33.5`.
**Expected:**
- `400 Bad Request`
- `error = invalid_payload`

### SPL-15 Poster account_id does not match escrow payer
**Setup:** Create accounts for `alice`, `bob`, and `carol`. Lock escrow from `alice`. Capture `escrow_id`.
**Action:** `POST /escrow/{escrow_id}/split` with `{"token": jws(platform, {"action": "escrow_split", "escrow_id": escrow_id, "worker_account_id": bob, "worker_pct": 50, "poster_account_id": carol})}`.
**Expected:**
- `400 Bad Request`
- `error = payload_mismatch`

### SPL-16 Worker account not found
**Setup:** Lock escrow from `alice`. Capture `escrow_id`.
**Action:** `POST /escrow/{escrow_id}/split` with `{"token": jws(platform, {"action": "escrow_split", "escrow_id": escrow_id, "worker_account_id": "a-nonexistent-uuid", "worker_pct": 50, "poster_account_id": alice})}`.
**Expected:**
- `404 Not Found`
- `error = account_not_found`

### SPL-17 Zero-amount split legs are skipped identically on both storage backends
**Setup:** Lock escrow of `amount: 100` from `alice`. Capture `escrow_id`. Note the number of transactions on `bob`'s (worker) account before the split.
**Action:** Split with `worker_pct: 0`.
**Expected:**
- `worker_amount` is `0`, `poster_amount` is `100`
- The count of `bob`'s transactions is unchanged (no zero-amount transaction row written)
- This holds identically whether the ledger is backed by `InMemoryLedgerStore` or the DB-Gateway-backed `LedgerDbClient` (contract-tested by `tests/unit/test_ledger_store_contract.py` against both)

---

## Category 8: Health (`GET /health`)

### HLTH-01 Health schema is correct
**Action:** `GET /health`
**Expected:**
- `200 OK`
- Body contains `status`, `uptime_seconds`, `started_at`, `total_accounts`, `total_escrowed`
- `status` is `"ok"`
- `uptime_seconds` is a non-negative number
- `started_at` is valid ISO 8601 timestamp
- `total_accounts` is a non-negative integer
- `total_escrowed` is a non-negative integer

### HLTH-02 total_accounts is accurate
**Setup:** Create `N` accounts.
**Action:** `GET /health`
**Expected:**
- `total_accounts` equals `N`

### HLTH-03 total_escrowed equals sum of locked escrows only
**Setup:** Create accounts for `alice` and `bob`. Lock escrow of `amount: 30` from `alice`. Lock escrow of `amount: 20` from `alice`. Release the first escrow to `bob`.
**Action:** `GET /health`
**Expected:**
- `total_escrowed` is `20` (only the still-locked escrow counts; released escrow is excluded)

### HLTH-04 Uptime is monotonic
**Action:** Call `GET /health` twice with a delay of at least 1 second.
**Expected:**
- Second `uptime_seconds` is strictly greater than first `uptime_seconds`

### HLTH-05 Degrades to 503 when the DB Gateway is unreachable
**Setup:** Configure (or fake) the ledger so its `count_accounts`/`total_escrowed` calls raise as they would against a downed DB Gateway.
**Action:** `GET /health`
**Expected:**
- `503 Service Unavailable` (not `500`)
- `error = service_not_ready`
- Response still conforms to the standard 3-field error envelope

---

## Category 9: HTTP Method Misuse

### HTTP-01 Wrong method on defined routes is blocked
**Action:** Send unsupported HTTP methods:
- `GET /accounts` (POST only)
- `PUT /accounts`
- `DELETE /accounts`
- `POST /accounts/{account_id}` (GET only)
- `PUT /accounts/{account_id}`
- `DELETE /accounts/{account_id}`
- `POST /accounts/{account_id}/transactions` (GET only)
- `PUT /accounts/{account_id}/transactions`
- `DELETE /accounts/{account_id}/transactions`
- `PUT /accounts/{account_id}/credit`
- `DELETE /accounts/{account_id}/credit`
- `GET /escrow/lock` (POST only)
- `PUT /escrow/lock`
- `DELETE /escrow/lock`
- `GET /escrow/{escrow_id}/release` (POST only)
- `PUT /escrow/{escrow_id}/release`
- `DELETE /escrow/{escrow_id}/release`
- `GET /escrow/{escrow_id}/split` (POST only)
- `PUT /escrow/{escrow_id}/split`
- `DELETE /escrow/{escrow_id}/split`
- `POST /health` (GET only)
**Expected:** `405`, `error = method_not_allowed` for each

---

## Category 10: Cross-Cutting Security Assertions

### SEC-01 Error envelope consistency
**Action:** For at least one failing test per error code, assert response has exactly:
- top-level `error` (string, lowercase `snake_case`)
- top-level `message` (string)
- top-level `details` (object)
**Expected:** All failures comply. `details` is an object (may be empty `{}`).

### SEC-02 No internal error leakage
**Action:** Trigger representative failures (`invalid_json`, `invalid_jws`, `account_not_found`, `escrow_not_found`, `insufficient_funds`, `payload_mismatch`).
**Expected:** `message` never includes stack traces, SQL fragments, file paths, or driver internals.

### SEC-03 IDs are correctly formatted
**Action:** Create 3+ accounts, perform 3+ credits, lock 3+ escrows.
**Expected:**
- Every returned `account_id` matches `a-<uuid4>`
- Every returned `tx_id` matches `tx-<uuid4>`
- Every returned `escrow_id` matches `esc-<uuid4>`

---

## Release Gate Checklist

Service is release-ready only if:

1. All tests in this document pass.
2. No test marked deterministic has alternate acceptable behavior.
3. No endpoint returns `500` in any test scenario.
4. All failing responses conform to the required error envelope.

---

## Coverage Summary

| Category | IDs | Count |
|----------|-----|-------|
| Account Creation | ACC-01 to ACC-17 | 17 |
| Credit | CR-01 to CR-11 | 11 |
| Balance Query | BAL-01 to BAL-07 | 7 |
| Transaction History | TX-01 to TX-07 | 7 |
| Escrow Lock | ESC-01 to ESC-13 | 13 |
| Escrow Release | REL-01 to REL-09 | 9 |
| Escrow Split | SPL-01 to SPL-17 | 17 |
| Health | HLTH-01 to HLTH-05 | 5 |
| HTTP misuse | HTTP-01 | 1 |
| Cross-cutting security | SEC-01 to SEC-03 | 3 |
| **Total** |  | **90** |

| Endpoint | Covered By |
|----------|------------|
| `POST /accounts` | ACC-01 to ACC-17, SEC-01, SEC-02 |
| `POST /accounts/{account_id}/credit` | CR-01 to CR-11, SEC-01, SEC-02 |
| `GET /accounts/{account_id}` | BAL-01 to BAL-07, ESC-02, SPL-06 |
| `GET /accounts/{account_id}/transactions` | TX-01 to TX-07, ACC-03, ESC-03, REL-03, SPL-07 |
| `POST /escrow/lock` | ESC-01 to ESC-13, SEC-01, SEC-02 |
| `POST /escrow/{escrow_id}/release` | REL-01 to REL-09, SEC-01, SEC-02 |
| `POST /escrow/{escrow_id}/split` | SPL-01 to SPL-17, SEC-01, SEC-02 |
| `GET /health` | HLTH-01 to HLTH-05 |
