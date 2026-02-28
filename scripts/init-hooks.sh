#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="${REPO_ROOT}/.githooks"

printf "\033[0;34m--- Git Hooks Setup ---\033[0m\n"

# Verify required tools
if ! command -v git >/dev/null 2>&1; then
    echo "❌ git not found"
    exit 1
fi

if ! command -v bd >/dev/null 2>&1; then
    echo "❌ bd (beads) not found. Install it: brew install beads"
    exit 1
fi

# Install beads hooks (ensures .beads/hooks/ shims are current)
printf "Installing beads hooks...\n"
bd hooks install --beads --force

# Point git to .githooks/ (our composite hooks that chain beads + custom logic)
printf "Setting core.hooksPath to .githooks/\n"
git config core.hooksPath .githooks

# Ensure all hooks are executable
chmod +x "${HOOKS_DIR}"/*

# Verify
printf "\n\033[0;34m--- Installed Hooks ---\033[0m\n"
for hook in pre-commit prepare-commit-msg pre-push post-checkout post-merge; do
    if [ -x "${HOOKS_DIR}/${hook}" ]; then
        printf "  \033[0;32m✓\033[0m %s\n" "${hook}"
    else
        printf "  \033[0;31m✗\033[0m %s (missing or not executable)\n" "${hook}"
        exit 1
    fi
done

printf "\n\033[0;32m✓ Git hooks activated from .githooks/\033[0m\n"
