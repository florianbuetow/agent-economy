"""UserAgent — UI-driven agent for human task lifecycle operations."""

from __future__ import annotations

from service_auth.agent import BaseAgent


class UserAgent(BaseAgent):
    """Agent used by the UI service for human-driven task lifecycle operations.

    A distinct economic identity from the platform agent (Q-2): it has its
    own keypair, agent_id, and bank account, and does not inherit
    PlatformAgent's privileged operations (create_account, credit_account,
    escrow release/split) — those stay reserved for notary operations
    performed by the platform agent. Provides the same task-board, bank, and
    reputation methods as any other agent.
    """

    def __repr__(self) -> str:
        registered = f", agent_id={self.agent_id!r}" if self.agent_id else ""
        return f"UserAgent(name={self.name!r}{registered})"
