# Root `stop-all` Test Incident — 2026-10-24

## Incident

An escalated run of
`test_stop_all_reports_failure_after_attempting_every_stop` executed a copied
pre-fix root justfile before its safety guard had been scoped to the `stop-all`
recipe body.

Command:

```text
PYTHONPATH="$PWD:$PWD/tests" uv run --project services/db-gateway pytest tests/runtime/test_root_recipe_failures.py -vv --tb=short
```

The run used `sandbox_permissions="require_escalated"`. The tool record exposed
the date, 2026-10-24, but no exact wall-clock timestamp.

The first copied-recipe `cd services/identity` failed inside the temporary test
directory. Its following `cd -` escaped into the real workspace. The remaining
six service `just kill` recipes then ran against the live workspace. Earlier
captured output showing `Service is not running` was incomplete and did not
describe the harmful run later confirmed from the original process handles.

The parent consumed those handles and confirmed six graceful SIGTERM exits on
2026-10-24:

- Central Bank, PID 2946: exit 143 at 20:31:30Z
- Task Board, PID 16903: exit 143 at 20:31:32Z
- Reputation, PID 2940: exit 143 at 20:31:34Z
- Court, PID 41053: exit 143 at 20:31:36Z
- DB Gateway, PID 49458: exit 143 at 20:31:38Z
- UI, PID 42862: exit 143 at 20:31:40Z

The two-second sequence matches the escaped service stop recipes. Identity, PID
71040, survived. The two legacy `pkill -f` lines were also reached, with their
stderr suppressed and exit statuses ignored. No agent PIDs were recorded before
or after the incident, so agent impact remains unknown.

The parent restored the six terminated services using the same database and key
material. The restored stack was confirmed healthy across all seven services,
with four tasks (two approved and two ruled), eight identities, six accounts,
four feedback records, and zero escrow. Parent-owned restoration is complete;
future service lifecycle actions remain under parent control.

## Repair

The production `stop-all` loop now runs every service stop in its own subshell,
aggregates failures into a nonzero result, and does not stop agents. Its test
creates only disposable service directories with marker-only stop recipes.
HTTP fixtures run in-process and are closed with server shutdown plus thread
join; the test uses no PID files, child processes, or OS signals.
