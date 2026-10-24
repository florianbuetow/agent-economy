"""Single source of truth for the UI service's domain taxonomies.

This module centralises the event-type and task-status vocabularies that were
previously duplicated across the ``metrics``, ``tasks``, ``quarterly`` and
``agents`` service modules (and re-spelled as raw SQL literals in dozens of
queries). Each vocabulary, mapping and named grouping is declared exactly once
here and consumed everywhere else.

The backend event -> badge mapping (``EVENT_TYPE_TO_BADGE``) is the canonical
*backend* badge taxonomy. The frontend keeps its own, finer badge taxonomy in
``data/web/assets/shared.js``; the two layers legitimately differ and must not
be unified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable


class EventType:
    """Canonical ``events.event_type`` string constants."""

    AGENT_REGISTERED: Final = "agent.registered"
    SALARY_PAID: Final = "salary.paid"
    TASK_CREATED: Final = "task.created"
    BID_SUBMITTED: Final = "bid.submitted"
    TASK_ACCEPTED: Final = "task.accepted"
    ASSET_UPLOADED: Final = "asset.uploaded"
    TASK_SUBMITTED: Final = "task.submitted"
    TASK_APPROVED: Final = "task.approved"
    TASK_AUTO_APPROVED: Final = "task.auto_approved"
    TASK_DISPUTED: Final = "task.disputed"
    TASK_RULED: Final = "task.ruled"
    TASK_CANCELLED: Final = "task.cancelled"
    TASK_EXPIRED: Final = "task.expired"
    ESCROW_LOCKED: Final = "escrow.locked"
    ESCROW_RELEASED: Final = "escrow.released"
    ESCROW_SPLIT: Final = "escrow.split"
    FEEDBACK_REVEALED: Final = "feedback.revealed"


# Map event_type -> agent-feed badge category (backend badge taxonomy). This is
# the single declaration of the backend vocabulary previously held in
# ``agents._EVENT_TYPE_TO_BADGE``.
EVENT_TYPE_TO_BADGE: Final[dict[str, str]] = {
    EventType.AGENT_REGISTERED: "SYSTEM",
    EventType.SALARY_PAID: "SYSTEM",
    EventType.TASK_CREATED: "TASK",
    EventType.BID_SUBMITTED: "BID",
    EventType.TASK_ACCEPTED: "TASK",
    EventType.ASSET_UPLOADED: "TASK",
    EventType.TASK_SUBMITTED: "TASK",
    EventType.TASK_APPROVED: "PAYOUT",
    EventType.TASK_AUTO_APPROVED: "PAYOUT",
    EventType.TASK_DISPUTED: "TASK",
    EventType.TASK_RULED: "TASK",
    EventType.TASK_CANCELLED: "TASK",
    EventType.TASK_EXPIRED: "TASK",
    EventType.ESCROW_LOCKED: "ESCROW",
    EventType.ESCROW_RELEASED: "PAYOUT",
    EventType.ESCROW_SPLIT: "ESCROW",
    EventType.FEEDBACK_REVEALED: "REP",
}

# Badge used for any event_type absent from ``EVENT_TYPE_TO_BADGE``.
DEFAULT_EVENT_BADGE: Final = "SYSTEM"

# Event types included in the agent activity feed (per agents spec). Exactly the
# keys of ``EVENT_TYPE_TO_BADGE``.
AGENT_FEED_EVENT_TYPES: Final[frozenset[str]] = frozenset(EVENT_TYPE_TO_BADGE)

# --- Named event-type groups consumed by the sparkline GROUP BY queries ---
# Task completions (approved or auto-approved).
APPROVAL_EVENT_TYPES: Final[tuple[str, ...]] = (
    EventType.TASK_APPROVED,
    EventType.TASK_AUTO_APPROVED,
)
# All terminal task events (the completion-rate denominator).
TERMINAL_EVENT_TYPES: Final[tuple[str, ...]] = (
    EventType.TASK_APPROVED,
    EventType.TASK_AUTO_APPROVED,
    EventType.TASK_DISPUTED,
    EventType.TASK_CANCELLED,
    EventType.TASK_EXPIRED,
)
# Events that mean an agent stopped working on a task.
WORK_STOP_EVENT_TYPES: Final[tuple[str, ...]] = (
    EventType.TASK_APPROVED,
    EventType.TASK_AUTO_APPROVED,
    EventType.TASK_DISPUTED,
)


class TaskStatus:
    """Canonical ``board_tasks.status`` string constants."""

    OPEN: Final = "open"
    ACCEPTED: Final = "accepted"
    SUBMITTED: Final = "submitted"
    APPROVED: Final = "approved"
    DISPUTED: Final = "disputed"
    RULED: Final = "ruled"
    EXPIRED: Final = "expired"
    CANCELLED: Final = "cancelled"


VALID_TASK_STATUSES: Final[frozenset[str]] = frozenset(
    {
        TaskStatus.OPEN,
        TaskStatus.ACCEPTED,
        TaskStatus.SUBMITTED,
        TaskStatus.APPROVED,
        TaskStatus.DISPUTED,
        TaskStatus.RULED,
        TaskStatus.EXPIRED,
        TaskStatus.CANCELLED,
    }
)

# --- Named task-status groupings ---
# These are the derived semantics that were previously re-encoded as ad-hoc SQL
# ``IN (...)`` fragments. NOTE: individual call sites historically differ in how
# they combine these (e.g. completion-rate denominators); those differences are
# preserved at the call sites, this module only names the shared vocabularies.
#
# Tasks currently in execution (accepted or submitted).
IN_EXECUTION_STATUSES: Final[tuple[str, ...]] = (TaskStatus.ACCEPTED, TaskStatus.SUBMITTED)
# Tasks that went through (or are in) dispute (disputed or ruled).
DISPUTED_STATUSES: Final[tuple[str, ...]] = (TaskStatus.DISPUTED, TaskStatus.RULED)
# Statuses surfaced by the "open" competitive-tasks board view.
COMPETITIVE_OPEN_STATUSES: Final[tuple[str, ...]] = (TaskStatus.OPEN, TaskStatus.ACCEPTED)


def sql_placeholders(items: Iterable[object]) -> str:
    """Return a comma-separated run of SQL ``?`` placeholders for ``items``.

    Used to build parameterised ``IN (...)`` clauses from the named groups above
    without ever interpolating data into the SQL text.
    """
    return ", ".join("?" for _ in items)
