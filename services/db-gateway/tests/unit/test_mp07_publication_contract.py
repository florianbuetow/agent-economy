"""Publication and its immutable timing contract must be one Gateway write."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.fakes.native_board_history import BusinessClock
from tests.unit.routers.test_mp07_task_operation_boundary import _seed_graph

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fixed_publication_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "db_gateway_service.task_time.utc_now_us",
        BusinessClock(now_us=parse_utc_text_to_epoch_us("2099-01-01T00:00:00Z")),
    )


def test_new_publication_has_exact_native_timing_sidecar(
    app_with_writer: TestClient, db_writer: DbWriter
) -> None:
    graph = _seed_graph(app_with_writer)
    rows = db_writer.connection.execute(
        "SELECT task_id, lifecycle_version, timing_contract_version, "
        "acceptance_window_seconds, bidding_cutoff_us, acceptance_cutoff_us, "
        "execution_cutoff_us, review_cutoff_us, accepted_reward, accepted_bid_amount "
        "FROM board_task_contracts WHERE task_id=?",
        (graph.task_id,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (graph.task_id, 0, 2, 86400, 4070995200000000, 4071081600000000, None, None, None, None)
    ]
    assert audit_task_contracts(db_writer.connection).contracts_clean
