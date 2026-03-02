"""API routers."""

from db_gateway_service.routers import bank, board, court, health, identity, reputation

__all__ = ["bank", "board", "court", "health", "identity", "reputation"]
