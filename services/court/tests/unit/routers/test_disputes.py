"""Dispute endpoint tests.

Covers all test categories from court-service-tests.md and court-service-auth-tests.md:
- FILE-01 to FILE-17: File Dispute
- REB-01 to REB-10: Submit Rebuttal
- RULE-01 to RULE-19: Trigger Ruling
- GET-01 to GET-05: Get Dispute
- LIST-01 to LIST-06: List Disputes
- HTTP-01: HTTP Method Misuse
- SEC-01 to SEC-03: Cross-Cutting Security
- LIFE-01 to LIFE-05: Dispute Lifecycle
- AUTH-01 to AUTH-16: Platform JWS Validation
- PUB-01 to PUB-03: Public Endpoints
- IDEP-01 to IDEP-03: Identity Dependency
- REPLAY-01 to REPLAY-02: Token Replay
- PREC-01 to PREC-06: Error Precedence
- SEC-AUTH-01 to SEC-AUTH-03: Auth Security
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from court_service.core.state import get_app_state
from tests.helpers import (
    make_mock_identity_client,
    make_tampered_jws,
    new_escrow_id,
    new_task_id,
)
from tests.unit.routers.conftest import (
    PLATFORM_AGENT_ID,
    ROGUE_AGENT_ID,
    file_and_rebut,
    file_dispute,
    file_dispute_payload,
    file_rebut_and_rule,
    inject_central_bank_error,
    inject_identity_error,
    inject_identity_verify,
    inject_judge,
    inject_reputation_error,
    inject_task_board_error,
    rebuttal_payload,
    ruling_payload,
    token_body,
)

if TYPE_CHECKING:
    from httpx import AsyncClient

UUID4_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
DISPUTE_ID_PATTERN = re.compile(r"^disp-" + UUID4_PATTERN.pattern[1:])
VOTE_ID_PATTERN = re.compile(r"^vote-" + UUID4_PATTERN.pattern[1:])
ISO8601_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


@pytest.mark.unit
class TestFileDispute:
    """FILE-01 to FILE-17: File dispute tests."""

    async def test_file_01_valid_dispute(self, client: AsyncClient) -> None:
        """FILE-01: File a valid dispute returns 201 with correct status."""
        payload = file_dispute_payload()
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 201
        data = response.json()
        assert DISPUTE_ID_PATTERN.match(data["dispute_id"])
        assert data["status"] == "rebuttal_pending"

    async def test_file_02_response_includes_all_fields(self, client: AsyncClient) -> None:
        """FILE-02: Response includes all dispute fields."""
        data = await file_dispute(client)
        expected_fields = {
            "dispute_id",
            "task_id",
            "claimant_id",
            "respondent_id",
            "claim",
            "rebuttal",
            "status",
            "rebuttal_deadline",
            "worker_pct",
            "ruling_summary",
            "escrow_id",
            "filed_at",
            "rebutted_at",
            "ruled_at",
            "votes",
        }
        assert expected_fields.issubset(set(data.keys()))
        assert data["rebuttal"] is None
        assert data["worker_pct"] is None
        assert data["ruling_summary"] is None
        assert data["rebutted_at"] is None
        assert data["ruled_at"] is None
        assert data["votes"] == []
        assert ISO8601_PATTERN.match(data["filed_at"])
        assert ISO8601_PATTERN.match(data["rebuttal_deadline"])

    async def test_file_03_rebuttal_deadline_calculated(self, client: AsyncClient) -> None:
        """FILE-03: Rebuttal deadline is filed_at + configured seconds."""
        data = await file_dispute(client)
        filed_at = datetime.fromisoformat(data["filed_at"])
        deadline = datetime.fromisoformat(data["rebuttal_deadline"])
        expected = filed_at + timedelta(seconds=86400)
        assert abs((deadline - expected).total_seconds()) < 5

    async def test_file_04_duplicate_task_rejected(self, client: AsyncClient) -> None:
        """FILE-04: Duplicate dispute for same task is rejected."""
        task_id = new_task_id()
        payload = file_dispute_payload(task_id=task_id)
        await file_dispute(client, payload)
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 409
        assert response.json()["error"] == "DISPUTE_ALREADY_EXISTS"

    async def test_file_05_task_not_found(self, client: AsyncClient) -> None:
        """FILE-05: Task not found in Task Board."""
        inject_task_board_error(ServiceError("TASK_NOT_FOUND", "Not found", status_code=404))
        payload = file_dispute_payload()
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 404
        assert response.json()["error"] == "TASK_NOT_FOUND"

    async def test_file_06_missing_claim(self, client: AsyncClient) -> None:
        """FILE-06: Missing claim text."""
        payload = file_dispute_payload()
        del payload["claim"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_07_empty_claim(self, client: AsyncClient) -> None:
        """FILE-07: Empty claim text."""
        payload = file_dispute_payload(claim="")
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_08_claim_too_long(self, client: AsyncClient) -> None:
        """FILE-08: Claim exceeds 10,000 characters."""
        payload = file_dispute_payload(claim="x" * 10001)
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_09_missing_task_id(self, client: AsyncClient) -> None:
        """FILE-09: Missing task_id."""
        payload = file_dispute_payload()
        del payload["task_id"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_10_missing_claimant_id(self, client: AsyncClient) -> None:
        """FILE-10: Missing claimant_id."""
        payload = file_dispute_payload()
        del payload["claimant_id"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_11_missing_respondent_id(self, client: AsyncClient) -> None:
        """FILE-11: Missing respondent_id."""
        payload = file_dispute_payload()
        del payload["respondent_id"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_12_missing_escrow_id(self, client: AsyncClient) -> None:
        """FILE-12: Missing escrow_id."""
        payload = file_dispute_payload()
        del payload["escrow_id"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_13_wrong_action(self, client: AsyncClient) -> None:
        """FILE-13: Wrong action value."""
        payload = file_dispute_payload(action="submit_rebuttal")
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_14_non_platform_signer(self, client: AsyncClient) -> None:
        """FILE-14: Non-platform signer is rejected."""
        payload = file_dispute_payload()
        inject_identity_verify(ROGUE_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload, kid=ROGUE_AGENT_ID))
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_file_15_tampered_jws(self, client: AsyncClient) -> None:
        """FILE-15: Tampered JWS is rejected."""
        payload = file_dispute_payload()
        state = get_app_state()
        state.identity_client = make_mock_identity_client(
            verify_response={"valid": False, "agent_id": None, "payload": None}
        )
        token = make_tampered_jws(payload, kid=PLATFORM_AGENT_ID)
        response = await client.post("/disputes/file", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_file_16_missing_token(self, client: AsyncClient) -> None:
        """FILE-16: Missing token field."""
        response = await client.post("/disputes/file", json={})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_file_17_task_board_unavailable(self, client: AsyncClient) -> None:
        """FILE-17: Task Board unavailable."""
        inject_task_board_error(ConnectionError("Connection refused"))
        payload = file_dispute_payload()
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 502
        assert response.json()["error"] == "TASK_BOARD_UNAVAILABLE"


@pytest.mark.unit
class TestSubmitRebuttal:
    """REB-01 to REB-10: Submit rebuttal tests."""

    async def test_reb_01_valid_rebuttal(self, client: AsyncClient) -> None:
        """REB-01: Valid rebuttal returns 200 with rebuttal set."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rebuttal"] is not None
        assert ISO8601_PATTERN.match(data["rebutted_at"])

    async def test_reb_02_dispute_not_found(self, client: AsyncClient) -> None:
        """REB-02: Rebuttal on non-existent dispute returns 404."""
        fake_id = "disp-00000000-0000-0000-0000-000000000000"
        reb_pay = rebuttal_payload(fake_id)
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{fake_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 404
        assert response.json()["error"] == "DISPUTE_NOT_FOUND"

    async def test_reb_03_duplicate_rebuttal(self, client: AsyncClient) -> None:
        """REB-03: Second rebuttal is rejected with 409."""
        dispute = await file_and_rebut(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id, rebuttal="Another rebuttal attempt.")
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 409
        assert response.json()["error"] == "REBUTTAL_ALREADY_SUBMITTED"

    async def test_reb_04_rebuttal_after_ruling(self, client: AsyncClient) -> None:
        """REB-04: Rebuttal after ruling is rejected with 409."""
        ruling = await file_rebut_and_rule(client)
        dispute_id = ruling["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 409
        assert response.json()["error"] == "INVALID_DISPUTE_STATUS"

    async def test_reb_05_missing_rebuttal_field(self, client: AsyncClient) -> None:
        """REB-05: Missing rebuttal field returns 400."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id)
        del reb_pay["rebuttal"]
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_reb_06_empty_rebuttal(self, client: AsyncClient) -> None:
        """REB-06: Empty rebuttal text returns 400."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id, rebuttal="")
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_reb_07_rebuttal_too_long(self, client: AsyncClient) -> None:
        """REB-07: Rebuttal exceeding 10,000 chars returns 400."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id, rebuttal="x" * 10001)
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_reb_08_wrong_action(self, client: AsyncClient) -> None:
        """REB-08: Wrong action value returns 400."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id, action="file_dispute")
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_reb_09_non_platform_signer(self, client: AsyncClient) -> None:
        """REB-09: Non-platform signer is rejected."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id)
        inject_identity_verify(ROGUE_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay, kid=ROGUE_AGENT_ID),
        )
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_reb_10_status_after_rebuttal(self, client: AsyncClient) -> None:
        """REB-10: Status remains rebuttal_pending after rebuttal, rebuttal fields set."""
        dispute = await file_and_rebut(client)
        dispute_id = dispute["dispute_id"]
        response = await client.get(f"/disputes/{dispute_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rebuttal_pending"
        assert data["rebuttal"] is not None
        assert data["rebutted_at"] is not None


@pytest.mark.unit
class TestTriggerRuling:
    """RULE-01 to RULE-19: Trigger ruling tests."""

    async def test_rule_01_valid_ruling(self, client: AsyncClient) -> None:
        """RULE-01: Valid ruling returns 200 with worker_pct and votes."""
        ruling = await file_rebut_and_rule(client, worker_pct=70)
        assert ruling["worker_pct"] == 70
        assert ruling["ruling_summary"] is not None
        assert len(ruling["votes"]) == 1

    async def test_rule_02_median_single_judge(self, client: AsyncClient) -> None:
        """RULE-02: Median of single judge is the judge's value."""
        ruling = await file_rebut_and_rule(client, worker_pct=65)
        assert ruling["worker_pct"] == 65

    async def test_rule_03_status_after_ruling(self, client: AsyncClient) -> None:
        """RULE-03: Status is ruled after ruling."""
        ruling = await file_rebut_and_rule(client)
        dispute_id = ruling["dispute_id"]
        response = await client.get(f"/disputes/{dispute_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "ruled"

    async def test_rule_04_ruled_at_timestamp(self, client: AsyncClient) -> None:
        """RULE-04: ruled_at is ISO 8601 and after filed_at."""
        ruling = await file_rebut_and_rule(client)
        dispute_id = ruling["dispute_id"]
        response = await client.get(f"/disputes/{dispute_id}")
        data = response.json()
        assert ISO8601_PATTERN.match(data["ruled_at"])
        filed_at = datetime.fromisoformat(data["filed_at"])
        ruled_at = datetime.fromisoformat(data["ruled_at"])
        assert ruled_at > filed_at

    async def test_rule_05_vote_structure(self, client: AsyncClient) -> None:
        """RULE-05: Each vote has correct structure."""
        ruling = await file_rebut_and_rule(client)
        for vote in ruling["votes"]:
            assert VOTE_ID_PATTERN.match(vote["vote_id"])
            assert vote["dispute_id"] == ruling["dispute_id"]
            assert vote["judge_id"] == "judge-0"
            assert isinstance(vote["worker_pct"], int)
            assert 0 <= vote["worker_pct"] <= 100
            assert isinstance(vote["reasoning"], str)
            assert len(vote["reasoning"]) > 0
            assert ISO8601_PATTERN.match(vote["voted_at"])

    async def test_rule_06_central_bank_called(self, client: AsyncClient) -> None:
        """RULE-06: Central bank split_escrow called with correct args."""
        escrow_id = new_escrow_id()
        payload = file_dispute_payload(escrow_id=escrow_id)
        await file_rebut_and_rule(client, file_payload=payload, worker_pct=70)
        state = get_app_state()
        assert state.central_bank_client.split_escrow.call_count == 1
        call_args = state.central_bank_client.split_escrow.call_args
        assert escrow_id in str(call_args)
        assert 70 in call_args.args or any(v == 70 for v in call_args.kwargs.values())

    async def test_rule_07_reputation_called(self, client: AsyncClient) -> None:
        """RULE-07: Reputation service record_feedback called at least twice."""
        await file_rebut_and_rule(client)
        state = get_app_state()
        assert state.reputation_client.record_feedback.call_count >= 2

    async def test_rule_08_worker_pct_zero(self, client: AsyncClient) -> None:
        """RULE-08: worker_pct=0 means poster full refund."""
        ruling = await file_rebut_and_rule(client, worker_pct=0)
        assert ruling["worker_pct"] == 0
        state = get_app_state()
        call_args = state.central_bank_client.split_escrow.call_args
        assert 0 in call_args.args or any(v == 0 for v in call_args.kwargs.values())

    async def test_rule_09_worker_pct_hundred(self, client: AsyncClient) -> None:
        """RULE-09: worker_pct=100 means worker full payout."""
        ruling = await file_rebut_and_rule(client, worker_pct=100)
        assert ruling["worker_pct"] == 100
        state = get_app_state()
        call_args = state.central_bank_client.split_escrow.call_args
        assert 100 in call_args.args or any(v == 100 for v in call_args.kwargs.values())

    async def test_rule_10_worker_pct_fifty(self, client: AsyncClient) -> None:
        """RULE-10: worker_pct=50 even split."""
        ruling = await file_rebut_and_rule(client, worker_pct=50)
        assert ruling["worker_pct"] == 50
        state = get_app_state()
        call_args = state.central_bank_client.split_escrow.call_args
        assert 50 in call_args.args or any(v == 50 for v in call_args.kwargs.values())

    async def test_rule_11_worker_pct_arbitrary(self, client: AsyncClient) -> None:
        """RULE-11: worker_pct=73 arbitrary split."""
        ruling = await file_rebut_and_rule(client, worker_pct=73)
        assert ruling["worker_pct"] == 73
        state = get_app_state()
        call_args = state.central_bank_client.split_escrow.call_args
        assert 73 in call_args.args or any(v == 73 for v in call_args.kwargs.values())

    async def test_rule_12_dispute_not_found(self, client: AsyncClient) -> None:
        """RULE-12: Ruling on non-existent dispute returns 404."""
        fake_id = "disp-00000000-0000-0000-0000-000000000000"
        rule_pay = ruling_payload(fake_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{fake_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 404
        assert response.json()["error"] == "DISPUTE_NOT_FOUND"

    async def test_rule_13_already_ruled_after_rebuttal(self, client: AsyncClient) -> None:
        """RULE-13: Ruling again after file+rebut+rule returns 409."""
        ruling = await file_rebut_and_rule(client)
        dispute_id = ruling["dispute_id"]
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 409
        assert response.json()["error"] == "DISPUTE_ALREADY_RULED"

    async def test_rule_14_already_ruled_without_rebuttal(self, client: AsyncClient) -> None:
        """RULE-14: File dispute, rule without rebuttal, try to rule again."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        inject_judge(worker_pct=80)
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 200

        rule_pay2 = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay2)
        response2 = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay2),
        )
        assert response2.status_code == 409
        assert response2.json()["error"] == "DISPUTE_ALREADY_RULED"

    async def test_rule_15_judge_unavailable(self, client: AsyncClient) -> None:
        """RULE-15: Judge failure returns 502, status remains rebuttal_pending."""
        dispute = await file_and_rebut(client)
        dispute_id = dispute["dispute_id"]
        inject_judge(side_effect=Exception("Judge crashed"))
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 502
        assert response.json()["error"] == "JUDGE_UNAVAILABLE"

        get_response = await client.get(f"/disputes/{dispute_id}")
        assert get_response.json()["status"] == "rebuttal_pending"

    async def test_rule_16_central_bank_unavailable(self, client: AsyncClient) -> None:
        """RULE-16: Central bank failure returns 502, status remains rebuttal_pending."""
        dispute = await file_and_rebut(client)
        dispute_id = dispute["dispute_id"]
        inject_judge(worker_pct=70)
        inject_central_bank_error(ConnectionError("Connection refused"))
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 502
        assert response.json()["error"] == "CENTRAL_BANK_UNAVAILABLE"

        get_response = await client.get(f"/disputes/{dispute_id}")
        assert get_response.json()["status"] == "rebuttal_pending"

    async def test_rule_17_reputation_unavailable(self, client: AsyncClient) -> None:
        """RULE-17: Reputation failure returns 502, status remains rebuttal_pending."""
        dispute = await file_and_rebut(client)
        dispute_id = dispute["dispute_id"]
        inject_judge(worker_pct=70)
        inject_reputation_error(ConnectionError("Connection refused"))
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 502
        assert response.json()["error"] == "REPUTATION_SERVICE_UNAVAILABLE"

        get_response = await client.get(f"/disputes/{dispute_id}")
        assert get_response.json()["status"] == "rebuttal_pending"

    async def test_rule_18_wrong_action(self, client: AsyncClient) -> None:
        """RULE-18: Wrong action value returns 400."""
        dispute = await file_and_rebut(client)
        dispute_id = dispute["dispute_id"]
        rule_pay = ruling_payload(dispute_id, action="file_dispute")
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_rule_19_ruling_without_rebuttal(self, client: AsyncClient) -> None:
        """RULE-19: Ruling without rebuttal succeeds."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        inject_judge(worker_pct=80)
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rebuttal"] is None
        assert data["rebutted_at"] is None
        assert data["worker_pct"] is not None
        assert data["status"] == "ruled"
        assert len(data["votes"]) > 0


@pytest.mark.unit
class TestGetDispute:
    """GET-01 to GET-05: Get dispute tests."""

    async def test_get_01_pending_dispute(self, client: AsyncClient) -> None:
        """GET-01: GET pending dispute returns correct null fields."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        response = await client.get(f"/disputes/{dispute_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["worker_pct"] is None
        assert data["ruling_summary"] is None
        assert data["ruled_at"] is None
        assert data["votes"] == []
        assert data["status"] == "rebuttal_pending"

    async def test_get_02_ruled_dispute(self, client: AsyncClient) -> None:
        """GET-02: GET ruled dispute returns populated fields."""
        ruling = await file_rebut_and_rule(client)
        dispute_id = ruling["dispute_id"]
        response = await client.get(f"/disputes/{dispute_id}")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["worker_pct"], int)
        assert 0 <= data["worker_pct"] <= 100
        assert data["ruling_summary"] is not None
        assert ISO8601_PATTERN.match(data["ruled_at"])
        assert data["status"] == "ruled"
        assert len(data["votes"]) > 0

    async def test_get_03_vote_structure(self, client: AsyncClient) -> None:
        """GET-03: Vote structure is correct in ruled dispute."""
        ruling = await file_rebut_and_rule(client)
        dispute_id = ruling["dispute_id"]
        response = await client.get(f"/disputes/{dispute_id}")
        data = response.json()
        assert len(data["votes"]) == 1
        vote = data["votes"][0]
        assert VOTE_ID_PATTERN.match(vote["vote_id"])
        assert vote["judge_id"] == "judge-0"
        assert isinstance(vote["worker_pct"], int)
        assert len(vote["reasoning"]) > 0
        assert ISO8601_PATTERN.match(vote["voted_at"])

    async def test_get_04_not_found(self, client: AsyncClient) -> None:
        """GET-04: GET non-existent dispute returns 404."""
        fake_id = "disp-00000000-0000-0000-0000-000000000000"
        response = await client.get(f"/disputes/{fake_id}")
        assert response.status_code == 404
        assert response.json()["error"] == "DISPUTE_NOT_FOUND"

    async def test_get_05_public_endpoint(self, client: AsyncClient) -> None:
        """GET-05: GET dispute requires no auth."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        response = await client.get(f"/disputes/{dispute_id}")
        assert response.status_code == 200


@pytest.mark.unit
class TestListDisputes:
    """LIST-01 to LIST-06: List disputes tests."""

    async def test_list_01_empty_system(self, client: AsyncClient) -> None:
        """LIST-01: Empty system returns empty list."""
        response = await client.get("/disputes")
        assert response.status_code == 200
        assert response.json() == {"disputes": []}

    async def test_list_02_multiple_disputes(self, client: AsyncClient) -> None:
        """LIST-02: Multiple disputes returned."""
        for _ in range(3):
            await file_dispute(client, file_dispute_payload(task_id=new_task_id()))

        response = await client.get("/disputes")
        assert response.status_code == 200
        data = response.json()
        assert len(data["disputes"]) == 3
        for dispute in data["disputes"]:
            assert "dispute_id" in dispute
            assert "task_id" in dispute
            assert "claimant_id" in dispute
            assert "respondent_id" in dispute
            assert "status" in dispute
            assert "worker_pct" in dispute
            assert "filed_at" in dispute
            assert "ruled_at" in dispute

    async def test_list_03_filter_by_task_id(self, client: AsyncClient) -> None:
        """LIST-03: Filter disputes by task_id."""
        task_id_a = new_task_id()
        task_id_b = new_task_id()
        await file_dispute(client, file_dispute_payload(task_id=task_id_a))
        await file_dispute(client, file_dispute_payload(task_id=task_id_b))

        response = await client.get(f"/disputes?task_id={task_id_a}")
        assert response.status_code == 200
        disputes = response.json()["disputes"]
        assert len(disputes) == 1
        assert disputes[0]["task_id"] == task_id_a

    async def test_list_04_filter_by_status(self, client: AsyncClient) -> None:
        """LIST-04: Filter disputes by status."""
        pending_payload = file_dispute_payload(task_id=new_task_id())
        ruled_payload_data = file_dispute_payload(task_id=new_task_id())
        await file_dispute(client, pending_payload)
        await file_rebut_and_rule(client, file_payload=ruled_payload_data)

        response = await client.get("/disputes?status=rebuttal_pending")
        assert response.status_code == 200
        disputes = response.json()["disputes"]
        assert len(disputes) == 1
        assert disputes[0]["status"] == "rebuttal_pending"

    async def test_list_05_filter_by_task_id_and_status(self, client: AsyncClient) -> None:
        """LIST-05: Filter by both task_id and status."""
        task_id_a = new_task_id()
        task_id_b = new_task_id()
        await file_dispute(client, file_dispute_payload(task_id=task_id_a))
        await file_rebut_and_rule(client, file_payload=file_dispute_payload(task_id=task_id_b))

        response = await client.get(f"/disputes?task_id={task_id_a}&status=rebuttal_pending")
        assert response.status_code == 200
        disputes = response.json()["disputes"]
        assert len(disputes) == 1
        assert disputes[0]["task_id"] == task_id_a
        assert disputes[0]["status"] == "rebuttal_pending"

    async def test_list_06_public_endpoint(self, client: AsyncClient) -> None:
        """LIST-06: List disputes requires no auth."""
        response = await client.get("/disputes")
        assert response.status_code == 200


@pytest.mark.unit
class TestHTTPMethods:
    """HTTP-01: HTTP method misuse tests."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/disputes/file"),
            ("PUT", "/disputes/file"),
            ("DELETE", "/disputes/file"),
            ("PUT", "/disputes/disp-00000000-0000-0000-0000-000000000000"),
            ("DELETE", "/disputes/disp-00000000-0000-0000-0000-000000000000"),
            ("PATCH", "/disputes/disp-00000000-0000-0000-0000-000000000000"),
            ("GET", "/disputes/disp-00000000-0000-0000-0000-000000000000/rebuttal"),
            ("PUT", "/disputes/disp-00000000-0000-0000-0000-000000000000/rebuttal"),
            ("DELETE", "/disputes/disp-00000000-0000-0000-0000-000000000000/rebuttal"),
            ("GET", "/disputes/disp-00000000-0000-0000-0000-000000000000/rule"),
            ("PUT", "/disputes/disp-00000000-0000-0000-0000-000000000000/rule"),
            ("DELETE", "/disputes/disp-00000000-0000-0000-0000-000000000000/rule"),
            ("POST", "/disputes"),
            ("POST", "/health"),
        ],
    )
    async def test_http_01_method_not_allowed(
        self, client: AsyncClient, method: str, path: str
    ) -> None:
        """HTTP-01: Unsupported HTTP methods return 405."""
        response = await client.request(method, path)
        assert response.status_code == 405
        assert response.json()["error"] == "METHOD_NOT_ALLOWED"


@pytest.mark.unit
class TestCrossCuttingSecurity:
    """SEC-01 to SEC-03: Cross-cutting security tests."""

    async def test_sec_01_error_response_format(self, client: AsyncClient) -> None:
        """SEC-01: Error responses have error and message fields."""
        # INVALID_JWS
        resp1 = await client.post("/disputes/file", json={})
        assert isinstance(resp1.json()["error"], str)
        assert isinstance(resp1.json()["message"], str)

        # DISPUTE_NOT_FOUND
        fake_id = "disp-00000000-0000-0000-0000-000000000000"
        resp2 = await client.get(f"/disputes/{fake_id}")
        assert isinstance(resp2.json()["error"], str)
        assert isinstance(resp2.json()["message"], str)

        # FORBIDDEN
        payload = file_dispute_payload()
        inject_identity_verify(ROGUE_AGENT_ID, payload)
        resp3 = await client.post("/disputes/file", json=token_body(payload, kid=ROGUE_AGENT_ID))
        assert isinstance(resp3.json()["error"], str)
        assert isinstance(resp3.json()["message"], str)

    async def test_sec_02_no_sensitive_info_in_errors(self, client: AsyncClient) -> None:
        """SEC-02: Error messages don't leak sensitive info."""
        sensitive_patterns = [
            "Traceback",
            'File "',
            "line ",
            "SELECT ",
            "INSERT ",
            "/home/",
            "/Users/",
            ".py",
        ]

        # INVALID_JWS
        resp1 = await client.post("/disputes/file", json={})
        msg1 = resp1.json()["message"]
        for pattern in sensitive_patterns:
            assert pattern not in msg1

        # DISPUTE_NOT_FOUND
        fake_id = "disp-00000000-0000-0000-0000-000000000000"
        resp2 = await client.get(f"/disputes/{fake_id}")
        msg2 = resp2.json()["message"]
        for pattern in sensitive_patterns:
            assert pattern not in msg2

        # FORBIDDEN
        payload = file_dispute_payload()
        inject_identity_verify(ROGUE_AGENT_ID, payload)
        resp3 = await client.post("/disputes/file", json=token_body(payload, kid=ROGUE_AGENT_ID))
        msg3 = resp3.json()["message"]
        for pattern in sensitive_patterns:
            assert pattern not in msg3

    async def test_sec_03_id_formats(self, client: AsyncClient) -> None:
        """SEC-03: All IDs follow correct format patterns."""
        disputes_data: list[dict[str, Any]] = []
        for _ in range(3):
            ruling = await file_rebut_and_rule(
                client, file_payload=file_dispute_payload(task_id=new_task_id())
            )
            disputes_data.append(ruling)

        for ruling in disputes_data:
            assert DISPUTE_ID_PATTERN.match(ruling["dispute_id"])
            for vote in ruling["votes"]:
                assert VOTE_ID_PATTERN.match(vote["vote_id"])


@pytest.mark.unit
class TestDisputeLifecycle:
    """LIFE-01 to LIFE-05: Dispute lifecycle tests."""

    async def test_life_01_full_lifecycle(self, client: AsyncClient) -> None:
        """LIFE-01: Full lifecycle: file, rebut, rule, verify."""
        # File
        payload = file_dispute_payload()
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        file_resp = await client.post("/disputes/file", json=token_body(payload))
        assert file_resp.status_code == 201
        dispute = file_resp.json()
        dispute_id = dispute["dispute_id"]
        assert dispute["status"] == "rebuttal_pending"

        # Rebut
        reb_pay = rebuttal_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        reb_resp = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert reb_resp.status_code == 200
        assert reb_resp.json()["rebuttal"] is not None

        # Rule
        inject_judge(worker_pct=70)
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        rule_resp = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert rule_resp.status_code == 200
        ruling = rule_resp.json()
        assert ruling["worker_pct"] == 70
        assert ruling["status"] == "ruled"

        # Verify via GET
        get_resp = await client.get(f"/disputes/{dispute_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["status"] == "ruled"
        assert data["rebuttal"] is not None
        assert data["worker_pct"] == 70
        assert data["ruling_summary"] is not None
        filed_at = datetime.fromisoformat(data["filed_at"])
        rebutted_at = datetime.fromisoformat(data["rebutted_at"])
        ruled_at = datetime.fromisoformat(data["ruled_at"])
        assert filed_at < rebutted_at < ruled_at
        assert len(data["votes"]) == 1
        assert data["votes"][0]["worker_pct"] == 70

    async def test_life_02_skip_rebuttal(self, client: AsyncClient) -> None:
        """LIFE-02: File dispute, skip rebuttal, trigger ruling."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        inject_judge(worker_pct=80)
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ruled"
        assert data["rebuttal"] is None
        assert data["rebutted_at"] is None
        assert data["worker_pct"] == 80

    async def test_life_03_duplicate_task_dispute(self, client: AsyncClient) -> None:
        """LIFE-03: Duplicate dispute for same task is rejected."""
        task_id = new_task_id()
        await file_dispute(client, file_dispute_payload(task_id=task_id))
        payload2 = file_dispute_payload(task_id=task_id)
        inject_identity_verify(PLATFORM_AGENT_ID, payload2)
        response = await client.post("/disputes/file", json=token_body(payload2))
        assert response.status_code == 409
        assert response.json()["error"] == "DISPUTE_ALREADY_EXISTS"

    async def test_life_04_rebuttal_after_ruling_rejected(self, client: AsyncClient) -> None:
        """LIFE-04: Rebuttal after ruling is rejected."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        inject_judge(worker_pct=80)
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )

        reb_pay = rebuttal_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 409
        assert response.json()["error"] == "INVALID_DISPUTE_STATUS"

    async def test_life_05_double_ruling_rejected(self, client: AsyncClient) -> None:
        """LIFE-05: Double ruling is rejected."""
        ruling = await file_rebut_and_rule(client)
        dispute_id = ruling["dispute_id"]
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 409
        assert response.json()["error"] == "DISPUTE_ALREADY_RULED"


@pytest.mark.unit
class TestPlatformJWS:
    """AUTH-01 to AUTH-16: Platform JWS validation tests."""

    async def test_auth_01_valid_jws_file(self, client: AsyncClient) -> None:
        """AUTH-01: Valid platform JWS on POST /disputes/file."""
        payload = file_dispute_payload()
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 201
        data = response.json()
        assert DISPUTE_ID_PATTERN.match(data["dispute_id"])
        assert data["status"] == "rebuttal_pending"
        expected_fields = {
            "dispute_id",
            "task_id",
            "claimant_id",
            "respondent_id",
            "claim",
            "rebuttal",
            "status",
            "rebuttal_deadline",
            "worker_pct",
            "ruling_summary",
            "escrow_id",
            "filed_at",
            "rebutted_at",
            "ruled_at",
            "votes",
        }
        assert expected_fields.issubset(set(data.keys()))

    async def test_auth_02_valid_jws_rebuttal(self, client: AsyncClient) -> None:
        """AUTH-02: Valid platform JWS on POST /disputes/{id}/rebuttal."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rebuttal"] is not None
        assert ISO8601_PATTERN.match(data["rebutted_at"])

    async def test_auth_03_valid_jws_rule(self, client: AsyncClient) -> None:
        """AUTH-03: Valid platform JWS on POST /disputes/{id}/rule."""
        ruling = await file_rebut_and_rule(client)
        assert ruling["status"] == "ruled"
        assert isinstance(ruling["worker_pct"], int)
        assert len(ruling["votes"]) > 0

    async def test_auth_04_no_token_field(self, client: AsyncClient) -> None:
        """AUTH-04: Request body without token field returns 400."""
        response = await client.post(
            "/disputes/file",
            json={"task_id": "t-xxx", "claim": "test"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_05_null_token(self, client: AsyncClient) -> None:
        """AUTH-05: Null token value returns 400."""
        response = await client.post("/disputes/file", json={"token": None})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    @pytest.mark.parametrize(
        "bad_token",
        [12345, ["eyJ..."], {"jws": "eyJ..."}, True],
        ids=["int", "list", "dict", "bool"],
    )
    async def test_auth_06_wrong_token_type(self, client: AsyncClient, bad_token: Any) -> None:
        """AUTH-06: Non-string token types return 400."""
        response = await client.post("/disputes/file", json={"token": bad_token})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_07_empty_token(self, client: AsyncClient) -> None:
        """AUTH-07: Empty string token returns 400."""
        response = await client.post("/disputes/file", json={"token": ""})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    @pytest.mark.parametrize(
        "bad_jws",
        ["not-a-jws-at-all", "only.two-parts", "four.parts.is.wrong"],
        ids=["no-dots", "two-parts", "four-parts"],
    )
    async def test_auth_08_malformed_jws(self, client: AsyncClient, bad_jws: str) -> None:
        """AUTH-08: Malformed JWS strings return 400."""
        response = await client.post("/disputes/file", json={"token": bad_jws})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_09_tampered_jws(self, client: AsyncClient) -> None:
        """AUTH-09: Tampered JWS returns 403."""
        payload = file_dispute_payload()
        state = get_app_state()
        state.identity_client = make_mock_identity_client(
            verify_response={"valid": False, "agent_id": None, "payload": None}
        )
        token = make_tampered_jws(payload, kid=PLATFORM_AGENT_ID)
        response = await client.post("/disputes/file", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_auth_10_non_platform_signer_file(self, client: AsyncClient) -> None:
        """AUTH-10: Non-platform signer on POST /disputes/file."""
        payload = file_dispute_payload()
        inject_identity_verify(ROGUE_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload, kid=ROGUE_AGENT_ID))
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_auth_11_non_platform_signer_rebuttal(self, client: AsyncClient) -> None:
        """AUTH-11: Non-platform signer on POST /disputes/{id}/rebuttal."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id)
        inject_identity_verify(ROGUE_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay, kid=ROGUE_AGENT_ID),
        )
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_auth_12_non_platform_signer_rule(self, client: AsyncClient) -> None:
        """AUTH-12: Non-platform signer on POST /disputes/{id}/rule."""
        dispute = await file_and_rebut(client)
        dispute_id = dispute["dispute_id"]
        rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(ROGUE_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay, kid=ROGUE_AGENT_ID),
        )
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_auth_13_wrong_action_on_file(self, client: AsyncClient) -> None:
        """AUTH-13: Wrong action 'create_task' on POST /disputes/file."""
        payload = file_dispute_payload(action="create_task")
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_auth_14_missing_action_field(self, client: AsyncClient) -> None:
        """AUTH-14: Missing action field on POST /disputes/file."""
        payload = file_dispute_payload()
        del payload["action"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_auth_15_malformed_json(self, client: AsyncClient) -> None:
        """AUTH-15: Malformed JSON body returns 400."""
        response = await client.post(
            "/disputes/file",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JSON"

    async def test_auth_16_non_object_json(self, client: AsyncClient) -> None:
        """AUTH-16: Non-object JSON body returns 400."""
        response = await client.post(
            "/disputes/file",
            content=b'"just a string"',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JSON"


@pytest.mark.unit
class TestPublicEndpoints:
    """PUB-01 to PUB-03: Public endpoint tests."""

    async def test_pub_01_get_dispute_public(self, client: AsyncClient) -> None:
        """PUB-01: GET /disputes/{id} is public."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        response = await client.get(f"/disputes/{dispute_id}")
        assert response.status_code == 200
        data = response.json()
        assert "dispute_id" in data
        assert "status" in data

    async def test_pub_02_list_disputes_public(self, client: AsyncClient) -> None:
        """PUB-02: GET /disputes is public."""
        response = await client.get("/disputes")
        assert response.status_code == 200
        assert "disputes" in response.json()

    async def test_pub_03_health_public(self, client: AsyncClient) -> None:
        """PUB-03: GET /health is public."""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


@pytest.mark.unit
class TestIdentityDependency:
    """IDEP-01 to IDEP-03: Identity dependency tests."""

    async def test_idep_01_connection_error(self, client: AsyncClient) -> None:
        """IDEP-01: Identity ConnectionError returns 502."""
        inject_identity_error(ConnectionError("Connection refused"))
        payload = file_dispute_payload()
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 502
        assert response.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"

    async def test_idep_02_timeout_error(self, client: AsyncClient) -> None:
        """IDEP-02: Identity TimeoutError returns 502."""
        inject_identity_error(TimeoutError("Request timed out"))
        payload = file_dispute_payload()
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 502
        assert response.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"

    async def test_idep_03_runtime_error(self, client: AsyncClient) -> None:
        """IDEP-03: Identity RuntimeError returns 502."""
        inject_identity_error(RuntimeError("Unexpected response"))
        payload = file_dispute_payload()
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 502
        assert response.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"


@pytest.mark.unit
class TestTokenReplay:
    """REPLAY-01 to REPLAY-02: Token replay tests."""

    async def test_replay_01_rebuttal_action_on_file(self, client: AsyncClient) -> None:
        """REPLAY-01: JWS with submit_rebuttal action sent to /disputes/file."""
        payload = file_dispute_payload(action="submit_rebuttal")
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_replay_02_file_action_on_rule(self, client: AsyncClient) -> None:
        """REPLAY-02: JWS with file_dispute action sent to /disputes/{id}/rule."""
        dispute = await file_and_rebut(client)
        dispute_id = dispute["dispute_id"]
        rule_pay = ruling_payload(dispute_id, action="file_dispute")
        inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay),
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"


@pytest.mark.unit
class TestErrorPrecedence:
    """PREC-01 to PREC-06: Error precedence tests."""

    async def test_prec_01_content_type_before_jws(self, client: AsyncClient) -> None:
        """PREC-01: Content-Type text/plain returns 415 before JWS check."""
        response = await client.post(
            "/disputes/file",
            content=b"not json",
            headers={"content-type": "text/plain"},
        )
        assert response.status_code == 415
        assert response.json()["error"] == "UNSUPPORTED_MEDIA_TYPE"

    async def test_prec_02_payload_too_large(self, client: AsyncClient) -> None:
        """PREC-02: Oversized body returns 413."""
        response = await client.post(
            "/disputes/file",
            content=b"x" * 2_000_000,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["error"] == "PAYLOAD_TOO_LARGE"

    async def test_prec_03_invalid_json_before_jws(self, client: AsyncClient) -> None:
        """PREC-03: Invalid JSON returns 400 INVALID_JSON."""
        response = await client.post(
            "/disputes/file",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JSON"

    async def test_prec_04_jws_type_before_payload(self, client: AsyncClient) -> None:
        """PREC-04: Non-string token returns INVALID_JWS before INVALID_PAYLOAD."""
        response = await client.post("/disputes/file", json={"token": 12345})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_prec_05_action_checked_before_signer(self, client: AsyncClient) -> None:
        """PREC-05: Wrong action checked before signer identity."""
        payload = file_dispute_payload(action="wrong_action")
        inject_identity_verify(ROGUE_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload, kid=ROGUE_AGENT_ID))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_prec_06_identity_checked_before_payload(self, client: AsyncClient) -> None:
        """PREC-06: Identity error takes precedence over wrong action."""
        inject_identity_error(ConnectionError("Connection refused"))
        payload = file_dispute_payload(action="wrong_action")
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 502
        assert response.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"


@pytest.mark.unit
class TestAuthSecurity:
    """SEC-AUTH-01 to SEC-AUTH-03: Auth security tests."""

    async def test_sec_auth_01_error_format(self, client: AsyncClient) -> None:
        """SEC-AUTH-01: Auth errors have error, message, and details fields."""
        # INVALID_JWS
        resp1 = await client.post("/disputes/file", json={})
        data1 = resp1.json()
        assert isinstance(data1["error"], str)
        assert isinstance(data1["message"], str)
        assert isinstance(data1["details"], dict)

        # INVALID_PAYLOAD
        payload = file_dispute_payload(action="wrong_action")
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        resp2 = await client.post("/disputes/file", json=token_body(payload))
        data2 = resp2.json()
        assert isinstance(data2["error"], str)
        assert isinstance(data2["message"], str)
        assert isinstance(data2["details"], dict)

        # FORBIDDEN
        payload3 = file_dispute_payload()
        inject_identity_verify(ROGUE_AGENT_ID, payload3)
        resp3 = await client.post("/disputes/file", json=token_body(payload3, kid=ROGUE_AGENT_ID))
        data3 = resp3.json()
        assert isinstance(data3["error"], str)
        assert isinstance(data3["message"], str)
        assert isinstance(data3["details"], dict)

        # IDENTITY_SERVICE_UNAVAILABLE
        inject_identity_error(ConnectionError("Connection refused"))
        payload4 = file_dispute_payload()
        resp4 = await client.post("/disputes/file", json=token_body(payload4))
        data4 = resp4.json()
        assert isinstance(data4["error"], str)
        assert isinstance(data4["message"], str)
        assert isinstance(data4["details"], dict)

    async def test_sec_auth_02_no_leaks_in_messages(self, client: AsyncClient) -> None:
        """SEC-AUTH-02: Auth error messages don't leak sensitive info."""
        sensitive_patterns = [
            "Traceback",
            'File "',
            "http://",
            "https://",
            "private key",
            "EdDSA",
            "Ed25519",
        ]

        # INVALID_JWS
        resp1 = await client.post("/disputes/file", json={})
        msg1 = resp1.json()["message"]
        for pattern in sensitive_patterns:
            assert pattern not in msg1

        # FORBIDDEN
        payload = file_dispute_payload()
        inject_identity_verify(ROGUE_AGENT_ID, payload)
        resp2 = await client.post("/disputes/file", json=token_body(payload, kid=ROGUE_AGENT_ID))
        msg2 = resp2.json()["message"]
        for pattern in sensitive_patterns:
            assert pattern not in msg2

        # IDENTITY_SERVICE_UNAVAILABLE
        inject_identity_error(ConnectionError("Connection refused"))
        payload3 = file_dispute_payload()
        resp3 = await client.post("/disputes/file", json=token_body(payload3))
        msg3 = resp3.json()["message"]
        for pattern in sensitive_patterns:
            assert pattern not in msg3

    async def test_sec_auth_03_central_bank_action_rejected(self, client: AsyncClient) -> None:
        """SEC-AUTH-03: Central Bank action on /disputes/file returns 400."""
        payload = file_dispute_payload(action="escrow_lock")
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"
