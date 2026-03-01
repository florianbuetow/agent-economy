#!/bin/bash
# test-sec-02.sh — SEC-02: No internal error leakage
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "SEC-02" "No internal error leakage"

FORBIDDEN=("Traceback" "File \"" "sqlite" "psycopg" "sqlalchemy" "/home/" "/Users/" "/app/src/" "DETAIL:" "Internal Server Error" "NoneType")

step "Trigger INVALID_JSON and check leakage"
http_post_raw "/agents/register" '{broken'
assert_body_not_contains "${FORBIDDEN[@]}"

step "Trigger INVALID_BASE64 and check leakage"
crypto_keygen
register_agent "LeakCheckA" "$PUBLIC_KEY"
BODY_BAD_B64=$(jq -nc --arg agent_id "$AGENT_ID" --arg payload "%%%" --arg signature "aaa" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY_BAD_B64"
assert_body_not_contains "${FORBIDDEN[@]}"

step "Trigger duplicate key conflict and check leakage"
crypto_keygen
PUB_DUP="$PUBLIC_KEY"
register_agent "LeakDup1" "$PUB_DUP"
BODY_DUP=$(jq -nc --arg name "LeakDup2" --arg public_key "$PUB_DUP" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$BODY_DUP"
assert_body_not_contains "${FORBIDDEN[@]}"

step "Trigger malformed ID read and check leakage"
http_get "/agents/not-valid"
assert_body_not_contains "${FORBIDDEN[@]}"

test_end
