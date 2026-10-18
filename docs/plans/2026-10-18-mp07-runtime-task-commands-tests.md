# MP-07 runtime Task Board command test interface

## Scope

This T-181 test-only slice defines the coordinator between the public Task Board SDK and the frozen
real-filesystem intent journal. It adds no product coordinator, compatibility cache, private raw HTTP
call, Gateway access, remote discovery, YAML value or dependency. Every persistence assertion uses
the production `FileTaskIntentStore`; the tests do not implement a second journal.

The implementation module is `base_agent.runtime_task_commands` and exposes
`RuntimeTaskCommands` plus `runtime_owner_from_agent`.

## Coordinator interface

`RuntimeTaskCommands(agent, store)` receives the public agent SDK and one already opened,
identity-bound store. Its public methods are:

```python
async def execute(
    *,
    task_id: str,
    slot: str,
    prepare: Callable[[], Awaitable[NewTaskIntent]],
) -> FrozenTaskIntent: ...

async def replay_unfinished() -> tuple[FrozenTaskIntent, ...]: ...
```

`execute` serializes work with an async lock scoped to the `(task_id, slot)` pair. It loads the slot
before awaiting `prepare`. A missing slot awaits the decision exactly once and durably creates the
returned intent before transport. A stored slot reuses its frozen public arguments and never invokes
the decision again. Different slot keys can progress concurrently.

The only dispatched public commands are:

- `submit_bid(task_id, amount, operation_id=..., expected_lifecycle_version=...)`;
- `accept_bid(task_id, bid_id, operation_id=..., expected_lifecycle_version=...)`;
- `submit_deliverable(task_id, operation_id=..., expected_lifecycle_version=...)`;
- `approve_task(task_id, operation_id=..., expected_lifecycle_version=...)`;
- `dispute_task(task_id, reason, operation_id=..., expected_lifecycle_version=...)`.

The coordinator does not read the current task or recompute amount, bid, answer, decision or reason.
A coordinator is admitted only when the agent's registered ID, raw-public-key fingerprint and exact
five configured service URLs match `store.owner`. It checks this at construction and again after any
awaited preparation, immediately before transport. Replay performs the same check before dispatch.
Identity or endpoint drift raises local `owner_conflict` and performs no SDK call.

The dispute intent freezes only facts available before transport: worker identity and exact reason.
It does not invent the Court-generated dispute ID or effect reference. A completed acknowledgment
supplies that ID and the journal must bind it to the operation result and effect reference.

A typed `TaskBoardCommandResponse` records its actual status and body. An `httpx.HTTPStatusError`
records its response's actual status and decoded object body. A `TaskBoardCommandContractError`
retains its actual status with `body=None`; the coordinator does not invent a success, error body or
status. These expected observations return the updated `FrozenTaskIntent` and are not re-raised.

An `httpx.RequestError`, including timeout and connection loss, returns the original stored record
without writing a diagnostic or advancing its revision. Any unforeseen programming error, invalid
prepared intent, or local journal error propagates. A local failure before transport never calls the
SDK.

`completed`, `definitive_rejection` and `attention_required` records return unchanged without
preparation or transport. A `blocked` record gets one exact transport attempt per explicit execute or
replay pass; it is not spun in an internal loop.

`replay_unfinished()` snapshots `store.unfinished()` and processes that deterministic task/slot order.
It returns one record per snapshot item in the same order, including unchanged attention records.
Request loss for one item does not prevent later known items from running. It calls no task-list or
other discovery endpoint; broader authoritative discovery remains in MP-17/18.

## Runtime owner binding

`runtime_owner_from_agent` has five required keyword-only inputs:

```python
runtime_owner_from_agent(
    *,
    runtime_config: RuntimeStateConfig,
    agent: object,
    role: str,
    profile: str,
    handle: str,
) -> RuntimeOwner
```

The returned owner has schema version 1, the exact unnormalized environment/role/profile/handle,
the already registered agent ID, and the exact five configured service URLs. The key fingerprint is
lowercase SHA-256 over the Ed25519 public key's raw 32 bytes obtained through the real public
`agent.get_public_key_b64()` SDK boundary. Base64 must be canonical and decode to exactly 32 bytes;
malformed encoding or length fails locally. A missing registered ID also fails locally. The function
does not read `config.public_key` or any private key and needs no private SDK method, network call,
Task Board read or Gateway path.

## Test matrix

`agents/tests/unit/test_runtime_task_commands.py` contains 32 collected cases covering:

- required module, constructor and keyword-only owner-factory signatures;
- exact dispatch for all five native actions;
- real-file create-before-transport and reopen proof;
- existing-load-before-prepare and local preparation/storage failure ordering;
- construction and pre-dispatch registered-ID/key/URL owner checks, including drift while preparation
  is awaited and before replay;
- same-slot serialization and different-slot concurrency;
- unchanged bytes/revision after timeout or connection loss;
- actual HTTP-error status/body and contract-error status/`None` recording;
- propagation of unforeseen SDK errors;
- no transport from completed, definitive or attention states;
- one blocked attempt per replay pass;
- deterministic known-work replay, no remote discovery and no timeout starvation;
- exact owner, public-SDK raw-key fingerprint, URL and registration binding, plus malformed key
  encoding/length rejection.

The four non-dispute action builders and all completed/definitive/attention/blocked state fixtures
were separately created and admitted through the real `FileTaskIntentStore` under disposable
`/private/tmp` roots. The corrected dispute builder currently reaches a distinct journal prerequisite
failure, `invalid_input: dispute result facts are invalid`, because the current journal still requires
the unavailable Court-generated ID before transport. This is one product-contract prerequisite, not
a coordinator fixture error or an additional coordinator defect. Its output is retained at
`/private/tmp/agent-economy-mp07-runtime-task-commands-prerequisite.log` while the journal owner
reconciles that contract.

## Fresh RED and static evidence

From `agents/` with `UV_CACHE_DIR=/tmp/agent-economy-review-uv`, `UV_FROZEN=1` and `--no-sync`:

```text
uv run --no-sync pytest tests/unit/test_runtime_task_commands.py -q --tb=short
collected 32 items
1 failed, 31 errors in 0.28s
```

The single failure is `test_runtime_task_command_interface_is_available`: the product module does
not exist. The remaining 31 cases stop in the required-interface fixture and are classified as
unexecuted downstream assertions, not 31 independent product defects. No case is skipped, xfailed or
suppressed. Full output is retained at
`/private/tmp/agent-economy-mp07-runtime-task-commands-red-owner-final.log`.

The preceding 25-case source was frozen before the owner-binding correction at
`/private/tmp/agent-economy-mp07-runtime-task-commands-25case-source.py`, with its digest in the
adjacent `.sha256` file. The correction completed the public SDK fixture and added coverage; it did
not weaken or remove any original case.

The same test source passes Ruff formatting/linting, codespell and standalone strict Pyright with
zero errors or warnings. No full CI was run because this is an intentionally red interface baseline.
No product source, existing test, agent configuration, ordinary runtime state or Git history was
modified by this slice. T-181/T-182 remain open pending coordinator implementation, green proof,
independent review and the repository closure gate.
