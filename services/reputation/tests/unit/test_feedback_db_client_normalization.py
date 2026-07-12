"""FeedbackDbClient response handling must not drop persisted feedback columns.

GAP-E12 residue audit (see task-board's TaskDbClient._normalize_task /
commit 91b649b for the original finding). `FeedbackDbClient._dict_to_record`
rebuilds a `FeedbackRecord` from named fields, which is the same
whitelist-by-a-different-spelling risk: a schema column not named there is
silently unavailable to every caller with no failing test anywhere.

`role` was exactly this case: `reputation_feedback.role` is a real, NOT NULL,
persisted column (written from `_role_for_category(category)` at insert time),
but `_dict_to_record` never read it back and `FeedbackRecord` had no field for
it — every reader of a fetched record lost the value, even though nothing
currently *acts* on it (role happens to be 1:1 derivable from category today,
but the read path silently discarding real, stored data is exactly the failure
mode GAP-E12 flags). Fixed by adding `role: str | None = None` to
`FeedbackRecord` (defaulted so no existing construction call site breaks) and
having `_dict_to_record` populate it from the gateway response when present.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from reputation_service.services.feedback_db_client import FeedbackDbClient

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"

_NON_COLUMN_PREFIXES = ("FOREIGN KEY", "CHECK", "PRIMARY KEY", "UNIQUE")


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
def client() -> FeedbackDbClient:
    return FeedbackDbClient(base_url="http://gateway.invalid", timeout_seconds=1)


@pytest.mark.unit
class TestReputationFeedbackSchemaShape:
    def test_reputation_feedback_has_the_expected_columns(self) -> None:
        assert _schema_columns("reputation_feedback") == {
            "feedback_id",
            "task_id",
            "from_agent_id",
            "to_agent_id",
            "role",
            "category",
            "rating",
            "comment",
            "submitted_at",
            "visible",
        }


@pytest.mark.unit
class TestDictToRecordCoversEveryColumn:
    """get_by_id (and every other read via _dict_to_record) must surface every
    reputation_feedback column that FeedbackRecord has a field for."""

    def test_get_by_id_returns_a_record_with_every_schema_field(
        self, client: FeedbackDbClient
    ) -> None:
        row = {
            "feedback_id": "fb-1",
            "task_id": "t-1",
            "from_agent_id": "a-alice",
            "to_agent_id": "a-bob",
            "role": "worker",
            "category": "spec_quality",
            "rating": "satisfied",
            "comment": "Great spec",
            "submitted_at": "2026-03-01T09:00:00Z",
            "visible": True,
        }
        client._client.get = MagicMock(return_value=_mock_response(200, row))

        record = client.get_by_id("fb-1")

        assert record is not None
        record_fields = {f.name for f in dataclasses.fields(record)}
        missing = _schema_columns("reputation_feedback") - record_fields
        assert missing == set(), (
            f"reputation_feedback columns absent from FeedbackRecord entirely: {sorted(missing)}."
        )
        assert record.role == "worker"

    def test_role_survives_normalization(self, client: FeedbackDbClient) -> None:
        row = {
            "feedback_id": "fb-1",
            "task_id": "t-1",
            "from_agent_id": "a-alice",
            "to_agent_id": "a-bob",
            "role": "poster",
            "category": "delivery_quality",
            "rating": "extremely_satisfied",
            "comment": None,
            "submitted_at": "2026-03-01T09:00:00Z",
            "visible": False,
        }
        client._client.get = MagicMock(return_value=_mock_response(200, row))

        record = client.get_by_id("fb-1")

        assert record is not None
        assert record.role == "poster"

    def test_role_defaults_to_none_when_the_gateway_response_omits_it(
        self, client: FeedbackDbClient
    ) -> None:
        """Backward-compatible default: a response body without role does not crash."""
        row = {
            "feedback_id": "fb-1",
            "task_id": "t-1",
            "from_agent_id": "a-alice",
            "to_agent_id": "a-bob",
            "category": "spec_quality",
            "rating": "satisfied",
            "comment": None,
            "submitted_at": "2026-03-01T09:00:00Z",
            "visible": True,
        }
        client._client.get = MagicMock(return_value=_mock_response(200, row))

        record = client.get_by_id("fb-1")

        assert record is not None
        assert record.role is None
