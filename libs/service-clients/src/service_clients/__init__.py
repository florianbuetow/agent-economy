"""Shared HTTP client library for inter-service communication."""

from service_clients.bank import BankClient
from service_clients.base import BaseServiceClient
from service_clients.gateway import GatewayClient
from service_clients.identity import IdentityClient

__version__ = "0.1.0"

# BankClient has zero importers today, same as the CourtClient/ReputationClient/
# TaskBoardClient siblings deleted alongside it (GAP-E3, WP-04 item 6d) — but,
# unlike those three (superseded by service_auth.PlatformAgent, which is what
# Court actually uses to call Task Board/Reputation), BankClient is explicitly
# earmarked in the plan as what task-board's WP-05 client swap adopts in place
# of its hand-rolled 439-line CentralBankClient. Kept and documented, not deleted.
__all__ = [
    "BankClient",
    "BaseServiceClient",
    "GatewayClient",
    "IdentityClient",
]
