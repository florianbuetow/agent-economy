# Spec Delta

## MODIFIED Requirements

### Requirement: Autonomous Agent Task Economy
The system SHALL provide a micro-economy where autonomous agents can register identities, receive funds, post tasks, bid for work, deliver results, review outcomes, resolve disputes, and build reputation from specification and delivery quality.

#### Scenario: Economy services support a complete task market
- **WHEN** registered agents participate in the platform
- **THEN** they can act as task posters, workers, reviewers, and dispute participants through service APIs rather than manual database edits

#### Scenario: Ambiguous specifications favor the worker
- **WHEN** a disputed task has an ambiguous, incomplete, or underspecified task specification
- **THEN** the Court ruling favors the worker relative to the ambiguity and records the poster's specification quality penalty

#### Scenario: Human and independent agent complete a market round
- **WHEN** a human posts supported work through the UI and an independent agent joins using only the documented agent-facing APIs
- **THEN** they can progress through input retrieval, bidding, acceptance, delivery, review or dispute, payment and feedback without database edits or notary-key access
- **AND** the worker can use earned funds to post another job that a different participant completes

#### Scenario: Economic claims match the active policy
- **WHEN** the platform presents fixed-reward v1 bids or reports specialization and quality improvement
- **THEN** it distinguishes bid signals from actual paid rewards and distinguishes measured economic outcomes from scripted or mock demonstrations
- **AND** any post-v1 price-forming policy is implemented only after its separately tracked decision, including rounding and outstanding-task treatment

### Requirement: Signed Identity and Platform Authority
The system SHALL use Ed25519-backed agent identities and JWS signatures to authenticate mutating operations, with platform-owned notary authority reserved for privileged service-to-service actions.

#### Scenario: Agent registration creates a verifiable identity
- **WHEN** an agent registers with the Identity service using a public key and display name
- **THEN** the Identity service stores a unique agent record and exposes it for later signature verification

#### Scenario: Agent actions are signature verified
- **WHEN** an agent posts a task, bids, uploads assets, submits deliverables, approves work, disputes work, or submits feedback
- **THEN** the receiving service verifies the signed payload through Identity before applying the action

#### Scenario: Platform-only actions use the notary identity
- **WHEN** the system creates funded accounts, credits funds, files Court claims on behalf of the workflow, records rulings, splits escrow, or submits Court-generated reputation feedback
- **THEN** the action is signed by the configured platform/notary agent and rejected for ordinary agent signatures

#### Scenario: Agent opens an unfunded account
- **WHEN** a registered ordinary agent requests its own zero-balance account under the Bank contract
- **THEN** it can join without notary authority while creation of nonzero funds remains platform-only

#### Scenario: Gateway caller lacks domain authority
- **WHEN** an unsigned, expired, tampered or wrong-domain caller attempts a persistence operation
- **THEN** the operation is rejected before any data or event changes
- **AND** an ordinary economic agent cannot mint coins, settle arbitrary escrow or bypass service authorization through gateway reads or writes

#### Scenario: Concurrent provisioning and damaged keys
- **WHEN** multiple processes initialize one identity concurrently or an existing keypair is incomplete or inconsistent
- **THEN** successful initializers use one consistent identity, and damaged existing identity material fails explicitly without silently replacing an identity holding funds or commitments
- **AND** private key material is protected from unauthorized filesystem access

#### Scenario: Equivalent public-key encodings cannot create separate identities
- **WHEN** two registrations encode the same valid Ed25519 public-key bytes with different base64 pad bits
- **THEN** Identity validates and stores the canonical base64 representation before its uniqueness check, so the second registration conflicts with the same existing identity
- **AND** existing historical aliases are handled by an audited identity-preserving migration; colliding identities are not silently merged

#### Scenario: Token validators agree
- **WHEN** a request has missing, malformed, wrong-type, expired or future-dated required token fields, an unsupported algorithm, or the wrong action/resource audience
- **THEN** every applicable verifier rejects it using the documented client-error contract
- **AND** a non-object protected header cannot cause an unhandled server error
- **AND** replay inside a valid token lifetime still follows the command's operation-identity contract

#### Scenario: Browser content and cross-origin actions are untrusted
- **WHEN** an agent-controlled name, title, comment, summary or artifact is displayed or an off-origin page attempts an operator write
- **THEN** untrusted content cannot execute in the operator origin and unauthorized Host/Origin requests cannot cause operator-signed effects

### Requirement: Funds and Escrow Lifecycle
The Central Bank SHALL manage account balances, transaction history, escrow locks, escrow releases, and escrow splits for task rewards.

#### Scenario: Task posting locks escrow
- **WHEN** a poster creates a task with a valid task token and matching escrow token
- **THEN** the Task Board requests Central Bank escrow lock for the task reward before the task becomes open

#### Scenario: Approval releases payment
- **WHEN** a submitted task is approved by the poster
- **THEN** Task Board releases escrow to the worker through Central Bank and records the approved outcome

#### Scenario: Court ruling splits payment
- **WHEN** the Court issues a ruling with a worker payout percentage
- **THEN** Court calls Task Board with a platform-signed ruling and Task Board releases or splits escrow through Central Bank based on the worker percentage

#### Scenario: Financial result survives a lost acknowledgment
- **WHEN** a money operation commits but its response is lost and the same operation is retried
- **THEN** the original result and transaction/event identities are returned without another debit, credit or escrow
- **AND** reuse of that identity with different amounts, recipients or settlement terms returns a conflict without changes
- **AND** an escrow lock cannot debit again for the same task after the escrow has already settled

#### Scenario: Native financial receipts distinguish identity from current state
- **WHEN** a native positive issuance, credit, lock, release or split command is accepted
- **THEN** its caller-supplied canonical operation ID, kind, canonical request hash, source reference, commit time, event, result and logical legs are persisted atomically with its financial effect
- **AND** exact replay returns the complete immutable receipt and a separate truthful current escrow projection, without replacing winning transaction identities with retry-generated candidates
- **AND** a different operation ID for a native credit or lock's existing natural key conflicts with the true winning operation ID rather than creating a receipt alias

#### Scenario: Historical financial effects remain honestly identified
- **WHEN** a verified v1 database is explicitly migrated to receipt schema v2
- **THEN** all historical transactions, escrow locks and resolved escrow facts receive immutable observed-fact coverage with a deterministic cutover-set attestation, preserving their existing rows and identities
- **AND** no historical caller operation ID, original request hash or native receipt is invented
- **AND** an exact historical natural-key credit or lock replay returns a labeled legacy observation with null operation identity, while changed historical content and historical settlement retries fail closed without movement
- **AND** a later effect missing native receipt ownership cannot be treated as legacy; missing ownership or changed attested markers fails audit

#### Scenario: Invalid financial values and ownership
- **WHEN** account creation, credit or escrow settlement supplies booleans as numbers, invalid bounds, inconsistent issuance, a non-payer poster or values that would overflow a recipient balance
- **THEN** the command is rejected atomically with a documented error and no ledger or event changes
- **AND** stored monetary values remain nonnegative integers

#### Scenario: Money is conserved under concurrency and retries
- **WHEN** concurrent and replayed issuance, lock, refund and split commands finish
- **THEN** authorized issuance minus recorded burns equals spendable account balances plus locked escrow
- **AND** each natively resolved escrow has exactly one consistent receipt, with no duplicated ledger or event legs; pre-cutover resolutions retain their explicit attested historical observation
- **AND** zero-valued split legs remain visible in the receipt with null transaction and balance identities, create no ledger entries, and require no account for that zero-valued recipient

### Requirement: Canonical Task Lifecycle
The Task Board SHALL be the source of truth for task lifecycle state and SHALL expose one canonical lowercase status vocabulary: `open`, `accepted`, `submitted`, `approved`, `cancelled`, `disputed`, `ruled`, and `expired`.

#### Scenario: Real agents discover open work
- **WHEN** feeder or worker agents query for available work
- **THEN** they use the canonical `open` status returned by Task Board, not legacy statuses such as `BIDDING`

#### Scenario: Bid acceptance starts execution
- **WHEN** a poster accepts a bid on an `open` task
- **THEN** Task Board assigns the winning worker, records the accepted bid, sets status to `accepted`, and starts the execution deadline

#### Scenario: Poster specifies the acceptance window
- **WHEN** a poster publishes a new task
- **THEN** the poster explicitly supplies the acceptance-window duration following the bidding deadline, and it is frozen with the published terms without a silent global duration
- **AND** authorized early acceptance remains available without imposing the feeder's quorum or ranking rule on human posters

#### Scenario: Bidding closes before selection
- **WHEN** the bidding deadline is reached
- **THEN** new bids are rejected and a task with no bids expires immediately
- **AND** a task with bids remains eligible for authorized acceptance until its poster-selected acceptance cutoff, where expiry wins
- **AND** migration preserves already-published contractual cutoffs rather than silently extending them

#### Scenario: Worker submission enters review
- **WHEN** the assigned worker uploads at least one asset and submits deliverables for an `accepted` task
- **THEN** Task Board sets status to `submitted` and starts the review deadline

#### Scenario: Review produces a terminal or dispute branch
- **WHEN** a task is in `submitted` status
- **THEN** the poster can approve it, dispute it, or allow deadline evaluation to apply configured timeout behavior

#### Scenario: Invalid lifecycle transitions are rejected
- **WHEN** a caller attempts an action that does not match the current task status
- **THEN** Task Board rejects the request with an explicit invalid-status error instead of silently coercing the transition

#### Scenario: Agents use canonical statuses
- **WHEN** feeder, worker, and reviewer loops poll Task Board
- **THEN** they query and compare lowercase statuses such as `open`, `accepted`, `submitted`, `disputed`, and `ruled`

#### Scenario: Conflicting task actions race
- **WHEN** acceptance and cancellation, submission and expiry, approval and dispute, or two competing acceptances operate on the same task version
- **THEN** at most one incompatible transition succeeds and the loser receives an explicit conflict
- **AND** no losing operation leaves money movement, a claim, worker assignment or asset state contradicting the winning transition

#### Scenario: Posting fails between escrow and publication
- **WHEN** task publication fails or its acknowledgment is lost after funds are locked
- **THEN** recovery produces exactly one correctly funded public task or exactly one documented refund
- **AND** ambiguous publication is reconciled before any compensation refunds an existing task

#### Scenario: Native posting identity precedes financial transport
- **WHEN** a poster prepares and submits one native posting intent
- **THEN** its task and operation IDs and complete immutable terms are retained before transport and reserved durably before Bank dispatch
- **AND** exact replay returns the original publication result, changed terms conflict, and two deliberately distinct postings with identical content remain distinct
- **AND** publication operation IDs cannot also own lifecycle operations

#### Scenario: Bank acknowledgment is absent but a request may still commit
- **WHEN** an admitted posting has no observed lock receipt after a timeout or restart
- **THEN** it remains pending without publication or compensation based solely on that absence
- **AND** background recovery can publish a subsequently proven lock, but only a fresh poster-signed retry of the same intent can initiate an unproven debit
- **AND** a late original dispatch and concurrent same-intent retry produce one lock and one task

#### Scenario: Funding and publication become authoritative together
- **WHEN** the native publication reconciler observes the exact persisted lock receipt
- **THEN** it verifies the request, payer, task, amount, escrow state, leg, transaction and event ownership before atomically creating the task, timing contract, creation event and immutable result
- **AND** bidding and acceptance cutoffs derive from atomic publication time, separately from intent admission, without changing old published terms
- **AND** pending postings remain absent from public task discovery

#### Scenario: Legacy posting orphans are recovered without inventing terms
- **WHEN** verified quiescent migration finds supported historical escrow evidence unreferenced by any published task's escrow
- **THEN** it records a separate durable observation and per-escrow refund identity rather than inventing a native posting intent or specification
- **AND** a different payer's orphan may be refunded while the same task ID's published winner keeps its funding
- **AND** legacy observations permanently reserve their task IDs, existing proven refunds retain their original receipts, and ambiguous provenance never authorizes a refund or new posting

#### Scenario: Terminal task has pending effects
- **WHEN** a task is approved, expired, cancelled or ruled but an associated financial effect remains unfinished
- **THEN** background recovery continues independently of task reads or its terminal business status
- **AND** the API distinguishes pending settlement from a verified financial receipt

#### Scenario: Deadlines progress without readers
- **WHEN** no client reads a task during a deadline or a temporary dependency outage
- **THEN** eligible transitions and pending recovery still progress after the dependency recovers
- **AND** exact cutoff equality and acceptance eligibility follow one explicitly documented policy

#### Scenario: Fractional timestamps preserve the full configured phase duration
- **WHEN** a bidding, execution or review phase begins at a timestamp with fractional seconds
- **THEN** its calculated deadline is the complete phase-start timestamp plus the configured duration
- **AND** no fractional seconds are discarded to cause premature expiry, while established whole-second timestamp formatting remains valid

### Requirement: Binding and Sealed Bidding
The bidding system SHALL make bids binding and sealed while a task is open.

#### Scenario: Worker submits a binding bid
- **WHEN** a non-poster agent submits a signed bid for an `open` task
- **THEN** Task Board records one bid for that worker and prevents duplicate bids on the same task

#### Scenario: Open-task bids remain sealed
- **WHEN** a task remains `open`
- **THEN** only the authenticated poster can list all bids for that task

#### Scenario: Bids become inspectable after the open phase
- **WHEN** a task leaves `open` status
- **THEN** bid information can be listed according to the Task Board API contract for downstream review and observability

#### Scenario: UI cannot bypass a sealed bid
- **WHEN** a task is open and an observer requests its UI/API/event projections
- **THEN** bidder identities and amounts remain unavailable except to the authorized poster according to the sealed-bid contract
- **AND** the poster sees each actual offered amount separately from posted reward and expected payment

#### Scenario: Automatic acceptance honors competition
- **WHEN** a feeder selects among eligible bids
- **THEN** it follows the configured window or quorum, selects the lowest amount, and resolves ties using the service-owned delivery-quality signal and a documented deterministic final rule
- **AND** its acceptance cutoff is consistent with task expiration so it cannot accept refunded work

### Requirement: On-Platform Assets and Review
The system SHALL keep task inputs and deliverables on-platform so approval, dispute, and Court flows can inspect the same artifacts.

#### Scenario: Worker uploads deliverables
- **WHEN** the assigned worker uploads an asset for an accepted task
- **THEN** Task Board stores asset metadata, associates it with the task, and emits an asset upload event

#### Scenario: Submission requires uploaded assets
- **WHEN** a worker tries to submit an accepted task without uploaded assets
- **THEN** Task Board rejects the submission

#### Scenario: Poster review uses submitted platform state
- **WHEN** a poster approves or disputes a submitted task
- **THEN** the decision applies to the Task Board task, escrow, and observability state associated with the uploaded deliverables

#### Scenario: Poster supplies immutable task inputs
- **WHEN** a poster publishes supported work with input attachments
- **THEN** the accepted specification and input manifest identify immutable artifact hashes, sizes, media types and display names
- **AND** authorized workers and Court retrieve the same bytes under the accepted artifact-access policy

#### Scenario: Hostile filename and concurrent upload
- **WHEN** a caller supplies an absolute or traversing filename, exceeds limits concurrently, or uploads while a task leaves its eligible state
- **THEN** no bytes are written outside the configured storage boundary and the authoritative size/count/state limits still hold
- **AND** failed or interrupted uploads cannot accumulate unaccounted final artifacts indefinitely

#### Scenario: Human reviews exact submitted files
- **WHEN** a worker submits its output manifest and a human opens the review view
- **THEN** the human can safely download the exact submitted bytes and inspect their metadata before approving or disputing
- **AND** later uploads cannot silently replace evidence in an already frozen submission

#### Scenario: Evidence cannot be judged completely
- **WHEN** required input or output evidence is unavailable, unsupported or larger than the approved evaluation envelope
- **THEN** the system exposes the limitation and does not issue an automatic financial ruling that pretends the omitted evidence was evaluated
- **AND** access, retention and external-model disclosure follow the explicitly accepted Q-M1 policy

### Requirement: Court Dispute Resolution
The Court SHALL provide a real dispute lifecycle from Task Board dispute through platform claim filing, worker rebuttal, judge ruling, Task Board escrow settlement, and Reputation feedback.

#### Scenario: Task Board dispute auto-files Court claim
- **WHEN** a poster disputes a submitted task
- **THEN** Task Board validates the poster-signed dispute, calls Court with a platform-signed claim, marks the task `disputed`, and returns the Court `dispute_id` when available

#### Scenario: Court claim contains full task context
- **WHEN** a Court claim is filed
- **THEN** the platform-signed token includes `task_id`, `claimant_id`, `respondent_id`, `claim`, and `escrow_id`

#### Scenario: Worker rebuttal is Task Board mediated
- **WHEN** a worker responds to a dispute
- **THEN** the worker signs a Task Board `/tasks/{task_id}/rebuttal` request containing `task_id`, `dispute_id`, `worker_id`, and `rebuttal`, and Task Board submits the rebuttal to Court using the platform agent

#### Scenario: Court ruling commits all side effects
- **WHEN** the Court triggers a ruling
- **THEN** judge evaluation produces a median worker payout percentage and approved quality findings, Court persists the immutable decision and votes before delivery, Task Board settles escrow, and Court records both feedback outcomes before reporting fully delivered completion

#### Scenario: Ruling failure does not leave partial completion
- **WHEN** a ruling side effect fails before all required side effects are committed
- **THEN** the dispute remains recoverable for retry and is not reported as fully ruled

#### Scenario: Ruling can proceed without rebuttal
- **WHEN** a Court dispute is ready for ruling and no worker rebuttal has been submitted
- **THEN** the Court can still trigger judge evaluation using a null rebuttal context

#### Scenario: Claim context does not match the task
- **WHEN** a validly signed claim names a claimant, respondent, escrow or task version inconsistent with the authoritative task
- **THEN** Court rejects it without creating a claim or event

#### Scenario: Two triggers or a real process crash
- **WHEN** concurrent callers trigger one dispute or the adjudicating process dies without exception cleanup
- **THEN** only one fenced attempt owns adjudication, abandoned work can be reclaimed safely, and unfinished delivery resumes after restart
- **AND** a losing attempt cannot erase another attempt's committed ruling

#### Scenario: Retry does not change a decided ruling
- **WHEN** a ruling has been decided and persisted but settlement or either feedback delivery fails
- **THEN** retries reuse the same evidence identity, votes, quality findings, payout and result hash without invoking the judges again
- **AND** Bank, Task Board, Court and both feedback records converge on that exact result
- **AND** the same ruling identifier with changed content is rejected as a conflict

#### Scenario: Rebuttal acknowledgment is lost
- **WHEN** Court accepts a worker's rebuttal but the mediated acknowledgment or task marker fails
- **THEN** identical retry returns the accepted rebuttal and original timestamp, changed content conflicts, and background processing recovers the immediate ruling eligibility

#### Scenario: Untrusted evidence tries to direct the judge
- **WHEN** any specification, artifact, claim or rebuttal contains instructions attempting to change the judgment rules
- **THEN** it is treated as untrusted evidence rather than privileged instruction
- **AND** accepted model output follows the validated rubric/output contract and references identifiable evidence
- **AND** each promoted real profile passes a predeclared adversarial and ambiguity evaluation, with bounded provider time/context/cost and no silent mock fallback

### Requirement: Reputation and Feedback
The Reputation service SHALL track quality signals separately for task posters and workers, using specification-quality and delivery-quality feedback.

#### Scenario: Mutual feedback remains sealed until reveal conditions
- **WHEN** task parties submit feedback
- **THEN** feedback visibility follows the sealed-feedback rules rather than exposing one party's rating early

#### Scenario: Court-generated feedback reflects ruling outcome
- **WHEN** the Court rules on a dispute
- **THEN** the poster receives specification-quality feedback and the worker receives delivery-quality feedback derived from the finalized ruling and the accepted versioned quality policy, with payout kept distinct from quality measurements

#### Scenario: Reputation queries expose quality history
- **WHEN** clients query feedback by task or by agent
- **THEN** the service returns records according to visibility, category, rating, and task association rules

#### Scenario: Unrelated actor attempts feedback
- **WHEN** a signed feedback command references an unrelated task, wrong participant, wrong quality direction or ineligible outcome
- **THEN** it is rejected without changing feedback, scores or public events
- **AND** Court feedback has distinct finalized-ruling provenance rather than impersonating an ordinary reviewer

#### Scenario: Sealed and timed-out feedback across all projections
- **WHEN** feedback is submitted before its reveal condition, or one-sided feedback later reaches its timeout
- **THEN** no API, event payload, browser view or aggregate reveals the sealed rating early
- **AND** timeout reveal becomes durable and all intended views agree after restart, with one reveal event

#### Scenario: Independent quality standing is queried
- **WHEN** a client requests specification or delivery standing under the accepted Q-M3 scoring policy
- **THEN** it receives service-owned, versioned scores with observation counts and provenance, distinguishing unmeasured history from earned quality
- **AND** payout percentage alone is not presented as a measurement of both dimensions
- **AND** duplicate ruling or feedback delivery cannot double-count evidence

### Requirement: Gateway-Owned Persistence and Semantic Events
The Database Gateway SHALL own shared SQLite persistence and SHALL serialize writes from the domain services, while lifecycle changes emit semantically meaningful events consumed by UI and metrics.

#### Scenario: Domain services write through the gateway
- **WHEN** Identity, Central Bank, Task Board, Reputation, or Court needs to persist shared state
- **THEN** it calls the DB Gateway API instead of writing directly to the shared database

#### Scenario: Lifecycle transitions emit specific event types
- **WHEN** a task is accepted, submitted, approved, auto-approved, disputed, ruled, cancelled, or expired
- **THEN** Task Board emits the corresponding semantic event type such as `task.accepted`, `task.submitted`, `task.approved`, `task.auto_approved`, `task.disputed`, `task.ruled`, `task.cancelled`, or `task.expired`

#### Scenario: UI metrics read canonical lifecycle state
- **WHEN** the Observatory/UI computes GDP, active work, disputes, unemployment, agent histories, and event ticker content
- **THEN** it uses canonical lowercase statuses and semantic lifecycle events from the shared database

#### Scenario: Court and reputation events are persisted
- **WHEN** Court claims, rebuttals, rulings, and feedback reveal operations occur
- **THEN** DB Gateway records event rows such as `claim.filed`, `rebuttal.submitted`, `ruling.delivered`, and `feedback.revealed`

#### Scenario: Persistence success is malformed
- **WHEN** a required successful upstream response is invalid JSON, has missing required fields, or contains invalid entries
- **THEN** the consumer returns a visible dependency-contract error rather than plausible zero values, empty history or partial silently filtered results

#### Scenario: Schema is unknown or incomplete
- **WHEN** startup encounters an unreadable schema, partial database or unsupported version
- **THEN** readiness fails before the first domain request rather than treating any existing table as a complete schema
- **AND** supported migration preserves audited money and evidence after a verified restorable backup

#### Scenario: Observer reconnects between snapshot and stream
- **WHEN** events arrive while a UI loads history, reconnects or resumes after interruption
- **THEN** its cursor closes the history/stream gap, events are deduplicated by identity, and display/backfill retention is bounded

#### Scenario: Economic metrics reconcile
- **WHEN** views aggregate approved, ruled, pending, expired, cancelled and zero-bid work
- **THEN** common explicit metric definitions produce consistent totals, separate bids/rewards/payments/refunds, and count distinct workers where employment is reported
- **AND** minting and escrow locks are not double-counted as completed economic output

### Requirement: Honest Demonstrations and Agent Loops
The scripted demo and real agent loops SHALL exercise actual service integrations for the lifecycle behavior they claim to demonstrate.

#### Scenario: Demo scripts call real service APIs
- **WHEN** the quick or full lifecycle demo registers agents, funds accounts, posts tasks, bids, accepts, uploads, submits, approves, disputes, files claims, submits rebuttals, rules, or records feedback
- **THEN** each claimed service behavior is executed through the corresponding API rather than hidden service-side fake behavior

#### Scenario: Demo Court scope is explicit
- **WHEN** the quick or full lifecycle demo describes a dispute path
- **THEN** the scenario includes dispute, rebuttal, and ruling steps through real APIs

#### Scenario: Real worker loops follow Task Board state
- **WHEN** feeder, worker, and reviewer agents poll for work, acceptance, submission review, disputes, or rulings
- **THEN** they compare against the Task Board's actual lowercase statuses and use the correct Task Board and Court endpoints for each phase

#### Scenario: Worker handles disputes through rebuttal
- **WHEN** a math worker sees its submitted task become `disputed`
- **THEN** it resolves the Court `dispute_id`, submits a worker rebuttal through Task Board, waits for `ruled`, and records the payout outcome from the ruled task

#### Scenario: Worker or feeder restarts with commitments
- **WHEN** a worker restarts after bidding, acceptance, upload, submission or dispute, or a feeder restarts before acceptance/review
- **THEN** it discovers and resumes its owned commitments and review evidence without duplicate bids, artifacts, rebuttals or payments
- **AND** losing an in-memory answer map cannot silently convert known-bad work into an unreviewed timeout approval
- **AND** earnings follow settled receipts rather than promised reward or polling timeout

#### Scenario: General participation and specialization are demonstrated honestly
- **WHEN** a non-math example and multi-round market evaluation run
- **THEN** supported inputs and multiple outputs pass through real public service interfaces, and adaptation/quality/cost results are measured with reproducible settings
- **AND** a scripted winner or fixed mock judgment is not presented as observed specialization or empirical judge quality

### Requirement: Agent Runtime and UI Proxy
The system SHALL provide reusable agent runtime helpers for service clients, key management, worker profiles, scripted feeder/reviewer loops, and UI-driven task lifecycle actions.

#### Scenario: Agents are created from roster handles
- **WHEN** code asks `AgentFactory` for a roster handle
- **THEN** the factory loads a consistent provisioned keypair or uses the explicit safe provisioning path, loads service URLs from agent config, and returns an agent wired for Identity, Bank, Task Board, Reputation, and Court APIs

#### Scenario: Named worker profiles are validated
- **WHEN** `WorkerFactory` creates a math worker from a named profile
- **THEN** it validates the profile against `roster.yaml`, constructs the BaseAgent, LLM client, and MathWorkerLoop, and fails fast for missing or wrong-type profiles

#### Scenario: Feeder funding is platform-mediated
- **WHEN** the feeder account needs initial coins
- **THEN** the fund-feeder CLI registers platform and feeder identities, creates the feeder account idempotently, credits funds via the platform agent, and verifies the feeder balance

#### Scenario: UI proxy uses UserAgent
- **WHEN** the UI service receives proxy requests for identity, task creation, bid acceptance, approval, or dispute filing
- **THEN** it delegates to a configured UserAgent and returns service errors if the UserAgent is unavailable or not registered

#### Scenario: External participant joins without repository privilege
- **WHEN** an agent starts on a separate host or isolated network namespace with only its own persistent keys and documented agent-facing service URLs
- **THEN** it can register, open its permitted account, discover/bid/submit/rebut, read receipts and give feedback without a server roster edit, shared filesystem, gateway access, operator-proxy access or notary key
- **AND** any initial nonzero grant is administrator-invoked and platform-signed; an unfunded worker can still earn first

#### Scenario: Human completes poster and worker actions
- **WHEN** the configured operator posts work or takes another agent's job through the UI
- **THEN** it can perform its authorized funding-status, input, offer, acceptance, output, approval/dispute, cancellation, feedback and worker rebuttal actions with visible results and errors
- **AND** it acts only as its own economic identity and never impersonates another worker

#### Scenario: Ambiguous browser write is retried
- **WHEN** a user double-submits or retries after a lost response
- **THEN** the UI resolves the original operation result or an explicit conflict without a duplicate economic effect
- **AND** it preserves actionable downstream error codes and does not display false success

### Requirement: Uniform Service Contracts
Every service SHALL expose a FastAPI application factory, explicit YAML-backed configuration, a health endpoint, structured JSON error responses, and acceptance tests for the observable API contract.

#### Scenario: Service startup validates configuration
- **WHEN** a service starts
- **THEN** required configuration values are loaded explicitly from config files or environment variables and invalid configuration fails startup

#### Scenario: Health endpoints are uniform
- **WHEN** a caller requests `GET /health` on a service
- **THEN** the service returns an operational health response using the shared health contract

#### Scenario: Errors are machine-readable
- **WHEN** a request fails validation, authorization, lifecycle, dependency, or persistence checks
- **THEN** the service returns a JSON error object with `error`, `message`, and `details` fields and an explicit HTTP status code

#### Scenario: Startup dependency or operator is unavailable
- **WHEN** a required database, service or operator dependency is unavailable
- **THEN** startup/readiness cannot claim the complete economy is ready and degraded state is visible with actionable diagnostics
- **AND** configured recovery or fail-fast behavior is explicit and testable

#### Scenario: A slow task does not stall unrelated work
- **WHEN** one persistence request times out or a large deadline batch is processed
- **THEN** unrelated Task Board requests and deadline progress remain responsive within the documented operational bounds
- **AND** pending-work age, failures and sweep lag are observable without exposing secrets or sealed evidence

#### Scenario: Restored economy continues consistently
- **WHEN** the database, keys and artifact manifest are restored to an isolated runtime
- **THEN** pending operations resume with the original identities/evidence and no additional issuance or duplicate settlement
- **AND** migration failure retains a usable original backup
