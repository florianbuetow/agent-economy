"""DisputeDbClient response handling must not drop persisted claim/rebuttal/ruling columns.

GAP-E12 residue audit (see task-board's TaskDbClient._normalize_task /
commit 91b649b for the original finding). Unlike the other three services'
*_db_client.py files, `DisputeDbClient._compose_dispute` is a genuine
many-to-one DTO: it merges rows from THREE tables (court_claims,
court_rebuttals, court_rulings) into one synthetic "dispute" view, renaming
fields along the way (`reason`->`claim`, `content`->`rebuttal`,
`summary`->`ruling_summary`, the rebuttal's `submitted_at`->`rebutted_at`).
A straight "are all dict keys present" diff (as used for the other three
services) does not fit that shape, so this suite maps each schema column to
its composed-dispute field explicitly.

Every column across the three tables is accounted for: either it appears
(under its mapped name) in the composed dispute, or it is a redundant
identifier column that always equals a value the caller already has —
documented below, exactly like task-board's `_RECOMPUTED_COLUMNS`:

- court_claims.claim_id: redundant with the `dispute_id` argument (one claim
  per dispute; `get_dispute_row`/`get_dispute` are always called BY claim_id).
- court_rebuttals.rebuttal_id: an internal identifier never surfaced anywhere.
- court_rebuttals.claim_id: redundant with `dispute_id` (one rebuttal per claim).
- court_rebuttals.agent_id: redundant with claim.respondent_id (a rebuttal's
  author is always the respondent — enforced by the write path).
- court_rulings.ruling_id: DisputeDbClient.persist_ruling always sets
  ruling_id = claim_id = dispute_id, so it is redundant by construction.
- court_rulings.claim_id / court_rulings.task_id: redundant with dispute_id /
  claim["task_id"], already present in the composed dispute.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from court_service.services.dispute_db_client import DisputeDbClient

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"

_NON_COLUMN_PREFIXES = ("FOREIGN KEY", "CHECK", "PRIMARY KEY", "UNIQUE")

_CLAIMS_REDUNDANT = frozenset({"claim_id"})
_REBUTTALS_REDUNDANT = frozenset({"rebuttal_id", "claim_id", "agent_id"})
_RULINGS_REDUNDANT = frozenset({"ruling_id", "claim_id", "task_id"})


def _schema_columns(table: str) -> set[str]:
    """Column names declared for `table` in the canonical schema."""
    schema = SCHEMA_PATH.read_text()
    match = re.search(rf"CREATE TABLE {table}\s*\((.*?)\n\);", schema, re.DOTALL)
    assert match is not None, f"{table} table not found in schema.sql"

    columns: set[str] = set()
    for raw_line in match.group(1).splitlines():
        line = raw_line.split("--")[0].strip()
        if not line or line.startswith(_NON_COLUMN_PREFIXES):
            continue
        name = line.split()[0].strip(",")
        if name.isidentifier():
            columns.add(name)
    return columns


def _mock_response(status_code: int, json_data: Any = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = ""
    if json_data is not None:
        response.json.return_value = json_data
    return response


@pytest.fixture
def client() -> DisputeDbClient:
    return DisputeDbClient(base_url="http://gateway.invalid", timeout_seconds=1)


@pytest.mark.unit
class TestCourtTablesSchemaShape:
    def test_court_claims_has_the_expected_columns(self) -> None:
        assert _schema_columns("court_claims") == {
            "claim_id",
            "task_id",
            "claimant_id",
            "respondent_id",
            "reason",
            "status",
            "rebuttal_deadline",
            "filed_at",
        }

    def test_court_rebuttals_has_the_expected_columns(self) -> None:
        assert _schema_columns("court_rebuttals") == {
            "rebuttal_id",
            "claim_id",
            "agent_id",
            "content",
            "submitted_at",
        }

    def test_court_rulings_has_the_expected_columns(self) -> None:
        assert _schema_columns("court_rulings") == {
            "ruling_id",
            "claim_id",
            "task_id",
            "worker_pct",
            "summary",
            "judge_votes",
            "ruled_at",
        }


@pytest.mark.unit
class TestComposeDisputeCoversEveryNonRedundantColumn:
    def test_get_dispute_surfaces_every_non_redundant_column(self, client: DisputeDbClient) -> None:
        dispute_id = "clm-1"
        claim = {
            "claim_id": dispute_id,
            "task_id": "t-1",
            "claimant_id": "a-poster",
            "respondent_id": "a-worker",
            "reason": "Spec was not met",
            "status": "ruled",
            "rebuttal_deadline": "2026-03-01T12:00:00Z",
            "filed_at": "2026-03-01T09:00:00Z",
        }
        rebuttal = {
            "rebuttal_id": "reb-1",
            "claim_id": dispute_id,
            "agent_id": "a-worker",
            "content": "I did meet the spec",
            "submitted_at": "2026-03-01T10:00:00Z",
        }
        ruling = {
            "ruling_id": dispute_id,
            "claim_id": dispute_id,
            "task_id": "t-1",
            "worker_pct": 70,
            "summary": "Spec was ambiguous",
            "judge_votes": '[{"judge_id": "judge-1", "worker_pct": 70, "reasoning": "ok"}]',
            "ruled_at": "2026-03-01T11:00:00Z",
        }
        task = {"escrow_id": "esc-1"}

        def _get(path: str, **_kwargs: object) -> MagicMock:
            if path == f"/court/claims/{dispute_id}":
                return _mock_response(200, claim)
            if path == f"/court/claims/{dispute_id}/rebuttal":
                return _mock_response(200, rebuttal)
            if path == f"/court/rulings/{dispute_id}":
                return _mock_response(200, ruling)
            if path == "/board/tasks/t-1":
                return _mock_response(200, task)
            return _mock_response(404)

        client._client.get = MagicMock(side_effect=_get)

        dispute = client.get_dispute(dispute_id)

        assert dispute is not None
        # court_claims — every non-redundant column, under its composed name.
        assert dispute["task_id"] == claim["task_id"]
        assert dispute["claimant_id"] == claim["claimant_id"]
        assert dispute["respondent_id"] == claim["respondent_id"]
        assert dispute["claim"] == claim["reason"]
        assert dispute["status"] == claim["status"]
        assert dispute["rebuttal_deadline"] == claim["rebuttal_deadline"]
        assert dispute["filed_at"] == claim["filed_at"]
        missing_claims_cols = _schema_columns("court_claims") - _CLAIMS_REDUNDANT - {"task_id"}
        assert missing_claims_cols == {
            "claimant_id",
            "respondent_id",
            "reason",
            "status",
            "rebuttal_deadline",
            "filed_at",
        }, "this test's own accounting of court_claims columns is out of date"

        # court_rebuttals — every non-redundant column, under its composed name.
        assert dispute["rebuttal"] == rebuttal["content"]
        assert dispute["rebutted_at"] == rebuttal["submitted_at"]
        missing_rebuttals_cols = _schema_columns("court_rebuttals") - _REBUTTALS_REDUNDANT
        assert missing_rebuttals_cols == {"content", "submitted_at"}, (
            "this test's own accounting of court_rebuttals columns is out of date"
        )

        # court_rulings — every non-redundant column, under its composed name.
        assert dispute["worker_pct"] == ruling["worker_pct"]
        assert dispute["ruling_summary"] == ruling["summary"]
        assert dispute["ruled_at"] == ruling["ruled_at"]
        assert dispute["votes"] != []  # judge_votes is parsed, not dropped
        missing_rulings_cols = _schema_columns("court_rulings") - _RULINGS_REDUNDANT
        assert missing_rulings_cols == {"worker_pct", "summary", "judge_votes", "ruled_at"}, (
            "this test's own accounting of court_rulings columns is out of date"
        )

    def test_get_dispute_with_no_rebuttal_or_ruling_yet(self, client: DisputeDbClient) -> None:
        """A freshly filed claim (no rebuttal, no ruling) composes without KeyErrors."""
        dispute_id = "clm-2"
        claim = {
            "claim_id": dispute_id,
            "task_id": "t-2",
            "claimant_id": "a-poster",
            "respondent_id": "a-worker",
            "reason": "Spec was not met",
            "status": "filed",
            "rebuttal_deadline": "2026-03-01T12:00:00Z",
            "filed_at": "2026-03-01T09:00:00Z",
        }

        def _get(path: str, **_kwargs: object) -> MagicMock:
            if path == f"/court/claims/{dispute_id}":
                return _mock_response(200, claim)
            if path in (
                f"/court/claims/{dispute_id}/rebuttal",
                f"/court/rulings/{dispute_id}",
            ):
                return _mock_response(404)
            if path == "/board/tasks/t-2":
                return _mock_response(200, {"escrow_id": "esc-2"})
            return _mock_response(404)

        client._client.get = MagicMock(side_effect=_get)

        dispute = client.get_dispute(dispute_id)

        assert dispute is not None
        assert dispute["rebuttal"] is None
        assert dispute["rebutted_at"] is None
        assert dispute["worker_pct"] is None
        assert dispute["ruling_summary"] is None
        assert dispute["ruled_at"] is None
        assert dispute["votes"] == []
