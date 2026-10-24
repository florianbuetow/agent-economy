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

from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import jws as core_jws
from service_auth.signing import create_jws

from .conftest import make_operation_id
from .test_two_tier_auth import PLATFORM_ID, identity_down

__all__ = ["identity_down"]  # re-exported fixture, referenced only by name in tests


@pytest.mark.unit
async def test_non_platform_signer_on_release_is_forbidden(identity_down: Any) -> None:
    """AUTH-12 / REL-06: a non-platform signer on POST /escrow/{id}/release returns
    403 forbidden (real local Ed25519 verification against the platform's own key)."""
    client, state, _platform_private_key = identity_down
    await state.ledger.create_account("a-payer", 100, make_operation_id())
    locked = await state.ledger.escrow_lock("a-payer", 40, "t-rel-06", make_operation_id())
    escrow_id = locked["receipt"]["escrow_id"]

    non_platform_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {
            "action": "escrow_release",
            "escrow_id": escrow_id,
            "recipient_account_id": "a-payer",
            "operation_id": make_operation_id(),
            "source_reference": "task:t-rel-06:approved",
        },
        non_platform_key,
        kid="a-eve",
        audience="central-bank",
        resource=f"POST /escrow/{escrow_id}/release",
        token_ttl_seconds=60,
    )
    resp = await client.post(f"/escrow/{escrow_id}/release", json={"token": token})

    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"


@pytest.mark.unit
async def test_non_platform_signer_on_split_is_forbidden(identity_down: Any) -> None:
    """AUTH-13 / SPL-11: a non-platform signer on POST /escrow/{id}/split returns
    403 forbidden (real local Ed25519 verification against the platform's own key)."""
    client, state, _platform_private_key = identity_down
    await state.ledger.create_account("a-poster", 100, make_operation_id())
    await state.ledger.create_account("a-worker", 0, None)
    locked = await state.ledger.escrow_lock("a-poster", 100, "t-spl-11", make_operation_id())
    escrow_id = locked["receipt"]["escrow_id"]

    non_platform_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {
            "action": "escrow_split",
            "escrow_id": escrow_id,
            "worker_account_id": "a-worker",
            "worker_pct": 50,
            "poster_account_id": "a-poster",
            "operation_id": make_operation_id(),
            "source_reference": "task:t-spl-11:ruling:r-one",
        },
        non_platform_key,
        kid="a-eve",
        audience="central-bank",
        resource=f"POST /escrow/{escrow_id}/split",
        token_ttl_seconds=60,
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
        audience="central-bank",
        resource="POST /accounts",
        token_ttl_seconds=60,
    )
    resp = await client.post("/accounts", json={"token": token})

    assert resp.status_code == 502
    assert resp.json()["error"] == "identity_service_unavailable"


@pytest.mark.unit
async def test_expired_platform_token_rejected_on_credit(
    identity_down: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EXP-01: a platform-signed credit token with an already-elapsed TTL is
    rejected with 401 token_expired."""
    client, state, private_key = identity_down
    await state.ledger.create_account("a-worker", 0, None)

    issued_at = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(core_jws, "_clock", lambda: issued_at)
    token = create_jws(
        {
            "action": "credit",
            "account_id": "a-worker",
            "amount": 10,
            "reference": "r-exp",
            "operation_id": make_operation_id(),
        },
        private_key,
        kid=PLATFORM_ID,
        audience="central-bank",
        resource="POST /accounts/a-worker/credit",
        token_ttl_seconds=60,
    )
    monkeypatch.setattr(
        core_jws,
        "_clock",
        lambda: datetime.fromtimestamp(int(issued_at.timestamp()) + 60, tz=UTC),
    )
    resp = await client.post("/accounts/a-worker/credit", json={"token": token})

    assert resp.status_code == 401
    assert resp.json()["error"] == "token_expired"


@pytest.mark.unit
async def test_non_expiring_token_is_rejected_before_request(identity_down: Any) -> None:
    """EXP-02: production issuance rejects the removed no-expiry mode."""
    _client, state, private_key = identity_down
    await state.ledger.create_account("a-worker", 0, None)

    with pytest.raises(ValueError, match="positive integer"):
        create_jws(
            {
                "action": "credit",
                "account_id": "a-worker",
                "amount": 10,
                "reference": "r-noexp",
                "operation_id": make_operation_id(),
            },
            private_key,
            kid=PLATFORM_ID,
            audience="central-bank",
            resource="POST /accounts/a-worker/credit",
            token_ttl_seconds=None,  # type: ignore[arg-type]
        )
    account = await state.ledger.get_account("a-worker")
    assert account is not None
    assert account["balance"] == 0
