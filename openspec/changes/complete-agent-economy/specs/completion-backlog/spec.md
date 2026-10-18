# Spec Delta

## ADDED Requirements

### Requirement: MP-01 Isolated runtime and baseline
The completion backlog SHALL retain the paired proof and implementation obligations for MP-01. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-110 Write acceptance tests for isolated runtime and baseline
- **WHEN** the test ticket for MP-01 is selected
- **THEN** the acceptance proof demonstrates the gap: Two disposable economy runs and an unrelated sentinel demonstrate any shared-port, shared-data or teardown interference, with the current gate failure recorded.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

#### Scenario: T-111 Deliver isolated runtime and baseline
- **WHEN** T-110 is red-proven and the prerequisites are satisfied: none
- **THEN** Independent test runs and teardown preserve each other and unrelated processes; required prerequisites and actual full-gate exit status are recorded against a frozen baseline.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

### Requirement: MP-02 Service and browser authority
The completion backlog SHALL retain the paired proof and implementation obligations for MP-02. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-112 Write acceptance tests for service and browser authority
- **WHEN** the test ticket for MP-02 is selected
- **THEN** the acceptance proof demonstrates the gap: Unsigned or wrong-domain persistence requests, malicious rendered content and off-origin operator actions demonstrate the current unauthorized effects.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit 0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

#### Scenario: T-113 Deliver service and browser authority
- **WHEN** T-112 is red-proven and the prerequisites are satisfied: MP-01, MP-03
- **THEN** Only authorized domain callers reach persistence; stored untrusted strings remain inert on every consuming page; Host/Origin and token failures make no data or event changes.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit 0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

### Requirement: MP-03 Durable identity and token validation
The completion backlog SHALL retain the paired proof and implementation obligations for MP-03. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-114 Write acceptance tests for durable identity and token validation
- **WHEN** the test ticket for MP-03 is selected
- **THEN** the acceptance proof demonstrates the gap: Concurrent provisioning, partial key material and malformed or inconsistent token fields reproduce identity or validation failures.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

#### Scenario: T-115 Deliver durable identity and token validation
- **WHEN** T-114 is red-proven and the prerequisites are satisfied: MP-01
- **THEN** Concurrent initializers retain one consistent identity, damaged keys fail without replacement, and all verifiers reject invalid token shapes/freshness/algorithms/action audiences without server crashes.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

### Requirement: MP-04 Financial operation receipts
The completion backlog SHALL retain the paired proof and implementation obligations for MP-04. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-116 Write acceptance tests for financial operation receipts
- **WHEN** the test ticket for MP-04 is selected
- **THEN** the acceptance proof demonstrates the gap: Lost responses after committed release/split and public lock replay after settlement expose duplicated or nonconvergent financial commands.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED 2026-10-17 — Own genuine replay/identity/history red proof, green coverage, independent review and root19 exit 0 verified. See evidence/mp04-receipts/completion-audit.md and evidence/root19-checkpoint.md in the active change; evidence limitations remain explicit.

#### Scenario: T-117 Deliver financial operation receipts
- **WHEN** T-116 is red-proven and the prerequisites are satisfied: MP-02, MP-06
- **THEN** Exact Bank-operation replay returns the original receipt and ledger/event identities; changed content conflicts; concurrent opposing commands have one financial winner without duplicated movement.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED 2026-10-17 — Native/historical receipts, consumers, public opposing-settlement proof and documentation are implemented and independently approved. Root19 passed with932 unchanged source/config hashes and19 owned stack groups absent. See the active change's evidence/mp04-receipts/completion-audit.md and evidence/root19-checkpoint.md; lifecycle claims/recovery remain MP-07/08.

### Requirement: MP-05 Recoverable task publication
The completion backlog SHALL retain the paired proof and implementation obligations for MP-05. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-118 Write acceptance tests for recoverable task publication
- **WHEN** the test ticket for MP-05 is selected
- **THEN** the acceptance proof demonstrates the gap: Failures before and after acknowledged or ambiguous publication, including failed compensation, expose orphaned escrow or incorrect refunds.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-119 Deliver recoverable task publication
- **WHEN** T-118 is red-proven and the prerequisites are satisfied: MP-04, MP-07
- **THEN** Recovery produces one funded published task or one documented refund; the same posting identity cannot create a duplicate job or refund an already published matching task.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-01 Read-only CI checks
CI SHALL reject formatting and lint failures without modifying source files. Explicit formatting commands SHALL remain available separately. Implementation T-174 depends on red-proven test ticket T-173; both retain the universal closure gate.

#### Scenario: T-173 Write acceptance tests for read-only CI formatting
- **WHEN** verbose or quiet service CI receives deliberately malformed source in an isolated disposable project
- **THEN** the acceptance proof demonstrates the existing auto-format mutation and requires a nonzero result with byte-identical source instead
- **AND** the exact command, before/after source hashes and failure are recorded before implementation
- **Status:** CLOSED — Own red proof, green acceptance and independent review are recorded; root13 exited 0 with 875 unchanged source/config hashes. See evidence/root13-checkpoint.md in the active change.

#### Scenario: T-174 Keep CI formatting checks read-only
- **WHEN** T-173 is red-proven
- **THEN** every service CI path uses the existing read-only style/format check, malformed source fails without rewriting it, and explicit code-format still performs requested formatting
- **AND** project structure validation and fresh isolated root CI pass with identical source hashes before and after
- **Status:** CLOSED — Own red proof, green acceptance and independent review are recorded; root13 exited 0 with 875 unchanged source/config hashes. See evidence/root13-checkpoint.md in the active change.

### Requirement: MP-06 Schema and monetary integrity
The completion backlog SHALL retain the paired proof and implementation obligations for MP-06. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-120 Write acceptance tests for schema and monetary integrity
- **WHEN** the test ticket for MP-06 is selected
- **THEN** the acceptance proof demonstrates the gap: Wrong escrow payer, inconsistent initial issuance, boolean/overflow values, partial schemas and invalid lifetime escrow uniqueness expose authoritative integrity gaps.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED — Own 12 intended failures/6 controls and subsequent targeted proof are retained; independent review and root14 exit 0 with 886 unchanged source/config hashes satisfy this test ticket's closure gate. T-121's additional concurrent-lock/settlement and process-death proofs were subsequently gated at root15. See evidence/root14-checkpoint.md and evidence/root15-checkpoint.md in the active change.

#### Scenario: T-121 Deliver schema and monetary integrity
- **WHEN** T-120 is red-proven and the prerequisites are satisfied: MP-01
- **THEN** A verified restorable backup precedes real migration; supported schemas migrate safely and unknown schemas fail readiness; all money stays nonnegative integer; authorized issuance minus recorded burn equals spendable balances plus locked escrow; invalid commands and replay create no duplicate ledger/event/receipt legs.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED — Backup-bound migration, schema/data readiness, integer money guards, historical diagnostics, actual process-death recovery and authoritative maximum-bound concurrency are implemented and independently approved. Root15 exited 0 with 888 unchanged source/config hashes. See evidence/root15-checkpoint.md in the active change; durable settlement receipts and operational restored-runtime recovery remain their separate MP-04/MP-21 gates.

### Requirement: MP-07 Atomic task transitions
The completion backlog SHALL retain the paired proof and implementation obligations for MP-07. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-122 Write acceptance tests for atomic task transitions
- **WHEN** the test ticket for MP-07 is selected
- **THEN** the acceptance proof demonstrates the gap: Controlled conflicting acceptance/cancellation, submission/expiry and approval/dispute histories expose incompatible successful transitions.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED — Genuine multi-instance atomic/deadline/bid/window reds, serial-history proofs and the fresh Root22 gate are independently verified. Fixture/setup failures are separately classified. [Closure evidence](../../evidence/mp07-transitions/completion-audit.md).

#### Scenario: T-123 Deliver atomic task transitions
- **WHEN** T-122 is red-proven and the prerequisites are satisfied: MP-06
- **THEN** Each concurrent history has one valid serial outcome; losing operations return explicit conflicts with no contradictory side effects; cutoff equality and acceptance eligibility are consistent.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED — All eleven native transitions, claim-before-effect coordination, authoritative receipt/Court evidence, exact replay, selected acceptance windows, migration/audit and public producer rollout are implemented and independently verified in Root22. General posting/effect/adjudication recovery remains in its later packages. [Closure evidence](../../evidence/mp07-transitions/completion-audit.md).

### Requirement: MP-08 Pending financial effect recovery
The completion backlog SHALL retain the paired proof and implementation obligations for MP-08. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-124 Write acceptance tests for pending financial effect recovery
- **WHEN** the test ticket for MP-08 is selected
- **THEN** the acceptance proof demonstrates the gap: A terminal task with a failed or ambiguously acknowledged payout/refund remains unfinished without reads or after process restart.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-125 Deliver pending financial effect recovery
- **WHEN** T-124 is red-proven and the prerequisites are satisfied: MP-04, MP-07
- **THEN** Pending financial/task effects are recovered regardless of business status, with one receipt, bounded retries and visible persistent failures; this package does not claim Court rebuttal-delivery completion.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-09 Safe immutable artifacts
The completion backlog SHALL retain the paired proof and implementation obligations for MP-09. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-126 Write acceptance tests for safe immutable artifacts
- **WHEN** the test ticket for MP-09 is selected
- **THEN** the acceptance proof demonstrates the gap: Hostile filenames, concurrent quotas, state races, failed metadata/file publication and missing poster input paths demonstrate unsafe or incomplete evidence handling.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-127 Deliver safe immutable artifacts
- **WHEN** T-126 is red-proven and the prerequisites are satisfied: MP-07; Q-M1 before policy closure
- **THEN** Supported inputs and submissions have immutable attributable manifests and exact retrievable bytes; no outside-root writes or quota/state bypass occurs; interrupted uploads reconcile; authorized users can download safely under the accepted artifact policy.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-10 Recoverable immutable adjudication
The completion backlog SHALL retain the paired proof and implementation obligations for MP-10. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-128 Write acceptance tests for recoverable immutable adjudication
- **WHEN** the test ticket for MP-10 is selected
- **THEN** the acceptance proof demonstrates the gap: Concurrent ruling triggers, process death and changed model outputs on retry expose conflicting decisions; mismatched claim context and lost rebuttal acknowledgments are rejected or stranded incorrectly.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-129 Deliver recoverable immutable adjudication
- **WHEN** T-128 is red-proven and the prerequisites are satisfied: MP-04, MP-07, MP-08, MP-09, MP-11
- **THEN** One fenced adjudication owns immutable evidence and a persisted decision before delivery; retries do not rejudge; all economic records agree; mismatched task/party/escrow/version causes no writes; identical rebuttal replay preserves the original timestamp and changed content conflicts.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-11 Admissible judge evidence and output
The completion backlog SHALL retain the paired proof and implementation obligations for MP-11. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-130 Write acceptance tests for admissible judge evidence and output
- **WHEN** the test ticket for MP-11 is selected
- **THEN** the acceptance proof demonstrates the gap: Truncated or unsupported decisive evidence, injected instructions, malformed model output and out-of-bound provider operation expose unsupported rulings.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-131 Deliver admissible judge evidence and output
- **WHEN** T-130 is red-proven and the prerequisites are satisfied: MP-09; Q-M1 and Q-M3 before contract closure
- **THEN** Judge evidence retains hashes/provenance/media types and explicit completeness; untrusted data cannot redefine the accepted rubric; validated outputs and provider time/context/cost limits apply; unsupported evidence blocks automatic judgment without silent mock fallback.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-12 Sealed public projections
The completion backlog SHALL retain the paired proof and implementation obligations for MP-12. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-132 Write acceptance tests for sealed public projections
- **WHEN** the test ticket for MP-12 is selected
- **THEN** the acceptance proof demonstrates the gap: An unrelated observer can retrieve open bids or sealed feedback content through UI/API/event history or streaming paths.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** CLOSED — Own red proof, green acceptance and independent review are recorded; root13 exited 0 with 875 unchanged source/config hashes. See evidence/root13-checkpoint.md in the active change.

#### Scenario: T-133 Deliver sealed public projections
- **WHEN** T-132 is red-proven and the prerequisites are satisfied: MP-02
- **THEN** Only authorized poster views reveal open bid amounts/identities, while all public projections and aggregates honor sealing and later reveal consistently, including reconnect and history.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** CLOSED — Own red proof, green acceptance and independent review are recorded; root13 exited 0 with 875 unchanged source/config hashes. See evidence/root13-checkpoint.md in the active change.

### Requirement: MP-13 Authorized durable feedback
The completion backlog SHALL retain the paired proof and implementation obligations for MP-13. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-134 Write acceptance tests for authorized durable feedback
- **WHEN** the test ticket for MP-13 is selected
- **THEN** the acceptance proof demonstrates the gap: Unrelated or premature feedback, wrong recipients/categories, duplicate Court deliveries and one-sided timeout visibility expose authorization or projection inconsistencies.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-135 Deliver authorized durable feedback
- **WHEN** T-134 is red-proven and the prerequisites are satisfied: MP-07, MP-10, MP-12
- **THEN** Only eligible task parties and finalized-ruling sources create appropriately directed feedback; exact replay is stable; reveal is durable and every intended API/UI/score view agrees after restart.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-14 Independent quality scores
The completion backlog SHALL retain the paired proof and implementation obligations for MP-14. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-136 Write acceptance tests for independent quality scores
- **WHEN** the test ticket for MP-14 is selected
- **THEN** the acceptance proof demonstrates the gap: Absent or divergent standing, payout-confounded dimensions and duplicated/hidden observations demonstrate the missing accepted score contract.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-137 Deliver independent quality scores
- **WHEN** T-136 is red-proven and the prerequisites are satisfied: MP-11, MP-13; Q-M3
- **THEN** A versioned service-owned query returns separate specification/delivery dimensions, observation counts and provenance; unmeasured standing is distinguishable; sealed or duplicate evidence cannot influence scores incorrectly; all consumers agree.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-15 Complete human participation
The completion backlog SHALL retain the paired proof and implementation obligations for MP-15. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-138 Write acceptance tests for complete human participation
- **WHEN** the test ticket for MP-15 is selected
- **THEN** the acceptance proof demonstrates the gap: Real browser actions cannot finish the operator poster and worker journeys, inspect artifacts/payments, or recover actionable errors and ambiguous writes.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-139 Deliver complete human participation
- **WHEN** T-138 is red-proven and the prerequisites are satisfied: MP-04, MP-05, MP-09, MP-12, MP-13, MP-19; MP-14 for scores
- **THEN** Through the UI the operator posts/works as itself, sees funding and real bids, transfers supported artifacts, accepts/submits/approves/disputes/rebuts/cancels/gives feedback when entitled, and sees matching receipts with no duplicate effects or impersonation.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-16 Independent external agent participation
The completion backlog SHALL retain the paired proof and implementation obligations for MP-16. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-140 Write acceptance tests for independent external agent participation
- **WHEN** the test ticket for MP-16 is selected
- **THEN** the acceptance proof demonstrates the gap: An unlisted participant on a separate host/network namespace exposes any reliance on server roster, shared files, gateway/operator access or privileged keys.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-141 Deliver independent external agent participation
- **WHEN** T-140 is red-proven and the prerequisites are satisfied: MP-02, MP-03, MP-04, MP-05, MP-09
- **THEN** Using only its own persistent keys and documented agent-facing URLs, an external agent completes work, reads its payment and posts a funded job; any initial grant is administrator-invoked and platform-signed; zero-balance working remains allowed.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-17 Worker commitment recovery
The completion backlog SHALL retain the paired proof and implementation obligations for MP-17. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-142 Write acceptance tests for worker commitment recovery
- **WHEN** the test ticket for MP-17 is selected
- **THEN** the acceptance proof demonstrates the gap: Restart after bid, acceptance, upload, submission or dispute abandons work, duplicates actions or records unearned income.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-143 Deliver worker commitment recovery
- **WHEN** T-142 is red-proven and the prerequisites are satisfied: MP-07, MP-08, MP-09, MP-10, MP-16
- **THEN** The restarted worker discovers and resumes owned commitments through one correct terminal outcome, preserves artifact/rebuttal identities and records earnings from receipts within explicit execution budgets.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-18 Feeder acceptance and review recovery
The completion backlog SHALL retain the paired proof and implementation obligations for MP-18. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-144 Write acceptance tests for feeder acceptance and review recovery
- **WHEN** the test ticket for MP-18 is selected
- **THEN** the acceptance proof demonstrates the gap: Restart loses review evidence, or cutoff/tie/selection failures prevent timely and deterministic autonomous progress.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-145 Deliver feeder acceptance and review recovery
- **WHEN** T-144 is red-proven and the prerequisites are satisfied: MP-05, MP-07, MP-13, MP-14, MP-16
- **THEN** The feeder resumes posted work and durable review evidence, honors configured window/quorum and lowest-bid/quality tie rules, and approves correct or disputes incorrect work without memory-loss timeout approval or duplicate posting.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-19 Accurate observatory and event delivery
The completion backlog SHALL retain the paired proof and implementation obligations for MP-19. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-146 Write acceptance tests for accurate observatory and event delivery
- **WHEN** the test ticket for MP-19 is selected
- **THEN** the acceptance proof demonstrates the gap: Known outcome fixtures and reconnect races expose inaccurate bid/payment values, completion/employment denominators or duplicated/missed events.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-147 Deliver accurate observatory and event delivery
- **WHEN** T-146 is red-proven and the prerequisites are satisfied: MP-08, MP-12, MP-13
- **THEN** Every view uses explicit consistent outcome and monetary definitions, correct zero-bid/distinct-worker counts, durable feedback visibility and a bounded resumable deduplicated event view.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-20 Strict service contracts and responsive task processing
The completion backlog SHALL retain the paired proof and implementation obligations for MP-20. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-148 Write acceptance tests for strict service contracts and responsive task processing
- **WHEN** the test ticket for MP-20 is selected
- **THEN** the acceptance proof demonstrates the gap: Malformed success bodies and conflated errors appear as plausible empty/zero results, and a slow persistence request stalls unrelated Task Board requests or deadline progress.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-149 Deliver strict service contracts and responsive task processing
- **WHEN** T-148 is red-proven and the prerequisites are satisfied: producer contracts from MP-04, MP-07, MP-10
- **THEN** Invalid upstream contracts fail visibly and distinctly; production/fake contracts agree; Task Board health, unrelated requests and deadline progress meet the documented bounds during slow dependency failures.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-21 Operational readiness and restoration
The completion backlog SHALL retain the paired proof and implementation obligations for MP-21. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-150 Write acceptance tests for operational readiness and restoration
- **WHEN** the test ticket for MP-21 is selected
- **THEN** the acceptance proof demonstrates the gap: Missing services, wrong port ownership, incomplete schema/keys/assets and interrupted restoration expose false readiness or unrecoverable work.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-151 Deliver operational readiness and restoration
- **WHEN** T-150 is red-proven and the prerequisites are satisfied: MP-01, MP-03, MP-06, MP-08, MP-10
- **THEN** Startup/teardown honor exact process ownership and readiness; restore recovers matching keys/evidence/money and pending work once; migrations preserve original backups; diagnostics and recovery operations are actionable and do not expose secrets.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-22 Honest comprehensive verification
The completion backlog SHALL retain the paired proof and implementation obligations for MP-22. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-152 Write acceptance tests for honest comprehensive verification
- **WHEN** the test ticket for MP-22 is selected
- **THEN** the acceptance proof demonstrates the gap: Known faults, early command failures, order leakage or source-mutating checks escape or undermine the current quality gate.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-153 Deliver honest comprehensive verification
- **WHEN** T-152 is red-proven and the prerequisites are satisfied: MP-01 through MP-21; MP-23 through MP-26 are consumers
- **THEN** The isolated root quality gate fails on injected known faults, propagates setup failures, preserves source and unrelated data, and passes all required suites with deterministic concurrency/restart and browser coverage without weakened assertions or skips.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-23 Complete platform journeys
The completion backlog SHALL retain the paired proof and implementation obligations for MP-23. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-154 Write acceptance tests for complete platform journeys
- **WHEN** the test ticket for MP-23 is selected
- **THEN** the acceptance proof demonstrates the gap: Public-interface journeys in the release matrix expose missing integration without direct database transitions or fake browser writes.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-155 Deliver complete platform journeys
- **WHEN** T-154 is red-proven and the prerequisites are satisfied: MP-15 through MP-22 and MP-25
- **THEN** Fresh human/external-agent poster and worker journeys, approval/dispute/timeout/cancellation, restarts and lost responses pass with matching receipts/evidence/events/UI state, the evaluated real Court profile and a fresh full-root gate.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-24 Measured non-math economic behavior
The completion backlog SHALL retain the paired proof and implementation obligations for MP-24. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-156 Write acceptance tests for measured non-math economic behavior
- **WHEN** the test ticket for MP-24 is selected
- **THEN** the acceptance proof demonstrates the gap: The non-math input/output and multi-round examples expose unsupported participation or claims of price formation/specialization that the active policy does not justify.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-157 Deliver measured non-math economic behavior
- **WHEN** T-156 is red-proven and the prerequisites are satisfied: MP-04, MP-14, MP-16, MP-17, MP-18; Q-M2 and Q-M3
- **THEN** Real public APIs drive reproducible non-math work and measured adaptation/quality/cost; pricing matches the accepted fixed-reward or post-v1 contract, with conserved rounding/refunds and protected outstanding tasks; failed hypotheses are reported honestly.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-25 Empirical real judge readiness
The completion backlog SHALL retain the paired proof and implementation obligations for MP-25. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-158 Write acceptance tests for empirical real judge readiness
- **WHEN** the test ticket for MP-25 is selected
- **THEN** the acceptance proof demonstrates the gap: The baseline real panel is evaluated against predeclared precise/vague, compliant/noncompliant and malicious-evidence cases; fixed mock output is excluded from quality proof.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-159 Deliver empirical real judge readiness
- **WHEN** T-158 is red-proven and the prerequisites are satisfied: MP-10, MP-11, MP-14
- **THEN** A configured three-judge real profile satisfies predeclared quality/robustness and operating-budget criteria with traceable immutable evidence, valid output, recoverable provider failure and independently verified retry stability.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: MP-26 Consistent specifications and evidence
The completion backlog SHALL retain the paired proof and implementation obligations for MP-26. Implementation SHALL wait for its red proof and the prerequisite packages or decisions. Both tickets SHALL remain OPEN until their own proof and the universal closure gate are satisfied.

#### Scenario: T-160 Write acceptance tests for consistent specifications and evidence
- **WHEN** the test ticket for MP-26 is selected
- **THEN** the acceptance proof demonstrates the gap: A behavior/spec/test/ticket and command/link audit exposes stale claims or missing current proof.
- **AND** the exact baseline, command and observed failing assertion are recorded before implementation
- **Status:** OPEN — Implementation and item-specific proof pending.

#### Scenario: T-161 Deliver consistent specifications and evidence
- **WHEN** T-160 is red-proven and the prerequisites are satisfied: MP-23; MP-24 and MP-25 for full Vision closure
- **THEN** Current specifications, guides and canonical tickets map every required outcome to fresh proof or an explicit open/ratified-deferral state; all accepted policies are reflected; final claims match the selected Core/Vision gate and office-hour history rules.
- **AND** the corresponding product spec, master-plan release gates and fresh isolated root `just ci-quiet` proof agree before closure
- **Status:** OPEN — Implementation and item-specific proof pending.

### Requirement: Explicit completion policy decisions
The completion backlog SHALL track unresolved evidence/privacy, post-v1 pricing and quality-aggregation decisions with an accountable project owner, accepted examples and dependent-task gates. Existing local single-operator, manual issuance and fixed-reward v1 decisions SHALL remain effective until explicitly superseded.

#### Scenario: T-162 Decide Q-M1 evidence admissibility and artifact privacy
- **WHEN** the accountable project owner resolves Q-M1
- **THEN** the accepted decision defines supported formats/limits, immutable provenance, participant and bidder access, retention, external-provider disclosure and unsupported-evidence outcomes before MP-09 policy and MP-11 close
- **AND** the accepted record is linked from the affected specifications and implementation proofs without closing those implementations prematurely
- **Status:** OPEN — Explicit owner decision pending; no default option is accepted.

#### Scenario: T-163 Decide Q-M2 post-v1 price-forming economics
- **WHEN** the accountable project owner resolves Q-M2
- **THEN** the accepted decision defines whether post-v1 price formation is enabled, including excess-refund timing, rounding and existing-contract treatment before MP-24 pricing claims close; fixed-reward Core remains valid
- **AND** the accepted record is linked from the affected specifications and implementation proofs without closing those implementations prematurely
- **Status:** OPEN — Explicit owner decision pending; no default option is accepted.

#### Scenario: T-164 Decide Q-M3 independent quality aggregation
- **WHEN** the accountable project owner resolves Q-M3
- **THEN** the accepted versioned contract defines separate quality dimensions, payout separation, source weighting, no-history treatment and scored examples before MP-11 and MP-14 close
- **AND** the accepted record is linked from the affected specifications and implementation proofs without closing those implementations prematurely
- **Status:** OPEN — Explicit owner decision pending; no default option is accepted.

### Requirement: MP-01 Discovered deadline precision baseline
The completion backlog SHALL track the independently repairable deadline truncation exposed by the isolated root baseline as T-169/T-170. This prerequisite SHALL preserve configured durations without weakening existing expiry assertions. It SHALL NOT close or bypass MP-07's schema, atomic-transition or concurrency obligations.

#### Scenario: T-169 Prove full deadline duration at fractional timestamps
- **WHEN** a task phase starts at a timestamp with fractional seconds and has a configured duration
- **THEN** deterministic tests demonstrate that the current calculation can expire it before the full duration has elapsed
- **AND** just-before, exact-cutoff and whole-second compatibility cases are recorded before implementation
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

#### Scenario: T-170 Preserve deadline precision in the baseline
- **WHEN** T-169 is red-proven
- **THEN** the shared deadline calculation preserves the full configured duration and existing expiry, refund and event assertions remain intact
- **AND** focused tests, Task Board CI and a fresh isolated root gate pass before closure
- **AND** broader atomic state-transition requirements remain open under T-122/T-123
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

### Requirement: MP-00 Reported UI configuration fixture repair
The completion backlog SHALL prioritize reproducing and repairing the reported invalid UI test fixtures while retaining required production configuration and all existing health/configuration/logging assertions. T-166 SHALL depend on T-165 red proof; fixture-only corrections SHALL follow the recorded test-spec reconciliation.

#### Scenario: T-165 Prove UI fixtures omit required operator configuration
- **WHEN** the reported configuration, health-setup and successful-initialization cases are reproduced against the current baseline
- **THEN** the four missing-operator-section failures are recorded separately from their intended assertions
- **AND** an otherwise complete extra-field case proves its intended validation error and all required-section negative cases remain covered
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

#### Scenario: T-166 Repair explicit valid UI fixtures
- **WHEN** T-165 is red-proven and the narrow fixture-only reconciliation is recorded
- **THEN** health, valid configuration and successful initialization tests reach and pass their unchanged behavior assertions with explicit isolated operator settings
- **AND** missing required sections still fail, partial-section behavior remains separately covered and fixture state is cleaned after failures
- **AND** UI and isolated root quality gates pass before closure
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

### Requirement: MP-00 Reported UI browser prerequisite repair
The completion backlog SHALL prioritize the missing Chromium failure separately from configuration and JavaScript behavior. T-168 SHALL depend on T-167 proof of the missing or mismatched browser prerequisite; no trend assertion or browser test SHALL be skipped to restore a green gate.

#### Scenario: T-167 Prove browser prerequisite failure
- **WHEN** the three reported trend cases are run with an isolated missing or mismatched browser cache
- **THEN** the failure is identified as browser startup before the shipped JavaScript assertions
- **AND** local unit-CI prerequisite ordering and the installed browser revision are recorded
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

#### Scenario: T-168 Prepare and verify matching Chromium before unit CI
- **WHEN** T-167 is red-proven and explicit browser preparation has run for the installed package version
- **THEN** early local and hosted preflight verifies the matching browser and all increasing/decreasing/stable assertions run against the shipped JavaScript and pass
- **AND** absent or stale browser revisions cause an actionable nonzero prerequisite failure without skips, fake success or weakened assertions
- **AND** the complete UI unit suite, UI quality gate and isolated root quality gate pass before closure
- **Status:** CLOSED 2026-10-15 — Item-specific red/green proof, independent review and isolated root gate (attempt 7, exit 0) verified. See openspec/changes/complete-agent-economy/evidence/foundation-checkpoint.md.

### Requirement: MP-01 Audit-discovered development dependency repair
The completion backlog SHALL retain a separate fail-first obligation for the dependency finding surfaced during MP-02 verification. No vulnerability suppression or untested dependency override satisfies this baseline repair.

#### Scenario: T-171 Prove the development MCP advisory blocks CI
- **WHEN** the service dependency audit runs with Semgrep's pinned mcp 1.29.0
- **THEN** the gate reports GHSA-84m7-p3x7-pcfv and exits nonzero
- **AND** official dependency/advisory evidence identifies the affected environments, patched version and compatibility constraints before any override
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit 0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

#### Scenario: T-172 Repair the affected development dependency
- **WHEN** T-171 is red-proven and a compatible patched dependency is verified
- **THEN** affected service environments use the patched package through reproducible uv-generated locks, their configured Semgrep rules and audits pass, and no new vulnerability ignore is added
- **AND** any override of an upstream exact pin is explicit, limited to the development tool and documented with compatibility evidence and a removal condition
- **AND** a fresh isolated root `just ci-quiet` exits zero before closure
- **Status:** CLOSED 2026-10-15 — Item-specific evidence, independent review, root attempt10 exit 0 and unchanged809-input snapshot verified; see openspec/changes/complete-agent-economy/evidence/mp02-checkpoint.md, including browser baseline provenance.

### Requirement: MP-01 Quarterly navigation race repair
The quarterly report SHALL preserve every accepted Prev/Next click in order while earlier report requests are pending. Implementation T-176 depends on red-proven test ticket T-175. Existing navigation assertions and timeouts SHALL remain unchanged; the universal root closure gate applies to both tickets.

#### Scenario: T-175 Write acceptance tests for concurrent quarterly navigation
- **WHEN** several Prev/Next clicks occur before the first requested report completes
- **THEN** deterministic browser tests demonstrate the lost-navigation gap and assert the final requested quarter across year boundaries, including a no-data response
- **AND** report failures remain visible and a later navigation can recover
- **Status:** CLOSED 2026-10-17 — Two genuine reds/one control, focused green and independent review verified; root19 passed the complete298-case browser suite and full root gate. See the active change's evidence/quarter-navigation-checkpoint.md and evidence/root19-checkpoint.md.

#### Scenario: T-176 Preserve each quarterly navigation click
- **WHEN** T-175 is red-proven
- **THEN** each accepted click shifts from the preceding navigation result in order, without duplicate requests caused by stale quarter state
- **AND** the existing browser suite, new regression proofs, independent review and fresh isolated root CI pass without weakened assertions or increased timeouts
- **Status:** CLOSED 2026-10-17 — Ordered navigation is implemented without changing original assertions/timeouts; focused proof, independent review and root19 full UI/root gate pass. See the active change's evidence/quarter-navigation-checkpoint.md and evidence/root19-checkpoint.md.


### Requirement: MP-01 UI fixture readiness honors its existing budget
The controlled UI browser fixture SHALL tolerate transient connection and request timeouts until its existing readiness deadline, while preserving immediate child-exit failure and terminal timeout diagnostics. T-178 depends on red-proven T-177; neither item closes without the universal root gate.

#### Scenario: T-177 Write acceptance tests for delayed UI readiness
- **WHEN** the fixture's listening socket accepts a health connection before the child can answer HTTP
- **THEN** a deterministic test demonstrates the premature ReadTimeout failure and requires a later successful health response to establish readiness
- **AND** persistent timeouts and child exit remain explicit failures with server-log diagnostics
- **Status:** CLOSED 2026-10-17 — Two genuine readiness reds/two controls, four unit and ten focused browser passes, independent review and root19 full UI/root gate verified. See the active change's evidence/ui-readiness-checkpoint.md and evidence/root19-checkpoint.md.

#### Scenario: T-178 Preserve bounded UI readiness polling
- **WHEN** T-177 is red-proven
- **THEN** transient connection and timeout failures remain within the existing readiness polling budget without increasing timeout values or suppressing terminal failure
- **AND** focused readiness tests, unchanged single-agent browser cases, full UI suite, independent review and a fresh root gate pass
- **Status:** CLOSED 2026-10-17 — Transient timeout handling preserves the existing readiness budget and terminal diagnostics. Focused proof, independent review and root19 full298-case UI/root gate pass. See the active change's evidence/ui-readiness-checkpoint.md and evidence/root19-checkpoint.md.

### Requirement: Standalone integration log isolation
Every service test fixture that starts logging SHALL keep log writes inside its owned temporary resource directory, including when the root runtime supervisor is not used. Config-only fixtures that never start logging are not runtime-write evidence.

#### Scenario: T-179 Prove standalone integration log escape
- **WHEN** the audited Gateway, Task Board, Identity, Central Bank, Reputation and Court startup fixtures construct their service configurations
- **THEN** tests prove whether each log directory is absolute, owned by the temporary test root and distinct from the other service directory before any service starts
- **AND** ordinary runtime logs are not mutated by the proof itself
- **Status:** CLOSED — All seventeen cause-specific configuration reds are archived. The formerly missing six outputs were recovered byte-for-byte from original execution records and independently verified. Root22 supplies the required fresh full gate. [Closure evidence](../../evidence/mp07-transitions/completion-audit.md).

#### Scenario: T-180 Isolate standalone integration log writes
- **WHEN** T-179 is red-proven
- **THEN** all audited startup fixture configurations route logs to explicit service-specific directories inside their temporary root
- **AND** live regression execution leaves ordinary runtime log bytes unchanged, while existing logs are preserved without deletion or rewriting
- **AND** item-specific evidence and a fresh isolated root quality gate pass before closure
- **Status:** CLOSED — Audited startup logs use owned temporary paths. Root22 passes with all202 ordinary logs byte-identical, no additions/removals, and verified owned-process cleanup. Earlier ordinary log contents were preserved. [Closure evidence](../../evidence/mp07-transitions/completion-audit.md).

### Requirement: Durable caller-owned native task intents
The built-in feeder and worker SHALL retain an immutable native mutation intent before transport and replay that intent after interruption. This independent MP-07 producer prerequisite consumes the reviewed native command contract and completed MP-01/MP-03 isolation/key foundations; it does not close the broader MP-05/MP-16/MP-17/MP-18 recovery obligations. Runtime files SHALL be caller-owned and SHALL NOT use the private Gateway or weaken direct-SQL restrictions.

#### Scenario: T-181 Prove native runtime intent loss and durable replay boundaries
- **WHEN** a feeder or worker prepares a bid, acceptance, submission, approval or dispute and transport or the process is interrupted
- **THEN** test evidence demonstrates whether operation identity, source version, exact semantic terms and response-validation context survive before any retry
- **AND** exact stored replay precedes current-task eligibility or decision recomputation, while foreign ownership, malformed responses and unconfirmed target state do not imply completion
- **AND** real-filesystem and process tests cover owner/profile/key/URL binding, private file admission, concurrent identity leases and atomic publication faults without touching ordinary runtime data
- **Status:** CLOSED — Genuine durability/ACK/caller/startup regressions are preserved separately from capability/setup reds. Owner binding, filesystem faults and all ten actual SIGKILL/restart histories pass in Root22; independent final proof audit approves. [Closure evidence](../../evidence/mp07-transitions/completion-audit.md).

#### Scenario: T-182 Persist and replay native runtime task intents
- **WHEN** T-181 is red-proven and the MP-01/MP-03 foundations and reviewed native producer fields are available
- **THEN** one caller-owned identity lease and strict atomic JSON journal retain the original operation ID, version, public arguments and validation context before any mutation transport
- **AND** known pending work resumes with exact replay, changed slot terms fail locally, definite rejection never silently allocates a replacement, and storage failures prevent mutation transport
- **AND** the feeder publishes its explicitly configured acceptance window without conflating it with its selection delay or introducing a platform-wide default
- **AND** item-specific restart/fault evidence, independent review and fresh isolated root CI pass before closure, while posting identity, asset/execution/oracle/rebuttal recovery and authoritative discovery remain mapped to their original full packages
- **Status:** CLOSED — Owner-bound atomic journals, exact public SDK replay, typed ACK validation, leased startup/cleanup and explicit feeder windows are implemented and independently verified. Root22 passes all required gates. Posting, execution/assets/oracle/rebuttal/discovery recovery, distributed leases and external rollback detection remain outside this bounded package. [Closure evidence](../../evidence/mp07-transitions/completion-audit.md).
