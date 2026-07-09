# Platform Agent Design

## Problem

Services need to perform privileged banking operations (create accounts, credit funds, release escrow) that regular agents cannot. Currently this is handled by a hardcoded `platform.agent_id` in config and an ad-hoc `PlatformSigner` in the Task Board. There is no unified pattern.

## Design

### AgentFactory

A factory class in `agents/src/base_agent/` that creates agents with their keys loaded. The factory knows the key storage convention internally — callers never deal with key paths.

```python
from base_agent import AgentFactory

factory = AgentFactory(identity_url="http://localhost:8001", bank_url="http://localhost:8002")
platform = factory.platform_agent()
await platform.register()
```

The factory resolves keys from the standard `data/keys/` directory relative to the project root. The `platform_agent()` method returns a `BaseAgent` (or subclass) initialized with the platform keypair.

### Platform Agent

The platform agent is defined in `agents/roster.yaml` alongside regular agents:

```yaml
agents:
  platform:
    name: "Platform"
    type: "platform"
  alice:
    name: "Alice"
    type: "worker"
```

Its keypair is generated during `just init` in `agents/`, stored in `data/keys/platform.key` and `data/keys/platform.pub` — same as any other agent.

### Privileged Operations

The platform agent signs JWS tokens for operations that only it is authorized to perform:

- `create_account(agent_id, initial_balance)`
- `credit_account(account_id, amount)`
- `release_escrow(escrow_id, recipient_id)`
- `split_escrow(escrow_id, splits)`

These are methods on the platform agent class that sign and send requests to the Central Bank.

### Verification

When a service receives a request claiming to be from the platform agent, it validates the JWS using its own `PlatformAgent` instance's public key. Local cryptographic verification — no Identity service round-trip needed, no agent_id comparison.

```python
# Service startup
platform = factory.platform_agent()
await platform.register()
app_state.platform_agent = platform

# Request validation
platform.verify_platform_jws(incoming_token)
```

### Service Integration

Services that need platform operations:

1. Add `base_agent` as a dependency
2. Instantiate `AgentFactory` at startup, call `platform_agent()`, register, store in `AppState`
3. Use the instance to sign outgoing platform requests and verify incoming ones

### What This Replaces

- `platform.agent_id` in service config files (derived from the agent instance)
- `PlatformSigner` in Task Board (replaced by the platform agent)
- `require_platform()` agent_id comparison (replaced by JWS verification against platform public key)
