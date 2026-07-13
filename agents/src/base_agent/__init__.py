"""Base Agent — programmable client for the Agent Task Economy platform.

The PKI, agent, and platform-agent implementations moved to ``service_auth``
(``libs/service-auth``) in WP-02. This package re-exports them so existing
``base_agent.*`` imports keep working; ``WorkerFactory`` remains here because it
depends on the ``math_worker`` application package.
"""

from service_auth import AgentFactory, BaseAgent, PlatformAgent, UserAgent

from base_agent.worker_factory import WorkerFactory

__version__ = "0.1.0"

__all__ = ["AgentFactory", "BaseAgent", "PlatformAgent", "UserAgent", "WorkerFactory"]
