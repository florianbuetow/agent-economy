"""Bound global historical audits to one immutable context per list read."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from db_gateway_service import financial_effect_projection as projection_module
from db_gateway_service.services import db_reader as reader_module
from db_gateway_service.services.db_reader import DbReader

pytestmark = pytest.mark.unit


@dataclass
class _Rows:
    rows: list[dict[str, object]]

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows

    def fetchone(self) -> None:
        return None


@dataclass
class _Connection:
    rows: list[dict[str, object]]

    def execute(self, query: str, _params: object = ()) -> _Rows:
        return _Rows(self.rows if query.startswith("\nWITH active_task_operations") else [])


def _row(index: int, *, historical_candidate: bool) -> dict[str, object]:
    return {
        "task_id": f"task-{index}",
        "status": "approved" if historical_candidate else "open",
        "approved_at": "2099-01-01T00:00:00Z" if historical_candidate else None,
        "worker_id": f"worker-{index}" if historical_candidate else None,
        "escrow_id": f"escrow-{index}",
        "_contract_task_id": f"task-{index}",
        "_pending_transition_json": None,
        "_legacy_reconciliations_json": "[]",
    }


def test_many_tasks_share_one_global_audit_context_per_list_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(index, historical_candidate=index in {40, 80}) for index in range(100)]
    builds: list[object] = []
    classifications: list[str] = []

    def possible(_connection: object, task: dict[str, object]) -> bool:
        return task["status"] == "approved"

    def build(_connection: object) -> object:
        context = object()
        builds.append(context)
        return context

    def classify(*_args: object) -> SimpleNamespace:
        classifications.append("classified")
        return SimpleNamespace(qualifies=False)

    monkeypatch.setattr(
        reader_module,
        "possible_historical_worker_payment",
        possible,
        raising=False,
    )
    monkeypatch.setattr(
        projection_module,
        "possible_historical_worker_payment",
        possible,
        raising=False,
    )
    monkeypatch.setattr(
        reader_module,
        "build_historical_audit_context",
        build,
        raising=False,
    )
    monkeypatch.setattr(projection_module, "classify_historical_worker_payment", classify)
    reader = DbReader(_Connection(rows))  # type: ignore[arg-type]

    assert len(reader.list_tasks(None, None, None, None, None)) == 100
    assert len(classifications) == 2
    assert len(builds) == 1

    classifications.clear()
    assert len(reader.list_tasks(None, None, None, None, None)) == 100
    assert len(builds) == 2
    assert len(classifications) == 2
