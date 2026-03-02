#!/bin/bash
set -e

BASE_URL="${REPUTATION_BASE_URL:-http://localhost:8004}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Reputation Service Acceptance Tests${NC}"
echo -e "${BLUE}Base URL: ${BASE_URL}${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

for cmd in curl jq python3; do
    if ! command -v "$cmd" > /dev/null 2>&1; then
        echo -e "${RED}Error: required command not found: $cmd${NC}"
        exit 1
    fi
done

TESTS=(
    "test-health-01.sh"
    "test-health-03.sh"
    "test-fb-01.sh"
    "test-fb-02.sh"
    "test-fb-03.sh"
    "test-fb-04.sh"
    "test-fb-05.sh"
    "test-fb-06.sh"
    "test-fb-07.sh"
    "test-fb-08.sh"
    "test-fb-09.sh"
    "test-fb-10.sh"
    "test-fb-11.sh"
    "test-fb-12.sh"
    "test-fb-13.sh"
    "test-fb-14.sh"
    "test-fb-15.sh"
    "test-fb-16.sh"
    "test-fb-17.sh"
    "test-fb-18.sh"
    "test-fb-19.sh"
    "test-fb-20.sh"
    "test-fb-21.sh"
    "test-fb-22.sh"
    "test-fb-23.sh"
    "test-fb-24.sh"
    "test-fb-25.sh"
    "test-vis-01.sh"
    "test-vis-02.sh"
    "test-vis-03.sh"
    "test-vis-04.sh"
    "test-vis-05.sh"
    "test-vis-06.sh"
    "test-vis-07.sh"
    "test-vis-08.sh"
    "test-read-01.sh"
    "test-read-02.sh"
    "test-read-03.sh"
    "test-read-04.sh"
    "test-read-05.sh"
    "test-task-01.sh"
    "test-task-02.sh"
    "test-agent-01.sh"
    "test-agent-02.sh"
    "test-agent-03.sh"
    "test-health-02.sh"
    "test-http-01.sh"
    "test-sec-01.sh"
    "test-sec-02.sh"
    "test-sec-03.sh"
)

total=${#TESTS[@]}
passed=0
start_time=$(date +%s)

for test_file in "${TESTS[@]}"; do
    echo -e "${BLUE}Running${NC} $test_file..."

    if ! bash "$SCRIPT_DIR/$test_file"; then
        echo -e "${RED}STOPPED:${NC} $test_file failed. $passed/$total passed before failure."
        exit 1
    fi

    echo -e "${GREEN}✓ $test_file passed${NC}"
    echo ""
    passed=$((passed + 1))
done

end_time=$(date +%s)
elapsed=$((end_time - start_time))

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Summary${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "Total:   $total"
echo -e "Passed:  ${GREEN}$passed${NC}"
echo -e "Elapsed: ${elapsed}s"
echo -e "All tests passed!"
echo -e "${BLUE}========================================${NC}"

exit 0
