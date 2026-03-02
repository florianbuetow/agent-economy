#!/bin/bash
set -e

BASE_URL="${IDENTITY_BASE_URL:-http://localhost:8001}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CRYPTO="$SCRIPT_DIR/crypto_helper.py"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

TESTS_PASSED=0
TESTS_FAILED=0

UUID4_PATTERN='^a-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
ISO8601_PATTERN='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'

HTTP_STATUS=""
HTTP_BODY=""


test_start() {
    local test_id="$1"
    local description="$2"
    echo ""
    echo "========================================"
    echo "TEST: $test_id — $description"
    echo "========================================"
    echo ""
}


step() {
    echo -e "  ${YELLOW}-> $1${NC}"
}


test_end() {
    if [ "$TESTS_FAILED" -eq 0 ]; then
        echo -e "\n${GREEN}Test passed${NC} ($TESTS_PASSED assertions passed)"
    else
        echo -e "\n${RED}Test failed${NC} ($TESTS_PASSED passed, $TESTS_FAILED failed)"
        exit 1
    fi
}


http_post() {
    local path="$1"
    local body="$2"
    local tmp_file
    tmp_file="$(mktemp)"

    HTTP_STATUS=$(curl -s -o "$tmp_file" -w '%{http_code}' -X POST -H "Content-Type: application/json" "${BASE_URL}${path}" -d "$body")
    HTTP_BODY="$(cat "$tmp_file")"
    rm -f "$tmp_file"
}


http_post_raw() {
    local path="$1"
    local body="$2"
    local tmp_file
    tmp_file="$(mktemp)"

    HTTP_STATUS=$(curl -s -o "$tmp_file" -w '%{http_code}' -X POST -H "Content-Type: application/json" "${BASE_URL}${path}" --data-raw "$body")
    HTTP_BODY="$(cat "$tmp_file")"
    rm -f "$tmp_file"
}


http_post_content_type() {
    local path="$1"
    local content_type="$2"
    local body="$3"
    local tmp_file
    tmp_file="$(mktemp)"

    HTTP_STATUS=$(curl -s -o "$tmp_file" -w '%{http_code}' -X POST -H "Content-Type: $content_type" "${BASE_URL}${path}" -d "$body")
    HTTP_BODY="$(cat "$tmp_file")"
    rm -f "$tmp_file"
}


http_post_file() {
    local path="$1"
    local filepath="$2"
    local tmp_file
    tmp_file="$(mktemp)"

    HTTP_STATUS=$(curl -s -o "$tmp_file" -w '%{http_code}' -X POST -H "Content-Type: application/json" "${BASE_URL}${path}" --data-binary "@$filepath" --max-time 10)
    HTTP_BODY="$(cat "$tmp_file")"
    rm -f "$tmp_file"
}


http_get() {
    local path="$1"
    local tmp_file
    tmp_file="$(mktemp)"

    HTTP_STATUS=$(curl -s -o "$tmp_file" -w '%{http_code}' "${BASE_URL}${path}")
    HTTP_BODY="$(cat "$tmp_file")"
    rm -f "$tmp_file"
}


http_method() {
    local method="$1"
    local path="$2"
    local tmp_file
    tmp_file="$(mktemp)"

    HTTP_STATUS=$(curl -s -o "$tmp_file" -w '%{http_code}' -X "$method" -H "Content-Type: application/json" "${BASE_URL}${path}")
    HTTP_BODY="$(cat "$tmp_file")"
    rm -f "$tmp_file"
}


assert_status() {
    local expected="$1"

    if [ "$HTTP_STATUS" = "$expected" ]; then
        echo -e "${GREEN}✓ PASS${NC}: status is $expected"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: status should be $expected"
        echo -e "  Expected: '$expected'"
        echo -e "  Actual:   '$HTTP_STATUS'"
        echo -e "  Response body: $HTTP_BODY"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_json_eq() {
    local jq_path="$1"
    local expected="$2"
    local actual
    actual=$(echo "$HTTP_BODY" | jq -r "$jq_path")

    if [ "$actual" = "$expected" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $jq_path equals '$expected'"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $jq_path should equal '$expected'"
        echo -e "  jq path:  $jq_path"
        echo -e "  Expected: '$expected'"
        echo -e "  Actual:   '$actual'"
        echo -e "  Response body: $HTTP_BODY"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_json_field() {
    local jq_path="$1"
    local expected="$2"
    assert_json_eq "$jq_path" "$expected"
}


assert_json_exists() {
    local jq_path="$1"
    local actual
    actual=$(echo "$HTTP_BODY" | jq -r "$jq_path")

    if [ "$actual" != "null" ] && [ -n "$actual" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $jq_path exists"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $jq_path should exist"
        echo -e "  jq path: $jq_path"
        echo -e "  Response body: $HTTP_BODY"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_json_not_exists() {
    local jq_path="$1"
    local actual
    actual=$(echo "$HTTP_BODY" | jq -r "$jq_path")

    if [ "$actual" = "null" ] || [ -z "$actual" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $jq_path does not exist"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $jq_path should not exist"
        echo -e "  jq path: $jq_path"
        echo -e "  Actual: '$actual'"
        echo -e "  Response body: $HTTP_BODY"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_json_matches() {
    local jq_path="$1"
    local pattern="$2"
    local actual
    actual=$(echo "$HTTP_BODY" | jq -r "$jq_path")

    if [[ "$actual" =~ $pattern ]]; then
        echo -e "${GREEN}✓ PASS${NC}: $jq_path matches pattern"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $jq_path should match pattern"
        echo -e "  jq path:  $jq_path"
        echo -e "  Pattern:  $pattern"
        echo -e "  Actual:   '$actual'"
        echo -e "  Response body: $HTTP_BODY"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_json_true() {
    local jq_path="$1"
    local actual
    actual=$(echo "$HTTP_BODY" | jq "$jq_path")

    if [ "$actual" = "true" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $jq_path is true"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $jq_path should be true"
        echo -e "  Actual: '$actual'"
        echo -e "  Response body: $HTTP_BODY"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_json_false() {
    local jq_path="$1"
    local actual
    actual=$(echo "$HTTP_BODY" | jq "$jq_path")

    if [ "$actual" = "false" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $jq_path is false"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $jq_path should be false"
        echo -e "  Actual: '$actual'"
        echo -e "  Response body: $HTTP_BODY"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_json_array_min_length() {
    local jq_path="$1"
    local min_length="$2"
    local actual_length
    actual_length=$(echo "$HTTP_BODY" | jq "$jq_path | length")

    if [ "$actual_length" -ge "$min_length" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $jq_path length is at least $min_length"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $jq_path length should be at least $min_length"
        echo -e "  Actual length: $actual_length"
        echo -e "  Response body: $HTTP_BODY"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_json_gt() {
    local jq_path="$1"
    local threshold="$2"
    local actual
    actual=$(echo "$HTTP_BODY" | jq -r "$jq_path")

    if echo "$actual $threshold" | awk '{exit !($1 > $2)}'; then
        echo -e "${GREEN}✓ PASS${NC}: $jq_path is greater than $threshold"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $jq_path should be greater than $threshold"
        echo -e "  Actual: '$actual'"
        echo -e "  Threshold: '$threshold'"
        echo -e "  Response body: $HTTP_BODY"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_error_envelope() {
    local error_type
    local message_type

    error_type=$(echo "$HTTP_BODY" | jq -r '.error | type')
    message_type=$(echo "$HTTP_BODY" | jq -r '.message | type')

    if [ "$error_type" = "string" ] && [ "$message_type" = "string" ]; then
        echo -e "${GREEN}✓ PASS${NC}: error envelope contains string error and message"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: error envelope should contain string error and message"
        echo -e "  error type: '$error_type'"
        echo -e "  message type: '$message_type'"
        echo -e "  Response body: $HTTP_BODY"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_body_not_contains() {
    local patterns=("$@")
    local pattern

    for pattern in "${patterns[@]}"; do
        if echo "$HTTP_BODY" | grep -qi "$pattern"; then
            echo -e "${RED}✗ FAIL${NC}: response body should not contain pattern"
            echo -e "  Forbidden pattern: '$pattern'"
            echo -e "  Response body: $HTTP_BODY"
            ((TESTS_FAILED++))
            return 1
        fi
    done

    echo -e "${GREEN}✓ PASS${NC}: response body does not contain forbidden patterns"
    ((TESTS_PASSED++))
    return 0
}


assert_equals() {
    local expected="$1"
    local actual="$2"
    local message="$3"

    if [ "$expected" = "$actual" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $message"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $message"
        echo -e "  Expected: '$expected'"
        echo -e "  Actual:   '$actual'"
        ((TESTS_FAILED++))
        return 1
    fi
}


assert_not_equals() {
    local a="$1"
    local b="$2"
    local message="$3"

    if [ "$a" != "$b" ]; then
        echo -e "${GREEN}✓ PASS${NC}: $message"
        ((TESTS_PASSED++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}: $message"
        echo -e "  Should not equal: '$a'"
        echo -e "  But got:          '$b'"
        ((TESTS_FAILED++))
        return 1
    fi
}


crypto_keygen() {
    local output
    output=$(cd "$SERVICE_DIR" && uv run python "$CRYPTO" keygen)
    PRIVATE_KEY_HEX=$(echo "$output" | sed -n '1p')
    PUBLIC_KEY=$(echo "$output" | sed -n '2p')
}


crypto_sign_raw() {
    local priv_hex="$1"
    local raw_string="$2"
    local output
    output=$(cd "$SERVICE_DIR" && uv run python "$CRYPTO" sign_raw "$priv_hex" "$raw_string")
    PAYLOAD_B64=$(echo "$output" | sed -n '1p')
    SIGNATURE_B64=$(echo "$output" | sed -n '2p')
}


crypto_sign() {
    local priv_hex="$1"
    local payload_b64="$2"
    local output
    output=$(cd "$SERVICE_DIR" && uv run python "$CRYPTO" sign "$priv_hex" "$payload_b64")
    SIGNATURE_B64=$(echo "$output" | sed -n '1p')
}


crypto_pubkey_bytes() {
    local byte_count="$1"
    PUBLIC_KEY=$(cd "$SERVICE_DIR" && uv run python "$CRYPTO" pubkey_bytes "$byte_count")
}


crypto_zero_key() {
    PUBLIC_KEY=$(cd "$SERVICE_DIR" && uv run python "$CRYPTO" zero_key)
}


crypto_random_b64() {
    local byte_count="$1"
    cd "$SERVICE_DIR" && uv run python "$CRYPTO" random_b64 "$byte_count"
}


crypto_large_sign() {
    local priv_hex="$1"
    local size_bytes="$2"
    local output
    output=$(cd "$SERVICE_DIR" && uv run python "$CRYPTO" large_sign "$priv_hex" "$size_bytes")
    PAYLOAD_B64=$(echo "$output" | sed -n '1p')
    SIGNATURE_B64=$(echo "$output" | sed -n '2p')
}


register_agent() {
    local name="$1"
    local public_key="$2"
    local body
    body=$(jq -nc --arg name "$name" --arg public_key "$public_key" '{name:$name, public_key:$public_key}')

    http_post "/agents/register" "$body"

    if [ "$HTTP_STATUS" != "201" ]; then
        echo -e "${RED}✗ FAIL${NC}: Setup failed: register_agent returned non-201"
        echo -e "  Status: '$HTTP_STATUS'"
        echo -e "  Response body: $HTTP_BODY"
        exit 1
    fi

    AGENT_ID=$(echo "$HTTP_BODY" | jq -r '.agent_id')
}


build_verify_body() {
    local agent_id="$1"
    local payload_b64="$2"
    local signature_b64="$3"
    local agent_id_file
    local payload_file
    local signature_file

    VERIFY_BODY_FILE="$(mktemp)"
    agent_id_file="$(mktemp)"
    payload_file="$(mktemp)"
    signature_file="$(mktemp)"

    printf '%s' "$agent_id" >"$agent_id_file"
    printf '%s' "$payload_b64" >"$payload_file"
    printf '%s' "$signature_b64" >"$signature_file"

    jq -nc \
        --rawfile agent_id "$agent_id_file" \
        --rawfile payload "$payload_file" \
        --rawfile signature "$signature_file" \
        '{agent_id:$agent_id, payload:$payload, signature:$signature}' >"$VERIFY_BODY_FILE"

    rm -f "$agent_id_file" "$payload_file" "$signature_file"
}
