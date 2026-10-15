"""T-072 coverage-sweep gap fixes needing REAL Ed25519 verification (not the
payload-decoding mock in conftest.py): non-platform signers on escrow release/split,
Identity unreachable during create_account, and platform token expiry.

Reuses the exact `identity_down` real-PlatformAgent fixture already established in
test_two_tier_auth.py (a real IdentityClient pointed at a dead port, and a real
PlatformAgent with a fresh Ed25519 keypair) — these scenarios need genuine signature
verification (a mocked `validate_certificate` that just decodes the payload, as used
by the standard `client` fixture, cannot detect a non-platform signer or an expired
token).
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.signing import create_jws

from .test_two_tier_auth import PLATFORM_ID, identity_down

__all__ = ["identity_down"]  # re-exported fixture, referenced only by name in tests


@pytest.mark.unit
async def test_non_platform_signer_on_release_is_forbidden(identity_down: Any) -> None:
    """AUTH-12 / REL-06: a non-platform signer on POST /escrow/{id}/release returns
    403 forbidden (real local Ed25519 verification against the platform's own key)."""
    client, state, _platform_private_key = identity_down
    await state.ledger.create_account("a-payer", 100)
    locked = await state.ledger.escrow_lock("a-payer", 40, "t-rel-06")
    escrow_id = locked["escrow_id"]

    non_platform_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {
            "action": "escrow_release",
            "escrow_id": escrow_id,
            "recipient_account_id": "a-payer",
        },
        non_platform_key,
        kid="a-eve",
    )
    resp = await client.post(f"/escrow/{escrow_id}/release", json={"token": token})

    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"


@pytest.mark.unit
async def test_non_platform_signer_on_split_is_forbidden(identity_down: Any) -> None:
    """AUTH-13 / SPL-11: a non-platform signer on POST /escrow/{id}/split returns
    403 forbidden (real local Ed25519 verification against the platform's own key)."""
    client, state, _platform_private_key = identity_down
    await state.ledger.create_account("a-poster", 100)
    await state.ledger.create_account("a-worker", 0)
    locked = await state.ledger.escrow_lock("a-poster", 100, "t-spl-11")
    escrow_id = locked["escrow_id"]

    non_platform_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {
            "action": "escrow_split",
            "escrow_id": escrow_id,
            "worker_account_id": "a-worker",
            "worker_pct": 50,
            "poster_account_id": "a-poster",
        },
        non_platform_key,
        kid="a-eve",
    )
    resp = await client.post(f"/escrow/{escrow_id}/split", json={"token": token})

    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"


@pytest.mark.unit
async def test_create_account_fails_cleanly_when_identity_down(identity_down: Any) -> None:
    """PREC-05: a well-formed self-service create_account payload fails 502
    identity_service_unavailable while Identity is unreachable — create_account has
    no local-verification fallback, unlike credit/escrow_release/escrow_split."""
    client, _state, _platform_private_key = identity_down

    agent_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {"action": "create_account", "agent_id": "a-alice", "initial_balance": 0},
        agent_key,
        kid="a-alice",
    )
    resp = await client.post("/accounts", json={"token": token})

    assert resp.status_code == 502
    assert resp.json()["error"] == "identity_service_unavailable"


@pytest.mark.unit
async def test_expired_platform_token_rejected_on_credit(identity_down: Any) -> None:
    """EXP-01: a platform-signed credit token with an already-elapsed TTL is
    rejected with 401 token_expired."""
    client, state, private_key = identity_down
    await state.ledger.create_account("a-worker", 0)

    token = create_jws(
        {"action": "credit", "account_id": "a-worker", "amount": 10, "reference": "r-exp"},
        private_key,
        kid=PLATFORM_ID,
        token_ttl_seconds=-10,
    )
    resp = await client.post("/accounts/a-worker/credit", json={"token": token})

    assert resp.status_code == 401
    assert resp.json()["error"] == "token_expired"


@pytest.mark.unit
async def test_non_expiring_token_accepted_regardless_of_age(identity_down: Any) -> None:
    """EXP-02: a platform-signed token with no `exp` claim (token_ttl_seconds=None)
    is accepted — a token without an exp claim never expires."""
    client, state, private_key = identity_down
    await state.ledger.create_account("a-worker", 0)

    token = create_jws(
        {"action": "credit", "account_id": "a-worker", "amount": 10, "reference": "r-noexp"},
        private_key,
        kid=PLATFORM_ID,
        token_ttl_seconds=None,
    )
    resp = await client.post("/accounts/a-worker/credit", json={"token": token})

    assert resp.status_code == 200
    assert resp.json()["balance_after"] == 10
