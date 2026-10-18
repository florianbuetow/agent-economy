# MP-07 runtime-state configuration test interface

## Scope

This T-181 test-only slice defines the configuration boundary required by the durable caller-owned
intent journal. It adds no product module, YAML value, default, compatibility path or runtime file.
The implementation module is `base_agent.runtime_config` and exports the public names
`RuntimeStateConfig` and `load_runtime_state_config` used by this slice.

## Public contract

`RuntimeStateConfig` is a frozen, strict value with four required fields and no defaults:

- `environment_id: str` is nonblank and retained byte-for-byte as the ownership environment; the
  loader does not trim, case-fold or otherwise normalize it.
- `directory: Path` is absolute and canonical. Direct construction rejects a relative path because
  it has no configuration-file anchor. An absent final directory is allowed and is not created by
  configuration loading. An existing final path must be a directory.
- `diagnostic_limit: int` is a strict positive integer; booleans, numeric strings and floats are
  rejected.
- `max_record_bytes: int` has the same strict positive-integer contract.

Unknown fields are rejected. The exact positive lower boundary is one. Accepting
`max_record_bytes=1` proves configuration validation only; a later owner or intent publication whose
canonical bytes exceed that bound must fail record-size admission and is not a successful operational
configuration. The model rejects a symlink at the configured path or in an existing ancestor before
returning a canonical path.

`load_runtime_state_config(config_path: Path) -> RuntimeStateConfig` has one required argument and no
implicit repository or environment fallback. A non-`Path` argument is rejected. The YAML document
must be a mapping with a required top-level `runtime_state` mapping containing every field above.
Other top-level agent sections remain allowed, while extra fields inside `runtime_state` are not.
YAML booleans and coercible strings do not satisfy integer or string fields.

Relative `runtime_state.directory` values are anchored at the explicit configuration file's parent,
independent of the process working directory. The loader inspects lexical components for symlinks
before resolving `.` or `..`; therefore a value such as `linked-parent/../apparently-safe` is unsafe
when `linked-parent` is a symlink even if normalization would remove that component. The resulting
accepted value is absolute and lexically canonical for the public maintenance-lock boundary.

## Test matrix

`agents/tests/unit/test_runtime_config.py` contains 64 collected cases covering:

- required interface and required loader argument;
- all four missing fields, unknown fields, frozen model behavior and exact environment retention;
- blank strings, wrong types, booleans, zero/negative bounds and the exact positive boundary;
- absent/nonmapping YAML documents and sections plus strict YAML coercion rejection;
- config-relative and absolute canonical resolution without directory creation;
- regular-file rejection, direct/ancestor symlink rejection and symlink-before-`..` resolution.

The absent module is represented once as the capability failure. The remaining 63 cases fail in the
required-interface fixture and are classified as unexecuted downstream assertions. They are not 63
independently observed product defects, and none is skipped, xfailed or suppressed.

## Fresh RED and static evidence

From `agents/` with `UV_CACHE_DIR=/tmp/agent-economy-review-uv`, `UV_FROZEN=1` and `--no-sync`:

```text
uv run --no-sync pytest tests/unit/test_runtime_config.py -q --tb=short
collected 64 items
1 failed, 63 errors in 0.32s
```

The single failure is `test_runtime_config_interface_is_available`: the module
`base_agent.runtime_config` does not exist. Each setup error explicitly says that the downstream
case is blocked by that absent interface. Full output is retained at
`/private/tmp/agent-economy-mp07-runtime-config-red-final.log`.

The same frozen test source passed:

```text
uv run --no-sync ruff format --check tests/unit/test_runtime_config.py
1 file already formatted
uv run --no-sync ruff check tests/unit/test_runtime_config.py
All checks passed!
uv run --no-sync codespell tests/unit/test_runtime_config.py
exit 0
uv run --no-sync pyright tests/unit/test_runtime_config.py
zero errors and zero warnings
```

No agent source module, ordinary runtime state, agent configuration or existing test was modified by
this slice. T-181 and T-182 remain open pending implementation, full green proof, independent review
and the repository closure gate.

## Parent implementation checkpoint

The first implementation run executed60 assertions successfully and exposed four fixture errors:
the helper parameter `directory` collided with the same key in override kwargs before validation.
The original red source is retained at `agent-economy-mp07-runtime-config-red-source.py` in the
evidence bundle. Renaming only that helper parameter to `state_directory` preserves every assertion.
The corrected suite passes64; combined feeder-window/config checks pass93. Scoped Mypy, Pyright and
Ruff pass. Independent review approves the loader, strict model, path rules and fixture correction.
See `openspec/changes/complete-agent-economy/evidence/mp07-transitions/runtime-config/`.
Runtime startup wiring and the durable journal remain pending; no root gate or ticket closure is claimed.
