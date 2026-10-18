# MP-07 deadline fixture reconciliation

## Plan before consumer edits

Adopt the reviewed Gateway-backed test store in `test_deadline_evaluator.py`,
`test_deadline_sweep.py`, and `test_wp06_ruling_trigger.py`. A shared test-only
`native_task_fixture.py` may construct explicit Identity, Bank, Board, and Court
facts through real writers and native commands. Production code, other consumers,
and the old dictionary fake are outside this slice.

The untouched baseline collected twenty tests: eighteen failed at obsolete
`DeadlineEvaluator` constructors missing explicit authority arguments, while the
two deadline arithmetic tests passed. Log:
`/private/tmp/agent-economy-mp07-deadline-fixtures-before.log` (exit 1).
These fixture failures do not prove new product defects.

## Contract changes and preserved observations

- New bid-bearing tasks stay open after bidding closes and expire at the poster's
  explicit acceptance cutoff. Zero-bid tasks expire at bidding cutoff. Historical
  timing-v1 tasks retain their original bidding/acceptance cutoff. A labeled stored
  timing-v1 fixture verifies that read shape; migration execution remains covered
  by the existing Gateway migration suites, not claimed by this fixture test.
- Accepted/submitted/approved/disputed setup uses real native phase histories,
  including bids, owned assets, frozen accepted terms, financial receipts and Court
  claim rows as appropriate. No arbitrary status dictionary is treated as history.
- A bare `escrow_pending` flag cannot authorize financial retry. Replace the old
  flag-based retry expectation with an actually claimed automatic operation after
  delivery fails; prove retry keeps its exact operation ID and produces one receipt,
  one payout and one semantic event. Add a separate pre-deadline flag-only control.
- Replace calls to legacy `try_release_escrow` with assertions on actual persisted
  escrow release, receipt recipient/amount, completed operation, and semantic event.
  Terminal and pre-deadline cases must produce no new delivery or transition.
- Concurrent same-operation retries may deliver more than once. Force overlap at
  Bank delivery, then prove identical effect identity and exactly one financial
  settlement, receipt and terminal event. Repeated reads after completion must not
  issue additional delivery.
- Preserve batch coverage, real periodic sweep execution without API reads,
  coverage of all live statuses, no early expiry, and continuation past a failing
  task. Keep asyncio's real timers; inject only the shared business clock.
- Ruling-trigger tests prove eligibility, attempted claim ID, failure containment,
  and retry. A mocked trigger response is not proof of judging or a persisted
  ruling. Real claim/rebuttal rows establish eligibility; the task stays disputed
  unless an actual native ruling is persisted.

All state lives under each test's temporary directory. Constructors receive
explicit schema, timeout, WAL mode, caller, authority, and clock. Fixture logging
uses isolated logger objects writing to pytest-captured stderr, with no service
startup, file logging, or ordinary runtime paths. Any direct historical sidecar
fixture mutation is labeled and is not an ordinary writer capability.

## Validation scope

Run the three converted modules together with the seventeen adapter parity tests,
then Ruff, Mypy and Pyright on the owned Python files. Redirect all test and static
output to `/private/tmp/agent-economy-mp07-deadline-fixtures-*.log` and record terminal
results. Independent review, other fixture adoption, and root CI remain separate
completion gates.

## Verified results

The final combined run passed **39 tests**: twelve evaluator cases, five sweep
cases, five ruling-trigger cases, and seventeen adapter parity cases. All original
twenty scenarios remain represented, with separate timing-v1 and flag-only
controls added. No skips or expected failures were introduced.

Run from `services/task-board`:

```sh
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 uv run --no-sync pytest tests/unit/test_deadline_evaluator.py tests/unit/test_deadline_sweep.py tests/unit/test_wp06_ruling_trigger.py tests/unit/test_mp07_gateway_store_parity.py -q --tb=short --color=no
```

Final test log:
`/private/tmp/agent-economy-mp07-deadline-fixtures-final2-green.log`
(terminal exit 0, 39 passed in 19.22 seconds).

Ruff check, Ruff format check, Mypy, and Pyright all exited 0 for the four owned
Python files. Mypy used the local Board, Gateway, and shared-library source roots
through `MYPYPATH`; no import or typing suppressions were added. Logs:

- `/private/tmp/agent-economy-mp07-deadline-fixtures-ruff-final2.log`
- `/private/tmp/agent-economy-mp07-deadline-fixtures-format-final2.log`
- `/private/tmp/agent-economy-mp07-deadline-fixtures-mypy-final2.log`
- `/private/tmp/agent-economy-mp07-deadline-fixtures-pyright-final2.log`

Verified source SHA-256 values:

| File relative to `services/task-board` | SHA-256 |
| --- | --- |
| `tests/fakes/native_task_fixture.py` | `46a362ef0f723a4e14dc01d69ad43878f4dd944ef629a3c2fb415f12ab6f1e9a` |
| `tests/unit/test_deadline_evaluator.py` | `bdbc95ea5dcb2535319905e362b0b701fbbb7608683369f8579efca78ba44518` |
| `tests/unit/test_deadline_sweep.py` | `22e655faa0631cc0c9dfa99731b1014189b51a8db6ac1269d7ce3a382a65d341` |
| `tests/unit/test_wp06_ruling_trigger.py` | `9677d42956a1dcef9ce2d102d283bb6b3133125916b5f2179c25cea8040a0755` |

The shared helper is an explicit context manager used by local pytest fixtures;
it does not register a global pytest plugin. Settlement assertions inspect one
actual release transaction, one receipt and recipient leg, one Board event, the
released escrow, and ledger-to-account balance reconciliation. Overlap is forced
before either Bank write, and both deliveries carry the same operation identity.

This is test-fixture adoption, not a production fix or a completion claim for all
MP-07 work. Other fake consumers, router fixtures, independent review, fresh root
CI, and actual Court judging remain outside this evidence.
