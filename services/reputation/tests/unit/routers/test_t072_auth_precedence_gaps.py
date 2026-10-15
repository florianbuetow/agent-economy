"""T-072 coverage sweep: closes two auth-spec gaps found against
docs/specifications/service-tests/reputation-service-auth-tests.md.

- AUTH-08b: an expired JWS token must be rejected as 403 forbidden, not a
  distinct error code, mirroring the real ``TokenExpiredError`` -> ``valid:
  False`` mapping in ``PlatformJwsVerifier``.
- PREC-05: verification failure must be checked before the missing-``kid``
  check, even for a token whose header omits ``kid`` entirely.

Both scenarios were previously untested: ``test_feedback_auth.py`` covers
AUTH-01..AUTH-14 but has no expired-token case, and its "PREC-05" test
(despite the label) actually exercises what the current spec calls PREC-06.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import signing
from service_auth.config import AgentConfig
from service_auth.platform import PlatformAgent
from service_auth.signing import create_jws

from reputation_service.core.state import get_app_state
from tests.unit.routers.conftest import inject_mock_identity

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient

PLATFORM_ID = "a-platform-t072"


def _real_platform_agent() -> tuple[PlatformAgent, Ed25519PrivateKey]:
    """Build a real PlatformAgent with a real Ed25519 keypair (no mocks)."""
    private_key = Ed25519PrivateKey.generate()
    config = AgentConfig(
        name="platform",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url="http://127.0.0.1:1",
        bank_url="http://localhost:8002",
        task_board_url="http://localhost:8003",
        reputation_url="http://localhost:8004",
        court_url="http://localhost:8005",
        token_ttl_seconds=60,
    )
    agent = PlatformAgent(config)
    agent.agent_id = PLATFORM_ID
    return agent, private_key


@pytest.fixture
def _reset_signing_clock() -> Iterator[None]:
    """Restore service_auth.signing's clock seam after a test overrides it."""
    original = signing._clock
    try:
        yield
    finally:
        signing._clock = original


def _kidless_token(payload: dict[str, object]) -> str:
    """Build a structurally valid JWS whose header has no ``kid`` claim."""
    header_b64 = (
        base64.urlsafe_b64encode(json.dumps({"alg": "EdDSA", "typ": "JWT"}).encode())
        .rstrip(b"=")
        .decode()
    )
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signature_b64 = base64.urlsafe_b64encode(b"fake-signature").rstrip(b"=").decode()
    return f"{header_b64}.{payload_b64}.{signature_b64}"


@pytest.mark.unit
@pytest.mark.usefixtures("_reset_signing_clock")
async def test_auth_08b_expired_token_is_rejected_as_forbidden(client: AsyncClient) -> None:
    """AUTH-08b: an expired JWS token returns 403 forbidden (not a distinct code).

    Uses a real PlatformAgent/Ed25519 keypair and the injectable
    ``service_auth.signing._clock`` seam: freeze the clock to sign a token
    with a 60s TTL, then advance it past ``exp`` before verification.
    """
    agent, private_key = _real_platform_agent()
    state = get_app_state()
    state.platform_agent = agent

    frozen_signing_time = datetime(2026, 1, 1, tzinfo=UTC)
    signing._clock = lambda: frozen_signing_time

    payload: dict[str, object] = {
        "action": "submit_feedback",
        "task_id": "t-auth08b",
        "from_agent_id": PLATFORM_ID,
        "to_agent_id": "a-worker",
        "category": "delivery_quality",
        "rating": "satisfied",
    }
    token = create_jws(payload, private_key, kid=PLATFORM_ID, token_ttl_seconds=60)

    # Advance the verification-time clock past the token's exp (iat + 60s).
    signing._clock = lambda: frozen_signing_time + timedelta(seconds=120)

    response = await client.post("/feedback", json={"token": token})

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.unit
async def test_prec_05_verification_failure_checked_before_missing_kid(
    client: AsyncClient,
) -> None:
    """PREC-05: verification runs before the missing-``kid`` check.

    A token with no ``kid`` at all AND a signature that fails verification
    must return 403 forbidden -- never 400 invalid_jws for the missing kid,
    and never 400 invalid_payload.
    """
    inject_mock_identity(verify_response={"valid": False})

    token = _kidless_token(
        {
            "action": "submit_feedback",
            "task_id": "t-prec05",
            "from_agent_id": "a-alice-uuid",
            "to_agent_id": "a-bob-uuid",
            "category": "delivery_quality",
            "rating": "satisfied",
        }
    )

    response = await client.post("/feedback", json={"token": token})

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
