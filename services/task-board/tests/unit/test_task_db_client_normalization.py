"""TaskDbClient response normalization must not drop persisted task columns.

`TaskDbClient._normalize_task` rebuilds every gateway response from its
`_TASK_COLUMNS` whitelist, so a column that exists in the database but is
missing from that tuple is silently dropped on the production read path
while the in-memory test fakes keep returning it. That failure mode is
invisible to the router unit tests, which inject a fake store.

`dispute_id` is the concrete case: without it in the whitelist every
`get_task` returns `dispute_id=None`, so `submit_rebuttal` would reject
every legitimate rebuttal with 409 "Task has no recorded dispute" (GAP-B9).
These tests pin the whitelist against the canonical schema.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from task_board_service.services.task_db_client import TaskDbClient

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"

_NON_COLUMN_PREFIXES = ("FOREIGN KEY", "CHECK", "PRIMARY KEY", "UNIQUE")

# Stored in board_tasks but deliberately not normalized: TaskManager recomputes
# these from created_at/accepted_at/submitted_at plus the *_seconds columns via
# DeadlineEvaluator.compute_deadline, so the client never reads them back.
_RECOMPUTED_COLUMNS = frozenset({"bidding_deadline", "execution_deadline", "review_deadline"})


def _schema_board_task_columns() -> set[str]:
    """Column names declared for board_tasks in the canonical schema."""
    schema = SCHEMA_PATH.read_text()
    match = re.search(r"CREATE TABLE board_tasks\s*\((.*?)\n\);", schema, re.DOTALL)
    assert match is not None, "board_tasks table not found in schema.sql"

    columns: set[str] = set()
    for raw_line in match.group(1).splitlines():
        line = raw_line.split("--")[0].strip()
        if not line or line.startswith(_NON_COLUMN_PREFIXES):
            continue
        name = line.split()[0].strip(",")
        if name.isidentifier():
            columns.add(name)
    return columns


@pytest.fixture
def client() -> TaskDbClient:
    """A client bound to an unroutable base_url; no request is ever issued."""
    return TaskDbClient(base_url="http://gateway.invalid", timeout_seconds=1)


@pytest.mark.unit
class TestTaskDbClientNormalization:
    def test_whitelist_covers_every_persisted_schema_column(self) -> None:
        """Any column added to board_tasks must be added to _TASK_COLUMNS too."""
        expected = _schema_board_task_columns() - _RECOMPUTED_COLUMNS
        missing = expected - set(TaskDbClient._TASK_COLUMNS)
        assert missing == set(), (
            f"board_tasks columns absent from TaskDbClient._TASK_COLUMNS: {sorted(missing)}. "
            "They would be silently dropped from every gateway read."
        )

    def test_recomputed_columns_are_not_read_back(self) -> None:
        """The deadline columns stay out of the whitelist; TaskManager derives them."""
        assert _RECOMPUTED_COLUMNS.isdisjoint(TaskDbClient._TASK_COLUMNS)
        assert _schema_board_task_columns() >= _RECOMPUTED_COLUMNS

    def test_dispute_id_survives_normalization(self, client: TaskDbClient) -> None:
        """A gateway row carrying dispute_id must keep it after normalization."""
        row: dict[str, Any] = dict.fromkeys(TaskDbClient._TASK_COLUMNS)
        row["task_id"] = "t-1"
        row["dispute_id"] = "disp-xyz"

        normalized = client._normalize_task(row)

        assert normalized["dispute_id"] == "disp-xyz"

    def test_unknown_columns_are_dropped(self, client: TaskDbClient) -> None:
        """Normalization is a whitelist: unknown keys do not leak through."""
        row: dict[str, Any] = dict.fromkeys(TaskDbClient._TASK_COLUMNS)
        row["surprise_column"] = "leaked"

        normalized = client._normalize_task(row)

        assert "surprise_column" not in normalized
