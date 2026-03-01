"""Task creation, query, and cancellation endpoint tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token, tamper_jws
from tests.unit.routers.conftest import (
    ALICE_AGENT_ID,
    BOB_AGENT_ID,
    create_task,
    make_task_id,
    setup_task_in_execution,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Task Creation (POST /tasks)
# ---------------------------------------------------------------------------
class TestTaskCreation:
    """Tests for POST /tasks endpoint (TC-01 through TC-28)."""

    @pytest.mark.unit
    async def test_tc01_create_valid_task_with_escrow(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-01: Create a valid task with escrow."""
        task_id = make_task_id()
        resp = await create_task(
            client,
            alice_keypair,
            alice_agent_id,
            task_id=task_id,
            title="Implement login page",
            spec="Create a login page with email and password fields",
            reward=100,
        )
        assert resp.status_code == 201

        data = resp.json()
        assert data["task_id"] == task_id
        assert data["poster_id"] == alice_agent_id
        assert data["title"] == "Implement login page"
        assert data["reward"] == 100
        assert data["status"] == "open"
        assert data["bid_count"] == 0
        assert data["escrow_pending"] is False
        assert data["worker_id"] is None
        assert data["accepted_bid_id"] is None
        assert data["accepted_at"] is None
        assert data["submitted_at"] is None
        assert data["approved_at"] is None
        assert data["cancelled_at"] is None
        assert data["disputed_at"] is None
        assert data["dispute_reason"] is None
        assert data["ruling_id"] is None
        assert data["ruled_at"] is None
        assert data["worker_pct"] is None
        assert data["ruling_summary"] is None
        assert data["expired_at"] is None
        assert isinstance(data["created_at"], str)
        assert isinstance(data["escrow_id"], str)
        assert data["escrow_id"].startswith("esc-")
        assert data["bidding_deadline"] is not None
        assert data["execution_deadline"] is None
        assert data["review_deadline"] is None

    @pytest.mark.unit
    async def test_tc02_duplicate_task_id_rejected(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-02: Duplicate task_id is rejected with 409."""
        task_id = make_task_id()
        resp1 = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        assert resp1.status_code == 201

        resp2 = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        assert resp2.status_code == 409
        assert resp2.json()["error"] == "TASK_ALREADY_EXISTS"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "bad_id",
        ["not-a-uuid", "t-notauuid", "123", ""],
        ids=["not-a-uuid", "t-notauuid", "digits", "empty"],
    )
    async def test_tc03_invalid_task_id_format(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bad_id: str,
    ) -> None:
        """TC-03: Invalid task_id format returns 400 INVALID_TASK_ID."""
        resp = await create_task(client, alice_keypair, alice_agent_id, task_id=bad_id)
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_TASK_ID"

    @pytest.mark.unit
    async def test_tc04_missing_task_token(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-04: Missing task_token returns 400 INVALID_JWS."""
        private_key = alice_keypair[0]
        task_id = make_task_id()
        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post("/tasks", json={"escrow_token": escrow_token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_tc05_missing_escrow_token(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-05: Missing escrow_token returns 400 INVALID_JWS."""
        private_key = alice_keypair[0]
        task_id = make_task_id()
        task_payload = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        resp = await client.post("/tasks", json={"task_token": task_token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_tc06_both_tokens_missing(self, client: AsyncClient) -> None:
        """TC-06: Missing both tokens returns 400 INVALID_JWS."""
        resp = await client.post("/tasks", json={})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "bad_token",
        ["not-a-jws", "only.two-parts", 12345, None, ""],
        ids=["no-dots", "two-parts", "integer", "null", "empty"],
    )
    async def test_tc07_malformed_task_token(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bad_token: Any,
    ) -> None:
        """TC-07: Malformed task_token returns 400 INVALID_JWS."""
        private_key = alice_keypair[0]
        task_id = make_task_id()
        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": bad_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_tc08_wrong_action_in_task_token(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-08: Wrong action in task_token returns 400 INVALID_PAYLOAD."""
        private_key = alice_keypair[0]
        task_id = make_task_id()

        task_payload = {
            "action": "submit_bid",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "missing_field",
        ["poster_id", "title", "spec", "reward", "task_id"],
        ids=["poster_id", "title", "spec", "reward", "task_id"],
    )
    async def test_tc09_missing_required_payload_fields(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        missing_field: str,
    ) -> None:
        """TC-09: Missing required field in task_token returns 400 INVALID_PAYLOAD."""
        private_key = alice_keypair[0]
        task_id = make_task_id()

        task_payload: dict[str, Any] = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        del task_payload[missing_field]

        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_payload.get("task_id", task_id),
            "agent_id": alice_agent_id,
            "amount": task_payload.get("reward", 100),
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_tc10_signer_does_not_match_poster_id(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        bob_agent_id: str,
    ) -> None:
        """TC-10: Signer != poster_id returns 403 FORBIDDEN."""
        private_key = alice_keypair[0]
        task_id = make_task_id()

        task_payload = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": bob_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(private_key, ALICE_AGENT_ID, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": bob_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, ALICE_AGENT_ID, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_tc11_task_id_mismatch_between_tokens(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-11: task_id mismatch between tokens returns 400 TOKEN_MISMATCH."""
        private_key = alice_keypair[0]
        task_id_a = make_task_id()
        task_id_b = make_task_id()

        task_payload = {
            "action": "create_task",
            "task_id": task_id_a,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id_b,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "TOKEN_MISMATCH"

    @pytest.mark.unit
    async def test_tc12_reward_amount_mismatch_between_tokens(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-12: reward/amount mismatch between tokens returns 400 TOKEN_MISMATCH."""
        private_key = alice_keypair[0]
        task_id = make_task_id()

        task_payload = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 50,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "TOKEN_MISMATCH"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "bad_reward",
        [0, -1, 3.5, "abc", None],
        ids=["zero", "negative", "float", "string", "null"],
    )
    async def test_tc13_invalid_reward_values(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bad_reward: Any,
    ) -> None:
        """TC-13: Invalid reward values return 400 INVALID_REWARD."""
        private_key = alice_keypair[0]
        task_id = make_task_id()

        task_payload: dict[str, Any] = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": bad_reward,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        escrow_payload: dict[str, Any] = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": bad_reward if bad_reward is not None else 0,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_REWARD"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "bad_deadline",
        [0, -3600, 1.5, "one hour"],
        ids=["zero", "negative", "float", "string"],
    )
    async def test_tc14a_invalid_bidding_deadline(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bad_deadline: Any,
    ) -> None:
        """TC-14a: Invalid bidding_deadline_seconds returns 400 INVALID_DEADLINE."""
        private_key = alice_keypair[0]
        task_id = make_task_id()

        task_payload: dict[str, Any] = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": bad_deadline,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_DEADLINE"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "bad_deadline",
        [0, -3600, 1.5, "one hour"],
        ids=["zero", "negative", "float", "string"],
    )
    async def test_tc14b_invalid_execution_deadline(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bad_deadline: Any,
    ) -> None:
        """TC-14b: Invalid execution_deadline_seconds returns 400 INVALID_DEADLINE."""
        private_key = alice_keypair[0]
        task_id = make_task_id()

        task_payload: dict[str, Any] = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": bad_deadline,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_DEADLINE"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "bad_deadline",
        [0, -3600, 1.5, "one hour"],
        ids=["zero", "negative", "float", "string"],
    )
    async def test_tc14c_invalid_review_deadline(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bad_deadline: Any,
    ) -> None:
        """TC-14c: Invalid review_deadline_seconds returns 400 INVALID_DEADLINE."""
        private_key = alice_keypair[0]
        task_id = make_task_id()

        task_payload: dict[str, Any] = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": bad_deadline,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_DEADLINE"

    @pytest.mark.unit
    async def test_tc15_escrow_insufficient_funds(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-15: Insufficient funds returns 402 INSUFFICIENT_FUNDS."""
        state = get_app_state()
        state.central_bank_client.escrow_lock = AsyncMock(
            side_effect=Exception("INSUFFICIENT_FUNDS")
        )

        resp = await create_task(client, alice_keypair, alice_agent_id, reward=10000)
        assert resp.status_code == 402
        assert resp.json()["error"] == "INSUFFICIENT_FUNDS"

    @pytest.mark.unit
    async def test_tc16_central_bank_unavailable(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-16: Central Bank unavailable returns 502 CENTRAL_BANK_UNAVAILABLE."""
        state = get_app_state()
        state.central_bank_client.escrow_lock = AsyncMock(
            side_effect=ConnectionError("Central Bank unreachable")
        )

        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 502
        assert resp.json()["error"] == "CENTRAL_BANK_UNAVAILABLE"

    @pytest.mark.unit
    async def test_tc17_title_at_max_length_accepted(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-17: Title at max length (200 chars) is accepted."""
        long_title = "A" * 200
        resp = await create_task(client, alice_keypair, alice_agent_id, title=long_title)
        assert resp.status_code == 201
        assert resp.json()["title"] == long_title

    @pytest.mark.unit
    async def test_tc18_title_exceeds_max_length(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-18: Title exceeding max length returns 400 TITLE_TOO_LONG."""
        long_title = "A" * 201
        resp = await create_task(client, alice_keypair, alice_agent_id, title=long_title)
        assert resp.status_code == 400
        assert resp.json()["error"] == "TITLE_TOO_LONG"

    @pytest.mark.unit
    async def test_tc19_spec_at_max_length_accepted(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-19: Spec at max length (10000 chars) is accepted."""
        long_spec = "S" * 10000
        resp = await create_task(client, alice_keypair, alice_agent_id, spec=long_spec)
        assert resp.status_code == 201

    @pytest.mark.unit
    async def test_tc20_tampered_task_token(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-20: Tampered task_token returns 403 FORBIDDEN."""
        private_key = alice_keypair[0]
        task_id = make_task_id()

        task_payload = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)
        tampered_token = tamper_jws(task_token)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": tampered_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_tc21_escrow_signer_differs_from_task_signer(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        bob_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-21: Escrow signer != task signer returns 400 TOKEN_MISMATCH."""
        alice_key = alice_keypair[0]
        bob_key = bob_keypair[0]
        task_id = make_task_id()

        task_payload = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(alice_key, alice_agent_id, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(bob_key, BOB_AGENT_ID, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "TOKEN_MISMATCH"

    @pytest.mark.unit
    async def test_tc22_identity_service_unavailable(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-22: Identity service unavailable returns 502."""
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            side_effect=ConnectionError("Identity service unreachable")
        )

        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 502
        assert resp.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"

    @pytest.mark.unit
    async def test_tc23_malformed_json_body(self, client: AsyncClient) -> None:
        """TC-23: Malformed JSON body returns 400 INVALID_JSON."""
        resp = await client.post(
            "/tasks",
            content=b"{invalid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JSON"

    @pytest.mark.unit
    async def test_tc24_wrong_content_type(self, client: AsyncClient) -> None:
        """TC-24: Wrong content type returns 415 UNSUPPORTED_MEDIA_TYPE."""
        resp = await client.post(
            "/tasks",
            content=b'{"task_token": "x", "escrow_token": "y"}',
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415
        assert resp.json()["error"] == "UNSUPPORTED_MEDIA_TYPE"

    @pytest.mark.unit
    async def test_tc25_oversized_body(self, client: AsyncClient) -> None:
        """TC-25: Oversized body returns 413 PAYLOAD_TOO_LARGE."""
        huge_body = b"x" * (1048576 + 1)
        resp = await client.post(
            "/tasks",
            content=huge_body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"] == "PAYLOAD_TOO_LARGE"

    @pytest.mark.unit
    async def test_tc26_extra_fields_in_payload_ignored(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-26: Extra fields in payload are ignored, task created normally."""
        private_key = alice_keypair[0]
        task_id = make_task_id()

        task_payload: dict[str, Any] = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
            "status": "approved",
            "escrow_id": "esc-fake",
            "worker_id": "a-attacker",
            "is_admin": True,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": escrow_token},
        )
        assert resp.status_code == 201

        data = resp.json()
        assert data["status"] == "open"
        assert data["worker_id"] is None
        assert data["escrow_id"] != "esc-fake"

    @pytest.mark.unit
    async def test_tc27_concurrent_duplicate_task_id(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TC-27: Concurrent duplicate task_id -- one 201, one 409."""
        task_id = make_task_id()

        results = await asyncio.gather(
            create_task(client, alice_keypair, alice_agent_id, task_id=task_id),
            create_task(client, alice_keypair, alice_agent_id, task_id=task_id),
        )
        status_codes = sorted(r.status_code for r in results)
        assert status_codes == [201, 409]

    @pytest.mark.unit
    async def test_tc28_empty_body(self, client: AsyncClient) -> None:
        """TC-28: Empty body returns 400 INVALID_JWS."""
        resp = await client.post(
            "/tasks",
            content=b"",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"


# ---------------------------------------------------------------------------
# Task Queries (GET /tasks, GET /tasks/{task_id})
# ---------------------------------------------------------------------------
class TestTaskQueries:
    """Tests for GET /tasks and GET /tasks/{task_id} (TQ-01 through TQ-13)."""

    @pytest.mark.unit
    async def test_tq01_get_task_by_id(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TQ-01: GET /tasks/{task_id} returns 200 with full task fields."""
        task_id = make_task_id()
        create_resp = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        assert create_resp.status_code == 201

        resp = await client.get(f"/tasks/{task_id}")
        assert resp.status_code == 200

        data = resp.json()
        assert data["task_id"] == task_id
        assert data["poster_id"] == alice_agent_id
        assert data["status"] == "open"
        assert "title" in data
        assert "spec" in data
        assert "reward" in data
        assert "created_at" in data
        assert "escrow_id" in data

    @pytest.mark.unit
    async def test_tq02_get_nonexistent_task(self, client: AsyncClient) -> None:
        """TQ-02: GET /tasks/{nonexistent} returns 404 TASK_NOT_FOUND."""
        resp = await client.get("/tasks/t-00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
        assert resp.json()["error"] == "TASK_NOT_FOUND"

    @pytest.mark.unit
    async def test_tq03_get_malformed_task_id(self, client: AsyncClient) -> None:
        """TQ-03: GET /tasks/malformed-id returns 404."""
        resp = await client.get("/tasks/not-a-valid-id")
        assert resp.status_code == 404

    @pytest.mark.unit
    async def test_tq04_path_traversal(self, client: AsyncClient) -> None:
        """TQ-04: Path traversal attempt returns 404."""
        resp = await client.get("/tasks/../../etc/passwd")
        assert resp.status_code == 404
        body = resp.text
        assert "/etc/passwd" not in body
        assert "Traceback" not in body

    @pytest.mark.unit
    async def test_tq05_list_tasks_empty(self, client: AsyncClient) -> None:
        """TQ-05: GET /tasks on empty system returns 200 with empty array."""
        resp = await client.get("/tasks")
        assert resp.status_code == 200
        assert resp.json()["tasks"] == []

    @pytest.mark.unit
    async def test_tq06_list_tasks_with_data(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TQ-06: GET /tasks returns array of created tasks."""
        resp1 = await create_task(client, alice_keypair, alice_agent_id)
        assert resp1.status_code == 201
        resp2 = await create_task(client, alice_keypair, alice_agent_id)
        assert resp2.status_code == 201

        resp = await client.get("/tasks")
        assert resp.status_code == 200

        tasks = resp.json()["tasks"]
        assert len(tasks) >= 2

        for task in tasks:
            assert "task_id" in task
            assert "poster_id" in task
            assert "title" in task
            assert "status" in task
            assert "reward" in task

    @pytest.mark.unit
    async def test_tq07_filter_by_status(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TQ-07: Filter by status returns only matching tasks."""
        task_id = make_task_id()
        resp_create = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        assert resp_create.status_code == 201

        # Cancel a second task to get a "cancelled" status
        task_id_2 = make_task_id()
        resp_create_2 = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_2)
        assert resp_create_2.status_code == 201

        private_key = alice_keypair[0]
        cancel_payload = {
            "action": "cancel_task",
            "task_id": task_id_2,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(private_key, alice_agent_id, cancel_payload)
        cancel_resp = await client.post(f"/tasks/{task_id_2}/cancel", json={"token": cancel_token})
        assert cancel_resp.status_code == 200

        resp = await client.get("/tasks?status=open")
        assert resp.status_code == 200

        tasks = resp.json()["tasks"]
        for task in tasks:
            assert task["status"] == "open"

    @pytest.mark.unit
    async def test_tq08_filter_by_poster_id(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bob_keypair: Any,
        bob_agent_id: str,
    ) -> None:
        """TQ-08: Filter by poster_id returns only matching tasks."""
        await create_task(client, alice_keypair, alice_agent_id)
        await create_task(client, bob_keypair, bob_agent_id)

        resp = await client.get(f"/tasks?poster_id={alice_agent_id}")
        assert resp.status_code == 200

        tasks = resp.json()["tasks"]
        for task in tasks:
            assert task["poster_id"] == alice_agent_id

    @pytest.mark.unit
    async def test_tq09_pagination_offset_limit(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TQ-09: Pagination with offset and limit works correctly."""
        for _ in range(5):
            resp = await create_task(client, alice_keypair, alice_agent_id)
            assert resp.status_code == 201

        resp = await client.get("/tasks?offset=1&limit=2")
        assert resp.status_code == 200

        tasks = resp.json()["tasks"]
        assert len(tasks) <= 2

    @pytest.mark.unit
    async def test_tq10_ordered_by_created_at_desc(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TQ-10: Tasks are ordered by created_at descending."""
        for _ in range(3):
            resp = await create_task(client, alice_keypair, alice_agent_id)
            assert resp.status_code == 201

        resp = await client.get("/tasks")
        assert resp.status_code == 200

        tasks = resp.json()["tasks"]
        timestamps = [t["created_at"] for t in tasks]
        assert timestamps == sorted(timestamps, reverse=True)

    @pytest.mark.unit
    async def test_tq11_no_internal_fields_exposed(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TQ-11: List endpoint does not expose internal/detail-only fields."""
        await create_task(client, alice_keypair, alice_agent_id)

        resp = await client.get("/tasks")
        assert resp.status_code == 200

        tasks = resp.json()["tasks"]
        assert len(tasks) >= 1
        for task in tasks:
            assert "spec" not in task
            assert "dispute_reason" not in task
            assert "ruling_summary" not in task

    @pytest.mark.unit
    async def test_tq12_idempotent_read(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """TQ-12: Reading the same task twice returns identical JSON."""
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)

        resp1 = await client.get(f"/tasks/{task_id}")
        resp2 = await client.get(f"/tasks/{task_id}")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json() == resp2.json()

    @pytest.mark.unit
    async def test_tq13_sql_injection_safety(self, client: AsyncClient) -> None:
        """TQ-13: SQL injection in task_id path returns 404 safely."""
        resp = await client.get("/tasks/' OR '1'='1")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task Cancellation (POST /tasks/{task_id}/cancel)
# ---------------------------------------------------------------------------
class TestTaskCancellation:
    """Tests for POST /tasks/{task_id}/cancel (CAN-01 through CAN-09)."""

    @pytest.mark.unit
    async def test_can01_poster_cancels_open_task(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """CAN-01: Poster cancels open task -- 200, status=cancelled."""
        task_id = make_task_id()
        create_resp = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        assert create_resp.status_code == 201

        private_key = alice_keypair[0]
        cancel_payload = {
            "action": "cancel_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(private_key, alice_agent_id, cancel_payload)

        resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token})
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["cancelled_at"] is not None

    @pytest.mark.unit
    async def test_can02_cancel_releases_escrow(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """CAN-02: Cancellation releases escrow back to poster."""
        task_id = make_task_id()
        create_resp = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        assert create_resp.status_code == 201

        private_key = alice_keypair[0]
        cancel_payload = {
            "action": "cancel_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(private_key, alice_agent_id, cancel_payload)

        resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token})
        assert resp.status_code == 200

        state = get_app_state()
        state.central_bank_client.escrow_release.assert_called()

    @pytest.mark.unit
    async def test_can03_non_poster_forbidden(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bob_keypair: Any,
        bob_agent_id: str,
    ) -> None:
        """CAN-03: Non-poster cannot cancel -- 403 FORBIDDEN."""
        task_id = make_task_id()
        create_resp = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        assert create_resp.status_code == 201

        bob_key = bob_keypair[0]
        cancel_payload = {
            "action": "cancel_task",
            "task_id": task_id,
            "poster_id": bob_agent_id,
        }
        cancel_token = make_jws_token(bob_key, bob_agent_id, cancel_payload)

        resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token})
        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_can04_cancel_nonexistent_task(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """CAN-04: Cancel nonexistent task returns 404 TASK_NOT_FOUND."""
        private_key = alice_keypair[0]
        fake_task_id = "t-00000000-0000-0000-0000-000000000000"
        cancel_payload = {
            "action": "cancel_task",
            "task_id": fake_task_id,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(private_key, alice_agent_id, cancel_payload)

        resp = await client.post(f"/tasks/{fake_task_id}/cancel", json={"token": cancel_token})
        assert resp.status_code == 404
        assert resp.json()["error"] == "TASK_NOT_FOUND"

    @pytest.mark.unit
    async def test_can05_cancel_already_cancelled(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """CAN-05: Cancel already-cancelled task returns 409 INVALID_STATUS."""
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)

        private_key = alice_keypair[0]
        cancel_payload = {
            "action": "cancel_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(private_key, alice_agent_id, cancel_payload)

        resp1 = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token})
        assert resp1.status_code == 200

        cancel_token_2 = make_jws_token(private_key, alice_agent_id, cancel_payload)
        resp2 = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token_2})
        assert resp2.status_code == 409
        assert resp2.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_can06_wrong_action_in_cancel_token(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """CAN-06: Wrong action in cancel token returns 400 INVALID_PAYLOAD."""
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)

        private_key = alice_keypair[0]
        bad_payload = {
            "action": "approve_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        bad_token = make_jws_token(private_key, alice_agent_id, bad_payload)

        resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": bad_token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_can07_cancel_wrong_status_accepted(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bob_keypair: Any,
        bob_agent_id: str,
    ) -> None:
        """CAN-07: Cancel task in accepted status returns 409 INVALID_STATUS."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        private_key = alice_keypair[0]
        cancel_payload = {
            "action": "cancel_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(private_key, alice_agent_id, cancel_payload)

        resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token})
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_can08_malformed_token_on_cancel(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """CAN-08: Malformed token on cancel returns 400 INVALID_JWS."""
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)

        resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": "not-a-jws"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_can09_cancel_with_expired_bidding_deadline(
        self,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
    ) -> None:
        """CAN-09: Cancel with expired bidding deadline still works."""
        task_id = make_task_id()
        create_resp = await create_task(
            client,
            alice_keypair,
            alice_agent_id,
            task_id=task_id,
            bidding_deadline_seconds=1,
        )
        assert create_resp.status_code == 201

        # Even if deadline is very short, poster should still be able to cancel
        private_key = alice_keypair[0]
        cancel_payload = {
            "action": "cancel_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(private_key, alice_agent_id, cancel_payload)

        resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token})
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
