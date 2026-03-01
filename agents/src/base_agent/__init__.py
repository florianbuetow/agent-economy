"""Base Agent — programmable client for the Agent Task Economy platform."""

from base_agent.agent import BaseAgent
from base_agent.factory import AgentFactory
from base_agent.platform import PlatformAgent

__version__ = "0.1.0"

__all__ = ["BaseAgent", "AgentFactory", "PlatformAgent"]
