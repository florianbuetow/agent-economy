#!/usr/bin/env bash
# ci-all-quiet-hook.sh — Claude Code PreToolUse hook for git commit.
#
# Receives tool input on stdin. If the command is a git commit,
# runs `just ci-all-quiet` first. Blocks the commit (exit 2) if CI fails.
# All other commands pass through immediately.
#
# Usage: Configured as a PreToolUse hook in .claude/settings.json

set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Only intercept git commit commands
if ! echo "$COMMAND" | grep -qE 'git commit'; then
    exit 0
fi

PROJECT_DIR=$(echo "$INPUT" | jq -r '.cwd // empty')
if [ -z "$PROJECT_DIR" ]; then
    PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi
cd "$PROJECT_DIR"

if ! just ci-all-quiet 2>&1; then
    echo "CI failed — fix all errors before committing." >&2
    exit 2
fi

exit 0
