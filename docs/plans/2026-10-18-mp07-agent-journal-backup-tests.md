# MP-07 agent journal backup-integrity test contract

## Scope

The durable agent journal contains caller-owned retry authority. Losing it during backup or turning a
previously absent journal into an empty directory changes runtime behavior even when database and key
restore succeeds. This T-181 test-only slice defines its neutral recovery inventory, snapshot fencing,
byte preservation and restore diagnostics. It does not change recovery product code, create ordinary
runtime state, or claim that hand-written stored fixture bytes exercise journal behavior.

## Inventory contract

The `agents` configuration contributes exactly one filesystem binding for
`runtime_state.directory`. Relative values use `PathResolutionBase.CONFIG_PARENT`, matching
`load_runtime_state_config`; the recovery process CWD and package root are not valid substitutes.
The binding targets logical resource `agents.runtime_state`, with `ResourceKind.DIRECTORY` and no
silent required-directory assumption.

A present journal uses `CaptureKind.COPY`. A journal absent before its first use uses the new
`CaptureKind.ABSENT_DIRECTORY`, represented by a directory resource with explicit absence evidence.
It is not `DIRECTORY_ONLY`: restore must leave the target absent rather than creating an empty root.

## Present snapshot fencing

Before copying `agents.runtime_state`, backup enumerates the exact existing identity directories and
their real `runtime.lock` files. In deterministic path order it acquires every existing identity lock
through public `hold_existing_maintenance_lock`, with `MaintenanceLockMode.EXCLUSIVE` and
`blocking=False`, bound to the observed device and inode. All identity leases remain held together
through directory copy, open-file verification and final identity-set/lock revalidation.

A busy lease fails with `runtime_active`. A new, disappeared or replaced identity/lock during the
snapshot fails closed without `COMPLETE`; existing `source_changed` or durable-publication failure
wrapping is acceptable when the source mutation is still rejected. The backup operation does not
further modify the injected source state.

The journal remains an opaque copied directory at this boundary. `owner.json`, `runtime.lock` and
intent bytes and exact `S_IMODE` values are restored. Recovery does not rewrite owner environment, identity,
profile, key fingerprint, service URLs or intent content.

An identity directory can survive a crash after its private directories, `runtime.lock`, or intent
prerequisites exist but before exclusive `owner.json` publication completes. Recovery verification
must enumerate identity directories rather than only globbing owner files. A missing, nonregular,
malformed or otherwise invalid owner produces finding `agent_runtime_owner_invalid`, keeps
`identity_complete=False` and `operational_restore_verified=False`, and preserves the stored bytes
without fabricating an owner. `snapshot_preserved=True` remains correct when the incomplete source was
faithfully preserved.

## Restore owner-binding diagnostic

Target configuration rendering may explicitly select different service URLs. The preserved snapshot
still restores byte-for-byte, but its owner records remain bound to the source URLs. Verification must
therefore include finding code `agent_runtime_owner_binding_mismatch` and keep
`operational_restore_verified=False`. It must not silently adopt, normalize or rewrite the owner.

## Test matrix and honest baseline

`tests/runtime/test_mp07_agent_journal_inventory.py` has three independent reds:

- the agents field is omitted from discovery;
- the filesystem field has no recovery-resource policy;
- `CaptureKind.ABSENT_DIRECTORY` and its directory-resource shape do not exist.

Fresh command and result:

```text
PYTHONPATH=<repo>:<repo>/services/db-gateway/src uv run --no-sync pytest \
  ../../tests/runtime/test_mp07_agent_journal_inventory.py -q --tb=short
3 failed in 0.21s
```

Full output is `/private/tmp/agent-economy-mp07-agent-journal-inventory-red-final.log`.

The original `services/db-gateway/tests/unit/test_mp07_agent_journal_backup.py` baseline collected
eight cases. Five were genuine gaps at that checkpoint:

- a busy identity lease does not block backup;
- identity locks are not acquired/held through copy verification;
- a newly appearing identity is not detected;
- absent-directory capture does not exist;
- changed target URLs produce no owner-binding finding.

Three controls already pass and remain required: an explicitly inventoried present journal restores
owner/intent/lock bytes exactly; existing pinned-file checks reject replaced and disappeared identity
paths without publishing `COMPLETE`. The latter two controls accept the current typed rejection
wrapping and assert backup causes no additional journal/detached-directory byte change.

Fresh command and result:

```text
uv run --no-sync pytest tests/unit/test_mp07_agent_journal_backup.py -q --tb=short
5 failed, 3 passed, 1 warning in 0.57s
```

Full output is `/private/tmp/agent-economy-mp07-agent-journal-backup-red-final.log`. The warning is an
existing Starlette/httpx deprecation emitted at import, unrelated to these cases.

Before adding owner validation, the eight-case source and plan were frozen at
`/private/tmp/agent-economy-mp07-agent-journal-backup-8case-source.py` and the adjacent `.sha256`
manifest. After the parent integrated the first recovery implementation, all eight original cases
pass. The ninth missing-owner case is the only current failure:

```text
uv run --no-sync pytest tests/unit/test_mp07_agent_journal_backup.py -q --tb=short
1 failed, 8 passed, 1 warning in 0.63s
```

The focused missing-owner run is retained at
`/private/tmp/agent-economy-mp07-agent-journal-missing-owner-red.log`; the complete current run is
`/private/tmp/agent-economy-mp07-agent-journal-backup-red-owner-final.log`. Its byte and mode
assertions execute before the intended `identity_complete` failure, proving this is verification
classification after exact restore rather than capture loss or fixture setup failure.

The owner and intent files in these tests are private, deterministic stored-prerequisite bytes. They
prove recovery preservation and binding inspection only; they are not a second journal and do not
prove journal parsing, replay or acknowledgment behavior.

## Static and source evidence

Both new test files pass Ruff formatting/linting and codespell. The Gateway unit test passes standalone
strict Pyright with zero errors or warnings. The root runtime test is outside the Gateway Pyright
project's import roots; its repository `scripts` imports are exercised by pytest with the same explicit
root path used by the runtime gate rather than hidden with import suppressions.

The pre-implementation hashes of recovery fields, inventory, targets, neutral contract, public lock,
Gateway recovery engine and runtime-owner model are recorded at
`/private/tmp/agent-economy-mp07-agent-journal-backup-source-baseline.sha256`. No full CI or broad root
gate was run for this intentionally red slice. T-181/T-182 remain open pending implementation, green
proof, independent review and the repository closure gate.
