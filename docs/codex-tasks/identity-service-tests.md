Read these files FIRST before doing anything:
1. AGENTS.md — project conventions, architecture, testing rules
2. docs/specifications/service-tests/identity-service-tests.md — the AUTHORITATIVE test specification (48 tests). This is your single source of truth for what each test must assert
3. docs/specifications/service-api/identity-service-specs.md — the API specification for the service
4. /Users/flo/Developer/github/guard.git/main/tests/helpers-cli.sh — REFERENCE: shell test helper patterns to follow
5. /Users/flo/Developer/github/guard.git/main/tests/run-cli-tests-sequential.sh — REFERENCE: test runner pattern to follow
6. /Users/flo/Developer/github/guard.git/main/tests/test-init-001.sh — REFERENCE: example individual test script
7. /Users/flo/Developer/github/guard.git/main/tests/test-error-messages-001.sh — REFERENCE: example error test script

After reading ALL files, implement the following. Execute Phase 1 through Phase 4 in order. Do NOT skip phases.

All files go in: services/identity/tests/acceptance/
Create the acceptance/ directory first: mkdir -p services/identity/tests/acceptance

Use `uv run` for all Python execution — never use raw python, python3, or pip install.
Do NOT modify any existing files in the project. Only create new files.
Do NOT create any __init__.py files or pytest files. These are pure bash tests.
Do NOT implement the identity service. Only create tests.
Every .sh file must start with #!/bin/bash and be made executable with chmod +x.

=== PHASE 1: crypto_helper.py ===

Create services/identity/tests/acceptance/crypto_helper.py

This is a Python CLI script that provides Ed25519 cryptographic operations for the shell tests. It uses the `cryptography` library which is already a dependency of the identity service.

It is called from bash as:
  cd services/identity && uv run python tests/acceptance/crypto_helper.py <command> [args]

Implement these exact commands (each prints to stdout, one value per line):

1. keygen — no args
   Line 1: private key as hex string (from private_bytes_raw().hex())
   Line 2: "ed25519:" + base64(public_key_bytes_raw)

2. sign_raw <private_key_hex> <raw_string>
   Converts raw_string to UTF-8 bytes, signs with Ed25519 private key
   Line 1: base64(raw_string_bytes) — the payload_b64
   Line 2: base64(signature) — the signature_b64
   IMPORTANT: if raw_string is empty string "", sign empty bytes b""

3. sign <private_key_hex> <payload_b64>
   Decodes payload_b64, signs the decoded bytes
   Line 1: base64(signature)

4. pubkey_bytes <n>
   Generates n random bytes
   Line 1: "ed25519:" + base64(those n bytes)

5. zero_key — no args
   Line 1: "ed25519:" + base64(32 zero bytes)

6. random_b64 <n>
   Generates n random bytes
   Line 1: base64(those bytes)

7. large_sign <private_key_hex> <size_bytes>
   Generates size_bytes random bytes, signs them
   Line 1: base64(random_payload)
   Line 2: base64(signature)

Use sys.argv for argument parsing. Exit with code 1 and print usage to stderr if wrong command or wrong number of args.
Use from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey for all crypto operations.
Use private_bytes_raw(), from_private_bytes(), public_bytes_raw() methods (available since cryptography>=41).

After creating the file, verify it works by running from the project root:
  cd services/identity && uv run python tests/acceptance/crypto_helper.py keygen

It should print two lines: a hex string and an "ed25519:..." string.

=== PHASE 2: helpers.sh ===

Create services/identity/tests/acceptance/helpers.sh

This is the shared helper library sourced by every test script. Model it after the Guard project's helpers-cli.sh but adapted for HTTP API testing.

Structure (implement ALL of these sections):

--- Section 1: Header ---
#!/bin/bash
set -e

--- Section 2: Configuration ---
BASE_URL="${IDENTITY_BASE_URL:-http://localhost:8001}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CRYPTO="$SCRIPT_DIR/crypto_helper.py"

--- Section 3: Colors (identical to Guard) ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

--- Section 4: Test state tracking (identical to Guard) ---
TESTS_PASSED=0
TESTS_FAILED=0

--- Section 5: Constants ---
UUID4_PATTERN='^a-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
ISO8601_PATTERN='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'

--- Section 6: Output functions ---

test_start() — takes test_id and description, prints banner like Guard's log_test:
  echo ""
  echo "========================================"
  echo "TEST: $test_id — $description"
  echo "========================================"
  echo ""

step() — takes description string, prints:
  echo -e "  ${YELLOW}-> $1${NC}"

test_end() — prints summary like Guard's print_test_summary for one test:
  If TESTS_FAILED == 0:
    echo -e "\n${GREEN}Test passed${NC} ($TESTS_PASSED assertions passed)"
  Else:
    echo -e "\n${RED}Test failed${NC} ($TESTS_PASSED passed, $TESTS_FAILED failed)"
    exit 1

--- Section 7: HTTP functions ---

All HTTP functions must set two globals: HTTP_STATUS and HTTP_BODY.
Use curl with -s (silent), -o to temp file for body, -w '%{http_code}' for status.
Clean up temp files with rm -f after reading.

http_post <path> <json_body>:
  POST with -H "Content-Type: application/json" -d "$body"

http_post_raw <path> <raw_body>:
  POST with -H "Content-Type: application/json" --data-raw "$body"
  (for malformed JSON tests where -d might interpret special chars)

http_post_content_type <path> <content_type> <body>:
  POST with -H "Content-Type: $content_type" -d "$body"

http_post_file <path> <filepath>:
  POST with -H "Content-Type: application/json" --data-binary "@$filepath" --max-time 10

http_get <path>:
  GET request

http_method <method> <path>:
  Arbitrary HTTP method with -H "Content-Type: application/json"

--- Section 8: Assertion functions ---

Follow the Guard pattern EXACTLY for output format:
  PASS: echo -e "${GREEN}✓ PASS${NC}: $message" and ((TESTS_PASSED++))
  FAIL: echo -e "${RED}✗ FAIL${NC}: $message" then details, then ((TESTS_FAILED++)) and return 1

With set -e active, returning 1 exits the script immediately on assertion failure. This is the desired fail-fast behavior within a test.

Implement these assertions:

assert_status <expected>:
  Compare $HTTP_STATUS to expected.
  On fail, show expected vs actual AND the response body.

assert_json_eq <jq_path> <expected>:
  Extract value with: echo "$HTTP_BODY" | jq -r "$jq_path"
  Compare to expected string.
  On fail, show jq_path, expected, actual, and response body.

assert_json_exists <jq_path>:
  Extract value. Fail if "null" or empty.

assert_json_not_exists <jq_path>:
  Extract value. Fail if NOT "null" and not empty.

assert_json_matches <jq_path> <regex_pattern>:
  Extract value. Test with: [[ "$actual" =~ $pattern ]]
  On fail, show the pattern and actual value.

assert_json_true <jq_path>:
  Extract with jq (not -r). Assert equals "true".

assert_json_false <jq_path>:
  Extract with jq (not -r). Assert equals "false".

assert_json_array_min_length <jq_path> <min>:
  Get length with: echo "$HTTP_BODY" | jq "$jq_path | length"
  Assert actual >= min.

assert_json_gt <jq_path> <threshold>:
  Extract numeric value. Assert actual > threshold using awk.

assert_error_envelope:
  Check that .error is type "string" and .message is type "string" using jq type.

assert_body_not_contains <patterns...>:
  For each pattern, check with grep -qi. Fail if ANY pattern found.

assert_equals <expected> <actual> <message>:
  Same as Guard: compare strings, print PASS/FAIL.

assert_not_equals <a> <b> <message>:
  Same as Guard: fail if equal.

--- Section 9: Crypto wrappers ---

All crypto functions call: cd "$SERVICE_DIR" && uv run python "$CRYPTO" <cmd> <args>

crypto_keygen:
  Call keygen. Parse output:
  PRIVATE_KEY_HEX=$(echo "$output" | sed -n '1p')
  PUBLIC_KEY=$(echo "$output" | sed -n '2p')

crypto_sign_raw <priv_hex> <raw_string>:
  Call sign_raw. Sets PAYLOAD_B64 and SIGNATURE_B64.

crypto_sign <priv_hex> <payload_b64>:
  Call sign. Sets SIGNATURE_B64.

crypto_pubkey_bytes <n>:
  Call pubkey_bytes. Sets PUBLIC_KEY.

crypto_zero_key:
  Call zero_key. Sets PUBLIC_KEY.

crypto_random_b64 <n>:
  Call random_b64. Prints to stdout (caller captures with $(...)).

crypto_large_sign <priv_hex> <size>:
  Call large_sign. Sets PAYLOAD_B64 and SIGNATURE_B64.

--- Section 10: Convenience helpers ---

register_agent <name> <public_key>:
  Calls http_post "/agents/register" with the name and public_key as JSON.
  Asserts HTTP_STATUS is 201 (fail with "Setup failed:" message if not).
  Sets AGENT_ID=$(echo "$HTTP_BODY" | jq -r '.agent_id')

=== PHASE 3: run_all.sh ===

Create services/identity/tests/acceptance/run_all.sh

Model after the Guard project's run-cli-tests-sequential.sh. Same colors, same structure.

1. Print banner: "Identity Service Acceptance Tests" with $BASE_URL
2. Check prerequisites exist: curl, jq, uv (using command -v, exit 1 if missing)
3. Do NOT check if the service is running (the tests will fail naturally if it's not)
4. Define an explicit ordered TESTS array (NOT auto-discovered — order matters):

TESTS=(
    "test-list-01.sh"
    "test-health-01.sh"
    "test-health-03.sh"
    "test-reg-01.sh"
    "test-reg-02.sh"
    "test-reg-03.sh"
    "test-reg-04.sh"
    "test-reg-05.sh"
    "test-reg-06.sh"
    "test-reg-07.sh"
    "test-reg-08.sh"
    "test-reg-09.sh"
    "test-reg-10.sh"
    "test-reg-11.sh"
    "test-reg-12.sh"
    "test-reg-13.sh"
    "test-reg-14.sh"
    "test-reg-15.sh"
    "test-reg-16.sh"
    "test-reg-17.sh"
    "test-reg-18.sh"
    "test-read-01.sh"
    "test-read-02.sh"
    "test-read-03.sh"
    "test-list-02.sh"
    "test-health-02.sh"
    "test-ver-01.sh"
    "test-ver-02.sh"
    "test-ver-03.sh"
    "test-ver-04.sh"
    "test-ver-05.sh"
    "test-ver-06.sh"
    "test-ver-07.sh"
    "test-ver-08.sh"
    "test-ver-09.sh"
    "test-ver-10.sh"
    "test-ver-11.sh"
    "test-ver-12.sh"
    "test-ver-13.sh"
    "test-ver-14.sh"
    "test-ver-15.sh"
    "test-ver-16.sh"
    "test-ver-17.sh"
    "test-ver-18.sh"
    "test-http-01.sh"
    "test-sec-01.sh"
    "test-sec-02.sh"
    "test-sec-03.sh"
)

5. Loop through tests:
   - Print "Running $test_name..."
   - Run with: bash "$SCRIPT_DIR/$test_file"
   - If it fails: print "STOPPED: $test_file failed. $passed/$total passed before failure." and exit 1
   - If it passes: print green checkmark, increment passed counter
6. Record elapsed time with $(date +%s) before and after the loop
7. Print summary: total, passed, elapsed time, "All tests passed!"

=== PHASE 4: All 48 test scripts ===

Create ALL 48 test scripts in services/identity/tests/acceptance/.

Every test script follows this exact skeleton:

#!/bin/bash
# test-<id>.sh — <TEST_ID>: <description>
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "<TEST_ID>" "<description from test spec>"

step "<description of what this step does>"
<commands>

step "<next step>"
<assertions>

test_end

Read docs/specifications/service-tests/identity-service-tests.md for the EXACT expected behavior of each test. Every status code, every error code, every assertion described there must be implemented in the corresponding test script. Do not skip any assertions. Do not add assertions that are not in the spec.

Here are the implementation details for each test. Use the test spec as the authoritative source — if anything here contradicts the test spec, the test spec wins.

REGISTRATION TESTS (test-reg-01.sh through test-reg-18.sh):

test-reg-01.sh — REG-01: Register one valid agent
  step: crypto_keygen
  step: http_post "/agents/register" with name "Alice" and the PUBLIC_KEY
  assert: status 201
  assert: json_exists .agent_id, .name, .public_key, .registered_at
  assert: json_eq .name "Alice"
  assert: json_eq .public_key "$PUBLIC_KEY"
  assert: json_matches .agent_id "$UUID4_PATTERN"
  assert: json_matches .registered_at "$ISO8601_PATTERN"

test-reg-02.sh — REG-02: Register second valid agent with different key
  step: crypto_keygen, register_agent "Alice", save ALICE_ID="$AGENT_ID"
  step: crypto_keygen, register_agent "Bob", save BOB_ID="$AGENT_ID"
  assert: not_equals "$ALICE_ID" "$BOB_ID" "agent IDs should differ"

test-reg-03.sh — REG-03: Duplicate key is rejected
  step: crypto_keygen, save PUB_A="$PUBLIC_KEY", register_agent "Alice", save ALICE_ID
  step: http_post "/agents/register" with name "Eve" and same PUB_A
  assert: status 409, json_eq .error "PUBLIC_KEY_EXISTS"
  step: http_get "/agents/$ALICE_ID"
  assert: status 200, json_eq .name "Alice", json_eq .public_key "$PUB_A"

test-reg-04.sh — REG-04: Concurrent duplicate key race is safe
  step: crypto_keygen, save PUB_R
  step: Fire two curl POST requests in background with & and wait
    Use temp files for body and status (mktemp). Read results after wait.
    TMP1=$(mktemp) TMP2=$(mktemp) S1_FILE=$(mktemp) S2_FILE=$(mktemp)
    (curl -s -o "$TMP1" -w '%{http_code}' -X POST -H "Content-Type: application/json" "${BASE_URL}/agents/register" -d "{...}" > "$S1_FILE") &
    PID1=$!
    Same for second request > "$S2_FILE" with TMP2 &
    PID2=$!
    wait $PID1; wait $PID2
    Read statuses and bodies from temp files. Clean up with rm -f.
  step: Sort the two status codes, assert they equal "201\n409"
  step: Identify the winner (the one with 201), extract agent_id
  step: Verify loser's error code is PUBLIC_KEY_EXISTS
  step: http_get "/agents/$WINNER_ID", assert status 200, assert public_key matches

test-reg-05.sh — REG-05: Duplicate names are allowed
  step: crypto_keygen, register_agent "SharedName", save ID1
  step: crypto_keygen, register_agent "SharedName", save ID2
  assert: not_equals "$ID1" "$ID2"

test-reg-06.sh — REG-06: Missing name
  step: crypto_keygen
  step: http_post "/agents/register" '{"public_key":"$PUBLIC_KEY"}' (no name field)
  assert: status 400, json_eq .error "MISSING_FIELD"

test-reg-07.sh — REG-07: Missing public_key
  step: http_post "/agents/register" '{"name":"Orphan"}'
  assert: status 400, json_eq .error "MISSING_FIELD"

test-reg-08.sh — REG-08: Null required fields
  step: http_post "/agents/register" '{"name":null,"public_key":null}'
  assert: status 400, json_eq .error "MISSING_FIELD"

test-reg-09.sh — REG-09: Wrong field types
  step: http_post "/agents/register" '{"name":123,"public_key":true}'
  assert: status 400, json_eq .error "INVALID_FIELD_TYPE"

test-reg-10.sh — REG-10: Empty or whitespace-only name (TWO sub-tests)
  step: crypto_keygen, save PUB1
  step: http_post with name "" and PUB1
  assert: status 400, json_eq .error "INVALID_NAME"
  step: crypto_keygen, save PUB2 (need different key to avoid PUBLIC_KEY_EXISTS)
  step: http_post with name "   " (3 spaces) and PUB2
  assert: status 400, json_eq .error "INVALID_NAME"

test-reg-11.sh — REG-11: Invalid key prefix
  step: http_post with public_key "rsa:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
  assert: status 400, json_eq .error "INVALID_PUBLIC_KEY"

test-reg-12.sh — REG-12: Invalid base64 in key
  step: http_post with public_key "ed25519:%%%not-base64%%%"
  assert: status 400, json_eq .error "INVALID_PUBLIC_KEY"

test-reg-13.sh — REG-13: Wrong key length (16 bytes)
  step: crypto_pubkey_bytes 16
  step: http_post with that PUBLIC_KEY
  assert: status 400, json_eq .error "INVALID_PUBLIC_KEY"

test-reg-14.sh — REG-14: All-zero key
  step: crypto_zero_key
  step: http_post with that PUBLIC_KEY
  assert: status 400, json_eq .error "INVALID_PUBLIC_KEY"

test-reg-15.sh — REG-15: Mass-assignment resistance
  step: crypto_keygen
  step: http_post with valid name+public_key PLUS extra fields: "agent_id":"a-00000000-0000-0000-0000-000000000000", "registered_at":"1999-01-01T00:00:00Z", "is_admin":true
  assert: status 201
  assert: json_matches .agent_id "$UUID4_PATTERN"
  assert: agent_id is NOT "a-00000000-0000-0000-0000-000000000000"
  assert: registered_at is NOT "1999-01-01T00:00:00Z"

test-reg-16.sh — REG-16: Malformed JSON
  step: http_post_raw "/agents/register" '{"name":"Alice","public_key":"ed2' (truncated)
  assert: status 400, json_eq .error "INVALID_JSON"

test-reg-17.sh — REG-17: Wrong content type
  step: crypto_keygen
  step: http_post_content_type "/agents/register" "text/plain" '{"name":"Alice","public_key":"$PUBLIC_KEY"}'
  assert: status 415, json_eq .error "UNSUPPORTED_MEDIA_TYPE"

test-reg-18.sh — REG-18: Oversized body
  step: Create temp file with ~2MB payload:
    TMP_FILE=$(mktemp)
    printf '{"name":"' > "$TMP_FILE"
    dd if=/dev/zero bs=1024 count=2048 2>/dev/null | tr '\0' 'A' >> "$TMP_FILE"
    printf '","public_key":"ed25519:AAAA"}' >> "$TMP_FILE"
  step: http_post_file "/agents/register" "$TMP_FILE"
  step: rm -f "$TMP_FILE"
  assert: status 413, json_eq .error "PAYLOAD_TOO_LARGE"

VERIFICATION TESTS (test-ver-01.sh through test-ver-18.sh):

test-ver-01.sh — VER-01: Valid signature verifies true
  step: crypto_keygen, save PRIV_A, register_agent "Alice", save ALICE_ID
  step: crypto_sign_raw "$PRIV_A" "hello world"
  step: http_post "/agents/verify" with agent_id, PAYLOAD_B64, SIGNATURE_B64
  assert: status 200, json_true .valid, json_eq .agent_id "$ALICE_ID"

test-ver-02.sh — VER-02: Wrong signature (Bob signs, verify under Alice)
  step: crypto_keygen, save PRIV_A, register_agent "Alice", save ALICE_ID
  step: crypto_keygen, save PRIV_B, register_agent "Bob"
  step: crypto_sign_raw "$PRIV_B" "hello world"
  step: http_post "/agents/verify" with ALICE_ID, PAYLOAD_B64, SIGNATURE_B64
  assert: status 200, json_false .valid, json_eq .reason "signature mismatch"

test-ver-03.sh — VER-03: Tampered payload
  step: Register Alice, sign "original message", save ORIG_SIG="$SIGNATURE_B64"
  step: crypto_sign_raw with "tampered message" to get TAMPERED_PAYLOAD="$PAYLOAD_B64"
  step: http_post "/agents/verify" with ALICE_ID, TAMPERED_PAYLOAD, ORIG_SIG
  assert: status 200, json_false .valid, json_eq .reason "signature mismatch"

test-ver-04.sh — VER-04: Cross-identity replay
  step: Register Alice (save PRIV_A) and Eve (different key, save EVE_ID)
  step: Alice signs "secret action"
  step: Verify with EVE_ID + Alice's payload + Alice's signature
  assert: status 200, json_false .valid, json_eq .reason "signature mismatch"

test-ver-05.sh — VER-05: Non-existent agent_id
  step: Generate a valid-length signature: FAKE_SIG=$(crypto_random_b64 64)
  step: http_post "/agents/verify" with agent_id "a-00000000-0000-0000-0000-000000000000", payload "aGVsbG8=", signature "$FAKE_SIG"
  assert: status 404, json_eq .error "AGENT_NOT_FOUND"

test-ver-06.sh — VER-06: Invalid base64 payload
  step: Register agent, save AID
  step: FAKE_SIG=$(crypto_random_b64 64)
  step: http_post with AID, payload "%%%not-base64%%%", signature "$FAKE_SIG"
  assert: status 400, json_eq .error "INVALID_BASE64"

test-ver-07.sh — VER-07: Invalid base64 signature
  step: Register agent, save AID
  step: http_post with AID, payload "aGVsbG8=", signature "%%%not-base64%%%"
  assert: status 400, json_eq .error "INVALID_BASE64"

test-ver-08.sh — VER-08: Signature too short (32 bytes)
  step: Register agent, save AID
  step: SHORT_SIG=$(crypto_random_b64 32)
  step: http_post with AID, payload "aGVsbG8=", signature "$SHORT_SIG"
  assert: status 400, json_eq .error "INVALID_SIGNATURE_LENGTH"

test-ver-09.sh — VER-09: Signature too long (128 bytes)
  step: Register agent, save AID
  step: LONG_SIG=$(crypto_random_b64 128)
  step: http_post with AID, payload "aGVsbG8=", signature "$LONG_SIG"
  assert: status 400, json_eq .error "INVALID_SIGNATURE_LENGTH"

test-ver-10.sh — VER-10: Missing required fields (THREE sub-tests)
  step: Register agent, sign a payload to get valid AID, PAYLOAD_B64, SIGNATURE_B64
  sub-test 1: http_post omitting agent_id → 400 MISSING_FIELD
  sub-test 2: http_post omitting payload → 400 MISSING_FIELD
  sub-test 3: http_post omitting signature → 400 MISSING_FIELD

test-ver-11.sh — VER-11: Null required fields
  step: http_post '{"agent_id":null,"payload":null,"signature":null}'
  assert: status 400, json_eq .error "MISSING_FIELD"

test-ver-12.sh — VER-12: Wrong field types
  step: http_post '{"agent_id":true,"payload":[1],"signature":{"x":1}}'
  assert: status 400, json_eq .error "INVALID_FIELD_TYPE"

test-ver-13.sh — VER-13: Empty payload is supported
  step: Register Alice, crypto_sign_raw with empty string ""
  step: Verify with the empty payload and its signature
  assert: status 200, json_true .valid

test-ver-14.sh — VER-14: Large payload (1 MB)
  step: Register Alice
  step: crypto_large_sign "$PRIV_A" 1048576
  step: Verify (the JSON body will be ~1.3MB due to base64 encoding)
  assert: status 200, json_true .valid

test-ver-15.sh — VER-15: Malformed JSON
  step: http_post_raw "/agents/verify" '{"agent_id":"abc","payload":'
  assert: status 400, json_eq .error "INVALID_JSON"

test-ver-16.sh — VER-16: Wrong content type
  step: http_post_content_type "/agents/verify" "text/plain" '{"agent_id":"x","payload":"y","signature":"z"}'
  assert: status 415, json_eq .error "UNSUPPORTED_MEDIA_TYPE"

test-ver-17.sh — VER-17: Idempotent verification
  step: Register Alice, sign payload, build JSON body string
  step: http_post once, save RESP1="$HTTP_BODY", assert status 200
  step: http_post again with same body, save RESP2="$HTTP_BODY", assert status 200
  assert: equals "$RESP1" "$RESP2" "responses should be identical"

test-ver-18.sh — VER-18: SQL injection in agent_id
  step: FAKE_SIG=$(crypto_random_b64 64)
  step: http_post with agent_id "' OR '1'='1", payload "aGVsbG8=", signature "$FAKE_SIG"
  assert: status 404, json_eq .error "AGENT_NOT_FOUND"

READ/LIST/HEALTH TESTS:

test-list-01.sh — LIST-01: Empty list on fresh system
  step: http_get "/agents"
  assert: status 200
  assert: json array .agents has length 0 (use jq '.agents | length' and assert equals "0")

test-read-01.sh — READ-01: Lookup existing agent
  step: crypto_keygen, save PUB_A, register_agent "Alice", save ALICE_ID
  step: http_get "/agents/$ALICE_ID"
  assert: status 200, json_eq .agent_id "$ALICE_ID", json_eq .name "Alice", json_eq .public_key "$PUB_A", json_exists .registered_at

test-read-02.sh — READ-02: Lookup non-existent agent
  step: http_get "/agents/a-00000000-0000-0000-0000-000000000000"
  assert: status 404, json_eq .error "AGENT_NOT_FOUND"

test-read-03.sh — READ-03: Malformed/path-traversal ID
  step: http_get "/agents/not-a-valid-id"
  assert: status 404
  assert: body_not_contains "Traceback" "File \"" "sqlite" "/home/" "/Users/"
  step: http_get "/agents/..%2F..%2Fetc%2Fpasswd"
  assert: status 404
  assert: body_not_contains "Traceback" "File \"" "sqlite" "/home/" "/Users/" "root:"

test-list-02.sh — LIST-02: Populated list omits public keys
  step: Register 2 agents with crypto_keygen + register_agent
  step: http_get "/agents"
  assert: status 200
  assert: .agents array length >= 2
  assert: no entry contains public_key (use jq: '[.agents[] | has("public_key")] | any' should be "false")
  assert: all entries have agent_id, name, registered_at (use jq: '[.agents[] | (has("agent_id") and has("name") and has("registered_at"))] | all' should be "true")

test-health-01.sh — HEALTH-01: Health schema is correct
  step: http_get "/health"
  assert: status 200
  assert: json_exists .status, .uptime_seconds, .started_at, .registered_agents
  assert: json_eq .status "ok"

test-health-02.sh — HEALTH-02: Registered count is exact
  step: http_get "/health", save BEFORE count from .registered_agents
  step: Register 3 agents (loop with crypto_keygen + register_agent)
  step: http_get "/health", save AFTER count
  assert: AFTER equals BEFORE + 3

test-health-03.sh — HEALTH-03: Uptime is monotonic
  step: http_get "/health", save UPTIME1 from .uptime_seconds
  step: sleep 1.5
  step: http_get "/health", save UPTIME2
  assert: UPTIME2 > UPTIME1 (use awk: echo "$UPTIME2 $UPTIME1" | awk '{exit !($1 > $2)}')

HTTP METHOD MISUSE:

test-http-01.sh — HTTP-01: Wrong method on defined routes
  Define a local helper function check_method that takes method and path:
    http_method "$method" "$path"
    assert_status 405
    assert_json_eq ".error" "METHOD_NOT_ALLOWED"
  Call check_method for ALL 8 combinations:
    GET    /agents/register
    PUT    /agents/register
    GET    /agents/verify
    POST   /agents/a-00000000-0000-0000-0000-000000000000
    PATCH  /agents/a-00000000-0000-0000-0000-000000000000
    DELETE /agents/a-00000000-0000-0000-0000-000000000000
    POST   /agents
    POST   /health

CROSS-CUTTING SECURITY:

test-sec-01.sh — SEC-01: Error envelope consistency
  Trigger at least one error per error code and assert assert_error_envelope for each:
  - MISSING_FIELD: http_post "/agents/register" '{}'
  - INVALID_PUBLIC_KEY: http_post "/agents/register" '{"name":"X","public_key":"bad"}'
  - INVALID_JSON: http_post_raw "/agents/register" '{broken'
  - AGENT_NOT_FOUND: http_get "/agents/a-00000000-0000-0000-0000-000000000000"
  - METHOD_NOT_ALLOWED: http_method "DELETE" "/agents/register"
  - UNSUPPORTED_MEDIA_TYPE: http_post_content_type "/agents/register" "text/plain" '{}'
  - MISSING_FIELD (verify): http_post "/agents/verify" '{}'
  - INVALID_BASE64: register an agent, http_post "/agents/verify" with payload "%%%" and signature "aaa"

test-sec-02.sh — SEC-02: No internal error leakage
  Define FORBIDDEN=("Traceback" "File \"" "sqlite" "psycopg" "sqlalchemy" "/home/" "/Users/" "/app/src/" "DETAIL:" "Internal Server Error" "NoneType")
  Trigger these failures and assert_body_not_contains "${FORBIDDEN[@]}" for each:
  - INVALID_JSON: http_post_raw "/agents/register" '{broken'
  - INVALID_BASE64: register agent, verify with bad payload
  - Duplicate key: register with same key twice
  - Malformed ID: http_get "/agents/not-valid"

test-sec-03.sh — SEC-03: Agent IDs are opaque and random
  step: Register 5 agents in a loop, collect IDs in a bash array
  assert: every ID matches UUID4_PATTERN
  assert: all IDs are unique (sort -u, count, assert equals 5)

=== FINAL STEP ===

After creating ALL files, make all .sh files executable:
  chmod +x services/identity/tests/acceptance/*.sh

Then verify the crypto helper works:
  cd services/identity && uv run python tests/acceptance/crypto_helper.py keygen

Then verify the test runner can at least start (it will fail because the service is not running, but it should fail gracefully):
  bash services/identity/tests/acceptance/run_all.sh 2>&1 | head -20

The tests are EXPECTED to fail — the identity service is not implemented yet. We are writing tests first (TDD).

Do NOT implement the identity service. Only create tests.
Do NOT commit anything. Just create the files.

START NOW by reading the files listed at the top of this message.
