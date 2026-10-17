# UI fixture readiness: T-177/T-178

Status: T-177/T-178 CLOSED after deterministic red/green, focused proof, independent review,
root19 exit0 and final closure verification. This test-server repair is separate from the quarterly race.

The full UI command exited1 with295 passed/3 setup errors:
`/private/tmp/agent-economy-quarter-navigation-full-ui.log` (parent session66927, terminal).
All3 new and4 existing quarterly cases passed in this run. The three errors are one cached
single-agent session-fixture failure: httpx.ReadTimeout escaped `_wait_for_server` on a health probe.
The helper creates a listening socket before its child can answer HTTP, permits15 seconds for
readiness, but previously retried only ConnectError and allowed a2-second read timeout to abort.

Independent diagnosis observed healthy startup followed by cleanup in the temporary single-agent
server log. That raw log had disappeared before parent archival (both ordinary and host reads failed);
do not cite it as a retained artifact. The complete pytest traceback remains in the full-UI log,
and the causal helper behavior is independently reproduced by deterministic tests below.

NEW `services/ui/tests/unit/test_e2e_server_readiness.py` proves:

- transient ReadTimeout then HTTP200 must reach readiness;
- persistent ReadTimeout must end with the diagnostic readiness TimeoutError and child log;
- ConnectError then HTTP200 remains supported;
- child exit fails before a health request and retains log diagnostics.

The first two were genuine reds, the latter two passing controls. Parent's final pre-fix log is
`/private/tmp/agent-economy-ui-readiness-parent-red-final3.log` (2 failed/2 passed). An earlier direct
module import failed collection under pytest; it is a harness error, not product-red evidence.
The final test loads the actual helper through a pytest fixture with a restored import path,
no new lint/type suppression, controlled clock and no real sleeping/process/network work.

One line in `_fixture_support.py` now handles ConnectError and TimeoutException in the existing
readiness loop. All timeout values, child-exit fail-fast behavior and terminal diagnostics remain
unchanged. Other exceptions and all browser assertions remain unchanged.

Parent proof after repair:

- 4 unit tests passed, exit0, `/private/tmp/agent-economy-ui-readiness-parent-green-final.log`.
- 3 unchanged single-agent browser cases plus7 quarterly cases passed, exit0,
  `/private/tmp/agent-economy-ui-readiness-browser-green.log`.
- Ruff check, explicit new-file formatting and direct Pyright passed (0 errors/warnings).

Root19 passed the full298-case UI suite and every root phase; see root19-checkpoint.md for exact
hash/cleanup evidence and independent final verification. No timeout value was increased.
