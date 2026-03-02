#!/bin/bash
# test-smoke-01.sh — SMOKE-01: End-to-end flow
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "SMOKE-01" "End-to-end flow"

step "Register worker agent on Identity"
jws_keygen
WORKER_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Worker" "$PUBLIC_KEY"
WORKER_ID="$AGENT_ID"

step "Register poster agent on Identity"
jws_keygen
POSTER_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Poster" "$PUBLIC_KEY"
POSTER_ID="$AGENT_ID"

step "Create accounts via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$WORKER_ID" 0
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$POSTER_ID" 200

step "Health check: total_accounts == 2"
http_get "/health"
assert_status "200"
assert_json_eq ".status" "ok"
assert_json_eq ".total_accounts" "2"
assert_json_eq ".total_escrowed" "0"

step "Poster locks 100 in escrow for task T-001"
PAYLOAD=$(jq -nc --arg agent_id "$POSTER_ID" --arg task_id "T-001" --argjson amount 100 '{action:"escrow_lock", agent_id:$agent_id, amount:$amount, task_id:$task_id}')
jws_sign "$POSTER_PRIV" "$POSTER_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/lock" "$BODY"
assert_status "201"
ESCROW_ID=$(echo "$HTTP_BODY" | jq -r '.escrow_id')

step "Health check: total_escrowed == 100"
http_get "/health"
assert_status "200"
assert_json_eq ".total_escrowed" "100"

step "Platform releases escrow to worker"
PAYLOAD=$(jq -nc --arg escrow_id "$ESCROW_ID" --arg recipient "$WORKER_ID" '{action:"escrow_release", escrow_id:$escrow_id, recipient_account_id:$recipient}')
jws_sign "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/$ESCROW_ID/release" "$BODY"
assert_status "200"
assert_json_eq ".status" "released"

step "Worker checks balance == 100"
PAYLOAD=$(jq -nc '{action:"get_balance"}')
jws_sign "$WORKER_PRIV" "$WORKER_ID" "$PAYLOAD"
http_get_with_bearer "/accounts/$WORKER_ID" "$JWS_TOKEN"
assert_status "200"
assert_json_eq ".balance" "100"

step "Poster checks balance == 100"
PAYLOAD=$(jq -nc '{action:"get_balance"}')
jws_sign "$POSTER_PRIV" "$POSTER_ID" "$PAYLOAD"
http_get_with_bearer "/accounts/$POSTER_ID" "$JWS_TOKEN"
assert_status "200"
assert_json_eq ".balance" "100"

step "Health check: total_escrowed == 0"
http_get "/health"
assert_status "200"
assert_json_eq ".total_escrowed" "0"

test_end

