# Docker mode descoped (WP-10 item 1)

**Date:** 2026-07-13
**Executes:** `docs/plans/2026-07-10-q6-docker-scope-decision.md` (Q-6, ratified: descope now)

## What was deleted

- `docker-compose.yml`, `docker-compose.dev.yml`
- Every `services/*/Dockerfile` (identity, central-bank, task-board, reputation, court, db-gateway, ui)
- `services/reputation/docker-config.yaml` (the only per-service Docker config that existed)
- The root justfile's `docker-*` recipes (`docker-up`, `docker-up-dev`, `docker-down`, `docker-logs`,
  `docker-build`) and their help-menu lines, plus the `docker` entry in `just check`'s tool list.
- The identical `docker-*` recipes, help lines, and `check_tool "docker"` line in all seven
  per-service justfiles, plus the now-dead `DIR_NAME` variable each used only to address itself
  inside `docker compose`.

No `.dockerignore` files existed. No test asserted a Dockerfile or compose file exists.

## What is the supported mode now

Local 4-tier `just start-all` (db-gateway → identity → economy services → UI) is the only supported
way to run the stack, until an actual multi-host deployment target exists (per Q-6's rationale: zero
current consumer, zero CI coverage, six independent breakages, effort not justified without a real
target). `just start-all`/`just stop-all`/`just status` are unaffected by this change.

## Left for WP-12 (docs sweep)

- `README.md` still documents `just docker-up`/`docker-up-dev`/`docker-down`/`docker-logs` and lists
  Docker as an optional prerequisite (lines ~84, ~103, ~125-132 as of this commit).
