# Docs Tracking Policy: Tracked Contracts vs. Archival/Generated

**Date:** 2026-07-13
**Status:** Accepted (records the WP-12.4 policy owed by GAP-G4/H-1)
**Source:** `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §4 (GAP-G4),
§5.0 (H-1), Appendix A

## Context

Commit `a791984` (2026-06-21) removed `docs/codex-tasks/*` and other generated docs (and
`DELEGATE.md`) from git tracking; they exist on disk only from that point forward, so any
post-removal edit to them leaves no history (§3, line 287). At some point after that, the
`.gitignore` blanket-ignored the entire `docs/` tree and `openspec/`, which GAP-G4 identified as a
defect: it silently untracked all 22 authored service specs, the runtime-loaded
`docs/specifications/schema.sql`, both 2026-06-13 decision records, the canonical `openspec/`
specs, and the target-architecture plan itself. Hotfix H-1 re-tracked the authored-contract
subtrees (commit `02fa5db`, 72 files): `docs/specifications/` was unignored and `openspec/` was
unignored.

**Current `.gitignore` state, verified directly (repo root, 2026-07-13):**

```
docs/*
!docs/specifications/
docs/plans/

.dolt/
*.db
```

`docs/specifications/` is unignored and tracked. `openspec/` does not appear in `.gitignore` at
all, so it is tracked normally. **`docs/plans/` itself still carries no `!` negation** — the
`docs/plans/` line re-states the ignore (redundant with the preceding `docs/*`), so on disk today
`docs/plans/` is git-ignored, even though H-1's own description (§5.0: "`.gitignore` `docs/` →
`docs/*` + `!docs/specifications/` + `!docs/plans/`") called for unignoring it too. This policy
document (and the eight ADRs alongside it) is being authored into `docs/plans/` on the expectation
that a separate step re-tracks that directory — this document does not itself change
`.gitignore`, per this task's scope.

## Decision

| Subtree | Classification | Rationale |
|---|---|---|
| `docs/specifications/` | **TRACKED CONTRACT** | API/test/schema contracts — `schema.sql` is a runtime dependency (db-gateway loads it at startup); source of truth for service behavior. |
| `docs/plans/` | **TRACKED CONTRACT** | Decision records, ADRs, and the target-architecture plan itself — the authoritative record of what was decided and why. |
| `openspec/` | **TRACKED CONTRACT** | Canonical specs and the completion backlog (Q-1) — the single issue tracker and behavioral source of truth. |
| `docs/codex-tasks/*` | **ARCHIVAL / regenerate-on-demand** | Historical, agent-execution scratch (phased task plans for coding agents). Untracked since `a791984`; kept on disk as historical reference, not hand-maintained, not a contract. |
| `docs/arc42/*` | **ARCHIVAL / regenerate-on-demand** | As-built reference documentation, reconstructed from code (see `docs/arc42/09-architecture-decisions.md` header: "generated_at", "source_commit"). Kept as a snapshot; regenerated on demand (planned after WP-06/08), not hand-maintained between regenerations. |
| `docs/java/*` | **ARCHIVAL / regenerate-on-demand** | Verified present on disk (`project_goals_and_architectural_decisions_claude.md`, `project_goals_and_architectural_decisions_codex.md`) — historical design notes from an earlier phase of the project, same class as `docs/codex-tasks/*`. Untracked since `a791984` per Appendix A. |

**Every other subtree currently on disk under `docs/` (`ls docs/`, 2026-07-13), each verified individually with `git check-ignore -v`:**

| Subtree / file | Classification | Rationale |
|---|---|---|
| `docs/checks/` | **ARCHIVAL** | `pytestarch-guide.md` — a reference guide, not a contract. `git check-ignore` confirms `docs/*` matches it. |
| `docs/codex/` | **ARCHIVAL** | `demo-replay-implementation.md` and siblings — prior-era implementation notes, same class as `docs/codex-tasks/*`. Ignored. |
| `docs/demo/` | **ARCHIVAL** | `demo-transcript.md` — a captured run transcript, regenerable. Ignored. |
| `docs/diagrams/` | **ARCHIVAL** | `system-sequence-diagrams.md` — per Appendix A this is stale (pre-gateway, wrong ports) and due a WP-12.2 regeneration from §2's flows; not hand-tracked as a contract in the meantime. Ignored. |
| `docs/explanations/` | **ARCHIVAL** | Prose explainers (e.g. `why-public-key-cryptography.md`) — background reading, not a source of truth for behavior. Ignored. |
| `docs/main/` | **ARCHIVAL** | `agent-task-economy.md`, the vision document. Appendix A says "KEEP as vision; add a 'status vs v1' preface" (WP-12.2) — authoritative for *intent* (CLAUDE.md precedence order #5) but classified archival here because it is not currently git-tracked; a future decision could promote it to a tracked contract, but that has not been ratified. Ignored today (verified). |
| `docs/mockups/` | **ARCHIVAL** | React-era HTML mockups superseded by the vanilla-JS frontend decision (R7); Appendix A calls for SUPERSEDED banners, not tracking. Ignored. |
| `docs/submission/` | **ARCHIVAL** | Screenshots and submission text for a past deliverable — a point-in-time artifact, not a maintained contract. Ignored. |
| `docs/superpowers/` | **ARCHIVAL** | Tooling-support content (`plans/`, `specs/` subfolders) unrelated to this project's own specs. Ignored. |
| `docs/retrospective/` | **ARCHIVAL** | Dated retrospective notes (`2026-03-16-v1.md` etc.) — historical record, not a contract. Ignored. |
| `docs/service-implementation-guide.md` | **ARCHIVAL** | Referenced extensively by CLAUDE.md as a how-to guide; still classified archival because it is a loose file directly under `docs/*` and is currently ignored (verified) — the tracking policy only carves out the three subtree exceptions below, not individual loose files. Flagged as a candidate for promotion if it is ever treated as a hard contract rather than a guide. |
| `docs/report-architecture-assessment.md`, `docs/report-beyond-solid-architecture-audit.md`, `docs/report-solid-audit.md` | **ARCHIVAL** | Point-in-time audit reports. Ignored (verified). |

## Consequences

- New authored contracts (a new service spec, a new decision record, a new openspec scenario)
  must land under `docs/specifications/`, `docs/plans/`, or `openspec/` and must be committed —
  these three subtrees are never allowed to silently fall back under a blanket `docs/*` ignore.
- `docs/codex-tasks/*`, `docs/arc42/*`, and `docs/java/*` may be regenerated, pruned, or left stale
  without a tracking-policy violation; they are not release gates and nothing in CI depends on
  their being current.
- **Escalation, not silently resolved:** `docs/plans/` is described as re-tracked by H-1 (§5.0,
  commit `02fa5db`) but the `.gitignore` read directly today does not carry the `!docs/plans/`
  negation the hotfix description calls for — only `docs/specifications/` was actually unignored.
  This document does not modify `.gitignore` (out of scope for this task); the discrepancy should
  be reconciled by whoever next touches `.gitignore` for WP-12.4, by adding `!docs/plans/`
  alongside the existing `!docs/specifications/` line.
- Commit-hash citations in this document were independently verified via `git log` (read-only;
  this task's rules exclude git *write* commands, not reads): `a791984` is
  "chore: stop tracking generated docs and delegation guide" (2026-06-21), matching the plan's
  "commit `a791984`" claim for the original blanket `docs/` ignore; `02fa5db` is
  "chore(repo): re-track authored contracts hidden by blanket docs/ ignore" (2026-07-10), matching
  H-1's description. `git ls-files docs/plans/` currently shows 59 tracked files despite the
  ignore-pattern discrepancy above — i.e. specific files were force-added individually, but the
  pattern itself does not exempt the directory, so each *new* file (including this one) still
  needs an explicit `git add -f` by whatever process commits this batch.
