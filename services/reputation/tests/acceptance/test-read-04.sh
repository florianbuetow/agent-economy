#!/bin/bash
# test-read-04.sh — READ-04: SQL injection in path parameters
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "READ-04" "SQL injection in path parameters"

step "SQL injection in feedback ID"
http_get "/feedback/' OR '1'='1"
assert_status "404"
assert_json_eq ".error" "FEEDBACK_NOT_FOUND"
assert_body_not_contains "traceback" "Traceback" "sqlalchemy" "sqlite" "psycopg" ".py"

step "SQL injection in agent ID"
http_get "/feedback/agent/' OR '1'='1"
assert_status "200"
assert_json_array_length ".feedback" 0
assert_body_not_contains "traceback" "Traceback" "sqlalchemy" "sqlite" "psycopg" ".py"

step "SQL injection in task ID"
http_get "/feedback/task/' OR '1'='1"
assert_status "200"
assert_json_array_length ".feedback" 0
assert_body_not_contains "traceback" "Traceback" "sqlalchemy" "sqlite" "psycopg" ".py"

test_end
