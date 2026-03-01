# Phase 10 — Final Verification

## Working Directory

Commands run from `services/central-bank/` and `services/identity/`.

---

## Task B14: Run full CI and fix issues

### Step 10.1: Add joserfc + cryptography to central-bank dev dependencies

The test fixtures use `joserfc` and `cryptography` to create test JWS tokens. Add to `pyproject.toml` dev dependencies:
- `"joserfc>=1.0.0"` (for creating test tokens)
- `"cryptography>=44.0.0"` (for Ed25519 key generation in tests)

### Step 10.2: Run init

```bash
cd services/central-bank && just init
```

### Step 10.3: Verify service starts

```bash
cd services/central-bank && just run
```

Ctrl+C after it starts. Expected: Service starts on port 8002.

### Step 10.4: Run full CI for Central Bank

```bash
cd services/central-bank && just ci
```

Expected: All checks pass. Fix any issues that arise (ruff formatting, mypy errors, etc.).

### Step 10.5: Run Identity service CI too

```bash
cd services/identity && just ci
```

Expected: All checks pass including new JWS tests.

### Step 10.6: Commit any fixes

```bash
git add -A
git commit -m "fix(central-bank): resolve CI issues"
```

---

## Final Verification Checklist

- [ ] `cd services/identity && just ci-quiet` — PASS
- [ ] `cd services/central-bank && just ci-quiet` — PASS
- [ ] Identity service starts (`just run`) and responds to `GET /health`
- [ ] Central Bank service starts (`just run`) and responds to `GET /health`
- [ ] `POST /agents/verify-jws` works on Identity service
- [ ] All unit tests pass for both services

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| 1 | A1–A4 | Extend Identity service with `POST /agents/verify-jws` |
| 2 | B1–B2 | Central Bank config.yaml, pyproject.toml, config.py |
| 3 | B3 | Foundation modules (__init__, logging, schemas) |
| 4 | B4 | Core infrastructure (state, exceptions, middleware) |
| 5 | B5 | IdentityClient HTTP client |
| 6 | B6 | Ledger business logic (accounts, transactions, escrow) |
| 7 | B7 | Routers (health, accounts, escrow) |
| 8 | B8 | Application factory and lifespan |
| 9 | B9–B13 | All tests (config, health, accounts, escrow, placeholders) |
| 10 | B14 | Final CI verification and fixes |

**Total tasks:** 18

**Key files created/modified:**

Identity service:
- `services/identity/pyproject.toml` (add joserfc)
- `services/identity/src/identity_service/services/agent_registry.py` (add `verify_jws`)
- `services/identity/src/identity_service/routers/agents.py` (add endpoint)
- `services/identity/src/identity_service/core/middleware.py` (whitelist endpoint)
- `services/identity/tests/unit/routers/test_verify_jws.py` (new tests)

Central Bank service (all new):
- `services/central-bank/config.yaml`
- `services/central-bank/src/central_bank_service/` (all modules)
- `services/central-bank/tests/` (all test files)
