# MP-07 Runtime Process-Crash Proof

## Scope

T-181 requires process-level evidence that the caller-owned runtime journal survives an uncatchable agent process death and replays the original operation. The proof covers the five runtime mutations shipped by `RuntimeTaskCommands`: bid, acceptance, submission, approval, and dispute.

Each mutation is exercised at two crash boundaries:

1. after the durable intent is published and the public SDK method is entered, before any request is sent;
2. after the real public SDK method returns a successful response, before the local acknowledgment is recorded.

The resulting ten histories run against the disposable seven-service stack created by `scripts/isolated_test_runtime.py`.

## Test contract

`agents/tests/e2e/test_mp07_runtime_crash_replay.py` provisions run-owned Ed25519 key material, explicit service URLs, a registered identity, and an explicit journal owner under the test temporary directory. Its child helper, `runtime_crash_actor.py`, is the only process that holds the journal lease while a command is in flight.

The child wraps only the selected public SDK mutation method. At the first boundary it publishes a durable gate before calling the method. At the second boundary it calls the real method, durably records the typed response, and then publishes the gate. The parent sends `SIGKILL` to the exact Python actor PID, waits for its direct `uv` launcher to reap it, and verifies that actor PID is gone. A fresh process durably publishes its own PID before opening the same identity, configuration, and journal, then calls only `RuntimeTaskCommands.replay_unfinished()`; it has no decision-preparation path.

For every history the test verifies:

- the killed child held a canonical revision-1 pending record whose complete semantic payload matches `intent.semantic_json()`, whose semantic hash is independently recomputed, and whose owner hash matches the reopened owner;
- the lease becomes available to the fresh replay process after `SIGKILL`;
- replay yields one typed revision-2 completed record where the only changed fields are `revision`, `state`, and `result`; diagnostics remain empty and every semantic and ownership field is unchanged;
- replay leaves exactly one bid, the selected worker and public lifecycle outcome are correct, and balances reflect exactly one economic effect;
- response-loss replay does not duplicate the already advanced public effect; a disputed task may still advance naturally to its autonomous ruling;
- approval retains its successful bank-release receipt identity and dispute retains its successful Court-claim reference.

The pending and completed journal byte hashes are computed and required to differ only because the acknowledgment creates the next revision. Child stdout and stderr are isolated in per-history temporary logs, and `finally` cleanup targets each exact Python actor PID before waiting for its direct launcher.

## Boundary

This proof closes the missing process-crash/restart coverage for the five MP-07 runtime task commands. It does not extend MP-07 to task posting, asset upload, oracle work, rebuttals, commitments, or broader operation discovery; those remain owned by MP-05, MP-09, MP-17, and MP-18.
