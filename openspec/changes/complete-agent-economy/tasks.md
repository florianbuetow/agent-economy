# Tasks

> **Owner scope update — 2026-10-23:** Follow `MASTERPLAN-TO-COMPLETE-THE-PROJECT.md` and `FEATURE-COMPLETENESS.md` for feature-first execution. Existing data is disposable test data: old-schema/data migration, historical asset conversion and backward compatibility are out of scope. Initialize fresh databases directly. Reuse existing tests and add only missing basic feature checks; no exhaustive fault matrices or repeated planning/review gates. Older migration/testing obligations below are historical where they conflict with this update. Completed work stays complete; no feature is closed by this scope change.


Checkboxes record verified task progress. Canonical implementation and decision tickets remain OPEN until their required closure evidence is complete. Complete only the relevant numbered checkbox after its stated verification. Stable T-IDs identify the canonical backlog obligations; MP IDs group the master plan. Red proof precedes each implementation pair. Supplied CI output is historical evidence, not a substitute for fresh reproduction. Policy tasks require an accepted owner decision; do not silently choose a policy.

Work in the existing refactor workspace. The latest owner instruction permits commits and pushes throughout Saturday and Sunday and outside 08:00–18:00 on weekdays, in Europe/Amsterdam time. Both remain prohibited Monday through Friday from 08:00 inclusive until 18:00 exclusive. This overrides the initial goal's all-hours prohibition for commits and pushes only. Branch switches, merges, cherry-picks and rebases remain prohibited. Preserve unrelated changes and verify the local time before any permitted Git write; read-only inspection is allowed. Full-root CI must use MP-01 isolation.

## 1. MP-00: Repair the reported UI CI baseline

Prerequisites: First actionable work. Full-stack verification waits for MP-01 isolation.

- [x] 1.1 [T-165] Reproduce the four missing-user_agent configuration/setup paths in the existing health, config and lifespan tests; verify the recorded failure occurs before each original assertion and retain the supplied 19-test log separately.
- [x] 1.2 [T-165] Add a cause-specific proof that an otherwise complete configuration rejects an extra field; verify the proof cannot pass from a missing section and retain all six required-section negative cases.
- [x] 1.3 [T-166] Record the narrow fixture-only reconciliation in the UI test spec, then add explicit isolated user_agent path/handle values and controlled dependencies to the valid fixtures; verify the four original behaviors pass without production-default changes or assertion weakening.
- [x] 1.4 [T-166] Make fixture environment/settings/state cleanup run after failure as well as success; verify focused tests pass alone and in changed order and that absent required sections still fail.
- [x] 1.5 [T-167] Reproduce missing and mismatched Chromium revisions with an isolated PLAYWRIGHT_BROWSERS_PATH; verify failure is browser startup before any trend assertion and record the package/browser version relationship.
- [x] 1.6 [T-168] Document an explicit uv run playwright install chromium preparation step and add browser launch preflight before local browser-dependent unit tests; verify an empty cache fails early with actionable nonzero output and prepared cache launches successfully.
- [x] 1.7 [T-168] Align hosted setup and shared recipe structure with the same browser prerequisite; verify the increasing/decreasing/stable tests execute real shared.js with original assertions, the full UI unit suite passes without skips, and just test-project-structure still passes.
- [x] 1.8 [T-166] Reconcile the same missing-user_agent error in UI integration/conftest.py and the separate integration health fixture, with explicit temporary configuration, stubbed registration and failure-safe cleanup; verify the full UI integration suite reaches and passes its existing assertions without weakening them.
- [x] 1.9 [T-166/T-168] Run UI component CI and, after MP-01 isolation, root just ci-quiet; verify exit codes/logs before closing either pair and keep failures open rather than claiming a planning fix.

## 2. MP-01: Isolate verification and freeze the baseline

Prerequisites: None; MP-00 focused proofs can precede this group.

- [x] 2.1 [T-110] Add sentinel-process and dual-run acceptance proofs for shared ports/data/teardown; verify they fail for the current unsafe isolation behavior without resetting a live economy.
- [x] 2.2 [T-111] Provide per-run ports, configs, data/assets/keys and owned process handles; verify two disposable stacks register different identities and teardown independently while the sentinel survives.
- [x] 2.3 [T-111] Make required tooling and baseline command exits observable; verify a missing prerequisite or failed startup is nonzero and source files remain unchanged by checks.
- [x] 2.4 [T-111] Document isolated commands and capture the actual root-gate baseline; verify the run is reproducible and preserve logs/schema/config identity for later packages.
- [x] 2.5 [T-169] Reproduce the baseline deadline truncation with deterministic fractional timestamps, including just-before and exact-cutoff cases; verify full configured bidding/execution/review durations are not preserved by the current calculation, while whole-second cases remain covered.
- [x] 2.6 [T-170] Preserve fractional timestamp precision in the shared deadline calculation and persisted/event cutoff; verify the new boundary proofs and unchanged expiry/bid assertions, then run Task Board CI. Keep full MP-07 atomic transitions and package dependencies open; this narrowly independent baseline repair does not satisfy them.


- [x] 2.7 [T-171] Capture the newly surfaced MCP dependency-audit failure and identify Semgrep's exact pin across all seven services; retain official advisory and compatibility evidence before choosing a patch.
- [x] 2.8 [T-172] Apply the smallest verified compatible patched dependency through uv-generated locks; document any upstream-pin override/removal condition and verify all affected Semgrep/audit gates plus isolated root CI with no new advisory ignore.
- [x] 2.9 [T-173] Reproduce CI changing a deliberately malformed disposable source file through its auto-format step; verify both verbose and quiet service CI should fail without changing source, while explicit code-format remains available.
- [x] 2.10 [T-174] Remove automatic formatting from service CI and use the existing read-only code-style gate; verify all service recipe paths reject malformed source without mutation, project structure remains valid, and fresh root CI has identical source hashes before and after.

- [x] 2.11 [T-175] Add deterministic rapid-click quarterly navigation proofs, including year boundaries, no-data responses and recovery after a request failure; record the lost-navigation red before implementation. Two intended reds/one control independently reproduced; see evidence/quarter-navigation-checkpoint.md. Root19 passed the full gate; see evidence/root19-checkpoint.md.
- [x] 2.12 [T-176] Preserve navigation clicks while report requests are pending; verify the new proofs and unchanged existing browser assertions, independent review and a fresh isolated root gate.

- [x] 2.13 [T-177] Prove that a transient health ReadTimeout aborts controlled UI fixture setup before its existing readiness budget; retain persistent-timeout and child-exit failure controls with diagnostics. Parent reproduced two intended reds/two controls; independent review approved. See evidence/ui-readiness-checkpoint.md; root19 passed the full gate; see evidence/root19-checkpoint.md.
- [x] 2.14 [T-178] Keep transient timeouts inside the existing readiness polling loop without increasing timeout values; verify focused and unchanged browser cases, full UI, review and fresh root CI.

- [x] 2.15 [T-179] Prove that audited standalone integration/unit startup log directories escape their temporary roots without starting services or writing ordinary logs; distinguish config-only inputs and manual ASGI helpers.
- [x] 2.16 [T-180] Configure distinct owned temporary log directories for all audited service startup fixtures; verify boundary proofs and live regression runs with unchanged ordinary log hashes, preserving existing contents; require fresh root CI before closure.

Baseline repairs verified on 2026-10-22 (independent of MP-03). T-183–T-190 have
item-specific proof/review and the fresh root37981 gate. See
`evidence/mp05-posting/root-ci-2026-10-22.md` and the resume evidence index:

- [x] 2.17 [T-183] Preserve the current isolated Gateway audit failure, verify official advisory/fix evidence and inventory every affected environment for certifi and Pillow; distinguish environment/setup errors from vulnerable-version failures.
- [x] 2.18 [T-184] Update only verified affected dependency constraints and uv-generated locks, with no new ignores; run affected gates and fresh isolated root CI before closure.
- [x] 2.19 [T-185] Preserve exact installed-path audits and lock metadata for remaining agents/tools/shared-library dependency findings; verify upstream corrected-version compatibility, separating temporary audit overlays from project dependencies.
- [x] 2.20 [T-186] Apply targeted corrections for those verified non-service findings; require clean exact-path audits, affected component gates and fresh isolated root CI without new ignores.
- [x] 2.21 [T-187] Reproduce fast command exit before owned process-group/session capture with deterministic scheduling, retaining foreign-group and cleanup-retry controls.
- [x] 2.22 [T-188] Repair command identity capture without PID-only signaling authority; verify actual exit codes, safe teardown and fresh isolated root CI.
- [x] 2.23 [T-189] Capture the missing commit/push hook baseline and test weekday boundaries, weekends and clock failures without attempting either Git action.
- [x] 2.24 [T-190] Restore and install the Europe/Amsterdam office-hours hooks, preserving any existing hooks; verify both guards and fresh isolated root CI before closure.

Additional prerequisite surfaced during MP-08 component CI (2026-10-22):

- [x] 2.25 [T-191] Preserve the failing installed OpenTelemetry audit, verify official advisory and compatible family metadata, and inventory the seven Semgrep consumers.
- [x] 2.26 [T-192] Apply only the verified Semgrep-scoped dependency correction with uv-generated locks; require Semgrep smoke/rule execution, clean audits and a fresh isolated root gate without exclusions.

- [x] 2.27 [T-193] Retain the R7 filelock audit failure and verify upstream fixed versions and consumer bounds; preserve the compatibility exception rationale.
- [x] 2.28 [T-194] Apply targeted secure filelock constraints and uv-generated locks; verify consumers, audits and fresh root CI without exclusions.

## 3. MP-03: Durable keys and one token contract

Prerequisites: MP-01.

- [x] 3.1 [T-114] Write controlled concurrent provisioning, partial/mismatched-key and malformed/expired-token cases; verify intended red failures across local and Identity verifiers.
- [x] 3.2 [T-115] Implement exclusive atomic key publication, restrictive private permissions and pair validation; verify one ID under concurrent cold bootstrap and explicit failure without replacing damaged registered keys.
- [x] 3.3 [T-115] Specify and implement typed required token fields, finite lifetime/skew and action/resource checks; verify all consumer conformance cases including non-object headers and exact expiry boundaries.
- [x] 3.4 [T-115] Update signing/bootstrap integration guidance and configuration requirements; verify documented callers use the canonical profile and affected component/consumer tests pass.

## 4. MP-02: Gateway and browser authority

Prerequisites: MP-01 and MP-03.

- [x] 4.1 [T-112] Add red proofs for unsigned/wrong-domain gateway access, each stored-XSS sink and off-origin bodyless writes; verify zero-authority actors can currently reach the identified gaps.
- [x] 4.2 [T-113] Provision distinct service capabilities and enforce gateway command/read authorization; verify legitimate lifecycle calls succeed and wrong/expired/tampered callers cause no writes/events.
- [x] 4.3 [T-113] Replace unsafe browser interpolation and restrict active-content previews; verify hostile name/title/comment/summary values remain inert on landing, task, agent, quarterly and live-feed views.
- [x] 4.4 [T-113] Enforce Host/Origin and non-loopback-auth guards; verify off-origin forms fail before operator action, then document the trust/access matrix and run affected auth/browser contracts.
- [x] 4.5 [T-112/T-113] Prove and repair frontend fallback containment: encoded traversal, absolute paths and symlinked page/index/asset roots must not render files outside the configured web root; preserve regular page and SPA fallback behavior and verify the fresh root gate.

## 5. MP-06: Safe schema and money invariants

Prerequisites: MP-01; coordinate authoritative command schemas with MP-02.

- [x] 5.1 [T-120] Add direct authoritative tests for wrong payer, mismatched issuance, booleans, overflow, partial schemas and lifetime escrow uniqueness; verify each intended red failure and no-write assertions.
- [x] 5.2 [T-121] Build and test pre-migration consistent backup/restore before enabling migrations; verify a missing or invalid backup blocks real-data migration and restoration preserves original money/key/asset identity.
- [x] 5.3 [T-121] Implement versioned transactional migrations and complete schema readiness; verify fresh/known old layouts converge while partial/unknown schemas fail before readiness.
- [x] 5.4 [T-121] Enforce authoritative ownership, issuance consistency, integer bounds and guarded arithmetic; verify issuance minus burn equals balances plus locked escrow and no REAL-valued or duplicate ledger/event/receipt legs arise.
- [x] 5.5 [T-121] Audit/reconcile historical duplicate escrows before lifetime constraints and document migration/recovery commands; verify migration fixtures and Bank/gateway contract gates pass.

## 6. MP-04: Idempotent financial receipts

Prerequisites: MP-02 and MP-06.

MP-04 is verified complete at root19. Native/history persistence, consumers, public Bank race
proof and documentation are independently approved. Root19 passed with932 unchanged source/config
hashes,203 cross/runtime,298 UI browser and94 live-agent cases; all19 owned stack groups are absent.
See `evidence/mp04-receipts/completion-audit.md` and `evidence/root19-checkpoint.md` for exact proof
and limitations. The full Core/Vision goal and later lifecycle/recovery packages remain open.

- [x] 6.1 [T-116] Inject response loss after Bank commits and replay locks after settlement; verify red behavior for exact versus conflicting requests without depending on task lifecycle claims. Bounded eight-case red proof independently reproduced and approved; see evidence/mp04-receipts/checkpoint.md. T-116/T-117 close with the complete item proof and root19 gate; see evidence/mp04-receipts/completion-audit.md.
- [x] 6.2 [T-117] Add stable operation identity, payload hash and durable result receipt to financial commands; verify exact replay returns original identities and changed recipients/amounts/percentages conflict.
- [x] 6.3 [T-117] Update Bank API and shared clients to return authoritative receipts; verify opposing direct Bank commands have one winner and zero/partial/full splits conserve money.
- [x] 6.4 [T-117] Document receipt/replay/error contracts and run Bank/gateway contracts; verify lifecycle consistency is reserved for MP-07/08 rather than falsely proved here.

## 7. MP-07: Atomic task transitions

Prerequisites: MP-06.

MP-07 is verified complete at Root22. Native schema/commands, all eleven lifecycle
histories, poster-selected windows, producer rollout and durable caller replay are
independently approved. Root22 passed every phase with 1,059 unchanged source/config
files and 202 unchanged ordinary logs, including 65 Board integration, 211 cross/runtime,
298 browser and 104 live-agent cases. All ten final SIGKILL histories passed in that gate.
The three existing provider-credential skips do not prove the later real Court profile.
See `evidence/root22-checkpoint.md` and `evidence/mp07-transitions/completion-audit.md`.
The final independent verifier approved tasks 7.1–7.6 plus log-isolation tasks 2.15–2.16.
The full Core/Vision goal and later recovery/evidence/economic packages remain open.

- [x] 7.1 [T-122] Write barrier-based multi-instance accept/cancel, submit/expire, approve/dispute and competing-acceptance tests; verify the current check-then-act failures.
- [x] 7.2 [T-123] Require authoritative state/version/actor/cutoff predicates and claim transitions before external effects; verify losers have conflicts without contradictory claim, asset, assignment or money effects.
- [x] 7.3 [T-123] Implement the owner-selected per-poster acceptance window with exact deadline equality and Q-16 feeder interaction; verify early poster choice, closed bidding, immediate zero-bid expiry, selected-window cutoff and immutable published/accepted terms without a silent duration.
- [x] 7.4 [T-123] Document the revised state/operation API and run real-gateway state histories; verify every history matches a valid serial outcome and semantic event.

The runtime producer needs a durable caller intent before delivery; an in-memory UUID cache cannot
satisfy the accepted restart requirement. T-181/T-182 isolate this prerequisite under MP-07 using the
already reviewed producer contract and MP-01/03 foundations. They do not substitute for the complete
MP-05/16/17/18 packages or their prerequisite gates.

- [x] 7.5 [T-181] Capture runtime intent-loss and process/filesystem fault proofs for known bid/accept/submit/review slots; verify frozen replay before fresh eligibility, strict owner binding, and no false completion from current state.
- [x] 7.6 [T-182] Implement caller-owned atomic JSON intent persistence and exact native replay plus explicit feeder acceptance-window input; verify restart/response-loss/local-write-failure cases without private Gateway access, SQL-rule exceptions or broader recovery closure claims.

## 8. MP-05: Recoverable posting

Prerequisites: MP-04 and MP-07.

Current 2026-10-22 checkpoint supersedes the historical progress below: UI,
feeder, SDK and durable demo rollout are implemented and reviewed. Creation,
late-request, legacy refund and demo restart proofs pass their bounded checks;
the retired posting API is removed. Supplied demo scenarios and completed replay
pass in an isolated mock-Court runtime. Fresh root CI handle54816 and independent
item-level completion audit are pending. No checkbox or ticket closes yet; see
`evidence/mp05-posting/resume-2026-10-22/checkpoint.md` for exact evidence and gaps.

Reviewed contract: `docs/plans/2026-10-18-mp05-publication-contract.md`.
Initial/corrected baseline proof is under `evidence/mp05-posting/`. Native
publication, legacy refund reconciliation, and the Board recovery loop are now
implemented with bounded green evidence in `native-rollout-progress/`; the real
lock-commit/process-death/restart proof is retained in `process-native-green/`.
Feeder posting now uses a leased durable source/intent journal and status-first
replay; its bounded journal review is approved and parent agents component CI
passes (889 tests, three unchanged optional credential skips). UI/demo caller
rollout remains underway. Posting-journal backup/restore auditing now passes
focused recovery/migration checks and independent review; fresh component/root
gates, real feeder restart proof, remaining fault cases and demo rollout remain
pending. Full E2E last reports101pass/3fail/3errors, with UIproduction posting
under implementation and the stale unattended fixture now repaired. Current evidence is
indexed in `evidence/mp05-posting/feeder-and-ci-progress/`. These bounded results
do not close the items.

- [x] 8.1 [T-118] Inject lock-success/insert-failure, post-commit response loss and failed-compensation histories; verify orphaned or incorrectly refunded work on the current baseline.
- [x] 8.2 [T-119] Persist stable posting intent and reconcile ambiguous publication before refund; verify one correctly funded public task or one documented refund after each restart.
- [x] 8.3 [T-119] Add payload-bound create replay to SDK/proxy and document pending publication; verify duplicate submits return one task and changed terms under one ID conflict.
- [x] 8.4 [T-119] Run creation fault and conservation integration cases; verify incomplete tasks are not discoverable as open and no orphan escrow remains.

MP-05 closure: independent item review and root37981 pass, with1180 unchanged
source/config files and365 recorded process groups absent. See
`evidence/mp05-posting/completion-audit.md`. Later Core/Vision work remains open.

## 9. MP-08: Recover terminal financial effects

Prerequisites: MP-04 and MP-07. Court handoffs close in MP-10.

- [x] 9.1 [T-124] Write no-reader and restart proofs for terminal tasks with failed/ambiguous payout or refund; verify the existing pending-work gap.
- [x] 9.2 [T-125] Persist and schedule financial/task effects independently of business status with bounded retries and fenced ownership; verify one final receipt and cleared pending state after recovery.
- [x] 9.3 [T-125] Expose pending-age/failure diagnostics and safe retry operations; verify one persistent failure does not starve other work and no unpaid task appears paid.
- [x] 9.4 [T-125] Document the recovery contract and exercise kill/response-loss histories; verify generic operation primitives are available to Court without claiming rebuttal recovery in this package.

## 10. MP-09: Safe files and immutable manifests

Prerequisites: MP-07. Storage safety is independent; policy closure requires Q-M1/T-162.

- [ ] 10.1 [T-126] Add hostile-filename, quota/state-race and file/metadata interruption proofs plus missing poster-input proof; verify red outcomes inside disposable storage.
- [ ] 10.2 [T-127] Use generated storage paths, bounded streaming and atomic quota/state reservations; verify no traversal/symlink escape, outside-root write or concurrent limit bypass.
- [ ] 10.3 [T-127] Add immutable input/submission manifests and recoverable byte/metadata publication; verify hashes, filenames, media types and exact bytes survive upload/download/restart.
- [ ] 10.4 [T-127] After T-162 acceptance, apply the artifact access/retention contract and safe UI/SDK downloads; verify the authorized-access matrix and document supported evidence formats/limits.

## 11. Decision gates Q-M1 and Q-M3

The owner's 2026-10-22 Q-M1 choices are recorded in
`evidence/policy-options/owner-decisions-2026-10-22.md`: public instructions/files,
configurable OpenAI-compatible Court providers, initial text/source/JSON/CSV,
immediate terminal-job page download closure and archive after more than three
days. Existing no-appeal finality remains settled. These choices supersede draft
recommendations but do not close T-162 or supply the remaining evidence limits
and implementation proof. The owner has now selected Q-M3 flat task weighting, Court override, completed-submission eligibility, and public aggregates with restricted provenance; implementation remains open.

Prerequisites: Prepare evidence now; dependent policy tasks remain OPEN until accepted decisions exist.

- [ ] 11.1 [T-162] Prepare the supported-format/access/retention/external-provider matrix and alternatives for the project owner; verify the accepted ADR covers bidder access, unsupported evidence and concrete failure outcomes before MP-09 policy/MP-11 closure.
- [ ] 11.2 [T-164] Prepare independent quality-output and aggregation examples for the project owner; verify the accepted versioned contract covers no history, sample counts, source weighting, precise-poor/vague-adequate work, duplicates and malicious feedback before MP-11/MP-14 closure.

## 12. MP-11: Judge evidence, prompts and output

Prerequisites: MP-09 and accepted T-162/T-164; precedes durable adjudication MP-10.

- [ ] 12.1 [T-130] Write red fixtures for missing/truncated decisive content, unsupported binaries, injected instructions and malformed/provider-out-of-bound responses; verify each is classified correctly.
- [ ] 12.2 [T-131] Implement the versioned complete manifest and approved extraction envelope; verify provenance/hashes/omission markers and explicit blocked judgment for unavailable evidence.
- [ ] 12.3 [T-131] Implement structured untrusted-input boundaries, the ambiguity rubric and validated independent quality/payout output; verify deterministic adversarial and malformed-output cases without silent fallback.
- [ ] 12.4 [T-131] Enforce the configured three-judge real profile and context/time/cost bounds; verify provider failures remain recoverable and document profile/version promotion inputs for MP-25.

## 13. MP-10: One durable Court decision

Prerequisites: MP-04, MP-07, MP-08, MP-09 and MP-11.

- [ ] 13.1 [T-128] Write simultaneous-trigger, real-kill, nondeterministic retry, mismatched-claim and lost-rebuttal-acknowledgment cases; verify current divergent/stuck outcomes.
- [ ] 13.2 [T-129] Validate authoritative claim parties/escrow/version and atomically lease one attempt; verify mismatches cause no writes and a losing trigger cannot alter the winner.
- [ ] 13.3 [T-129] Persist immutable evidence/votes/verdict before delivery and resume unfinished steps with fencing; verify retries never rejudge and all four service projections/receipts agree.
- [ ] 13.4 [T-129] Make exact rebuttal and ruling replay converge with original timestamps/results; verify changed content conflicts and startup reclaims abandoned judging work safely.
- [ ] 13.5 [T-129] Document attempt/delivery states and run multi-process failure histories; verify no deleted committed ruling, duplicate feedback or permanently stranded judging state.

## 14. MP-12: Sealed public projections

Prerequisites: MP-02; can proceed in parallel with Court work.

- [x] 14.1 [T-132] Add actor-by-state tests for public UI bid data and sealed rating leakage via history/SSE/aggregates; verify the current disclosure paths.
- [x] 14.2 [T-133] Centralize public versus authorized projections and safe event serialization; verify only entitled posters see open bid identities/amounts and no rating-dependent data leaks early.
- [x] 14.3 [T-133] Document field visibility and test reconnect/cache/history contexts; verify views reveal only at the accepted conditions and retain correct ordinary observation.

## 15. MP-13: Authorized feedback and durable reveal

Prerequisites: MP-07, MP-10 and MP-12.

- [ ] 15.1 [T-134] Add unrelated-party, wrong-direction, premature outcome, duplicate Court and timeout-restart cases; verify the intended authorization/visibility failures.
- [ ] 15.2 [T-135] Enforce task/source/subject eligibility and payload-bound feedback replay; verify only legitimate parties/finalized rulings affect reputation and error causes remain distinct.
- [ ] 15.3 [T-135] Persist timed and mutual reveal with one event; verify API/UI/metrics consistency after restart and no sealed score influence.
- [ ] 15.4 [T-135] Document source/role migration and reveal contracts; verify production-gateway tests preserve the already-atomic counter-feedback behavior.

## 16. MP-14: Independent quality standing

Prerequisites: MP-11, MP-13 and accepted T-164.

- [ ] 16.1 [T-136] Add accepted scoring fixtures for no history, separate dimensions, source weighting, sealing and replay; verify missing/divergent current score behavior.
- [ ] 16.2 [T-137] Implement the versioned service-owned score query with counts/provenance; verify every accepted fixture, no double counting and explicit unmeasured standing.
- [ ] 16.3 [T-137] Move UI and feeder score consumers to the contract; verify identical displayed/selection values and document formula/version migration with examples.

## 17. MP-20: Strict clients and responsive Task Board

Prerequisites: Accepted producer contracts from MP-04, MP-07 and MP-10; design shared models earlier with producers.

- [ ] 17.1 [T-148] Add malformed-success and distinct-error contract cases plus slow-gateway Task Board responsiveness proof; verify fake zero/empty success and blocking behavior fail visibly.
- [ ] 17.2 [T-149] Implement strict response/error adapters and a safe async/offload boundary; verify production/fake parity and bounded unrelated-request/deadline lag during timeouts.
- [ ] 17.3 [T-149] Remove only inventoried dead/private-client paths and update consumer guidance; verify all affected client/component tests and architecture boundaries after each bounded refactor.

## 18. MP-16: Supported external integration

Prerequisites: MP-02, MP-03, MP-04, MP-05 and MP-09.

- [ ] 18.1 [T-140] Run a new participant from a separate host/network namespace with no server filesystem/roster/Gateway/operator/notary access; verify current undocumented/private prerequisites.
- [ ] 18.2 [T-141] Expose and document the existing public SDK/config/key bootstrap path and all lifecycle/artifact/rebuttal/receipt methods; verify a clean external example uses no private signing workaround.
- [ ] 18.3 [T-141] Document administrator-invoked platform-signed grants and protected agent-facing connectivity; verify zero-balance working and authorized idempotent grants without operator mint authority.
- [ ] 18.4 [T-141] Run external earn-then-post-then-second-worker completion; verify receipts, restart identity and the published quickstart reproduce the journey.

## 19. MP-17: Worker restart recovery

Prerequisites: MP-07, MP-08, MP-09, MP-10 and MP-16.

- [ ] 19.1 [T-142] Add process restart checkpoints after bid/accept/upload/submit/dispute; verify abandoned commitments and phantom/duplicated earnings on the current runtime.
- [ ] 19.2 [T-143] Implement durable execution checkpoints and authoritative commitment reconciliation; verify every checkpoint resumes without duplicate bids/assets/submissions/rebuttals.
- [ ] 19.3 [T-143] Bound execution and separate outcome/rebuttal tracking from discovery; verify expired/cancelled states and receipt-derived earnings, then document recovery/operator controls.

## 20. MP-18: Feeder restart and selection

Prerequisites: MP-05, MP-07, MP-13, MP-14 and MP-16.

- [ ] 20.1 [T-144] Reproduce lost answer evidence, cutoff conflicts and unstable tie/selection histories; verify known-bad submissions can currently escape intended review after restart.
- [ ] 20.2 [T-145] Persist posted-task/review provenance and reconcile at startup; verify restarted feeder approves good work and disputes bad work without duplicate posting or memory-loss timeout approval.
- [ ] 20.3 [T-145] Implement accepted window/quorum and service-score tie rules with bounded per-task progress; verify lowest amount and deterministic final ties, and document fallback/cutoff behavior.

## 21. MP-19: Truthful metrics and event views

Prerequisites: MP-08, MP-12 and MP-13.

- [ ] 21.1 [T-146] Add hand-calculated all-outcome/zero-bid/distinct-worker fixtures and snapshot/reconnect race proofs; verify incorrect totals and duplicate/missing events.
- [ ] 21.2 [T-147] Implement explicit metric definitions and accurate bid/reward/receipt distinctions across views; verify every fixture against authoritative money/status records.
- [ ] 21.3 [T-147] Implement high-watermark snapshot, resume cursor, deduplication and bounded feed/backfill; verify history/live reconnect delivers unique ordered events without resetting inspected state.
- [ ] 21.4 [T-147] Document metric windows/denominators and event retention; verify charts, tables, agent profiles and reports use the same semantics.

## 22. MP-15: Complete human UI journeys

Prerequisites: MP-04, MP-05, MP-09, MP-12, MP-13 and MP-19; MP-14 for score display.

- [ ] 22.1 [T-138] Add real-browser red journeys for missing funding status, inputs, true offers, downloads, cancel/feedback, errors and human-as-worker actions; verify failures reach real service seams.
- [ ] 22.2 [T-139] Implement operator funding-status, post/input and offer/accept views; verify exact bid amounts versus fixed reward and clear insufficient-funds/conflict errors.
- [ ] 22.3 [T-139] Implement safe review/download, approval/dispute/cancel/feedback and operator worker bid/upload/submit/rebuttal flows; verify entitlement and exact bytes/receipts without impersonation.
- [ ] 22.4 [T-139] Implement durable write retry/result display and accessible pending/error states; verify double-click, lost-response and refresh cases have one effect and actionable status.
- [ ] 22.5 [T-139] Document both human journeys and run live browser happy/dispute/worker paths; verify financial/UI agreement without direct database state manipulation.

## 23. MP-21: Startup, backup and restore operations

Prerequisites: MP-01, MP-03, MP-06, MP-08 and MP-10.

- [ ] 23.1 [T-150] Add wrong-port/missing-dependency, failed-readiness and inconsistent-restore cases; verify current false success and stranded-work histories.
- [ ] 23.2 [T-151] Implement owned-process readiness/startup/teardown with explicit config limits; verify failed dependencies return nonzero and unrelated processes survive.
- [ ] 23.3 [T-151] Implement complete DB/key/asset restore and reconciliation reports; verify a disputed/pending-settlement snapshot continues once with conserved money and original evidence.
- [ ] 23.4 [T-151] Document disk-full, permissions, failed fresh-initialization and stuck-effect operations; verify runbook drills and redacted correlated diagnostics in isolated runs.

## 24. MP-22: Full verification infrastructure

Prerequisites: MP-01 through MP-21; subsequent integration/evaluation consumers are not prerequisites.

- [ ] 24.1 [T-152] Add deliberate-known-fault, startup-exit, order-isolation and source-mutation gate probes; verify the current gate misses or mishandles each intended case.
- [ ] 24.2 [T-153] Implement deterministic clocks/barriers, real-gateway contracts and process-kill/response-loss harnesses; verify injected known defects fail meaningful guards.
- [ ] 24.3 [T-153] Make CI checks read-only, prerequisite/exit handling strict and browser fixtures isolated; verify just ci-quiet passes on a clean disposable run without source mutation, weakening or skips.
- [ ] 24.4 [T-153] Update per-spec coverage mapping and CI troubleshooting docs, including current vulnerability-exception review; verify whole-suite varied ordering and retained failure artifacts.

## 25. MP-25: Evaluate the real judge profile

Prerequisites: MP-10, MP-11 and MP-14; corpus may be drafted earlier.

- [ ] 25.1 [T-158] Run the baseline real profile on predeclared precise/vague, good/bad and injected-evidence fixtures within an explicit budget; verify current deficiencies are recorded separately from mock CI.
- [ ] 25.2 [T-159] Implement versioned bounded evaluation/promotion tooling and approved three-judge configuration; verify quality, structured-output, robustness, latency and cost thresholds with traceable evidence.
- [ ] 25.3 [T-159] Document credentials/budget/profile/reproduction and failure triage; verify outages remain pending and retry stability uses the persisted decision rather than model determinism.

## 26. MP-23: Prove complete Core integration

Prerequisites: MP-15 through MP-22 and MP-25; Q-M1/Q-M3 must be accepted.

- [ ] 26.1 [T-154] Encode the Core release journeys through browser and external public APIs; verify baseline failures and prohibit direct DB state transitions in the acceptance lane.
- [ ] 26.2 [T-155] Resolve only exposed integration wiring gaps through their owning packages; verify human/external-agent poster/worker, approve/dispute/cancel/timeout and autonomous flows match receipts/events/evidence.
- [ ] 26.3 [T-155] Repeat with real process restarts and lost acknowledgments; verify the supported real Court profile, browser artifact/error behavior and a fresh isolated root just ci-quiet exit 0.

## 27. Q-M2 and MP-24: Full economic Vision

Pricing implementation starts now from MP-04/MP-07 using the accepted T-163 owner decision; it does not wait for reputation or the non-math experiment. The non-math/adaptation branch uses MP-14, MP-16, MP-17 and MP-18.

- [ ] 27.1 [T-163] Apply the already selected accepted-bid price, final unused-budget refund, and floor-rounded Court share; reconcile the decision/specification with basic examples. No historical-data conversion or repeated owner choice is required.
- [ ] 27.2 [T-156] Add non-math input/multi-output and multi-round measurement proofs; verify missing capability or unsupported market-pricing/specialization claims on the current baseline.
- [ ] 27.3 [T-157] Implement only the accepted economic policy and reusable non-math examples; verify all settlement outcomes conserve value and preserve outstanding accepted terms.
- [ ] 27.4 [T-157] Run reproducible classification/adaptation rounds through real APIs and publish measured outcomes/uncertainty; verify no hard-coded winner or mock result is presented as emergent behavior and update economic docs.

## 28. MP-26: Reconcile final specifications and evidence

Prerequisites: Inventory can begin immediately; closure requires MP-23 and applicable MP-24/MP-25 Vision proof.

- [ ] 28.1 [T-160] Audit behavior/spec/test/ticket and command/link mappings against current evidence; verify stale claims and unsupported closures are explicitly identified.
- [ ] 28.2 [T-161] Reconcile remaining baseline API/test/ADR/backlog differences and preserve historical IDs; verify every required finding maps to passing evidence, open work or a ratified deferral.
- [ ] 28.3 [T-161] Run final cross-document/journey/quality checks and record exact release profile and residual risks; verify no required unchecked task, unresolved blocking decision or unsupported full-CI claim remains.

## Workflow follow-up

- The owner authorized a local checkpoint commit on 2026-10-17 and clarified that weekends are unrestricted. Apply the weekday 08:00–18:00 history restriction; the current commit request does not request a push. A checkpoint is not implementation completion.
- After implementation review, fresh required gates and all applicable decision tasks are complete, use the OpenSpec sync/archive workflow to update the baseline specs and archive the change.
- The old masterplan publication branch remains unused; any later deletion is a separate after-hours action, not part of this preparation.
