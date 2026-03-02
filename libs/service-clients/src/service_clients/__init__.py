"""Shared HTTP client library for inter-service communication."""

from service_clients.bank import BankClient
from service_clients.base import BaseServiceClient
from service_clients.court import CourtClient
from service_clients.gateway import GatewayClient
from service_clients.identity import IdentityClient
from service_clients.reputation import ReputationClient
from service_clients.task_board import TaskBoardClient

__version__ = "0.1.0"

__all__ = [
    "BankClient",
    "BaseServiceClient",
    "CourtClient",
    "GatewayClient",
    "IdentityClient",
    "ReputationClient",
    "TaskBoardClient",
]
