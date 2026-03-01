# Central Bank Acceptance Tests — Codex Task

> Read AGENTS.md FIRST for project conventions.
> Use `uv run` for all Python execution. Never use raw python or pip install.
> Do NOT modify any existing files.
> Commit after each task.

## Context

The identity service has shell-based acceptance tests at `services/identity/tests/acceptance/`. Study these files to understand the pattern:
- `services/identity/tests/acceptance/helpers.sh` — shared test helpers (http_get, http_post, assert_*, crypto_*)
- `services/identity/tests/acceptance/run_all.sh` — test runner that starts/stops service per test
- `services/identity/tests/acceptance/crypto_helper.py` — Python CLI for Ed25519 keygen/sign operations
- `services/identity/tests/acceptance/test-health-01.sh` — example health test
- `services/identity/tests/acceptance/test-ver-01.sh` — example verification test
- `services/identity/tests/acceptance/test-sec-01.sh` — example security/error envelope test

You must create an equivalent acceptance test suite for the central bank at `services/central-bank/tests/acceptance/`.

## Architecture

The central bank requires BOTH services running:
- **Identity service** on port 8001 — for agent registration and JWS verification
- **Central bank service** on port 8002 — the service under test

The `run_all.sh` must start both services, each with a fresh temp database.

All mutating operations require **JWS compact tokens** (RFC 7515, EdDSA algorithm). You need a `jws_helper.py` that creates these tokens.

### JWS Token Format

A JWS compact token has three base64url-encoded parts: `header.payload.signature`
- Protected header: `{"alg":"EdDSA","kid":"<agent_id>"}`
- Payload: JSON object with operation-specific fields
- Signature: Ed25519 signature over `header.payload`

Use the `joserfc` library (already a dev dependency) to create JWS tokens.

### Auth Model

- **Platform operations** (create account, credit, escrow release, escrow split): JWS token in request body as `{"token": "<jws>"}`
- **Agent operations** (get balance, get transactions): JWS token in `Authorization: Bearer <jws>` header
- **Escrow lock**: Agent's own JWS token in request body as `{"token": "<jws>"}`

---

## Task 1: Create jws_helper.py

**File:** `services/central-bank/tests/acceptance/jws_helper.py`

Create a Python CLI tool (similar to identity's `crypto_helper.py`) that provides:

```
Usage:
  jws_helper.py keygen                              # Generate Ed25519 keypair: prints private_hex, public_key
  jws_helper.py jws <private_hex> <agent_id> <json_payload>  # Create JWS compact token
```

Implementation:

```python
#!/usr/bin/env python
"""JWS helper for acceptance tests."""
import base64
import json
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc import jws
from joserfc.jwk import OKPKey

USAGE = (
    "Usage:\n"
    "  jws_helper.py keygen\n"
    "  jws_helper.py jws <private_key_hex> <agent_id> <json_payload>"
)


def keygen() -> None:
    """Generate Ed25519 keypair."""
    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes_raw()
    public_raw = private_key.public_key().public_bytes_raw()
    print(private_raw.hex())
    print(f"ed25519:{base64.b64encode(public_raw).decode()}")


def make_jws(private_hex: str, agent_id: str, payload_json: str) -> None:
    """Create a JWS compact token."""
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    raw_private = private_key.private_bytes_raw()
    raw_public = private_key.public_key().public_bytes_raw()
    jwk_dict = {
        "kty": "OKP",
        "crv": "Ed25519",
        "d": base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
        "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
    }
    key = OKPKey.import_key(jwk_dict)
    protected = {"alg": "EdDSA", "kid": agent_id}
    # Normalize JSON: compact, sorted keys
    payload_bytes = json.dumps(
        json.loads(payload_json), separators=(",", ":"), sort_keys=True
    ).encode()
    token = jws.serialize_compact(protected, payload_bytes, key, algorithms=["EdDSA"])
    print(token)


def main() -> None:
    if len(sys.argv) < 2:
        print(USAGE, file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "keygen" and len(sys.argv) == 2:
        keygen()
    elif cmd == "jws" and len(sys.argv) == 5:
        make_jws(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print(USAGE, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

Commit: `test(central-bank): add JWS helper for acceptance tests`

---

## Task 2: Create helpers.sh

**File:** `services/central-bank/tests/acceptance/helpers.sh`

Base it on the identity service's `helpers.sh` but with these changes:

1. `BASE_URL` defaults to `http://localhost:8002` (central bank port)
2. `IDENTITY_BASE_URL` defaults to `http://localhost:8001`
3. Replace `CRYPTO` with `JWS_HELPER` pointing to `jws_helper.py`
4. Add `ESCROW_ID_PATTERN='^esc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'`
5. Add `TX_ID_PATTERN='^tx-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'`

Add these helper functions (in addition to all the assert_* and http_* functions from identity's helpers.sh):

```bash
# JWS helpers
jws_keygen() {
    local output
    output=$(cd "$SERVICE_DIR" && uv run python "$JWS_HELPER" keygen)
    PRIVATE_KEY_HEX=$(echo "$output" | sed -n '1p')
    PUBLIC_KEY=$(echo "$output" | sed -n '2p')
}

jws_sign() {
    local priv_hex="$1"
    local agent_id="$2"
    local payload_json="$3"
    JWS_TOKEN=$(cd "$SERVICE_DIR" && uv run python "$JWS_HELPER" jws "$priv_hex" "$agent_id" "$payload_json")
}

# Register agent on Identity service (required for JWS verification)
identity_register_agent() {
    local name="$1"
    local public_key="$2"
    local tmp_file
    tmp_file="$(mktemp)"
    local body
    body=$(jq -nc --arg name "$name" --arg public_key "$public_key" '{name:$name, public_key:$public_key}')

    HTTP_STATUS=$(curl -s -o "$tmp_file" -w '%{http_code}' -X POST -H "Content-Type: application/json" "${IDENTITY_BASE_URL}/agents/register" -d "$body")
    HTTP_BODY="$(cat "$tmp_file")"
    rm -f "$tmp_file"

    if [ "$HTTP_STATUS" != "201" ]; then
        echo -e "${RED}✗ FAIL${NC}: Setup failed: identity_register_agent returned $HTTP_STATUS"
        echo -e "  Response body: $HTTP_BODY"
        exit 1
    fi

    AGENT_ID=$(echo "$HTTP_BODY" | jq -r '.agent_id')
}

# Create account via platform token
bank_create_account() {
    local platform_priv="$1"
    local platform_id="$2"
    local target_agent_id="$3"
    local initial_balance="$4"
    local payload
    payload=$(jq -nc --arg agent_id "$target_agent_id" --argjson initial_balance "$initial_balance" '{action:"create_account", agent_id:$agent_id, initial_balance:$initial_balance}')
    jws_sign "$platform_priv" "$platform_id" "$payload"
    local body
    body=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
    http_post "/accounts" "$body"
    if [ "$HTTP_STATUS" != "201" ]; then
        echo -e "${RED}✗ FAIL${NC}: Setup failed: bank_create_account returned $HTTP_STATUS"
        echo -e "  Response body: $HTTP_BODY"
        exit 1
    fi
}

# Credit account via platform token
bank_credit_account() {
    local platform_priv="$1"
    local platform_id="$2"
    local account_id="$3"
    local amount="$4"
    local reference="$5"
    local payload
    payload=$(jq -nc --arg account_id "$account_id" --argjson amount "$amount" --arg reference "$reference" '{action:"credit", account_id:$account_id, amount:$amount, reference:$reference}')
    jws_sign "$platform_priv" "$platform_id" "$payload"
    local body
    body=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
    http_post "/accounts/$account_id/credit" "$body"
    if [ "$HTTP_STATUS" != "200" ]; then
        echo -e "${RED}✗ FAIL${NC}: Setup failed: bank_credit_account returned $HTTP_STATUS"
        echo -e "  Response body: $HTTP_BODY"
        exit 1
    fi
}

# Add http_get_with_bearer for agent-authenticated GET requests
http_get_with_bearer() {
    local path="$1"
    local token="$2"
    local tmp_file
    tmp_file="$(mktemp)"

    HTTP_STATUS=$(curl -s -o "$tmp_file" -w '%{http_code}' -H "Authorization: Bearer $token" "${BASE_URL}${path}")
    HTTP_BODY="$(cat "$tmp_file")"
    rm -f "$tmp_file"
}
```

Commit: `test(central-bank): add acceptance test helpers`

---

## Task 3: Create run_all.sh

**File:** `services/central-bank/tests/acceptance/run_all.sh`

This must start BOTH the identity service AND the central bank service for each test. Use fresh temp databases for each test.

Key differences from identity's run_all.sh:
- Start identity service on port 8001 with a temp config
- Start central bank service on port 8002 with a temp config (pointing identity.base_url to localhost:8001)
- Set `platform.agent_id` to a platform agent registered on the identity service BEFORE starting the bank
  - Actually, we cannot do this before starting since the identity service is started fresh each test. Instead, use a known placeholder like `"a-platform-acceptance"` in config.yaml, and the tests themselves will register the platform agent on identity first. Wait — the identity service assigns agent_ids, so we can't pre-know them.
  - **Solution**: The `platform.agent_id` in config must be set AFTER registering the platform on identity. So the run_all.sh should:
    1. Start identity service with temp config
    2. Wait for identity health
    3. Register a "Platform" agent on identity service and capture the agent_id
    4. Write central bank temp config with that agent_id as `platform.agent_id`
    5. Start central bank service with that config
    6. Wait for central bank health
    7. Export `PLATFORM_AGENT_ID` and `PLATFORM_PRIVATE_KEY_HEX` as env vars for the test scripts
    8. Run the test script
    9. Stop both services

Here's the skeleton:

```bash
#!/usr/bin/env bash
set -euo pipefail

IDENTITY_BASE_URL="${IDENTITY_BASE_URL:-http://localhost:8001}"
BANK_BASE_URL="${BANK_BASE_URL:-http://localhost:8002}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
IDENTITY_DIR="$(cd "$SERVICE_DIR/../identity" && pwd)"
JWS_HELPER="$SCRIPT_DIR/jws_helper.py"
TEST_GLOB="tests/acceptance/test-*.sh"
RUN_LOG_IDENTITY="/tmp/central-bank-acceptance-identity.log"
RUN_LOG_BANK="/tmp/central-bank-acceptance-bank.log"
TMP_DIRS=()

wait_for_health() {
    local url="$1"
    local attempts=40
    local sleep_seconds=0.25
    for ((i = 1; i <= attempts; i++)); do
        if curl -fsS "$url/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep "$sleep_seconds"
    done
    return 1
}

stop_services() {
    cd "$SERVICE_DIR" && just kill >/dev/null 2>&1 || true
    cd "$IDENTITY_DIR" && just kill >/dev/null 2>&1 || true
}

cleanup() {
    local tmp_dir
    stop_services
    for tmp_dir in "${TMP_DIRS[@]:-}"; do
        rm -rf "$tmp_dir"
    done
    unset CONFIG_PATH PLATFORM_AGENT_ID PLATFORM_PRIVATE_KEY_HEX || true
}

trap cleanup EXIT

for f in $TEST_GLOB; do
    echo "=== Running $f ==="

    stop_services

    test_tmp_dir="$(mktemp -d)"
    TMP_DIRS+=("$test_tmp_dir")

    # --- Start Identity service ---
    identity_db_path="$test_tmp_dir/identity.db"
    identity_config_path="$test_tmp_dir/identity-config.yaml"
    cat >"$identity_config_path" <<IDEOF
service:
  name: "identity"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8001
  log_level: "warning"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "$identity_db_path"
crypto:
  algorithm: "ed25519"
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64
request:
  max_body_size: 1572864
IDEOF

    cd "$IDENTITY_DIR"
    CONFIG_PATH="$identity_config_path" just run >"$RUN_LOG_IDENTITY" 2>&1 &
    identity_pid=$!

    if ! wait_for_health "$IDENTITY_BASE_URL"; then
        echo "FAILED: identity service did not become healthy for $f"
        tail -n 60 "$RUN_LOG_IDENTITY" || true
        exit 1
    fi

    # --- Register platform agent on Identity ---
    keygen_output=$(cd "$SERVICE_DIR" && uv run python "$JWS_HELPER" keygen)
    PLATFORM_PRIVATE_KEY_HEX=$(echo "$keygen_output" | sed -n '1p')
    PLATFORM_PUBLIC_KEY=$(echo "$keygen_output" | sed -n '2p')
    export PLATFORM_PRIVATE_KEY_HEX

    register_body=$(jq -nc --arg name "Platform" --arg public_key "$PLATFORM_PUBLIC_KEY" '{name:$name, public_key:$public_key}')
    register_response=$(curl -s -X POST -H "Content-Type: application/json" "${IDENTITY_BASE_URL}/agents/register" -d "$register_body")
    PLATFORM_AGENT_ID=$(echo "$register_response" | jq -r '.agent_id')
    export PLATFORM_AGENT_ID

    if [ -z "$PLATFORM_AGENT_ID" ] || [ "$PLATFORM_AGENT_ID" = "null" ]; then
        echo "FAILED: could not register platform agent for $f"
        echo "  Response: $register_response"
        exit 1
    fi

    # --- Start Central Bank service ---
    bank_db_path="$test_tmp_dir/central-bank.db"
    bank_config_path="$test_tmp_dir/bank-config.yaml"
    cat >"$bank_config_path" <<BANKEOF
service:
  name: "central-bank"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8002
  log_level: "warning"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "$bank_db_path"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"
platform:
  agent_id: "$PLATFORM_AGENT_ID"
request:
  max_body_size: 1048576
BANKEOF

    cd "$SERVICE_DIR"
    CONFIG_PATH="$bank_config_path" just run >"$RUN_LOG_BANK" 2>&1 &
    bank_pid=$!

    if ! wait_for_health "$BANK_BASE_URL"; then
        echo "FAILED: central-bank service did not become healthy for $f"
        tail -n 60 "$RUN_LOG_BANK" || true
        exit 1
    fi

    # --- Run test ---
    cd "$SERVICE_DIR"
    if ! bash "$f"; then
        echo "FAILED: $f"
        stop_services
        exit 1
    fi

    stop_services
    wait "$bank_pid" >/dev/null 2>&1 || true
    wait "$identity_pid" >/dev/null 2>&1 || true
done

echo "ALL TESTS PASSED"
```

Make it executable: `chmod +x services/central-bank/tests/acceptance/run_all.sh`

Commit: `test(central-bank): add acceptance test runner`

---

## Task 4: Create health tests

**Files:**
- `services/central-bank/tests/acceptance/test-health-01.sh` — Health schema is correct
- `services/central-bank/tests/acceptance/test-health-02.sh` — Account count updates after creation
- `services/central-bank/tests/acceptance/test-health-03.sh` — POST /health returns 405

Tests:

**test-health-01.sh**: GET /health returns 200 with status, uptime_seconds, started_at, total_accounts, total_escrowed. Assert status == "ok", total_accounts == 0, total_escrowed == 0.

**test-health-02.sh**: Create an agent on identity, create an account on central bank via platform token, then GET /health and assert total_accounts == 1.

**test-health-03.sh**: POST /health returns 405 METHOD_NOT_ALLOWED.

Commit: `test(central-bank): add health acceptance tests`

---

## Task 5: Create account tests

**Files:**
- `test-acct-01.sh` — Create account success (201, has account_id, balance, created_at)
- `test-acct-02.sh` — Create account with zero balance
- `test-acct-03.sh` — Duplicate account returns 409 ACCOUNT_EXISTS
- `test-acct-04.sh` — Non-platform agent cannot create accounts (403 FORBIDDEN)
- `test-acct-05.sh` — Credit account success (balance increases)
- `test-acct-06.sh` — Get balance (agent reads own account via Bearer header)
- `test-acct-07.sh` — Get balance forbidden for other agent's account (403)
- `test-acct-08.sh` — Get transactions returns history
- `test-acct-09.sh` — Missing token returns 400 INVALID_JWS
- `test-acct-10.sh` — Create account for non-existent agent returns 404 AGENT_NOT_FOUND

Each test must:
1. Register agents on the identity service using `jws_keygen` and `identity_register_agent`
2. Use `PLATFORM_AGENT_ID` and `PLATFORM_PRIVATE_KEY_HEX` env vars (set by run_all.sh) for platform operations
3. Use `jws_sign` to create JWS tokens for requests

For agent-signed operations (get balance, get transactions), the agent signs with its own private key.

Example pattern for test-acct-01.sh:
```bash
#!/bin/bash
# test-acct-01.sh — ACCT-01: Create account success
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-01" "Create account success"

step "Register agent on Identity service"
jws_keygen
AGENT_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"

step "Create account via platform token"
PAYLOAD=$(jq -nc --arg agent_id "$ALICE_ID" '{action:"create_account", agent_id:$agent_id, initial_balance:100}')
jws_sign "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/accounts" "$BODY"

step "Assert account created"
assert_status "201"
assert_json_eq ".account_id" "$ALICE_ID"
assert_json_eq ".balance" "100"
assert_json_exists ".created_at"

test_end
```

Commit: `test(central-bank): add account acceptance tests`

---

## Task 6: Create escrow tests

**Files:**
- `test-escrow-01.sh` — Lock funds in escrow success (201, has escrow_id, amount, task_id, status=locked)
- `test-escrow-02.sh` — Insufficient funds returns 402
- `test-escrow-03.sh` — Agent cannot lock another agent's funds (403)
- `test-escrow-04.sh` — Release escrow success (status=released, recipient gets funds)
- `test-escrow-05.sh` — Release already-resolved escrow returns 409
- `test-escrow-06.sh` — Release non-existent escrow returns 404
- `test-escrow-07.sh` — Split escrow success (worker_amount + poster_amount == original)
- `test-escrow-08.sh` — Only platform can release escrow (non-platform gets 403)

For escrow lock, the AGENT signs with their own key (not the platform):
```bash
PAYLOAD=$(jq -nc --arg agent_id "$PAYER_ID" --arg task_id "T-001" '{action:"escrow_lock", agent_id:$agent_id, amount:50, task_id:$task_id}')
jws_sign "$PAYER_PRIV" "$PAYER_ID" "$PAYLOAD"
```

For escrow release/split, the PLATFORM signs:
```bash
PAYLOAD=$(jq -nc --arg escrow_id "$ESCROW_ID" --arg recipient "$WORKER_ID" '{action:"escrow_release", escrow_id:$escrow_id, recipient_account_id:$recipient}')
jws_sign "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYLOAD"
```

Commit: `test(central-bank): add escrow acceptance tests`

---

## Task 7: Create error envelope and HTTP method tests

**Files:**
- `test-sec-01.sh` — Error envelope consistency (all error codes return {error, message, details})
- `test-http-01.sh` — Wrong HTTP methods return 405

**test-sec-01.sh**: Test these error scenarios and assert each returns an error envelope:
- POST /accounts with empty body → 400 INVALID_JWS
- POST /accounts with invalid JSON → 400 INVALID_JSON
- GET /accounts/{id} without Authorization header → 400 INVALID_JWS
- POST /escrow/lock with empty body → 400 INVALID_JWS
- POST /escrow/esc-fake/release with valid platform token but non-existent escrow → 404 ESCROW_NOT_FOUND
- POST /accounts with wrong content type → 415 UNSUPPORTED_MEDIA_TYPE

**test-http-01.sh**: Test wrong HTTP methods:
- GET /accounts → 405
- DELETE /accounts → 405
- GET /escrow/lock → 405
- DELETE /escrow/lock → 405
- POST /health → 405

Commit: `test(central-bank): add security and HTTP method acceptance tests`

---

## Task 8: Create comprehensive smoke test

**File:** `test-smoke-01.sh` — End-to-end flow

Full lifecycle in one test:
1. Register platform agent (already done by run_all.sh, use env vars)
2. Register worker agent on Identity
3. Register poster agent on Identity
4. Create accounts for worker (balance 0) and poster (balance 200) via platform
5. Health check: assert total_accounts == 2
6. Poster locks 100 in escrow for task T-001
7. Health check: assert total_escrowed == 100
8. Platform releases escrow to worker
9. Worker checks balance via Bearer header: assert balance == 100
10. Poster checks balance via Bearer header: assert balance == 100
11. Health check: assert total_escrowed == 0

Commit: `test(central-bank): add end-to-end smoke acceptance test`

---

## After all tasks

Do NOT try to run run_all.sh yourself — I will run it since it requires starting both services.
Just make sure the scripts are executable (chmod +x *.sh).
