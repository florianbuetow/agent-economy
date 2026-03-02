"""Backward-compatibility shim for legacy GatewayAgentStore imports."""

from identity_service.services.agent_db_client import AgentDbClient as GatewayAgentStore

__all__ = ["GatewayAgentStore"]
