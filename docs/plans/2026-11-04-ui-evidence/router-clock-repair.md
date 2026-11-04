# Resolved router fixture clock isolation

The next full root run exited 1 on a different existing test:
`TestBidAcceptance.test_accept_at_acceptance_deadline_is_rejected` expected
`cutoff_reached` but received `task_version_conflict`. 1,120 other Task Board
cases passed, including the repaired shared-connection race and its regression.
The complete natural result is `root-ci-repaired-failure.log`; the same cutoff
test passed in isolation (`router-deadline-isolated.log`).

The router fixture runs a genuine periodic deadline sweep with a 3600-second
interval. The test freezes business wall time at the acceptance deadline before
signing and sending its acceptance. Freezegun's ordinary wall-time freeze also
replaces `time.monotonic`, making pending asyncio timers immediately due. The
sweep can therefore expire the task after the helper reads/signs its lifecycle
version and before the acceptance reaches the mutation boundary. The resulting
optimistic version conflict is valid production behavior; the single-request
test unintentionally introduced a concurrent expiry.

A new deterministic regression first scheduled a one-hour elapsed timer, moved
business time to 2040 and yielded twice. The timer immediately fired, proving
the fixture clock defect (`ate-ui-router-clock-red.log`). The test also asserts
that business time really moves; it does not remove deadline testing.

The approved fixture-only repair captures the real monotonic function before
any freeze and binds it to the running loop's `time` method for the router app
fixture. Pytest monkeypatch restores the original method during teardown.
The sweep still runs on real elapsed time, and dedicated periodic-sweep tests
are unchanged. All existing cutoff/error assertions remain unchanged. The only
new source changes are this setup in router `conftest.py` and the new
`test_fixture_clock_isolation.py`; no production code changed.

Incidental artifact-maintenance errors in the old fixture startup/teardown logs
are not suppressed or attributed to this repair. No unrelated maintenance or
service implementation has been changed.

T-221/T-222 are closed against their red-first proof, passing focused tests,
static checks, independent review and final parent-owned root exit 0.

## Verified repair

The focused command (working directory `services/task-board`) was:

```sh
uv run pytest tests/unit/routers/test_fixture_clock_isolation.py tests/unit/routers/test_bids.py tests/unit/test_deadline_sweep.py tests/unit/test_gateway_fixture_serialization.py -q
```

It exited **0**, with **43 passed in 129.19 seconds**: all 36 existing bid cases,
including the exact cutoff assertion, the new elapsed-clock regression, all five
real periodic-sweep cases and the shared-connection regression. The retained
output is `ate-ui-router-clock-green.log`. Formatting, mypy, pyright and spelling
also exited 0 (`ate-ui-router-clock-static.log`).

Independent review approved the two-file clock change with zero findings.
The loop-instance monkeypatch is restored at fixture teardown after background
work is cancelled and awaited; no production scheduling or deadline behavior
changed. `router-clock-source-sha256.txt` identifies the tested repair. The parent-owned final root gate exited 0; `final-root-result.json` and
`root-ci-final.log` preserve that successful completion.
