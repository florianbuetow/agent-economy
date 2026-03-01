"""Unit tests for math_worker.parser."""

import pytest

from math_worker.parser import parse_bid_amount, parse_solution, parse_task_selection


@pytest.mark.unit
class TestParseTaskSelection:
    def test_exact_id_match(self) -> None:
        assert parse_task_selection("t-abc-123", ["t-abc-123", "t-def-456"]) == "t-abc-123"

    def test_id_in_sentence(self) -> None:
        result = parse_task_selection(
            "I would choose t-abc-123 because it looks easy.",
            ["t-abc-123", "t-def-456"],
        )
        assert result == "t-abc-123"

    def test_none_response(self) -> None:
        assert parse_task_selection("NONE", ["t-abc-123"]) is None

    def test_none_case_insensitive(self) -> None:
        assert parse_task_selection("none", ["t-abc-123"]) is None

    def test_unrecognised_response(self) -> None:
        assert parse_task_selection("I don't know", ["t-abc-123"]) is None

    def test_first_match_wins(self) -> None:
        result = parse_task_selection(
            "t-first then t-second",
            ["t-first", "t-second"],
        )
        assert result == "t-first"


@pytest.mark.unit
class TestParseBidAmount:
    def test_bare_integer(self) -> None:
        assert parse_bid_amount("50", 100) == 50

    def test_integer_in_text(self) -> None:
        assert parse_bid_amount("I would bid 75 credits", 100) == 75

    def test_out_of_range_too_high(self) -> None:
        assert parse_bid_amount("200", 100) is None

    def test_out_of_range_zero(self) -> None:
        assert parse_bid_amount("0", 100) is None

    def test_no_integer(self) -> None:
        assert parse_bid_amount("lots of money", 100) is None

    def test_exact_max(self) -> None:
        assert parse_bid_amount("100", 100) == 100

    def test_exact_min(self) -> None:
        assert parse_bid_amount("1", 100) == 1


@pytest.mark.unit
class TestParseSolution:
    def test_answer_marker(self) -> None:
        response = "Step 1: add\nStep 2: done\nANSWER: 42"
        assert parse_solution(response) == "42"

    def test_answer_marker_case_insensitive(self) -> None:
        assert parse_solution("answer: 42") == "42"

    def test_answer_marker_with_spaces(self) -> None:
        assert parse_solution("  Answer:   x=5, y=3  ") == "x=5, y=3"

    def test_fallback_last_line(self) -> None:
        response = "Let me think...\nThe result is 7"
        assert parse_solution(response) == "The result is 7"

    def test_empty_response(self) -> None:
        assert parse_solution("") is None

    def test_whitespace_only(self) -> None:
        assert parse_solution("   \n  \n  ") is None

    def test_multiple_answer_markers_uses_last(self) -> None:
        response = "ANSWER: wrong\nActually...\nANSWER: 99"
        assert parse_solution(response) == "99"
