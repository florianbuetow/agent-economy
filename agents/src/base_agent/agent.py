"""
BaseAgent — programmable client for the Agent Task Economy platform.

Composes service-specific mixins for Identity, Central Bank, Task Board,
Reputation, and Court services. All cross-cutting concerns (signing, HTTP,
config) live here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import yaml

from base_agent.mixins import (
    BankMixin,
    CourtMixin,
    IdentityMixin,
    ReputationMixin,
    TaskBoardMixin,
)
from base_agent.signing import (
    create_jws,
    generate_keypair,
    load_private_key,
    load_public_key,
    public_key_to_b64,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    from base_agent.config import Settings


class BaseAgent(IdentityMixin, BankMixin, TaskBoardMixin, ReputationMixin, CourtMixin):
    """Programmable client for the Agent Task Economy platform.

    Holds agent identity (keypair, handle, name) and provides HTTP + signing
    internals used by all service mixins. Methods on mixins are dual-use:
    callable directly from Python and usable as Strands @tool functions.

    Usage::

        config = get_settings()
        agent = BaseAgent(handle="alice", config=config)
        await agent.register()
        tasks = await agent.list_tasks(status="open")
    """

    def __init__(self, handle: str, config: Settings) -> None:
        """Initialize the agent with a handle and configuration.

        Loads the keypair from disk (or generates one if missing) and reads
        the agent's name from the roster file.

        Args:
            handle: Agent handle from roster (e.g. "alice").
                    Maps to key files: {keys_dir}/alice.key, {keys_dir}/alice.pub
            config: Loaded Settings object with platform URLs and data paths.
        """
        self.handle = handle
        self.config = config
        self.agent_id: str | None = None

        # Resolve paths relative to config file directory
        config_dir = Path(config.data.keys_dir)
        if not config_dir.is_absolute():
            config_dir = Path.cwd() / config_dir
        self._keys_dir = config_dir.resolve()

        # Load roster
        roster_path = Path(config.data.roster_path)
        if not roster_path.is_absolute():
            roster_path = Path.cwd() / roster_path
        roster = self._load_roster(roster_path.resolve())
        self.name: str = roster["agents"][handle]["name"]
        self.agent_type: str = roster["agents"][handle]["type"]

        # Load or generate keypair
        self._private_key, self._public_key = self._load_or_generate_keys()

        # HTTP client
        self._http = httpx.AsyncClient()

    @staticmethod
    def _load_roster(roster_path: Path) -> dict[str, Any]:
        """Load the agent roster from a YAML file.

        Args:
            roster_path: Absolute path to roster.yaml.

        Returns:
            Parsed roster dictionary.

        Raises:
            FileNotFoundError: If roster file does not exist.
            ValueError: If roster file is empty or invalid.
        """
        if not roster_path.exists():
            msg = f"Roster file not found: {roster_path}"
            raise FileNotFoundError(msg)

        with roster_path.open() as f:
            roster = yaml.safe_load(f)

        if not isinstance(roster, dict) or "agents" not in roster:
            msg = f"Invalid roster file: {roster_path} — must contain 'agents' key"
            raise ValueError(msg)

        return roster

    def _load_or_generate_keys(self) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
        """Load keypair from disk, or generate if missing.

        Returns:
            Tuple of (private_key, public_key).
        """
        private_path = self._keys_dir / f"{self.handle}.key"
        public_path = self._keys_dir / f"{self.handle}.pub"

        if private_path.exists() and public_path.exists():
            return load_private_key(private_path), load_public_key(public_path)

        return generate_keypair(self.handle, self._keys_dir)

    def get_public_key_b64(self) -> str:
        """Return the public key as a base64-encoded string.

        Returns:
            Base64 string of the raw 32-byte public key.
        """
        return public_key_to_b64(self._public_key)

    def _sign_jws(self, payload: dict[str, object]) -> str:
        """Create a JWS token signed with this agent's private key.

        Args:
            payload: Dictionary to encode and sign.

        Returns:
            Compact JWS string (header.payload.signature).
        """
        return create_jws(payload, self._private_key)

    def _auth_header(self, payload: dict[str, object]) -> dict[str, str]:
        """Create an Authorization header with a signed JWS token.

        Args:
            payload: Dictionary to encode and sign.

        Returns:
            Dictionary with 'Authorization' key containing 'Bearer <JWS>'.
        """
        token = self._sign_jws(payload)
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an HTTP request with consistent error handling.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Full URL to request.
            **kwargs: Additional arguments passed to httpx.AsyncClient.request().

        Returns:
            Parsed JSON response as a dictionary.

        Raises:
            httpx.HTTPStatusError: If the response status indicates an error.
        """
        response = await self._http.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def _request_raw(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request and return the raw response.

        Does NOT raise on error status codes — the caller decides how to handle.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Full URL to request.
            **kwargs: Additional arguments passed to httpx.AsyncClient.request().

        Returns:
            The raw httpx.Response object.
        """
        return await self._http.request(method, url, **kwargs)

    def get_tools(self) -> list[Any]:
        """Return all @tool-decorated methods for use with Strands Agent.

        Returns:
            List of tool-decorated methods. Empty list if Strands is not installed.
        """
        tools: list[Any] = []
        for attr_name in dir(self):
            attr = getattr(self, attr_name, None)
            if callable(attr) and hasattr(attr, "tool_definition"):
                tools.append(attr)
        return tools

    async def close(self) -> None:
        """Close the HTTP client. Call this when done using the agent."""
        await self._http.aclose()

    def __repr__(self) -> str:
        registered = f", agent_id={self.agent_id!r}" if self.agent_id else ""
        return f"BaseAgent(handle={self.handle!r}, name={self.name!r}{registered})"
