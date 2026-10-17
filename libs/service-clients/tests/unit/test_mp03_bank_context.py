"""BankClient stamps strict context through the shared platform signer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest
from service_auth.jws import TokenPolicy, verify_jws
from service_auth.signing import PlatformSigner, generate_keypair

from service_clients.bank import BankClient

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_and_split_use_concrete_bank_resources(tmp_path: Path) -> None:
    _, public_key = generate_keypair("platform", tmp_path)
    signer = PlatformSigner("a-platform", str(tmp_path / "platform.key"), 50)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    client = BankClient(
        base_url="https://bank",
        escrow_lock_path="/escrow/lock",
        escrow_release_path="/escrow/{escrow_id}/release",
        escrow_split_path="/escrow/{escrow_id}/split",
        timeout_seconds=5,
        platform_signer=signer,
    )
    await client.connection.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://bank", transport=httpx.MockTransport(handler)
    )
    try:
        await client.release_escrow("esc-1", "acct-1")
        await client.split_escrow("esc-2", "worker", "poster", 70)
    finally:
        await client.close()

    policy = TokenPolicy(max_lifetime_seconds=50, clock_skew_seconds=0)
    release = verify_jws(json.loads(requests[0].content)["token"], public_key, policy=policy)
    split = verify_jws(json.loads(requests[1].content)["token"], public_key, policy=policy)
    assert (release.header.aud, release.header.resource) == (
        "central-bank",
        "POST /escrow/esc-1/release",
    )
    assert (split.header.aud, split.header.resource) == (
        "central-bank",
        "POST /escrow/esc-2/split",
    )
    assert release.payload["action"] == "escrow_release"
    assert split.payload["action"] == "escrow_split"
