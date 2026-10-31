"""Service-specific mixin classes for BaseAgent."""

from service_auth.mixins.bank import BankMixin
from service_auth.mixins.court import CourtMixin
from service_auth.mixins.identity import IdentityMixin
from service_auth.mixins.reputation import ReputationMixin
from service_auth.mixins.task_board import TaskBoardMixin

__all__ = [
    "BankMixin",
    "CourtMixin",
    "IdentityMixin",
    "ReputationMixin",
    "TaskBoardMixin",
]
