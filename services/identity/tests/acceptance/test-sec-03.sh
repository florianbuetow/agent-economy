#!/bin/bash
# test-sec-03.sh — SEC-03: Agent IDs are opaque and random
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "SEC-03" "Agent IDs are opaque and random"

step "Register five agents and collect IDs"
IDS=()
for i in 1 2 3 4 5; do
    crypto_keygen
    register_agent "Opaque-$i" "$PUBLIC_KEY"
    IDS+=("$AGENT_ID")
done

step "Assert every ID matches UUID format"
for id in "${IDS[@]}"; do
    if [[ "$id" =~ $UUID4_PATTERN ]]; then
        echo -e "${GREEN}✓ PASS${NC}: ID matches UUID pattern"
        ((TESTS_PASSED++))
    else
        echo -e "${RED}✗ FAIL${NC}: ID should match UUID pattern"
        echo -e "  Actual ID: '$id'"
        ((TESTS_FAILED++))
        exit 1
    fi
done

step "Assert all IDs are unique"
UNIQUE_COUNT=$(printf "%s\n" "${IDS[@]}" | sort -u | wc -l | tr -d ' ')
assert_equals "5" "$UNIQUE_COUNT" "all IDs should be unique"

test_end
