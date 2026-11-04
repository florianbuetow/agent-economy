# UI work: make Agent Economy intuitive to use

## Goal and authority

Improve the UX of the existing Agent Economy so people can understand what is
happening, find the right action, complete a job-related task, and recover from
ordinary mistakes without knowing the service architecture.

This is a usability rework, not a redesign of the underlying services. Visual
changes must support comprehension, navigation, readability, or task completion.
A prettier screenshot alone does not satisfy this goal.

This document is the execution brief requested by the owner. Creating this brief
does not itself start implementation. Its checklist tracks the work sequence;
the canonical issue tracker remains
`openspec/specs/completion-backlog/spec.md`. Follow `AGENTS.md` and delivery
governance for implementation tickets, failing tests, review, and publication.
Do not treat this as permission to reopen completed backend work in `GOAL.md`.

## Model and design guidance

Use GPT-6 Astra with the installed Impeccable skill for the UX evaluation and
rework. Read the installed skill before execution; use its product/application
guidance, rather than treating the application as a marketing site. Follow its
context setup using the existing repository and this brief. Infer routine
decisions from those sources instead of requiring another product interview.

Use critique to locate problems, shape to choose the simplest coherent solution,
and audit/polish to verify the result. These are bounded activities, not a mandate
to invoke every skill command or create an elaborate design process. Do not
install or update skills as part of this work. If Astra is unavailable, report
that specifically rather than silently switching models.

Preserve useful existing visual conventions. Do not impose Scandinavian styling,
a new theme, or a brand overhaul just because another skill is available.

## Existing surfaces and implementation boundary

Inspect the running application as well as its source before proposing changes.
These are starting points, not a requirement to rewrite every file:

| Surface | Current source | UX responsibility |
| --- | --- | --- |
| Entry and posting | `services/ui/data/web/index.html`, `assets/landing.js`, `assets/task-posting.js` | Explain the product, expose useful entry actions, guide posting |
| Work and review | `services/ui/data/web/task.html`, `assets/task.js` | Explain job state, roles, files, deadlines, and available actions |
| Agent details | `services/ui/data/web/agent.html`, `assets/agent.js` | Make identity, reputation, and work history understandable |
| Monitoring | `services/ui/data/web/observatory.html`, `assets/observatory.js` | Make activity and status readable and navigable |
| Reports | `services/ui/data/web/quarterly-report.html`, `assets/quarterly.js` | Preserve access to reports and explain existing metrics |
| Shared presentation | `services/ui/data/web/assets/style.css`, `assets/shared.js` | Consistent navigation, type, spacing, feedback, and controls |

Asset paths in the first five rows are relative to `services/ui/data/web/`.
That directory contains tracked frontend source despite its `data` path; it is
in scope. Runtime databases, keys, uploads, and generated data are not.

The main implementation scope is the existing HTML, CSS, browser JavaScript,
UI tests, and directly relevant documentation. Keep the existing framework and
public interfaces. Read UI routers, schemas, and proxies to understand contracts;
do not change their behavior to make a design convenient. If a demonstrated UX
need truly requires a service/API change, record the exact limitation and bring
that scope decision to the owner. Continue independent frontend work meanwhile.

## User journeys to improve

Evaluate the following against the actual shipped capabilities. Do not invent
new capabilities when improving how existing ones are presented.

| Journey | What the interface must make clear | Validation outcome |
| --- | --- | --- |
| Arrive and find work | What this product does; where to post, browse jobs, and inspect activity | Relevant destinations can be found from visible navigation without guessing URLs |
| Post a job | Required specification, inputs, reward, deadlines, validation, and the consequence of publishing | A valid job with an input file is posted; a correctable error retains entered information |
| Select a worker | Bids, bidder identity, offered price, and what acceptance commits to | Poster can inspect bids and accept one through the existing flow; accepted terms are visible |
| Work on a job | Current acting identity, eligibility, input files, next action, and deadline | Worker can bid, access accepted inputs, upload output, and submit using visible controls |
| Review work | Submitted files, review window, approval/payment consequence, and dispute path | Poster can inspect output and approve or use the existing dispute flow |
| Track an outcome | Current state, who acts next, completed actions, payment outcome, and dispute/ruling information when available | User can distinguish waiting, action required, success, and failure without reading raw events |
| Inspect an agent | Display name and identity, reputation meaning, related jobs, and a route back | Agent history and associated tasks can be navigated without losing orientation |
| Recover from interruption | Whether an action is pending, confirmed, failed, or needs recovery | Existing retry/recovery behavior is understandable and never presented as success prematurely |

Do not require a new account system, role switcher, or impersonation feature.
Reflect the current operator/agent identity and existing authorization accurately.
An unavailable action must have a comprehensible explanation where relevant;
do not display controls that imply permission the user does not have.

## Ordered todos

- [x] **Establish the baseline.** Read current UI code, relevant API contracts,
  existing browser tests, and the completion evidence. Walk the journeys above
  with realistic disposable data. Capture representative desktop and narrow
  viewport screenshots. Record observed problems separately from hypotheses.
- [x] **Prioritize actual friction.** For each finding, record the page/state,
  user goal, confusing or obstructive behavior, proposed change, and observable
  acceptance condition. Prioritize blocked or misleading actions, then unclear
  navigation/state, then readability and consistency. Do not manufacture a quota
  of findings or redesign surfaces that already work well.
- [x] **Choose a coherent interaction structure.** Make primary actions easy to
  find; group supporting information by the user's task. Keep status, next step,
  relevant deadline, and important terms together. Use progressive disclosure
  for technical detail while retaining access to it. Record concise decisions
  in existing design documentation if present, otherwise in this document.
- [x] **Clarify navigation and language.** Use consistent names, meaningful page
  headings, recognizable links, and action labels that describe the result.
  Explain economic terms where users need them. Keep browser back, refresh, and
  existing deep links useful. Avoid replacing domain accuracy with vague copy.
- [x] **Improve forms and action feedback.** Provide labels, examples only where
  helpful, visible required fields and units, local error messages, and clear
  pending/success/failure feedback. Preserve entered values after correctable
  errors. Explain consequential actions without adding confirmation dialogs to
  every ordinary action. Preserve existing intent and duplicate-request guards.
- [x] **Improve task and file comprehension.** Distinguish inputs from submitted
  outputs; expose existing preview/download controls clearly. Make uploader,
  filename, and relevant file state understandable. Distinguish offered reward,
  accepted price, and actual payout rather than collapsing them into one amount.
- [x] **Improve readability and accessibility.** Refine spacing, type, contrast,
  focus, responsive layout, and semantic structure where the audit shows a need.
  Reduce distracting decoration when it interferes with tasks. Keep useful
  information density; do not turn a working tool into an empty presentation.
- [x] **Implement in bounded slices.** Create the required test/implementation
  ticket pairs before coding. Reuse existing tests; add the smallest missing
  failing behavior check for each defect. Integrate one journey or shared pattern
  at a time and validate it before proceeding.
- [x] **Validate and independently review.** Run the browser journeys and checks
  below. Have a bounded independent reviewer inspect the changed UI in context,
  scope boundaries, and evidence. Resolve concrete findings; avoid endless review
  loops or speculative improvements.
- [x] **Close out accurately.** Record before/after evidence, commands and exits,
  ticket status, actual remaining limitations, and changed files. Commit and push
  only during allowed Git hours; report publication separately from UX completion.

## Don'ts: hard scope boundaries

- Do not redesign services, move business logic into the browser, change database
  schemas, introduce endpoints, or refactor shared libraries.
- Do not alter task states, bidding rules, deadline rules, settlement, escrow,
  reputation, dispute policy, authorization, signatures, or idempotency semantics.
- Do not change Court models, prompts, worker models, or agent runtime behavior.
- Do not add features such as chat, notifications, new analytics, discovery
  streams, new account management, or new persistence merely to support a mockup.
- Do not replace the frontend stack, add a component framework, introduce build
  infrastructure, or add dependencies without a separate explicit decision.
- Do not require another LAN machine, SSH, new hardware, Docker, Kubernetes,
  hosting, TLS, or expanded network exposure for this UX task.
- Do not expose private keys, storage paths, hidden fields, or additional data
  through presentation changes. Keep existing privacy and access boundaries.
- Do not remove supported actions, histories, file access, or important details
  to make a screenshot simpler. Do not hide required information behind obscure
  icons, hover-only interactions, or unexplained abbreviations.
- Do not show fake activity, fabricated metrics, invented payment outcomes, or
  optimistic success that contradicts authoritative state.
- Do not add decorative animation, imagery, oversized headings, or a new color
  scheme without a concrete usability reason. Do not chase a design-award look.
- Do not treat CSS cleanup, wholesale file rewrites, or a generic design-system
  project as deliverables. Extract shared patterns only where the changes need them.
- Do not edit ordinary runtime data, destroy user sessions, or stop unrelated
  services to test. Use the existing disposable runtime for mutations and failures.
- Do not weaken assertions, skip failing checks, fake tools, change PATH, or claim
  usability from a green test suite alone. Do not claim real-user testing occurred
  when only the agent performed a walkthrough.
- Do not ask the owner to supervise routine design choices, debug the UI, or
  perform validation. Use judgment inside this scope. Ask only for a material
  product or service change that this brief does not authorize.

## How to validate the work

### 1. Prove the UX improvement

For each accepted finding, record a short before/after comparison: user task,
initial obstacle, implemented change, and what can now be done or understood.
Use the same representative data and viewport for comparison. Where relevant,
record unnecessary steps removed, ambiguous labels clarified, or information
previously separated that is now visible at the decision point. Do not invent
numeric usability scores, arbitrary click quotas, or claims about user speed.

Walk each affected journey from its entry page using visible controls and the
page's instructions, without developer explanations, direct URL shortcuts, or
console commands. At each significant state, check that the page communicates:
where I am, what happened, what I can do next, and what that action will do.
This is an agent usability walkthrough, not evidence from recruited users.

### 2. Exercise real behavior and representative states

Reuse `scripts/isolated_test_runtime.py` and existing Playwright/browser fixtures.
Use actual UI actions against disposable services for the poster and worker
journeys. Fixture or mocked browser tests can exercise visual/error states, but
cannot replace real-service evidence for posting, submission, and payment.

Verify a job with inputs through posting, bid acceptance, output submission,
review, approval, and payment. Separate actors may be driven by the existing
test harness where necessary; do not add UI impersonation to run the test.
Verify the existing dispute/rebuttal/ruling presentation and affected controls
with existing fixtures/tests. Reuse established Court evidence if its unchanged
behavior is not under test; do not start a new model evaluation project.

Check these states on the changed surfaces where applicable:

- Empty lists, populated lists, long names/specifications, and multiple files.
- Loading, unavailable data, validation errors, and a recoverable network failure.
- Pending and confirmed actions, refresh/recovery, and repeat clicks on mutations.
- Actions available to the current actor versus actions unavailable to that actor.
- Browser back and direct navigation to an existing task or agent link.

Confirm authoritative task state and exact input/output retrieval, and reuse
existing assertions for accepted-price settlement, conserved balances, and no
duplicate effect after retry. Do not create an exhaustive new fault matrix.

### 3. Check the rendered UI

Inspect actual browser screenshots at 1440 x 900, 768 x 1024, and 390 x 844,
plus desktop at 200% zoom. These are representative checks, not a demand for a
new device lab. Fix clipped controls, unreadable text, hidden primary actions,
overlapping content, and unintended page-wide horizontal scrolling. A wide data
table may scroll within a clearly usable container.

Complete affected forms and dialogs with the keyboard. Verify visible focus,
logical focus order, associated labels, understandable errors, and focus return
when dialogs close. Do not rely on color alone for status. Check text contrast
(4.5:1 for normal text; 3:1 for large text) and meaningful control/focus contrast.
Respect reduced-motion preferences. Check console/network output for newly
introduced errors; screenshots alone cannot prove interaction correctness.

### 4. Run the existing engineering gates

Read `.agents/skills/testing-and-debugging/SKILL.md` before changing code or tests.
Inspect current fixture setup and recipes before running checks. Existing entry
points include:

```sh
# From services/ui; select the tests for the changed journeys.
uv run pytest tests/e2e/test_landing.py tests/e2e/test_task_lifecycle.py tests/e2e/test_agent_page.py -q

# Real-stack posting/recovery regression; owns an isolated runtime.
uv run pytest tests/e2e/test_mp05_live_posting_conservation.py -q

# UI service quality gate, from services/ui.
just ci-quiet

# Final integrated gate, from repository root, using existing isolation support.
just ci-quiet
```

Run other affected browser and integration tests as indicated by the changes;
the commands above are entry points, not a substitute for coverage judgment.
Keep existing behavioral assertions when updating presentation-dependent
selectors. Prefer accessible roles/names where stable. Never delete a regression
because the new layout makes its selector inconvenient.

Capture command, working directory, exit status, tested revision/tree, and
meaningful assertions. Read complete results. Record environment failures
honestly and resolve them without suppressing checks. A passing run predating
the UI changes is not final validation of this work.

## Definition of done

All of the following must be true:

1. The audit has identified concrete UX friction, and each selected finding is
   resolved with before/after evidence or explicitly recorded as outside this
   brief. No unresolved blocker introduced by the rework remains.
2. The affected journeys above can be completed through visible, understandable
   UI controls. Identity, current status, next action, relevant terms/deadlines,
   and action outcomes are clear at the point of use.
3. All previously supported journeys remain available. File access, financial
   outcomes, authorization, recovery, and duplicate-action protection still work
   against the unchanged service contracts.
4. The changed UI passes the specified responsive, zoom, keyboard, contrast,
   feedback, and representative-state checks. Visual changes serve the UX goal.
5. Real browser evidence supports the interaction claims. Relevant regression
   checks, UI CI, and the final isolated root CI pass on the integrated changes.
6. Independent review has checked usability, unintended regressions, and scope;
   concrete findings are resolved and affected checks rerun.
7. Todos and canonical ticket statuses match the evidence. The handoff lists
   changed files, resolved UX problems, verification results, any genuine limits,
   and commit/push status. No backend redesign or invented side project is hidden
   in the diff.

Stop when these conditions are met. Do not keep restyling a usable interface to
consume time, satisfy personal aesthetic preferences, or create more work.

## Execution record (2026-11-04)

Impeccable context and product-register guidance are loaded; PRODUCT.md records
only the existing brief and application conventions. A separate read-only review
checked secondary surfaces. The detector was run; its single-font warning is
inapplicable to the product register, which permits one family. Cosmetic detector
findings do not justify a theme overhaul. No live overlay or recruited-user test
is claimed.

Baseline: `docs/plans/2026-11-04-ui-evidence/baseline.log` records 5 failing and
11 passing browser checks. Screenshots in `before/` follow test parameter order:
Home, Post, Agents, Observatory, Reports; each at 1440, 768, 390 pixels. Initial
restricted runs could not bind loopback sockets; the actual baseline ran with
local fixture access and disposable data.

Selected findings and interaction decisions:

| Page/state and user goal | Observed obstacle | Change and acceptance |
| --- | --- | --- |
| Home: reach a destination | Navigation exceeds 768/390px width | Wrap navigation; every destination fits and can be activated |
| Post: enter a job on phone | Fixed 360px feed leaves only a sliver for the form | Stack supporting feed below task; full-width fields remain reachable |
| Shared: read labels and use keyboard | Dim text token is #4a5568 on #0b0e14; brand divs and task phases lack keyboard semantics | Raise muted contrast, use links, expose phase keyboard controls and focus |
| Post: understand requirements | Required controls have no required semantics | Announce required fields and explain units without changing validation contracts |
| Task: orient after a deep link | Title and acting identity are dispersed; phase title dominates | Add compact task title, current actor and next-step summary above history |
| Files/review | Input filenames imply download only by link; output section says Uploaded files | Label download affordance and explain inputs versus uploaded outputs without claiming submission prematurely |

Implementation is one bounded shared-layout slice and one task-comprehension
slice. Existing action guards, input retention, API contracts and financial
projections remain authoritative. No wholesale CSS cleanup is planned. T-217
and T-218 are the canonical UI test/implementation pair; T-219–T-222 record the owner-authorized fixture repairs. All six are closed against the final root result.

Final review and browser evidence are indexed in `docs/plans/2026-11-04-ui-evidence/acceptance.md`. **UI-WORK is complete.** The independent final root `just ci-quiet` exited **0**, including 495 cross-service/runtime tests, 364 UI browser tests and 115 live E2E tests. UI CI, the real file/payment journeys, responsive/keyboard/contrast checks and independent reviews passed. All selected findings and the two explicitly authorized test-fixture races are resolved; T-217–T-222 are closed. No production service code, economic rules, APIs or dependencies changed.

Changed files: five UI HTML pages; `assets/style.css`, `assets/task.js`, `assets/agent.js`, `assets/observatory.js`; the new UI usability tests; three authorized Task Board fixture helpers and two new regressions; PRODUCT.md, this brief, the canonical tracker and the evidence directory. Existing acceptance assertions and prior staged work were preserved.

Limits: this is an agent walkthrough, not recruited-user testing. The 200% check uses Chromium page scaling plus a separate equivalent reflow viewport, not the saved browser-menu zoom setting. No unresolved usability or engineering blocker remains. Publication is being handled separately after the verified 18:36 Europe/Amsterdam allowed-hours check; the publishing lane records commit/push status without mixing prior staged work.
