#!/usr/bin/env bash

# ruleid: agent-economy.no-shell-path-overrides
PATH="/tmp/tool-shims:/usr/bin:/bin" command --version

# ruleid: agent-economy.no-shell-path-overrides
export PATH="/tmp/tool-shims:/usr/bin:/bin"

# ruleid: agent-economy.no-shell-path-overrides
env PATH="/tmp/tool-shims:/usr/bin:/bin" command --version

# ok: agent-economy.no-shell-path-overrides
printf '%s\n' "$PATH"

# ok: agent-economy.no-shell-path-overrides
route_path="/api/tasks"
