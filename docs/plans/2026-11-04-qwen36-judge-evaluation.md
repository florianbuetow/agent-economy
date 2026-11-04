# Court model switch and evaluation — 2026-11-04

The owner explicitly selected `qwen/qwen3.6-35b-a3b` in place of Qwen3-0.6B for
Court judging. All three judges now use `openai/qwen/qwen3.6-35b-a3b` through
LM Studio's OpenAI-compatible endpoint. The provider prefix is removed by LiteLLM;
the requested LM Studio model ID is exactly `qwen/qwen3.6-35b-a3b`.

The loaded-model API confirmed that model was loaded. Temperature0.2,
300-second per-call timeout, and `/no_think` remain explicit and unchanged.
Worker-agent profiles were not changed.

Court was gracefully stopped using `just stop-court`, then started with
`just _launch-court`. Its health response reported healthy, started at
2026-11-04T10:01:59Z, with the same seven stored disputes and zero active disputes.
No runtime database or keys were reset.

## Unchanged evaluation corpus

Command from services/court with the existing configured credential:

```text
uv run python -m court_service.judge_eval --out /Users/flo/Developer/github/agent-economy/docs/plans/2026-11-04-qwen36-judge-evaluation-results.json
```

The run finished within ten minutes and exited1 because two panel cases lacked
all three successful votes. The three actual failures were LM Studio HTTP400
responses reporting `LM Link connection entered error state peer_keepalive_timeout`.
They were not JSON or output-schema failures.

| Case | Expected worker percent | Panel median | Result |
| --- | --- | --- | --- |
| Precise, correct delivery | 70–100 | 100 | Pass |
| Precise, wrong delivery | 0–30 | — | Incomplete: one provider failure |
| Vague, good-faith delivery | 60–100 | 90 | Pass |
| Vague, no effort | 0–40 | — | Incomplete: two provider failures |
| Injected rebuttal | 0–30 | 0 | Pass |

One judge voted0 on the correct delivery while the other two voted100; the panel
median passed. These results support improvement on the completed cases, not a
claim of perfect or fully verified judgment quality. Raw votes and failures are
retained in `2026-11-04-qwen36-judge-evaluation-results.json`. The CLI's static
local-cost note is not a measurement of LM Link billing or resource use.

The explicit closed-port unavailable-provider probe also returned the expected
generic `judge_unavailable` response.

## Configuration verification

Court `just ci-quiet` passed on retry. The first attempt stopped at `code-audit`
on an external PyPI read timeout; the retry passed every configured service gate.
No code, rubric, parser, corpus, expected bands, or dependency was changed.

Publication remains pending because the current Europe/Amsterdam time is within
the repository's weekday08:00–18:00 commit/push prohibition.

## Completed transport-only continuation

The owner asked the agent to handle the incomplete calls rather than hand them
back. LM Studio reported the selected model unloaded. Its documented v1 load API
was used to reload that same model. Once the original instance reappeared, the
extra instance created by that reload (`qwen/qwen3.6-35b-a3b:2`) was unloaded by its
exact instance ID; the original model and other user-loaded models were retained.

Only failed transport calls were retried. The first continuation completed two
votes; the remaining call returned LM Link `connection_closed`. After removing
the extra reload instance, that one failed call was retried and completed.
Original failures and retry history are retained, and every originally valid vote
is byte-for-byte unchanged in the completed result.

| Case | Expected worker percent | Completed panel median | Result |
| --- | --- | --- | --- |
| Precise, correct delivery | 70–100 | 100 | Pass |
| Precise, wrong delivery | 0–30 | 10 | Pass |
| Vague, good-faith delivery | 60–100 | 90 | Pass |
| Vague, no effort | 0–40 | 0 | Pass |
| Injected rebuttal | 0–30 | 0 | Pass |

The continuation exited0. All15 final votes are valid, all five panel results
meet the original bands, and no valid vote was rerolled. This is a completed
transport-retried evaluation, not a claim that the original uninterrupted run
passed or that LM Link never disconnects.

Artifacts: `2026-11-04-qwen36-judge-retry-results.json` and
`2026-11-04-qwen36-judge-completed-results.json`. Court service CI already passed;
final root verification for the new configuration is being run separately.

Model management used the official [load](https://lmstudio.ai/docs/developer/rest/load)
and [instance-specific unload](https://lmstudio.ai/docs/developer/rest/unload) APIs.

## Final root verification in progress

Root `just ci-quiet` is running on the updated configuration with the normal PATH
and existing isolated runtimes. Frozen inputs: `2026-11-04-qwen36-root-source.json`
(1,497 files). Live execution handle:32676. Output:
`/tmp/agent-economy-qwen36-root-ci.log`; terminal status will be recorded in
`/tmp/agent-economy-qwen36-root-ci.exit`. A started gate is not a passing result.

The first root attempt stopped at Central Bank's dependency audit on another
PyPI read timeout. The unchanged audit passed when retried directly. A fresh full
root run was then started; no advisories, checks, or assertions were suppressed.
Retry output: `/tmp/agent-economy-qwen36-root-ci-retry.log`; terminal exit file:
`/tmp/agent-economy-qwen36-root-ci-retry.exit`. Original exec32676 is terminal
with exit1. The active retry handle is recorded below.

## Final root gate passed

`PIP_AUDIT_LOGLEVEL=DEBUG just ci-quiet` exited **0** at
**2026-11-04 12:37:43 UTC**. All **1,497** recorded source/config/test inputs were
unchanged. Diagnostic audit logging did not skip or alter any check; PATH stayed
unchanged. Evidence is in
[`2026-11-04-qwen36-verification/final-root-result.json`](2026-11-04-qwen36-verification/final-root-result.json)
and the lossless compressed full log in that directory.

All five real-model panel cases and the final software gate are now satisfied.
The owner-requested model is running in Court. Only publication remains pending
until the allowed Git window; the read-only publication timer performs no writes.
