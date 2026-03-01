"""Tests for feedback business logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from reputation_service.core.state import FeedbackRecord
from reputation_service.services.feedback import (
    ValidationError,
    get_feedback_by_id,
    get_feedback_for_agent,
    get_feedback_for_task,
    is_visible,
    submit_feedback,
    validate_feedback,
)
from reputation_service.services.feedback_store import FeedbackStore

if TYPE_CHECKING:
    from pathlib import Path

MAX_COMMENT_LENGTH = 256
REVEAL_TIMEOUT = 86400


def _make_store(tmp_path: Path) -> FeedbackStore:
    """Create a FeedbackStore with a temp database."""
    return FeedbackStore(db_path=str(tmp_path / "test.db"))


def _valid_body(**overrides: object) -> dict[str, object]:
    """Return a valid feedback body with optional overrides."""
    base: dict[str, object] = {
        "task_id": "task-1",
        "from_agent_id": "agent-a",
        "to_agent_id": "agent-b",
        "category": "delivery_quality",
        "rating": "satisfied",
        "comment": "Good work",
    }
    base.update(overrides)
    return base


@pytest.mark.unit
class TestValidationOrder:
    """Test validation fires in priority order."""

    def test_invalid_field_type_before_missing_field(self) -> None:
        """INVALID_FIELD_TYPE fires before MISSING_FIELD."""
        # task_id is an int (invalid type) AND from_agent_id is missing
        body: dict[str, object] = {
            "task_id": 123,
            "to_agent_id": "agent-b",
            "category": "delivery_quality",
            "rating": "satisfied",
        }
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "INVALID_FIELD_TYPE"

    def test_missing_field_before_self_feedback(self) -> None:
        """MISSING_FIELD fires before SELF_FEEDBACK."""
        body: dict[str, object] = {
            "task_id": "task-1",
            "from_agent_id": "agent-a",
            "to_agent_id": "agent-a",
            "category": "delivery_quality",
            # rating is missing
        }
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "MISSING_FIELD"

    def test_self_feedback_before_invalid_category(self) -> None:
        """SELF_FEEDBACK fires before INVALID_CATEGORY."""
        body = _valid_body(
            from_agent_id="agent-a",
            to_agent_id="agent-a",
            category="bogus",
        )
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "SELF_FEEDBACK"

    def test_invalid_category_before_invalid_rating(self) -> None:
        """INVALID_CATEGORY fires before INVALID_RATING."""
        body = _valid_body(category="bogus", rating="bogus")
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "INVALID_CATEGORY"

    def test_invalid_rating_before_comment_too_long(self) -> None:
        """INVALID_RATING fires before COMMENT_TOO_LONG."""
        body = _valid_body(rating="bogus", comment="x" * (MAX_COMMENT_LENGTH + 1))
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "INVALID_RATING"


@pytest.mark.unit
class TestValidateFeedback:
    """Individual validation rules."""

    def test_valid_body_passes(self) -> None:
        """A fully valid body returns None (no error)."""
        result = validate_feedback(_valid_body(), MAX_COMMENT_LENGTH)
        assert result is None

    def test_valid_body_without_comment(self) -> None:
        """A valid body with no comment field passes."""
        body = _valid_body()
        del body["comment"]
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is None

    def test_valid_body_with_none_comment(self) -> None:
        """A valid body with comment=None passes."""
        result = validate_feedback(_valid_body(comment=None), MAX_COMMENT_LENGTH)
        assert result is None

    # INVALID_FIELD_TYPE
    def test_task_id_integer_gives_invalid_field_type(self) -> None:
        result = validate_feedback(_valid_body(task_id=42), MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "INVALID_FIELD_TYPE"
        assert result.details["field"] == "task_id"

    def test_from_agent_id_list_gives_invalid_field_type(self) -> None:
        result = validate_feedback(_valid_body(from_agent_id=["a"]), MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "INVALID_FIELD_TYPE"
        assert result.details["field"] == "from_agent_id"

    def test_category_bool_gives_invalid_field_type(self) -> None:
        result = validate_feedback(_valid_body(category=True), MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "INVALID_FIELD_TYPE"
        assert result.details["field"] == "category"

    def test_rating_dict_gives_invalid_field_type(self) -> None:
        result = validate_feedback(_valid_body(rating={}), MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "INVALID_FIELD_TYPE"
        assert result.details["field"] == "rating"

    # MISSING_FIELD
    def test_missing_task_id(self) -> None:
        body = _valid_body()
        del body["task_id"]
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "MISSING_FIELD"
        assert result.details["field"] == "task_id"

    def test_missing_from_agent_id(self) -> None:
        body = _valid_body()
        del body["from_agent_id"]
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "MISSING_FIELD"
        assert result.details["field"] == "from_agent_id"

    def test_missing_to_agent_id(self) -> None:
        body = _valid_body()
        del body["to_agent_id"]
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "MISSING_FIELD"
        assert result.details["field"] == "to_agent_id"

    def test_missing_category(self) -> None:
        body = _valid_body()
        del body["category"]
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "MISSING_FIELD"
        assert result.details["field"] == "category"

    def test_missing_rating(self) -> None:
        body = _valid_body()
        del body["rating"]
        result = validate_feedback(body, MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "MISSING_FIELD"
        assert result.details["field"] == "rating"

    def test_empty_string_field_is_missing(self) -> None:
        result = validate_feedback(_valid_body(task_id=""), MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "MISSING_FIELD"
        assert result.details["field"] == "task_id"

    def test_null_field_is_missing(self) -> None:
        result = validate_feedback(_valid_body(task_id=None), MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "MISSING_FIELD"
        assert result.details["field"] == "task_id"

    # SELF_FEEDBACK
    def test_self_feedback(self) -> None:
        result = validate_feedback(
            _valid_body(from_agent_id="agent-a", to_agent_id="agent-a"),
            MAX_COMMENT_LENGTH,
        )
        assert result is not None
        assert result.error == "SELF_FEEDBACK"
        assert result.status_code == 400

    # INVALID_CATEGORY
    def test_invalid_category(self) -> None:
        result = validate_feedback(_valid_body(category="bogus"), MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "INVALID_CATEGORY"

    def test_valid_category_spec_quality(self) -> None:
        result = validate_feedback(_valid_body(category="spec_quality"), MAX_COMMENT_LENGTH)
        assert result is None

    def test_valid_category_delivery_quality(self) -> None:
        result = validate_feedback(_valid_body(category="delivery_quality"), MAX_COMMENT_LENGTH)
        assert result is None

    # INVALID_RATING
    def test_invalid_rating(self) -> None:
        result = validate_feedback(_valid_body(rating="bogus"), MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "INVALID_RATING"

    def test_valid_rating_dissatisfied(self) -> None:
        result = validate_feedback(_valid_body(rating="dissatisfied"), MAX_COMMENT_LENGTH)
        assert result is None

    def test_valid_rating_satisfied(self) -> None:
        result = validate_feedback(_valid_body(rating="satisfied"), MAX_COMMENT_LENGTH)
        assert result is None

    def test_valid_rating_extremely_satisfied(self) -> None:
        result = validate_feedback(_valid_body(rating="extremely_satisfied"), MAX_COMMENT_LENGTH)
        assert result is None

    # COMMENT_TOO_LONG
    def test_comment_too_long(self) -> None:
        long_comment = "x" * (MAX_COMMENT_LENGTH + 1)
        result = validate_feedback(_valid_body(comment=long_comment), MAX_COMMENT_LENGTH)
        assert result is not None
        assert result.error == "COMMENT_TOO_LONG"
        assert result.details["max_length"] == MAX_COMMENT_LENGTH
        assert result.details["actual_length"] == MAX_COMMENT_LENGTH + 1

    def test_comment_at_max_length_is_ok(self) -> None:
        exact_comment = "x" * MAX_COMMENT_LENGTH
        result = validate_feedback(_valid_body(comment=exact_comment), MAX_COMMENT_LENGTH)
        assert result is None


@pytest.mark.unit
class TestSubmitFeedback:
    """Tests for submit_feedback()."""

    def test_submit_returns_record(self, tmp_path: Path) -> None:
        """Successful submission returns a FeedbackRecord."""
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)

    def test_feedback_id_starts_with_fb(self, tmp_path: Path) -> None:
        """Generated feedback_id must start with 'fb-'."""
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        assert result.feedback_id.startswith("fb-")

    def test_submitted_at_is_iso_timestamp(self, tmp_path: Path) -> None:
        """submitted_at must be a valid ISO format timestamp."""
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        # Should not raise
        datetime.fromisoformat(result.submitted_at)

    def test_record_stored_in_store(self, tmp_path: Path) -> None:
        """Record is retrievable from the store."""
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        fetched = store.get_by_id(result.feedback_id)
        assert fetched is not None
        assert fetched.feedback_id == result.feedback_id

    def test_record_indexed_by_task(self, tmp_path: Path) -> None:
        """Record is retrievable by task_id."""
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        records = store.get_by_task("task-1")
        assert any(r.feedback_id == result.feedback_id for r in records)

    def test_record_indexed_by_target_agent(self, tmp_path: Path) -> None:
        """Record is retrievable by to_agent_id."""
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        records = store.get_by_agent("agent-b")
        assert any(r.feedback_id == result.feedback_id for r in records)

    def test_uniqueness_constraint_enforced(self, tmp_path: Path) -> None:
        """Duplicate (task_id, from_agent_id, to_agent_id) is rejected."""
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        assert store.count() == 1

    def test_comment_stored_correctly(self, tmp_path: Path) -> None:
        """Comment is stored as given."""
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(comment="Nice!"), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        assert result.comment == "Nice!"

    def test_none_comment_stored_as_none(self, tmp_path: Path) -> None:
        """When comment is None, it is stored as None."""
        store = _make_store(tmp_path)
        body = _valid_body()
        del body["comment"]
        result = submit_feedback(store, body, MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        assert result.comment is None

    def test_submit_with_validation_error_returns_error(self, tmp_path: Path) -> None:
        """Invalid body returns ValidationError, not FeedbackRecord."""
        store = _make_store(tmp_path)
        body = _valid_body(category="bogus")
        result = submit_feedback(store, body, MAX_COMMENT_LENGTH)
        assert isinstance(result, ValidationError)
        assert result.error == "INVALID_CATEGORY"


@pytest.mark.unit
class TestFeedbackExists:
    """Duplicate (task_id, from_agent_id, to_agent_id) returns 409."""

    def test_duplicate_submission_returns_feedback_exists(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        first = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(first, FeedbackRecord)

        second = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(second, ValidationError)
        assert second.error == "FEEDBACK_EXISTS"
        assert second.status_code == 409

    def test_different_task_id_is_not_duplicate(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        submit_feedback(store, _valid_body(task_id="task-1"), MAX_COMMENT_LENGTH)
        result = submit_feedback(store, _valid_body(task_id="task-2"), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)

    def test_different_from_agent_is_not_duplicate(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        submit_feedback(store, _valid_body(from_agent_id="agent-a"), MAX_COMMENT_LENGTH)
        result = submit_feedback(store, _valid_body(from_agent_id="agent-c"), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)


@pytest.mark.unit
class TestVisibility:
    """First feedback in a pair is sealed; second reveals both."""

    def test_first_feedback_is_sealed(self, tmp_path: Path) -> None:
        """First feedback in a pair has visible=False."""
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        assert result.visible is False

    def test_second_feedback_reveals_both(self, tmp_path: Path) -> None:
        """When the reverse pair submits, both become visible=True."""
        store = _make_store(tmp_path)
        first = submit_feedback(
            store,
            _valid_body(from_agent_id="agent-a", to_agent_id="agent-b"),
            MAX_COMMENT_LENGTH,
        )
        assert isinstance(first, FeedbackRecord)
        assert first.visible is False

        second = submit_feedback(
            store,
            _valid_body(from_agent_id="agent-b", to_agent_id="agent-a"),
            MAX_COMMENT_LENGTH,
        )
        assert isinstance(second, FeedbackRecord)
        assert second.visible is True
        # First record is now also visible (re-fetch from store)
        first_refetched = store.get_by_id(first.feedback_id)
        assert first_refetched is not None
        assert first_refetched.visible is True

    def test_unrelated_feedback_does_not_reveal(self, tmp_path: Path) -> None:
        """Feedback from a different agent does not reveal the first."""
        store = _make_store(tmp_path)
        first = submit_feedback(
            store,
            _valid_body(from_agent_id="agent-a", to_agent_id="agent-b"),
            MAX_COMMENT_LENGTH,
        )
        assert isinstance(first, FeedbackRecord)

        submit_feedback(
            store,
            _valid_body(from_agent_id="agent-c", to_agent_id="agent-a"),
            MAX_COMMENT_LENGTH,
        )
        # Re-fetch first from store to check visibility
        first_refetched = store.get_by_id(first.feedback_id)
        assert first_refetched is not None
        assert first_refetched.visible is False


@pytest.mark.unit
class TestIsVisible:
    """is_visible returns True for visible records or timed-out sealed records."""

    def test_visible_record_returns_true(self) -> None:
        record = FeedbackRecord(
            feedback_id="fb-1",
            task_id="task-1",
            from_agent_id="agent-a",
            to_agent_id="agent-b",
            category="delivery_quality",
            rating="satisfied",
            comment=None,
            submitted_at=datetime.now(UTC).isoformat(),
            visible=True,
        )
        assert is_visible(record, REVEAL_TIMEOUT) is True

    def test_sealed_record_within_timeout_returns_false(self) -> None:
        record = FeedbackRecord(
            feedback_id="fb-1",
            task_id="task-1",
            from_agent_id="agent-a",
            to_agent_id="agent-b",
            category="delivery_quality",
            rating="satisfied",
            comment=None,
            submitted_at=datetime.now(UTC).isoformat(),
            visible=False,
        )
        assert is_visible(record, REVEAL_TIMEOUT) is False

    def test_sealed_record_past_timeout_returns_true(self) -> None:
        past_time = datetime.now(UTC) - timedelta(seconds=REVEAL_TIMEOUT + 1)
        record = FeedbackRecord(
            feedback_id="fb-1",
            task_id="task-1",
            from_agent_id="agent-a",
            to_agent_id="agent-b",
            category="delivery_quality",
            rating="satisfied",
            comment=None,
            submitted_at=past_time.isoformat(),
            visible=False,
        )
        assert is_visible(record, REVEAL_TIMEOUT) is True

    def test_sealed_record_at_exact_timeout_returns_true(self) -> None:
        """At exactly the timeout boundary, record should be visible."""
        past_time = datetime.now(UTC) - timedelta(seconds=REVEAL_TIMEOUT)
        record = FeedbackRecord(
            feedback_id="fb-1",
            task_id="task-1",
            from_agent_id="agent-a",
            to_agent_id="agent-b",
            category="delivery_quality",
            rating="satisfied",
            comment=None,
            submitted_at=past_time.isoformat(),
            visible=False,
        )
        # At exactly the timeout boundary, elapsed >= timeout should be true
        assert is_visible(record, REVEAL_TIMEOUT) is True

    def test_sealed_record_with_zero_timeout_returns_true(self) -> None:
        """With timeout=0, any sealed record is immediately visible."""
        record = FeedbackRecord(
            feedback_id="fb-1",
            task_id="task-1",
            from_agent_id="agent-a",
            to_agent_id="agent-b",
            category="delivery_quality",
            rating="satisfied",
            comment=None,
            submitted_at=datetime.now(UTC).isoformat(),
            visible=False,
        )
        assert is_visible(record, 0) is True


@pytest.mark.unit
class TestGetFeedbackById:
    """get_feedback_by_id returns None for missing or sealed feedback."""

    def test_returns_none_for_missing_id(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert get_feedback_by_id(store, "fb-nonexistent", REVEAL_TIMEOUT) is None

    def test_returns_none_for_sealed_feedback(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        assert result.visible is False
        # Sealed record should not be returned
        assert get_feedback_by_id(store, result.feedback_id, REVEAL_TIMEOUT) is None

    def test_returns_record_for_visible_feedback(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Submit both directions to reveal
        submit_feedback(
            store,
            _valid_body(from_agent_id="agent-a", to_agent_id="agent-b"),
            MAX_COMMENT_LENGTH,
        )
        second = submit_feedback(
            store,
            _valid_body(from_agent_id="agent-b", to_agent_id="agent-a"),
            MAX_COMMENT_LENGTH,
        )
        assert isinstance(second, FeedbackRecord)
        assert second.visible is True
        fetched = get_feedback_by_id(store, second.feedback_id, REVEAL_TIMEOUT)
        assert fetched is not None
        assert fetched.feedback_id == second.feedback_id

    def test_returns_record_for_timed_out_feedback(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        assert isinstance(result, FeedbackRecord)
        # Use a timeout of 0 to simulate timeout behavior
        fetched = get_feedback_by_id(store, result.feedback_id, 0)
        assert fetched is not None


@pytest.mark.unit
class TestGetFeedbackForTask:
    """get_feedback_for_task returns only visible records sorted by submitted_at."""

    def test_empty_store_returns_empty_list(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = get_feedback_for_task(store, "task-1", REVEAL_TIMEOUT)
        assert result == []

    def test_unknown_task_returns_empty_list(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        submit_feedback(store, _valid_body(task_id="task-1"), MAX_COMMENT_LENGTH)
        result = get_feedback_for_task(store, "task-999", REVEAL_TIMEOUT)
        assert result == []

    def test_sealed_feedback_not_returned(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        result = get_feedback_for_task(store, "task-1", REVEAL_TIMEOUT)
        assert result == []

    def test_visible_feedback_returned(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        submit_feedback(
            store,
            _valid_body(from_agent_id="agent-a", to_agent_id="agent-b"),
            MAX_COMMENT_LENGTH,
        )
        submit_feedback(
            store,
            _valid_body(from_agent_id="agent-b", to_agent_id="agent-a"),
            MAX_COMMENT_LENGTH,
        )
        result = get_feedback_for_task(store, "task-1", REVEAL_TIMEOUT)
        assert len(result) == 2

    def test_results_sorted_by_submitted_at(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        submit_feedback(
            store,
            _valid_body(from_agent_id="agent-a", to_agent_id="agent-b"),
            MAX_COMMENT_LENGTH,
        )
        submit_feedback(
            store,
            _valid_body(from_agent_id="agent-b", to_agent_id="agent-a"),
            MAX_COMMENT_LENGTH,
        )
        records = get_feedback_for_task(store, "task-1", REVEAL_TIMEOUT)
        timestamps = [r.submitted_at for r in records]
        assert timestamps == sorted(timestamps)


@pytest.mark.unit
class TestGetFeedbackForAgent:
    """get_feedback_for_agent returns only visible records for target agent."""

    def test_empty_store_returns_empty_list(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = get_feedback_for_agent(store, "agent-b", REVEAL_TIMEOUT)
        assert result == []

    def test_unknown_agent_returns_empty_list(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        result = get_feedback_for_agent(store, "agent-zzz", REVEAL_TIMEOUT)
        assert result == []

    def test_sealed_feedback_not_returned(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH)
        result = get_feedback_for_agent(store, "agent-b", REVEAL_TIMEOUT)
        assert result == []

    def test_visible_feedback_returned_for_target_agent(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        submit_feedback(
            store,
            _valid_body(from_agent_id="agent-a", to_agent_id="agent-b"),
            MAX_COMMENT_LENGTH,
        )
        submit_feedback(
            store,
            _valid_body(from_agent_id="agent-b", to_agent_id="agent-a"),
            MAX_COMMENT_LENGTH,
        )
        # agent-b has one visible feedback record (from agent-a)
        result = get_feedback_for_agent(store, "agent-b", REVEAL_TIMEOUT)
        assert len(result) == 1
        assert result[0].to_agent_id == "agent-b"

    def test_results_sorted_by_submitted_at(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Two different tasks, both with both directions submitted
        submit_feedback(
            store,
            _valid_body(task_id="task-1", from_agent_id="agent-a", to_agent_id="agent-b"),
            MAX_COMMENT_LENGTH,
        )
        submit_feedback(
            store,
            _valid_body(task_id="task-1", from_agent_id="agent-b", to_agent_id="agent-a"),
            MAX_COMMENT_LENGTH,
        )
        submit_feedback(
            store,
            _valid_body(task_id="task-2", from_agent_id="agent-c", to_agent_id="agent-b"),
            MAX_COMMENT_LENGTH,
        )
        submit_feedback(
            store,
            _valid_body(task_id="task-2", from_agent_id="agent-b", to_agent_id="agent-c"),
            MAX_COMMENT_LENGTH,
        )
        records = get_feedback_for_agent(store, "agent-b", REVEAL_TIMEOUT)
        timestamps = [r.submitted_at for r in records]
        assert timestamps == sorted(timestamps)
