"""Parse structured decisions from free-text LLM responses."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def parse_task_selection(response: str, valid_task_ids: list[str]) -> str | None:
    """Extract the chosen task_id from a task-selection response.

    Args:
        response:       Raw LLM response text.
        valid_task_ids: List of task IDs that were presented to the model.

    Returns:
        The selected task_id, or ``None`` if the model declined or
        the response could not be parsed.
    """
    text = response.strip()

    if text.upper() == "NONE":
        return None

    # Exact match against known IDs (model might return just the ID)
    for tid in valid_task_ids:
        if tid in text:
            return tid

    logger.warning("Could not parse task selection from LLM response: %s", text[:200])
    return None


def parse_bid_amount(response: str, max_reward: int) -> int | None:
    """Extract a bid amount (integer) from the LLM response.

    Args:
        response:   Raw LLM response text.
        max_reward: Maximum valid bid (the task reward).

    Returns:
        The bid amount, or ``None`` if unparseable or out of range.
    """
    text = response.strip()

    # Try to find a bare integer
    match = re.search(r"\b(\d+)\b", text)
    if match is None:
        logger.warning("Could not parse bid amount from LLM response: %s", text[:200])
        return None

    amount = int(match.group(1))

    if amount < 1 or amount > max_reward:
        logger.warning("Bid amount %d out of range [1, %d]", amount, max_reward)
        return None

    return amount


def parse_solution(response: str) -> str | None:
    """Extract the final answer from a solve-problem response.

    Looks for a line starting with ``ANSWER:`` (case-insensitive).
    Falls back to the last non-empty line.

    Args:
        response: Raw LLM response text.

    Returns:
        The extracted answer string, or ``None`` if empty.
    """
    text = response.strip()
    if not text:
        return None

    # Look for explicit ANSWER: marker
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        match = re.match(r"(?i)^ANSWER:\s*(.+)$", stripped)
        if match:
            return match.group(1).strip()

    # Fallback: last non-empty line
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            logger.debug("No ANSWER: marker found, using last line: %s", stripped)
            return stripped

    return None
