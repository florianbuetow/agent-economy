Read these files FIRST before doing anything:
1. AGENTS.md — project conventions, architecture, testing rules
2. agents/pyproject.toml — dependencies, test config, linting rules
3. agents/src/task_feeder/reader.py — RawTask dataclass and load_tasks()
4. agents/src/task_feeder/config.py — TaskFeederConfig (Pydantic model)
5. agents/src/task_feeder/loop.py — TaskFeederLoop (existing feed loop)
6. agents/src/task_feeder/__main__.py — Entry point, agent setup, signal handling
7. agents/src/base_agent/mixins/task_board.py — approve_task(), dispute_task(), list_tasks(), get_task()
8. agents/src/base_agent/agent.py — BaseAgent class
9. agents/tests/unit/task_feeder/test_loop.py — REFERENCE: existing unit test patterns (_make_config helper, TaskFeederLoop.__new__ pattern)
10. agents/tests/unit/task_feeder/test_reader.py — REFERENCE: existing test patterns for reader
11. agents/config.yaml — Full config including task_feeder section

After reading ALL files, implement the following. Execute Phase 1 through Phase 3 in order. Do NOT skip phases.

The NEW test file goes in: agents/tests/unit/task_feeder/test_review.py
Do NOT modify any existing test files. Only create new files.
Use `uv run` for all Python execution — never use raw python, python3, or pip install.
Do NOT implement the review_loop module itself. Only create tests.

## Background

The feeder auto-approval review loop (`agents/src/task_feeder/review.py` — does NOT exist yet)
will be a module that:

1. Polls the TaskBoard for tasks in REVIEW status that were posted by the feeder agent
2. For each such task, fetches the submitted deliverable (worker's answer)
3. Looks up the original RawTask from an in-memory mapping (task_id -> RawTask)
4. Compares the worker's submitted answer against `RawTask.solutions`
5. If any solution matches (case-insensitive, whitespace-trimmed): calls `approve_task(task_id)`
6. If no solution matches: calls `dispute_task(task_id, reason)` with a reason like
   "Expected one of [<solutions>], got '<submitted_answer>'"
7. Runs as a separate asyncio.Task in __main__.py, sharing the same BaseAgent instance

Since the review module does NOT exist yet, we write **unit tests** that define the expected
behaviour. The tests must be syntactically valid and CI-compliant (ruff, mypy markers) but are
expected to FAIL because the module doesn't exist.

## Important Design Details

### RawTask (from reader.py)
```python
@dataclass(frozen=True)
class RawTask:
    title: str
    spec: str
    solutions: list[str]   # e.g., ["x=15", "15"]
    level: int
    problem_type: str
    solution_note: str | None
```

### Answer Comparison Rules
- Case-insensitive: "X=15" matches "x=15"
- Whitespace-trimmed: "  15  " matches "15"
- Any solution in the list is valid: if solutions=["x=15", "15"], both "x=15" and "15" are correct

### TaskBoardMixin methods (from base_agent/mixins/task_board.py)
- `approve_task(task_id: str) -> dict` — POST /tasks/{task_id}/approve
- `dispute_task(task_id: str, reason: str) -> dict` — POST /tasks/{task_id}/dispute
- `list_tasks(status=None, poster_id=None, worker_id=None) -> list[dict]`
- `get_task(task_id: str) -> dict`

### Expected Module Interface (what we test against)
The test file will import from `task_feeder.review` which does not exist yet.
The expected interface:
```python
# task_feeder/review.py (TO BE IMPLEMENTED LATER)

def check_answer(submitted: str, solutions: list[str]) -> bool:
    """Return True if submitted answer matches any solution (case-insensitive, trimmed)."""
    ...

class ReviewLoop:
    def __init__(self, agent: BaseAgent, task_map: dict[str, RawTask]) -> None:
        """
        agent: BaseAgent with task_board mixin methods
        task_map: mapping of task_id -> RawTask for solution lookup
        """
        ...

    async def review_one(self, task_id: str) -> str:
        """Review a single task. Returns 'approved' or 'disputed'."""
        ...

    async def run(self) -> None:
        """Main loop: poll for REVIEW tasks, review each one."""
        ...

    def stop(self) -> None:
        """Signal the loop to stop."""
        ...
```

=== PHASE 1: Test File ===

Create file: agents/tests/unit/task_feeder/test_review.py

This file contains unit tests for the review loop module. Use `@pytest.mark.unit` marker on
every test class. The module under test does NOT exist yet — that is intentional. The tests
define the expected behaviour.

### Imports

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from task_feeder.reader import RawTask
from task_feeder.review import ReviewLoop, check_answer
```

The import of `task_feeder.review` will cause an ImportError when running tests because the
module doesn't exist yet. That is correct and expected. The tests should be syntactically valid
Python that would pass once the module is implemented.

### Helper: _make_raw_task

```python
def _make_raw_task(
    title: str = "Solve 2+2",
    spec: str = "What is 2+2?",
    solutions: list[str] | None = None,
    level: int = 1,
    problem_type: str = "addition_positive",
    solution_note: str | None = None,
) -> RawTask:
    return RawTask(
        title=title,
        spec=spec,
        solutions=solutions if solutions is not None else ["4"],
        level=level,
        problem_type=problem_type,
        solution_note=solution_note,
    )
```

### Test Class 1: TestCheckAnswer

Tests for the `check_answer(submitted, solutions)` function.

#### test_exact_match
- `check_answer("4", ["4"])` -> True

#### test_case_insensitive_match
- `check_answer("X=15", ["x=15", "15"])` -> True

#### test_whitespace_trimmed_match
- `check_answer("  15  ", ["x=15", "15"])` -> True

#### test_combined_case_and_whitespace
- `check_answer("  X=15  ", ["x=15", "15"])` -> True

#### test_no_match
- `check_answer("42", ["x=15", "15"])` -> False

#### test_multiple_valid_solutions_all_accepted
- `check_answer("x=15", ["x=15", "15"])` -> True
- `check_answer("15", ["x=15", "15"])` -> True

#### test_empty_submitted_answer
- `check_answer("", ["4"])` -> False

#### test_empty_solutions_list
- `check_answer("4", [])` -> False

### Test Class 2: TestReviewLoop

Tests for the ReviewLoop class. Uses mock BaseAgent.

#### Setup Pattern for Each Test
Create a mock agent with async methods:
```python
agent = MagicMock()
agent.agent_id = "a-feeder-test"
agent.list_tasks = AsyncMock(return_value=[...])
agent.get_task = AsyncMock(return_value={...})
agent.approve_task = AsyncMock(return_value={"status": "approved"})
agent.dispute_task = AsyncMock(return_value={"status": "disputed"})
```

#### test_correct_answer_triggers_approve
- Create task_map with one task: `{"t-1": _make_raw_task(solutions=["4"])}`
- Mock `agent.list_tasks` to return `[{"task_id": "t-1", "status": "submitted"}]` (tasks in review)
- Mock `agent.get_task` to return a task dict with submitted answer "4"
  (The exact field name for the submitted answer depends on implementation.
  Use a reasonable mock — the test validates that approve_task was called.)
- Create ReviewLoop(agent, task_map)
- Call `await loop.review_one("t-1")`
- Assert `agent.approve_task.assert_called_once_with("t-1")`

#### test_incorrect_answer_triggers_dispute
- Create task_map with one task: `{"t-1": _make_raw_task(solutions=["4"])}`
- Mock agent so submitted answer is "42" (wrong)
- Create ReviewLoop(agent, task_map)
- Call `await loop.review_one("t-1")`
- Assert `agent.dispute_task.assert_called_once()`
- Check dispute reason contains "Expected" and "got" and "42"

#### test_case_insensitive_comparison_in_review
- task_map with solutions=["x=15", "15"]
- Submitted answer is "X=15" (uppercase)
- review_one should call approve_task (not dispute)

#### test_whitespace_trimmed_comparison_in_review
- task_map with solutions=["15"]
- Submitted answer is "  15  " (padded)
- review_one should call approve_task

#### test_multiple_valid_solutions_accepted_in_review
- task_map with solutions=["x=15", "15"]
- Test with submitted="x=15" -> approve
- Test with submitted="15" -> approve

#### test_task_map_lookup
- Create task_map with multiple entries: {"t-1": task1, "t-2": task2}
- Create ReviewLoop(agent, task_map)
- Verify ReviewLoop stores the mapping correctly (access loop._task_map or equivalent)
- Verify looking up "t-1" returns task1, "t-2" returns task2

#### test_unknown_task_id_skipped
- task_map does NOT contain "t-unknown"
- review_one("t-unknown") should not call approve or dispute
- (It should log a warning or raise a specific error — either behaviour is acceptable)

=== PHASE 2: Verify CI Compliance ===

From the agents/ directory, run:
```bash
cd agents && uv run ruff check tests/unit/task_feeder/test_review.py && uv run ruff format --check tests/unit/task_feeder/test_review.py
```

Fix any formatting or linting errors. Common issues:
- Import ordering (ruff will flag this)
- Line length > 100 characters
- Unused imports (remove AsyncMock or patch if not used)
- Missing type annotations on test method parameters (not required for tests)

After fixing, re-run until clean.

=== PHASE 3: Verify Tests Collect (but Fail) ===

From the agents/ directory, run:
```bash
cd agents && uv run pytest tests/unit/task_feeder/test_review.py --collect-only 2>&1 | head -40
```

This will fail with ImportError because `task_feeder.review` does not exist.
That is CORRECT and EXPECTED. The tests are syntactically valid Python that define
the behaviour of a module that will be implemented later.

If the collect-only shows an ImportError for `task_feeder.review`, that confirms:
1. The test file is syntactically valid
2. The import is correct (just the module doesn't exist yet)
3. Once the module is implemented, these tests will run

Report the ImportError and confirm the test file is ready.

Then commit with message:
```
test: add unit tests for feeder auto-approval review loop

Tests define expected behaviour for task_feeder.review module:
check_answer() with case-insensitive and whitespace-trimmed matching,
ReviewLoop with approve/dispute decisions, task_map lookup,
and unknown task handling.

Module does not exist yet — tests fail with ImportError by design (TDD).
Closes agent-economy-gsu.
```

Do NOT push to remote. Only commit locally.
