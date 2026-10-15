# Proposed completion-backlog additions

Status: PROPOSED / OPEN. These are new requirements for the requested master plan, not evidence of implementation or ticket closure. Merge accepted items into the canonical backlog before execution. T-110–T-161 are paired test/implementation tickets; T-162–T-164 track the three major decisions. Resolve any intervening ID collision before merging.

## ADDED Requirements

### Requirement: Complete the functional platform and economic vision

The platform SHALL satisfy the work packages and acceptance matrix in `MASTERPLAN-TO-COMPLETE-THE-PROJECT.md`. Each implementation ticket SHALL depend on its paired test ticket reaching red-proven status and the package prerequisites listed below. Red-proven unblocks implementation; final ticket closure still requires the universal green root CI gate. Core closure requires Q-M1/T-162 and Q-M3/T-164 decisions plus MP-25 real-judge proof; Q-M2/T-163 is Vision-only.

#### Scenario: T-110 Write acceptance tests for: Isolate runtime and establish the current baseline
- **Status:** OPEN
- **Package:** MP-01
- **WHEN** the current implementation is exercised for isolate runtime and establish the current baseline
- **THEN** new acceptance tests demonstrate the gap: launch two disposable run profiles and an unrelated sentinel listener/process; demonstrate that present fixed-port startup/teardown cannot isolate them. Capture the current full-gate first failure without touching the user's active data.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-111 Isolate runtime and establish the current baseline
- **Status:** OPEN
- **Package:** MP-01
- **Depends on:** T-110 red-proven; package prerequisites: none
- **WHEN** MP-01 is implemented
- **THEN** two concurrent disposable stacks perform separate registrations/tasks and teardown independently; sentinel survives; actual root CI output/exit captured for the reviewed snapshot; no source file autoformat or original data reset is hidden in “verification.”
- **AND** the implementation satisfies: introduce explicit run IDs, per-run data/assets/keys/config paths and port allocation; persist process ownership; make test setup consume those values. Keep the documented normal local ports for ordinary `just start-all`. Preflight tool/browser/model prerequisites and required Python version.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-112 Write acceptance tests for: Enforce persistence authority and the local browser boundary
- **Status:** OPEN
- **Package:** MP-02
- **WHEN** the current implementation is exercised for enforce persistence authority and the local browser boundary
- **THEN** new acceptance tests demonstrate the gap: malicious names/titles/comments/event summaries execute markup in the UI; unsigned and wrong-domain gateway calls can mutate valid fixture records; an off-origin browser request can invoke a bodyless operator action. Check private event/read routes as well as writes.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-113 Enforce persistence authority and the local browser boundary
- **Status:** OPEN
- **Package:** MP-02
- **Depends on:** T-112 red-proven; package prerequisites: MP-01, MP-03
- **WHEN** MP-02 is implemented
- **THEN** malicious stored strings render as inert text on task/agent/landing/quarterly/feed pages; legitimate full lifecycle works; unsigned, tampered, expired, wrong-service and off-origin requests make no writes/events. Operator cannot mint or settle arbitrary escrow. Negative tests cover every domain capability, not just `/bank/credit`.
- **AND** the implementation satisfies: provision distinct service credentials/capabilities; authenticate the full command and restrict each service to its domain routes. Gateway stays private. Ordinary agent credentials are never gateway credentials. Replace implicit shared-key access with explicit allowed delegation for Task Board→Bank/Court and Court→Task Board/Reputation.
- **AND** the implementation satisfies: replace unsafe HTML interpolation with DOM text nodes or a single audited encoding boundary on every page; never render uploaded active content in the operator origin. Add a restrictive CSP and browser XSS regression fixtures for all identified sinks.
- **AND** the implementation satisfies: enforce allowed Host/Origin and CSRF/session protection for browser writes; reject non-loopback exposure without the required configured auth. Preserve local single-operator behavior. Add bounded request/admission limits before inviting untrusted network agents.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-114 Write acceptance tests for: Make identities durable and token validation consistent
- **Status:** OPEN
- **Package:** MP-03
- **WHEN** the current implementation is exercised for make identities durable and token validation consistent
- **THEN** new acceptance tests demonstrate the gap: controlled concurrent first creation yields differing/mismatched keys; a missing key half regenerates identity; malformed header crashes; required expiry/algorithm/type checks differ across verifiers.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-115 Make identities durable and token validation consistent
- **Status:** OPEN
- **Package:** MP-03
- **Depends on:** T-114 red-proven; package prerequisites: MP-01
- **WHEN** MP-03 is implemented
- **THEN** parallel cold bootstrap converges on one ID, corrupt keys fail with actionable errors, all consumers share the malformed/expiry conformance table, and replay protection composes with MP-04 rather than treating TTL as exactly-once execution.
- **AND** the implementation satisfies: exclusive atomic key provisioning; private-file permissions; pair validation; explicit recovery instead of regeneration; public-key canonicalization. Consumers load provisioned credentials instead of racing to create them.
- **AND** the implementation satisfies: a typed JWS profile with required fields, finite lifetime/skew, object/algorithm checks and action/resource audience binding; stamp it consistently in official SDK and platform clients. Make missing signing configuration fail at startup. Preserve only explicitly scoped test fixtures, not a silent legacy production bypass.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-116 Write acceptance tests for: Give financial operations durable replay receipts
- **Status:** OPEN
- **Package:** MP-04
- **WHEN** the current implementation is exercised for give financial operations durable replay receipts
- **THEN** new acceptance tests demonstrate the gap: drop the successful response after release/split commit, then retry; replay a lock after settlement; replay with altered parties/amount. Demonstrate non-convergence or second debit.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-117 Give financial operations durable replay receipts
- **Status:** OPEN
- **Package:** MP-04
- **Depends on:** T-116 red-proven; package prerequisites: MP-02, MP-06
- **WHEN** MP-04 is implemented
- **THEN** Bank release/refund and 0/partial/100% split operations survive response loss with one set of ledger legs; changed recipient/percentage fails; concurrent opposing Bank commands produce one financial winner and its receipt. Cross-service consistency with the winning lifecycle claim is proved in MP-07/08, after atomic lifecycle claims exist.
- **AND** the implementation satisfies: stable operation ID plus canonical request hash and durable result for lock/release/split and issuance where needed. Atomically store receipt, escrow status, ledger legs and event. Exact replay returns the original successful result; changed content conflicts. Retain lifetime uniqueness of a task's escrow.
- **AND** the implementation satisfies: receipts expose actual recipient(s), amount(s), ruling/operation reference and transaction/event IDs so callers can reconcile ambiguous outcomes. Never convert any generic `escrow_already_resolved` into success.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-118 Write acceptance tests for: Make task posting and compensation recoverable
- **Status:** OPEN
- **Package:** MP-05
- **WHEN** the current implementation is exercised for make task posting and compensation recoverable
- **THEN** new acceptance tests demonstrate the gap: fail after escrow lock but before task insert; lose task-insert response after commit; fail/refuse compensation; restart the process at each boundary.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-119 Make task posting and compensation recoverable
- **Status:** OPEN
- **Package:** MP-05
- **Depends on:** T-118 red-proven; package prerequisites: MP-04, MP-07
- **WHEN** MP-05 is implemented
- **THEN** each failure history ends in one funded published task or one proven refund; no stranded escrow, duplicate task or refund for a successfully published task. The UI can resolve “request timed out” by operation ID.
- **AND** the implementation satisfies: a durable posting intent with stable task/request identity; lock escrow, publish exactly once, and reconcile ambiguous insertion before refunding. Keep incomplete postings out of public open-job discovery. Record and retry compensation as an operation even when no task row exists.
- **AND** the implementation satisfies: repeat submissions of the same client operation return the same job; a changed specification/reward under the same key conflicts. Validate that task terms, escrow payer and reward match.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-120 Write acceptance tests for: Version the schema and enforce financial invariants
- **Status:** OPEN
- **Package:** MP-06
- **WHEN** the current implementation is exercised for version the schema and enforce financial invariants
- **THEN** new acceptance tests demonstrate the gap: direct split to an unrelated poster; mismatched initial credit; bool and oversized monetary inputs; balance overflow; missing/partial schema; direct lifetime-unique escrow constraint and duplicate-escrow migration. Public Bank replay receipts belong to MP-04. Assert unchanged transactions/events on every rejected command.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-121 Version the schema and enforce financial invariants
- **Status:** OPEN
- **Package:** MP-06
- **Depends on:** T-120 red-proven; package prerequisites: MP-01; coordinate MP-02
- **WHEN** MP-06 is implemented
- **THEN** fresh and supported old schemas converge; unknown/partial schemas fail before ready; authorized issuance minus recorded burn equals spendable balances plus locked escrow at bounds and under concurrent credit/lock/settlement; replay produces no duplicate ledger/event/receipt legs; no stored money column becomes REAL. Preserve backups before migrating real data.
- **AND** the implementation satisfies: first provide and test a consistent pre-migration backup plus restoration of the original DB/assets/key references, then add explicit schema version and transactional ordered migrations. Require a verified backup identifier before migrating real data; refuse migration if backup/validation fails. Mandatory readable schema and structural validation. Audit existing balances/types/escrows before constraint installation; quarantine/report invalid rows for reconciliation, never silently coerce or delete.
- **AND** the implementation satisfies: derive or validate payer in the settlement transaction, derive initial balance from its issuance record, reject booleans, constrain integer/nonnegative values and conditional arithmetic. Use an explicit maximum representable monetary value and guard additions; aggregate supply checks must also avoid SQL integer overflow. This technical representability bound is not a new monetary-policy question.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-122 Write acceptance tests for: Make all task transitions atomic and cutoff-aware
- **Status:** OPEN
- **Package:** MP-07
- **WHEN** the current implementation is exercised for make all task transitions atomic and cutoff-aware
- **THEN** new acceptance tests demonstrate the gap: barrier-scheduled accept vs cancel/expire, submit vs expiry, approve vs dispute/auto-approve, duplicate acceptance and bid vs deadline. Execute through separate service instances/connections, not only one object's lock.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-123 Make all task transitions atomic and cutoff-aware
- **Status:** OPEN
- **Package:** MP-07
- **Depends on:** T-122 red-proven; package prerequisites: MP-06
- **WHEN** MP-07 is implemented
- **THEN** every concurrent history is equivalent to one valid serial history; losing commands return explicit conflicts and no money/claim/asset side effects; events name the actual winning transition.
- **AND** the implementation satisfies: required expected version/status predicates in authoritative writes; atomically claim an operation before external effects; validate bid/task/worker membership and cutoff in the transaction. Define exact deadline equality and the acceptance-window policy in the spec. A stale action cannot unconditionally replace a terminal state.
- **AND** the implementation satisfies: reconcile Q-16's acceptance window with Task Board's bidding expiration: use an explicit acceptance cutoff/grace or the configured quorum path; do not claim that accepting after an expired bidding deadline works. Freeze accepted terms including posted reward and offered amount.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-124 Write acceptance tests for: Recover pending effects independently of task status
- **Status:** OPEN
- **Package:** MP-08
- **WHEN** the current implementation is exercised for recover pending effects independently of task status
- **THEN** new acceptance tests demonstrate the gap: Bank outage during auto-approval/refund, task write failure after payout, process kill during effect delivery, and transient failure on an already terminal task. Run with no GET polling.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-125 Recover pending effects independently of task status
- **Status:** OPEN
- **Package:** MP-08
- **Depends on:** T-124 red-proven; package prerequisites: MP-04, MP-07
- **WHEN** MP-08 is implemented
- **THEN** terminal pending tasks settle exactly once after recovery; interruption at every await/commit boundary converges; no “paid” UI state without matching financial receipt and no pending item disappears from scheduling.
- **AND** the implementation satisfies: durable operation/outbox records with retry count, next attempt, safe diagnostic and receipt reference; query pending work regardless of task status. Use bounded backoff, idempotent receipt lookups and fenced ownership. Distinguish business terminal state from settlement completion in the API.
- **AND** the implementation satisfies: startup/background reconciliation; dead-letter/operator visibility for persistent failure, with a documented safe retry command. A failed item cannot starve the rest of the scan. Provide the generic operation primitives that MP-10 will use for Court claim/rebuttal handoffs; MP-08 closes only financial/task-effect recovery.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-126 Write acceptance tests for: Make task inputs and deliverables safe, immutable evidence
- **Status:** OPEN
- **Package:** MP-09
- **WHEN** the current implementation is exercised for make task inputs and deliverables safe, immutable evidence
- **THEN** new acceptance tests demonstrate the gap: upload filename escapes its storage directory; two uploads bypass count/status checks; task input cannot be attached by poster; uploaded metadata/file failure leaves mismatched evidence; evidence changes after submission.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-127 Make task inputs and deliverables safe, immutable evidence
- **Status:** OPEN
- **Package:** MP-09
- **Depends on:** T-126 red-proven; package prerequisites: MP-07
- **WHEN** MP-09 is implemented
- **THEN** no write/read outside configured root; concurrent quotas hold; filenames/content/hashes survive upload→browser→Court; crash between bytes/metadata is repaired. Unsupported artifact formats are rejected or routed to an explicitly supported review path, never silently treated as text.
- **AND** the implementation satisfies: generated storage names; treat supplied filename solely as display metadata; reject traversal/absolute paths and symlink escape; atomic file publication and durable metadata reconciliation. Enforce authorization, size/count and lifecycle predicates together. Bound streaming memory/disk use and clean only proven orphan files.
- **AND** the implementation satisfies: poster input attachments before publication and an immutable input manifest; worker output manifest frozen at submission. Record hash, media type, filename, byte size, actor and revision. Accepted work is tied to one terms/input revision. Serve downloads safely as attachments with authorization/projection policy and correct content types.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-128 Write acceptance tests for: Persist one ruling and resume its delivery
- **Status:** OPEN
- **Package:** MP-10
- **WHEN** the current implementation is exercised for persist one ruling and resume its delivery
- **THEN** new acceptance tests demonstrate the gap: concurrent ruling triggers; kill after `judging`; fail after Task Board settlement then retry with judges returning a different percentage; mismatch claim parties/escrow; fail rebuttal acknowledgment.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-129 Persist one ruling and resume its delivery
- **Status:** OPEN
- **Package:** MP-10
- **Depends on:** T-128 red-proven; package prerequisites: MP-04, MP-07, MP-08, MP-09, MP-11; Q-M1 evidence decision
- **WHEN** MP-10 is implemented
- **THEN** nondeterministic judge fixture settles and records one identical verdict under every retry; no permanently `judging` case after restart; simultaneous triggers produce one owned adjudication; no duplicate or contradictory feedback; mismatched claim parties/task/escrow/version cause no writes; identical rebuttal replay preserves the original Court timestamp, conflicting replay fails, and Task Board recovers the acknowledgment.
- **AND** the implementation satisfies: claim context validated against authoritative task and linked dispute; fenced durable adjudication lease with restart recovery; snapshot evidence; persist votes and final ruling/hash before requesting settlement. Retry side effects from that same immutable result; do not judge again after a result exists.
- **AND** the implementation satisfies: separate adjudication status from delivery progress (Task Board receipt and each reputation write); hash-checked idempotency at consumers; forbid compensation that deletes an already delivered ruling. Handle expired leases and repeated rebuttal with exact-payload replay/conflict behavior.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-130 Write acceptance tests for: Define complete judge evidence and robust prompt/output contracts
- **Status:** OPEN
- **Package:** MP-11
- **WHEN** the current implementation is exercised for define complete judge evidence and robust prompt/output contracts
- **THEN** new acceptance tests demonstrate the gap: decisive evidence beyond current truncation budget; binary artifact; malicious instructions in each untrusted field; malformed/out-of-range model output; precise poor delivery vs vague acceptable delivery.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-131 Define complete judge evidence and robust prompt/output contracts
- **Status:** OPEN
- **Package:** MP-11
- **Depends on:** T-130 red-proven; package prerequisites: MP-09; decisions Q-M1 and Q-M3
- **WHEN** MP-11 is implemented
- **THEN** deterministic adversarial fixtures exercise typed manifests, hashes, filenames/media types, explicit truncation/unsupported-content outcomes, structured untrusted inputs and validated output schemas; provider context/time/cost bounds are enforced; no silent truncation or mock fallback in a real profile. Prompt changes alone do not close empirical injection/quality claims; MP-25 supplies measured evidence.
- **AND** the implementation satisfies: typed, versioned evidence bundle with complete manifest and supported extracted content; explicit aggregate budget/overflow policy. Preserve identity of each file and any omitted bytes; block automatic judgment when required evidence cannot be evaluated. Never fetch arbitrary model-provided URLs or execute deliverables.
- **AND** the implementation satisfies: system rubric and separate structured untrusted data; explain that task/claim/rebuttal text cannot redefine judge instructions. Validate structured output, separate specification and delivery judgments from payout, record model/prompt/version and reasons. Validation failure causes bounded retry or visible pending review, not an invented award.
- **AND** the implementation satisfies: enforce configured real-run three-judge policy, deterministic median semantics, provider deadlines, concurrency and cost limits; keep mock judge profile explicit for CI only.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-132 Write acceptance tests for: Close sealed-data leaks across public projections
- **Status:** OPEN
- **Package:** MP-12
- **WHEN** the current implementation is exercised for close sealed-data leaks across public projections
- **THEN** new acceptance tests demonstrate the gap: unrelated caller reads open bids via UI task API; sealed rating appears in event history/SSE; personalized response is cached or rendered for another actor.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-133 Close sealed-data leaks across public projections
- **Status:** OPEN
- **Package:** MP-12
- **Depends on:** T-132 red-proven; package prerequisites: MP-02
- **WHEN** MP-12 is implemented
- **THEN** multi-actor visibility matrix for open/accepted and sealed/revealed states across every read path, including reconnect, history, raw JSON and rendered UI. Public read-only observation still works.
- **AND** the implementation satisfies: classify each read field/event as public, party-only or platform-only; centralize the projection rule across UI SQL/API, Task Board and Reputation. Authenticate the operator/party context where needed. Read-only DB access remains allowed but never bypasses domain visibility policy.
- **AND** the implementation satisfies: public event envelope without sealed payload; authorized poster gets actual bid amounts, identities and timestamps; unauthenticated observers see permitted aggregate counts only. Apply reveal timestamps consistently and prevent rating-dependent aggregate leakage.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-134 Write acceptance tests for: Authorize feedback and make reveal durable
- **Status:** OPEN
- **Package:** MP-13
- **WHEN** the current implementation is exercised for authorize feedback and make reveal durable
- **THEN** new acceptance tests demonstrate the gap: unrelated signer/task/recipient, premature task feedback, swapped category, expired one-sided feedback absent from UI, mislabeled FK errors and duplicate Court delivery.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-135 Authorize feedback and make reveal durable
- **Status:** OPEN
- **Package:** MP-13
- **Depends on:** T-134 red-proven; package prerequisites: MP-07, MP-10, MP-12
- **WHEN** MP-13 is implemented
- **THEN** three-party adversarial test, concurrent counter-feedback, timeout across restart, Court partial delivery and zero premature public score influence all pass.
- **AND** the implementation satisfies: task-party/category/outcome authorization at authoritative insertion; explicit source and subject roles for ordinary versus Court feedback; unique logical identities and exact-replay comparison. Preserve sealed mutual-reveal atomicity already implemented.
- **AND** the implementation satisfies: durable timed reveals/events without a reader; same visibility used by API/UI/scores. Distinguish unknown task/agent, unauthorized, duplicate/conflicting content and unavailable dependency.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-136 Write acceptance tests for: Expose trustworthy specification and delivery scores
- **Status:** OPEN
- **Package:** MP-14
- **WHEN** the current implementation is exercised for expose trustworthy specification and delivery scores
- **THEN** new acceptance tests demonstrate the gap: no service-owned score query; UI/feeder give different standing; equal payouts conceal different specification/delivery failures; untested identities appear to have earned perfect quality.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-137 Expose trustworthy specification and delivery scores
- **Status:** OPEN
- **Package:** MP-14
- **Depends on:** T-136 red-proven; package prerequisites: MP-11, MP-13; resolve Q-M3
- **WHEN** MP-14 is implemented
- **THEN** table-driven scoring fixtures, replay/no-double-count, sealed exclusion, version migration and identical values in UI and agent selection. No unverifiable claim of Sybil resistance.
- **AND** the implementation satisfies: versioned `/reputation/agents/{id}/scores` contract with raw counts, dimensions, formula version and provenance; ratify a minimal formula with examples before code. Distinguish lack of observations from a measured 100%. Specify treatment of ordinary feedback, Court quality findings, cancellations and expired work.
- **AND** the implementation satisfies: all consumers use this projection; tie-breaking uses delivery quality and a deterministic final tie rule, not duplicated ad hoc ratios. Keep outcome/payment separate from quality.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-138 Write acceptance tests for: Complete the human operator journey
- **Status:** OPEN
- **Package:** MP-15
- **WHEN** the current implementation is exercised for complete the human operator journey
- **THEN** new acceptance tests demonstrate the gap: a fresh operator cannot inspect balance/history, attach inputs, compare true amounts, download/review artifacts, cancel eligible jobs, give feedback, take an agent-posted job as the operator identity, or resolve ambiguous write errors entirely through supported UI.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-139 Complete the human operator journey
- **Status:** OPEN
- **Package:** MP-15
- **Depends on:** T-138 red-proven; package prerequisites: MP-04, MP-05, MP-09, MP-12, MP-13, MP-19; score display after MP-14
- **WHEN** MP-15 is implemented
- **THEN** human posts a non-math task with input, independent agent works, human approves; second task disputes and reaches ruling; cancel/refund and review-timeout paths are observable; balances and histories match Bank receipts. Also prove the operator bids/works on an agent-posted job, receives payment and can rebut a dispute as the actual assigned worker. Test after browser refresh and lost write response.
- **AND** the implementation satisfies: explicit operator identity/balance/funding status; job form with validation, inputs, terms/deadlines and insufficient-funds feedback; own-jobs queue and accurate offer comparison; acceptance confirmation showing full v1 reward vs signal bid; deliverable inspection/download; approve/dispute with reasons; eligible cancel; feedback; final ledger/ruling links; a “My work” view for the operator to bid, submit artifacts and rebut on an agent-posted job as itself.
- **AND** the implementation satisfies: stable operation keys for double-click/retry, pending/error/recovery views, authorization-aware action eligibility, loading/empty states and keyboard-accessible forms. The UI must expose enough state to act safely, not merely hide an unauthorized button.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-140 Write acceptance tests for: Make external-agent participation a supported interface
- **Status:** OPEN
- **Package:** MP-16
- **WHEN** the current implementation is exercised for make external-agent participation a supported interface
- **THEN** new acceptance tests demonstrate the gap: run a new agent outside the repository with only its own keys and configured public service URLs; document every private roster/notary/filesystem dependency needed to finish registration→payment→posting.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-141 Make external-agent participation a supported interface
- **Status:** OPEN
- **Package:** MP-16
- **Depends on:** T-140 red-proven; package prerequisites: MP-02–MP-05, MP-09
- **WHEN** MP-16 is implemented
- **THEN** clean external directory on a separate host or isolated network namespace, with access only to documented agent-facing URLs and no shared filesystem, Gateway, operator proxy, local roster entry or notary key: agent earns from a human job, sees its receipt, then posts funded work. A second independently registered agent can complete it.
- **AND** the implementation satisfies: documented public SDK/config constructor or CLI using existing primitives; key provisioning/import, registration/recovery, zero-balance account opening, owned balance/history, pagination/discovery, task/asset methods, bids, accept/review, dispute/rebuttal and feedback. No private `_sign_jws` calls in examples. Document authenticated tunneled/private-network access to the agent-facing APIs while Gateway and the browser operator proxy remain inaccessible; the local deployment does not become a public multi-user site.
- **AND** the implementation satisfies: an explicit administrator-invoked, platform-signed grant/top-up procedure for new posters with auditable issuance reference; workers can begin at zero balance per ratified policy. Give errors, idempotency/retry behavior, capabilities/limits and token profile a single integration contract.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-142 Write acceptance tests for: Recover worker commitments after restart
- **Status:** OPEN
- **Package:** MP-17
- **WHEN** the current implementation is exercised for recover worker commitments after restart
- **THEN** new acceptance tests demonstrate the gap: restart after bid, acceptance, upload, submission and dispute; original loop forgets its commitment or cannot rebut; failed/expired outcomes leave inconsistent earnings/history.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-143 Recover worker commitments after restart
- **Status:** OPEN
- **Package:** MP-17
- **Depends on:** T-142 red-proven; package prerequisites: MP-07–MP-10, MP-16
- **WHEN** MP-17 is implemented
- **THEN** process termination at each commitment boundary recovers to one correct outcome; no abandoned accepted task, duplicate ledger earnings or missing rebuttal solely due to restart. Hostile task text cannot command local shell/tool execution in the example runtime.
- **AND** the implementation satisfies: reconcile remote open bids and accepted/submitted/disputed jobs at startup and periodically; durable local journal for execution checkpoints and operation IDs; resume or visibly fail owned commitments. Do not re-bid or re-upload completed work after an acknowledgment is lost.
- **AND** the implementation satisfies: bounded concurrency/inference/time budgets and task-capability filtering; continue review/rebuttal tracking independently of new-job discovery. Record settled earnings from receipts, not promised reward or poll timeout. Handle expired/cancelled tasks and late acceptance explicitly.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-144 Write acceptance tests for: Persist feeder review and acceptance policy
- **Status:** OPEN
- **Package:** MP-18
- **WHEN** the current implementation is exercised for persist feeder review and acceptance policy
- **THEN** new acceptance tests demonstrate the gap: restart loses expected answers and silently stops reviewing old submissions; bid window cutoff races expiration; lowest/tied offers yield unstable selection; selection-dependent pagination starves jobs.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-145 Persist feeder review and acceptance policy
- **Status:** OPEN
- **Package:** MP-18
- **Depends on:** T-144 red-proven; package prerequisites: MP-05, MP-07, MP-13, MP-14, MP-16
- **WHEN** MP-18 is implemented
- **THEN** restart during an unattended approve and dispute round still reaches settlement without demo/human actions; reviews do not silently turn into timeout approvals because memory was lost; multiple feeders cannot post/review the same item twice.
- **AND** the implementation satisfies: durable posted-task/expected-answer/review records tied to task version and hashes; reconcile server state on startup; separate posting, acceptance and review schedules so one failed task cannot block the economy.
- **AND** the implementation satisfies: Q-16 window/quorum and cutoff policy from MP-07; lowest amount, service-owned delivery score then stable tie-break; explicit fallback when scores unavailable. Preserve generic task support by not applying math answer parsing to arbitrary human work.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-146 Write acceptance tests for: Make observatory state, metrics and reconnects accurate
- **Status:** OPEN
- **Package:** MP-19
- **WHEN** the current implementation is exercised for make observatory state, metrics and reconnects accurate
- **THEN** new acceptance tests demonstrate the gap: accepted bidder amounts disappear; ruled work excluded from completion; multiple jobs for one worker distort employment; zero-bid tasks disappear from average; reconnect replays old events or skips racing new ones.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-147 Make observatory state, metrics and reconnects accurate
- **Status:** OPEN
- **Package:** MP-19
- **Depends on:** T-146 red-proven; package prerequisites: MP-08, MP-12, MP-13
- **WHEN** MP-19 is implemented
- **THEN** a hand-calculated dataset covering all terminal outcomes matches every chart/table; history→stream race and reconnect with new events deliver each event once in the view; score timeout reveal propagates consistently.
- **AND** the implementation satisfies: one documented metric definition per field with explicit denominators, distinct-agent counting, outcome categories, time zone/window and payment basis. Differentiate posted reward, offered amount, settled payment, refunds and money supply; GDP must not double-count minting or locked escrow.
- **AND** the implementation satisfies: consistent snapshot plus high-watermark, resume cursor/Last-Event-ID, stable event-ID deduplication and reconnect/disconnect behavior; bounded backfill and retention policy. Preserve chronological order even when timestamps match. Show pending settlement and model/mock provenance.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-148 Write acceptance tests for: Tighten service contracts and remove unsafe blocking/fallbacks
- **Status:** OPEN
- **Package:** MP-20
- **WHEN** the current implementation is exercised for tighten service contracts and remove unsafe blocking/fallbacks
- **THEN** new acceptance tests demonstrate the gap: invalid 2xx JSON/missing fields returns fake zero/empty success; wrong 409 code is collapsed; slow synchronous gateway I/O blocks unrelated Task Board requests and deadline progress; fake store accepts inputs rejected in production.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-149 Tighten service contracts and remove unsafe blocking/fallbacks
- **Status:** OPEN
- **Package:** MP-20
- **Depends on:** T-148 red-proven; package prerequisites: producer contracts from MP-04/MP-07/MP-10; design starts earlier
- **WHEN** MP-20 is implemented
- **THEN** malformed upstream replies produce explicit dependency errors, all consumers pass contract tests, Task Board health/unrelated work and sweep lag stay bounded during slow gateway probes, and existing public behavior survives each small refactor.
- **AND** the implementation satisfies: typed required response fields, strict errors and safe diagnostic context, explicit timeouts and cancellation; offload an entire sync operation safely or convert the boundary consistently to async. Do not split a single shared SQLite transaction across concurrent threadpool calls.
- **AND** the implementation satisfies: public SDK methods replace consumer-private signing; remove dead client paths only after call-site inventory; consolidate duplicated validation where behavior is identical and covered. Keep domain policy at authoritative commands rather than hidden in convenience clients.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-150 Write acceptance tests for: Provide reliable startup, health, backup and recovery operations
- **Status:** OPEN
- **Package:** MP-21
- **WHEN** the current implementation is exercised for provide reliable startup, health, backup and recovery operations
- **THEN** new acceptance tests demonstrate the gap: required service missing but startup says success; keys/assets absent on restoration; stale pending work never resumes; false readiness with invalid schema/dependency; teardown kills unrelated process.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-151 Provide reliable startup, health, backup and recovery operations
- **Status:** OPEN
- **Package:** MP-21
- **Depends on:** T-150 red-proven; package prerequisites: MP-01, MP-03, MP-06, MP-08, MP-10
- **WHEN** MP-21 is implemented
- **THEN** restore a live-like snapshot to a fresh isolated run and finish pending work without extra mint/payment; failure drills produce actionable diagnostics; startup/teardown honor exact ownership and required dependencies.
- **AND** the implementation satisfies: owned-process lifecycle and readiness separate from liveness; fail-fast configuration with positive limits and valid paths; shutdown waits for durable claims or releases leases safely. Document single-gateway-process support until multi-process migrations/ownership are tested.
- **AND** the implementation satisfies: consistent DB/WAL backup, hashed asset manifest and protected key backups; versioned restoration/migration command; reconciliation report for supply, balances, escrow/task/ruling links and pending effects. Include disk-full, permissions and failed migration recovery.
- **AND** the implementation satisfies: structured operation/task/dispute/escrow correlation, pending-work age/retry/judge failure indicators and bounded logs. Do not log private keys, tokens or sealed evidence in public metrics.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-152 Write acceptance tests for: Turn CI into an honest failure/concurrency gate
- **Status:** OPEN
- **Package:** MP-22
- **WHEN** the current implementation is exercised for turn ci into an honest failure/concurrency gate
- **THEN** new acceptance tests demonstrate the gap: fault scenarios escape current coverage; an earlier startup/provisioning failure can be masked by a later passing command; shared fixture order alters UI results; format step silently changes files.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-153 Turn CI into an honest failure/concurrency gate
- **Status:** OPEN
- **Package:** MP-22
- **Depends on:** T-152 red-proven; package prerequisites: MP-01 through MP-21; MP-23–MP-26 consume this harness and are not prerequisites
- **WHEN** MP-22 is implemented
- **THEN** root `just ci-quiet` exits 0 from a clean disposable run with no source mutation; deliberately injected known faults fail their guards; the entire UI suite passes in isolation and varied order. Do not weaken flaky assertions to force green.
- **AND** the implementation satisfies: deterministic clock/barrier tests, real gateway contracts, actual process-kill tests, mock transport response-loss hooks and repeated multi-instance histories. Add property/model-based money/state invariants using existing tools first; any new dependency requires explicit approval.
- **AND** the implementation satisfies: read-only formatting checks, fail-fast shell exit propagation, unique test runtime resources, reproducible seeded browser fixtures, required browser install preflight, clean checkout/frozen lock validation and retained failure artifacts. Audit the existing vulnerability exception T-108 against the installed dependency resolution at implementation time.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-154 Write acceptance tests for: Prove the complete human and autonomous platform journeys
- **Status:** OPEN
- **Package:** MP-23
- **WHEN** the current implementation is exercised for prove the complete human and autonomous platform journeys
- **THEN** new acceptance tests demonstrate the gap: encode each section 8 journey through public interfaces; record which fails on the current baseline. No direct DB inserts or monkeypatched browser write proxy in the end-to-end acceptance lane.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-155 Prove the complete human and autonomous platform journeys
- **Status:** OPEN
- **Package:** MP-23
- **Depends on:** T-154 red-proven; package prerequisites: MP-15–MP-22 and required MP-25 live-profile proof
- **WHEN** MP-23 is implemented
- **THEN** clean install/provision → human job with input → independent worker → submission → approval/payment/feedback; second job → rebuttal → real-profile Court ruling; cancellation and timeouts; two autonomous agents perform both approve/dispute rounds with restarts; UI matches real outcomes. repeat with injected response loss and service restarts; record IDs, receipts, evidence hashes, screenshots/browser errors, logs and full CI result. Deterministic mock-judge CI and bounded real-judge evaluation are separate, labeled evidence.
- **AND** the implementation satisfies: only missing integration wiring and failures exposed by these journeys, assigned back to their owning package; do not place business behavior in test setup or demo scripts.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-156 Write acceptance tests for: Complete the economic mechanisms and non-math example
- **Status:** OPEN
- **Package:** MP-24
- **WHEN** the current implementation is exercised for complete the economic mechanisms and non-math example
- **THEN** new acceptance tests demonstrate the gap: current bids affect ranking but not payment; no general task capability/learning example proves specialization; classification-arena requirements are absent or unproven.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-157 Complete the economic mechanisms and non-math example
- **Status:** OPEN
- **Package:** MP-24
- **Depends on:** T-156 red-proven; package prerequisites: MP-04, MP-14, MP-16–MP-18; resolve Q-M2/Q-M3
- **WHEN** MP-24 is implemented
- **THEN** real service APIs drive the classification example; measure specialization/quality rather than hard-coding role assignments or asserting a guaranteed emergent outcome. Repeated seeds show results and uncertainty. If the hypothesis fails, report that result and revise policy openly; do not falsify the demo.
- **AND** the implementation satisfies: for fixed-reward v1, state that fact in UI/API/docs and test payment equals reward. If the full-vision decision adopts price-forming awards, freeze accepted bid as contract price, settle worker share from that price and refund reward-minus-price; define rounding and all timeout/dispute/cancellation cases before migration. Never silently change outstanding accepted contracts.
- **AND** the implementation satisfies: reproducible non-math task corpus and contrasting worker capabilities using existing dependencies; generic task/capability metadata or explicit supported content types; record costs, accepted prices, quality, outcomes and score-informed selection over rounds. Keep any provider-specific expense distinct from platform coins unless an exchange policy is explicitly defined.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-158 Write acceptance tests for: Validate the real judge system against the core thesis
- **Status:** OPEN
- **Package:** MP-25
- **WHEN** the current implementation is exercised for validate the real judge system against the core thesis
- **THEN** new acceptance tests demonstrate the gap: evaluate current real prompts on paired precise/vague specs, compliant/noncompliant deliveries and injected instructions; mock 50% results do not count as quality proof.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-159 Validate the real judge system against the core thesis
- **Status:** OPEN
- **Package:** MP-25
- **Depends on:** T-158 red-proven; package prerequisites: MP-10, MP-11, MP-14; corpus authoring can start earlier
- **WHEN** MP-25 is implemented
- **THEN** the panel meets predeclared quality/robustness thresholds, each verdict is traceable to frozen evidence, and retry stability is proven independently of model determinism. If it does not pass, keep adjudication readiness open and improve rubric/evidence/model configuration.
- **AND** the implementation satisfies: a versioned evaluation corpus with human-specified expected ranges/rationales, evidence completeness checks and neutral identifiers; configured three-judge real profile; structured-output success, consistency, injection resistance, worker-favoring ambiguity, cost and latency measurements. Set acceptance thresholds in the test spec before looking at the new model results.
- **AND** the implementation satisfies: bounded opt-in provider execution with explicit credentials/budget, reproducible prompt/model settings, anonymized retained results and failure triage. No automatic fallback to mock payouts. Real-provider downtime leaves a recoverable pending dispute.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

#### Scenario: T-160 Write acceptance tests for: Reconcile specifications, backlog and release evidence
- **Status:** OPEN
- **Package:** MP-26
- **WHEN** the current implementation is exercised for reconcile specifications, backlog and release evidence
- **THEN** new acceptance tests demonstrate the gap: create a behavior→spec→test→ticket matrix showing stale or unsupported claims; check commands, routes, JSON examples, lifecycle vocabularies and all links in the integration guide.
- **AND** the observed failure, command, baseline and artifact are recorded before implementation begins
- **AND** final closure requires the paired implementation proof and `just ci-quiet` exit 0 from the repository root

#### Scenario: T-161 Reconcile specifications, backlog and release evidence
- **Status:** OPEN
- **Package:** MP-26
- **Depends on:** T-160 red-proven; package prerequisites: inventory starts immediately; closure follows MP-23 and MP-24/25 for full-vision completion
- **WHEN** MP-26 is implemented
- **THEN** a reader can reproduce both journeys without private knowledge; every task has red/green/full-gate/commit evidence or remains open; all new changes committed/pushed; exact release configuration and residual limitations recorded.
- **AND** the implementation satisfies: update baseline/domain API/test specs and relevant ADRs after accepted behavior changes; label historical plans rather than erase history; reconcile T-001–T-109 using section 7; keep one canonical OpenSpec tracker and this execution view synchronized.
- **AND** the implementation satisfies: fresh-start human guide, external-agent guide, financial/recovery runbook, judge-provider/evaluation guide, explicit supported deployment/artifact limits and remaining accepted risks. Remove claims of salaries, price-forming bids, multi-user hosting or mock-judge quality unless their gates passed.
- **AND** the corresponding master-plan release gates pass, independent review is recorded, and root `just ci-quiet` exits 0

### Requirement: Resolve material economic and evidence policy

The project SHALL record the following decisions with accountable ownership and worked acceptance examples before the dependent policy implementation closes. Engineering preferences and already-ratified v1 choices SHALL NOT become new decision blockers.

#### Scenario: T-162 Decide Q-M1: Court evidence admissibility and artifact privacy
- **Status:** OPEN decision
- **Accountable owner:** Project owner; implementation lead prepares options
- **WHEN** Q-M1 is resolved
- **THEN** an accepted ADR defines supported formats/limits, immutable provenance, participant/bidder access, retention, external-provider disclosure and unsupported-evidence outcomes; MP-09/MP-11 cannot close without it
- **AND** the decision record is linked in the master plan and relevant specifications; dependent implementations and their proof obligations remain separately tracked

#### Scenario: T-163 Decide Q-M2: Post-v1 price-forming economics
- **Status:** OPEN decision
- **Accountable owner:** Project owner; Bank/Task Board leads prepare examples
- **WHEN** Q-M2 is resolved
- **THEN** an accepted economic ADR specifies fixed-reward versus accepted-price policy, excess-refund timing, split rounding and outstanding-contract treatment; Q-8 fixed reward remains valid for Core; MP-24 pricing claims match the decision
- **AND** the decision record is linked in the master plan and relevant specifications; dependent implementations and their proof obligations remain separately tracked

#### Scenario: T-164 Decide Q-M3: Independent quality dimensions and score formula
- **Status:** OPEN decision
- **Accountable owner:** Project owner; Reputation/Court leads prepare fixtures
- **WHEN** Q-M3 is resolved
- **THEN** an accepted versioned contract defines separate specification/delivery quality, payout separation, source weighting, no-history treatment, sample counts and worked scoring cases; MP-11/MP-14 cannot close without it
- **AND** the decision record is linked in the master plan and relevant specifications; dependent implementations and their proof obligations remain separately tracked
