# Design

## Context

The Agent Task Economy already implements its normal seven-service lifecycle, autonomous feeder and worker loops, Court integration, a vanilla-JavaScript observatory, and gateway-owned SQLite persistence. The completion work therefore repairs the current architecture rather than replacing it. The source of truth for scope is `MASTERPLAN-TO-COMPLETE-THE-PROJECT.md`: all 54 audit findings remain in scope, MP-01 through MP-26 remain intact, and ticket IDs T-110 through T-164 retain their existing meaning.

This change adds MP-00 and T-165 through T-168 for the newly reported UI CI baseline: five unit-test failures and two setup errors. T-165/T-166 cover valid UI test fixtures that omit the required `user_agent` section. T-167/T-168 cover the Playwright package being present while the matching Chromium executable is absent. These are plan items only; this design does not repair code, close tasks, or claim a full CI pass.

During implementation, root baseline attempt 4 exposed fractional-second truncation in Task Board deadline calculation. T-169/T-170 track its deterministic proof and narrow precision repair under MP-01. This calculation has no dependency on schema migration or transition ownership, so repairing it as a baseline prerequisite does not advance or bypass MP-07's atomic-transition work. Existing expiry/refund/event assertions stay intact; a longer sleep or deadline is not the fix.

The functional boundary is a real human and external-agent economy that survives retries, concurrency, lost acknowledgments, service restarts, and process restarts. A human can fund an operator, post a task with immutable inputs, compare sealed offers when authorized, select a worker, inspect deliverables, approve or dispute, and observe payment and reputation. An independently operated agent can register with its own keys, earn, post subsequent work, submit evidence, rebut a claim, and recover commitments after restart without the platform private key, database access, or a private roster entry.

The core economic rule remains: ambiguity in an accepted task specification favors the worker. The immutable accepted specification, inputs, deliverables, claim, rebuttal, ruling, settlement, reputation updates, events, and UI projections must describe the same operation and evidence versions.

The current workspace is the existing `refactor` workspace. This proposal creates no delivery branch and performs no Git history operations. During this planning request, no commits or pushes are permitted from 08:00 through 18:00 Europe/Amsterdam.

## Goals / Non-Goals

**Goals:**

- Preserve the existing service boundaries while making authority, lifecycle, money, evidence, adjudication, privacy, participation, and recovery explicit and testable.
- Give every retried external effect a stable operation identity, request hash, durable result receipt, and content-conflict behavior.
- Claim lifecycle transitions atomically before effects and reconcile pending effects independently of terminal business status.
- Version the schema, verify backup and restoration before migration, and enforce integer conservation at the authoritative persistence boundary.
- Store immutable, attributable, bounded task inputs, deliverables, Court evidence, rulings, settlement receipts, and reputation provenance.
- Protect sealed bids, sealed feedback, private artifacts, browser rendering, uploads, Host/Origin boundaries, and public event/metric projections.
- Complete supported human, external-agent, feeder, and worker journeys, including restart recovery and real browser behavior.
- Restore the UI CI prerequisite baseline through explicit valid fixtures and matching Chromium preparation without weakening production validation or trend assertions.
- Preserve tests-before-implementation governance: each implementation ticket depends on its red-proven test ticket, and all T-110 through T-170 tasks remain OPEN until their own evidence and integrated gates pass.
- Preserve the ratified local single-operator deployment, manual platform-authorized funding, fixed posted reward for v1, vanilla JavaScript UI, and Task-Board-owned settlement coordination.

**Non-Goals:**

- Replacing the seven services, SQLite gateway, local-process deployment, or vanilla JavaScript frontend.
- Adding a message broker, generic distributed transaction framework, blockchain, Kubernetes, Docker, real-money conversion, salaries, dispute fees, or a general compute marketplace.
- Adding public multi-user hosting, partial login/session support, or a privileged persistence path for external agents.
- Reopening settled v1 funding, single-operator, fixed-reward, frontend, or settlement-ownership decisions.
- Choosing Q-M1 artifact/evidence policy, Q-M2 post-v1 pricing, or Q-M3 score aggregation in implementation code before their decision records are accepted.
- Treating the supplied UI failures as fresh red proof, skipping browser tests, weakening validation, or claiming repository-wide CI success from planning work.

## Decisions

### 1. Preserve domain ownership and strengthen authoritative boundaries

Each service continues to own its domain policy. Identity and `service-auth` own identities, keys, signing, and token validation. Central Bank owns issuance, ledger, escrow policy, and financial receipts. Task Board owns task state, accepted terms, deadlines, settlement intent, and reconciliation. Court owns evidence evaluation and immutable verdicts. Reputation owns feedback visibility and score projections. DB Gateway owns schema, transactions, domain-scoped persistence commands, migrations, and authoritative persistence invariants. UI owns an operator-signed write proxy and privacy-aware read projections. Agents own public integration and restart-safe local execution state.

| Boundary | Primary modules | Required design outcome |
|---|---|---|
| Identity and credentials | `services/identity/`, `libs/service-auth/` | One durable key identity, strict typed JWS profile, explicit caller/resource authorization |
| Money and escrow | `services/central-bank/`, Gateway bank writers | Integer bounds, lifetime replay identity, exact settlement receipts, conservation checks |
| Tasks and effects | `services/task-board/`, Gateway board writers | CAS transition claims, immutable accepted terms, durable pending-effect reconciliation |
| Assets and evidence | Task Board asset store, evidence manifests | Safe names, bounded immutable bytes, hashes, access policy, complete retrieval |
| Adjudication | `services/court/`, Court persistence | Fenced leases, one evidence version, one verdict, resumable delivery without re-judging |
| Feedback and scores | `services/reputation/`, reputation projections | Participant authorization, durable reveal, independent spec/delivery dimensions |
| Human observatory | `services/ui/` | Safe rendering, operator actions, accurate metrics, cursor-based SSE reconciliation |
| External participation | `agents/`, public clients and examples | Own keys, public APIs, no private roster/notary/Gateway dependency, restart journals |
| Runtime and verification | root/service justfiles, scripts, CI and E2E fixtures | Isolated run IDs/resources, honest prerequisites, deterministic failure and journey evidence |

Rationale: ownership is already distributed by domain, while the Gateway can atomically protect the tables it owns. Moving policy into one generic coordinator would enlarge the failure surface and obscure accountability.

Alternatives rejected: a new database, broker, workflow engine, or cross-service transaction framework; direct Gateway access for agents; moving Bank settlement policy into Court or UI.

### 2. Authenticate gateway callers and authorize every domain action

Gateway commands require authenticated, domain-scoped service capabilities. Signatures establish actor identity; the receiving domain also validates action, resource, parties, expected state, and visibility. Browser writes pass through the registered operator proxy with Host/Origin checks. Browser reads and events use explicit public/operator projections rather than raw database visibility.

Rationale: signature validity alone does not authorize minting, settlement, raw persistence, feedback, or access to sealed data.

Alternative rejected: trusting network location, caller-supplied service names, or a shared platform key as sufficient authority.

MP-02 fixture reconciliation supplies explicit temporary service credentials and trust grants to existing Gateway and service fixtures. Distinct domain keys replace unsigned setup requests; original domain/status/body assertions remain. No omnipotent fixture key or production authentication bypass is permitted. Browser fixtures supply the required local Host/Origin policy and same-origin request marker. Synthetic `http://test` hosts are replaced with `http://localhost` because the supported UI mode permits only loopback browser authorities. Tests for missing/invalid origins or markers remove the corresponding valid fixture input explicitly, so they still prove their intended rejection before operator calls. Non-loopback binding and non-loopback operator requests are rejected; no partial public-login mode is introduced. The browser marker is not a secret or user authentication token.

The existing `agents/tests/e2e/test_proxy_routes.py` client fixture obtains the public marker from `/api/proxy/browser-config` and supplies its exact local Origin, matching the shipped browser helper. The original operator identity, task state and payout assertions remain unchanged. The Gateway integration fixture must load its supervisor's generated `configs/db-gateway.yaml` when `ISOLATED_RUNTIME_DIR` is present, so its distinct caller keys belong to that run rather than the ordinary repository keystore.

Root attempt 8 exposed the same pre-MP-02 setup contract in `tests/integration/gateway_helpers.py`: 15 cross-service cases fail on missing `auth` before executing their assertions. Reconcile only this shared helper with per-client temporary keys/trust and per-domain signed requests; retain all original cross-service database/event assertions. Cleanup must restore temporary configuration, settings caches and resources after both setup and body failure. The fixture must not introduce a shared all-domain credential or load ordinary key material.

Root attempt 9 passed through the adjusted cross-service/runtime phase, then 168 browser cases failed because Playwright string-expression polling invokes `eval` under the new CSP. Reconcile only those `wait_for_function` arguments to explicit JavaScript functions returning the exact same predicates. Retain their conditions, timing, arguments and all assertions. Do not add `unsafe-eval`, bypass the browser CSP or suppress errors. A separate browser control must prove runtime eval remains blocked while a function predicate runs successfully.

The frontend page fallback is part of the same operator-origin boundary. Six temporary-file red cases demonstrated that encoded traversal, absolute request paths and symlinked page/index/asset roots can render files outside `frontend.web_root`. Resolve the configured root and require requested pages, fallback index and mounted asset directories to remain contained within it. Out-of-root requests return 404; regular static pages and ordinary unknown-route index fallback remain supported. This is independent of MP-09's upload storage and artifact-access policy, which remain open.

### 3. Provision keys once and validate one strict token profile

An explicit bootstrap owner creates platform keys under an exclusive claim, publishes the pair atomically with restrictive permissions, validates pair consistency, and fails on missing halves or corruption. Consumers load provisioned keys. Local and remote verification share typed rules for protected headers, algorithm, payload object shape, expiry, actor, action, and resource claims.

Rationale: concurrent first-run generation or permissive parser differences can split platform identity and authorization semantics.

Alternative rejected: silent regeneration, process-local key generation by every service, or parser-specific fallback behavior.

MP-03 fixture reconciliation is limited to valid key-consumer setup. `agents/tests/unit/test_factory.py` and `test_worker_factory.py` currently pass empty temporary key directories to factories while asserting returned types, names and stable identities; they do not assert automatic key creation as a product requirement. Their setup must explicitly provision the required temporary roster identities before calling load-only consumers, preserving all existing behavior assertions. New independent tests must prove that missing, partial, mismatched or insecure production key material is rejected without regeneration. This fixture change does not authorize a production fallback or permit bypassing pair validation. The isolated runtime must likewise provision its disposable roster before starting consumers; ordinary production startup remains load-only.

Task Board's required platform configuration needs the same valid-fixture reconciliation in `tests/unit/routers/conftest.py`, `tests/integration/conftest.py`, `tests/unit/test_deadline_evaluation_config.py`, `tests/unit/test_config.py` and `tests/unit/test_response_contracts.py`. Supply an explicit temporary agent configuration and pre-provisioned keys wherever lifespan runs, with controlled registration dependencies; supply the required path in configuration-only fixtures. The existing missing-deadline test must still reach its intended missing-deadline failure. No existing endpoint, signing, deadline or response assertion may be weakened. New production-path tests separately require failure on missing/damaged credentials and prove that no private-only fallback is created.

The same setup-only reconciliation applies to Central Bank `tests/unit/test_config.py`, `tests/unit/routers/conftest.py`, `tests/unit/routers/test_two_tier_auth.py`, `tests/integration/conftest.py`; Reputation `tests/unit/routers/conftest.py`, `tests/unit/conftest.py` (its existing persistence-helper adapter), `tests/unit/test_persistence.py`, `tests/integration/conftest.py`; and Court `tests/unit/test_config.py`, `tests/unit/test_wp06_config.py`, `tests/unit/routers/conftest.py`, `tests/integration/conftest.py`. Supply the now-required credential configuration and provision temporary pairs before valid startup. Registration dependencies may be controlled in isolated fixtures, while independent production-path negatives must fail on missing/damaged keys. Existing authentication, persistence, health, zero-balance and no-mint assertions remain unchanged. Existing UI fixtures already provide their credential fields. Central Bank's signer-gap tests reuse the reconciled two-tier fixture; they need no assertion changes.

Demo replay must also use the canonical pair loader. Its tools configuration supplies a required `platform.platform_key_path`, resolved relative to the tools configuration file; the configured filename must match the platform handle's `.key`/`.pub` pair. The prior hardcoded repository private-key path is removed. Valid setup in `tools/tests/unit/demo_replay/test_config.py` and `test_engine.py` gains this explicit path, preserving every existing assertion and the intended missing-URL/extra-field causes. Independent new tests cover missing/mismatched/insecure pair rejection, configured-path selection and missing credential configuration.

The existing UI observatory browser fixture in `services/ui/tests/e2e/conftest.py` and the auxiliary server helper/import setup in `test_cross_page.py` also require explicit disposable operator credentials. Both must directly use the shared controlled startup helper, with no obsolete ordinary-key path retained behind a collection-time monkeypatch. They must never load the ordinary repository key directory or register a fixture identity against ordinary running services. The test-server process may control only the registration dependency while retaining the real pair loader, and must keep the existing seeded observatory database and all browser assertions. Startup failures retain logs and reap the owned server; held sockets are closed even on pre-launch setup failure, and the child clock mode is explicit rather than inherited. This remains observatory/render coverage; it is not substituted for the real human/economy write-journey gate.

The MP-03 token interface is fixed before its implementation as follows. The compact Ed25519 JWS protected header contains exactly `alg`, `typ`, `kid`, `iat`, `exp`, `aud` and `resource`. `alg` is `EdDSA`, `typ` is `JWT`; `kid` is a non-blank registered identity. `iat`/`exp` are JSON integers excluding booleans, with `exp > iat` and lifetime bounded by explicit policy. Audience and concrete `METHOD /path` resource are protected header metadata; the signed payload remains the existing object containing `action` and domain fields. This preserves domain payload contracts instead of stripping authentication fields out of business data.

Shared verification returns a typed `VerifiedJws` containing a typed protected header and object payload. It verifies the original compact bytes, never reserializes before signature checking. Receivers compare the authenticated header audience/resource and payload action with constants and route parameters they own before any domain effect. Local platform verification also requires the registered platform `kid`. There is no payload-only compatibility wrapper or production no-expiry mode.

Unauthenticated parsing validates wire structure and returns `ParsedJws`, without validating the domain action. Shared verification checks the signature and temporal policy before requiring a valid action and returning `VerifiedJws`. Thus bad signatures and authentic temporal failures precede malformed-action errors. Issuers and Identity claimed-success parsing still require a valid action. Identity action-error fixtures must use registered signing keys so an unrelated unknown-key lookup cannot stand in for the intended check.

The shared `TokenPolicy` has required positive `max_lifetime_seconds` and nonnegative `clock_skew_seconds`. At the configured clock, `iat > now + skew` is not yet valid and `now >= exp + skew` is expired. The initial explicit deployment values remain a 300-second signer lifetime, a 300-second admission maximum and 30 seconds of allowed skew; these are YAML configuration, not code defaults. Tests use explicit policy values and controlled time.

The agent YAML `signing` section requires `token_ttl_seconds`, `max_lifetime_seconds` and `clock_skew_seconds`. Runtime `AgentConfig` requires a positive `token_ttl_seconds` and a validated `token_policy: TokenPolicy`, with issuer lifetime no greater than its admission maximum. Identity separately requires `token_validation.max_lifetime_seconds` and `token_validation.clock_skew_seconds`. Both agent configuration entry points use the same typed file model. The demo issuer requires its explicit positive `platform.token_ttl_seconds`; it does not invent receiver policy defaults.

An action-only payload is valid when its subject is bound in the resource path, as with balance/history reads; no second domain field is invented. A missing or wrong-type action retains `400 invalid_payload`, while a non-object body or malformed compact/header representation is `400 invalid_jws`. Domain fields named like authentication metadata never authorize a target: only the verified protected header supplies audience/resource. Identity response binding preserves JSON value types, so boolean/integer/float substitution cannot compare as an unchanged signed payload.

Identity's successful wire result adds `protected_header` to `valid`, `agent_id` and `payload`; `agent_id` must match the header `kid`. Signature mismatch remains an explicit invalid result. The shared Identity client validates the complete successful response and returns `VerifiedJws`; malformed upstream success is an unavailable/invalid-upstream error, never a fabricated valid payload. Admission mapping is: malformed profile `400 invalid_jws`, expired `401 token_expired`, future issue time `401 token_not_yet_valid`, bad signature or wrong platform identity `403 forbidden`, and authenticated wrong audience/resource/action `400 invalid_payload`. Actual Identity transport/response failure alone uses the upstream-unavailable path.

Legacy success assertions for missing expiry conflict with the requested breaking profile. After independent raw-signature red proofs, their explicit contract reconciliation must strengthen them to exact rejection/no-side-effect assertions; they cannot be deleted, skipped or retained as a production bypass. Ordinary happy-token fixtures must emit the required profile. Authentication tests exercise real signing/verifying; domain-only mocks may supply a complete typed verified result but are not authentication proof. These interface changes are engineering details of MP-03 and do not decide Q-M1, Q-M2 or Q-M3.

### 4. Use stable operation identities, request hashes, receipts, and content conflicts

Every money or workflow command that may cross a process boundary carries a stable caller-derived operation ID. The authoritative store persists the normalized request hash and complete result receipt atomically with the state change. An exact replay returns the stored result; the same identity with different content returns an explicit conflict. Escrow lock identity remains unique for the task and payer for the escrow lifetime.

Rationale: retrying with a new transaction ID cannot recover a committed response that was lost, and generic `409` interpretation cannot prove equivalence.

Alternative rejected: treating “already resolved” as success, relying on in-memory deduplication, or deriving recovery from current task status alone.

### 5. Claim transitions before effects and reconcile terminal work

Interactive and deadline transitions use the same compare-and-swap contract: expected state/version, database time predicate, authorized actor, and one winning claim. The winning operation persists its effect intent before calling another service. A durable reconciler retries pending posting, settlement, Court delivery, feedback, and event work even when the public task status is terminal. Business status never doubles as proof that all effects completed.

Rationale: approve/dispute, accept/cancel, submit/expire, and competing ruling triggers must produce one serial outcome and recover after lost acknowledgments.

Alternative rejected: process locks, unconditional status updates, GET-triggered recovery as the only mechanism, or exception cleanup as the recovery record.

### 6. Version schema changes and establish backup proof before migration

The Gateway uses a schema-version table and ordered transactional migrations. Startup validates the exact required schema, indexes, foreign keys, integrity checks, and supported version. MP-06 first supplies a tested database/WAL backup, asset manifest, and key-backup foundation; MP-21 later proves complete restore and pending-work recovery. Monetary columns enforce integer type, non-negativity, explicit configured bounds, payer/recipient correctness, and ledger-to-balance provenance.

Rationale: “any table exists” is not readiness, and financial migrations require a recoverable original snapshot.

Alternative rejected: opportunistic DDL at request time, silent partial-schema startup, or relying only on client-side validation.

MP-06 recovery wiring adds required `recovery.maintenance_lock_path` to Gateway, Task Board and UI; Gateway also declares `recovery.backup_root` for explicit recovery commands. Their maintenance path is config-parent-relative, converted to an absolute lexical path without resolving symlinks. The parent directory must already exist. All three services hold the shared lock before protected resource initialization through shutdown, including startup failure cleanup. The isolated runtime supplies one run-owned path with an existing parent. Existing valid fixtures may add these explicit temporary recovery values, preserving every behavior assertion; missing-recovery negatives are additional cases. Ordinary data and processes are not used for validation.

The concrete neutral recovery inventory distinguishes filesystem shape from capture intent: copy files/trees, capture declared keypairs through the public batch API, render captured configs, or create an empty target directory (logs/recovery roots). Keypair records explicitly identify economic versus Gateway roles and private/public resources; trust is never inferred from a logical ID. Process records retain PID/group/session and observed lifecycle facts but confer no signaling authority or independent proof of quiescence. Actual owned-process teardown and the maintenance lock remain required. See the detailed reviewed backup design in evidence/mp06-backup-design.md.

### 7. Freeze immutable evidence, accepted terms, and rulings

Task publication freezes specification, reward contract, deadlines, and input manifest. Acceptance freezes the selected bid and worker. Submission creates an immutable artifact manifest and revision. Court persists the complete admissible evidence bundle and evidence hash before judgment. A fenced, expiring lease admits one judging owner. The final verdict and version are stored before downstream delivery; retries deliver the same verdict to settlement and both reputation dimensions without invoking judges again.

Rationale: model nondeterminism and partial delivery make re-judging unsafe; incomplete evidence cannot support a financial ruling.

Alternative rejected: mutable file paths, reconstructing evidence from latest rows, holding ownership only in memory, or rerunning the panel after a delivery failure.

### 8. Enforce privacy, safe rendering, and bounded uploads at every projection

Sealed bids and feedback remain absent from unauthorized API fields, event payloads, aggregates, metrics, and rendered text until their reveal condition. UI rendering treats stored content as text and uses safe download behavior. Upload names are normalized and contained within the asset root; MIME/extension do not grant execution, previews are restricted, and count/size limits are claimed atomically under concurrency. Artifact access follows Q-M1 once decided.

Rationale: a private database field is still leaked if copied into SSE, metrics, HTML, or a public download route.

Alternative rejected: frontend-only hiding, raw HTML interpolation, trusting filenames, or public-by-default evidence without an accepted policy.

### 9. Authorize feedback by participation and gate scoring on Q-M3

Only verified task participants and Court delivery can create feedback for the relevant task and role. Paired and timed reveals are durable and use one visibility rule across API, UI, events, and scoring. Raw feedback and Court findings retain source/outcome/version provenance. Specification quality and delivery quality remain independent projections with sample counts and uncertainty; aggregation is not implemented until Q-M3 is accepted.

Rationale: payout share cannot truthfully encode both dimensions, and unrelated actors must not influence reputation.

Alternative rejected: complementary percentages as quality scores, immediate neutral-looking scores for identities with no observations, or duplicated scoring formulas in UI and feeder code.

### 10. Complete human, external-agent, and restart journeys through supported interfaces

The UI supports operator balance/history, funding status, posting with inputs, offer comparison, acceptance, safe deliverable review, approve/dispute/cancel/feedback, and a “My work” path in which the operator acts as its own worker identity. External agents use documented public constructors or CLI surfaces, their own keys, configured public service URLs, zero-balance account opening, discovery, assets, bids, submission, review, dispute/rebuttal, feedback, and authorized administrator grants for new posters.

Worker and feeder runtimes reconcile remote commitments at startup and periodically. Durable local journals retain operation IDs and execution/review checkpoints. Earnings come from settlement receipts. Independent schedules prevent one failed task from stalling posting, acceptance, review, rebuttal, or discovery.

Rationale: supported participation cannot depend on repository-local files, private signing methods, a notary key, the operator proxy, or database edits.

Alternative rejected: demo-script completion, privileged onboarding, or volatile dictionaries as the only commitment record.

### 11. Build the UI from persisted semantics and cursor-based events

Metric definitions state denominator, distinct-actor rules, time window/time zone, terminal categories, and whether a value is reward, bid, payment, refund, locked escrow, or issuance. Snapshot responses include a high-watermark. SSE resumes from a stable cursor or `Last-Event-ID`, supports bounded backfill, deduplicates stable event IDs, and preserves deterministic ordering. Pending and settled effects remain distinct.

Rationale: reconnect must converge without replaying history as new activity or missing events between snapshot and subscription.

Alternative rejected: timestamp-only cursors, client-side guessed totals, and treating all terminal states as one “completed” bucket.

### 12. Isolate runtime and make verification prerequisites explicit

Disposable verification uses explicit run IDs, ports, data/assets/keys/config paths, browser cache, and owned process handles. Normal local use retains documented ports. Tests use deterministic clocks, barriers, response-loss hooks, process kills, and real gateway/browser paths. Shared clients reject malformed success responses instead of substituting plausible empty or zero values.

Rationale: worktrees do not isolate fixed ports or runtime data, and a green command is not evidence if earlier failures were masked or prerequisites were missing.

Alternative rejected: broad process killing, shared runtime directories, sleep-based race tests, or successful defaults for malformed upstream responses.

### 13. Repair the UI CI baseline before the dependency waves

MP-00 precedes the existing waves. T-165 captures the four valid fixture paths that fail because `user_agent.agent_config_path` and `user_agent.handle` are absent, while preserving six missing-section negative cases, partial-block behavior, and a focused extra-field rejection. Before T-166 edits acceptance inputs, the test specification records that this is a narrow fixture-only correction. Valid fixtures then use explicit temporary values and stub factory/registration boundaries where required; `CONFIG_PATH`, caches, and app state are restored on success and failure. Production `user_agent` remains required.

T-167 reproduces the missing matching Chromium executable in an isolated cache before the increasing, decreasing, and stable trend assertions. T-168 adds a documented `uv run playwright install chromium` path and an early launch preflight aligned with the locked Playwright package for local and hosted CI. Missing Chromium is an actionable nonzero prerequisite error. All three trend assertions continue to execute real `shared.js`; none are skipped or weakened.

Rationale: fixture validity and browser installation are separate failures and require separate red/green evidence.

Alternative rejected: production defaults, global fixture backfills, skipped browser tests, assertion changes, or interpreting the historical user log as the required fresh red run.

### 14. Sequence work by dependency waves

| Wave | Packages | Exit condition |
|---|---|---|
| Preflight | MP-00 / T-165–T-168 | Explicit valid fixtures, negative config checks, and real-browser trend tests pass; root closure remains pending |
| 0 | MP-01; begin MP-26 inventory | Isolated stack and protected baseline |
| 1 | MP-03 → MP-02; MP-06 | Durable credentials, gateway authority, schema version, backup foundation, financial constraints |
| 2 | MP-04 → MP-05; MP-07 → MP-08; MP-09 | Receipts, atomic claims, recoverable posting/effects, safe immutable artifacts |
| 3 | MP-11 → MP-10; MP-12 → MP-13 → MP-14 | Complete evidence, one recoverable ruling, sealed projections, authorized feedback, decided scores |
| 4 | MP-15; MP-16 → MP-17/MP-18; MP-19; MP-20 | Complete human/external participation, restart recovery, accurate UI, strict clients |
| 5 | MP-21; MP-22; MP-25 → MP-23; functional MP-26 | Restore/startup proof, hostile histories, real Court profile, complete Core journeys and full gate |
| 6 | MP-24; Vision reruns of MP-25/MP-23/MP-26 | Decided pricing/reputation policy and measured non-math economic demonstration |

MP-20 response schemas are designed in wave 1 and land incrementally with producers. MP-25 corpus authoring may start early, but live evaluation waits for MP-11. Arrows express technical dependencies rather than permission handoffs.

### 15. Keep major unresolved policies as explicit BLOCKING gates

- **Q-M1 / T-162 — artifact admissibility, visibility, retention, and model-data policy: BLOCKING.** An accepted ADR with supported-format table, access matrix, failure/appeal behavior, retention/redaction rules, bidder-input access, and external-provider treatment is required before MP-09 policy and MP-11 can close. It also gates relevant MP-15 downloads and MP-25 corpus use. Safe storage and traversal repairs may proceed.
- **Q-M2 / T-163 — post-v1 price formation: BLOCKING for the Vision gate.** Fixed posted reward and signal-only bids remain settled for Core. An accepted economic ADR with approve/cancel/expire and 0/partial/100% dispute examples, rounding, excess-refund timing, and migration treatment is required before the price-forming branch of MP-24 or claims of a functioning price market.
- **Q-M3 / T-164 — quality aggregation: BLOCKING.** An accepted versioned scoring/output contract with no-history, sample-size, provenance, peer/Court weighting, precise-poor work, vague-adequate work, disagreement, replay, and malicious-feedback fixtures is required before MP-11/MP-14 can close and before reputation-driven specialization claims in MP-24.

Q-M1 and Q-M3 block the functional/Core gate. Q-M2 blocks only the full economic-vision gate. None blocks immediate authority, transaction, recovery, safe-storage, onboarding, isolation, or UI-baseline work. No implementation may silently choose these policies.

## Risks / Trade-offs

- **[Cross-service contract rollout can create mixed-version failures] →** version commands and responses, land authoritative producers before consumers, reject unknown versions, and use isolated compatibility tests during each wave.
- **[Durable operation records add storage and reconciliation complexity] →** keep records domain-specific, bounded, indexed, observable, and prunable only after their audit/recovery horizon.
- **[CAS claims can expose more conflicts to clients] →** return stable conflict codes and current state/version so UI and agents can reconcile without repeating effects.
- **[Schema rebuilds can endanger financial history] →** take and verify a database/WAL plus asset/key backup before migration, run conservation/integrity checks, and retain the original snapshot until restore proof passes.
- **[Fenced leases may stall after process death] →** use database time, explicit expiry, ownership tokens, bounded renewal, and safe reclaim tests; a persisted verdict is never recomputed.
- **[Privacy policy is unresolved] →** ship storage, traversal, authorization, and sealed-projection protections first; do not expose or adjudicate unsupported artifact classes until Q-M1 is accepted.
- **[Score changes can misstate trust] →** retain raw provenance and algorithm version, show counts/uncertainty, and block aggregation rollout until Q-M3 fixtures are accepted.
- **[Real judge evaluation has cost and provider variability] →** use bounded opt-in runs, frozen profiles and corpora, predeclared thresholds, retained anonymized outputs, and recoverable pending status on provider failure.
- **[Browser prerequisites can drift from the locked package] →** install and launch-check Chromium through the same environment before unit tests and fail early with the exact remediation command.
- **[Isolation work can diverge from normal local operation] →** make all disposable values explicit while preserving documented ordinary ports and commands.
- **[A complete plan may be mistaken for a complete product] →** keep every task OPEN, record red/green/full-gate evidence separately, and prohibit completion language until fresh isolated root `just ci-quiet` and release journeys pass.

## Migration Plan

1. Record this OpenSpec change and keep MP-01–MP-26, T-110–T-164, and all 54 findings stable. Add MP-00 and T-165–T-168; track the subsequently reproduced independent deadline precision baseline as T-169/T-170 without renumbering existing work.
2. Execute T-165 and T-167 first to obtain fresh isolated red proof. Record the narrow fixture-only correction in the UI test specification before changing valid acceptance inputs. Preserve every missing-section negative test, partial-block case, health/config/lifespan assertion, and all three trend assertions.
3. Implement T-166 and T-168, then run the focused UI unit tests, complete UI unit suite, and UI CI. Do not close MP-00 until isolated root `just ci-quiet` also passes later.
4. Complete MP-01 runtime isolation and baseline capture before any destructive full-stack verification. Allocate explicit runtime resources and preserve active user data and unrelated processes.
5. In wave 1, provision durable keys, enforce gateway capabilities, create schema versioning, take and verify backups, and add financial constraints. Unknown or partial schemas fail readiness.
6. In wave 2, migrate operation/receipt tables and immutable task/evidence manifests, then enable stable replay, CAS transition claims, and terminal reconciliation. Reconcile historical duplicate escrow identities before adding lifetime uniqueness.
7. In wave 3, resolve Q-M1 and Q-M3 at their gates, deploy privacy projections and participant-authorized feedback, persist evidence/verdicts before delivery, and publish versioned score projections.
8. In wave 4, migrate consumers to strict response contracts and complete UI, SDK, worker, feeder, metric, and SSE behavior. Run old and new projections in comparison where needed, but keep only one authoritative writer for each domain.
9. In wave 5, prove backup/restore, readiness, hostile concurrency/crash schedules, the supported three-judge profile, full human and autonomous journeys, and a clean isolated root `just ci-quiet` result. MP-26 records exact evidence and keeps unsupported claims open.
10. In wave 6, resolve Q-M2 and rerun Vision evidence. Existing accepted tasks retain their frozen v1 contract terms through any later pricing migration.

Rollout is package-by-package behind versioned schema/command contracts. Each package requires a failing test first, targeted green evidence, affected component CI, cross-service checks, and finally isolated root CI. Red-proven status unblocks implementation but does not close either ticket. No task closes from an old run, fixture name, mock-only proof, or planning statement.

Rollback restores the verified pre-migration database/WAL, asset manifest, key material, configuration, and compatible service versions together. Stop writers before restore, verify hashes and schema version, run integrity and conservation checks, then restart reconciliation with the original operation identities. Never roll back by deleting receipts, rerunning judgment, regenerating keys, changing accepted task terms, or issuing compensating money without recorded provenance.

The stop condition for this design phase is the complete proposal/design/spec/tasks artifact set with all implementation and decision work still OPEN. The implementation stop condition is stricter: no required unchecked task, unresolved blocking decision, failing Core/Vision gate applicable to the claimed release, unsupported completion claim, or missing fresh full-CI evidence remains.
