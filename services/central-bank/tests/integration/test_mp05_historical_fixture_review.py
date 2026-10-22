"""Independent review regression for MP-05 historical Board fixture coherence."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from tests.integration.test_mp04_historical_observations import _seed_v1

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_disputed_task_retains_the_linked_claim_rebuttal_deadline(tmp_path: Path) -> None:
    database = tmp_path / "historical.db"
    _seed_v1(database)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT t.rebuttal_deadline,c.rebuttal_deadline "
            "FROM board_tasks t JOIN court_claims c ON c.claim_id=t.dispute_id "
            "WHERE t.task_id='task-split'"
        ).fetchone()

    assert row is not None
    assert row[0] == row[1]
