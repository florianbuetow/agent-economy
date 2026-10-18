"""Integration tests — the real court app driven end-to-end against a real,
live, in-process DB Gateway (the production `DisputeDbClient` path,
T-071/GAP-F1). Unlike tests/unit (InMemoryDisputeStore fake), every
assertion here is checked both through the court API and by reading the
gateway's own SQLite file directly — including the FK-enforced relationship
between court_claims.task_id and a real board_tasks row that a fake store
has no concept of.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import TYPE_CHECKING

import pytest
from service_auth.financial_identity import operation_id_for

from tests.helpers import make_jws_token
from tests.integration.conftest import (
    PLATFORM_AGENT_ID,
    finalize_task_dispute,
    seed_task,
)

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _read_one(
    db_path: str, query: str, params: tuple[object, ...] = ()
) -> dict[str, object] | None:
    """Read a single SQLite row as a dict, straight from the gateway's own file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


class TestDisputeLifecycleGatewayBacked:
    async def test_file_rebut_and_rule_persists_through_real_gateway(
        self,
        client: httpx.AsyncClient,
        gateway_client: httpx.AsyncClient,
        gateway_db_path: str,
    ) -> None:
        """File -> rebuttal -> ruling, verified against the real
        gateway-backed DisputeDbClient at every step."""
        claimant_id = "a-claimant-integration"
        respondent_id = "a-respondent-integration"
        task_id = f"t-{uuid.uuid4()}"
        reason = "The worker did not deliver as specified."
        seeded = await seed_task(
            gateway_client,
            task_id=task_id,
            poster_id=claimant_id,
            worker_id=respondent_id,
            reason=reason,
        )

        file_payload = {
            "action": "file_dispute",
            "task_id": task_id,
            "claimant_id": claimant_id,
            "respondent_id": respondent_id,
            "claim": reason,
            "escrow_id": seeded.escrow_id,
        }
        file_token = make_jws_token(file_payload, kid=PLATFORM_AGENT_ID)
        file_resp = await client.post("/disputes/file", json={"token": file_token})
        assert file_resp.status_code == 201, file_resp.text
        dispute_id = file_resp.json()["dispute_id"]

        claim_row = _read_one(
            gateway_db_path,
            "SELECT task_id, claimant_id, respondent_id, status FROM court_claims "
            "WHERE claim_id = ?",
            (dispute_id,),
        )
        assert claim_row is not None
        assert claim_row["task_id"] == task_id
        assert claim_row["claimant_id"] == claimant_id
        assert claim_row["respondent_id"] == respondent_id
        assert claim_row["status"] == "rebuttal_pending"

        completed_dispute = await finalize_task_dispute(gateway_client, seeded)
        assert completed_dispute["effect_result_reference"] == dispute_id

        rebuttal_payload = {
            "action": "submit_rebuttal",
            "dispute_id": dispute_id,
            "rebuttal": "The specification was ambiguous.",
        }
        rebuttal_token = make_jws_token(rebuttal_payload, kid=PLATFORM_AGENT_ID)
        rebuttal_resp = await client.post(
            f"/disputes/{dispute_id}/rebuttal", json={"token": rebuttal_token}
        )
        assert rebuttal_resp.status_code == 200, rebuttal_resp.text

        rebuttal_row = _read_one(
            gateway_db_path,
            "SELECT claim_id, agent_id, content FROM court_rebuttals WHERE claim_id = ?",
            (dispute_id,),
        )
        assert rebuttal_row is not None
        assert rebuttal_row["agent_id"] == respondent_id
        assert rebuttal_row["content"] == "The specification was ambiguous."

        ruling_payload = {"action": "trigger_ruling", "dispute_id": dispute_id}
        ruling_token = make_jws_token(ruling_payload, kid=PLATFORM_AGENT_ID)
        ruling_resp = await client.post(
            f"/disputes/{dispute_id}/rule", json={"token": ruling_token}
        )
        assert ruling_resp.status_code == 200, ruling_resp.text

        ruling_row = _read_one(
            gateway_db_path,
            "SELECT claim_id, task_id, worker_pct FROM court_rulings WHERE claim_id = ?",
            (dispute_id,),
        )
        assert ruling_row is not None
        assert ruling_row["task_id"] == task_id
        assert ruling_row["worker_pct"] == 70

        final_claim_row = _read_one(
            gateway_db_path,
            "SELECT status FROM court_claims WHERE claim_id = ?",
            (dispute_id,),
        )
        assert final_claim_row is not None
        assert final_claim_row["status"] == "ruled"

        board_row = _read_one(
            gateway_db_path,
            "SELECT t.status, c.lifecycle_version FROM board_tasks AS t "
            "JOIN board_task_contracts AS c ON c.task_id=t.task_id WHERE t.task_id=?",
            (task_id,),
        )
        assert board_row == {"status": "disputed", "lifecycle_version": 3}
        escrow_row = _read_one(
            gateway_db_path,
            "SELECT status, resolved_at FROM bank_escrow WHERE escrow_id=?",
            (seeded.escrow_id,),
        )
        assert escrow_row == {"status": "locked", "resolved_at": None}
        ruling_operation_id = operation_id_for("court-ruling", (dispute_id,))
        assert (
            _read_one(
                gateway_db_path,
                "SELECT operation_id FROM board_task_operations WHERE operation_id=?",
                (ruling_operation_id,),
            )
            is None
        )
        assert (
            _read_one(
                gateway_db_path,
                "SELECT operation_id FROM bank_operation_receipts WHERE operation_id=?",
                (ruling_operation_id,),
            )
            is None
        )
