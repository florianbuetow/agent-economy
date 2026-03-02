#!/bin/bash
# test-reg-04.sh — REG-04: Concurrent duplicate key race is safe
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-04" "Concurrent duplicate key race is safe"

step "Generate one shared public key"
crypto_keygen
PUB_R="$PUBLIC_KEY"

step "Send two concurrent registration requests with identical key"
TMP1=$(mktemp)
TMP2=$(mktemp)
S1_FILE=$(mktemp)
S2_FILE=$(mktemp)

BODY1=$(jq -nc --arg name "RaceA" --arg public_key "$PUB_R" '{name:$name, public_key:$public_key}')
BODY2=$(jq -nc --arg name "RaceB" --arg public_key "$PUB_R" '{name:$name, public_key:$public_key}')

(curl -s -o "$TMP1" -w '%{http_code}' -X POST -H "Content-Type: application/json" "${BASE_URL}/agents/register" -d "$BODY1" > "$S1_FILE") &
PID1=$!
(curl -s -o "$TMP2" -w '%{http_code}' -X POST -H "Content-Type: application/json" "${BASE_URL}/agents/register" -d "$BODY2" > "$S2_FILE") &
PID2=$!

wait "$PID1"
wait "$PID2"

STATUS1="$(cat "$S1_FILE")"
STATUS2="$(cat "$S2_FILE")"
BODY_RESP1="$(cat "$TMP1")"
BODY_RESP2="$(cat "$TMP2")"

rm -f "$TMP1" "$TMP2" "$S1_FILE" "$S2_FILE"

step "Assert exactly one success and one conflict"
SORTED_STATUSES=$(printf "%s\n%s\n" "$STATUS1" "$STATUS2" | sort)
assert_equals $'201\n409' "$SORTED_STATUSES" "exactly one 201 and one 409 expected"

step "Identify winner and validate loser error"
if [ "$STATUS1" = "201" ]; then
    WINNER_BODY="$BODY_RESP1"
    LOSER_BODY="$BODY_RESP2"
else
    WINNER_BODY="$BODY_RESP2"
    LOSER_BODY="$BODY_RESP1"
fi

WINNER_ID=$(echo "$WINNER_BODY" | jq -r '.agent_id')
LOSER_ERROR=$(echo "$LOSER_BODY" | jq -r '.error')
assert_equals "PUBLIC_KEY_EXISTS" "$LOSER_ERROR" "loser must return PUBLIC_KEY_EXISTS"

step "Verify winner record exists with expected key"
http_get "/agents/$WINNER_ID"
assert_status "200"
assert_json_eq ".public_key" "$PUB_R"

test_end
