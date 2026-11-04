# Isolated UI worker journey — 2026-11-04

Command:

```text
uv run --directory services/db-gateway /Users/flo/Developer/github/agent-economy/scripts/isolated_test_runtime.py --provision -- uv run --project agents /tmp/agent-economy-goal-2026-11-04/ui_worker_journey.py
```

Disposable runtime:
`/private/var/folders/d5/vbvfgyfs4757w9nrv35rn_7m0000gn/T/agent-economy-f201d14b4fdb45708ed6a8beec731be4-2fav94eo`

Result: **failed at UI proxy submission contract validation**.

Verified before failure:

- Operator identity returned by `/api/proxy/identity` matched the isolated operator key.
- Operator submitted its own signed bid through `/api/proxy/tasks/{task_id}/bids` (201).
- Independent poster accepted the operator bid (200).
- Operator read the frozen task input manifest and downloaded the exact input bytes through UI proxy routes (200).
- Operator uploaded the exact output through the UI proxy with an idempotency key (201).
- Task Board accepted the operator-signed governed submission (200).

Failure:

- UI `/api/proxy/tasks/{task_id}/submit` returned 502:
  `{"error":"deliverable_submission_failed","message":"Task Board returned a malformed success response","details":{}}`.
- The governed Task Board operation uses protocol version 2. UI mutation validation currently requires protocol version 1 for every command, including `submit_deliverable`.
- Approval and accepted-price payment were not reached and remain unverified by this run.

The isolated runtime stopped its owned services. No ordinary services, data, or keys were used.
