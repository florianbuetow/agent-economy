# Recovery lane command record

Revision at campaign start:
`35cf0e3a5fcfd3c6a92d6c2e305bdfd711c7aa16`. The working tree also
contained the parent-owned dispute/UI changes; final integration evidence must
record the final tree rather than treating this revision alone as sufficient.

All root-runtime commands used:

```text
cwd: repository root
environment: UV_CACHE_DIR=/tmp/ate-campaign-uv-cache
             PYTHONPATH=<repo>:<repo>/tests
runner: uv run --directory services/db-gateway --no-sync pytest ...
```

Test selections and retained output:

```text
tests/runtime/test_mp05_demo_native_restart.py
tests/runtime/test_mp05_demo_other_action_restart.py
tests/runtime/test_mp08_financial_effect_restart.py
  -> recovery-cuts.log (exit 0, 33 passed)

tests/runtime/test_supervise.py
tests/runtime/test_service_stop_safety.py
tests/runtime/test_agent_stop_safety.py
  -> supervision.log (exit 0, 38 passed)

tests/runtime/test_backup.py
tests/runtime/test_current_backup_resume.py
tests/runtime/test_recovery_live_inventory.py
tests/runtime/test_recovery_live_restore.py
tests/runtime/test_isolated_test_runtime.py
tests/runtime/test_mp05_source_capture_anchor.py
tests/runtime/test_mp09_private_asset_root.py
  -> operations.log (exit 0, 28 passed)

tests/runtime/test_recovery_preparation.py
tests/runtime/test_recovery_inventory.py
tests/runtime/test_recovery_adoption.py
  -> recovery-inventory.log (exit 0, 44 passed)

tests/runtime/test_adversarial_gateway_outage.py
  -> gateway-live-outage.log

tests/runtime/test_adversarial_full_stack_isolation.py
  -> full-stack-isolation.log (exit 0, 1 passed)

tests/runtime/test_adversarial_real_telemetry.py
  -> real-telemetry.log (exit 0, 5 passed)
tests/runtime/test_adversarial_real_telemetry.py::
  test_real_dispute_traces_correlate_authoritative_task_and_dispute_ids
  -> real-dispute-trace.log (exit 0, 1 passed before stricter correlation rerun)

tests/runtime/test_adversarial_multistate_restore.py
  -> multistate-restore.log (exit 0, 1 passed)
```

Task Board commands used the service directory as cwd and its existing locked
environment:

```text
uv run --no-sync pytest \
  tests/unit/test_token_validator.py \
  tests/unit/routers/test_two_tier_ruling.py -q --tb=short
  -> identity-outage.log (exit 0, 17 passed)

uv run --no-sync pytest \
  tests/integration/test_mp07_native_effect_recovery.py \
  tests/integration/test_mp08_current_pending_gap.py \
  tests/integration/test_mp07_native_deadlines.py -q --tb=short
  -> bank-recovery.log (exit 0, 11 passed)

uv run --no-sync pytest \
  tests/integration/test_mp07_public_command_replay.py \
  tests/unit/test_mp07_gateway_store_parity.py \
  tests/unit/test_mp04_financial_intents.py -q --tb=short
  -> operation-identity.log (exit 0, 27 passed)

uv run --no-sync pytest \
  tests/unit/test_loop_supervision.py \
  tests/unit/test_gateway_read_contracts.py \
  tests/unit/test_mp07_task_operation_client_regressions.py -q --tb=short
  -> gateway-contracts.log (exit 0, 44 passed)
```

Other service/root selections:

```text
central-bank: two-tier Identity tests -> identity-bank.log (6 passed)
reputation: two-tier/feedback Identity tests -> identity-reputation.log (46 passed)
root startup-order regression -> identity-startup.log (1 passed)
root Gateway namespace outage clients -> gateway-offline.log (5 passed)
```

Agent E2E commands ran behind `scripts/isolated_test_runtime.py --provision`:

```text
test_unattended_economy.py -> unattended.log (exit 0, 1 passed)
test_adversarial_unattended_expiry.py -> unattended-expiry.log (exit 0, 1 passed)
test_adversarial_valid_workloads.py -> valid-workloads.log (exit 0, 1 passed)
```

The first cache-lock, DNS, and socket-bind attempts were setup failures, not
scenario results. Successful commands used existing environments (`--no-sync`)
and only disposable loopback/process permission. No normal runtime was touched.
