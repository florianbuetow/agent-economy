"""Backward-compatibility shim for legacy GatewayDisputeStore imports."""

from court_service.services.dispute_db_client import DisputeDbClient as GatewayDisputeStore

__all__ = ["GatewayDisputeStore"]
