"""Ed25519 key management and JWS token creation for demo agents.

Signing and canonicalization come from the shared ``service_auth`` library
(WP-02) so demo tokens are byte-compatible with every other signer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from service_auth.signing import KeyMaterialError, create_jws, load_keypair, public_key_to_b64
from service_auth.mixins.task_board import TaskBoardMixin

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class _TaskBoardConfig:
    task_board_url: str


@dataclass
class DemoAgent(TaskBoardMixin):
    """In-memory agent with keypair, used during demo replay."""

    handle: str
    name: str
    private_key: Ed25519PrivateKey = field(repr=False)
    public_key: Ed25519PublicKey = field(repr=False)
    token_ttl_seconds: int
    agent_id: str | None = None
    _task_board_config: _TaskBoardConfig | None = field(init=False, default=None, repr=False)
    _http: httpx.AsyncClient | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.token_ttl_seconds, bool)
            or not isinstance(self.token_ttl_seconds, int)
            or self.token_ttl_seconds <= 0
        ):
            msg = "token_ttl_seconds must be a positive integer"
            raise ValueError(msg)

    @classmethod
    def create(cls, handle: str, name: str, token_ttl_seconds: int) -> DemoAgent:
        """Create a new demo agent with a fresh keypair."""
        private_key = Ed25519PrivateKey.generate()
        return cls(
            handle=handle,
            name=name,
            private_key=private_key,
            public_key=private_key.public_key(),
            token_ttl_seconds=token_ttl_seconds,
        )

    @classmethod
    def from_pem(
        cls, handle: str, name: str, private_key_path: Path, token_ttl_seconds: int
    ) -> DemoAgent:
        """Load an explicitly provisioned matching PEM pair without changing it."""
        if private_key_path.name != f"{handle}.key":
            msg = f"Configured key path must name {handle}.key and its matching .pub pair"
            raise KeyMaterialError("unsafe_path", msg)
        private_key, public_key = load_keypair(handle, private_key_path.parent)
        return cls(
            handle=handle,
            name=name,
            private_key=private_key,
            public_key=public_key,
            token_ttl_seconds=token_ttl_seconds,
        )

    def public_key_string(self) -> str:
        """Return 'ed25519:<base64>' format expected by Identity service."""
        return f"ed25519:{public_key_to_b64(self.public_key)}"

    @property
    def config(self) -> _TaskBoardConfig:
        if self._task_board_config is None:
            raise RuntimeError("Demo agent transport is not configured")
        return self._task_board_config

    def configure_task_board(self, http: httpx.AsyncClient, task_board_url: str) -> None:
        """Bind the shared replay transport used by the typed Task Board SDK."""
        if not task_board_url:
            raise ValueError("task_board_url must be a nonempty string")
        self._http = http
        self._task_board_config = _TaskBoardConfig(task_board_url=task_board_url)

    def _sign_jws(self, payload: dict[str, object], *, audience: str, resource: str) -> str:
        return self.sign_jws(payload, audience=audience, resource=resource)

    def _auth_header(
        self, payload: dict[str, object], *, audience: str, resource: str
    ) -> dict[str, str]:
        return self.auth_header(payload, audience=audience, resource=resource)

    async def _request_raw(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._http is None:
            raise RuntimeError("Demo agent transport is not configured")
        return await self._http.request(method, url, **kwargs)

    def sign_jws(self, payload: dict[str, object], *, audience: str, resource: str) -> str:
        """Sign a payload as a compact JWS token."""
        if self.agent_id is None:
            msg = f"Agent '{self.handle}' must be registered before signing"
            raise RuntimeError(msg)
        return create_jws(
            payload,
            self.private_key,
            kid=self.agent_id,
            audience=audience,
            resource=resource,
            token_ttl_seconds=self.token_ttl_seconds,
        )

    def auth_header(
        self, payload: dict[str, object], *, audience: str, resource: str
    ) -> dict[str, str]:
        """Create an Authorization: Bearer header with a signed JWS."""
        token = self.sign_jws(payload, audience=audience, resource=resource)
        return {"Authorization": f"Bearer {token}"}
