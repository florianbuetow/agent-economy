#!/bin/bash
# test-escrow-07.sh — ESCROW-07: Split escrow success
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ESCROW-07" "Split escrow success"

step "Register agents on Identity service"
jws_keygen
POSTER_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Poster" "$PUBLIC_KEY"
POSTER_ID="$AGENT_ID"

jws_keygen
WORKER_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Worker" "$PUBLIC_KEY"
WORKER_ID="$AGENT_ID"

step "Create accounts via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$POSTER_ID" 100
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$WORKER_ID" 0

step "Poster locks 100 in escrow"
PAYLOAD=$(jq -nc --arg agent_id "$POSTER_ID" --arg task_id "T-007" --argjson amount 100 '{action:"escrow_lock", agent_id:$agent_id, amount:$amount, task_id:$task_id}')
jws_sign "$POSTER_PRIV" "$POSTER_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/lock" "$BODY"
assert_status "201"
ESCROW_ID=$(echo "$HTTP_BODY" | jq -r '.escrow_id')

step "Platform splits escrow 70/30"
PAYLOAD=$(jq -nc --arg escrow_id "$ESCROW_ID" --arg worker "$WORKER_ID" --arg poster "$POSTER_ID" --argjson worker_pct 70 '{action:"escrow_split", escrow_id:$escrow_id, worker_account_id:$worker, poster_account_id:$poster, worker_pct:$worker_pct}')
jws_sign "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/$ESCROW_ID/split" "$BODY"

step "Assert split response"
assert_status "200"
assert_json_eq ".escrow_id" "$ESCROW_ID"
assert_json_eq ".status" "split"
WORKER_AMOUNT=$(echo "$HTTP_BODY" | jq -r '.worker_amount')
POSTER_AMOUNT=$(echo "$HTTP_BODY" | jq -r '.poster_amount')
SUM=$((WORKER_AMOUNT + POSTER_AMOUNT))
assert_equals "100" "$SUM" "Split amounts sum to original"
assert_json_eq ".worker_amount" "70"
assert_json_eq ".poster_amount" "30"

step "Balances reflect split"
PAYLOAD=$(jq -nc '{action:"get_balance"}')
jws_sign "$WORKER_PRIV" "$WORKER_ID" "$PAYLOAD"
http_get_with_bearer "/accounts/$WORKER_ID" "$JWS_TOKEN"
assert_status "200"
assert_json_eq ".balance" "70"

PAYLOAD=$(jq -nc '{action:"get_balance"}')
jws_sign "$POSTER_PRIV" "$POSTER_ID" "$PAYLOAD"
http_get_with_bearer "/accounts/$POSTER_ID" "$JWS_TOKEN"
assert_status "200"
assert_json_eq ".balance" "30"

test_end

