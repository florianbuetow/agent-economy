"""Lane U (2.4, G03): poster input attachments through public Task Board routes.

Posting intent -> input upload -> frozen input manifest -> publish (escrow lock),
backed by the production Gateway writers on a real SQLite database.
"""

from __future__ import annotations

import hashlib
import uuid
from contextlib import closing
from typing import TYPE_CHECKING, Any

import pytest
from db_gateway_service.board_authority import BoardAuthority
from db_gateway_service.task_time import utc_now_us
from service_auth.financial_identity import operation_id_for

from task_board_service.core.state import get_app_state
from tests.fakes.input_gateway_store import InputGatewayStore
from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    ALICE_AGENT_ID,
    BOB_AGENT_ID,
    CAROL_AGENT_ID,
    accept_bid,
    create_task,
    download_asset_headers,
    list_assets_headers,
    make_task_id,
    submit_bid,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

pytestmark = pytest.mark.unit

_INPUT = b"3 7 11\n"
_POLICY: dict[str, int] = {
    "contract_version": 1,
    "max_file_bytes": 10485760,
    "max_files_per_manifest": 20,
    "max_manifest_bytes": 20971520,
    "reservation_ttl_seconds": 300,
    "reservation_renew_before_expiry_seconds": 60,
    "max_upload_duration_seconds": 900,
}

Keypair = tuple["Ed25519PrivateKey", str]


@pytest.fixture
def input_store(
    app: Any,
    tmp_path: Path,
    platform_keypair: Keypair,
) -> Iterator[InputGatewayStore]:
    """Route the V2 publication and artifact Gateway paths to production writers."""
    _ = app
    with closing(
        InputGatewayStore(
            db_path=tmp_path / "test.db",
            caller_kid="gateway-task-board",
            authority=BoardAuthority(
                platform_public_key=platform_keypair[1],
                deadline_actor_id="system-task-board-deadline",
            ),
            clock_us=utc_now_us,
        )
    ) as store:
        state = get_app_state()
        assert state.asset_manager is not None
        state.store = store
        state.asset_manager.configure_artifacts(store, _POLICY)
        yield store


def _admit_token(
    keypair: Keypair, poster_id: str, task_id: str, publication_id: str, count: int
) -> str:
    return make_jws_token(
        keypair[0],
        poster_id,
        {
            "action": "task_publication_admit",
            "publication_operation_id": publication_id,
            "task_id": task_id,
            "poster_id": poster_id,
            "title": "Sum the inputs",
            "spec": "Return the sum of the integers in input.txt.",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "acceptance_window_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
            "expected_input_count": count,
        },
        resource="POST /task-publications",
    )


async def _admit(client: AsyncClient, keypair: Keypair, count: int) -> tuple[str, str]:
    task_id = make_task_id()
    publication_id = f"op-{uuid.uuid4()}"
    response = await client.post(
        "/task-publications",
        json={"token": _admit_token(keypair, ALICE_AGENT_ID, task_id, publication_id, count)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["publication_state"] == "awaiting_inputs"
    assert body["input_manifest_id"]
    return task_id, publication_id


async def _upload(
    client: AsyncClient,
    keypair: Keypair,
    signer_id: str,
    publication_id: str,
    *,
    poster_id: str,
    content: bytes = _INPUT,
) -> Any:
    token = make_jws_token(
        keypair[0],
        signer_id,
        {
            "action": "task_publication_upload_input",
            "publication_operation_id": publication_id,
            "poster_id": poster_id,
        },
        resource=f"POST /task-publications/{publication_id}/inputs",
    )
    return await client.post(
        f"/task-publications/{publication_id}/inputs",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        files={"file": ("input.txt", content, "text/plain")},
    )


async def _publish(client: AsyncClient, keypair: Keypair, task_id: str, publication_id: str) -> Any:
    task_token = make_jws_token(
        keypair[0],
        ALICE_AGENT_ID,
        {
            "action": "task_publication_publish",
            "publication_operation_id": publication_id,
            "task_id": task_id,
            "poster_id": ALICE_AGENT_ID,
        },
        resource=f"POST /task-publications/{publication_id}/publish",
    )
    escrow_token = make_jws_token(
        keypair[0],
        ALICE_AGENT_ID,
        {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": ALICE_AGENT_ID,
            "amount": 100,
            "operation_id": operation_id_for("task-lock", (task_id, ALICE_AGENT_ID)),
        },
        audience="central-bank",
    )
    return await client.post(
        f"/task-publications/{publication_id}/publish",
        json={"task_token": task_token, "escrow_token": escrow_token},
    )


async def _published_with_input(client: AsyncClient, alice: Keypair) -> tuple[str, str]:
    task_id, publication_id = await _admit(client, alice, 1)
    uploaded = await _upload(
        client, alice, ALICE_AGENT_ID, publication_id, poster_id=ALICE_AGENT_ID
    )
    assert uploaded.status_code == 201, uploaded.text
    published = await _publish(client, alice, task_id, publication_id)
    assert published.status_code == 201, published.text
    assert published.json()["publication_state"] == "published"
    return task_id, publication_id


@pytest.mark.usefixtures("input_store")
async def test_bidder_downloads_exact_input_bytes_after_publish(
    client: AsyncClient, alice_keypair: Keypair, bob_keypair: Keypair
) -> None:
    task_id, _publication_id = await _published_with_input(client, alice_keypair)

    detail = await client.get(f"/tasks/{task_id}")
    assert detail.status_code == 200, detail.text
    manifest = detail.json()["input_manifest"]
    assert manifest["state"] == "frozen"
    assert [
        (f["filename"], f["size_bytes"], f["content_sha256"], f["role"]) for f in manifest["files"]
    ] == [("input.txt", len(_INPUT), hashlib.sha256(_INPUT).hexdigest(), "input")]
    asset_id = manifest["files"][0]["asset_id"]

    listed = await client.get(
        f"/tasks/{task_id}/assets", headers=list_assets_headers(bob_keypair, BOB_AGENT_ID, task_id)
    )
    assert listed.status_code == 200, listed.text
    roles = {item["asset_id"]: item["role"] for item in listed.json()["assets"]}
    assert roles == {asset_id: "input"}

    downloaded = await client.get(
        f"/tasks/{task_id}/assets/{asset_id}",
        headers=download_asset_headers(bob_keypair, BOB_AGENT_ID, task_id, asset_id),
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == _INPUT


@pytest.mark.usefixtures("input_store")
async def test_input_manifest_is_immutable_after_acceptance(
    client: AsyncClient, alice_keypair: Keypair, bob_keypair: Keypair
) -> None:
    task_id, publication_id = await _published_with_input(client, alice_keypair)
    before = (await client.get(f"/tasks/{task_id}/inputs")).json()["input_manifest"]

    bid = await submit_bid(client, bob_keypair, BOB_AGENT_ID, task_id)
    assert bid.status_code == 201, bid.text
    accepted = await accept_bid(
        client, alice_keypair, ALICE_AGENT_ID, task_id, bid.json()["bid_id"]
    )
    assert accepted.status_code in {200, 202}, accepted.text

    late = await _upload(
        client,
        alice_keypair,
        ALICE_AGENT_ID,
        publication_id,
        poster_id=ALICE_AGENT_ID,
        content=b"changed\n",
    )
    assert late.status_code == 409, late.text
    assert late.json()["error"] == "invalid_status"

    after = (await client.get(f"/tasks/{task_id}/inputs")).json()["input_manifest"]
    assert after["files"] == before["files"]
    assert after["manifest_sha256"] == before["manifest_sha256"]
    assert after["accepted_publication_contract_sha256"] == before["publication_contract_sha256"]


@pytest.mark.usefixtures("input_store")
async def test_non_poster_cannot_upload_inputs(
    client: AsyncClient, alice_keypair: Keypair, carol_keypair: Keypair
) -> None:
    _task_id, publication_id = await _admit(client, alice_keypair, 1)

    impersonating = await _upload(
        client, carol_keypair, CAROL_AGENT_ID, publication_id, poster_id=ALICE_AGENT_ID
    )
    assert impersonating.status_code == 403, impersonating.text
    assert impersonating.json()["error"] == "forbidden"

    own_claim = await _upload(
        client, carol_keypair, CAROL_AGENT_ID, publication_id, poster_id=CAROL_AGENT_ID
    )
    assert own_claim.status_code == 404, own_claim.text
    assert own_claim.json()["error"] == "publication_not_found"


@pytest.mark.usefixtures("input_store")
async def test_publish_without_inputs_has_empty_frozen_manifest(
    client: AsyncClient, alice_keypair: Keypair
) -> None:
    task_id, publication_id = await _admit(client, alice_keypair, 0)
    published = await _publish(client, alice_keypair, task_id, publication_id)
    assert published.status_code == 201, published.text
    assert published.json()["result"]["status"] == "open"

    detail = await client.get(f"/tasks/{task_id}")
    assert detail.status_code == 200, detail.text
    manifest = detail.json()["input_manifest"]
    assert manifest["state"] == "frozen"
    assert manifest["files"] == []


@pytest.mark.usefixtures("input_store")
async def test_missing_declared_input_blocks_publish_before_escrow(
    client: AsyncClient, alice_keypair: Keypair
) -> None:
    task_id, publication_id = await _admit(client, alice_keypair, 1)
    bank = get_app_state().central_bank_client
    assert bank is not None
    published = await _publish(client, alice_keypair, task_id, publication_id)
    assert published.status_code == 409, published.text
    assert published.json()["error"] == "input_count_mismatch"
    bank.escrow_lock.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.usefixtures("input_store")
async def test_v1_task_read_reports_null_input_manifest(
    client: AsyncClient, alice_keypair: Keypair
) -> None:
    created = await create_task(client, alice_keypair, ALICE_AGENT_ID)
    assert created.status_code == 201, created.text
    task_id = created.json()["result"]["task_id"]
    detail = await client.get(f"/tasks/{task_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["input_manifest"] is None


@pytest.mark.usefixtures("input_store")
async def test_task_read_exposes_submission_binding_only_after_acceptance(
    client: AsyncClient, alice_keypair: Keypair, bob_keypair: Keypair
) -> None:
    task_id, _publication_id = await _published_with_input(client, alice_keypair)
    open_detail = (await client.get(f"/tasks/{task_id}")).json()
    assert open_detail["submission_binding"] is None

    bid = await submit_bid(client, bob_keypair, BOB_AGENT_ID, task_id)
    assert bid.status_code == 201, bid.text
    accepted = await accept_bid(
        client, alice_keypair, ALICE_AGENT_ID, task_id, bid.json()["bid_id"]
    )
    assert accepted.status_code in {200, 202}, accepted.text

    binding = (await client.get(f"/tasks/{task_id}")).json()["submission_binding"]
    assert binding["manifest_id"] == f"manifest-submission-{task_id}"
    assert binding["accepted_binding_kind"]
    assert len(binding["accepted_binding_sha256"]) == 64
