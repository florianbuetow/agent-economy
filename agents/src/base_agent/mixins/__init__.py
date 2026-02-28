"""Service-specific mixin classes for BaseAgent."""

from base_agent.mixins.bank import BankMixin
from base_agent.mixins.court import CourtMixin
from base_agent.mixins.identity import IdentityMixin
from base_agent.mixins.reputation import ReputationMixin
from base_agent.mixins.task_board import TaskBoardMixin

__all__ = [
    "BankMixin",
    "CourtMixin",
    "IdentityMixin",
    "ReputationMixin",
    "TaskBoardMixin",
]
