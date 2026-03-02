"""Base Agent — programmable client for the Agent Task Economy platform."""

from base_agent.agent import BaseAgent
from base_agent.factory import AgentFactory
from base_agent.platform import PlatformAgent
from base_agent.user_agent import UserAgent
from base_agent.worker_factory import WorkerFactory

__version__ = "0.1.0"

__all__ = ["AgentFactory", "BaseAgent", "PlatformAgent", "UserAgent", "WorkerFactory"]
