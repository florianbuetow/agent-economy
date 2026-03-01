"""Demo signer service for the human agent identity.

Generates an Ed25519 keypair, registers with the Identity service,
creates a bank account, and signs JWS tokens for task operations.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import TYPE_CHECKING, Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from observatory_service.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode bytes without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _create_jws(
    payload: dict[str, object],
    private_key: Ed25519PrivateKey,
    kid: str,
) -> str:
    """Create a compact JWS token (header.payload.signature) using EdDSA.

    Produces a three-part dot-separated string:
    base64url(header).base64url(payload).base64url(signature).

    Args:
        payload: Dictionary to sign as the JWS payload.
        private_key: Ed25519 private key used for signing.
        kid: Key ID (agent_id) to include in the JWS header.

    Returns:
        Compact JWS string.
    """
    header: dict[str, str] = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input)
    signature_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


def _public_key_to_b64(private_key: Ed25519PrivateKey) -> str:
    """Export public key as base64-encoded raw bytes.

    Args:
        private_key: The Ed25519 private key whose public key to export.

    Returns:
        Base64-encoded string of the raw 32-byte public key.
    """
    raw_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw_bytes).decode("ascii")


class DemoSigner:
    """Signs JWS tokens for the demo human agent and the platform."""

    def __init__(
        self,
        human_private_key: Ed25519PrivateKey,
        human_agent_id: str,
        platform_private_key: Ed25519PrivateKey,
        platform_agent_id: str,
    ) -> None:
        self._human_private_key = human_private_key
        self._human_agent_id = human_agent_id
        self._platform_private_key = platform_private_key
        self._platform_agent_id = platform_agent_id

    @property
    def human_agent_id(self) -> str:
        """Return the human agent's ID."""
        return self._human_agent_id

    def sign_create_task(
        self,
        title: str,
        spec: str,
        reward: int,
    ) -> dict[str, str]:
        """Sign tokens for creating a new task.

        Args:
            title: Task title.
            spec: Task specification text.
            reward: Reward amount in credits.

        Returns:
            Dictionary with task_id, task_token, and escrow_token.
        """
        task_id = f"t-{uuid.uuid4()}"

        task_payload: dict[str, object] = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": self._human_agent_id,
            "title": title,
            "spec": spec,
            "reward": reward,
            "bidding_deadline_seconds": 300,
            "execution_deadline_seconds": 600,
            "review_deadline_seconds": 300,
        }
        task_token = _create_jws(task_payload, self._human_private_key, self._human_agent_id)

        escrow_payload: dict[str, object] = {
            "action": "escrow_lock",
            "task_id": task_id,
            "amount": reward,
            "agent_id": self._human_agent_id,
        }
        escrow_token = _create_jws(escrow_payload, self._human_private_key, self._human_agent_id)

        return {
            "task_id": task_id,
            "task_token": task_token,
            "escrow_token": escrow_token,
        }

    def sign_accept_bid(self, task_id: str, bid_id: str) -> str:
        """Sign a token for accepting a bid.

        Args:
            task_id: The task to accept a bid on.
            bid_id: The bid to accept.

        Returns:
            Compact JWS string.
        """
        payload: dict[str, object] = {
            "action": "accept_bid",
            "task_id": task_id,
            "bid_id": bid_id,
            "poster_id": self._human_agent_id,
        }
        return _create_jws(payload, self._human_private_key, self._human_agent_id)

    def sign_dispute(self, task_id: str, reason: str) -> str:
        """Sign a token for filing a dispute.

        Args:
            task_id: The task to dispute.
            reason: Reason for the dispute.

        Returns:
            Compact JWS string.
        """
        payload: dict[str, object] = {
            "action": "dispute_task",
            "task_id": task_id,
            "poster_id": self._human_agent_id,
            "reason": reason,
        }
        return _create_jws(payload, self._human_private_key, self._human_agent_id)

    def sign_platform_create_account(self, agent_id: str, initial_balance: int) -> str:
        """Sign a platform token for creating a bank account.

        Args:
            agent_id: The agent to create an account for.
            initial_balance: Initial balance in credits.

        Returns:
            Compact JWS string signed with the platform key.
        """
        payload: dict[str, object] = {
            "action": "create_account",
            "agent_id": agent_id,
            "initial_balance": initial_balance,
        }
        return _create_jws(payload, self._platform_private_key, self._platform_agent_id)


def _load_or_generate_human_key(keys_dir: Path) -> Ed25519PrivateKey:
    """Load the human key from disk, or generate and save a new one.

    Args:
        keys_dir: Directory to read/write the key file.

    Returns:
        The Ed25519 private key.
    """
    key_path = keys_dir / "human.key"
    if key_path.exists():
        logger.info("loading_human_key", extra={"path": str(key_path)})
        raw = key_path.read_bytes()
        loaded = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            msg = f"Expected Ed25519 private key, got {type(loaded).__name__}"
            raise TypeError(msg)
        return loaded

    logger.info("generating_human_key", extra={"path": str(key_path)})
    keys_dir.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return private_key


def _load_platform_key(platform_key_path: Path) -> Ed25519PrivateKey:
    """Load the platform private key from a PEM file.

    Args:
        platform_key_path: Path to the platform key PEM file.

    Returns:
        The Ed25519 private key.

    Raises:
        FileNotFoundError: If the key file does not exist.
        TypeError: If the key is not Ed25519.
    """
    raw = platform_key_path.read_bytes()
    loaded = serialization.load_pem_private_key(raw, password=None)
    if not isinstance(loaded, Ed25519PrivateKey):
        msg = f"Expected Ed25519 private key, got {type(loaded).__name__}"
        raise TypeError(msg)
    return loaded


async def _register_human_agent(
    client: httpx.AsyncClient,
    identity_url: str,
    human_agent_name: str,
    public_key_b64: str,
) -> str:
    """Register the human agent with the Identity service.

    Args:
        client: HTTP client to use.
        identity_url: Base URL of the Identity service.
        human_agent_name: Display name for the human agent.
        public_key_b64: Base64-encoded public key.

    Returns:
        The agent_id assigned by the Identity service.
    """
    register_url = f"{identity_url}/agents/register"
    body = {
        "name": human_agent_name,
        "public_key": f"ed25519:{public_key_b64}",
    }

    logger.info("registering_human_agent", extra={"name": human_agent_name, "url": register_url})
    resp = await client.post(register_url, json=body)

    if resp.status_code == 201:
        data: dict[str, Any] = resp.json()
        agent_id: str = data["agent_id"]
        logger.info("human_agent_registered", extra={"agent_id": agent_id})
        return agent_id

    if resp.status_code == 409:
        logger.info(
            "human_agent_already_registered",
            extra={"name": human_agent_name},
        )
        list_url = f"{identity_url}/agents"
        list_resp = await client.get(list_url)
        list_resp.raise_for_status()
        agents: list[dict[str, Any]] = list_resp.json()
        for agent in agents:
            if agent.get("name") == human_agent_name:
                found_id: str = agent["agent_id"]
                logger.info("human_agent_found", extra={"agent_id": found_id})
                return found_id
        msg = f"Agent '{human_agent_name}' reported as 409 but not found in agent list"
        raise RuntimeError(msg)

    resp.raise_for_status()
    msg = f"Unexpected status {resp.status_code} from identity register"
    raise RuntimeError(msg)


async def _create_bank_account(
    client: httpx.AsyncClient,
    central_bank_url: str,
    signer: DemoSigner,
    agent_id: str,
    initial_balance: int,
) -> None:
    """Create a bank account for the agent via the Central Bank.

    Args:
        client: HTTP client to use.
        central_bank_url: Base URL of the Central Bank service.
        signer: DemoSigner instance for creating platform-signed tokens.
        agent_id: The agent to create an account for.
        initial_balance: Initial balance in credits.
    """
    account_url = f"{central_bank_url}/accounts"
    token = signer.sign_platform_create_account(agent_id, initial_balance)

    logger.info(
        "creating_bank_account",
        extra={"agent_id": agent_id, "initial_balance": initial_balance},
    )
    resp = await client.post(account_url, json={"token": token})

    if resp.status_code == 201:
        logger.info("bank_account_created", extra={"agent_id": agent_id})
        return

    if resp.status_code == 409:
        logger.info("bank_account_already_exists", extra={"agent_id": agent_id})
        return

    resp.raise_for_status()


async def bootstrap_demo_agent(
    identity_url: str,
    central_bank_url: str,
    platform_key_path: Path,
    keys_dir: Path,
    human_agent_name: str,
    human_initial_balance: int,
    timeout_seconds: int,
) -> DemoSigner:
    """Bootstrap the demo human agent: generate keys, register, create account.

    Args:
        identity_url: Base URL of the Identity service.
        central_bank_url: Base URL of the Central Bank service.
        platform_key_path: Path to the platform private key PEM file.
        keys_dir: Directory for storing/loading the human key.
        human_agent_name: Display name for the human agent.
        human_initial_balance: Initial bank balance in credits.
        timeout_seconds: HTTP request timeout in seconds.

    Returns:
        A configured DemoSigner instance.
    """
    human_key = _load_or_generate_human_key(keys_dir)
    platform_key = _load_platform_key(platform_key_path)

    human_pub_b64 = _public_key_to_b64(human_key)
    platform_pub_b64 = _public_key_to_b64(platform_key)

    timeout = httpx.Timeout(timeout=float(timeout_seconds))
    async with httpx.AsyncClient(timeout=timeout) as client:
        human_agent_id = await _register_human_agent(
            client,
            identity_url,
            human_agent_name,
            human_pub_b64,
        )

        # Resolve platform agent_id by listing agents and matching the public key
        platform_agent_id = await _resolve_platform_agent_id(
            client,
            identity_url,
            platform_pub_b64,
        )

        signer = DemoSigner(
            human_private_key=human_key,
            human_agent_id=human_agent_id,
            platform_private_key=platform_key,
            platform_agent_id=platform_agent_id,
        )

        await _create_bank_account(
            client,
            central_bank_url,
            signer,
            human_agent_id,
            human_initial_balance,
        )

    logger.info(
        "demo_agent_bootstrapped",
        extra={"agent_id": human_agent_id, "name": human_agent_name},
    )
    return signer


async def _resolve_platform_agent_id(
    client: httpx.AsyncClient,
    identity_url: str,
    platform_pub_b64: str,
) -> str:
    """Resolve the platform agent_id by matching the public key in the agent list.

    Args:
        client: HTTP client to use.
        identity_url: Base URL of the Identity service.
        platform_pub_b64: Base64-encoded platform public key.

    Returns:
        The platform agent_id.

    Raises:
        RuntimeError: If the platform agent cannot be found.
    """
    list_url = f"{identity_url}/agents"
    resp = await client.get(list_url)
    resp.raise_for_status()
    agents: list[dict[str, Any]] = resp.json()
    expected_key = f"ed25519:{platform_pub_b64}"
    for agent in agents:
        if agent.get("public_key") == expected_key:
            found_id: str = agent["agent_id"]
            logger.info("platform_agent_found", extra={"agent_id": found_id})
            return found_id
    msg = "Platform agent not found in identity service"
    raise RuntimeError(msg)
