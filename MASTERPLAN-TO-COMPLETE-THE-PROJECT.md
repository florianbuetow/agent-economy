# Finish the Agent Task Economy

Updated 2026-10-23. This is the active implementation plan. It replaces the previous audit-heavy master plan and the handoff document. [FEATURE-COMPLETENESS.md](FEATURE-COMPLETENESS.md) records what is implemented, what is missing, and the basic tests still needed. Existing OpenSpec ticket IDs remain the tracker; completed work stays completed.

## Objective

Finish a working economy in which humans and independent agents can post work with inputs, bid, deliver files, receive the agreed payment, resolve disputes, build separate specification and delivery reputations, and use those reputations in subsequent rounds. Complete both the Core product and the non-math economic Vision.

Prioritize usable features and core business logic. Finish each feature through its public API and relevant UI/agent caller. A helper, schema, mock, or isolated unit test does not make a feature complete.

## Scope decisions

- **No migration or backward compatibility work.** The owner confirmed that existing data is disposable test data and there is no user data to preserve. Initialize the current schema directly on fresh databases. Do not build V5-to-V6 conversion, historical asset inventories, legacy continuation, mixed historical manifests, cutover journals, or historical receipt adoption for this release.
- Existing migration-related work is not a prerequisite for finishing features. Preserve established source changes unless removing them is necessary for the current implementation; do not keep developing unused compatibility paths. New migration experiments from the resumed session are archived outside active source/tests.
- Use fresh disposable databases for verification. Resetting test data is acceptable when needed; do not gratuitously delete keys, stop unrelated processes, or destroy the ordinary running environment.
- Keep the seven services, SQLite Gateway, local deployment, existing frontend, and public signed APIs. No architecture replacement, new deployment platform, or generic workflow framework.
- Keep operational restart recovery for work created by the finished product. Losing an HTTP response or restarting a service must not duplicate payment or abandon a committed job. This is product behavior, not historical-data migration.
- Retain basic backup/restore of the current format as an operational feature after the main business flows work. It must not block uploads, settlement, Court, or the UI.
- **No excessive testing expansion.** Reuse existing coverage. Add a focused failing acceptance test for missing behavior, implement it, and run affected tests. Use a small number of meaningful integration journeys and critical money/authorization/retry checks. Do not build exhaustive fault matrices, recursive review gates, test-oracle refinement projects, or new verification frameworks.
- Run component checks at completed feature milestones and isolated root `just ci-quiet` before declaring the project complete. Existing tests remain useful regression coverage; this revision does not authorize weakening assertions, hiding failures, or deleting established tests to obtain green CI.

## Business rules to implement

1. The poster explicitly selects bidding, acceptance, execution, and review windows. One accepted bid defines the worker and immutable terms/inputs.
2. The accepted bid sets the payment price. Lock the posted budget; return unused budget at final settlement. Approval pays the accepted price. A Court award pays `floor(accepted_price * percentage / 100)` and returns the rest of the original escrow to the poster. Preserve integer conservation and replay-safe settlement.
3. Ambiguous specifications favor the worker. Judges receive the complete accepted specification, inputs, outputs, claim, and rebuttal. Never silently truncate evidence or turn decoding failures into fabricated text.
4. Initial evidence formats are text, source, JSON, and CSV. Instructions/files are public while the job is nonterminal. Job-page downloads close immediately at completion, cancellation, or expiry. Archive after more than three days; do not purge. Court access must still support adjudication.
5. Court providers are configurable OpenAI-compatible local or remote services. Use three judges, one persisted final ruling, and retry delivery without re-judging. No appeals.
6. Specification quality and delivery quality are separate from payout. Average eligible tasks equally; the final Court finding replaces peer feedback for that task. Only approved, auto-approved, or Court-ruled tasks with submissions count. Cancellations and missed deadlines do not become invented quality zeroes.
7. Quality scores and counts are public. Source-level details are restricted to the subject, relevant task participants, and operator. Unrated agents are explicitly unrated. The feeder uses the supported delivery-score contract, not a private UI formula.
8. Sealed bids and feedback remain sealed until their established reveal condition. External agents use their own keys and public APIs, never the Gateway or platform private key.

Only genuinely unresolved product decisions should interrupt implementation. Q-M1's exact evidence validation/size-limit behavior remains to be settled when the implementation reaches it. Q-M2 pricing and the owner's Q-M3 aggregation, eligibility, and visibility choices are recorded in [owner decisions](openspec/changes/complete-agent-economy/evidence/policy-options/owner-decisions-2026-10-22.md); do not ask those questions again.

## Implementation order

### 1. Complete the actual work-and-payment flow

**Primary work: MP-09 plus the accepted-price portion of MP-24.**

- Activate the current artifact/publication schema for fresh installations.
- Connect bounded streaming uploads to durable reservations and byte publication.
- Let a poster attach inputs before publishing; freeze the input manifest with the accepted terms.
- Let the assigned worker upload multiple outputs and submit one immutable manifest.
- Provide exact-byte downloads, public nonterminal access, Court access, terminal cutoff, and archive behavior.
- Apply accepted-bid pricing to approval, timeout, and dispute settlement; refund the unused budget at final settlement.
- Wire the same flow through the public SDK and minimal UI controls as each API becomes available. Do not leave the browser/agent caller until a separate long-running backend project is finished.

**Done:** a real poster publishes an input-bearing task, a worker bids below budget, delivers multiple files, and approval produces the correct worker payment and poster refund. Downloaded bytes match uploaded bytes. A wrong actor cannot upload or submit.

### 2. Finish dispute resolution and reputation

**MP-11 → MP-10 → MP-13 → MP-14.**

- Feed complete immutable evidence to the configured judge panel; validate structured responses and report unsupported or unavailable evidence explicitly.
- Persist the ruling before settlement/reputation delivery. Support rebuttal and resume pending delivery after restart without asking judges again.
- Authorize feedback using actual task parties and outcomes. Separate peer feedback from Court provenance; make mutual/timeout reveal durable.
- Expose the selected independent quality scores, counts, and authorized provenance. Use the same service-owned contract in UI and feeder.

**Done:** a worker rebuts a dispute; one Court ruling produces the correct split and both quality dimensions. One retry changes neither payment nor observation counts. Sealed feedback cannot influence visible standing prematurely.

### 3. Complete human and autonomous participation

**MP-15, MP-16, MP-17, MP-18; integrate MP-19/MP-20 as needed.**

- Finish human poster controls: balance/funding status, inputs, real bid amounts, accept, review/download, approve, dispute, cancel, and feedback.
- Finish human worker controls: bid, input download, output upload, submit, rebuttal, and payment status.
- Provide a runnable external-agent example using public APIs and its own keys. Document the existing administrator grant procedure; demonstrate earn-then-post.
- Persist worker commitments/results and feeder review evidence so ordinary restarts resume work rather than duplicate or forget it.
- Use accepted pricing and supported quality scores in feeder selection; show explicit errors when dependencies fail.
- Correct visible payment/status/metric inconsistencies and SSE reconnect gaps. Replace permissive client fallbacks and blocking request boundaries where they prevent these journeys.

**Done:** browser poster and browser worker journeys both work against real services. An independent agent earns and then posts work. The autonomous feeder/worker flow completes good work and disputes bad work without a demo script or human intervention, including an ordinary restart.

### 4. Demonstrate the economic Vision and finish operations

**MP-24, MP-25, MP-21, then MP-22/MP-23/MP-26 closure.**

- Add a public-API non-math classification workload with inputs and multiple outputs.
- Run repeated rounds and report observed selection, quality, cost, and specialization/adaptation. Do not hard-code winners or claim adaptation from a fixed demonstration.
- Evaluate the actual configured three-judge profile on a small declared set of precise/vague, good/bad, and injected-evidence examples. Mock tests do not prove real judge quality.
- Finish actionable startup/readiness, current-format backup/restore, and pending-work diagnostics.
- Update API/user documentation to match shipped behavior, resolve concrete remaining failures, and run final integration/E2E/root CI.

**Done:** the Core journeys work, the non-math rounds and real judge results support the stated Vision, operational instructions are reproducible, and the final isolated root `just ci-quiet` exits 0. Do not mark the project complete from a component pass.

## Working method and tests

- Work from the current tree. Reuse the substantial implemented lifecycle, signing, posting, and financial recovery code.
- Use native subagents for independent implementation lanes with explicit file ownership. Use Luna for bounded searches, Sol for implementation, and stronger reasoning only for a concrete difficult issue.
- Keep one main integration lane moving. Delegate only work that can be connected to a public feature, or a necessary independent prerequisite.
- Add the missing basic tests listed in `FEATURE-COMPLETENESS.md`; avoid duplicating existing tests. For money, authority, immutable evidence, and retry identity, retain a direct negative or replay check because those are business invariants.
- Fix observed failures. Do not repeatedly rewrite plans or expand speculative review/testing scope.
- Report delivered behavior, the next functional gap, and actual verification results. Keep tickets open until their corresponding behavior and applicable gates pass.
- Preserve the weekday 08:00–18:00 Europe/Amsterdam commit/push prohibition and existing hooks. Check actual time immediately before either action.

## Definition of project completion

All missing features in the inventory are implemented and connected; the basic acceptance tests and relevant existing regressions pass; human, external-agent, unattended-economy, dispute, and non-math journeys run through public interfaces; payment and quality semantics match the owner decisions; documentation matches the implementation; and final isolated root CI passes. Historical-data migration is not part of this definition.
