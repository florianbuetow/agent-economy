"""GET /board/tasks with a non-numeric limit/offset must return a clean 400,
not crash to an uncaught 500 (WP-12 sweep finding, WP-13 item 8b).

Before this fix, `list_tasks` did a bare `int(limit_str)` / `int(offset_str)`
with no exception handling — a non-numeric value raised an unhandled
`ValueError`, which the app's catch-all handler turned into a generic
`500 internal_error`, violating the no-500 release rule (every documented
error path must be a deliberate, typed `ServiceError`). Mirrors the
established convention for the exact same parameters on task-board's own
`GET /tasks` (`services/task-board/src/task_board_service/routers/tasks.py`):
`invalid_payload`, 400.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


@pytest.mark.unit
class TestListTasksLimitOffsetValidation:
    def test_non_numeric_limit_returns_400_not_500(self, app_with_writer: TestClient) -> None:
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks?limit=abc")

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    def test_non_numeric_offset_returns_400_not_500(self, app_with_writer: TestClient) -> None:
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks?offset=xyz")

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    def test_valid_numeric_limit_and_offset_still_work(self, app_with_writer: TestClient) -> None:
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks?limit=10&offset=0")

        assert response.status_code == 200
        assert response.json() == {"tasks": []}
