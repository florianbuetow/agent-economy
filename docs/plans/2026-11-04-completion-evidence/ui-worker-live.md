# Isolated UI worker journey — 2026-11-04

This evidence exercises the existing operator UI proxy against seven real,
disposable services. It covers the proxy API boundary rather than browser control
rendering. No ordinary service, database, key, or asset directory was used.

## Command

```text
uv run --directory services/db-gateway /Users/flo/Developer/github/agent-economy/scripts/isolated_test_runtime.py --provision -- uv run --project agents /tmp/agent-economy-goal-2026-11-04/ui_worker_journey.py
```

## Failing-first result

Disposable runtime:
`/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-f201d14b4fdb45708ed6a8beec731be4-2fav94eo`

Before the repair, the real journey reached these results:

- UI proxy identity matched the isolated operator key.
- Operator-signed bid: 201.
- Independent poster acceptance: 200.
- Frozen input manifest and exact input download through UI proxy: 200.
- Operator output upload through UI proxy with an idempotency key: 201.
- Task Board governed submission: 200.
- UI submission proxy: 502 `deliverable_submission_failed`, reporting a malformed
  success response.

The governed Task Board operation carried protocol version 2. UI mutation
validation required protocol version 1 for every command, so it rejected the
valid submission before approval and payment.

The focused failing regression
`tests/integration/test_protocol2_submit_proxy.py::test_protocol2_submission_success_passes_through_proxy`
returned 502 instead of 200.

## Repair and focused proof

UI mutation validation now permits protocol version 2 only for
`submit_deliverable`; all other commands remain protocol 1. A completed protocol-2
result must contain the exact task/command/status/version/event binding plus a
nonempty worker id, manifest id, and lowercase 64-character manifest hash.

Focused integration command:

```text
cd services/ui
uv run pytest tests/integration/test_protocol2_submit_proxy.py tests/integration/test_q_operator_participation_proxy.py -q --tb=short
```

Result: **27 passed in 8.03 seconds**. This includes rejection of a protocol-2
submission result missing its manifest hash.

## Passing live result

Disposable runtime:
`/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-be7031d56d9c4a498b583ad2d90fd274-o0kw6i0h`

The unchanged journey then completed:

```json
{"accepted_price": 60, "final_status": "approved", "input_bytes": 33, "operator_balance_delta": 60, "output_bytes": 9, "poster_balance_delta": -60, "result": "passed", "scope": "live isolated UI proxy API worker journey"}
```

The operator signed its own bid, downloaded the exact task input, uploaded and
submitted the exact output, and received the accepted price of 60. The poster
downloaded the submitted output before approval. Poster and operator balances
conserved funds: their deltas were -60 and +60. The task ended `approved`.

Both isolated runs stopped their owned service processes. The optional dispute
and rebuttal branch was not exercised in this bounded journey; existing dispute
and rebuttal tests remain the evidence for that branch.

## Browser control proof and governed artifact projection

The first real-browser extension loaded the accepted task but never rendered
`#btn-submit-deliverable`. The UI drilldown read only legacy `board_assets`; the
operator's successful governed upload was stored in `board_artifact_uploads` and
was absent from `task.assets`. Runtime:
`/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-e3d648d8e0d44579b3164141096aaf95-fwxpf6qo`.

The drilldown now merges ready and archived governed artifacts with active legacy
assets, deduplicates by asset id, preserves uploader, input/output role, filename,
media type, size, hash and timestamp, and does not expose object or storage keys.
The API response schema retains the role field.

Focused projection and browser checks include:

- A ready and an archived governed output both appear once.
- Private storage fields remain absent.
- A valid protocol-2 browser acknowledgment clears the saved intent.
- A response whose worker differs from the saved operator retains the intent.
- Backend proxy validation rejects the same wrong-worker response with 502.

The same journey was then run through an actual headless Chromium task page. The
browser rendered and clicked `#btn-submit-deliverable`, displayed `SUBMITTED`,
and showed no operation-recovery control. The independent poster downloaded the
output and approved it. Final result:

```json
{"accepted_price": 60, "final_status": "approved", "input_bytes": 33, "operator_balance_delta": 60, "output_bytes": 9, "poster_balance_delta": -60, "result": "passed", "scope": "live isolated browser UI worker journey"}
```

Passing runtime:
`/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-4f00112954dc4fc2afd1cc455de69fc2-fg3mycrf`.
It stopped all owned services after the check.

Independent review then found that the first governed projection exposed the raw
artifact UUID rather than Task Board's canonical `asset-{artifact_id}`. A focused
boundary check reproduced two 404 responses when the UI's projected ready and
archived IDs were sent to the operator download proxy. The projection now uses
the canonical public IDs, and both projected IDs download successfully.

The final Chromium run used the governed asset ID returned by `/api/tasks/{id}`
to click the page's rendered **View as text** control. The preview contained the
exact 9 output bytes before the browser clicked Submit. It then completed the
same approval and accepted-price assertions above. Runtime:
`/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-8aa922cac8bb4af9a5feb431720e6858-iopeutg2`.
All owned processes stopped cleanly.

## Browser poster with input attachment

The complementary human-poster journey used the real task form in headless
Chromium. The operator selected and posted a 44-byte input file through the
protocol-2 prepare, admit, upload and publish sequence. An independently keyed
zero-balance worker downloaded the exact input through the public SDK, bid 60,
and the operator accepted that bid through the browser. The worker uploaded and
submitted a 9-byte output. The browser used the output asset link returned by the
UI, previewed the exact bytes, and approved the task.

```json
{"accepted_price": 60, "final_status": "approved", "input_bytes": 44, "operator_balance_delta": -60, "output_bytes": 9, "result": "passed", "scope": "live isolated browser UI poster with input", "worker_balance_delta": 60}
```

Runtime:
`/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-2cca325edafd465c9eb33e14bd76b946-amtbuhkj`.
It stopped every owned process after the check. The operator and worker balance
deltas conserved funds and matched the accepted price.
