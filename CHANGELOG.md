# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added real dispute lifecycle coverage for Task Board to Court handoff, worker rebuttals, rulings, and agent loop behavior.
- Extracted PKI (Ed25519 signing, JWS, platform/user agents) into `libs/service-auth` — one canonicalization, `iat`/`exp` claims, shared by all services (WP-02).
- Added autonomous bid acceptance (feeder) and autonomous dispute-ruling triggers (Task Board), so the full task lifecycle now runs with no demo script and no human in the loop — proven end to end by `agents/tests/e2e/test_unattended_economy.py`, which drives the real feeder and worker loops via an injectable LLM-transport test seam (WP-06 `6aba0e2`, WP-09 `ded2a90`, WP-15 `dd6957f`).
- Added a dedicated UI `operator` agent identity plus an idempotent `just provision` treasury bootstrap, replacing the old UI-startup auto-mint (WP-08).
- Added hosted CI: a GitHub Actions workflow (`.github/workflows/ci.yml`) runs `just ci-quiet` on every push and pull request using the mock judge provider, so no LLM API key is required (WP-10, `3c8e6e6`, `ce5d791`).

### Changed

- Updated demo scenarios and replay clients to exercise dispute, rebuttal, and ruling steps through real service APIs.
- Updated agent loops to use the Task Board's lowercase lifecycle status vocabulary.
- Updated Task Board lifecycle event emission to publish semantic transition events.
- Ratified all 16 open questions (Q-1..Q-16) from the target-architecture-and-refactoring plan as decision records (`docs/plans/2026-07-10-q*-decision.md`); no product/architecture decision remains outstanding (Step E1, `a59d1a7`).
- Unified issue tracking on `openspec/specs/completion-backlog/spec.md` (`T-###` scenarios) with a governance spec for ratified decisions and ticket closure rules; beads and the root `tickets.md` are retired (WP-01).
- Rolled out two-tier signature verification: platform-signed operations (central bank, task board, reputation, court) verify locally via `libs/service-auth`; agent-signed operations continue to verify against Identity (WP-03).
- Reworked Reputation's `force_visible` semantics and moved its sealed-feedback reveal timeout onto an injectable clock seam, with mutation-checked non-interference guards (WP-07, `45729a6`); the underlying atomic in-transaction reveal itself (replacing a read-then-write race between concurrent counter-feedback submissions) landed earlier as hotfix H-7 (`478153e`).
- Decomposed the Task Board and Database Gateway god-classes into per-domain modules, and swept dead code, config, and dependencies across services and agents (WP-11 hygiene sweep, `e71c919`).
- Regenerated README.md, AGENTS.md, and this changelog for the current seven-service architecture, real ports, and the unattended economy as the headline capability (WP-12).

### Fixed

- Fixed Court client behavior for platform-mediated claim filing, rebuttal submission, and ruling triggers.
- Fixed UI competitive-task queries to use explicit aggregate expressions.
- Fixed UI quarterly integration fixtures to pin the seeded current quarter.
- Fixed Database Gateway write/event pairing gaps (ruling deletion, claim status updates) so every write emits its event, and fixed idempotent-replay sentinels to return real values. Rebuilt Identity's and Central Bank's DB clients as async wrappers over the shared `libs/service-clients` `GatewayClient` (routers drop `run_in_threadpool`); Reputation's and Court's hand-rolled sync clients stay by a recorded deviation (their sync calls now run via `run_in_threadpool` instead of blocking) — full migration is future work (WP-04, `2fd507e`).
- Fixed Task Board lifecycle correctness: bid-count parity with the gateway, tasks with bids now expire like any other, deadlines are swept proactively instead of only on read (WP-05).
- Fixed the agent review loop reading submitted answers from payload fields that don't exist on the real Task Board API — every real submission was auto-disputed regardless of correctness (WP-09, `ded2a90`).

### Removed

- Removed the retired UI leaderboard badge metric from active schemas, services, frontend rendering, and tests.
- Removed Docker support entirely (`docker-compose.yml`, every `services/*/Dockerfile`, and all `docker-*` justfile recipes) — zero current consumer, zero CI coverage; local `just start-all` is the only supported way to run the stack (WP-10, Q-6, `3c8e6e6`).
- Removed the unused `strands-agents` dependency from the agent runtime (WP-09, `ded2a90`).

### Security

- Remediated additional CVEs across all service virtual environments.

## 2026-05-19

### Security

- Remediated 21 known CVEs across all service virtual environments.

## 2026-04-13

### Added

- Introduced Agent Task Economy as a Python microservices monorepo (initially five services; grown since to seven services, an autonomous agent runtime, and three shared libraries).
- Added audit logging for Central Bank ledger mutations (CONS-013).
