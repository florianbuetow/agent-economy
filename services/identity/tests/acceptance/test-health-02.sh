#!/bin/bash
# test-health-02.sh — HEALTH-02: Registered count is exact
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "HEALTH-02" "Registered count is exact"

step "Read current registered agent count"
http_get "/health"
BEFORE=$(echo "$HTTP_BODY" | jq -r '.registered_agents')

step "Register three new agents"
for i in 1 2 3; do
    crypto_keygen
    register_agent "HealthCount-$i" "$PUBLIC_KEY"
done

step "Read new registered agent count"
http_get "/health"
AFTER=$(echo "$HTTP_BODY" | jq -r '.registered_agents')
EXPECTED=$((BEFORE + 3))

step "Assert count incremented by exactly three"
assert_equals "$EXPECTED" "$AFTER" "registered_agents should increase by 3"

test_end
