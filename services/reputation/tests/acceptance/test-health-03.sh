#!/bin/bash
# test-health-03.sh — HEALTH-03: Uptime is monotonic
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "HEALTH-03" "Uptime is monotonic"

step "Read initial uptime"
http_get "/health"
UPTIME1=$(echo "$HTTP_BODY" | jq -r '.uptime_seconds')

step "Wait and read uptime again"
sleep 1.5
http_get "/health"
UPTIME2=$(echo "$HTTP_BODY" | jq -r '.uptime_seconds')

step "Assert second uptime is greater than first"
if echo "$UPTIME2 $UPTIME1" | awk '{exit !($1 > $2)}'; then
    echo -e "${GREEN}✓ PASS${NC}: uptime_seconds increases over time"
    ((TESTS_PASSED++))
else
    echo -e "${RED}✗ FAIL${NC}: uptime_seconds should increase over time"
    echo -e "  First uptime:  '$UPTIME1'"
    echo -e "  Second uptime: '$UPTIME2'"
    ((TESTS_FAILED++))
    exit 1
fi

test_end
