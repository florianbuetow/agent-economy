# T-169/T-170 deadline precision baseline

Root CI attempt 4 rejected an immediate bid in the existing one-second expiry fixture. The configured duration was shortened by formatting the computed deadline to whole seconds, although its start timestamp contained microseconds. The same truncation existed in Gateway create payloads and the corresponding event.

The repair changes only those two deadline arithmetic formatters to `isoformat(timespec="auto")`. Nonzero fractions are preserved; existing whole-second formatting and `>=` cutoff semantics remain intact. Existing expiry, refund and event assertions were not changed. New tests live in `services/task-board/tests/unit/test_mp07_deadline_precision.py`.

- Evaluator red: `mp01-deadline-evaluator-red.log`, 2 failed/2 passed; precision and just-before behavior failed, while exact-cutoff and whole-second cases already passed.
- Persistence red: `mp01-deadline-persistence-red.log`, 1 failed/4 passed; captured outgoing `/board/tasks` payload discarded `.250000`.
- New suite green: `mp01-deadline-green.log`, 5 passed.
- Existing client/deadline/expiry coverage: `mp01-deadline-targeted-green.log`, 33 passed, including the original exactly-once refund case.
- Leader independently ran `UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 just ci-quiet` in `services/task-board`; `mp01-deadline-component-ci-green.log`, exit 0, all component gates passed.
- Independent reviewer `/root/money_persistence`: APPROVE, zero findings.

Tasks 2.5/2.6 are verified. T-169/T-170 closure still requires the shared fresh root gate. This baseline correction does not satisfy the schema/CAS/concurrency requirements or close T-122/T-123 or MP-07.
