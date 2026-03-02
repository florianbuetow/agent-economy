#!/bin/bash
# test-escrow-04.sh — ESCROW-04: Release escrow success
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ESCROW-04" "Release escrow success"

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

step "Poster locks 50 in escrow"
PAYLOAD=$(jq -nc --arg agent_id "$POSTER_ID" --arg task_id "T-004" --argjson amount 50 '{action:"escrow_lock", agent_id:$agent_id, amount:$amount, task_id:$task_id}')
jws_sign "$POSTER_PRIV" "$POSTER_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/lock" "$BODY"
assert_status "201"
ESCROW_ID=$(echo "$HTTP_BODY" | jq -r '.escrow_id')

step "Platform releases escrow to worker"
PAYLOAD=$(jq -nc --arg escrow_id "$ESCROW_ID" --arg recipient "$WORKER_ID" '{action:"escrow_release", escrow_id:$escrow_id, recipient_account_id:$recipient}')
jws_sign "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/$ESCROW_ID/release" "$BODY"

step "Assert release response"
assert_status "200"
assert_json_eq ".escrow_id" "$ESCROW_ID"
assert_json_eq ".status" "released"
assert_json_eq ".recipient" "$WORKER_ID"
assert_json_eq ".amount" "50"

step "Worker balance reflects release"
PAYLOAD=$(jq -nc '{action:"get_balance"}')
jws_sign "$WORKER_PRIV" "$WORKER_ID" "$PAYLOAD"
http_get_with_bearer "/accounts/$WORKER_ID" "$JWS_TOKEN"
assert_status "200"
assert_json_eq ".balance" "50"

test_end

