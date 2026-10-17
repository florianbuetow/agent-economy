# Root19 verified baseline and MP-04 checkpoint

Command from the repository root:

```text
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 just ci-quiet
```

Parent session18905 is TERMINAL0. The command completed every phase and printed Full CI passed.
The before/after snapshots contain the same932 source/config hashes, with no changed, added or
removed inputs. Source remains fixed while the closure record is reconciled.

## Gate evidence

| Phase | Observed result |
| --- | --- |
| Project structure | Passed |
| Shared libraries | 177 service-commons,104 service-clients,311 service-auth tests passed |
| Seven service component CIs | All configured style, type, security, dependency, spelling, Semgrep, audit, unit and architecture/LSP checks passed |
| Per-service integration | Identity7, Bank25, Task Board2, Reputation12, Court1, Gateway6, UI246 passed |
| Agents component CI | 390 passed,3 credential-dependent skips,94 E2E deselected here and run in final phase |
| Tools component CI | 144 passed |
| Cross-service/runtime | 203 passed, including all3 repaired migration/crash cases |
| UI browser suite | 298 passed, including quarterly concurrency and previously blocked single-agent fixtures |
| Live-agent E2E | 94 passed, including unattended approval/dispute and autonomous ruling paths |

Raw evidence is retained as root-ci-19.log/.exit, before/after/hash-comparison/summary JSON and
the two runtime manifests. The log SHA-256 is
`583f420a90089eeacd69711c6be51765fdedb03ea7254c773d446dfb2f6bc5f6`.

The two root-owned stacks contained19 process groups. Their manifests record stopped/drained
groups and no cleanup errors; fresh host signal0 probes found no live or unverified group.
See root-ci-19-cleanup.json. Separately,8 fresh migration/crash manifests/records were preserved
before pytest retention cleanup. root-ci-19-migration-cleanup.json verifies their56 owned groups
absent with clean metadata. These checks sent no termination signals to ordinary processes.

## Items supported for closure

- T-116/T-117 and tasks6.2–6.4: financial receipt implementation, history, caller integration,
  public opposing-command proof, documentation and required gate. Detailed mapping and honest
  red-proof limitations are in mp04-receipts/completion-audit.md.
- T-175/T-176 and task2.12: quarterly navigation preserves rapid clicks. Two intended reds and
  one existing error-recovery control preceded the repair; new and unchanged browser assertions
  now pass in the complete suite. See quarter-navigation-checkpoint.md.
- T-177/T-178 and task2.14: UI fixture readiness retries transient timeouts within the existing
  polling budget. Two intended reds/two controls preceded the one-line repair; persistent failure
  and child-exit diagnostics remain. See ui-readiness-checkpoint.md.

All corresponding source/test/fixture/API-documentation slices have independent approval. The
independent final closure verifier checked the raw gate, all current source paths, owned cleanup,
item-specific red/green proof and acceptance mapping and returned PASS. T-116/T-117 and T-175–T-178
are now CLOSED; tasks6.2–6.4,2.12 and2.14 are checked. Overall progress is44/121;77 tasks remain.
The raw earlier focused-run migration manifests and single-agent startup log were purged before
parent archival; they are not claimed retained. Root19's fresh migration cleanup evidence and
passing browser suite supply current proof without reconstructing old artifacts.

## Remaining scope

The full goal remains ACTIVE. This checkpoint does not prove MP-07 atomic task claims, MP-05/08
posting/effect recovery, complete human/external-agent journeys or the remaining Core/Vision work.
The three credential skips are not real Court/provider proof. Q-M1/Q-M2/Q-M3 and real-provider
credentials/budget remain unresolved; Q-M4 remains poster-specified acceptance windows with
preserved existing cutoffs. No OpenSpec change archival or full-goal completion is justified here.

Next: after the closure record and allowed Git checkpoint, install and execute the independently
reviewed first T-122 approve/dispute race proof. Its remaining acceptance/cancellation/deadline,
replay/authority/migration matrix still precedes T-123 implementation.
