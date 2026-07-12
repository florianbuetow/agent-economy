"""T-036: undocumented action aliases must be rejected.

task_manager.py accepted two undocumented aliases alongside the canonical,
spec'd action names: dispute_task/file_dispute and record_ruling/submit_ruling.

file_dispute was originally exercised directly (not via a fixture helper) by
tests/unit/routers/test_security.py::test_status_validation_before_domain_validation
(PREC-09), which relied on it being accepted to reach a downstream 409
invalid_status assertion — the test's actual subject is error-code precedence,
not the alias itself. Ratified frozen-test exception #8 (plan §5.0,
2026-07-13) authorized a setup-only fix: that test's token action literal is
now "dispute_task" (every assertion stayed byte-identical), which unblocks
removing the alias here.

submit_ruling had no such conflict — only the (editable) fixture helper
tests/unit/routers/conftest.py::submit_ruling used it, and no test asserts on
the request's internal action field — so it was removed without any exception.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import setup_task_in_dispute, setup_task_in_review

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.unit
async def test_file_dispute_alias_is_rejected(
    client: AsyncClient,
    alice_keypair,
    alice_agent_id,
    bob_keypair,
    bob_agent_id,
) -> None:
    """The undocumented 'file_dispute' action alias is no longer accepted (exception #8)."""
    task_id = await setup_task_in_review(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    payload = {
        "action": "file_dispute",
        "task_id": task_id,
        "poster_id": alice_agent_id,
        "reason": "Spec not met.",
    }
    token = make_jws_token(alice_keypair[0], alice_agent_id, payload)

    resp = await client.post(f"/tasks/{task_id}/dispute", json={"token": token})

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_payload"


@pytest.mark.unit
async def test_submit_ruling_alias_is_rejected(
    client: AsyncClient,
    alice_keypair,
    alice_agent_id,
    bob_keypair,
    bob_agent_id,
    platform_keypair,
    platform_agent_id,
) -> None:
    """The undocumented 'submit_ruling' action alias is no longer accepted."""
    task_id = await setup_task_in_dispute(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    payload = {
        "action": "submit_ruling",
        "task_id": task_id,
        "ruling_id": "rul-alias-test",
        "worker_pct": 50,
        "ruling_summary": "Split ruling",
    }
    token = make_jws_token(platform_keypair[0], platform_agent_id, payload)

    resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_payload"
