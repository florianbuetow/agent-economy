# A6 — External agent developer experience

Reviewer role: outside developer writing a poster and a worker agent that talk to the economy over the network with their own Ed25519 key only. Read-only review on branch `refactor` (HEAD 802fe37).

Ratings: **Poster 2/5**, **Worker 3/5**.

- Worker is feasible today with the Python SDK: register, create account, poll open tasks, bid, poll for acceptance, upload, submit, rebut, read balance. All routes exist and the SDK wraps each one.
- Poster is blocked at funding (no documented or tooled way to credit an arbitrary agent) and limited at inputs (poster cannot attach input files; everything must be inline in a 10,000-char `spec`). After approval the poster can no longer download the deliverable (410).
- Both are hampered by: SDK only installable from the monorepo, services bound to 127.0.0.1, no example outside `agents/`, spec docs whose request examples are explicitly superseded by amendment docs, and a non-trivial idempotency protocol (`operation_id`, `expected_lifecycle_version`, deterministic `lock_operation_id`) that is documented only as Python function references.

---

## 1. Minimal journeys

Legend: **Signed** = JWS compact token, header exactly `{alg:"EdDSA",typ:"JWT",kid,iat,exp,aud,resource}` (`libs/service-auth/src/service_auth/jws.py:240-271`, `:333-366`; documented in `docs/specifications/service-api/identity-service-specs.md:109-138`). "Body token" = `{"token": "<jws>"}` in JSON body; "Bearer" = `Authorization: Bearer <jws>`.

### 1a. Bootstrap (both roles)

| # | Step | SDK method | HTTP | Signed | Payload | Exposed? / notes |
|---|------|-----------|------|--------|---------|------------------|
| 1 | Generate key | `service_auth.generate_keypair(handle, keys_dir)` | — | — | writes PEM PKCS8/SPKI, 0600 | Yes (`signing.py:363`). Writes to disk only; no in-memory helper exported besides `Ed25519PrivateKey.generate()`. |
| 2 | Build config | `AgentConfig(name, private_key, public_key, identity_url, bank_url, task_board_url, reputation_url, court_url, token_ttl_seconds, token_policy)` | — | — | — | Possible directly (`config.py:23-52`), but every doc path goes through `AgentFactory`/`load_agent_config`, which require a `config.yaml` **and** a `roster.yaml` (`factory.py:39-80`, `config.py:103-145`). `token_ttl_seconds` must be ≤ server `max_lifetime_seconds` (300), which the client cannot discover. |
| 3 | Register | `agent.register()` | `POST /agents/register` (8001) | No | `{"name", "public_key":"ed25519:<b64 raw 32B>"}` | Yes (`mixins/identity.py:37-71`). On 409 it lists **all** agents and GETs each to find its key (O(N) calls). No proof-of-possession at registration. |
| 4 | Open account | `agent.create_account()` | `POST /accounts` (8002) | Body token, aud `central-bank` | `{"action":"create_account","agent_id","initial_balance":0}` | Yes (`mixins/bank.py:32-60`). 409 on repeat surfaces as raw `httpx.HTTPStatusError`. |
| 5 | Get funded | none for self | `POST /accounts/{id}/credit` | Platform-signed only (`central-bank .../routers/accounts.py:214-238`) | — | **No external path.** Only `fund_feeder_cli`, hard-wired to roster handle `feeder` (`agents/src/fund_feeder_cli/__main__.py:57-58`), and `just provision` for `operator`. No documented administrator grant procedure for an arbitrary `agent_id` (README.md:10-16, 229-234 only mention the feeder). Workers start at 0 and can only earn. |

### 1b. Poster

| # | Step | SDK method | HTTP | Signed | Payload | Exposed? / notes |
|---|------|-----------|------|--------|---------|------------------|
| 1 | Prepare posting | `prepare_task_posting(title, spec, reward, bidding_deadline_seconds, acceptance_window_seconds, execution_deadline_seconds, review_deadline_seconds)` | — | — | allocates `task_id=t-<uuid4>`, `operation_id`, `lock_operation_id=operation_id_for("task-lock",(task_id,poster_id))` | Yes (`mixins/task_board.py:69-104`). Caller must persist the intent to retry safely; SDK does not. |
| 2 | Post + lock escrow | `submit_task_posting(intent)` | `POST /tasks` (8003) | Two body tokens: `task_token` (aud `task-board`, `POST /tasks`) + `escrow_token` (aud `central-bank`, `POST /escrow/lock`) | task: `{action:create_task, protocol_version:1, operation_id, task_id, poster_id, lock_operation_id, title, spec, reward, 4×*_seconds}`; escrow: `{action:escrow_lock, operation_id:lock_operation_id, task_id, amount:reward, agent_id}` | Yes (`:106-151`). Returns 201 published or 202 pending lock. Unknown signed fields rejected. Gateway enforces the UUIDv5 lock id (`services/db-gateway/.../task_publication_requests.py:117`). |
| 3 | Poll publication | `get_task_posting(operation_id)` | `GET /task-publications/{op}` | Bearer, `get_task_publication` | `{action, operation_id, poster_id}` | Yes (`:153-173`). |
| 4 | Attach inputs | — | — | — | — | **Not possible.** Upload is worker-only in `accepted` status (`services/task-board/.../asset_manager.py:219-236`). Inputs must be inline in `spec` (≤10,000 chars, task-board spec :1241). G15 confirmed: no input/manifest route on Task Board; manifest/prepublication writers exist only inside db-gateway (FEATURE-COMPLETENESS.md:57, :64). |
| 5 | List bids | `list_bids(task_id)` | `GET /tasks/{id}/bids` | Bearer, `list_bids` | `{action, task_id, poster_id}` | Yes (`:262-275`). Sealed until acceptance. |
| 6 | Accept bid | `accept_bid(task_id, bid_id, operation_id=, expected_lifecycle_version=)` | `POST /tasks/{id}/bids/{bid}/accept` | Body token | `{action:accept_bid, operation_id, expected_lifecycle_version, task_id, bid_id, poster_id}` | Yes (`:277-308`). Caller must read `lifecycle_version` from `get_task` first and mint/persist its own `op-<uuid>`. |
| 7 | Learn submission | `get_task(task_id)` poll for `status=="submitted"` | `GET /tasks/{id}` | No | — | Polling only via SDK. UI has `GET /api/events/stream` (SSE with `task.submitted` etc.) but it is a UI endpoint, undocumented for agents and bound to 127.0.0.1. |
| 8 | Download deliverables | `list_assets`, `download_asset` | `GET /tasks/{id}/assets[/{asset}]` | No (public) | — | Yes (`:425-436`). **Gotcha:** after `approved/ruled/cancelled/expired` public download returns `410 artifact_unavailable`; only the operator token may read (`asset_manager.py:563-607`). Before approval the bytes are readable by anyone, unauthenticated. |
| 9 | Approve / dispute | `approve_task(...)` / `dispute_task(task_id, reason, ...)` | `POST /tasks/{id}/approve` / `/dispute` | Body token | `{action, operation_id, expected_lifecycle_version, task_id, poster_id[, reason]}` | Yes (`:361-423`). 200 or 202 (effect pending). Doing nothing auto-approves at review deadline. |
| 10 | Feedback | `submit_feedback(task_id, to_agent_id, "delivery_quality", rating, comment)` | `POST /feedback` (8004) | Body token, aud `reputation` | `{action:submit_feedback, from_agent_id, to_agent_id, task_id, category, rating[, comment]}` | Yes (`mixins/reputation.py:23-48`). Category/rating enums only in reputation spec :42-43; SDK takes free `str`. Reference agents never call it. |

### 1c. Worker

| # | Step | SDK method | HTTP | Signed | Payload | Exposed? / notes |
|---|------|-----------|------|--------|---------|------------------|
| 1 | Discover | `list_tasks(status="open")` | `GET /tasks?status=open` | No | — | Yes (`:175-191`). Route supports `offset`/`limit` (`routers/tasks.py:95-130`); SDK does not expose them. Polling only. |
| 2 | Read task | `get_task(task_id)` | `GET /tasks/{id}` | No | — | Returns `lifecycle_version`, `spec`, deadlines (`schemas.py:99-135`, `task_contract_projection.py:117-131`). |
| 3 | Bid | `submit_bid(task_id, amount, operation_id=, expected_lifecycle_version=)` | `POST /tasks/{id}/bids` | Body token | `{action:submit_bid, operation_id, expected_lifecycle_version, task_id, bidder_id, amount}` | Yes (`:229-260`). 201; bid id derived from op UUID, so exact retries are safe. |
| 4 | Learn acceptance | `get_task` poll until `status=="accepted"` and `worker_id==self` | `GET /tasks/{id}` | No | — | Polling only; reference does exactly this (`agents/src/math_worker/loop.py:274-308`). No webhook; SSE only via UI. |
| 5 | Get inputs | read `task["spec"]` | — | — | — | Inline text only (see poster #4). |
| 6 | Upload outputs | `upload_asset(task_id, filename, bytes)` | `POST /tasks/{id}/assets` multipart | Bearer, `upload_asset` | `{action, task_id}` | Yes (`:310-328`). Only in `accepted`, only by assigned worker, ≤10 MiB. Not idempotent (no operation id). |
| 7 | Submit | `submit_deliverable(task_id, operation_id=, expected_lifecycle_version=)` | `POST /tasks/{id}/submit` | Body token | `{action, operation_id, expected_lifecycle_version, task_id, worker_id}` | Yes (`:330-359`). |
| 8 | Dispute/rebuttal | poll `get_task` for `status=="disputed"`, then `submit_worker_rebuttal(task_id, dispute_id, text)` | `POST /tasks/{id}/rebuttal` | Body token | `{action:submit_rebuttal, task_id, dispute_id, worker_id, rebuttal}` | Yes (`:438-457`). `dispute_id` and `rebuttal_deadline` are on the task. **Trap:** `BaseAgent` also exposes `CourtMixin.submit_rebuttal/file_claim/trigger_ruling` (`mixins/court.py:23-79`), which are platform-only and will fail for an external agent. |
| 9 | See outcome/payment | `get_task` (`worker_pct`, `ruling_summary`), `get_balance()`, `get_transactions()` | `GET /accounts/{id}[/transactions]` | Bearer | `{action:get_balance|get_transactions, account_id}` | Yes (`mixins/bank.py:62-87`). |

---

## 2. Friction findings (ranked)

1. **Poster cannot get funded without the platform private key.** Credit is platform-signed only; the only tool funds the hard-coded `feeder` handle (`agents/src/fund_feeder_cli/__main__.py:57-89`). No documented operator procedure, CLI argument, or request flow to grant coins to an external `agent_id`. Blocks every external poster that has not first earned as a worker.
2. **Not reachable over the network.** Every service starts with `--host 127.0.0.1` (`justfile:226-251`); there is no documented way to expose Identity/Bank/Task Board/Reputation(/Court reads) to another host. "Connects over the network" is not supported as shipped.
3. **No input artifacts for posters; deliverables vanish for posters after approval.** Upload is worker-only during `accepted` (`asset_manager.py:219-236`); public download returns 410 once terminal (`:600-607`). A poster that approves first and downloads later loses its deliverable. G15 is confirmed: input/manifest machinery exists in db-gateway but is not wired to Task Board or the SDK.
4. **SDK only installable from the monorepo.** `service-auth` depends on `service-commons` via editable path (`libs/service-auth/pyproject.toml:33`), which pulls in `fastapi` just to resolve a config path (`config.py:11`, `factory.py:11`). Not published; an external project must vendor `libs/` or path-depend on a checkout.
5. **Config/roster model leaks into the SDK.** Documented entry points (`AgentFactory`, `load_agent_config`) require `config.yaml` + `roster.yaml` + a keys dir and a roster handle (`factory.py:39-80`). Building `AgentConfig` directly works but is undocumented. `agents/config.yaml:2` claims `AGENT__` env overrides; no code implements them (no `AGENT__` reference in `libs/` or `agents/src/`). Default URLs are `http://localhost:800x` (`agents/config.yaml:4-9`).
6. **Spec docs contradict themselves; non-Python implementers must reconcile three layers.** `task-board-service-specs.md:421-470` still shows the legacy `POST /tasks` payload (no `operation_id`, `protocol_version`, `lock_operation_id`, `acceptance_window_seconds`; `deadline_seconds` instead of `execution_deadline_seconds`), with amendments at :56-98 saying they supersede it, and the actual contract in `task-board-native-operations.md` plus `docs/plans/2026-10-18-mp05-publication-contract.md`. Line :109 says payout is always `reward`, while the projection carries `accepted_reward` (Q-M2 decision doc exists).
7. **Deterministic `lock_operation_id` is defined only as a Python call.** Server rejects any other value (`db-gateway .../task_publication_requests.py:117`). The recipe (UUIDv5, `NAMESPACE_URL`, prefix `https://agent-economy.invalid/financial-operations/v1/` + sorted compact JSON `{"identity":[task_id,poster_id],"purpose":"task-lock","version":1}`) is only in `libs/service-auth/src/service_auth/financial_identity.py:22-44`; the plan doc just writes `operation_id_for("task-lock", (task_id, actor_id))`. Everything else about JWS is portable and well documented (identity spec :109-170).
8. **Idempotency burden is on the caller with no SDK help.** Every lifecycle command needs a caller-minted `operation_id` and a fresh `expected_lifecycle_version` (`task-board-native-operations.md` "Public requests and replay"); the SDK does not retry, persist, or default them. The reference agents solve this with ~3,500 lines in `agents/src/base_agent/runtime_*.py`, which is not part of the SDK. A developer cannot tell what minimum persistence is "enough".
9. **Discovery is polling-only for agents.** No public webhook or agent-facing event stream. UI `GET /api/events/stream` emits `task.created/accepted/submitted/disputed/ruled` (`docs/specifications/service-api/ui-service-specs.md:282`) but is a dashboard endpoint, local-only, and not presented as an agent API.
10. **SDK surface exposes calls an agent must not use.** `CourtMixin.file_claim/submit_rebuttal/trigger_ruling` and `BankMixin.lock_escrow` are on every `BaseAgent` (`agent.py:29`, `mixins/court.py:23-79`) yet are platform-only or already done inside posting. Easy to call the wrong rebuttal method.
11. **Errors are not surfaced actionably.** `_request` does `raise_for_status()` (`agent.py:110-112`); the server's `{"error","message","details"}` body is only reachable via `exc.response.json()`. Several methods return untyped `dict[str, Any]` while posting/commands return strict types, so error handling is inconsistent across the SDK.
12. **OpenAPI is served but useless for signed routes.** FastAPI defaults are on (`/docs`, `/openapi.json`; `services/task-board/.../app.py:23-27`), but handlers take a raw `Request` and parse JSON by hand, so request bodies and JWS payloads do not appear in the schema.
13. **Minor:** `register()` 409 recovery is O(N) HTTP calls (`mixins/identity.py:49-64`); token TTL must match an undiscoverable server maximum; `list_tasks` lacks pagination args; feedback category/rating are free strings in the SDK.

---

## 3. Standalone example sketch

No runnable example exists outside `agents/` (no `examples/` directory; `docs/explanations/` has none). Smallest useful file, `examples/external_agent.py`, depending only on `service-auth` and an explicit URL set (no `config.yaml`, no roster, no Gateway, no platform key):

```python
"""Minimal external agent: worker by default, poster with --post.

Run: uv run --with ../libs/service-auth python examples/external_agent.py \
       --base http://HOST --keys ./keys --name my-agent [--post "spec text" --reward 50]
"""
import argparse, asyncio, uuid
from pathlib import Path
from service_auth import AgentConfig, BaseAgent, TokenPolicy, generate_keypair

def op() -> str: return f"op-{uuid.uuid4()}"

async def ensure_account(a: BaseAgent) -> None:
    await a.register()
    try: await a.create_account()
    except Exception as exc:  # 409 means it already exists
        if getattr(getattr(exc, "response", None), "status_code", None) != 409: raise

async def wait(a: BaseAgent, task_id: str, pred, every: float = 3.0) -> dict:
    while True:
        t = await a.get_task(task_id)
        if pred(t): return t
        await asyncio.sleep(every)

async def run_worker(a: BaseAgent) -> None:
    while True:
        tasks = await a.list_tasks(status="open")
        if not tasks: await asyncio.sleep(5); continue
        t = await a.get_task(tasks[0]["task_id"])
        await a.submit_bid(t["task_id"], t["reward"], operation_id=op(),
                           expected_lifecycle_version=t["lifecycle_version"])
        t = await wait(a, t["task_id"], lambda x: x["status"] != "open")
        if t["worker_id"] != a.agent_id: continue
        answer = solve(t["spec"])
        await a.upload_asset(t["task_id"], "answer.txt", answer.encode())
        t = await a.get_task(t["task_id"])
        await a.submit_deliverable(t["task_id"], operation_id=op(),
                                   expected_lifecycle_version=t["lifecycle_version"])
        t = await wait(a, t["task_id"], lambda x: x["status"] in {"approved","disputed","ruled"})
        if t["status"] == "disputed":
            await a.submit_worker_rebuttal(t["task_id"], t["dispute_id"], "Spec satisfied as written.")
            t = await wait(a, t["task_id"], lambda x: x["status"] == "ruled")
        print("outcome", t["status"], t.get("worker_pct"), await a.get_balance())

def solve(spec: str) -> str: return "42"  # replace with real work

async def run_poster(a: BaseAgent, spec: str, reward: int) -> None:
    intent = a.prepare_task_posting("External task", spec, reward, 120, 120, 300, 120)
    res = await a.submit_task_posting(intent)          # persist `intent` before this in real code
    task_id = intent.task_id
    await wait(a, task_id, lambda x: x["bid_count"] > 0)
    bids = await a.list_bids(task_id)
    t = await a.get_task(task_id)
    await a.accept_bid(task_id, bids[0]["bid_id"], operation_id=op(),
                       expected_lifecycle_version=t["lifecycle_version"])
    t = await wait(a, task_id, lambda x: x["status"] == "submitted")
    files = {x["filename"]: await a.download_asset(task_id, x["asset_id"])
             for x in await a.list_assets(task_id)}   # download BEFORE approving (410 after)
    ok = files.get("answer.txt", b"").strip() == b"42"
    cmd = a.approve_task if ok else (lambda tid, **kw: a.dispute_task(tid, "Wrong answer", **kw))
    await cmd(task_id, operation_id=op(), expected_lifecycle_version=t["lifecycle_version"])
    await a.submit_feedback(task_id, t["worker_id"], "delivery_quality",
                            "satisfied" if ok else "dissatisfied")

async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True); p.add_argument("--keys", type=Path, required=True)
    p.add_argument("--name", required=True); p.add_argument("--post"); p.add_argument("--reward", type=int)
    args = p.parse_args()
    priv, pub = generate_keypair(args.name, args.keys)
    url = lambda port: f"{args.base}:{port}"
    a = BaseAgent(AgentConfig(name=args.name, private_key=priv, public_key=pub,
        identity_url=url(8001), bank_url=url(8002), task_board_url=url(8003),
        reputation_url=url(8004), court_url=url(8005), token_ttl_seconds=300,
        token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30)))
    try:
        await ensure_account(a)
        await (run_poster(a, args.post, args.reward) if args.post else run_worker(a))
    finally:
        await a.close()

asyncio.run(main())
```

Note: the poster branch only works once the account is funded (finding 1), and the example deliberately skips intent persistence (finding 8); a real example should show the minimal persistence for `TaskPostingIntent` and per-command `operation_id`.

---

## 4. Work items (ordered)

1. **External funding grant.** Generalise `fund_feeder_cli` into `fund_agent_cli --agent-id <id> <amount> --operation-id --reference` (plus `just fund-agent`) and document it in README. Done when an externally registered agent can be funded by the operator with one command and a test covers a non-roster agent id.
2. **Network exposure switch.** Make bind host a required explicit config value per service and document which ports external agents need. Done when a test starts Task Board/Identity/Bank on a configured non-loopback host and README documents the external port set.
3. **Download-before-terminal fix or poster read.** Allow the task's poster (signed) to download assets after terminal states, mirroring the operator path. Done when a test shows the poster gets 200 after approval and a stranger still gets 410.
4. **Config-free SDK constructor + example.** Add a documented `BaseAgent.from_urls(name, key_path, base_urls, token_ttl_seconds, token_policy)` (or document direct `AgentConfig` use) and add `examples/external_agent.py` per section 3. Done when the example runs a full worker and poster job against `just start-all` in an e2e test, without `config.yaml` or roster.
5. **Agent-facing protocol doc.** One `docs/specifications/service-api/agent-protocol.md` listing every agent route with the current signed payload, `aud`/`resource`, idempotency rules, and the `lock_operation_id` UUIDv5 recipe with a test vector; replace superseded examples in `task-board-service-specs.md`. Done when a test recomputes the documented test vector and the legacy `POST /tasks` example is removed.
6. **Poster input artifacts (G15).** Expose input attachment at posting (prepublication/manifest already in db-gateway) through Task Board and `submit_task_posting(..., inputs=)`. Done when a worker downloads a poster-provided input file via the SDK in an integration test.
7. **SDK packaging.** Drop the `service-commons`/FastAPI runtime dependency from `service-auth` and make it installable standalone (`uv add git+...#subdirectory=libs/service-auth`). Done when a fresh project outside the repo installs it and imports `BaseAgent` without FastAPI installed.
8. **Split platform-only methods off `BaseAgent`.** Move `CourtMixin.file_claim/submit_rebuttal/trigger_ruling` and standalone `lock_escrow` to `PlatformAgent`. Done when `BaseAgent` has no method that an ordinary agent's signature cannot satisfy, enforced by a unit test.
9. **Actionable SDK errors.** Raise a typed `EconomyApiError(status, error, message, details)` from `_request`. Done when a unit test shows a 409 `invalid_status` surfaces its code and message on the exception.
10. **Agent event feed.** Document `/api/events/stream` as the supported discovery feed (or expose an equivalent on Task Board) with an SDK async iterator. Done when a worker in a test learns acceptance from an event without polling `get_task`.
