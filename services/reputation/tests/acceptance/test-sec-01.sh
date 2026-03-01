#!/bin/bash
# test-sec-01.sh — SEC-01: Error envelope consistency
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "SEC-01" "Error envelope consistency"

step "MISSING_FIELD: POST /feedback with empty body"
http_post "/feedback" '{}'
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"
assert_error_envelope

step "INVALID_RATING: POST /feedback with rating 'excellent'"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$BOB" \
    --arg category "delivery_quality" \
    --arg rating "excellent" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "INVALID_RATING"
assert_error_envelope

step "INVALID_CATEGORY: POST /feedback with category 'timeliness'"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$BOB" \
    --arg category "timeliness" \
    --arg rating "satisfied" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "INVALID_CATEGORY"
assert_error_envelope

step "SELF_FEEDBACK: POST /feedback with same from and to agent"
SELF_AGENT=$(gen_agent_id)
TASK=$(gen_task_id)
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$SELF_AGENT" \
    --arg to_agent_id "$SELF_AGENT" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "SELF_FEEDBACK"
assert_error_envelope

step "FEEDBACK_NOT_FOUND: GET /feedback with non-existent ID"
http_get "/feedback/fb-00000000-0000-0000-0000-000000000000"
assert_status "404"
assert_json_eq ".error" "FEEDBACK_NOT_FOUND"
assert_error_envelope

step "INVALID_JSON: POST /feedback with malformed JSON"
http_post_raw "/feedback" '{broken'
assert_status "400"
assert_json_eq ".error" "INVALID_JSON"
assert_error_envelope

step "UNSUPPORTED_MEDIA_TYPE: POST /feedback with text/plain"
http_post_content_type "/feedback" "text/plain" '{"task_id":"t-123"}'
assert_status "415"
assert_json_eq ".error" "UNSUPPORTED_MEDIA_TYPE"
assert_error_envelope

step "METHOD_NOT_ALLOWED: DELETE /feedback"
http_method "DELETE" "/feedback"
assert_status "405"
assert_json_eq ".error" "METHOD_NOT_ALLOWED"
assert_error_envelope

step "FEEDBACK_EXISTS: Submit feedback then submit same one again"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"
assert_status "201"
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"
assert_status "409"
assert_json_eq ".error" "FEEDBACK_EXISTS"
assert_error_envelope

step "INVALID_FIELD_TYPE: POST /feedback with task_id as integer"
BODY=$(jq -nc \
    --arg from_agent_id "a-test" \
    --arg to_agent_id "a-test2" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    '{task_id:123, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "INVALID_FIELD_TYPE"
assert_error_envelope

step "COMMENT_TOO_LONG: POST /feedback with 257-character comment"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)
LONG_COMMENT=$(python3 -c "print('A' * 257)")
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$BOB" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    --arg comment "$LONG_COMMENT" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating, comment:$comment}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "COMMENT_TOO_LONG"
assert_error_envelope

step "PAYLOAD_TOO_LARGE: POST /feedback with oversized body"
TMP_FILE=$(mktemp)
printf '{"task_id":"t-test","from_agent_id":"a-test","to_agent_id":"a-test2","category":"delivery_quality","rating":"satisfied","comment":"' > "$TMP_FILE"
dd if=/dev/zero bs=1024 count=2048 2>/dev/null | tr '\0' 'A' >> "$TMP_FILE"
printf '"}' >> "$TMP_FILE"
http_post_file "/feedback" "$TMP_FILE"
rm -f "$TMP_FILE"
assert_status "413"
assert_json_eq ".error" "PAYLOAD_TOO_LARGE"
assert_error_envelope

test_end
