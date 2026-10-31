# Quarterly navigation race: T-175/T-176

Status: T-175/T-176 CLOSED after deterministic red proof, implementation, independent review,
root19 exit0 and final closure verification. The full298-case browser suite passed. This baseline repair
does not reduce any MP-04 or remaining Core/Vision requirement.

The unchanged quarterly-page browser test failed in the full UI run:294 passed/1 failed, log
`/private/tmp/agent-economy-mp04-ui-e2e.log`. Two rapid Prev handlers read the same settled quarter
before either asynchronous fetch completed, so both requested the same destination. The timeout
was a consequence of a lost click, not evidence that the browser needed a longer wait.

The new `services/ui/tests/e2e/test_quarterly_navigation_concurrency.py` dispatches multiple clicks
in a single browser task and records requests made by the shipped JavaScript. It does not patch
the navigation implementation or increase any existing timeout. No-data responses are controlled
at the HTTP boundary. New tests carry integration and e2e markers without type suppressions.

| Proof | Before repair | After repair |
| --- | --- | --- |
| Prev twice from 2026-Q1 | Q4, Q4 (lost click) | 2025-Q4, 2025-Q3 |
| Next, Next, Prev from 2026-Q1 | Q2, Q2, 2025-Q4 | Q2, Q3, Q2 |
| 503 then later navigation | Visible failure, later success | Same behavior retained |

The first two cases are genuine product reds. The third is a passing control, not red evidence.
Author command and independent parent command both produced two failures and one passing control
before source modification. Parent red log:
`/private/tmp/agent-economy-quarter-navigation-parent-red.log`.

The production repair adds one promise queue in `services/ui/data/web/assets/quarterly.js`.
Each accepted click computes its destination after the previous navigation settles. The existing
handled-fetch-error path returns normally, so subsequent clicks can still navigate from the last
rendered quarter. No existing test, assertion, timeout or rendering behavior was changed.

Parent command from `services/ui`:

```text
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 uv run --no-sync pytest tests/e2e/test_quarterly_navigation_concurrency.py tests/e2e/test_quarterly_report_page.py -q --tb=short --color=no
```

Result:7 passed, exit0; `/private/tmp/agent-economy-quarter-navigation-parent-green.log`.
The isolated fixture starts its disposable UI application with uvicorn and exercises the real
browser page. Ruff, formatting, mypy and Pyright passed for the new test. Independent review
APPROVED the test and production change with no findings. Root19 supplied the passing full gate;
see root19-checkpoint.md for hashes, cleanup and independent closure verification.

The subsequent full UI run passed all seven quarterly cases and295 cases overall, but three
single-agent setup errors kept the suite red. Those errors are the separate bounded-readiness
issue T-177/T-178; see ui-readiness-checkpoint.md. They do not justify changing the original
quarterly assertions or timeout. The later root19 complete root/UI gate passed after that repair.
