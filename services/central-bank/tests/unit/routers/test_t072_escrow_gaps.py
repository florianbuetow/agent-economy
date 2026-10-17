"""T-072 coverage-sweep gap fixes: POST /escrow/lock, POST /escrow/{id}/release,
and POST /escrow/{id}/split scenarios from central-bank-service-auth-tests.md and
central-bank-service-tests.md that had no existing test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from central_bank_service.core.state import get_app_state

from .conftest import PLATFORM_AGENT_ID, make_jws_token, make_operation_id


def _setup_identity_mock(state: Any) -> None:
    """Configure mock identity client that decodes tokens (matches test_escrow.py)."""
    state.identity_client.get_agent = AsyncMock(
        return_value={"agent_id": "a-test-agent", "name": "Test"}
    )


async def _create_funded_account(
    client: Any, platform_keypair: Any, account_id: str, balance: int
) -> None:
    private_key, _ = platform_keypair
    payload: dict[str, object] = {
        "action": "create_account",
        "agent_id": account_id,
        "initial_balance": balance,
    }
    if balance > 0:
        payload["operation_id"] = make_operation_id()
    token = make_jws_token(
        private_key,
        PLATFORM_AGENT_ID,
        payload,
    )
    resp = await client.post("/accounts", json={"token": token})
    assert resp.status_code == 201


async def _lock(
    client: Any,
    agent_keypair: Any,
    agent_id: str,
    amount: int,
    task_id: str,
    operation_id: str,
) -> dict[str, Any]:
    private_key, _ = agent_keypair
    token = make_jws_token(
        private_key,
        agent_id,
        {
            "action": "escrow_lock",
            "agent_id": agent_id,
            "amount": amount,
            "task_id": task_id,
            "operation_id": operation_id,
        },
    )
    resp = await client.post("/escrow/lock", json={"token": token})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.unit
class TestEscrowLockScenarios:
    """ESC-05, ESC-07, ESC-08, ESC-09, ESC-10, ESC-11, ESC-12, ESC-13."""

    async def test_account_not_found(self, client, agent_keypair):
        """ESC-05: locking escrow with no account for the agent returns 404."""
        state = get_app_state()
        _setup_identity_mock(state)
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-ghost",
            {
                "action": "escrow_lock",
                "agent_id": "a-ghost",
                "amount": 10,
                "task_id": "T-003",
                "operation_id": make_operation_id(),
            },
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 404
        assert response.json()["error"] == "account_not_found"

    async def test_idempotent_lock_returns_same_escrow_id(
        self, client, platform_keypair, agent_keypair
    ):
        """ESC-07: an identical repeated lock request returns the same escrow_id and
        does not double-debit the balance."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-payer", 100)
        operation_id = make_operation_id()
        first = await _lock(client, agent_keypair, "a-payer", 30, "T-005", operation_id)
        second = await _lock(client, agent_keypair, "a-payer", 30, "T-005", operation_id)
        assert second["receipt"] == first["receipt"]
        assert second["receipt"]["escrow_id"] == first["receipt"]["escrow_id"]

    async def test_duplicate_task_id_different_amount_reports_winning_operation(
        self, client, platform_keypair, agent_keypair
    ):
        """ESC-08: another operation cannot claim an already-owned task lock."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-payer", 100)
        winner = make_operation_id()
        await _lock(client, agent_keypair, "a-payer", 30, "T-006", winner)

        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-payer",
            {
                "action": "escrow_lock",
                "agent_id": "a-payer",
                "amount": 50,
                "task_id": "T-006",
                "operation_id": make_operation_id(),
            },
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 409
        assert response.json()["error"] == "financial_operation_already_committed"
        assert response.json()["details"] == {"winning_operation_id": winner}

    async def test_zero_amount_returns_invalid_amount(self, client, agent_keypair):
        """ESC-09: zero escrow lock amount is rejected."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-payer",
            {"action": "escrow_lock", "agent_id": "a-payer", "amount": 0, "task_id": "T-007"},
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_amount"

    async def test_negative_amount_returns_invalid_amount(self, client, agent_keypair):
        """ESC-10: negative escrow lock amount is rejected."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-payer",
            {"action": "escrow_lock", "agent_id": "a-payer", "amount": -10, "task_id": "T-008"},
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_amount"

    async def test_missing_task_id_returns_invalid_payload(self, client, agent_keypair):
        """ESC-11: missing task_id in JWS payload returns invalid_payload."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-payer",
            {"action": "escrow_lock", "agent_id": "a-payer", "amount": 10},
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    async def test_missing_agent_id_returns_invalid_payload(self, client, agent_keypair):
        """ESC-12: missing agent_id in JWS payload returns invalid_payload."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key, "a-payer", {"action": "escrow_lock", "amount": 10, "task_id": "T-009"}
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    async def test_wrong_action_returns_invalid_payload(self, client, agent_keypair):
        """ESC-13: wrong action ("credit") on escrow lock returns invalid_payload."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-payer",
            {"action": "credit", "agent_id": "a-payer", "amount": 10, "task_id": "T-010"},
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"


@pytest.mark.unit
class TestEscrowReleaseScenarios:
    """REL-02, REL-07, REL-09; PREC-02: escrow release payload/error scenarios."""

    async def test_recipient_balance_increases_by_escrow_amount(
        self, client, platform_keypair, agent_keypair
    ):
        """REL-02: the recipient's balance increases by the released escrow amount."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-payer", 100)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)
        locked = await _lock(client, agent_keypair, "a-payer", 50, "T-rel-02", make_operation_id())

        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": locked["receipt"]["escrow_id"],
                "recipient_account_id": "a-worker",
                "operation_id": make_operation_id(),
                "source_reference": "task:T-rel-02:approved",
            },
        )
        release_resp = await client.post(
            f"/escrow/{locked['receipt']['escrow_id']}/release", json={"token": release_token}
        )
        assert release_resp.status_code == 200

        agent_private_key, _ = agent_keypair
        balance_token = make_jws_token(
            agent_private_key, "a-worker", {"action": "get_balance", "account_id": "a-worker"}
        )
        balance_resp = await client.get(
            "/accounts/a-worker", headers={"Authorization": f"Bearer {balance_token}"}
        )
        assert balance_resp.status_code == 200
        assert balance_resp.json()["balance"] == 50

    async def test_recipient_account_not_found(self, client, platform_keypair, agent_keypair):
        """REL-07: releasing to a nonexistent recipient account returns 404
        account_not_found."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-payer", 100)
        locked = await _lock(client, agent_keypair, "a-payer", 30, "T-200", make_operation_id())

        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": locked["receipt"]["escrow_id"],
                "recipient_account_id": "a-nonexistent-uuid",
                "operation_id": make_operation_id(),
                "source_reference": "task:T-200:approved",
            },
        )
        response = await client.post(
            f"/escrow/{locked['receipt']['escrow_id']}/release", json={"token": release_token}
        )
        assert response.status_code == 404
        assert response.json()["error"] == "account_not_found"

    async def test_missing_recipient_account_id_returns_invalid_payload(
        self, client, platform_keypair, agent_keypair
    ):
        """REL-09: missing recipient_account_id in JWS payload returns invalid_payload."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-payer", 100)
        locked = await _lock(client, agent_keypair, "a-payer", 30, "T-201", make_operation_id())

        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": locked["receipt"]["escrow_id"],
                "operation_id": make_operation_id(),
                "source_reference": "task:T-201:approved",
            },
        )
        response = await client.post(
            f"/escrow/{locked['receipt']['escrow_id']}/release", json={"token": release_token}
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    async def test_malformed_payload_from_non_platform_signer_beats_forbidden(
        self, client, agent_keypair
    ):
        """PREC-02: a malformed release payload (missing recipient_account_id) from a
        non-platform signer still returns invalid_payload, not 403 — payload-shape
        validation runs before the local platform-signature check."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-eve",
            {
                "action": "escrow_release",
                "escrow_id": "esc-whatever",
                "operation_id": make_operation_id(),
                "source_reference": "task:T-whatever:approved",
            },
        )
        response = await client.post("/escrow/esc-whatever/release", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"


@pytest.mark.unit
class TestEscrowSplitScenarios:
    """SPL-01, SPL-02, SPL-03, SPL-05, SPL-06, SPL-09, SPL-10, SPL-13, SPL-14, SPL-16;
    PREC-03: escrow split calculation and payload/error scenarios."""

    async def _split(
        self,
        client: Any,
        platform_keypair: Any,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> Any:
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "worker_pct": worker_pct,
                "poster_account_id": poster_account_id,
                "operation_id": operation_id,
                "source_reference": source_reference,
            },
        )
        return await client.post(f"/escrow/{escrow_id}/split", json={"token": token})

    async def test_even_50_50_split(self, client, platform_keypair, agent_keypair):
        """SPL-01: an even 50/50 split divides the escrow amount equally."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-poster", 1000)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)
        locked = await _lock(client, agent_keypair, "a-poster", 500, "T-300", make_operation_id())

        response = await self._split(
            client,
            platform_keypair,
            locked["receipt"]["escrow_id"],
            "a-worker",
            50,
            "a-poster",
            make_operation_id(),
            "task:T-300:ruling:r-one",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["receipt"]["result"] == {
            "escrow_status_at_commit": "split",
            "worker_pct": 50,
        }
        assert data["receipt"]["legs"][0]["amount"] == 250
        assert data["receipt"]["legs"][1]["amount"] == 250
        assert data["current_escrow"]["status"] == "split"

    async def test_uneven_80_20_split_floor_math(self, client, platform_keypair, agent_keypair):
        """SPL-02: an 80/20 split uses floor math (400/100 from a 500 escrow)."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-poster", 1000)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)
        locked = await _lock(client, agent_keypair, "a-poster", 500, "T-301", make_operation_id())

        response = await self._split(
            client,
            platform_keypair,
            locked["receipt"]["escrow_id"],
            "a-worker",
            80,
            "a-poster",
            make_operation_id(),
            "task:T-301:ruling:r-one",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["receipt"]["legs"][0]["amount"] == 400
        assert data["receipt"]["legs"][1]["amount"] == 100

    async def test_worker_gets_all_100_0_split(self, client, platform_keypair, agent_keypair):
        """SPL-03: worker_pct=100 gives the worker the entire escrow amount."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-poster", 1000)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)
        locked = await _lock(client, agent_keypair, "a-poster", 100, "T-302", make_operation_id())

        response = await self._split(
            client,
            platform_keypair,
            locked["receipt"]["escrow_id"],
            "a-worker",
            100,
            "a-poster",
            make_operation_id(),
            "task:T-302:ruling:r-one",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["receipt"]["legs"][0]["amount"] == 100
        assert data["receipt"]["legs"][1]["amount"] == 0
        assert data["receipt"]["legs"][1]["transaction_id"] is None

    async def test_odd_amount_floor_rounding(self, client, platform_keypair, agent_keypair):
        """SPL-05: floor(101 * 33 / 100) = 33, remainder 68 goes to the poster."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-poster", 1000)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)
        locked = await _lock(client, agent_keypair, "a-poster", 101, "T-303", make_operation_id())

        response = await self._split(
            client,
            platform_keypair,
            locked["receipt"]["escrow_id"],
            "a-worker",
            33,
            "a-poster",
            make_operation_id(),
            "task:T-303:ruling:r-one",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["receipt"]["legs"][0]["amount"] == 33
        assert data["receipt"]["legs"][1]["amount"] == 68

    async def test_both_balances_updated_after_split(self, client, platform_keypair, agent_keypair):
        """SPL-06: both poster and worker balances update correctly after a split."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-poster", 200)
        await _create_funded_account(client, platform_keypair, "a-worker", 10)
        locked = await _lock(client, agent_keypair, "a-poster", 100, "T-304", make_operation_id())

        response = await self._split(
            client,
            platform_keypair,
            locked["receipt"]["escrow_id"],
            "a-worker",
            60,
            "a-poster",
            make_operation_id(),
            "task:T-304:ruling:r-one",
        )
        assert response.status_code == 200

        agent_key, _ = agent_keypair
        poster_token = make_jws_token(
            agent_key, "a-poster", {"action": "get_balance", "account_id": "a-poster"}
        )
        poster_resp = await client.get(
            "/accounts/a-poster", headers={"Authorization": f"Bearer {poster_token}"}
        )
        assert poster_resp.json()["balance"] == 140

        worker_token = make_jws_token(
            agent_key, "a-worker", {"action": "get_balance", "account_id": "a-worker"}
        )
        worker_resp = await client.get(
            "/accounts/a-worker", headers={"Authorization": f"Bearer {worker_token}"}
        )
        assert worker_resp.json()["balance"] == 70

    async def test_escrow_not_found(self, client, platform_keypair):
        """SPL-09: splitting a nonexistent escrow returns 404 escrow_not_found."""
        response = await self._split(
            client,
            platform_keypair,
            "esc-nonexistent-uuid",
            "a-worker",
            50,
            "a-poster",
            make_operation_id(),
            "task:T-missing:ruling:r-one",
        )
        assert response.status_code == 404
        assert response.json()["error"] == "escrow_not_found"

    async def test_competing_split_reports_winning_release(
        self, client, platform_keypair, agent_keypair
    ):
        """SPL-10: a split cannot replace the release that owns settlement."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-poster", 100)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)
        locked = await _lock(client, agent_keypair, "a-poster", 50, "T-305", make_operation_id())

        platform_key, _ = platform_keypair
        winning_operation_id = make_operation_id()
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": locked["receipt"]["escrow_id"],
                "recipient_account_id": "a-worker",
                "operation_id": winning_operation_id,
                "source_reference": "task:T-305:approved",
            },
        )
        release_resp = await client.post(
            f"/escrow/{locked['receipt']['escrow_id']}/release", json={"token": release_token}
        )
        assert release_resp.status_code == 200

        response = await self._split(
            client,
            platform_keypair,
            locked["receipt"]["escrow_id"],
            "a-worker",
            50,
            "a-poster",
            make_operation_id(),
            "task:T-305:ruling:r-one",
        )
        assert response.status_code == 409
        assert response.json()["error"] == "financial_operation_already_committed"
        assert response.json()["details"] == {"winning_operation_id": winning_operation_id}

    async def test_worker_pct_below_zero_returns_invalid_amount(
        self, client, platform_keypair, agent_keypair
    ):
        """SPL-13: worker_pct < 0 is rejected with invalid_amount."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-poster", 100)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)
        locked = await _lock(client, agent_keypair, "a-poster", 50, "T-306", make_operation_id())

        response = await self._split(
            client,
            platform_keypair,
            locked["receipt"]["escrow_id"],
            "a-worker",
            -1,
            "a-poster",
            make_operation_id(),
            "task:T-306:ruling:r-one",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_amount"

    async def test_non_integer_worker_pct_returns_invalid_payload(
        self, client, platform_keypair, agent_keypair
    ):
        """SPL-14: a non-integer worker_pct returns invalid_payload."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-poster", 100)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)
        locked = await _lock(client, agent_keypair, "a-poster", 50, "T-307", make_operation_id())

        response = await self._split(
            client,
            platform_keypair,
            locked["receipt"]["escrow_id"],
            "a-worker",
            33.5,
            "a-poster",
            make_operation_id(),
            "task:T-307:ruling:r-one",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    async def test_worker_account_not_found(self, client, platform_keypair, agent_keypair):
        """SPL-16: a nonexistent worker account returns 404 account_not_found."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_funded_account(client, platform_keypair, "a-poster", 100)
        locked = await _lock(client, agent_keypair, "a-poster", 50, "T-308", make_operation_id())

        response = await self._split(
            client,
            platform_keypair,
            locked["receipt"]["escrow_id"],
            "a-nonexistent-uuid",
            50,
            "a-poster",
            make_operation_id(),
            "task:T-308:ruling:r-one",
        )
        assert response.status_code == 404
        assert response.json()["error"] == "account_not_found"

    async def test_malformed_payload_from_non_platform_signer_beats_forbidden(
        self, client, agent_keypair
    ):
        """PREC-03: a malformed split payload (missing poster_account_id and
        worker_pct) from a non-platform signer still returns invalid_payload, not
        403 — payload-shape validation runs before the local signature check."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-eve",
            {
                "action": "escrow_split",
                "escrow_id": "esc-whatever",
                "worker_account_id": "a-worker",
                "operation_id": make_operation_id(),
                "source_reference": "task:T-whatever:ruling:r-one",
            },
        )
        response = await client.post("/escrow/esc-whatever/split", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
