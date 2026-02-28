# =============================================================================
# Justfile Rules (follow these when editing justfile):
#
# 1. Use printf (not echo) to print colors — some terminals won't render
#    colors with echo.
#
# 2. Always add an empty `@echo ""` line before and after each target's
#    command block.
#
# 3. Always add new targets to the help section and update it when targets
#    are added, modified or removed.
#
# 4. Target ordering in help (and in this file) matters:
#    - Setup targets first (init, setup, install, etc.)
#    - Start/stop/run targets next
#    - Code generation / data tooling targets next
#    - Checks, linting, and tests next (ordered fastest to slowest)
#    Group related targets together and separate groups with an empty
#    `@echo ""` line in the help output.
#
# 5. Composite targets (e.g. ci) that call multiple sub-targets must fail
#    fast: exit 1 on the first error. Never skip over errors or warnings.
#    Use `set -e` or `&&` chaining to ensure immediate abort with the
#    appropriate error message.
#
# 6. Every target must end with a clear short status message:
#    - On success: green (\033[32m) message confirming completion.
#      E.g. printf "\033[32m✓ init completed successfully\033[0m\n"
#    - On failure: red (\033[31m) message indicating what failed, then exit 1.
#      E.g. printf "\033[31m✗ ci failed: tests exited with errors\033[0m\n"
# =============================================================================

# Default target
default: help

# Initialize repository settings
init:
    @echo ""
    @git config core.fileMode false
    @printf "\033[32m✓ init completed successfully\033[0m\n"
    @echo ""

# Initialize observatory service
init-observatory:
    @echo ""
    @printf "\033[34m=== Initializing Observatory Service ===\033[0m\n"
    @cd services/observatory && just init
    @printf "\033[32m✓ Observatory initialized\033[0m\n"
    @echo ""

# Run observatory CI
ci-observatory:
    @echo ""
    @printf "\033[34m=== Running Observatory CI ===\033[0m\n"
    @cd services/observatory && just ci-quiet
    @printf "\033[32m✓ Observatory CI passed\033[0m\n"
    @echo ""

# Show available targets
help:
    @echo ""
    @printf "\033[1mAvailable targets:\033[0m\n"
    @echo ""
    @printf "  \033[36minit\033[0m               Initialize repository settings\n"
    @printf "  \033[36minit-observatory\033[0m    Initialize observatory service\n"
    @echo ""
    @printf "  \033[36mci-observatory\033[0m     Run observatory CI checks\n"
    @echo ""
    @printf "  \033[36mhelp\033[0m               Show this help message\n"
    @echo ""
