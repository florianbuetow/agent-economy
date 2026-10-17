# Proposal

## Why

The current agent economy has working normal lifecycle paths but still has financial recovery, concurrency, adjudication, privacy and participation gaps identified in `MASTERPLAN-TO-COMPLETE-THE-PROJECT.md`. The latest user-supplied `just ci` run also fails in UI unit tests (5 failures, 2 setup errors), so repairing that baseline is the first implementation step.

## What Changes

- Repair outdated UI test configuration fixtures without weakening the required `user_agent` contract, and establish the matching Playwright Chromium prerequisite before browser-dependent unit tests. Track these separately as T-165/T-166 and T-167/T-168 under MP-00.
- Repair fractional-second deadline truncation exposed by the isolated baseline, with deterministic T-169/T-170 proof and no weakened expiry/refund assertions. This independent prerequisite does not replace MP-07's atomic-transition work.
- **BREAKING**: require authenticated, domain-scoped gateway callers and a consistent typed token profile; harden browser rendering, Host/Origin checks and sealed-data projections.
- **BREAKING**: give money and task commands payload-bound operation identities, durable receipts, atomic transition claims and restart reconciliation; reject conflicting replays and invalid monetary values explicitly.
- Version the schema, verify backup/restore before migrations, enforce integer conservation and repair startup/readiness and test-runtime isolation.
- Store immutable task inputs, deliverables, Court evidence and verdicts; resume the same ruling after failure, enforce safe artifact handling and evaluate the real three-judge profile against the ambiguity-favors-worker rule.
- Authorize feedback by task participation, make reveals durable and provide independent specification/delivery score semantics after Q-M3 is decided.
- Complete human posting and working flows, external-agent onboarding, feeder/worker restart recovery, trustworthy UI metrics and cursor-based event delivery.
- Preserve the 54 original audit findings, MP-01–MP-26 and T-110–T-164; add the newly reported UI CI gap and MP-00 without renumbering prior work. Require real journey, crash/concurrency and full-CI evidence for implementation closure.
- Keep Q-M1 (artifact/evidence policy), Q-M2 (post-v1 pricing) and Q-M3 (quality aggregation) explicit decision tasks. Retain the ratified local single-operator, manual funding, fixed-reward v1, vanilla-JavaScript and Task-Board-owned settlement choices.

## Capabilities

### New Capabilities

- `ui-ci-readiness`: explicit valid UI test fixtures, failure-specific configuration checks, reproducible Chromium preparation and fail-fast local/hosted browser-test prerequisites.

### Modified Capabilities

- `agent-task-economy`: strengthen the existing economy requirements for authority, atomic/recoverable lifecycle and money, immutable evidence/rulings, sealed projections, complete human/external-agent participation and verified operation.
- `completion-backlog`: retain stable existing IDs and add the completion work/test/decision pairs T-110–T-170 with concrete acceptance and dependency obligations.
- `delivery-governance`: specify the current dependency queue, red-proven unblocking without premature closure, isolated verification, the narrow UI fixture reconciliation and the user's office-hours prohibition on commits/pushes.

## Impact

Implementation affects all seven services, shared auth/clients, the schema, agent SDK/runtime/examples, frontend, setup/CI recipes and their tests/specifications. The immediate UI repair concerns `services/ui/tests/unit/routers/conftest.py`, `test_config.py`, `test_lifespan_logging.py`, Playwright setup and early preflight; the three trend assertions and production required-section validation remain intact.

This change recreates planning artifacts through OpenSpec's `spec-driven` workflow. Planning artifacts do not close implementation work. The working location is the existing `refactor` workspace. Commits and pushes are allowed all day on Saturday and Sunday; weekdays prohibit both from 08:00 inclusive until 18:00 exclusive Europe/Amsterdam. Verify the actual local date and time immediately before either action. No new runtime dependency, delivery branch, deployment platform or frontend rewrite is required by this proposal.
