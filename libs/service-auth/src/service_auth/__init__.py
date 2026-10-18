"""Service Auth — Ed25519 PKI, JWS signing/verification, and platform agents."""

from service_auth.agent import BaseAgent
from service_auth.config import AgentConfig, load_agent_config
from service_auth.factory import AgentFactory
from service_auth.jws import (
    ForbiddenError,
    InvalidJwsError,
    InvalidPayloadError,
    JwsAdmissionError,
    JwsProtectedHeader,
    ParsedJws,
    TokenNotYetValidError,
    TokenPolicy,
    UpstreamContractError,
    VerifiedJws,
    parse_identity_verification_response,
    parse_jws,
    require_context,
    require_signer,
)
from service_auth.platform import PlatformAgent
from service_auth.signing import (
    KeyMaterialError,
    KeypairSnapshot,
    PlatformSigner,
    TokenExpiredError,
    adopt_keypair_permissions,
    create_jws,
    generate_keypair,
    load_keypair,
    load_private_key,
    load_public_key,
    public_key_to_b64,
    snapshot_keypairs,
    verify_jws,
)
from service_auth.task_board_responses import (
    TaskBoardCommandContractError,
    TaskBoardCommandResponse,
)
from service_auth.user_agent import UserAgent

__version__ = "0.1.0"

__all__ = [
    "AgentConfig",
    "AgentFactory",
    "BaseAgent",
    "ForbiddenError",
    "InvalidJwsError",
    "InvalidPayloadError",
    "JwsAdmissionError",
    "JwsProtectedHeader",
    "KeyMaterialError",
    "KeypairSnapshot",
    "ParsedJws",
    "PlatformAgent",
    "PlatformSigner",
    "TaskBoardCommandContractError",
    "TaskBoardCommandResponse",
    "TokenExpiredError",
    "TokenNotYetValidError",
    "TokenPolicy",
    "UpstreamContractError",
    "UserAgent",
    "VerifiedJws",
    "adopt_keypair_permissions",
    "create_jws",
    "generate_keypair",
    "load_agent_config",
    "load_keypair",
    "load_private_key",
    "load_public_key",
    "parse_identity_verification_response",
    "parse_jws",
    "public_key_to_b64",
    "require_context",
    "require_signer",
    "snapshot_keypairs",
    "verify_jws",
]
