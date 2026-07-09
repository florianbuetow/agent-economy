# AgentConfig Refactor + Identity Scenario Tests

## Goal

Refactor BaseAgent to accept a single `AgentConfig` data object in its constructor instead of reading from config files, roster, and key directories. Then add scenario tests that exercise the IdentityMixin against a live identity service.

## Architecture

**AgentConfig** — a Pydantic model carrying everything the agent needs:
- `name`, `private_key`, `public_key` (agent identity)
- `identity_url`, `bank_url`, `task_board_url`, `reputation_url`, `court_url` (platform URLs)

**BaseAgent constructor** takes only `AgentConfig`. No file I/O, no roster, no key directories, no handle.

**Factory function** `load_agent_config(handle, settings_path)` builds `AgentConfig` from YAML + roster + key files. Lives outside BaseAgent.

**No backwards compatibility.** The old `BaseAgent(handle, config)` signature is removed entirely.

## Changes

### config.py
- Remove `PlatformConfig`, `DataConfig`, `Settings`
- Add `AgentConfig` with name, keypair, and URL fields
- Add `FileSettings` (or similar) for YAML loading only, used by the factory
- Add `load_agent_config(handle, settings_path)` factory function

### agent.py
- Constructor takes `AgentConfig` only
- Assigns name, keypair, URLs directly from config
- No roster loading, no key directory resolution, no handle

### mixins/identity.py
- URL access changes from `self.config.platform.identity_url` to `self.config.identity_url`
- Protocol `_IdentityClient` updated to match new config shape

### All unit tests
- Updated to build `AgentConfig` directly with in-memory keypairs
- No more temp roster files or key directories in fixtures
- conftest.py simplified

### Scenario tests
- `agents/tests/scenarios/conftest.py` — health check fixture with 3s timeout
- `agents/tests/scenarios/test_identity.py` — full identity workflow against live service
- `agents/justfile` — add `test-scenarios` target
- Root `justfile` — add `test-scenarios` target

## Scenario: Identity Service

1. Generate fresh Ed25519 keypair in memory
2. Build AgentConfig, create BaseAgent
3. `register()` — assert agent_id assigned (201)
4. `register()` again — assert same agent_id (409 idempotent)
5. `get_agent_info()` — assert record matches
6. `list_agents()` — assert agent in list
7. `verify_jws()` — assert valid: true
