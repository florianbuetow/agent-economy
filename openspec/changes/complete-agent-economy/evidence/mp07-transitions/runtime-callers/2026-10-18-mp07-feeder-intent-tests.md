# MP-07 feeder native-intent caller tests

## Scope

This T-181 test-only slice fixes the caller contract between the Task Feeder's `AcceptanceLoop` and
`ReviewLoop` and the implemented `RuntimeTaskCommands` coordinator. Both loops receive one required
`commands: RuntimeTaskCommands` dependency. Tests use the production `FileTaskIntentStore`, the
public agent SDK surface, one deterministic Ed25519 identity, its registered agent ID and the exact
five configured service URLs. They add no fake journal, private SDK call, Gateway access, dependency,
configuration value or ordinary runtime write.

The slice preserves the existing Q-16 winner policy. The later MP-18 provenance and quality policy
remains separate. Missing feeder oracle provenance is kept visible and cannot cause an approval or
dispute. Broader authoritative discovery, posting recovery and durable oracle storage remain outside
this test boundary.

## Required caller behavior

An existing `acceptance` slot is loaded and replayed before reading the current task, bids,
reputation or recomputing a winner. A missing slot may apply the existing eligibility and Q-16
selection rules, then freezes the actual source lifecycle version, selected bid and worker, poster,
escrow, task reward and selected bid amount before the public `accept_bid` call. Only a `completed`
intent returns `accepted` and enters the in-memory finalized set. Pending, blocked and
attention-required intents remain unfinished. A definitive rejection retains the original operation
ID and is never replaced by a new decision.

An existing `review-decision` slot is loaded and replayed before consulting the feeder task map,
current task, submission fields, assets or answer oracle. It preserves approval versus dispute and,
for disputes, the exact original reason. A new decision freezes the submitted task's actual source
version, worker, poster, escrow, accepted reward and accepted bid amount before transport. A dispute
intent does not predict the Court-generated dispute ID or effect result reference; those exist only
after a matching completed acknowledgment. Only completed review intents return `approved` or
`disputed`; every unfinished or terminal non-completion reports its journal state.

Each poll pass replays known unfinished local work before querying the filtered `open` or `submitted`
task list. This makes a lost acknowledgment replayable after the task has advanced beyond the list
filter. Replay is local known-work recovery and does not claim remote discovery.

## Test matrix

`agents/tests/unit/task_feeder/test_mp07_native_intents.py` adds 15 collected cases covering:

- required coordinator constructor dependencies on both loops;
- acceptance load-before-selection and exact frozen source, winner, party and financial facts;
- unchanged Q-16 amount/reputation selection behavior;
- completed-only acceptance finalization;
- pending, blocked and attention-required acceptance state handling;
- definitive rejection reuse of the original operation ID;
- acceptance replay before the filtered open-task query;
- review load-before-oracle/task/asset reads;
- completed approval and dispute outcomes;
- exact dispute reason without a pre-transport Court ID;
- missing oracle visibility with no mutation; and
- review replay before the filtered submitted-task query.

All intent fixtures are admitted through a real opened `FileTaskIntentStore`. Mutation completion and
non-completion states use the journal's public acknowledgment API or the real command coordinator;
the tests do not write intent files directly.

## RED and verification record

Run from `agents/` with the frozen environment:

```text
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 \
  uv run --no-sync pytest tests/unit/task_feeder/test_mp07_native_intents.py -q --tb=short
```

The fresh pre-implementation run collected 15 cases and failed all 15. The interface assertion shows
that both old constructors omit `commands`; the other 14 cases stop on the resulting unexpected
keyword argument. Those 14 failures are one shared interface prerequisite, not independent product
defects. No test is skipped, xfailed or suppressed. A separate real-store probe admitted the
acceptance, approval and dispute fixtures as `pending`, including the dispute fixture with no future
Court-generated identifier.

Before handoff, run scoped Ruff formatting and linting, standalone strict Pyright for the new test,
codespell for both owned files, and a real-store fixture probe. Full CI is intentionally deferred
because this slice freezes a failing caller contract before implementation.

## Review preparation regressions

`agents/tests/unit/task_feeder/test_mp07_native_intent_regressions.py` adds four regression cases at
the review preparation boundary. A raised public `list_assets` or `download_asset` call is an
unavailable observation, so preparation propagates the failure, remains retryable and writes no
decision intent. It cannot be converted into an empty answer and durable dispute. The tests invoke
each failure twice and require no approval or dispute transport and no `review-decision` record.

An already stored review decision still loads and replays before current-task or asset reads, even
when every such read would raise. For a new decision, an empty earlier `submitted_answer` projection
does not hide a later validated `submission.answer`; the later answer is used without asset access,
while the saved intent retains the actual submitted source version, poster actor, worker, escrow,
accepted reward and accepted bid amount.

A successful empty asset list keeps the existing behavior and policy boundary. Broader MP-18
asset/oracle provenance remains separate. The regression suite uses the real filesystem store,
`RuntimeTaskCommands`, deterministic Ed25519 owner binding and exact service URLs. It adds no second
journal, private SDK path or Gateway access.

Fresh pre-fix execution collected four cases: the stored-decision replay passed, while the two asset
exception boundaries and the empty-earlier-projection case failed. The asset failures logged the
raised exception, returned an empty answer and reached `dispute_task`; the projection failure saved a
`dispute_task` intent instead of the required approval. These are three direct behavioral REDs, not a
shared missing-interface prerequisite. No case is skipped, xfailed or suppressed.
