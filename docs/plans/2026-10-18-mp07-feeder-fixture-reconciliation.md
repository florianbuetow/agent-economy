# MP-07 feeder fixture reconciliation

## Scope

Reconcile the pre-MP-07 feeder unit fixtures with the reviewed native command
contract. Product code, end-to-end tests, shared libraries, and unrelated unit
tests remain outside this change.

Owned test files:

- `agents/tests/unit/task_feeder/test_acceptance.py`
- `agents/tests/unit/task_feeder/test_review.py`
- `agents/tests/unit/task_feeder/test_review_asset_fallback.py`
- `agents/tests/unit/task_feeder/test_loop_statuses.py`
- `agents/tests/unit/task_feeder/_runtime_fixtures.py` (new feeder-only helper)

## Original evidence

The original test sources were captured before edits:

| File | SHA-256 |
| --- | --- |
| `test_acceptance.py` | `5a0c73d46fd3a69de1147d76040d0881329b68b9f91e81831485104d3af20f10` |
| `test_review.py` | `c16cf0db9c195967ed2bfc6c4c524d951d229d0394616c04d450e0c84ea3e2f3` |
| `test_review_asset_fallback.py` | `8b36fa76b40b1c52c5403887737453e28b5d8781ab7c7812d2d150b725eacfe2` |
| `test_loop_statuses.py` | `82e4518de84f1e36fd4c74cca18c14cb9fd6dcc3b18fc6efc0ebebc6a1ffbfde` |

Reviewed contract sources used for the reconciliation:

| File | SHA-256 |
| --- | --- |
| `agents/src/task_feeder/acceptance.py` | `c256b033d71c5cd13e88074c19e1013de0a15745e691a5488e05abe785412955` |
| `agents/src/task_feeder/review.py` | `84d25f1c42c1704c2985d66e8bd3f4f69706648437603221a2ae6b2c96083201` |
| `agents/src/base_agent/runtime_task_commands.py` | `56f1538fdf7577f662f2b62f7b185e22351d5cb8e68000995024863b719165ac` |
| `agents/src/base_agent/runtime_task_intent_factory.py` | `4875b2ef68c3305ee0c5e9cd9dc7a2f54158ef17400d9f4f0acf4820e51f806f` |
| `agents/src/base_agent/runtime_intent_ack.py` | `891b0a870a813c106d406e92b602efc254148dc369bdbe742980df0c747bc6e9` |
| `agents/tests/unit/task_feeder/test_mp07_native_intents.py` | `8ea2ea0a16cd7ff753f2afd39f17592f20e5a61165c20a71183961663adda73f` |
| `agents/tests/unit/task_feeder/test_mp07_native_intent_regressions.py` | `95d2b7663080ba3b8943e99c9e8a67bc648fcc8dfdd87356e3a9ab146419d57d` |

Targeted pre-edit baseline command:

```text
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 uv run --no-sync pytest tests/unit/task_feeder/test_acceptance.py tests/unit/task_feeder/test_review.py tests/unit/task_feeder/test_review_asset_fallback.py tests/unit/task_feeder/test_loop_statuses.py -q
```

Baseline result: `43 collected; 17 passed; 26 failed`. Every failure was a
missing mandatory `commands` argument on `AcceptanceLoop` or `ReviewLoop`.

## Reconciliation plan and result

1. Keep pure answer, winner, reputation, quorum, deadline, and tie-break tests
   isolated from persistence.
2. Route every mutation and replay test through a real `FileTaskIntentStore`
   and `RuntimeTaskCommands` instance bound to a deterministic Ed25519 public
   key, registered poster ID, and five explicit service URLs.
3. Give native task snapshots canonical task IDs and exact lifecycle version,
   poster, worker, escrow, reward, and accepted-bid facts.
4. Complete successful commands only with exact typed Task Board
   acknowledgments, including bank-release and court-claim effect facts.
5. Preserve the original winner, quorum, timing, answer-quality, tie-break,
   oracle, asset-selection, empty-asset, and status-filter assertions.
6. Replace the obsolete generic-409-finalizes expectation with two controls:
   an untyped generic 409 remains pending and a typed `invalid_status`
   acknowledgment is a definitive rejection that is not sent again.
7. Replace the obsolete native-dependency exception expectation with durable
   observations: the intent remains pending, the task is not finalized, and a
   later call retries the exact saved command.
8. Keep successful empty asset lists as a dispute decision. Asset-list and
   asset-download exceptions remain pre-intent failures, as covered by the
   reviewed MP-07 regression module.

No second fake journal was introduced. The helper supplies only the public SDK
boundary and typed acknowledgments; persistence and state transitions use the
production file intent store.

## Verification

Targeted reconciliation suite:

```text
43 passed in 0.35s
```

Targeted suite plus adjacent reviewed MP-07 feeder controls:

```text
76 passed in 0.51s
```

Full agent unit suite:

```text
732 passed in 6.81s
```

Static checks:

```text
just code-style: 134 files formatted; all checks passed
just code-typecheck: no issues in 40 source files
just code-security: no issues identified
just code-spell: passed
```
