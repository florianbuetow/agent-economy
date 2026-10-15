"""T-072 coverage sweep gap fix.

RULE-06's full spec text (docs/specifications/service-tests/court-service-tests.md)
requires proving a negative that the existing ``test_rule_06_central_bank_called``
in ``test_disputes.py`` does not assert: "The Court itself makes no Central Bank
call -- this is a Task-Board-internal effect of record_ruling, not a Court-issued
escrow split." This file adds that missing assertion without touching the existing
(acceptance) test file, per project convention of adding new test files instead of
editing existing ones.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from court_service.core.state import get_app_state
from tests.unit.routers.conftest import file_dispute_payload, file_rebut_and_rule, new_escrow_id

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.unit
async def test_rule_06_court_makes_no_direct_central_bank_call(client: AsyncClient) -> None:
    """RULE-06: escrow settles on the Task Board, not at Court.

    The platform agent's ``split_escrow`` method is the Court-issued escrow-split
    call path (a direct Central Bank interaction). A correct ruling flow only calls
    ``record_ruling`` against the Task Board, which settles escrow internally there.
    Court must never call ``split_escrow`` itself.
    """
    escrow_id = new_escrow_id()
    payload = file_dispute_payload(escrow_id=escrow_id)
    await file_rebut_and_rule(client, file_payload=payload, worker_pct=70)

    state = get_app_state()
    assert state.platform_agent.record_ruling.call_count == 1
    state.platform_agent.split_escrow.assert_not_called()
