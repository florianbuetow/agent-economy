# Economics lane execution manifest

Parent handoff verification additionally captured the unchanged process helper
and durable-worker unit seam on 2026-11-05:

```text
6f7fe6ce5c10384ff715522a328fd636347eff93204ce593fc13924d3fb0af88  agents/tests/e2e/shared_key_bid_actor.py
a222d2ccdd14917dd537d64cf07f305d53e669c76c1f131e4483a89580cdaabc  agents/tests/unit/math_worker/test_adversarial_multi_job_restart.py
```

All commands used the normal repository toolchain. `UV_CACHE_DIR` named only
disposable writable cache locations; no executable or `PATH` substitution was
used. Commands requiring loopback listeners were run with the repository's
approved local-test permission. Source revision before campaign additions was
`35cf0e3a5fcfd3c6a92d6c2e305bdfd711c7aa16`.

## Evidence-bearing commands

| Log | CWD | Exact pytest selection | Exit |
| --- | --- | --- | ---: |
| `taskboard-unit.log` | `services/task-board` | `uv run pytest tests/unit/routers/test_bids.py tests/unit/routers/test_adversarial_bid_acceptance.py tests/unit/routers/test_g01_accepted_bid_pricing.py tests/unit/routers/test_u_poster_inputs.py tests/unit/routers/test_submission.py tests/unit/routers/test_t072_submission_gaps.py tests/unit/routers/test_t072_assets_gaps.py tests/unit/test_deadline_evaluator.py tests/unit/test_deadline_sweep.py tests/unit/test_gateway_fixture_serialization.py tests/unit/test_mp09_governed_artifacts.py tests/unit/test_mp09_asset_access_matrix.py tests/unit/test_mp09_completed_asset_access.py -q` | 0 |
| `identity-unit.log` | `services/identity` | `uv run pytest tests/unit/routers/test_t072_registration_gaps.py tests/unit/routers/test_agents.py -q` | 0 |
| `taskboard-races.log` | `services/task-board` | `uv run pytest tests/integration/test_mp07_public_command_replay.py tests/integration/test_mp07_bid_admission_races.py tests/integration/test_mp07_accept_expiry_race.py tests/integration/test_mp07_atomic_task_transitions.py tests/integration/test_mp07_native_public_lifecycle.py tests/integration/test_mp07_deadline_transition_races.py tests/integration/test_mp09_asset_atomicity.py::test_upload_that_read_accepted_cannot_publish_after_submission_wins -q` | 0 |
| `central-bank-admission.log` | `services/central-bank` | `uv run pytest tests/integration/test_adversarial_escrow_admission.py -q` | 0 |
| `taskboard-publication-funds.log` | `services/task-board` | `uv run pytest tests/integration/test_adversarial_publication_funds.py -q` | 0 |
| `asset-projection.log` | `services/task-board` | `uv run pytest tests/unit/test_atomic_asset_admission.py tests/unit/test_mp09_asset_storage_safety.py tests/unit/test_mp08_financial_projection_integrity.py tests/unit/test_mp07_public_task_projection.py tests/integration/test_mp08_public_financial_projection.py tests/integration/test_mp08_terminal_effect_recovery.py -q` | 0 |
| `ui-browser.log` | `services/ui` | `uv run pytest tests/e2e/test_adversarial_approval_recovery.py tests/e2e/test_mp05_live_posting_conservation.py -q --basetemp=/tmp/ate-campaign-ui-retained4` | 0 |
| `ui-pending-settlement.log` | `services/ui` | `uv run pytest tests/unit/test_adversarial_pending_settlement.py -q` | 0 |
| `court-no-feedback.log` | `services/court` | `LITELLM_LOCAL_MODEL_COST_MAP=True uv run pytest tests/unit/test_mp07_ruling_producer.py::test_invalid_task_board_ack_stops_before_feedback_and_local_persist -q` | 0 |
| `agent-economic-cycles.log` | repository root | `uv run --directory services/db-gateway scripts/isolated_test_runtime.py --artifacts-root /tmp/ate-campaign-economic-final --provision -- uv run --project agents pytest agents/tests/e2e/test_external_agent_input_economic_cycle.py agents/tests/e2e/test_unattended_economy.py agents/tests/e2e/test_mp05_posting_response_loss.py agents/tests/e2e/test_mp05_posting_server_restart.py -q` (absolute repository paths supplied to the actual process) | 0 |
| `agent-adversarial-final.log` | repository root | isolated provisioned runtime running `test_adversarial_shared_key_processes.py`, `test_adversarial_cross_task_content.py`, `test_adversarial_three_task_divergence.py`, and `test_adversarial_multi_job_process_restart.py` | 0 |
| `identity-process-final.log` | repository root | isolated provisioned runtime running the same-display-name full-stack test and both owned-process cleanup regressions | 0 |
| `multi-job-restart.log` | `agents` | `uv run pytest tests/unit/math_worker/test_adversarial_multi_job_restart.py -q` | 0 |
| `publication-pending-final.log` | `services/task-board` | protocol-2 freeze, opaque upload replay, public financial projection, and terminal recovery selections | 0 |
| `market-burst.log` | repository root | parent-owned isolated five-worker/five-task/two-poster sequence; exact command recorded in the shared market result | 0 |
| `upload-changed-bytes-contract-failure.log` | `services/task-board` | `uv run pytest tests/unit/routers/test_adversarial_upload_replay.py -q` with the proposed content-bound oracle | 1 (expected diagnostic) |
| `upload-current-contract.log` | `services/task-board` | same test after converting it to characterize the documented opaque-key contract | 0 |

The first attempts to retain component logs failed before collection because UV
could not create its home-cache lock in the sandbox. They were overwritten by
the evidence-bearing runs above using `/tmp/ate-campaign-unit-cache`. Integration
logs were then rerun with local socket permission. Earlier path-inventory and
relative-script setup failures are documented in `results.md` and are not cited
as scenario outcomes.

## Integrity hashes

```text
9baea173b121b2fb706c9d05b0c085a2c61093759d45ff8996df7b1720442b34  agent-economic-cycles.log
87535841f443aa83da03f3765c5b13b33c67bc3bfb30fdb9c6caf9a8454b114f  agent-adversarial-final.log
46fa4cc13c68478f3179fc05cec67ff2668a79e0db783543aad2b3d2fb70bd1d  asset-projection.log
bcd072468b57601e6d1f1a1a41bf228fc89bb4d42efaf4b02d0d5000172fb2d4  central-bank-admission.log
17f83b3db8c990da6c04905d18ad25df5e87260ae8a77ec3499001234432d741  identity-unit.log
dff7149d97bf0fee2b8ff83f74265998400be9ec56408dba48000724cfefe930  identity-process-final.log
550b6f295541feb7d31d9cd335aa89c57a95f671dd04235a6700c37c8c82a910  taskboard-publication-funds.log
8373b1eb90b9029f85bc0a39cf3f32c1b287ff327a6fc9f653012f4c27149f43  taskboard-races.log
3fe967924a1c743f30d0edad754b41f7517d777367b504a6866611bbeda50ef4  taskboard-unit.log
2ce854dcc9cfa55fdea83c7b11fc4c61b3cad85f5f370d090b83081714d5c74b  ui-browser.log
6aa4a5f1dd834ad9a3d3e253d997c2759bbd74265d5bd4eb97c9de958ba40e6c  ui-pending-settlement.log
92982a9abcebd22ea805c5a725ff62ebc346c2146960e9b2a22719038a27bf33  court-no-feedback.log
9b95fd89d37f6b66776cdb2f01831bfd208314946148624f5bf416207537915f  multi-job-restart.log
6da6041ec8e1a7c9783c95f1e00ff4a8079214f4920697f14047144257f12354  publication-pending-final.log
7a3621672118fdc71b646fd933a822f70b5999a7fdffcd6d1c68ca076ceb6a83  market-burst.log
0e024b99466555518f2094f9231822354ecdfe5542fc1e96bd4ebaee90a89f87  upload-changed-bytes-contract-failure.log
76b6dd1634a14a26fcd96380714b657a7bc8ab24bf64e5129389d17945632025  upload-current-contract.log
```

Campaign test-source hashes at the final retained run:

```text
b7013c1ee52d07687f9d66e680abea80a1729c9044fce1fffde27b7532b37869  services/task-board/tests/unit/routers/test_adversarial_bid_acceptance.py
21f032953cf5ac8e5b6e0f42f8595bdd6f1ca4cbb25d88726eb3c8b51eb83eb2  services/task-board/tests/integration/test_adversarial_publication_funds.py
2e95650edf4bb6b6c6ae780d48ef5964564fe50761c25d96ff840e5168b9248e  services/task-board/tests/unit/routers/test_adversarial_upload_replay.py
8ed07eb1275b8ff6fcc3bd9c20111e3badf059d8acc61900c5863980c2801498  services/task-board/tests/unit/routers/test_adversarial_protocol2_publication.py
04f2be9adb0a54fa4521b8c8677d9a4547c6920e9032d25b8da2b10a608ad670  services/central-bank/tests/integration/test_adversarial_escrow_admission.py
3b4b2700f254618a197cdd8187fda977aadf9e4cac4f9fa167e17d151f7d86dd  services/ui/tests/e2e/test_adversarial_approval_recovery.py
490b80d903987212f4ec10e7b2e73214bedb3bc2ab6b63cb66c6d0349d37a238  services/ui/tests/unit/test_adversarial_pending_settlement.py
876a008ddb0da75b6623920871b3fec31fa52b8bf73f1ce58c676e52712d6746  agents/tests/e2e/test_adversarial_shared_key_processes.py
0c7ca108a2e274b2143dbf9437af2a03e9513086660d4ac0321bd44f5daf5d5b  agents/tests/e2e/test_adversarial_cross_task_content.py
dc42cd8ac3a8e5b8cca4ceea1fe21a6b228a4d6fbd8f9deb98af5691b65ec409  agents/tests/e2e/test_adversarial_three_task_divergence.py
dbae98d201d78094fb65d2f1452356471b0ad52e6a26b9568df5e444dd4b8fc4  agents/tests/e2e/multi_job_crash_actor.py
4b7d95d2e3f4cc293df95deb14873a47b3d10b4beadfc22177c9ce81c1e0a46c  agents/tests/e2e/test_adversarial_multi_job_process_restart.py
e5de5a746f17ac8287e4771f3a4653aee24bfb30e3e058e5586dc7315c5e6ff5  agents/tests/e2e/test_adversarial_duplicate_names.py
20561dd69f58a62f71de0b9e3a4a9ca43a15ee1bc329e80d3de2c88496ef4fb1  agents/tests/unit/test_adversarial_process_cleanup.py
```
