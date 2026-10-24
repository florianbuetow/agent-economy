"""Capability-bound signing and verification for Database Gateway requests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from cryptography.exceptions import InvalidSignature
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from service_auth.jws import (
    ForbiddenError,
    InvalidPayloadError,
    TokenPolicy,
    VerifiedJws,
    create_jws,
    require_context,
    verify_jws,
)
from service_auth.signing import load_keypair

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

__all__ = [
    "GatewayRequestSigner",
    "GatewaySignerConfig",
    "GatewayTrustConfig",
    "GatewayTrustedCallerConfig",
    "verify_gateway_request",
]

_PAYLOAD_FIELDS = {"action", "body_sha256", "query_sha256"}
_GATEWAY_CAPABILITIES = {
    "gateway.identity.read",
    "gateway.identity.write",
    "gateway.bank.read",
    "gateway.bank.write",
    "gateway.board.read",
    "gateway.board.write",
    "gateway.board.task.read",
    "gateway.reputation.read",
    "gateway.reputation.write",
    "gateway.court.read",
    "gateway.court.write",
}


def _required_nonblank(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        msg = f"{field} must be a non-blank string"
        raise ValueError(msg)
    return value


class GatewaySignerConfig(BaseModel):
    """Required configuration for one service-to-Gateway capability signer."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kid: str
    private_key_path: Path
    audience: str
    token_ttl_seconds: int

    @field_validator("private_key_path", mode="before")
    @classmethod
    def parse_private_key_path(cls, value: object) -> Path:
        """Accept YAML path strings without enabling broad scalar coercion."""
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            return Path(value)
        msg = "private_key_path must be a path string"
        raise ValueError(msg)

    @model_validator(mode="after")
    def validate_signer(self) -> GatewaySignerConfig:
        """Validate canonical identity, key path, audience, and lifetime."""
        _required_nonblank(self.kid, field="kid")
        _required_nonblank(self.audience, field="audience")
        if self.private_key_path.suffix != ".key" or self.private_key_path.stem != self.kid:
            msg = "private_key_path must use the canonical '<kid>.key' filename"
            raise ValueError(msg)
        if self.token_ttl_seconds <= 0:
            msg = "token_ttl_seconds must be a positive integer"
            raise ValueError(msg)
        return self


class GatewayTrustedCallerConfig(BaseModel):
    """Public key and least-privilege grants for one Gateway caller."""

    model_config = ConfigDict(extra="forbid", strict=True)
    public_key_path: Path
    capabilities: list[str]

    @field_validator("public_key_path", mode="before")
    @classmethod
    def parse_public_key_path(cls, value: object) -> Path:
        """Accept YAML path strings without enabling broad scalar coercion."""
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            return Path(value)
        msg = "public_key_path must be a path string"
        raise ValueError(msg)

    @field_validator("public_key_path")
    @classmethod
    def validate_public_key_path(cls, value: Path) -> Path:
        """Require an explicit canonical public-key path."""
        if not str(value).strip() or value.suffix != ".pub":
            msg = "public_key_path must be a non-blank canonical .pub path"
            raise ValueError(msg)
        return value

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        """Reject empty, duplicate, or unknown capability grants."""
        if not value or len(value) != len(set(value)):
            msg = "capabilities must be a non-empty list without duplicates"
            raise ValueError(msg)
        if any(capability not in _GATEWAY_CAPABILITIES for capability in value):
            msg = "capabilities contains an unknown Gateway capability"
            raise ValueError(msg)
        return value


class GatewayTrustConfig(BaseModel):
    """Receiver-owned Gateway authentication and authorization policy."""

    model_config = ConfigDict(extra="forbid", strict=True)
    audience: str
    max_lifetime_seconds: int
    clock_skew_seconds: int
    trusted_callers: dict[str, GatewayTrustedCallerConfig]

    @model_validator(mode="after")
    def validate_auth_policy(self) -> GatewayTrustConfig:
        """Validate required identities, time policy, and canonical key names."""
        _required_nonblank(self.audience, field="audience")
        TokenPolicy(
            max_lifetime_seconds=self.max_lifetime_seconds,
            clock_skew_seconds=self.clock_skew_seconds,
        )
        if not self.trusted_callers:
            msg = "trusted_callers must not be empty"
            raise ValueError(msg)
        for kid, caller in self.trusted_callers.items():
            _required_nonblank(kid, field="trusted caller KID")
            if caller.public_key_path.stem != kid:
                msg = "trusted caller public key filename stem must match its KID"
                raise ValueError(msg)
        return self


class GatewayRequestSigner:
    """Sign a fully built HTTPX request with exact body/query digests."""

    def __init__(self, config: GatewaySignerConfig, *, config_dir: Path) -> None:
        if not isinstance(config, GatewaySignerConfig):
            msg = "config must be a GatewaySignerConfig"
            raise ValueError(msg)
        if not isinstance(config_dir, Path):
            msg = "config_dir must be a Path"
            raise ValueError(msg)
        key_path = config.private_key_path
        if not key_path.is_absolute():
            key_path = config_dir / key_path
        self._config = config
        self._private_key, _public_key = load_keypair(key_path.stem, key_path.parent)

    def sign_request(self, request: httpx.Request, *, action: str) -> httpx.Request:
        """Attach exactly one Bearer token after the request body is materialized."""
        action = _required_nonblank(action, field="action")
        if request.headers.get_list("authorization"):
            msg = "request already contains an Authorization header"
            raise ValueError(msg)
        try:
            body = request.content
        except httpx.RequestNotRead as exc:
            msg = "request body must be read before signing"
            raise ValueError(msg) from exc

        raw_path = request.url.raw_path.split(b"?", maxsplit=1)[0]
        try:
            path = raw_path.decode("ascii")
        except UnicodeDecodeError as exc:
            msg = "request path must have an ASCII wire representation"
            raise ValueError(msg) from exc
        query = request.url.query
        token = create_jws(
            {
                "action": action,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "query_sha256": hashlib.sha256(query).hexdigest(),
            },
            self._private_key,
            kid=self._config.kid,
            audience=self._config.audience,
            resource=f"{request.method.upper()} {path}",
            token_ttl_seconds=self._config.token_ttl_seconds,
        )
        request.headers["Authorization"] = f"Bearer {token}"
        return request


def verify_gateway_request(
    token: str,
    public_key: Ed25519PublicKey,
    *,
    policy: TokenPolicy,
    audience: str,
    method: str,
    path: str,
    body: bytes,
    query: bytes,
    allowed_actions: str | tuple[str, ...],
) -> VerifiedJws:
    """Authenticate and bind a Gateway request to its complete wire context."""
    try:
        verified = verify_jws(token, public_key, policy=policy)
    except InvalidSignature as exc:
        msg = "Gateway signature is invalid"
        raise ForbiddenError(msg) from exc
    if set(verified.payload) != _PAYLOAD_FIELDS:
        msg = "Gateway JWS payload must contain exactly the required fields"
        raise InvalidPayloadError(msg)
    expected_body_digest = hashlib.sha256(body).hexdigest()
    expected_query_digest = hashlib.sha256(query).hexdigest()
    if (
        verified.payload.get("body_sha256") != expected_body_digest
        or verified.payload.get("query_sha256") != expected_query_digest
    ):
        msg = "Authenticated Gateway request digests do not match the received bytes"
        raise InvalidPayloadError(msg)
    return require_context(
        verified,
        audience=audience,
        resource=f"{method.upper()} {path}",
        allowed_action=allowed_actions,
    )
