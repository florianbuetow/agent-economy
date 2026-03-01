#!/usr/bin/env bash
# ci-all-quiet-hook.sh — Claude Code Stop hook: runs CI only when tracked files changed.
#
# Behavior:
#   - No uncommitted changes → skip CI, exit 0
#   - Changes detected → run `just ci-all-quiet`
#   - CI passes → exit 0
#   - CI fails → output JSON block decision (keeps Claude working)
#   - After 2 consecutive failures → give up, exit 0
#
# Usage: just ci-all-quiet-hook  (or directly: bash scripts/ci-all-quiet-hook.sh)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RETRY_FILE="/tmp/ci-all-quiet-hook-$(echo "$PROJECT_DIR" | shasum -a 256 | cut -c1-12)"

cd "$PROJECT_DIR"

# No uncommitted changes to tracked files? Skip CI.
if git diff --quiet HEAD 2>/dev/null && git diff --cached --quiet 2>/dev/null; then
    rm -f "$RETRY_FILE"
    exit 0
fi

# Guard against infinite retry loops (max 2 attempts)
retries=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
if [ "$retries" -ge 2 ]; then
    rm -f "$RETRY_FILE"
    printf "\033[0;33m⚠ ci-all-quiet-hook: giving up after %d failed attempts\033[0m\n" "$retries"
    exit 0
fi
echo $((retries + 1)) > "$RETRY_FILE"

# Run CI
if just ci-all-quiet 2>&1; then
    rm -f "$RETRY_FILE"
    printf "\033[0;32m✓ ci-all-quiet-hook passed\033[0m\n"
    exit 0
else
    printf '{"decision":"block","reason":"CI failed on modified files. Please fix before stopping."}\n'
fi
