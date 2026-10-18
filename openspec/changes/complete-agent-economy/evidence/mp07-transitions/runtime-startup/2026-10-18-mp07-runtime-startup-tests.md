# MP-07 durable runtime startup test contract

## Scope

This T-181 test-only slice defines how the completed configuration, journal and command coordinator
compose at the worker and feeder process boundaries. It uses temporary configuration, provisioned
test keys, isolated state roots, finite fake loops and the real `FileTaskIntentStore` and
`RuntimeTaskCommands`. It starts no live API or LLM provider, handles no real signals, touches no
ordinary state, and changes no product or existing test.

## One explicit configuration path

Each entry point resolves `AGENT_CONFIG_PATH` or the explicit `config.yaml` location once, then passes
that exact `Path` to every loader. The worker passes it into `WorkerFactory`. The feeder passes it to
task-feeder, acceptance, agent and runtime-state loaders; no loader independently falls back to a
different environment or working-directory path.

`MathWorkerBundle` is a frozen construction-input value containing exactly:

- the loaded public SDK agent;
- the LLM client;
- `MathWorkerConfig`;
- the actual selected `profile_name`;
- `RuntimeStateConfig`.

The factory does not build `MathWorkerLoop`: registration and durable replay must occur first.

## Worker startup order and lifetime

Worker startup registers the loaded agent and creates its account before deriving a `RuntimeOwner`.
The owner uses explicit role `math-worker`, the bundle's actual profile name and the worker-config
handle. Startup opens one `FileTaskIntentStore`, constructs one `RuntimeTaskCommands`, and calls
`replay_unfinished()` before constructing `MathWorkerLoop`, filtered task discovery or any LLM call.
The constructed loop receives the same coordinator.

The store context and identity lease remain active until the loop returns or raises. The LLM and
agent clients close on normal completion and on registration-adjacent, store-open, replay or loop
failure. The lease is released afterward. A controlled loop proves the lease is busy through a
public exclusive nonblocking `hold_existing_maintenance_lock` attempt, and the same exact lock is
acquirable after startup returns.

Malformed `owner.json` or an owned malformed intent fails with `corrupt_record` before replay can
mutate a task and before loop construction or discovery. Cleanup still closes both clients and
releases any acquired lease.

## Feeder startup order and shared coordinator

Feeder startup registers and creates its account, then derives the owner using explicit role
`task-feeder` and the feeder handle for both `profile` and `handle`. It opens one store and constructs
one coordinator. Known pending intents replay before any feeder, acceptance or review loop is
launched.

`AcceptanceLoop` and `ReviewLoop` receive the same coordinator object; they never open independent
stores or leases. The feeder, acceptance and review tasks all run while the single lease remains
held. The lease is released and the public SDK client closes after all loops finish or after a loop
failure.

## Test matrix and baseline

`agents/tests/unit/test_mp07_runtime_startup.py` collects eight cases:

- one passing real-store fixture control for registered owner binding, empty replay, lease exclusion
  and post-context release;
- worker bundle field/no-prebuilt-loop contract;
- factory use of the single config path plus actual profile/runtime/config return values;
- worker registration/account/replay/loop/discovery/LLM ordering with normal cleanup;
- corrupt owner and corrupt journal failure paths;
- feeder explicit loaders, replay order, shared coordinator and lease lifetime;
- one passing async/signature control.

Fresh result:

```text
uv run --no-sync pytest tests/unit/test_mp07_runtime_startup.py -q --tb=short
6 failed, 2 passed in 0.18s
```

The bundle-shape and factory failures independently show the prebuilt-loop contract. Worker startup
and both corrupt-startup cases currently stop at the common missing explicit `config_path` argument
to `WorkerFactory`; the latter are downstream assertions, not two independently executed corruption
bugs. Feeder startup reaches its own missing coordinator injection at `AcceptanceLoop`.

The real corruption fixtures were probed separately and both fail with `corrupt_record`; retained
output is `/private/tmp/agent-economy-mp07-runtime-startup-fixture-probe.log`. The complete red is
`/private/tmp/agent-economy-mp07-runtime-startup-red-final.log`.

## Static and source evidence

The frozen test file passes Ruff formatting/linting, codespell and standalone strict Pyright with zero
errors or warnings. Baseline hashes for WorkerFactory, both mains and the three runtime modules are
recorded at `/private/tmp/agent-economy-mp07-runtime-startup-source-baseline.sha256`.

No full CI was run for this intentionally red producer slice. No product, existing test, dependency,
Git history, live service, ordinary key or ordinary runtime state was changed. T-181/T-182 remain
open pending implementation, green proof, independent review and the repository closure gate.

## Review amendment: admission precedes Bank mutation

This amendment supersedes the earlier worker and feeder wording that placed `create_account` before
owner/store admission. Registration must occur first because it supplies the immutable agent ID.
After registration the safe order is:

1. derive the exact runtime owner;
2. acquire the identity lease and validate `owner.json` plus every owned journal record;
3. only after successful admission, create or confirm the Bank account;
4. replay the already validated known intents;
5. log a bounded replay summary;
6. construct and launch loops.

A corrupt owner, corrupt owned intent or busy lease therefore causes no Bank account request, Task
Board mutation, LLM call, discovery, or loop construction. Registration still occurs. Clients close
and any acquired lease releases on each failure path.

Replay logging is an operator diagnostic, not persisted journal data. For `pending`, `blocked`, and
`attention_required` results it reports aggregate state counts and total work. At most the explicit
`runtime_state.diagnostic_limit` operation IDs may appear; the summary reports logged and omitted
counts. It never logs private keys, signed tokens, response bodies, reasons or other arbitrary
upstream content. Nonterminal replay results do not prevent independent loops from starting.

The feeder's `TaskGroup` remains the required sibling-failure behavior. When one loop raises, sibling
tasks finish their cancellation `finally` blocks while the identity lease is still held. The agent
closes only after that drain, and the lease releases afterward. The new control proves the current
implementation already satisfies this sequence; it is not a missing product behavior.

`agents/tests/unit/test_mp07_runtime_startup_regressions.py` collects eight cases. The six
worker/feeder owner, journal and busy-admission cases are genuine reds because current startup calls
`create_account` before admission. The bounded replay-summary case is a separate genuine red. The
feeder sibling-drain case passes as the expected control.

Fresh result:

```text
uv run --no-sync pytest tests/unit/test_mp07_runtime_startup_regressions.py -q --tb=short
7 failed, 1 passed in 0.19s
```

Full output is `/private/tmp/agent-economy-mp07-runtime-startup-regressions-red-final.log`. The
pre-amendment startup suite passes all eight cases and remains the compatibility baseline. No prior
test or product source was edited for this amendment.
