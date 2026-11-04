# UI usability acceptance evidence

This is an agent walkthrough with disposable data, not recruited-user research.
Scope: existing frontend HTML/CSS/JavaScript, one added browser-test file, and
context/tracker/evidence documentation. No endpoints, service behavior, models,
ledger rules, authorization, storage or dependency changes.

## Before and after

| User task | Initial obstacle | Verified improvement |
| --- | --- | --- |
| Reach Jobs/Post on a phone or tablet | Home navigation extended outside the viewport | Visible destinations wrap and retain their existing routes |
| Post a job on phone | Fixed 360px event feed crushed the form to about 22px | Fields occupy the task panel; feed follows the form in document order |
| Read labels and navigate by keyboard | Low-contrast dim token, non-link home brands, pointer-only phases | Contrast raised within existing palette; native links and reachable keyboard phase controls; future markers remain inert with explanatory labels |
| Understand a task after a deep link | Job title, actor and available action were dispersed | Title, acting identity/role and What happens now guidance precede the detailed history |
| Correct a posting mistake | Required fields not announced; result region not live | Explicit requirements/units, required attributes, announced error; entered values and focus retained |
| Distinguish inputs and work | Input assets also appeared/count as output and advanced delivery phase | Explicit input roles stay in Input files; output sections and phase counts exclude inputs; preview/download still work |
| Understand normal payment | A discounted accepted bid's refund was called a Court ruling | Confirmed split payment only references a ruling when one exists |
| Inspect agent history on phone | Fixed-width chart and feedback overflowed | Chart scales within its container, details wrap; quality/history controls remain available |
| Recover from agent fetch failure | Failure looked like Agent not found | Visible initial loading; separate missing and unavailable states with reload link |
| Read selected monitoring filters | Active controls only changed color | Pressed state is exposed to assistive technology and underlined |

The product-register Impeccable guidance was applied to critique, bounded
interaction choices, rendered audit and final polish. The detector's one-font
warning was rejected as incompatible with its own product-register permission;
cosmetic findings did not trigger a theme makeover. No live overlay is claimed.

## Screenshot map

`before/test_navigation_and_content_fiN.png` and matching `after/` names:

| N | Page | Viewport |
| --- | --- | --- |
| 0,1,2 | Home | 1440x900, 768x1024, 390x844 |
| 3,4,5 | Post | 1440x900, 768x1024, 390x844 |
| 6,7,8 | Agent list | 1440x900, 768x1024, 390x844 |
| 9,10,11 | Observatory | 1440x900, 768x1024, 390x844 |
| 12,13,14 | Current quarterly report (empty current period) | 1440x900, 768x1024, 390x844 |

The before captures were refreshed with fully loaded content and animations
finished using the committed frontend copied **only** into a disposable fixture
web directory. The working tree and normal runtime were untouched. See
`baseline-stable.log`: 4 expected layout failures, 11 passes. The original
`baseline.log` also records missing required-input semantics (5 failures).

`after/test_populated_content_reflowsN.png`: groups 0–4, 5–9, 10–14, 15–19 are
1440x900, 768x1024, 390x844 and 720x450 respectively. Within each group:
Home, approved task t-task1, Alice profile, Observatory, seeded 2026-Q1 report.
The 720x450 layout is the CSS viewport equivalent of desktop 200% zoom.
`accessibility/` separately records Chromium's actual 2x page-scale screenshot,
keyboard walkthrough and measured contrast; this is CDP page scaling, not a saved
browser-menu zoom preference.

## Behavior and review

- `live/acceptance.md`: actual isolated services, visible navigation, posting with
  input bytes, bid acceptance, worker upload/preview/submission, poster approval,
  exact input/output retrieval, accepted price and conserved balances; retry
  conservation and no duplicate posting effect. Separate actors use the harness,
  without UI impersonation.
- `responsive.log`: 45 new browser checks pass, including populated narrow
  layouts, keyboard phases/monitoring, input roles, payment copy, profile failure,
  feedback guidance and posting-error retention.
- `lifecycle-final.log`: 138 checks pass (98 existing task lifecycle checks plus
  the then-current 40 new checks), including disputes/rebuttals/rulings, pending
  phases, available controls, errors and recovery.
- `documents-final.log`: 12 unchanged worker and document regressions pass;
  exact links, inert preview, failed-preview download retention and phase access.
- `g1-final.log`: 18 unchanged task-page acceptance tests pass.
- Independent reviewer approved the repaired state communication, input/output
  distinction and scope. Rendered review additionally caught and verified the
  phone Home overflow fix. `accessibility/review.md` records final approval,
  keyboard focus/error retention and contrast (text 6.70–15.67:1; focus 12.56:1).

`source-sha256.txt` identifies final frontend/test inputs. Existing staged backend
and evaluation work belongs to the prior session and was preserved.

## Failures and corrections retained

The `*-red.log` files preserve each added failing behavior check. Initial socket
and cache errors were environment restrictions, resolved by running the disposable
fixtures with local process access. No PATH replacement or fake tools were used.

`browser-superseded.log`: 328 passed, 8 failed because newly disabled future
phase markers blocked the test driver's click. Future phases are now static
notes with not-reached labels and excluded from Tab order; pointer clicks remain
inert. Existing assertions are unchanged and all 98 lifecycle tests pass.

A new required-field paragraph initially duplicated `.form-note`, breaking an
existing unique balance-note assertion; the paragraph now uses `.form-hint`.
Strict output-role filtering hid role-unspecified files in existing acceptance
fixtures; the final filter excludes only explicit input roles and preserves
other file access. The unchanged 12 document/worker tests pass.

One combined unit/E2E invocation hit overlapping synchronous Playwright session
fixtures. The suites were rerun in their normal separate processes. New test-only
readiness bugs (CSP eval string, unseeded report period, wrong input-manifest
wrapper, and assertion against an omitted empty review section) were corrected
without changing production policy or existing tests. The What happens now label
preserves an existing assertion that no obsolete Next controls exist.

## Final engineering gates: passed

| Gate | Working directory | Exit | Evidence |
| --- | --- | --- | --- |
| `just ci-quiet` (UI) | `services/ui` | 0 | `ui-ci.log` |
| `just ci-quiet` (independent final root) | repository root | 0 | `root-ci-final.log`, `final-root-result.json` |

The complete root gate passed libraries, all services, agents, tools, integration,
UI browsers and live E2E. Its final phases report **495 cross-service/runtime
checks**, **364 UI browser checks** and **115 live E2E checks** passed. The agent
CI phase retained its existing 3 skips and 115 E2E deselections; those 115 E2E
cases ran in the final live phase. No check or assertion was weakened for this
work, and no new skip was introduced.

Both gates used normal PATH. Root recipes owned disposable integration and E2E
runtimes, leaving ordinary runtime data untouched. The parent independently
owned the successful root process (session 1752); its terminal exit was 0.
`final-root-source.json` captures the integrated repository tree before
closure-only documentation edits, including preserved prior staged work.
`source-sha256.txt`, `fixture-source-sha256.txt` and
`router-clock-source-sha256.txt` identify the changed frontend/test inputs.
T-217–T-222 are **CLOSED** against their own failing-first evidence and this root
pass. All UI-WORK todos and its definition of done are satisfied.

## Historical gate failures, now resolved

- `root-ci-failure.log`: one Task Board shared-connection fixture race, 1,119
  other cases passed. `root-ci-blocker.md` records the natural failure,
  deterministic diagnostic proof and owner-authorized test-only repair.
- `root-ci-repaired-failure.log`: the first repair passed, but a router wall-time
  freeze prematurely woke an elapsed-time sweep, causing a legitimate version
  conflict against an unchanged single-request cutoff assertion. 1,120 other
  Task Board cases passed. `router-clock-repair.md` records deterministic red
  proof, the approved fixture-only clock isolation and its passing tests.
- `root-ci-agent-selector-failure.log`: service/runtime/integration stages passed;
  UI E2E reported 363 passed and one failure because hidden loading status shared
  `.lb-empty` with the not-found result. The loading status now uses its own
  `.agent-loading` class with identical styling, status semantics and hide/clear
  behavior. No acceptance test was edited. All 6 agent tests plus 45 usability
  checks passed (51 total, 17.21s; `agent-final.log`), and independent review
  approved the two-line split. The final full root run passed all 364 UI tests.
- An earlier superseded root process was deliberately interrupted (exit 130)
  before integration when live review exposed inaccurate payment copy. It is
  not used as passing evidence.

The class-only agent-page correction does not affect the verified live
file/payment journeys. The after screenshots and source hashes are current.
No unresolved code-review, usability or engineering blocker remains.

## Changed files and remaining limits

Frontend: `index.html`, `task.html`, `agent.html`, `observatory.html`,
`quarterly-report.html`, and `assets/style.css`, `assets/task.js`,
`assets/agent.js`, `assets/observatory.js` under `services/ui/data/web/`.
Added `services/ui/tests/e2e/test_ui_usability.py`.

Authorized fixture extension: `services/task-board/tests/fakes/gateway_task_store.py`,
`native_task_fixture.py`, `tests/unit/routers/conftest.py`, plus new
`tests/unit/test_gateway_fixture_serialization.py` and
`tests/unit/routers/test_fixture_clock_isolation.py`.
Documentation: PRODUCT.md, UI-WORK.md, the canonical backlog and this evidence
folder. Production service behavior, public interfaces, economic rules, schemas,
dependencies and all existing acceptance assertions are unchanged.

This is an agent walkthrough, not recruited-user evidence. Desktop 200% was
checked through Chromium page scale plus equivalent layout reflow, not a saved
browser-menu zoom preference. These limits are disclosed in
`accessibility/review.md`; they are not unresolved UI defects.

## Publication

UX and engineering acceptance are complete. No commit or push was attempted
during prohibited office hours. At the actual 18:36 Europe/Amsterdam check,
publication became permitted; the parent publishing lane is handling a separate
UI-only commit/push while preserving prior staged changes. Its final commit/push
status is recorded separately. Evidence under `docs/plans/` is ignored and must
be explicitly added by that lane.
