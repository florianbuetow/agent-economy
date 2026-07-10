"""Service Auth — Ed25519 PKI, JWS signing/verification, and platform agents."""

from service_auth.agent import BaseAgent
from service_auth.config import AgentConfig, load_agent_config
from service_auth.factory import AgentFactory
from service_auth.platform import PlatformAgent
from service_auth.signing import (
    PlatformSigner,
    TokenExpiredError,
    create_jws,
    generate_keypair,
    load_private_key,
    load_public_key,
    public_key_to_b64,
    verify_jws,
)
from service_auth.user_agent import UserAgent

__version__ = "0.1.0"

__all__ = [
    "AgentConfig",
    "AgentFactory",
    "BaseAgent",
    "PlatformAgent",
    "PlatformSigner",
    "TokenExpiredError",
    "UserAgent",
    "create_jws",
    "generate_keypair",
    "load_agent_config",
    "load_private_key",
    "load_public_key",
    "public_key_to_b64",
    "verify_jws",
]
