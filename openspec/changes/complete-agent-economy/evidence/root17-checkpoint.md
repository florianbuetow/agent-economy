# Root17 interrupted validation

Command: `UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 just ci-quiet` from the repo root.
Parent session81299 is terminal, exit143. Both root and service recipes reported termination by
signal15. The signal source was not established; this is neither a passing gate nor a product-red
test result. No timeout inference or live-process restart was used.

The run passed structure, all three libraries and complete Identity, Bank, Task Board, Reputation
and Court component gates. Gateway passed through its unit and architecture checks and was
terminated at LSP validation. UI, per-service integration, agents/tools, cross/runtime, browser and
economy phases were not reached. The isolated stack launcher had not started any runtime.

Before/after snapshots contain931 identical source/config hashes. `root-ci-17-summary.json`
records the comparison and log hash. `root-ci-17-cleanup.json` contains no stack processes because
no isolated stack phase ran. A separate host process inspection found the observed root/process
tree gone; no unrelated process was signaled. The read-only inspection initially used unsupported
macOS ps column sid and was repeated with supported fields; that diagnostic error was not a
test failure.

The interrupted check was run independently in `services/db-gateway`:

```text
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 just code-lspchecks
0 errors, 0 warnings, 0 informations
```

Parent session71975 exited0. Log: `/private/tmp/agent-economy-root17-gateway-lsp-followup.log`.
Source remains frozen. Root18 is a new full-gate attempt, not a claim that the missing root17
phases passed. MP-04, T-175/T-176 and the full Core/Vision goal remain open.
