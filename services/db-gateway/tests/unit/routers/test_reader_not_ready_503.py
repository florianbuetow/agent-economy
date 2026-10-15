"""GET read routes must answer 503 service_not_ready when the DbReader isn't
wired yet — never 405 method_not_allowed (WP-12 escalation, WP-13 item 8).

`GET /board/tasks`, `GET /court/claims`, and `GET /reputation/feedback` each
had a copy-pasted `method_not_allowed`/405 branch instead of the
`service_not_ready`/503 branch every other not-ready read route in this
service uses (see e.g. `GET /board/tasks/count` right above `list_tasks` in
board.py). `app_with_writer` (tests/unit/conftest.py) leaves `state.db_reader`
unset by default — these tests never call `_wire_db_reader()`, so they hit
that not-ready branch directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.mark.unit
class TestBoardListTasksNotReady:
    def test_list_tasks_returns_503_not_405_when_reader_unset(
        self, app_with_writer: TestClient
    ) -> None:
        """GET /board/tasks with no DbReader wired returns 503 service_not_ready."""
        response = app_with_writer.get("/board/tasks")
        assert response.status_code == 503
        assert response.json()["error"] == "service_not_ready"


@pytest.mark.unit
class TestCourtListClaimsNotReady:
    def test_list_claims_returns_503_not_405_when_reader_unset(
        self, app_with_writer: TestClient
    ) -> None:
        """GET /court/claims with no DbReader wired returns 503 service_not_ready."""
        response = app_with_writer.get("/court/claims")
        assert response.status_code == 503
        assert response.json()["error"] == "service_not_ready"


@pytest.mark.unit
class TestReputationListFeedbackNotReady:
    def test_list_feedback_returns_503_not_405_when_reader_unset(
        self, app_with_writer: TestClient
    ) -> None:
        """GET /reputation/feedback with no DbReader wired returns 503 service_not_ready."""
        response = app_with_writer.get("/reputation/feedback")
        assert response.status_code == 503
        assert response.json()["error"] == "service_not_ready"
