"""UI detail/list expose financial truth from authentic migrated task histories."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import httpx

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[4]
_BUILD_HISTORY = Path(__file__).resolve().parents[1] / "fakes" / "mp08_financial_history.py"
_FINANCIAL_KEYS = {
    "schema_version",
    "settlement_state",
    "receipt_state",
    "outcome",
    "operation_id",
    "receipt_reference",
    "business_projection_pending",
    "pending_target_status",
}
_EXPECTED: dict[str, dict[str, object]] = {
    "legacy_pending": {
        "settlement_state": "pending",
        "receipt_state": "absent",
        "outcome": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "legacy_succeeded": {
        "settlement_state": "pending",
        "receipt_state": "confirmed",
        "outcome": "worker_paid",
        "business_projection_pending": True,
        "pending_target_status": "approved",
    },
    "historical_paid": {
        "settlement_state": "historical",
        "receipt_state": "historical",
        "outcome": "worker_paid",
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "court_only": {
        "settlement_state": "locked",
        "receipt_state": "absent",
        "outcome": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "native_pending": {
        "settlement_state": "pending",
        "receipt_state": "absent",
        "outcome": None,
        "business_projection_pending": True,
        "pending_target_status": "approved",
    },
    "receipt_projection_pending": {
        "settlement_state": "pending",
        "receipt_state": "confirmed",
        "outcome": "worker_paid",
        "business_projection_pending": True,
        "pending_target_status": "approved",
    },
    "completed_refund": {
        "settlement_state": "complete",
        "receipt_state": "confirmed",
        "outcome": "poster_refunded",
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "completed_split": {
        "settlement_state": "complete",
        "receipt_state": "confirmed",
        "outcome": "split",
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "source_free_approved": {
        "settlement_state": "attention_required",
        "receipt_state": "absent",
        "outcome": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "source_free_ruled": {
        "settlement_state": "attention_required",
        "receipt_state": "absent",
        "outcome": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "conflict_wrong_receipt": {
        "settlement_state": "conflict",
        "receipt_state": "absent",
        "outcome": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "conflict_competing_winner": {
        "settlement_state": "conflict",
        "receipt_state": "absent",
        "outcome": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "conflict_resolved_without_receipt": {
        "settlement_state": "conflict",
        "receipt_state": "absent",
        "outcome": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "conflict_confirmed_projection": {
        "settlement_state": "conflict",
        "receipt_state": "confirmed",
        "outcome": "worker_paid",
        "business_projection_pending": False,
        "pending_target_status": None,
    },
}


@pytest.fixture(params=tuple(_EXPECTED))
def financial_history(request: pytest.FixtureRequest, tmp_path: Path) -> dict[str, Any]:
    """Use Gateway's own environment and verified migrations, without importing it into UI."""
    kind = str(request.param)
    environment = dict(os.environ)
    environment.pop("CONFIG_PATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["UV_FROZEN"] = "1"
    built = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            str(_BUILD_HISTORY),
            str(tmp_path / "financial-history"),
            kind,
        ],
        cwd=_ROOT / "services/db-gateway",
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    history: dict[str, Any] = json.loads(built.stdout.strip().splitlines()[-1])
    history["kind"] = kind
    assert Path(history["database"]).is_relative_to(tmp_path)
    return history


@pytest.fixture
def db_path(financial_history: dict[str, Any]) -> Path:
    """Override only the integration app's owned database; keep real route/lifespan wiring."""
    return Path(financial_history["database"])


def _expected(history: dict[str, Any]) -> dict[str, object]:
    kind = history["kind"]
    operation_id = history["operation_id"]
    confirmed = _EXPECTED[kind]["receipt_state"] == "confirmed"
    modern = kind not in {"historical_paid", "court_only"}
    return {
        "schema_version": 1,
        **_EXPECTED[kind],
        "operation_id": operation_id if modern else None,
        "receipt_reference": operation_id if confirmed else None,
    }


async def test_ui_financial_projection_matches_authoritative_history(
    client: httpx.AsyncClient,
    financial_history: dict[str, Any],
) -> None:
    task_id = financial_history["task_id"]
    detail = await client.get(f"/api/tasks/{task_id}")
    listed = await client.get("/api/tasks?limit=100&offset=0")
    assert detail.status_code == 200, detail.text
    assert listed.status_code == 200, listed.text
    matches = [task for task in listed.json()["tasks"] if task["task_id"] == task_id]
    assert len(matches) == 1
    expected = _expected(financial_history)
    for value in (detail.json(), matches[0]):
        assert value["task_id"] == task_id
        assert "financial_effect" in value, "UI omitted authoritative settlement truth"
        assert set(value["financial_effect"]) == _FINANCIAL_KEYS
        assert value["financial_effect"] == expected
