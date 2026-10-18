# MP-07 explicit internal helper arguments

Parent Gateway CI84625 exited1 before unit execution with four genuine Semgrep no-default-parameter
findings. Preserve the raw CI log and original sources before repair. No rule or allowlist changes.

Require explicit error details in _service_error and _invalid. Existing callers already supply
most details; the no-context parser case supplies an explicit empty object. Require explicit tables
selection in _database_snapshot; callers requesting the full snapshot pass None, and the preservation
comparison retains its exact stored table tuple. Remove the unused accepted flag from
_require_task_arguments and require an explicit integer expected_reward at both callers: published
reward for acceptance and the already-derived frozen reward for settlement. Eliminate the unused
fallback branch; all actual runtime values remain the same.

These are private helper boundaries, not public protocol changes. Verify native operation/error/
receipt, migration and parser tests plus source type/style/Semgrep, then rerun the full Gateway gate.
