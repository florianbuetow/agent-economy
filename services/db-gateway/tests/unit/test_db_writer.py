"""DbWriter service-layer tests — direct method calls without HTTP."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from tests.conftest import make_event

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter


@pytest.mark.unit
class TestDbWriterIdentity:
    """Direct DbWriter tests for identity domain."""

    def test_register_agent_success(self, db_writer: DbWriter) -> None:
        """AGT-01 (service layer): Register a valid agent via DbWriter."""
        aid = f"a-{uuid4()}"
        pk = f"ed25519:{uuid4()}"
        result = db_writer.register_agent(
            {
                "agent_id": aid,
                "name": "Alice",
                "public_key": pk,
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            }
        )
        assert result["agent_id"] == aid
        assert isinstance(result["event_id"], int)
        assert result["event_id"] > 0

    def test_duplicate_public_key_raises_service_error(self, db_writer: DbWriter) -> None:
        """AGT-03 (service layer): Duplicate public_key raises ServiceError."""
        shared_key = f"ed25519:{uuid4()}"
        db_writer.register_agent(
            {
                "agent_id": f"a-{uuid4()}",
                "name": "Alice",
                "public_key": shared_key,
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            }
        )
        with pytest.raises(ServiceError) as exc_info:
            db_writer.register_agent(
                {
                    "agent_id": f"a-{uuid4()}",
                    "name": "Bob",
                    "public_key": shared_key,
                    "registered_at": "2026-02-28T10:00:00Z",
                    "event": make_event(),
                }
            )
        assert exc_info.value.error == "public_key_exists"


@pytest.mark.unit
class TestDbWriterBank:
    """Direct DbWriter tests for bank domain."""

    def _register_agent(self, db_writer: DbWriter) -> str:
        """Register an agent and return its agent_id."""
        aid = f"a-{uuid4()}"
        db_writer.register_agent(
            {
                "agent_id": aid,
                "name": "TestAgent",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            }
        )
        return aid

    def test_create_account(self, db_writer: DbWriter) -> None:
        """ACCT-01 (service layer): Create a bank account via DbWriter."""
        aid = self._register_agent(db_writer)
        result = db_writer.create_account(
            {
                "account_id": aid,
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            }
        )
        assert result["account_id"] == aid
        assert isinstance(result["event_id"], int)
        assert result["event_id"] > 0

    def test_credit_account(self, db_writer: DbWriter) -> None:
        """CR-01 (service layer): Credit an account via DbWriter."""
        aid = self._register_agent(db_writer)
        db_writer.create_account(
            {
                "account_id": aid,
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            }
        )
        result = db_writer.credit_account(
            {
                "tx_id": f"tx-{uuid4()}",
                "account_id": aid,
                "amount": 500,
                "reference": "salary_round_1",
                "timestamp": "2026-02-28T10:01:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            }
        )
        assert result["tx_id"] is not None
        assert result["balance_after"] == 500
        assert isinstance(result["event_id"], int)
        assert result["event_id"] > 0

    def test_credit_account_not_found(self, db_writer: DbWriter) -> None:
        """CR-04 (service layer): Credit non-existent account raises ServiceError."""
        with pytest.raises(ServiceError) as exc_info:
            db_writer.credit_account(
                {
                    "tx_id": f"tx-{uuid4()}",
                    "account_id": f"a-{uuid4()}",
                    "amount": 500,
                    "reference": "salary_round_1",
                    "timestamp": "2026-02-28T10:01:00Z",
                    "event": make_event(source="bank", event_type="salary.paid"),
                }
            )
        assert exc_info.value.error == "account_not_found"


@pytest.mark.unit
class TestDbWriterEscrow:
    """Direct DbWriter tests for escrow operations."""

    def _setup_funded_account(self, db_writer: DbWriter, balance: int) -> str:
        """Register agent, create account, fund it, and return account_id."""
        aid = f"a-{uuid4()}"
        db_writer.register_agent(
            {
                "agent_id": aid,
                "name": "TestAgent",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            }
        )
        tx_id = f"tx-{uuid4()}"
        db_writer.create_account(
            {
                "account_id": aid,
                "balance": balance,
                "created_at": "2026-02-28T10:00:00Z",
                "initial_credit": {
                    "tx_id": tx_id,
                    "amount": balance,
                    "reference": "initial",
                    "timestamp": "2026-02-28T10:00:00Z",
                },
                "event": make_event(source="bank", event_type="account.created"),
            }
        )
        return aid

    def test_escrow_lock_success(self, db_writer: DbWriter) -> None:
        """ELOCK-01 (service layer): Lock funds in escrow via DbWriter."""
        aid = self._setup_funded_account(db_writer, balance=500)
        task_id = f"t-{uuid4()}"
        result = db_writer.escrow_lock(
            {
                "escrow_id": f"esc-{uuid4()}",
                "payer_account_id": aid,
                "amount": 100,
                "task_id": task_id,
                "created_at": "2026-02-28T10:01:00Z",
                "tx_id": f"tx-{uuid4()}",
                "event": make_event(source="bank", event_type="escrow.locked"),
            }
        )
        assert result["escrow_id"] is not None
        assert result["balance_after"] == 400
        assert isinstance(result["event_id"], int)
        assert result["event_id"] > 0

    def test_escrow_lock_insufficient_funds(self, db_writer: DbWriter) -> None:
        """ELOCK-06 (service layer): Insufficient funds raises ServiceError."""
        aid = self._setup_funded_account(db_writer, balance=50)
        with pytest.raises(ServiceError) as exc_info:
            db_writer.escrow_lock(
                {
                    "escrow_id": f"esc-{uuid4()}",
                    "payer_account_id": aid,
                    "amount": 100,
                    "task_id": f"t-{uuid4()}",
                    "created_at": "2026-02-28T10:01:00Z",
                    "tx_id": f"tx-{uuid4()}",
                    "event": make_event(source="bank", event_type="escrow.locked"),
                }
            )
        assert exc_info.value.error == "insufficient_funds"
