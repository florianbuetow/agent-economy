# Classification runtime evaluation — 2026-11-04

## Initial outcome

The shipped non-math runtime did not reach bidding or classification. Both
configured workers skipped every posted task because Task Board list summaries do
not contain `spec`, while `ClassificationWorkerLoop._phase_scanning()` attempts to
parse the classification contract directly from each list summary.

This is retained failure evidence. It does not establish selection quality,
adaptation, cost, or uncertainty, and no measured economy report is claimed.

## Setup

- Disposable `IsolatedRuntime`; no ordinary service ports, data, or keys were used.
- Existing eight-entry `classification_seed.json`, rendered by
  `python -m task_feeder.workloads` into the run-owned data directory.
- Existing task feeder with two open slots, two-bid acceptance quorum, one-second
  loop intervals, and 30-second bidding windows.
- Existing `classbot_keyword` and `classbot_llm` profiles.
- LM Studio model API verified `qwen3-0.6b` was loaded before the run. The isolated
  agent config also named exactly `qwen3-0.6b`. The classification solver has no
  prompt-suffix setting, so no unrelated `/no_think` configuration was added.
- Feeder funded through the existing platform CLI inside the isolated runtime.
- Hard experiment timeout: 480 seconds. The run was stopped after the defect was
  demonstrated, about 150 seconds after launch.

Command from the repository root:

```bash
uv run --directory services/db-gateway \
  ../../scripts/isolated_test_runtime.py --provision -- \
  uv run --directory agents \
  /tmp/agent_economy_classification_experiment.py \
  /Users/flo/Developer/github/agent-economy
```

The temporary script only adjusted the isolated copy of `agents/config.yaml`,
rendered the tracked seed, started the existing feeder and worker entry points,
polled the isolated Task Board, and invoked the existing economy report if tasks
completed. It added no repository framework or runtime behavior.

## Observed evidence

At stop time, the isolated database contained:

```text
expired|10
open|2
board_bids: 0
```

The feeder had durably posted 12 records across repeated seed rounds. Both worker
logs contained 173 `No suitable task found` decisions and zero bidding log entries.
Their runtime identity journals existed, but neither worker had a job record, so
there were no actual outcome samples from which the existing economy report could
derive approval intervals, cost, or adapted bid percentages.

The public task list confirmed the tasks were genuine classification tasks by
title and feeder source, but its list items contained only the `TaskSummary`
projection. That model includes `task_id`, poster, title, reward, status, counts,
worker and deadlines; it intentionally omits `spec`.

The worker scan currently does this:

1. `list_tasks(status="open")`;
2. pass each returned summary to `task_data(task)`;
3. `task_data` reads `task.get("spec")` and returns `None` when absent;
4. skip the task before bidding.

The relevant boundary is therefore:

- `services/task-board/src/task_board_service/schemas.py` — `TaskSummary` omits
  `spec` as expected for a list projection.
- `agents/src/classification_worker/loop.py` — `_phase_scanning` assumes that same
  list projection contains a full specification.

No LM Studio completion was requested because neither worker advanced to solving.
The model was loaded and configured correctly, but this run provides no evidence
about its classification quality or token cost.

## Cleanup

The experiment was interrupted with SIGINT after the failure was established.
The temporary orchestrator sent SIGTERM to the owned feeder and workers, and the
isolated harness stopped its service processes. A process inventory afterwards
found no surviving experiment, classification-worker, feeder, or run-owned service
process.

## Smallest demonstrated repair

The classification scan should use list results only to select candidate IDs, then
fetch each candidate through `get_task(task_id)` before calling `task_data` and the
adapted policy. A focused regression should give `list_tasks` a summary without
`spec`, return the full classification task from `get_task`, and prove a bid is
prepared. After that repair, rerun this same bounded experiment and retain the
actual journal-derived economy report.

## Repair and failing-first proof

The scanner now uses each list summary only for its task ID, then calls the public
`get_task(task_id)` API and checks the current ID, `open` status, reward, full
classification specification and adapted policy before selecting a candidate.
This preserves the current task binding and avoids bidding from a stale summary.

New focused regression:

```text
uv run pytest tests/unit/classification_worker/test_scanning_task_summary.py -q
```

- Before the repair: one failure, because the scanner returned `None` for an
  actual `TaskSummary` without `spec` and never called `get_task`.
- After the repair: one passed.
- Full `tests/unit/classification_worker`: 27 passed.

This is the failing-test-first proof for T-156 and the bounded implementation is
part of T-157. Ticket status remains owned by the canonical tracker.

## Measured rerun

The same command was rerun against a fresh isolated stack. The evidence run used
one open auction at a time so both existing workers could enter the configured
two-bid contest. This controls auction availability, not the winner. The feeder
was stopped after eight tasks were posted; workers continued until every task was
terminal, then had five seconds to persist terminal outcomes before SIGTERM.

The rerun completed in 137.786 seconds. LM Studio reported the loaded model as:

```text
id: qwen3-0.6b
publisher: mlx-community
quantization: 8bit
loaded context: 40960
```

Eight real public tasks produced 12 bids and 12 durable worker job records:

| Final task status | Count |
|---|---:|
| approved | 3 |
| ruled | 3 |
| expired | 2 |

All accepted-price settlements used the recorded accepted bid. The three ruled
tasks paid the configured mock-Court 50 percent of accepted prices 25, 37 and 28,
yielding worker payments 12, 18 and 14 after integer rounding. Approved tasks paid
their full accepted prices 16, 22 and 14. Expired work paid zero.

The existing journal-derived `economy_report` printed:

```text
classbot_llm
  bids 4, selected 1, lost auctions 3, in flight 0
  approved 0/0 = n/a [0.00, 1.00]
  cost paid 0, mean accepted price 28.0
  quality delivery_quality unrated (n=0), specification_quality unrated (n=0)
    sentiment: 0/0 [0.00, 1.00], bid 80% of reward, bidding
    ticket_routing: 0/0 [0.00, 1.00], bid 70% of reward, bidding

classbot_keyword
  bids 8, selected 6, lost auctions 2, in flight 0
  approved 6/6 = 100% [0.61, 1.00]
  cost paid 96, mean accepted price 23.7
  quality delivery_quality 50/100 (n=3), specification_quality unrated (n=0)
    sentiment: 4/4 [0.51, 1.00], bid 90% of reward, bidding
    ticket_routing: 2/2 [0.34, 1.00], bid 60% of reward, bidding
```

These figures are derived from the worker journals, not a scripted winner. They
show persisted adaptation: the keyword worker's per-type bids diverged to 90
percent for sentiment and 60 percent for ticket routing after its observed wins
and auction losses. Its six successful selected jobs include both direct approvals
and disputes resolved with a positive payout.

## Actual limitation found in the measured run

The `qwen3-0.6b` worker won one ticket-routing auction at an accepted price of 28
but repeatedly failed to produce an answer accepted by `LLMSolver`'s JSON parser.
Its durable job ended as `timeout` with:

```text
last_error: solution could not be parsed; retrying next cycle
payout: 0
```

Because `timeout` is neither a decided quality outcome nor an auction loss in the
current adaptation policy, the report correctly shows one selected job and a mean
accepted price, but zero decided samples and zero cost paid. This run therefore
proves the LLM participant and model path execute, but it does not prove useful
classification quality for `qwen3-0.6b`. The keyword strategy is the only worker
with measured quality evidence in this sample.

No prompt or parser change was made during this evidence lane. The classification
solver has no prompt-suffix configuration, so `/no_think` was not silently added.
Any follow-up should first retain the actual model response that fails parsing and
then make the smallest model-compatible correction without changing the requested
model or fabricating outcomes.

## Final cleanup

The orchestrator stopped the feeder and both workers with SIGTERM, then the
isolated harness stopped its seven services. A final process inventory found no
surviving experiment, feeder, classification-worker or run-owned service process.

## Final rerun after exact-ID prompt repair

A separate focused diagnostic retained the remaining LLM failure: Qwen copied the
prompt's literal `<id>` placeholders instead of returning the task's actual input
keys. The solver prompt was repaired to render the exact input IDs in its JSON
skeleton. Labels, parser, token limits, model and acceptance thresholds were not
changed. Its focused real-model check returned a parser-accepted answer with four
of five labels correct, meeting the published `min_correct=4` rule.

The same bounded experiment command was then run once more against a fresh
isolated stack. The source corpus and one-open-auction setup were unchanged. This
final run completed in 148.037 seconds with the same loaded LM Studio model:
`qwen3-0.6b`, MLX Community 8-bit, 40,960-token loaded context.

Eight tasks produced 13 bids and 13 finished journal records:

| Final task status | Count |
|---|---:|
| approved | 4 |
| ruled | 3 |
| expired | 1 |

The final existing `economy_report` output was:

```text
classbot_llm
  bids 6, selected 1, lost auctions 5, in flight 0
  approved 1/1 = 100% [0.21, 1.00]
  cost paid 21, mean accepted price 21.0
  quality delivery_quality unrated (n=0), specification_quality unrated (n=0)
    sentiment: 0/0 [0.00, 1.00], bid 70% of reward, bidding
    ticket_routing: 1/1 [0.21, 1.00], bid 65% of reward, bidding

classbot_keyword
  bids 7, selected 6, lost auctions 1, in flight 0
  approved 6/6 = 100% [0.61, 1.00]
  cost paid 108, mean accepted price 25.3
  quality delivery_quality 50/100 (n=3), specification_quality unrated (n=0)
    sentiment: 3/3 [0.44, 1.00], bid 85% of reward, bidding
    ticket_routing: 3/3 [0.44, 1.00], bid 75% of reward, bidding
```

The Qwen worker's selected ticket-routing task was directly approved. Its durable
record contains the exact five task IDs, an accepted-price bid of 21, outcome
`approved`, payout 21 and no error. This is the real public-runtime confirmation
that the exact-ID prompt repair removed the observed parse blocker.

The keyword worker's six successful outcomes distinguish three direct approvals
from three `disputed_won` results. The three disputed jobs received partial
mock-Court awards, not full approval payments: accepted prices 28, 22 and 37 paid
14, 11 and 18 respectively. Its three direct approvals paid their accepted prices
28, 16 and 21. These components sum to the reported cost paid of 108.

Adaptation remained journal-derived and outcome-dependent. At the end of this
sample, the Qwen profile bid 65 percent for ticket routing and 70 percent for
sentiment; the keyword profile bid 75 percent for ticket routing and 85 percent
for sentiment. One task expired without an accepted bid, so this small sample is
evidence of functioning participation and adaptation rather than a claim that all
auctions clear or either worker is generally superior.

The final orchestrator again sent SIGTERM to the feeder and both workers before
the isolated harness stopped its seven services. Process inventory found no
surviving experiment, feeder, classification-worker or run-owned service process.
