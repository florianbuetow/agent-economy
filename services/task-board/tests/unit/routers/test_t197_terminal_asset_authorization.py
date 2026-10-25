"""T-197 acceptance tests for retained terminal asset operator access."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from service_auth.jws import TokenPolicy

from task_board_service.core.state import get_app_state
from task_board_service.operator_authority import OperatorAssetAuthority
from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    approve_task,
    setup_task_in_execution,
    submit_deliverable,
    upload_asset,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

pytestmark = pytest.mark.unit


def _download_headers(
    keypair: tuple[Ed25519PrivateKey, str],
    agent_id: str,
    task_id: str,
    asset_id: str,
) -> dict[str, str]:
    token = make_jws_token(
        keypair[0],
        agent_id,
        {"action": "download_asset", "task_id": task_id, "asset_id": asset_id},
        resource=f"GET /tasks/{task_id}/assets/{asset_id}",
    )
    return {"Authorization": f"Bearer {token}"}


async def test_terminal_asset_requires_the_configured_operator_key(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
    carol_keypair: tuple[Ed25519PrivateKey, str],
    carol_agent_id: str,
) -> None:
    task_id, _bid_id = await setup_task_in_execution(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    content = b"retained terminal evidence\n"
    uploaded = await upload_asset(
        client,
        bob_keypair,
        bob_agent_id,
        task_id,
        filename="answer.txt",
        content=content,
    )
    assert uploaded.status_code == 201
    asset_id = uploaded.json()["asset_id"]
    assert (await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)).status_code == 200
    approved = await approve_task(client, alice_keypair, alice_agent_id, task_id)
    assert approved.status_code in {200, 202}
    state = get_app_state()
    assert state.token_validator is not None
    assert state.identity_client is not None
    state.token_validator.configure_operator_asset_authority(
        OperatorAssetAuthority(
            public_key=carol_keypair[0].public_key(),
            canonical_public_key=carol_keypair[1],
            token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=0),
        )
    )
    state.identity_client.get_agent = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda agent_id: {
            "agent_id": agent_id,
            "public_key": {
                alice_agent_id: alice_keypair[1],
                bob_agent_id: bob_keypair[1],
                carol_agent_id: carol_keypair[1],
            }[agent_id],
        }
    )

    unsigned = await client.get(f"/tasks/{task_id}/assets/{asset_id}")
    poster_not_operator = await client.get(
        f"/tasks/{task_id}/assets/{asset_id}",
        headers=_download_headers(alice_keypair, alice_agent_id, task_id, asset_id),
    )
    worker_not_operator = await client.get(
        f"/tasks/{task_id}/assets/{asset_id}",
        headers=_download_headers(bob_keypair, bob_agent_id, task_id, asset_id),
    )
    operator = await client.get(
        f"/tasks/{task_id}/assets/{asset_id}",
        headers=_download_headers(carol_keypair, carol_agent_id, task_id, asset_id),
    )

    assert (unsigned.status_code, unsigned.json()["error"]) == (410, "artifact_unavailable")
    assert (poster_not_operator.status_code, poster_not_operator.json()["error"]) == (
        403,
        "forbidden",
    )
    assert (worker_not_operator.status_code, worker_not_operator.json()["error"]) == (
        403,
        "forbidden",
    )
    assert operator.status_code == 200 and operator.content == content


async def test_terminal_token_is_bound_to_exact_task_and_asset(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    task_id, _bid_id = await setup_task_in_execution(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    uploaded = await upload_asset(client, bob_keypair, bob_agent_id, task_id)
    asset_id = uploaded.json()["asset_id"]
    assert (await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)).status_code == 200
    assert (await approve_task(client, alice_keypair, alice_agent_id, task_id)).status_code in {
        200,
        202,
    }
    state = get_app_state()
    assert state.token_validator is not None
    assert state.identity_client is not None
    state.token_validator.configure_operator_asset_authority(
        OperatorAssetAuthority(
            public_key=alice_keypair[0].public_key(),
            canonical_public_key=alice_keypair[1],
            token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=0),
        )
    )
    state.identity_client.get_agent = AsyncMock(  # type: ignore[method-assign]
        return_value={"agent_id": alice_agent_id, "public_key": alice_keypair[1]}
    )

    wrong_asset = "asset-00000000-0000-4000-8000-000000000197"
    response = await client.get(
        f"/tasks/{task_id}/assets/{asset_id}",
        headers=_download_headers(alice_keypair, alice_agent_id, task_id, wrong_asset),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


async def test_signed_nonterminal_download_still_requires_configured_operator(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
    carol_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    task_id, _bid_id = await setup_task_in_execution(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    uploaded = await upload_asset(client, bob_keypair, bob_agent_id, task_id)
    asset_id = uploaded.json()["asset_id"]
    state = get_app_state()
    assert state.token_validator is not None
    assert state.identity_client is not None
    state.token_validator.configure_operator_asset_authority(
        OperatorAssetAuthority(
            public_key=carol_keypair[0].public_key(),
            canonical_public_key=carol_keypair[1],
            token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=0),
        )
    )
    state.identity_client.get_agent = AsyncMock(  # type: ignore[method-assign]
        return_value={"agent_id": alice_agent_id, "public_key": alice_keypair[1]}
    )

    response = await client.get(
        f"/tasks/{task_id}/assets/{asset_id}",
        headers=_download_headers(alice_keypair, alice_agent_id, task_id, asset_id),
    )

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
