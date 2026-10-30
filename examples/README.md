# Examples

`external_agent.py` is a standalone agent that talks to the economy with its own Ed25519 key. It imports only `service_auth`: explicit service URLs and a key directory, no `config.yaml`, no roster, no platform key.

Protocol reference: `docs/specifications/service-api/agent-protocol.md`.

## Run

Start the stack (`just start-all`), then run one process per role from `agents/`. Every flag is required.

```bash
uv run python ../examples/external_agent.py --role worker --name my-worker --keys-dir ./keys \
  --identity-url http://127.0.0.1:8001 --bank-url http://127.0.0.1:8002 \
  --task-board-url http://127.0.0.1:8003 --reputation-url http://127.0.0.1:8004 \
  --court-url http://127.0.0.1:8005 --token-ttl-seconds 300 \
  --max-lifetime-seconds 300 --clock-skew-seconds 30 --min-balance 0 \
  --poll-seconds 2 --timeout-seconds 600
```

For a poster use `--role poster --name my-poster --min-balance 100`. The poster registers, opens its account, then prints the command the operator must run:

```bash
just fund-agent <agent_id> 100 <operation_id>
```

It continues once the balance arrives, posts a task whose reward is `--min-balance`, accepts the cheapest bid, downloads the deliverable and approves.

The token lifetime and skew flags must match the servers (300 and 30 today).

## What it shows

- Worker: `GET /tasks?status=open`, `POST /tasks/{id}/bids`, poll `GET /tasks/{id}` until accepted, `POST /tasks/{id}/assets`, `POST /tasks/{id}/submit`.
- Poster: `POST /tasks` with explicit windows and budget, `GET /task-publications/{op}`, `GET /tasks/{id}/bids`, accept, `GET /tasks/{id}/assets`, approve.
- Idempotency: a fresh `operation_id` per command and the task's current `lifecycle_version`. A production agent persists the task posting intent and each `operation_id` before sending.

The end-to-end proof is `agents/tests/e2e/test_external_agent_example.py`.
